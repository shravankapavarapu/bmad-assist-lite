"""Tests for CursorProvider (Story 11.3: CursorProvider Core).

Covers all acceptance criteria:
- TestCursorProviderInit: Class properties (provider_name, default_model)
- TestCursorSupportsModel: composer-* accepted, others rejected
- TestCursorBuildCommand: Write mode flags, read-only, prompt position
- TestCursorParseOutput: Returns result.stdout.strip()
- TestCursorDoInvoke: Valid NDJSON stream -> ProviderResult
- TestCursorModelMismatch: Cost guard on model mismatch
- TestCursorMalformedNDJSON: Tolerant parsing, DEBUG logged
- TestCursorNonZeroExitAfterResult: Non-zero exit after result -> success
- TestCursorNoResultEvent: No result event -> ProviderError
- TestCursorTimeout: TimeoutExpired -> TimeoutError
- TestCursorToolActivity: Tool events mark collector activity
- TestCursorCleanup: Process killed if running, threads joined
- TestCursorResultEventError: result event with is_error:true -> ProviderError
- TestCursorBinaryNotFound: Popen FileNotFoundError -> ProviderError
- TestCursorVersionCacheReset: _reset_cursor_cli_version clears cache

All tests mock subprocess.Popen -- NO live CLI invocation (NFR2).
"""

import json
import logging
from subprocess import TimeoutExpired
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.core.exceptions import ProviderError, ProviderTimeoutError
from bmad_assist_lite.providers.base import BaseProvider, ProviderResult
from bmad_assist_lite.providers.cursor import (
    DEFAULT_CURSOR_MODEL,
    CursorProvider,
    _reset_cursor_cli_version,
)
from bmad_assist_lite.providers.result_collector import ResultCollector

# ============================================================================
# NDJSON Fixtures -- Cursor CLI event builders
# ============================================================================


def make_ndjson_line(data: dict) -> str:  # type: ignore[type-arg]
    """Create a single NDJSON line from a dict."""
    return json.dumps(data) + "\n"


def make_system_init(model: str = "composer-2.5") -> str:
    """Create a system init NDJSON event."""
    return make_ndjson_line({
        "type": "system",
        "subtype": "init",
        "model": model,
        "permissionMode": "default",
    })


def make_assistant_message(text: str) -> str:
    """Create an assistant message NDJSON event."""
    return make_ndjson_line({
        "type": "message",
        "message": {
            "content": [{"type": "text", "text": text}],
        },
    })


def make_tool_call_started(tool_name: str = "Bash") -> str:
    """Create a tool_call_started NDJSON event."""
    return make_ndjson_line({
        "type": "tool_call_started",
        "tool_name": tool_name,
    })


def make_tool_call_completed(tool_name: str = "Bash") -> str:
    """Create a tool_call_completed NDJSON event."""
    return make_ndjson_line({
        "type": "tool_call_completed",
        "tool_name": tool_name,
    })


def make_result_event(
    text: str = "Final result",
    session_id: str = "abc-123",
    is_error: bool = False,
    subtype: str = "success",
) -> str:
    """Create a terminal result NDJSON event."""
    return make_ndjson_line({
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": text,
        "session_id": session_id,
    })


def build_ndjson_stream(*messages: str) -> str:
    """Combine multiple NDJSON lines into a complete stream."""
    return "".join(messages)


def create_mock_process(
    stdout_content: str = "",
    stderr_content: str = "",
    returncode: int = 0,
    wait_side_effect: object = None,
) -> MagicMock:
    """Create a mock subprocess.Popen with configurable behavior."""
    process = MagicMock()
    process.pid = 12345

    # Create line-by-line iterators for stdout/stderr
    stdout_lines = stdout_content.split("\n") if stdout_content else []
    stderr_lines = stderr_content.split("\n") if stderr_content else []

    # readline() returns lines one at a time, then "" for EOF
    stdout_readline_values = [line + "\n" for line in stdout_lines if line] + [""]
    stderr_readline_values = [line + "\n" for line in stderr_lines if line] + [""]

    process.stdout = MagicMock()
    process.stdout.readline = MagicMock(side_effect=stdout_readline_values)
    process.stdout.close = MagicMock()

    process.stderr = MagicMock()
    process.stderr.readline = MagicMock(side_effect=stderr_readline_values)
    process.stderr.close = MagicMock()

    if wait_side_effect is not None:
        process.wait = MagicMock(side_effect=wait_side_effect)
    else:
        process.wait = MagicMock(return_value=returncode)

    process.poll = MagicMock(return_value=returncode)
    process.returncode = returncode

    return process


