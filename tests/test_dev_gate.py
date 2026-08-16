"""Tests for SP-A0 — the real per-story dev gate (quality_gate.real_dev_gate).

goal-run8 flipped real_dev_gate ON by default, so these tests isolate SP-A0 by
disabling the adaptive lever (lean_dev_adaptive=False) in the shared helper and
setting real_dev_gate explicitly where the "gate off" path is under test. On, the
gate runs the configured real commands in the story worktree and records an
objective verdict; SP-A0 alone never blocks and never retries.
"""

from types import SimpleNamespace
from unittest.mock import patch

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.dev_gate import DevGateHandler
from bmad_assist_lite.loop.runner import effective_story_phases

_PROVIDERS = {"providers": {"master": {"provider": "claude", "model": "opus"}}}


def _cfg(**quality_gate: object):
    # Isolate SP-A0: adaptive is on by default now, so disable it here so the gate's
    # own advance/no-retry behaviour is what's under test (adaptive is test_adaptive_dev).
    data: dict = dict(_PROVIDERS)
    data["speed"] = {"lean_dev_adaptive": False}
    if quality_gate:
        data["quality_gate"] = quality_gate
    return load_config(data)


def _run_result(all_passed: bool, failed_names: tuple[str, ...] = ()):
    failures = [SimpleNamespace(name=n) for n in failed_names]
    return SimpleNamespace(
        all_passed=all_passed,
        failures=failures,
        overall_classification=SimpleNamespace(value="real"),
    )


class TestDevGateConfig:
    """real_dev_gate defaults ON when a quality_gate group is present; commands parse."""

    def test_group_absent_defaults_to_none(self) -> None:
        assert _cfg().quality_gate is None

    def test_real_dev_gate_defaults_on_when_group_present(self) -> None:
        assert _cfg(typecheck="true").quality_gate.real_dev_gate is True

    def test_commands_parse(self) -> None:
        cfg = _cfg(
            real_dev_gate=True,
            real_dev_gate_commands=[
                {"name": "Typecheck", "command": "pnpm typecheck"},
                {"name": "Tests", "command": "pnpm test -- ingest"},
            ],
        )
        cmds = cfg.quality_gate.real_dev_gate_commands
        assert [c.name for c in cmds] == ["Typecheck", "Tests"]
        assert cmds[1].command == "pnpm test -- ingest"

    def test_gate_timeout_defaults_suite_sized(self, tmp_path) -> None:
        # The dev gate runs a FULL suite, so it gets its own 900s default rather
        # than sharing command_timeout (120s) with the quick fallback checks.
        handler = DevGateHandler(_cfg(typecheck="true"), tmp_path)
        assert handler._timeout() == 900

    def test_gate_timeout_independent_of_command_timeout(self, tmp_path) -> None:
        handler = DevGateHandler(_cfg(typecheck="true", command_timeout=60), tmp_path)
        assert handler._timeout() == 900

    def test_gate_timeout_configurable(self, tmp_path) -> None:
        cfg = _cfg(typecheck="true", real_dev_gate_command_timeout=300)
        assert DevGateHandler(cfg, tmp_path)._timeout() == 300


class TestEffectivePhases:
    """dev_gate is inserted only when the gate is on; off is byte-identical."""

    def test_off_is_unchanged(self) -> None:
        cfg = _cfg()  # no quality_gate group => no gate
        assert effective_story_phases(cfg) == list(cfg.loop.story)
        assert "dev_gate" not in effective_story_phases(cfg)

    def test_group_present_flag_off_is_unchanged(self) -> None:
        cfg = _cfg(typecheck="true", real_dev_gate=False)
        assert "dev_gate" not in effective_story_phases(cfg)

    def test_on_inserts_immediately_after_dev_story(self) -> None:
        cfg = _cfg(real_dev_gate=True)
        phases = effective_story_phases(cfg)
        assert phases[phases.index("dev_story") + 1] == "dev_gate"
        # Nothing else moved: removing dev_gate recovers the base list.
        assert [p for p in phases if p != "dev_gate"] == list(cfg.loop.story)

    def test_idempotent_if_already_present(self) -> None:
        cfg = _cfg(real_dev_gate=True)
        once = effective_story_phases(cfg)
        assert once.count("dev_gate") == 1


class TestDevGateHandler:
    """Records the objective verdict; advances regardless (never blocks/retries)."""

    def test_skipped_when_flag_off(self, tmp_path) -> None:
        handler = DevGateHandler(_cfg(typecheck="true", real_dev_gate=False), tmp_path)
        state = State(current_epic=7, current_story="7.2")
        result = handler.execute(state)
        assert result.outputs["dev_gate_action"] == "skipped"
        assert state.dev_gate_records == []

    def test_records_pass(self, tmp_path) -> None:
        cfg = _cfg(
            real_dev_gate=True,
            real_dev_gate_commands=[{"name": "Typecheck", "command": "true"}],
        )
        handler = DevGateHandler(cfg, tmp_path)
        state = State(current_epic=7, current_story="7.2")
        with patch(
            "bmad_assist_lite.loop.handlers.dev_gate.run_gates",
            return_value=_run_result(True),
        ):
            result = handler.execute(state)
        assert result.outputs["dev_gate_action"] == "pass"
        assert result.next_phase is None
        rec = state.dev_gate_records[0]
        assert rec["passed"] is True and rec["failed"] == []
        assert rec["classification"] == "pass" and rec["retry_fired"] is False

    def test_records_fail_but_advances(self, tmp_path) -> None:
        # adaptive off (via _cfg): SP-A0 alone records the fail and advances, no retry.
        cfg = _cfg(
            real_dev_gate=True,
            real_dev_gate_commands=[{"name": "Tests", "command": "false"}],
        )
        handler = DevGateHandler(cfg, tmp_path)
        state = State(current_epic=7, current_story="7.2")
        with patch(
            "bmad_assist_lite.loop.handlers.dev_gate.run_gates",
            return_value=_run_result(False, ("Tests",)),
        ):
            result = handler.execute(state)
        assert result.outputs["dev_gate_action"] == "fail"
        assert result.next_phase is None  # SP-A0 never retries
        assert state.dev_gate_records[0]["passed"] is False
        assert state.dev_gate_records[0]["failed"] == ["Tests"]

    def test_no_commands_records_noop(self, tmp_path) -> None:
        handler = DevGateHandler(_cfg(real_dev_gate=True), tmp_path)
        state = State(current_epic=7, current_story="7.2")
        result = handler.execute(state)
        assert result.outputs["dev_gate_action"] == "no_commands"
        assert state.dev_gate_records[0]["reason"] == "no_commands"

    def test_fallback_commands_from_typecheck_and_test(self, tmp_path) -> None:
        cfg = _cfg(real_dev_gate=True, typecheck="pnpm tc", test="pnpm test")
        handler = DevGateHandler(cfg, tmp_path)
        cmds = handler._commands()
        assert [c.name for c in cmds] == ["Typecheck", "Tests"]
        assert cmds[0].command == "pnpm tc"

    def test_test_unit_preferred_over_test(self, tmp_path) -> None:
        cfg = _cfg(real_dev_gate=True, test_unit="pnpm test:unit", test="pnpm test")
        handler = DevGateHandler(cfg, tmp_path)
        cmds = handler._commands()
        assert cmds[0].command == "pnpm test:unit"
