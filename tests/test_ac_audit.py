"""Tests for the AC-completeness audit lane (ac_audit lever).

The lever exists because a half-built acceptance criterion passed typecheck,
the full suite AND the multi-reviewer code review (goal-run9 Phase 2, story
5.2): general reviewers review the diff, so a file that SHOULD have changed but
didn't is invisible. These tests pin the lever's contract:

- default OFF, and OFF is byte-identical (same lanes, same prompts);
- ON adds exactly one lane, on the MASTER provider, at the master's effort
  (no lean-review notch — it is a gate, not a reviewer);
- the audit lane always gets the FULL audit prompt, never the SP-2 delta;
- a failed audit lane fails the phase (never a silent skip);
- the bundled workflow declares the epic file REQUIRED.
"""

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import yaml

from bmad_assist_lite.compiler.core import get_workflow_compiler
from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.ac_audit_trigger import AuditDecision
from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler
from bmad_assist_lite.providers.base import ProviderResult

# ============================================================================
# Helpers
# ============================================================================


def _config(ac_audit: bool, multi: list[dict[str, Any]] | None = None) -> Any:
    data: dict[str, Any] = {
        "providers": {
            "master": {"provider": "claude", "model": "opus", "effort": "high"},
            "multi": (
                multi
                if multi is not None
                else [
                    {"provider": "claude", "model": "sonnet", "effort": "high"},
                    {"provider": "claude", "model": "haiku", "effort": "high"},
                ]
            ),
        },
        "ac_audit": {"enabled": ac_audit},
    }
    return load_config(data)


def _config_auto(*, enabled: bool = False, auto: bool = False) -> Any:
    return load_config(
        {
            "providers": {
                "master": {"provider": "claude", "model": "opus", "effort": "high"},
                "multi": [
                    {"provider": "claude", "model": "sonnet", "effort": "high"},
                    {"provider": "claude", "model": "haiku", "effort": "high"},
                ],
            },
            "ac_audit": {"enabled": enabled, "auto": auto},
        }
    )


def _state() -> State:
    return State(current_epic=5, current_story="5.2")


def _patched_prompts() -> Any:
    """Patch prompt rendering so no compiler/filesystem work happens.

    ``render_prompt`` echoes which workflow was compiled, so lane prompts
    reveal their origin; ``git_diff`` returns a small diff so the audit
    orientation block is exercised.
    """
    return patch.object(
        CodeReviewHandler,
        "render_prompt",
        side_effect=lambda state, workflow_name=None: (
            f"WF:{workflow_name or 'code-review'}"
        ),
    )


def _lanes(handler: CodeReviewHandler) -> list[dict[str, Any]]:
    with _patched_prompts(), patch(
        "bmad_assist_lite.loop.handlers.code_review.git_diff",
        return_value="diff --git a/x b/x",
    ):
        return handler._build_lanes(_state())


def _stub_multi_run(result: Any) -> Any:
    def _runner(coro: Any) -> Any:
        coro.close()
        return result

    return _runner


# ============================================================================
# Config
# ============================================================================


class TestAcAuditConfig:
    def test_default_off(self):
        config = load_config(
            {"providers": {"master": {"provider": "claude", "model": "opus"}}}
        )
        assert config.ac_audit.enabled is False

    def test_enable_via_config(self):
        assert _config(ac_audit=True).ac_audit.enabled is True


# ============================================================================
# Lane composition
# ============================================================================


