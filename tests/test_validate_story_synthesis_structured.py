"""Tests for the SP-1 structured validate-story-synthesis path (goal-run6).

Mirrors the code-review synthesis adoption: with ``speed.structured_review`` on,
the synthesis prompt carries the deterministically merged validator findings
instead of every validator's full prose — and falls back to the prose reports
whenever a lane's block fails to parse, so no finding can be lost.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.paths import _reset_paths, init_paths
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.validate_story_synthesis import (
    ValidateStorySynthesisHandler,
)
from bmad_assist_lite.providers.base import ProviderResult
from bmad_assist_lite.validation.findings import (
    Bucket,
    Finding,
    Severity,
    render_findings_block,
)

STORY = "3.1"

CONFIG: dict[str, Any] = {
    "providers": {
        "master": {"provider": "claude", "model": "opus"},
        "multi": [{"provider": "claude", "model": "sonnet"}],
    },
    "speed": {"structured_review": True},
}


@pytest.fixture(autouse=True)
def _paths(tmp_path):
    _reset_paths()
    init_paths(tmp_path)
    yield
    _reset_paths()


def _finding(file: str, title: str, severity: Severity = Severity.MEDIUM) -> Finding:
    return Finding(
        file=file, title=title, severity=severity, bucket=Bucket.PATCH, anchor="a"
    )


def _seed_validations(project: Path, validations: list[dict[str, Any]]) -> None:
    cache = project / ".bmad-assist-lite" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "validations.json").write_text(
        json.dumps({"validations": validations, "evidence_score": None}),
        encoding="utf-8",
    )


def _validation_with_block(label: str, findings: list[Finding]) -> dict[str, Any]:
    return {
        "validator": label,
        "response": "validator prose\n\n" + render_findings_block(findings),
        "exit_code": 0,
    }


def _run(handler: ValidateStorySynthesisHandler, captured: dict[str, Any]) -> Any:
    def _fake_invoke(prompt: str, **kwargs: Any) -> ProviderResult:
        captured["prompt"] = prompt
        return ProviderResult(
            stdout="synthesis output", stderr="", exit_code=0,
            duration_ms=9, model="opus", command=("claude",),
        )

    with patch.object(handler, "invoke_provider", side_effect=_fake_invoke), patch.object(
        ValidateStorySynthesisHandler, "render_prompt", return_value="VSYNTH PROMPT"
    ):
        return handler.execute(State(current_epic=3, current_story=STORY))


class TestStructuredValidationSynthesis:
    def test_merged_candidates_replace_prose_reports(self, tmp_path):
        dup = _finding("s.md", "gap X", Severity.HIGH)
        _seed_validations(
            tmp_path,
            [
                _validation_with_block("Validator-1", [dup, _finding("s.md", "gap Y")]),
                _validation_with_block("Validator-2", [dup]),
            ],
        )
        handler = ValidateStorySynthesisHandler(load_config(CONFIG), tmp_path)
        captured: dict[str, Any] = {}
        result = _run(handler, captured)

        assert result.success
        assert result.outputs["structured_review"] is True
        assert result.outputs["merged_findings"] == 2  # shared high deduped
        assert "merged-validator-findings" in captured["prompt"]
        assert "gap X" in captured["prompt"] and "gap Y" in captured["prompt"]
        # The full validator prose must NOT ride along.
        assert "<validation-reports>" not in captured["prompt"]
        assert "validator prose" not in captured["prompt"]

    def test_all_clean_stays_structured(self, tmp_path):
        _seed_validations(tmp_path, [_validation_with_block("Validator-1", [])])
        handler = ValidateStorySynthesisHandler(load_config(CONFIG), tmp_path)
        captured: dict[str, Any] = {}
        result = _run(handler, captured)

        assert result.success
        assert result.outputs["structured_review"] is True
        assert result.outputs["merged_findings"] == 0
        assert "clean validation" in captured["prompt"]

    def test_unparseable_lane_falls_back_to_prose_reports(self, tmp_path):
        _seed_validations(
            tmp_path,
            [
                _validation_with_block("Validator-1", [_finding("s.md", "gap X")]),
                {"validator": "Validator-2", "response": "prose only", "exit_code": 0},
            ],
        )
        handler = ValidateStorySynthesisHandler(load_config(CONFIG), tmp_path)
        captured: dict[str, Any] = {}
        result = _run(handler, captured)

        assert result.success
        assert "structured_review" not in result.outputs
        # Prose fallback embeds every validator's raw report — nothing lost.
        assert "<validation-reports>" in captured["prompt"]
        assert "prose only" in captured["prompt"]

    def test_flag_off_uses_prose_reports(self, tmp_path):
        cfg = {**CONFIG, "speed": {"structured_review": False}}
        _seed_validations(
            tmp_path, [_validation_with_block("Validator-1", [_finding("s.md", "gap X")])]
        )
        handler = ValidateStorySynthesisHandler(load_config(cfg), tmp_path)
        captured: dict[str, Any] = {}
        result = _run(handler, captured)

        assert result.success
        assert "structured_review" not in result.outputs
        assert "<validation-reports>" in captured["prompt"]
