"""Tests for bmad_assist_lite.core.command_runner."""

import sys

from bmad_assist_lite.core.command_runner import run_command


class TestRunCommand:
    """Tests for run_command."""

    def test_successful_command(self, tmp_path):
        """Successful command returns exit_code=0."""
        if sys.platform == "win32":
            result = run_command("echo hello", tmp_path)
        else:
            result = run_command("echo hello", tmp_path)
        assert result.success is True
        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.duration_ms >= 0

    def test_failed_command(self, tmp_path):
        """Failed command returns non-zero exit code."""
        if sys.platform == "win32":
            result = run_command("cmd /c exit 1", tmp_path)
        else:
            result = run_command("false", tmp_path)
        assert result.success is False
        assert result.exit_code != 0

    def test_timeout_returns_error(self, tmp_path):
        """Timed out command returns exit_code=124."""
        if sys.platform == "win32":
            cmd = "ping -n 10 127.0.0.1"
        else:
            cmd = "sleep 10"
        result = run_command(cmd, tmp_path, timeout=1)
        assert result.success is False
        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()

    def test_command_captures_stderr(self, tmp_path):
        """Command stderr is captured."""
        if sys.platform == "win32":
            result = run_command("cmd /c echo error>&2", tmp_path)
        else:
            result = run_command("echo error >&2", tmp_path)
        assert "error" in result.stderr

    def test_command_string_stored(self, tmp_path):
        """The command string is stored in the result."""
        result = run_command("echo test", tmp_path)
        assert result.command == "echo test"