# Standard valid NDJSON stream for reuse
VALID_STREAM = build_ndjson_stream(
    make_system_init("composer-2.5"),
    make_assistant_message("Hello from Cursor"),
    make_tool_call_started("Bash"),
    make_tool_call_completed("Bash"),
    make_result_event("Final result text", "session-uuid-123"),
)


# ============================================================================
# TestCursorProviderInit -- Basic provider properties
# ============================================================================


class TestCursorProviderInit:
    """Test basic provider property implementations."""

    def test_provider_name(self) -> None:
        """provider_name returns 'cursor'."""
        provider = CursorProvider()
        assert provider.provider_name == "cursor"

    def test_default_model(self) -> None:
        """default_model returns DEFAULT_CURSOR_MODEL."""
        provider = CursorProvider()
        assert provider.default_model == DEFAULT_CURSOR_MODEL
        assert provider.default_model == "composer-2.5"

    def test_is_base_provider_subclass(self) -> None:
        """CursorProvider is a BaseProvider subclass."""
        provider = CursorProvider()
        assert isinstance(provider, BaseProvider)

    def test_initial_process_is_none(self) -> None:
        """New instance has _current_process=None."""
        provider = CursorProvider()
        assert provider._current_process is None

    def test_initial_threads_are_none(self) -> None:
        """New instance has _stdout_thread=None, _stderr_thread=None."""
        provider = CursorProvider()
        assert provider._stdout_thread is None
        assert provider._stderr_thread is None

    def test_invoke_is_base_class_method(self) -> None:
        """CursorProvider.invoke is BaseProvider.invoke (not overridden)."""
        assert CursorProvider.invoke is BaseProvider.invoke
        assert "invoke" not in CursorProvider.__dict__


# ============================================================================
# TestCursorSupportsModel -- Model acceptance boundaries (AC #7)
# ============================================================================


class TestCursorSupportsModel:
    """Test supports_model() acceptance and rejection."""

    def test_accepts_composer_2_5(self) -> None:
        """composer-2.5 returns True."""
        provider = CursorProvider()
        assert provider.supports_model("composer-2.5") is True

    def test_accepts_composer_2_5_fast(self) -> None:
        """composer-2.5-fast returns True."""
        provider = CursorProvider()
        assert provider.supports_model("composer-2.5-fast") is True

    def test_accepts_composer_1(self) -> None:
        """composer-1 returns True."""
        provider = CursorProvider()
        assert provider.supports_model("composer-1") is True

    def test_rejects_auto(self) -> None:
        """auto returns False."""
        provider = CursorProvider()
        assert provider.supports_model("auto") is False

    def test_rejects_gpt(self) -> None:
        """gpt-5.3-codex returns False."""
        provider = CursorProvider()
        assert provider.supports_model("gpt-5.3-codex") is False

    def test_rejects_claude(self) -> None:
        """claude-opus returns False."""
        provider = CursorProvider()
        assert provider.supports_model("claude-opus") is False

    def test_rejects_empty_string(self) -> None:
        """Empty string returns False."""
        provider = CursorProvider()
        assert provider.supports_model("") is False


# ============================================================================
# TestCursorBuildCommand -- Command construction (AC #8)
# ============================================================================


class TestCursorBuildCommand:
    """Test _build_command() command construction."""

    def test_write_mode_includes_force_and_trust(self) -> None:
        """Write mode (allowed_tools=None) includes --force and --trust."""
        provider = CursorProvider()
        cmd = provider._build_command(
            "/usr/bin/cursor-agent", "composer-2.5", "test prompt", write_mode=True
        )
        assert "--force" in cmd
        assert "--trust" in cmd

    def test_read_only_omits_force_keeps_trust(self) -> None:
        """Read-only mode omits --force but keeps --trust (D1: headless)."""
        provider = CursorProvider()
        cmd = provider._build_command(
            "/usr/bin/cursor-agent", "composer-2.5", "test prompt", write_mode=False
        )
        assert "--force" not in cmd
        assert "--trust" in cmd  # D1: --trust always in headless invocations

    def test_prompt_is_final_element(self) -> None:
        """Prompt is the last element in argv."""
        provider = CursorProvider()
        cmd = provider._build_command(
            "/usr/bin/cursor-agent", "composer-2.5", "my prompt text", write_mode=True
        )
        assert cmd[-1] == "my prompt text"

    def test_base_command_structure(self) -> None:
        """Command includes binary, -p, --output-format, stream-json, --model."""
        provider = CursorProvider()
        cmd = provider._build_command(
            "/usr/bin/cursor-agent", "composer-2.5", "prompt", write_mode=False
        )
        assert cmd[0] == "/usr/bin/cursor-agent"
        assert "-p" in cmd
        assert "--output-format" in cmd
        fmt_idx = cmd.index("--output-format")
        assert cmd[fmt_idx + 1] == "stream-json"
        assert "--model" in cmd
        model_idx = cmd.index("--model")
        assert cmd[model_idx + 1] == "composer-2.5"


