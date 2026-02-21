"""Tests for bmad_assist_lite.loop.handlers.quality_gate."""

from unittest.mock import patch

from bmad_assist_lite.core.command_runner import CommandResult
from bmad_assist_lite.core.config import _reset_config, load_config
from bmad_assist_lite.core.paths import init_paths
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.handlers.quality_gate import QualityGateHandler


def _make_config(**overrides):
    """Create a config with optional quality_gate overrides."""
    data = {
        "providers": {"master": {"provider": "claude", "model": "opus"}},
    }
    if overrides:
        data["quality_gate"] = overrides
    _reset_config()
    return load_config(data)


def _make_state(story="1.1", retry=0):
    return State(
        current_epic=1,
        current_story=story,
        current_phase=Phase.QUALITY_GATE,
        qa_retry_count=retry,
    )


def _ok_result(command="test cmd"):
    return CommandResult(
        command=command, exit_code=0, stdout="OK", stderr="", duration_ms=100
    )


def _fail_result(command="test cmd"):
    return CommandResult(
        command=command, exit_code=1, stdout="", stderr="Error", duration_ms=100
    )


class TestQualityGateHandler:
    """Tests for QualityGateHandler."""

    def test_all_gates_pass(self, tmp_path):
        """All gates passing returns action='pass'."""
        config = _make_config(lint="echo ok", test="echo ok")
        init_paths(tmp_path)
        handler = QualityGateHandler(config, tmp_path)
        state = _make_state()

        with patch(
            "bmad_assist_lite.loop.handlers.quality_gate.run_command",
            return_value=_ok_result(),
        ):
            result = handler.execute(state)

        assert result.success is True
        assert result.outputs["quality_gate_action"] == "pass"
        assert result.next_phase is None

    def test_gate_fails_first_try(self, tmp_path):
        """Gate failure on first try returns next_phase=FIX_QUALITY_GATE."""
        config = _make_config(test="echo fail")
        init_paths(tmp_path)
        handler = QualityGateHandler(config, tmp_path)
        state = _make_state(retry=0)

        with patch(
            "bmad_assist_lite.loop.handlers.quality_gate.run_command",
            return_value=_fail_result(),
        ):
            result = handler.execute(state)

        assert result.success is True
        assert result.next_phase == Phase.FIX_QUALITY_GATE
        assert result.outputs["quality_gate_action"] == "fix"

    def test_gate_fails_on_retry(self, tmp_path):
        """Gate failure on retry returns action='skip_story'."""
        config = _make_config(test="echo fail")
        init_paths(tmp_path)
        handler = QualityGateHandler(config, tmp_path)
        state = _make_state(retry=1)

        with patch(
            "bmad_assist_lite.loop.handlers.quality_gate.run_command",
            return_value=_fail_result(),
        ):
            result = handler.execute(state)

        assert result.success is True
        assert result.outputs["quality_gate_action"] == "skip_story"
        assert result.next_phase is None

    def test_failure_report_written(self, tmp_path):
        """Failure report is written to cache directory."""
        config = _make_config(test="echo fail")
        init_paths(tmp_path)
        handler = QualityGateHandler(config, tmp_path)
        state = _make_state(story="1.2")

        with patch(
            "bmad_assist_lite.loop.handlers.quality_gate.run_command",
            return_value=_fail_result("pytest"),
        ):
            handler.execute(state)

        report = tmp_path / ".bmad-assist-lite" / "cache" / "qa-failures-1.2.md"
        assert report.exists()
        content = report.read_text(encoding="utf-8")
        assert "Quality Gate Failures" in content
        assert "pytest" in content

    def test_no_commands_passes(self, tmp_path):
        """No quality gate commands found results in pass."""
        config = _make_config()
        init_paths(tmp_path)
        handler = QualityGateHandler(config, tmp_path)
        state = _make_state()

        result = handler.execute(state)

        assert result.success is True
        assert result.outputs["quality_gate_action"] == "pass"

    def test_story_file_updated(self, tmp_path):
        """Story file Quality Gates table is updated with PASS/FAIL."""
        config = _make_config()
        init_paths(tmp_path)

        # Create story file with Quality Gates table
        stories_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
        stories_dir.mkdir(parents=True)
        story_file = stories_dir / "story-1.1.md"
        story_file.write_text(
            "# Story\n\n"
            "| Gate | Command | Status |\n"
            "|------|---------|--------|\n"
            "| Lint | `echo ok` | **PENDING** |\n"
        )

        handler = QualityGateHandler(config, tmp_path)
        state = _make_state()

        with patch(
            "bmad_assist_lite.loop.handlers.quality_gate.run_command",
            return_value=_ok_result("echo ok"),
        ):
            result = handler.execute(state)

        assert result.outputs["quality_gate_action"] == "pass"
        content = story_file.read_text(encoding="utf-8")
        assert "**PASS**" in content

    def test_config_test_unit_preferred_over_test(self, tmp_path):
        """Config test_unit is used instead of test for quality gate."""
        config = _make_config(test="echo full-suite", test_unit="echo unit-only")
        init_paths(tmp_path)
        handler = QualityGateHandler(config, tmp_path)
        state = _make_state()

        commands = handler._get_commands(state)
        test_entries = [e for e in commands if e.name == "Tests"]
        assert len(test_entries) == 1
        assert test_entries[0].command == "echo unit-only"

    def test_config_test_fallback_when_no_test_unit(self, tmp_path):
        """Config test is used when test_unit is not set."""
        config = _make_config(test="echo full-suite")
        init_paths(tmp_path)
        handler = QualityGateHandler(config, tmp_path)
        state = _make_state()

        commands = handler._get_commands(state)
        test_entries = [e for e in commands if e.name == "Tests"]
        assert len(test_entries) == 1
        assert test_entries[0].command == "echo full-suite"

    def test_autodetect_test_unit_preferred(self, tmp_path):
        """Auto-detected test_unit is used instead of test."""
        config = _make_config()
        init_paths(tmp_path)
        handler = QualityGateHandler(config, tmp_path)
        state = _make_state()

        with patch(
            "bmad_assist_lite.loop.handlers.quality_gate.detect_toolchain",
        ) as mock_detect:
            from bmad_assist_lite.core.toolchain import ToolchainCommands

            mock_detect.return_value = ToolchainCommands(
                test="pnpm run test", test_unit="pnpm run test:unit"
            )
            commands = handler._get_commands(state)

        test_entries = [e for e in commands if e.name == "Tests"]
        assert len(test_entries) == 1
        assert test_entries[0].command == "pnpm run test:unit"
