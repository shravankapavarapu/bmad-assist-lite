"""Tests for the SP-1 structured code-review-synthesis path (goal-run6).

Operator decision: keep the synthesis as the round-1 FIXER. SP-1 feeds it the
deterministically pre-merged reviewer findings + a terse-output directive instead
of raw N-reviewer prose it must re-derive; the synthesis still applies fixes and
emits the remaining findings block. These tests exercise the wiring with a mocked
provider, plus the fallback to the legacy path.
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


def _review_with_block(label: str, findings: list[Finding]) -> dict[str, Any]:
    return {
        "reviewer": label,
        "response": "prose review\n\n" + render_findings_block(findings),
        "exit_code": 0,
    }


def _synthesis_response(remaining: list[Finding]) -> str:
    return "Terse report: applied fixes.\n\n" + render_findings_block(remaining)


class TestStructuredSynthesisKeepsFixer:
    def test_merges_and_feeds_candidates_to_fixer(self, tmp_path):
        dup = _finding("a.py", "bug A", Severity.HIGH, "def foo")
        r1 = _review_with_block(
            "Reviewer-1", [dup, _finding("b.py", "bug B", Severity.MEDIUM, "def bar")]
        )
        r2 = _review_with_block(
            "Reviewer-2", [dup, _finding("c.py", "nit C", Severity.LOW, "x")]
        )
        _seed_reviews(tmp_path, [r1, r2])
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)
        captured: dict[str, Any] = {}

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            # The fixer resolved bug B; bug A remains high/patch (blocking).
            return ProviderResult(
                stdout=_synthesis_response([_finding("a.py", "bug A", Severity.HIGH, "def foo")]),
                stderr="", exit_code=0, duration_ms=9, model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke), patch.object(
            CodeReviewSynthesisHandler, "render_prompt", return_value="SYNTH WORKFLOW PROMPT"
        ):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success
        assert result.outputs["structured_review"] is True
        assert result.outputs["merged_findings"] == 3  # shared high deduped
        # Fixer keeps full tools: the call must NOT be forced tool-free.
        assert captured["kwargs"].get("allowed_tools") != []
        # The de-duplicated candidate set is injected into the fixer prompt.
        assert "merged-reviewer-findings" in captured["prompt"]
        assert "bug A" in captured["prompt"] and "bug B" in captured["prompt"]
        # Outcome is driven by the synthesis' OWN remaining-findings block.
        assert result.outputs["review_blocking_findings"] == 1

        artifact = tmp_path / ".bmad-assist-lite" / "cache" / f"review-findings-{STORY}.md"
        assert artifact.exists() and "BMAD-FINDINGS" in artifact.read_text(encoding="utf-8")

    def test_clean_when_synthesis_reports_no_remaining(self, tmp_path):
        _seed_reviews(
            tmp_path, [_review_with_block("Reviewer-1", [_finding("a.py", "x", Severity.HIGH, "f")])]
        )
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            return ProviderResult(
                stdout=_synthesis_response([]), stderr="", exit_code=0,
                duration_ms=9, model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke), patch.object(
            CodeReviewSynthesisHandler, "render_prompt", return_value="P"
        ):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success
        assert result.outputs["review_blocking_findings"] == 0
        assert result.next_phase is None

    def test_synthesis_judges_at_full_effort_even_when_lean(self, tmp_path):
        # Refinement: SP-3 lowers REVIEWER effort but the synthesis keeps its
        # central severity re-judgment at master effort (no effort override).
        cfg = dict(CONFIG_STRUCTURED)
        cfg["speed"] = {"structured_review": True, "lean_review": True}
        _seed_reviews(
            tmp_path, [_review_with_block("Reviewer-1", [_finding("a.py", "x", Severity.LOW, "f")])]
        )
        handler = CodeReviewSynthesisHandler(load_config(cfg), tmp_path)
        captured: dict[str, Any] = {}

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            captured.update(kwargs)
            captured["prompt"] = prompt
            return ProviderResult(
                stdout=_synthesis_response([]), stderr="", exit_code=0,
                duration_ms=9, model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke), patch.object(
            CodeReviewSynthesisHandler, "render_prompt", return_value="P"
        ):
            handler.execute(State(current_epic=3, current_story=STORY))

        # No effort override -> invoke_provider uses the master effort.
        assert "effort" not in captured
        # The prompt demands central re-judgment, not copying reviewer buckets.
        assert "CENTRALLY" in captured["prompt"] and "Escalate" in captured["prompt"]

    def test_all_lanes_clean_stays_on_fast_path(self, tmp_path):
        # Every reviewer parsed a valid EMPTY block: that is a clean review, and
        # exactly where the fast path is cheapest — it must NOT fall back to the
        # legacy prose synthesis.
        _seed_reviews(
            tmp_path,
            [_review_with_block("Reviewer-1", []), _review_with_block("Reviewer-2", [])],
        )
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)
        captured: dict[str, Any] = {}

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            captured["prompt"] = prompt
            return ProviderResult(
                stdout=_synthesis_response([]), stderr="", exit_code=0,
                duration_ms=9, model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke), patch.object(
            CodeReviewSynthesisHandler, "render_prompt", return_value="P"
        ):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success
        assert result.outputs["structured_review"] is True
        assert result.outputs["merged_findings"] == 0
        assert "clean review" in captured["prompt"]

    def test_evidence_context_injected_into_structured_prompt(self, tmp_path):
        cache = tmp_path / ".bmad-assist-lite" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        reviews = [_review_with_block("Reviewer-1", [_finding("a.py", "x", Severity.LOW, "f")])]
        (cache / "reviews.json").write_text(
            json.dumps(
                {
                    "reviews": reviews,
                    "evidence_score": {"total_score": 7.5, "verdict": "PASS"},
                }
            ),
            encoding="utf-8",
        )
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)
        captured: dict[str, Any] = {}

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            captured["prompt"] = prompt
            return ProviderResult(
                stdout=_synthesis_response([]), stderr="", exit_code=0,
                duration_ms=9, model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke), patch.object(
            CodeReviewSynthesisHandler, "render_prompt", return_value="P"
        ):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success and result.outputs["structured_review"] is True
        # The pre-calculated Evidence Score reaches the structured prompt too.
        assert "Evidence Score" in captured["prompt"]

    def test_falls_back_to_legacy_when_no_block(self, tmp_path):
        _seed_reviews(
            tmp_path,
            [{"reviewer": "Reviewer-1", "response": "just prose, no block", "exit_code": 0}],
        )
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            return ProviderResult(
                stdout="legacy synthesis\n" + render_findings_block([]),
                stderr="", exit_code=0, duration_ms=9, model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke), patch.object(
            CodeReviewSynthesisHandler, "render_prompt", return_value="LEGACY PROMPT"
        ):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success
        assert "structured_review" not in result.outputs  # legacy path

    def test_one_unparseable_lane_falls_back_so_no_finding_is_lost(self, tmp_path):
        # Operator quality decision: if ANY lane's block fails to parse, its
        # findings would be lost to the merge — the whole story falls back to
        # the legacy prose synthesis, which reads the raw reviews.
        _seed_reviews(
            tmp_path,
            [
                _review_with_block(
                    "Reviewer-1", [_finding("a.py", "real bug", Severity.HIGH, "f")]
                ),
                {"reviewer": "Reviewer-2", "response": "prose only, no block", "exit_code": 0},
            ],
        )
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)
        captured: dict[str, Any] = {}

        def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
            captured["prompt"] = prompt
            return ProviderResult(
                stdout="legacy synthesis\n" + render_findings_block([]),
                stderr="", exit_code=0, duration_ms=9, model="opus", command=("claude",),
            )

        with patch.object(handler, "invoke_provider", side_effect=_fake_invoke), patch.object(
            CodeReviewSynthesisHandler, "render_prompt", return_value="LEGACY PROMPT"
        ):
            result = handler.execute(State(current_epic=3, current_story=STORY))

        assert result.success
        assert "structured_review" not in result.outputs  # legacy path took over
        # Legacy prompt embeds the raw reviewer prose, so nothing was lost.
        assert "code-review-reports" in captured["prompt"]


class TestVerdictRecordWritten:
    """The loop's exit writes the durable record the three-witness gate reads."""

    def _seed_cache(self, project: Path, meta: dict[str, Any] | None) -> None:
        cache = project / ".bmad-assist-lite" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "reviews": [],
            "evidence_score": {"total_score": 1.5, "verdict": "PASS"},
        }
        if meta is not None:
            payload["round_meta"] = meta
        (cache / "reviews.json").write_text(json.dumps(payload), encoding="utf-8")

    def _state(self) -> State:
        state = State(current_epic=3, current_story=STORY)
        state.review_story_id = STORY
        state.review_iteration = 1  # == the cap in CONFIG_STRUCTURED
        return state

    def test_loop_exit_records_the_promoting_round(self, tmp_path: Path) -> None:
        from bmad_assist_lite.core.verdict import load_review_verdict
        from bmad_assist_lite.validation.findings import FindingSet

        self._seed_cache(
            tmp_path,
            {
                "story_id": STORY,
                "review_iteration": 1,
                "full_pass": True,
                "audit_required": True,
                "audit_ran": True,
                "audit_passed": True,
            },
        )
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)

        decision = handler._decide_and_record(self._state(), FindingSet(findings=()))
        assert decision.proceeds

        record = load_review_verdict(tmp_path, STORY)
        assert record is not None
        assert record.verdict == "PASS"
        assert record.full_pass is True
        assert record.audit_passed is True
        assert record.outcome == "clean"

    def test_fix_round_writes_no_record(self, tmp_path: Path) -> None:
        """Mid-loop rounds are not the promoting round; recording them would
        let a later merge promote on a stale verdict."""
        from bmad_assist_lite.core.verdict import load_review_verdict
        from bmad_assist_lite.validation.findings import FindingSet

        cfg = {**CONFIG_STRUCTURED, "loop": {"review_max_iterations": 2}}
        self._seed_cache(tmp_path, None)
        handler = CodeReviewSynthesisHandler(load_config(cfg), tmp_path)

        state = State(current_epic=3, current_story=STORY)
        state.review_story_id = STORY
        state.review_iteration = 0
        blocking = FindingSet(
            findings=(
                _finding("a.py", "broken", Severity.HIGH, "x"),
                _finding("b.py", "also broken", Severity.HIGH, "y"),
            )
        )
        decision = handler._decide_and_record(state, blocking)
        assert not decision.proceeds  # FIX round

        assert load_review_verdict(tmp_path, STORY) is None

    def test_stale_meta_for_another_story_is_recorded_conservatively(
        self, tmp_path: Path
    ) -> None:
        """A story-mismatched round_meta must not lend its full_pass to this
        story: the record says full_pass=False, and the gate parks in review
        (recoverable) rather than promoting on borrowed evidence."""
        from bmad_assist_lite.core.verdict import load_review_verdict
        from bmad_assist_lite.validation.findings import FindingSet

        self._seed_cache(
            tmp_path,
            {
                "story_id": "9.9",
                "review_iteration": 1,
                "full_pass": True,
                "audit_required": True,
                "audit_ran": True,
                "audit_passed": True,
            },
        )
        handler = CodeReviewSynthesisHandler(load_config(CONFIG_STRUCTURED), tmp_path)

        handler._decide_and_record(self._state(), FindingSet(findings=()))

        record = load_review_verdict(tmp_path, STORY)
        assert record is not None
        assert record.full_pass is False