# ============================================================================
# TestCursorParseOutput -- Output parsing (AC #1)
# ============================================================================


class TestCursorParseOutput:
    """Test parse_output() returns result.stdout.strip()."""

    def test_returns_stripped_stdout(self) -> None:
        """Returns result.stdout.strip()."""
        provider = CursorProvider()
        result = ProviderResult(
            stdout="  some response text  ",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="composer-2.5",
            command=("cursor-agent",),
        )
        assert provider.parse_output(result) == "some response text"

    def test_empty_stdout(self) -> None:
        """Empty stdout returns empty string."""
        provider = CursorProvider()
        result = ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="composer-2.5",
            command=("cursor-agent",),
        )
        assert provider.parse_output(result) == ""


# ============================================================================
# TestCursorDoInvoke -- Valid NDJSON stream -> ProviderResult (AC #1)
# ============================================================================


class TestCursorDoInvoke:
    """Test _do_invoke() with valid NDJSON streams."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_valid_stream_returns_provider_result(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Valid init+assistant+tool+result NDJSON -> ProviderResult with correct fields."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()
        result = provider.invoke(
            "test prompt", model="composer-2.5", timeout=300
        )

        assert result.timed_out is False
        # AC #1: stdout carries the result-event text only (not assistant chunks)
        assert result.stdout == "Final result text"
        assert result.provider_session_id == "session-uuid-123"
        assert result.model == "composer-2.5"

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_stdin_is_devnull(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Cursor uses stdin=DEVNULL (prompt via argv, not stdin)."""
        import subprocess as subprocess_mod

        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()
        provider.invoke("test prompt", model="composer-2.5", timeout=300)

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["stdin"] == subprocess_mod.DEVNULL

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_duration_ms_is_non_negative(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Duration is measured and non-negative."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()
        result = provider.invoke("test", timeout=300)

        assert result.duration_ms >= 0

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_write_mode_when_allowed_tools_none(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """allowed_tools=None -> --force --trust in command."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()
        provider.invoke("test prompt", timeout=300)  # allowed_tools defaults to None

        call_args = mock_popen.call_args[0][0]
        assert "--force" in call_args
        assert "--trust" in call_args

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_read_only_mode_when_allowed_tools_set(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """allowed_tools=list -> no --force in command, --trust still present."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()
        provider.invoke(
            "test prompt",
            allowed_tools=["Read", "Glob"],
            timeout=300,
        )

        call_args = mock_popen.call_args[0][0]
        assert "--force" not in call_args
        assert "--trust" in call_args  # D1: always in headless invocations

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_tool_restriction_prompt_appended(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """allowed_tools parameter produces TOOL ACCESS RESTRICTIONS text in prompt."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()
        provider.invoke(
            "test prompt",
            allowed_tools=["Read", "Glob", "Grep"],
            timeout=300,
        )

        # The prompt with restriction text is the last argv element
        call_args = mock_popen.call_args[0][0]
        prompt_arg = call_args[-1]
        assert "TOOL ACCESS RESTRICTIONS" in prompt_arg
        assert "FORBIDDEN" in prompt_arg


# ============================================================================
# TestCursorModelMismatch -- Cost guard (AC #2)
# ============================================================================


class TestCursorModelMismatch:
    """Test model mismatch cost guard warning."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_model_mismatch_logged_and_recorded(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Init event reports different model -> WARNING, result reflects actual."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            make_system_init("composer-2.5-fast"),
            make_assistant_message("response"),
            make_result_event("done", "sid-1"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with caplog.at_level(logging.WARNING):
            result = provider.invoke("test", model="composer-2.5", timeout=300)

        # Result model reflects actual (from init event)
        assert result.model == "composer-2.5-fast"
        # Warning was logged
        assert any("model mismatch" in r.message.lower() for r in caplog.records)


# ============================================================================
# TestCursorMalformedNDJSON -- Tolerant parsing (AC #3)
# ============================================================================


class TestCursorMalformedNDJSON:
    """Test tolerant NDJSON parsing."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_malformed_lines_skipped_no_exception(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Malformed JSON lines are skipped and logged at DEBUG."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            "{invalid json\n",
            "\n",  # empty line
            make_system_init("composer-2.5"),
            "also broken}\n",
            make_result_event("result text", "sid-2"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with caplog.at_level(logging.DEBUG):
            result = provider.invoke("test", model="composer-2.5", timeout=300)

        assert result.stdout == "result text"
        assert result.timed_out is False
        # DEBUG log for malformed lines
        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("malformed" in m.lower() for m in debug_messages)

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_unknown_event_types_ignored(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Unknown event types are silently ignored."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            make_system_init("composer-2.5"),
            make_ndjson_line({"type": "unknown_future_event", "data": "foo"}),
            make_result_event("final", "sid-3"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()
        result = provider.invoke("test", model="composer-2.5", timeout=300)

        assert result.stdout == "final"


# ============================================================================
# TestCursorNonZeroExitAfterResult -- Known upstream quirk (AC #4)
# ============================================================================


class TestCursorNonZeroExitAfterResult:
    """Test non-zero exit code after result event is treated as success."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_nonzero_exit_after_result_is_success(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Non-zero exit code after result event -> success, exit code logged."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            make_system_init("composer-2.5"),
            make_result_event("success text", "sid-4"),
        )
        process = create_mock_process(stdout_content=stream, returncode=1)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with caplog.at_level(logging.INFO):
            result = provider.invoke("test", model="composer-2.5", timeout=300)

        assert result.timed_out is False
        assert result.stdout == "success text"
        assert result.exit_code == 1
        # Log mentions upstream quirk
        assert any("upstream quirk" in r.message.lower() for r in caplog.records)


# ============================================================================
# TestCursorNoResultEvent -- No result event errors (AC #5)
# ============================================================================


class TestCursorNoResultEvent:
    """Test error handling when no result event is received."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_no_result_nonzero_exit_raises_provider_error(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """No result event + non-zero exit -> ProviderError with stderr tail."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            make_system_init("composer-2.5"),
            make_assistant_message("partial response"),
        )
        process = create_mock_process(
            stdout_content=stream,
            stderr_content="Error: API key invalid",
            returncode=1,
        )
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with pytest.raises(ProviderError, match="no result event"):
            provider.invoke("test", model="composer-2.5", timeout=300)

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_no_result_zero_exit_raises_provider_error(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """No result event + zero exit -> ProviderError."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            make_system_init("composer-2.5"),
            make_assistant_message("some text"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with pytest.raises(ProviderError, match="without result event"):
            provider.invoke("test", model="composer-2.5", timeout=300)

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_no_result_stderr_truncated(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Long stderr is truncated in error message."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            make_system_init("composer-2.5"),
        )
        long_stderr = "a" * 300 + "b" * 301
        process = create_mock_process(
            stdout_content=stream,
            stderr_content=long_stderr,
            returncode=1,
        )
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with pytest.raises(ProviderError) as exc_info:
            provider.invoke("test", model="composer-2.5", timeout=300)

        error_msg = str(exc_info.value)
        assert "..." in error_msg


# ============================================================================
# TestCursorTimeout -- TimeoutExpired -> TimeoutError (AC #6)
# ============================================================================


class TestCursorTimeout:
    """Test timeout handling and propagation."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_timeout_expired_becomes_timeout_error(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """TimeoutExpired -> ProviderTimeoutError (via base class)."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(
            stdout_content="",
            wait_side_effect=TimeoutExpired(cmd="cursor-agent", timeout=10),
        )
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test", timeout=10)

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_timeout_with_enough_text_returns_partial(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Timeout with >= 200 chars captured -> partial result."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        large_text = "x" * 300
        stream = build_ndjson_stream(
            make_assistant_message(large_text),
        )
        process = create_mock_process(
            stdout_content=stream,
            wait_side_effect=TimeoutExpired(cmd="cursor-agent", timeout=10),
        )
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with patch.object(ResultCollector, "is_active", return_value=False):
            result = provider.invoke("test", timeout=10)

        assert result.timed_out is True
        assert len(result.stdout) >= 200

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_cleanup_called_on_timeout_path(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """_cleanup() called on timeout path via base class finally block."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(
            stdout_content="",
            wait_side_effect=TimeoutExpired(cmd="cursor-agent", timeout=10),
        )
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()
        cleanup_calls: list[bool] = []
        original_cleanup = provider._cleanup

        def tracking_cleanup() -> None:
            cleanup_calls.append(True)
            original_cleanup()

        with (
            patch.object(provider, "_cleanup", side_effect=tracking_cleanup),
            pytest.raises(ProviderTimeoutError),
        ):
            provider.invoke("test", timeout=10)

        assert len(cleanup_calls) == 1


# ============================================================================
# TestCursorToolActivity -- Tool events mark collector activity (AC #6)
# ============================================================================


class TestCursorToolActivity:
    """Test that tool events call collector.add('') for activity tracking."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_tool_events_mark_activity(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Tool call events call collector.add('') for activity tracking."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            make_system_init("composer-2.5"),
            make_tool_call_started("Bash"),
            make_tool_call_completed("Bash"),
            make_tool_call_started("Read"),
            make_tool_call_completed("Read"),
            make_result_event("result", "sid-5"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        # Capture collector via _do_invoke patching
        original_do_invoke = provider._do_invoke
        captured_collector: list[ResultCollector] = []

        def capturing_do_invoke(
            prompt: str,
            *,
            collector: ResultCollector,
            **kwargs: object,
        ) -> ProviderResult:
            captured_collector.append(collector)
            return original_do_invoke(  # type: ignore[arg-type]
                prompt, collector=collector, **kwargs
            )

        with patch.object(provider, "_do_invoke", side_effect=capturing_do_invoke):
            provider.invoke("test", timeout=300)

        assert len(captured_collector) == 1
        # 4 tool events + 1 result text = at least 5 chunks
        assert captured_collector[0].chunk_count >= 5


# ============================================================================
# TestCursorCleanup -- Process termination and thread joining (AC #1)
# ============================================================================


class TestCursorCleanup:
    """Test _cleanup() process termination behavior."""

    def test_cleanup_terminates_running_process(self) -> None:
        """When process.poll() returns None, terminate_process() called."""
        provider = CursorProvider()
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process still running
        mock_process.pid = 12345

        provider._current_process = mock_process

        with patch(
            "bmad_assist_lite.providers.cursor.terminate_process"
        ) as mock_terminate:
            provider._cleanup()

        mock_terminate.assert_called_once_with(12345)
        assert provider._current_process is None

    def test_cleanup_skips_dead_process(self) -> None:
        """When process.poll() returns 0, terminate_process() is NOT called."""
        provider = CursorProvider()
        mock_process = MagicMock()
        mock_process.poll.return_value = 0  # Process already exited

        provider._current_process = mock_process

        with patch(
            "bmad_assist_lite.providers.cursor.terminate_process"
        ) as mock_terminate:
            provider._cleanup()

        mock_terminate.assert_not_called()
        assert provider._current_process is None

    def test_cleanup_handles_none_process(self) -> None:
        """With _current_process=None, no exception raised."""
        provider = CursorProvider()
        provider._current_process = None

        with patch(
            "bmad_assist_lite.providers.cursor.terminate_process"
        ) as mock_terminate:
            provider._cleanup()  # Should not raise

        mock_terminate.assert_not_called()

    def test_cleanup_joins_threads(self) -> None:
        """Stdout and stderr threads are joined with timeout=1."""
        provider = CursorProvider()
        provider._current_process = MagicMock()
        provider._current_process.poll.return_value = 0  # Dead process

        mock_stdout_thread = MagicMock()
        mock_stderr_thread = MagicMock()
        provider._stdout_thread = mock_stdout_thread
        provider._stderr_thread = mock_stderr_thread

        provider._cleanup()

        mock_stdout_thread.join.assert_called_once_with(timeout=1)
        mock_stderr_thread.join.assert_called_once_with(timeout=1)
        assert provider._stdout_thread is None
        assert provider._stderr_thread is None

    def test_cleanup_resets_all_state(self) -> None:
        """After cleanup, process and threads are None."""
        provider = CursorProvider()
        provider._current_process = MagicMock()
        provider._current_process.poll.return_value = 0
        provider._stdout_thread = MagicMock()
        provider._stderr_thread = MagicMock()

        provider._cleanup()

        assert provider._current_process is None
        assert provider._stdout_thread is None
        assert provider._stderr_thread is None

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_cleanup_called_on_success(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """_cleanup() is called even on successful completion."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with patch.object(provider, "_cleanup") as mock_cleanup:
            provider.invoke("test prompt", timeout=300)
            mock_cleanup.assert_called_once()


# ============================================================================
# TestCursorResultEventError -- result with is_error:true (Validation Finding)
# ============================================================================


class TestCursorResultEventError:
    """Test result event with is_error:true raises ProviderError."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_result_is_error_true_raises(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Result event with is_error:true -> ProviderError."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            make_system_init("composer-2.5"),
            make_result_event(
                "Something went wrong",
                "sid-err",
                is_error=True,
                subtype="error",
            ),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with pytest.raises(ProviderError, match="result event reported error"):
            provider.invoke("test", model="composer-2.5", timeout=300)

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_result_non_success_subtype_raises(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Result event with subtype != 'success' -> ProviderError."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        stream = build_ndjson_stream(
            make_system_init("composer-2.5"),
            make_result_event(
                "Failed operation",
                "sid-err2",
                is_error=False,
                subtype="failure",
            ),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with pytest.raises(ProviderError, match="result event reported error"):
            provider.invoke("test", model="composer-2.5", timeout=300)


# ============================================================================
# TestCursorBinaryNotFound -- Popen FileNotFoundError (Validation Finding)
# ============================================================================


class TestCursorBinaryNotFound:
    """Test FileNotFoundError from Popen is wrapped as ProviderError."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_popen_file_not_found_raises_provider_error(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """Popen raises FileNotFoundError -> ProviderError with config hint."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        mock_popen.side_effect = FileNotFoundError("No such file")

        provider = CursorProvider()
        _reset_cursor_cli_version()

        with pytest.raises(ProviderError, match="Cursor CLI binary not found"):
            provider.invoke("test", timeout=300)

    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    def test_resolve_cli_path_not_found(
        self, mock_resolve_cli: MagicMock
    ) -> None:
        """resolve_cli_path raises ProviderError when cursor not found."""
        mock_resolve_cli.side_effect = ProviderError("cursor CLI not found")

        provider = CursorProvider()
        with pytest.raises(ProviderError, match="cursor CLI not found"):
            provider.invoke("test", timeout=300)


# ============================================================================
# TestCursorVersionCacheReset -- Singleton reset for test isolation
# ============================================================================


class TestCursorVersionCacheReset:
    """Test _reset_cursor_cli_version() clears the cache."""

    @patch("bmad_assist_lite.providers.cursor.subprocess")
    @patch("bmad_assist_lite.providers.cursor.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.cursor.resolve_cli_path")
    @patch("bmad_assist_lite.providers.cursor.Popen")
    def test_reset_clears_cache(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_subprocess: MagicMock,
    ) -> None:
        """_reset_cursor_cli_version() clears cache, next invocation re-runs --version."""
        mock_resolve_cli.return_value = "/usr/bin/cursor-agent"
        process = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process

        # Mock subprocess.run for version check
        version_result = MagicMock()
        version_result.stdout = "1.0.0"
        mock_subprocess.run.return_value = version_result

        provider = CursorProvider()
        _reset_cursor_cli_version()

        # First invocation should call subprocess.run for --version
        provider.invoke("test1", timeout=300)
        first_call_count = mock_subprocess.run.call_count

        # Second invocation should NOT re-run --version (cached)
        process2 = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process2
        provider.invoke("test2", timeout=300)
        assert mock_subprocess.run.call_count == first_call_count

        # Reset cache
        _reset_cursor_cli_version()

        # Third invocation should re-run --version
        process3 = create_mock_process(stdout_content=VALID_STREAM, returncode=0)
        mock_popen.return_value = process3
        provider.invoke("test3", timeout=300)
        assert mock_subprocess.run.call_count == first_call_count + 1


# ============================================================================
# TestCursorRegistryIntegration -- Provider registry (AC #9)
# ============================================================================


class TestCursorRegistryIntegration:
    """Test that CursorProvider integrates with the provider registry."""

    def test_cursor_in_registry(self) -> None:
        """'cursor' maps to CursorProvider in the registry."""
        from bmad_assist_lite.providers import _reset_registry, get_provider

        _reset_registry()
        provider = get_provider("cursor")
        assert isinstance(provider, CursorProvider)
        assert provider.provider_name == "cursor"
        _reset_registry()

    def test_cursor_in_list_providers(self) -> None:
        """'cursor' appears in list_providers()."""
        from bmad_assist_lite.providers import _reset_registry, list_providers

        _reset_registry()
        providers = list_providers()
        assert "cursor" in providers
        _reset_registry()