class TestLaneComposition:
    def test_off_is_reviewers_only(self, tmp_path):
        """LOAD-BEARING: disabled ⇒ the lane set is the pre-lever reviewer set."""
        handler = CodeReviewHandler(_config(ac_audit=False), tmp_path)
        lanes = _lanes(handler)
        assert [lane["label"] for lane in lanes] == ["Reviewer-1", "Reviewer-2"]
        assert all(lane["prompt"].startswith("WF:code-review") for lane in lanes)

    def test_on_appends_exactly_one_audit_lane(self, tmp_path):
        handler = CodeReviewHandler(_config(ac_audit=True), tmp_path)
        lanes = _lanes(handler)
        assert [lane["label"] for lane in lanes] == [
            "Reviewer-1",
            "Reviewer-2",
            "AC-Auditor",
        ]

    def test_audit_lane_runs_on_master_at_master_effort(self, tmp_path):
        """The gate is not a reviewer: master provider/model, NO effort notch."""
        handler = CodeReviewHandler(_config(ac_audit=True), tmp_path)
        lanes = _lanes(handler)
        audit = lanes[-1]
        assert audit["provider"] == "claude"
        assert audit["model"] == "opus"
        assert audit["effort"] == "high"
        # Reviewer lanes ARE notched (speed.lean_review defaults on).
        assert lanes[0]["effort"] == "medium"

    def test_audit_prompt_is_the_audit_workflow(self, tmp_path):
        handler = CodeReviewHandler(_config(ac_audit=True), tmp_path)
        lanes = _lanes(handler)
        assert lanes[-1]["prompt"].startswith("WF:ac-audit")
        # Diff orientation block + structured findings contract ride along.
        assert "<changed-code-diff>" in lanes[-1]["prompt"]
        assert "BMAD-FINDINGS" in lanes[-1]["prompt"]

    def test_audit_lane_ignores_delta_round(self, tmp_path):
        """Round-2 delta (SP-2) must never scope the audit down to the fix diff."""
        handler = CodeReviewHandler(_config(ac_audit=True), tmp_path)
        state = State(
            current_epic=5,
            current_story="5.2",
            review_iteration=1,
            review_story_id="5.2",
        )
        with _patched_prompts(), patch(
            "bmad_assist_lite.loop.handlers.code_review.git_diff",
            return_value="diff --git a/x b/x",
        ), patch.object(
            CodeReviewHandler,
            "_build_delta_review_prompt",
            return_value="DELTA",
        ):
            lanes = handler._build_lanes(state)
        assert lanes[0]["prompt"].startswith("DELTA")
        assert lanes[-1]["prompt"].startswith("WF:ac-audit")


# ============================================================================
# Auto-trigger (goal-run11) lane composition
# ============================================================================


class TestAutoTriggerLanes:
    def test_both_off_is_byte_identical(self, tmp_path):
        """LOAD-BEARING: auto=false AND enabled=false ⇒ pre-lever reviewer set."""
        handler = CodeReviewHandler(_config_auto(enabled=False, auto=False), tmp_path)
        lanes = _lanes(handler)
        assert [lane["label"] for lane in lanes] == ["Reviewer-1", "Reviewer-2"]
        assert all(lane["prompt"].startswith("WF:code-review") for lane in lanes)

    def test_auto_fires_on_uncertain_signals(self, tmp_path):
        """auto=true over a non-git tmp dir ⇒ signals unavailable ⇒ audit lane."""
        handler = CodeReviewHandler(_config_auto(auto=True), tmp_path)
        lanes = _lanes(handler)
        assert [lane["label"] for lane in lanes] == [
            "Reviewer-1",
            "Reviewer-2",
            "AC-Auditor",
        ]

    def test_auto_quiet_decision_omits_audit_lane(self, tmp_path):
        """When the resolver returns quiet, no audit lane is appended."""
        with patch(
            "bmad_assist_lite.loop.handlers.code_review.resolve_ac_audit_enabled",
            return_value=AuditDecision(fire=False, mode="auto", reason="quiet(...)"),
        ):
            handler = CodeReviewHandler(_config_auto(auto=True), tmp_path)
            lanes = _lanes(handler)
        assert [lane["label"] for lane in lanes] == ["Reviewer-1", "Reviewer-2"]

    def test_auto_records_decision_to_durable_jsonl(self, tmp_path, monkeypatch):
        """Every auto decision is recorded for the Phase-4 trigger-accuracy harvest."""
        import types

        import bmad_assist_lite.core.paths as paths_mod

        bmad_dir = tmp_path / ".bmad-assist-lite"
        bmad_dir.mkdir()
        fake = types.SimpleNamespace(
            bmad_assist_dir=bmad_dir, epics_dir=tmp_path, stories_dir=tmp_path
        )
        monkeypatch.setattr(paths_mod, "get_paths", lambda: fake)
        handler = CodeReviewHandler(_config_auto(auto=True), tmp_path)
        _lanes(handler)
        record = bmad_dir / "ac-audit-trigger.jsonl"
        assert record.exists()
        body = record.read_text()
        assert '"fired": true' in body
        assert '"mode": "auto"' in body


