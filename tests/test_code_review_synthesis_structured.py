"""Tests for the SP-1 structured code-review-synthesis path (goal-run6).

Exercises the wiring end-to-end with a mocked provider: reviewers' structured
blocks -> deterministic merge -> one tool-free adjudication call -> review loop,
plus the fallback to the legacy path when no reviewer emits a parseable block.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.code_review_synthesis import (
    CodeReviewSynthesisHandler,
)
from bmad_assist_lite.loop.review_merge import (
    ADJUDICATION_CLOSE_MARKER,
    ADJUDICATION_OPEN_MARKER,
)
from bmad_assist_lite.providers.base import ProviderResult
from bmad_assist_lite.validation.findings import (
    Bucket,
    Finding,
    Severity,
    render_findings_block,
)

STORY = "3.1"

CONFIG_STRUCTURED: dict[str, Any] = {
    "providers": {
        "master": {"provider": "claude", "model": "opus"},
        "multi": [
            {"provider": "claude", "model": "fable"},
            {"provider": "claude", "model": "opus"},
        ],
    },
    "loop": {"review_max_iterations": 1},
    "speed": {"structured_review": True},
}


def _finding(file: str, title: str, severity: Severity, anchor: str) -> Finding:
    return Finding(
        file=file, title=title, severity=severity, bucket=Bucket.PATCH, anchor=anchor
    )


def _seed_reviews(project: Path, reviews: list[dict[str, Any]]) -> None:
    cache = project / ".bmad-assist-lite" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "reviews.json").write_text(
        json.dumps({"reviews": reviews, "evidence_score": None}), encoding="utf-8"
    )


def _adjudication_response(decisions: list[dict[str, str]]) -> str:
    payload = {"verdict": "one blocking bug", "decisions": decisions}
    return (
        "adjudicated.\n"
        f"{ADJUDICATION_OPEN_MARKER}\n```json\n{json.dumps(payload)}\n```\n"
        f"{ADJUDICATION_CLOSE_MARKER}\n"
    )


def _review_with_block(label: str, findings: list[Finding]) -> dict[str, Any]:
    return {
        "reviewer": label,
        "response": "prose review\n\n" + render_findings_block(findings),
        "exit_code": 0,
    }


class TestStructuredSynthesisPath:
    def test_merges_dedups_and_adjudicates(self, tmp_path):
        dup = _finding("a.py", "bug A", Severity.HIGH, "def foo")
        r1 = _review_with_block("Reviewer-1", [dup, _finding("b.py", "bug B", Severity.MEDIUM, "def bar")])
        r2 = _review_with_block("Reviewer-2", [dup, _finding("c.py", "nit C", Severity.LOW, "x")])
        _seed_reviews(tmp_path, [r1, r2])

        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)
        captured: dict[str, Any] = {}

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            # F1 = the high (sorted first); keep as patch (blocks). Defer the low.
            return ProviderResult(
                stdout=_adjudication_response(
                    [
                        {"id": "F1", "bucket": "patch"},
                        {"id": "F2", "bucket": "patch"},
                        {"id": "F3", "bucket": "reject"},
                    ]
                ),
                stderr="",
                exit_code=0,
                duration_ms=5,
                model="opus",
                command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success
        assert result.outputs["structured_review"] is True
        # 3 distinct findings after dedup of the shared high.
        assert result.outputs["merged_findings"] == 3
        # The adjudication call must be tool-free (cannot fix/explore).
        assert captured["kwargs"]["allowed_tools"] == []
        # High + medium patch => 2 blocking; the low was rejected.
        assert result.outputs["review_blocking_findings"] == 2

        artifact = tmp_path / ".bmad-assist-lite" / "cache" / f"review-findings-{STORY}.md"
        assert artifact.exists()
        assert "BMAD-FINDINGS" in artifact.read_text(encoding="utf-8")

    def test_clean_review_when_all_rejected(self, tmp_path):
        r1 = _review_with_block("Reviewer-1", [_finding("a.py", "maybe", Severity.HIGH, "def foo")])
        _seed_reviews(tmp_path, [r1])
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            return ProviderResult(
                stdout=_adjudication_response([{"id": "F1", "bucket": "reject"}]),
                stderr="", exit_code=0, duration_ms=5, model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success
        assert result.outputs["review_blocking_findings"] == 0
        assert result.next_phase is None  # clean -> no fix detour

    def test_falls_back_to_legacy_when_no_block(self, tmp_path):
        # Neither reviewer emits a findings block -> structured returns None.
        _seed_reviews(
            tmp_path,
            [{"reviewer": "Reviewer-1", "response": "just prose, no block", "exit_code": 0}],
        )
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)

        legacy_stdout = "legacy synthesis\n" + render_findings_block([])

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            return ProviderResult(
                stdout=legacy_stdout, stderr="", exit_code=0, duration_ms=5,
                model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke), patch.object(
            CodeReviewSynthesisHandler, "render_prompt", return_value="LEGACY PROMPT"
        ):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success
        # Legacy path does not set the structured marker.
        assert "structured_review" not in result.outputs

    def test_unparseable_adjudication_keeps_reviewer_buckets(self, tmp_path):
        r1 = _review_with_block("Reviewer-1", [_finding("a.py", "bug", Severity.HIGH, "def foo")])
        _seed_reviews(tmp_path, [r1])
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            return ProviderResult(
                stdout="no adjudication block at all", stderr="", exit_code=0,
                duration_ms=5, model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success
        # Reviewer bucket was patch (default) -> the high finding still blocks.
        assert result.outputs["review_blocking_findings"] == 1