# ============================================================================
# Execute semantics
# ============================================================================


def _run_execute(tmp_path: Any, config: Any, results: list[dict[str, Any]]) -> Any:
    handler = CodeReviewHandler(config, tmp_path)
    with _patched_prompts(), patch(
        "bmad_assist_lite.loop.handlers.code_review.git_diff",
        return_value="diff --git a/x b/x",
    ), patch(
        "bmad_assist_lite.loop.handlers.code_review.run_async_in_thread",
        side_effect=_stub_multi_run(results),
    ):
        return handler.execute(_state())


class TestExecute:
    def test_failed_audit_lane_fails_the_phase(self, tmp_path):
        """LOAD-BEARING: a silently skipped audit would recreate the blind spot."""
        result = _run_execute(
            tmp_path,
            _config(ac_audit=True),
            [
                {"reviewer": "Reviewer-1", "response": "ok", "exit_code": 0},
                {"reviewer": "Reviewer-2", "response": "ok", "exit_code": 0},
                {"reviewer": "AC-Auditor", "error": "timeout", "exit_code": 1},
            ],
        )
        assert not result.success
        assert "audit" in (result.error or "").lower()

    def test_all_lanes_ok_passes_and_caches_the_audit(self, tmp_path):
        result = _run_execute(
            tmp_path,
            _config(ac_audit=True),
            [
                {"reviewer": "Reviewer-1", "response": "ok", "exit_code": 0},
                {"reviewer": "Reviewer-2", "response": "ok", "exit_code": 0},
                {"reviewer": "AC-Auditor", "response": "audited", "exit_code": 0},
            ],
        )
        assert result.success
        cached = (tmp_path / ".bmad-assist-lite" / "cache" / "reviews.json").read_text()
        assert "AC-Auditor" in cached

    def test_failed_reviewer_lane_still_passes(self, tmp_path):
        """NEG: reviewer-lane availability semantics are unchanged by the lever."""
        result = _run_execute(
            tmp_path,
            _config(ac_audit=True),
            [
                {"reviewer": "Reviewer-1", "error": "boom", "exit_code": 1},
                {"reviewer": "Reviewer-2", "response": "ok", "exit_code": 0},
                {"reviewer": "AC-Auditor", "response": "audited", "exit_code": 0},
            ],
        )
        assert result.success

    def test_empty_multi_warns_loudly_and_falls_back(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING)
        provider = MagicMock()
        provider.provider_name = "claude"
        provider.default_model = "opus"
        provider.invoke.return_value = ProviderResult(
            stdout="review body",
            stderr="",
            exit_code=0,
            duration_ms=1,
            model="opus",
            command=("claude",),
        )
        handler = CodeReviewHandler(_config(ac_audit=True, multi=[]), tmp_path)
        with patch(
            "bmad_assist_lite.loop.handlers.base.get_provider", return_value=provider
        ), _patched_prompts():
            handler.execute(_state())
        text = "\n".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "ac_audit" in text
        assert "SKIPPED" in text


# ============================================================================
# Workflow bundle
# ============================================================================


class TestAuditWorkflowBundle:
    def test_compiler_resolves_by_name(self):
        compiler = get_workflow_compiler("ac-audit")
        assert compiler.workflow_name == "ac-audit"

    def test_epic_file_is_required(self):
        """LOAD-BEARING: the audit must never silently run without the epic ACs."""
        import importlib.resources

        ref = (
            importlib.resources.files("bmad_assist_lite.workflows")
            / "ac-audit"
            / "workflow.yaml"
        )
        data = yaml.safe_load(ref.read_text(encoding="utf-8"))
        assert data["input_file_patterns"]["epic_file"]["required"] is True
        assert data["input_file_patterns"]["story_file"]["required"] is True

    def test_instructions_carry_the_handoff_protocol(self):
        import importlib.resources

        ref = (
            importlib.resources.files("bmad_assist_lite.workflows")
            / "ac-audit"
            / "instructions.xml"
        )
        text = ref.read_text(encoding="utf-8")
        assert "CONSUMING side" in text
        assert "scope boundary" in text
        assert "intent_gap" in text
