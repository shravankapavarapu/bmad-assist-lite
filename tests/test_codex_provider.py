"""Tests for CodexProvider (Story 10.6: E2E Testing & Hardening).

Covers all acceptance criteria:
- TestInvocation: Command construction, model flags, stdin=DEVNULL, schema flags
- TestNDJSONParsing: agent_message extraction, malformed handling, empty streams
- TestCleanup: Process kill, thread join, state reset, temp file removal
- TestTimeout: TimeoutExpired -> TimeoutError, partial results, ValueError on invalid
- TestParseOutput: JSON -> evidence text, priority mapping, clean pass, plain text fallback
- TestSupportsModel: Accepted prefixes, rejected models
- TestErrors: CLI not found, auth/rate-limit/network errors, empty response
- TestProviderProperties: Name, model, subclass, initial state

Test numbering per story tasks:
- Task 1: Scaffold and helpers
- Task 2: TestInvocation (command construction)
- Task 3: TestNDJSONParsing (NDJSON stream parsing)
- Task 4: TestCleanup (process termination)
- Task 5: TestTimeout (timeout propagation)
- Task 6: TestParseOutput (JSON -> evidence text)
- Task 7: TestSupportsModel (model acceptance)
- Task 8: TestErrors (error conditions)
- Task 9: TestProviderProperties (basic properties)
"""

import json
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.core.exceptions import (
    ProviderError,
    ProviderExitCodeError,
    ProviderTimeoutError,
)
from bmad_assist_lite.providers.base import BaseProvider, ProviderResult
from bmad_assist_lite.providers.codex import CodexProvider
from bmad_assist_lite.providers.result_collector import ResultCollector

# ============================================================================
# Helpers -- NDJSON stream simulation (Task 1)
# ============================================================================

# A mock Path that returns False for is_file(), disabling structured output
# in tests that don't need it.
_NO_SCHEMA = MagicMock(spec=Path)
_NO_SCHEMA.is_file.return_value = False


def make_ndjson_line(data: dict) -> str:
    """Create a single NDJSON line from a dict."""
    return json.dumps(data) + "\n"


def make_item_completed_agent_message(text: str) -> str:
    """Create an item.completed NDJSON line with agent_message type.

    Builds::

        {"type": "item.completed", "item": {"type": "agent_message",
         "content": [{"type": "output_text", "text": "..."}]}}
    """
    return make_ndjson_line({
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "content": [{"type": "output_text", "text": text}],
        },
    })


def make_item_completed_command_execution(cmd: str) -> str:
    """Create an item.completed NDJSON line with command_execution type."""
    return make_ndjson_line({
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": cmd,
        },
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
    """Create a mock subprocess.Popen with configurable behavior.

    Unlike the Gemini mock, Codex uses stdin=DEVNULL so no stdin mock is needed.
    """
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


# ============================================================================
# TestInvocation -- Command construction and subprocess spawning (Task 2)
# ============================================================================


class TestInvocation:
    """Test command construction and subprocess spawning."""

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_command_includes_exec_json_model(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Verify command includes codex exec --json --model <model> <prompt> (Task 2.1)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("Hello"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test prompt", model="codex-mini-latest", timeout=300)

        call_args = mock_popen.call_args
        command = call_args[0][0]
        assert command[0] == "/usr/bin/codex"
        assert "exec" in command
        assert "--json" in command
        assert "--model" in command
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "codex-mini-latest"
        assert command[-1] == "test prompt"

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_model_flag_uses_explicit_model(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """When model='gpt-5.3-codex', command uses that model (Task 2.2)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", model="gpt-5.3-codex", timeout=300)

        call_args = mock_popen.call_args
        command = call_args[0][0]
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "gpt-5.3-codex"
        assert result.model == "gpt-5.3-codex"

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_model_flag_uses_default_when_none(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """When model=None, uses 'codex-mini-latest' default (Task 2.3)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        call_args = mock_popen.call_args
        command = call_args[0][0]
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "codex-mini-latest"
        assert result.model == "codex-mini-latest"

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_stdin_is_devnull(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Verify Popen is called with stdin=subprocess.DEVNULL (Task 2.4)."""
        import subprocess

        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test", timeout=300)

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["stdin"] == subprocess.DEVNULL

    @patch("bmad_assist_lite.providers.codex.uuid")
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_output_schema_flag_added(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_uuid: MagicMock,
    ) -> None:
        """When schema file exists, command includes --output-schema (Task 2.5)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        mock_uuid.uuid4.return_value = MagicMock(hex="abcdef1234567890")
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        schema_mock = MagicMock(spec=Path)
        schema_mock.is_file.return_value = True
        schema_mock.__str__ = lambda self: "/path/to/schema.json"

        with patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", schema_mock):
            provider = CodexProvider()
            provider.invoke("test", timeout=300, cwd=Path("/tmp/test-project"))

        call_args = mock_popen.call_args
        command = call_args[0][0]
        assert "--output-schema" in command
        schema_idx = command.index("--output-schema")
        assert command[schema_idx + 1] == "/path/to/schema.json"

    @patch("bmad_assist_lite.providers.codex.uuid")
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_output_last_message_flag_added(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_uuid: MagicMock,
    ) -> None:
        """Verify --output-last-message points to cache dir (Task 2.6)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        mock_uuid.uuid4.return_value = MagicMock(hex="abcdef1234567890")
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        schema_mock = MagicMock(spec=Path)
        schema_mock.is_file.return_value = True
        schema_mock.__str__ = lambda self: "/path/to/schema.json"

        with patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", schema_mock):
            provider = CodexProvider()
            provider.invoke("test", timeout=300, cwd=Path("/tmp/test-project"))

        call_args = mock_popen.call_args
        command = call_args[0][0]
        assert "--output-last-message" in command
        olm_idx = command.index("--output-last-message")
        temp_path = command[olm_idx + 1]
        assert ".bmad-assist-lite" in temp_path
        assert "cache" in temp_path
        assert "codex-review-" in temp_path

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_normal_completion_returns_result(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Full NDJSON stream -> ProviderResult with timed_out=False (Task 2.7)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("Hello World"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test prompt", timeout=300)

        assert result.timed_out is False
        assert "Hello World" in result.stdout
        assert result.exit_code == 0

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_cleanup_called_on_success(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """_cleanup() is called even on successful completion (Task 2.8)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("response"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        with patch.object(provider, "_cleanup") as mock_cleanup:
            provider.invoke("test prompt", timeout=300)
            mock_cleanup.assert_called_once()

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_duration_ms_is_non_negative(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Duration is measured and non-negative (Task 2.9)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert result.duration_ms >= 0

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_tool_restriction_prompt(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """allowed_tools parameter produces TOOL ACCESS RESTRICTIONS text (Task 2.10)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test prompt", allowed_tools=["Read", "Glob", "Grep"], timeout=300)

        call_args = mock_popen.call_args
        command = call_args[0][0]
        # The prompt is the last element and should contain restriction text
        prompt_arg = command[-1]
        assert "TOOL ACCESS RESTRICTIONS" in prompt_arg
        assert "FORBIDDEN" in prompt_arg
        assert "Read" in prompt_arg

    def test_invoke_is_base_class_method(self) -> None:
        """CodexProvider.invoke is BaseProvider.invoke (not overridden) (Task 2.11)."""
        assert CodexProvider.invoke is BaseProvider.invoke
        assert "invoke" not in CodexProvider.__dict__


# ============================================================================
# TestNDJSONParsing -- Extract agent_message text (Task 3)
# ============================================================================


class TestNDJSONParsing:
    """Test NDJSON stream parsing for agent_message extraction."""

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_agent_message_text_extracted(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Single item.completed with agent_message -> text in result.stdout (Task 3.1)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(
            make_item_completed_agent_message("Hello from Codex")
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert "Hello from Codex" in result.stdout

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_multiple_agent_messages_captured(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Multiple item.completed events -> all text captured (Task 3.2)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(
            make_item_completed_agent_message("chunk1"),
            make_item_completed_agent_message("chunk2"),
            make_item_completed_agent_message("chunk3"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert "chunk1" in result.stdout
        assert "chunk2" in result.stdout
        assert "chunk3" in result.stdout

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_command_execution_events_ignored_in_text(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """item.completed with command_execution does NOT appear in text (Task 3.3)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(
            make_item_completed_agent_message("real content"),
            make_item_completed_command_execution("ls -la"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert result.stdout == "real content"
        assert "ls -la" not in result.stdout

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_malformed_ndjson_skipped(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Invalid JSON lines are silently skipped without error (Task 3.4)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = "not valid json\n{also broken\n"
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert result.stdout == ""
        assert result.timed_out is False

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_mixed_valid_and_invalid_lines(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Mix of valid NDJSON and garbage -> only valid text captured (Task 3.5)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = (
            "garbage line\n"
            + make_item_completed_agent_message("valid content")
            + "{broken json\n"
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert "valid content" in result.stdout

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_empty_ndjson_stream(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Empty stdout -> empty response text, no errors (Task 3.6)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        process = create_mock_process(stdout_content="", returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert result.stdout == ""
        assert result.timed_out is False

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_agent_message_with_empty_content(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """agent_message with empty content array -> no text added (Task 3.7)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        empty_content_event = make_ndjson_line({
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "content": [],
            },
        })
        stream = build_ndjson_stream(empty_content_event)
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert result.stdout == ""

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_collector_receives_all_chunks(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Verify ResultCollector.add() is called for each agent_message chunk (Task 3.8)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(
            make_item_completed_agent_message("A"),
            make_item_completed_agent_message("B"),
            make_item_completed_agent_message("C"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()

        # Capture the collector reference via _do_invoke patching
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
        assert "A" in captured_collector[0].text
        assert "B" in captured_collector[0].text
        assert "C" in captured_collector[0].text


# ============================================================================
# TestCleanup -- Process termination and thread joining (Task 4)
# ============================================================================


class TestCleanup:
    """Test _cleanup() process termination behavior."""

    def test_cleanup_kills_running_process(self) -> None:
        """When process.poll() returns None, kill_process() is called (Task 4.1)."""
        provider = CodexProvider()
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process still running

        provider._current_process = mock_process

        with patch(
            "bmad_assist_lite.providers.codex.kill_process"
        ) as mock_kill:
            provider._cleanup()

        mock_kill.assert_called_once_with(mock_process)
        assert provider._current_process is None

    def test_cleanup_skips_dead_process(self) -> None:
        """When process.poll() returns 0, kill_process() is NOT called (Task 4.2)."""
        provider = CodexProvider()
        mock_process = MagicMock()
        mock_process.poll.return_value = 0  # Process already exited

        provider._current_process = mock_process

        with patch(
            "bmad_assist_lite.providers.codex.kill_process"
        ) as mock_kill:
            provider._cleanup()

        mock_kill.assert_not_called()
        assert provider._current_process is None

    def test_cleanup_handles_none_process(self) -> None:
        """With _current_process=None, no exception, no kill (Task 4.3)."""
        provider = CodexProvider()
        provider._current_process = None

        with patch(
            "bmad_assist_lite.providers.codex.kill_process"
        ) as mock_kill:
            provider._cleanup()  # Should not raise

        mock_kill.assert_not_called()

    def test_cleanup_joins_threads(self) -> None:
        """Stdout and stderr threads are joined with timeout=1 (Task 4.4)."""
        provider = CodexProvider()
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
        """After cleanup, process, threads, temp_output_path are None (Task 4.5)."""
        provider = CodexProvider()
        provider._current_process = MagicMock()
        provider._current_process.poll.return_value = 0
        provider._stdout_thread = MagicMock()
        provider._stderr_thread = MagicMock()
        provider._temp_output_path = Path("/tmp/some-temp.json")

        provider._cleanup()

        assert provider._current_process is None
        assert provider._stdout_thread is None
        assert provider._stderr_thread is None
        assert provider._temp_output_path is None

    def test_cleanup_removes_temp_file(self) -> None:
        """Temp output file is deleted during cleanup (Task 4.6)."""
        provider = CodexProvider()
        provider._current_process = None  # No process to kill

        mock_temp_path = MagicMock(spec=Path)
        provider._temp_output_path = mock_temp_path

        provider._cleanup()

        mock_temp_path.unlink.assert_called_once_with(missing_ok=True)
        assert provider._temp_output_path is None

    def test_cleanup_preserves_structured_output(self) -> None:
        """_structured_output is NOT reset by cleanup (Task 4.7)."""
        provider = CodexProvider()
        provider._current_process = None
        provider._structured_output = (
            '{"findings": [], "overall_verdict": "PASS", "summary": "ok"}'
        )

        provider._cleanup()

        assert provider._structured_output is not None
        assert "findings" in provider._structured_output

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_cleanup_exception_caught_by_base_class(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """_cleanup() raising OSError does not mask the provider result (Task 4.8)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()

        with patch.object(
            provider, "_cleanup", side_effect=OSError("cleanup failed")
        ):
            # invoke() succeeds despite _cleanup() raising
            result = provider.invoke("test", timeout=300)

        assert result.timed_out is False
        assert "ok" in result.stdout


# ============================================================================
# TestTimeout -- TimeoutExpired -> TimeoutError propagation (Task 5)
# ============================================================================


class TestTimeout:
    """Test timeout propagation to base class for grace period handling."""

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_timeout_expired_becomes_timeout_error(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """TimeoutExpired from process.wait() -> ProviderTimeoutError (Task 5.1)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        process = create_mock_process(
            stdout_content="",
            wait_side_effect=TimeoutExpired(cmd="codex", timeout=10),
        )
        mock_popen.return_value = process

        provider = CodexProvider()
        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test", timeout=10)

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_timeout_with_enough_text_returns_partial(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Timeout with >= 200 chars -> partial result (Task 5.2)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        large_text = "x" * 300
        stream = build_ndjson_stream(make_item_completed_agent_message(large_text))
        process = create_mock_process(
            stdout_content=stream,
            wait_side_effect=TimeoutExpired(cmd="codex", timeout=10),
        )
        mock_popen.return_value = process

        provider = CodexProvider()
        with patch.object(ResultCollector, "is_active", return_value=False):
            result = provider.invoke("test", timeout=10)

        assert result.timed_out is True
        assert len(result.stdout) >= 200

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_timeout_with_no_response_raises_error(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Timeout with no streamed text -> ProviderTimeoutError (Task 5.3)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        process = create_mock_process(
            stdout_content="",
            wait_side_effect=TimeoutExpired(cmd="codex", timeout=10),
        )
        mock_popen.return_value = process

        provider = CodexProvider()
        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test", timeout=10)

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_timeout_collector_has_partial_content(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Collector accumulates text from chunks delivered before timeout (Task 5.4)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(
            make_item_completed_agent_message("partial1"),
            make_item_completed_agent_message("partial2"),
        )
        process = create_mock_process(
            stdout_content=stream,
            wait_side_effect=TimeoutExpired(cmd="codex", timeout=10),
        )
        mock_popen.return_value = process

        provider = CodexProvider()
        with pytest.raises(ProviderTimeoutError) as exc_info:
            provider.invoke("test", timeout=10)

        assert exc_info.value.partial_result is not None
        assert exc_info.value.partial_result.timed_out is True
        assert "partial1" in exc_info.value.partial_result.stdout
        assert "partial2" in exc_info.value.partial_result.stdout

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_cleanup_called_on_timeout_path(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """_cleanup() called on timeout path via base class finally block (Task 5.5)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        process = create_mock_process(
            stdout_content="",
            wait_side_effect=TimeoutExpired(cmd="codex", timeout=10),
        )
        mock_popen.return_value = process

        provider = CodexProvider()
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

    def test_timeout_zero_raises_value_error(self) -> None:
        """timeout=0 raises ValueError (Task 5.6)."""
        provider = CodexProvider()
        with pytest.raises(ValueError, match="timeout must be positive"):
            provider.invoke("test", timeout=0)

    def test_timeout_negative_raises_value_error(self) -> None:
        """Negative timeout raises ValueError (Task 5.7)."""
        provider = CodexProvider()
        with pytest.raises(ValueError, match="timeout must be positive"):
            provider.invoke("test", timeout=-5)


# ============================================================================
# TestParseOutput -- JSON -> Evidence Score text formatting (Task 6)
# ============================================================================


class TestParseOutput:
    """Test parse_output() with structured JSON and plain text fallback."""

    def _make_review_json(
        self,
        findings: list[dict] | None = None,
        verdict: str = "PASS",
        summary: str = "All good",
    ) -> str:
        """Build a Codex review JSON string."""
        return json.dumps({
            "findings": findings or [],
            "overall_verdict": verdict,
            "summary": summary,
        })

    def test_structured_json_formatted_as_evidence_text(self) -> None:
        """Cached structured JSON with findings -> Evidence Score text (Task 6.1)."""
        provider = CodexProvider()
        provider._structured_output = self._make_review_json(
            findings=[{
                "title": "Bug found",
                "body": "Null pointer in main",
                "priority": "P0",
                "code_location": {"file_path": "src/main.py"},
            }],
            verdict="FAIL",
            summary="Critical issue found",
        )

        result = ProviderResult(
            stdout="raw text",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex", "exec"),
        )

        output = provider.parse_output(result)
        assert "Evidence Score Summary" in output
        assert "CRITICAL" in output
        assert "Bug found" in output
        assert "src/main.py" in output

    def test_p0_maps_to_critical(self) -> None:
        """P0 finding -> CRITICAL severity with +3.0 score (Task 6.2)."""
        provider = CodexProvider()
        provider._structured_output = self._make_review_json(
            findings=[{"title": "Critical", "body": "Bad", "priority": "P0"}],
            verdict="FAIL",
        )

        result = ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert "CRITICAL" in output
        assert "+3.0" in output

    def test_p1_maps_to_important(self) -> None:
        """P1 finding -> IMPORTANT severity with +1.0 score (Task 6.3)."""
        provider = CodexProvider()
        provider._structured_output = self._make_review_json(
            findings=[{"title": "Issue", "body": "Minor", "priority": "P1"}],
            verdict="NEEDS_WORK",
        )

        result = ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert "IMPORTANT" in output
        assert "+1.0" in output

    def test_p2_maps_to_minor(self) -> None:
        """P2 finding -> MINOR severity with +0.3 score (Task 6.4)."""
        provider = CodexProvider()
        provider._structured_output = self._make_review_json(
            findings=[{"title": "Nit", "body": "Style", "priority": "P2"}],
            verdict="PASS",
        )

        result = ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert "MINOR" in output
        assert "+0.3" in output

    def test_p3_maps_to_minor(self) -> None:
        """P3 finding -> MINOR severity with +0.3 score (Task 6.5)."""
        provider = CodexProvider()
        provider._structured_output = self._make_review_json(
            findings=[{"title": "Trivial", "body": "Whitespace", "priority": "P3"}],
            verdict="PASS",
        )

        result = ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert "MINOR" in output
        assert "+0.3" in output

    def test_clean_pass_formatting(self) -> None:
        """Empty findings + PASS verdict -> CLEAN PASS row (Task 6.6)."""
        provider = CodexProvider()
        provider._structured_output = self._make_review_json(
            findings=[],
            verdict="PASS",
            summary="No issues found",
        )

        result = ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert "CLEAN PASS" in output
        assert "PASS" in output

    def test_plain_text_fallback(self) -> None:
        """No cached structured output -> returns result.stdout.strip() (Task 6.7)."""
        provider = CodexProvider()
        provider._structured_output = None

        result = ProviderResult(
            stdout="  plain text response  ",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert output == "plain text response"

    def test_non_review_json_returns_raw(self) -> None:
        """Cached JSON without review keys -> returns raw JSON string (Task 6.8)."""
        provider = CodexProvider()
        raw_json = json.dumps({"arbitrary": "data", "no_findings": True})
        provider._structured_output = raw_json

        result = ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert output == raw_json

    def test_code_location_included_in_source(self) -> None:
        """Finding with code_location.file_path -> appears in Source column (Task 6.9)."""
        provider = CodexProvider()
        provider._structured_output = self._make_review_json(
            findings=[{
                "title": "Issue",
                "body": "Problem here",
                "priority": "P1",
                "code_location": {"file_path": "src/core/config.py"},
            }],
            verdict="NEEDS_WORK",
        )

        result = ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert "src/core/config.py" in output

    def test_verdict_and_summary_appended(self) -> None:
        """overall_verdict and summary appended at end of formatted text (Task 6.10)."""
        provider = CodexProvider()
        provider._structured_output = self._make_review_json(
            findings=[],
            verdict="PASS",
            summary="Everything looks great",
        )

        result = ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="codex-mini-latest",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert "**Overall Verdict:** PASS" in output
        assert "**Summary:** Everything looks great" in output


# ============================================================================
# TestSupportsModel -- Accepted and rejected model names (Task 7)
# ============================================================================


class TestSupportsModel:
    """Test supports_model() acceptance and rejection."""

    def test_accepts_codex_mini_latest(self) -> None:
        """supports_model('codex-mini-latest') returns True (Task 7.1)."""
        provider = CodexProvider()
        assert provider.supports_model("codex-mini-latest") is True

    def test_accepts_gpt_prefix(self) -> None:
        """supports_model('gpt-5.3-codex') returns True (Task 7.2)."""
        provider = CodexProvider()
        assert provider.supports_model("gpt-5.3-codex") is True

    def test_accepts_gpt_5_4_mini(self) -> None:
        """supports_model('gpt-5.4-mini') returns True (Task 7.3)."""
        provider = CodexProvider()
        assert provider.supports_model("gpt-5.4-mini") is True

    def test_accepts_gpt_5_5(self) -> None:
        """supports_model('gpt-5.5') returns True (Task 7.4)."""
        provider = CodexProvider()
        assert provider.supports_model("gpt-5.5") is True

    def test_accepts_codex_prefix(self) -> None:
        """supports_model('codex-anything') returns True (Task 7.5)."""
        provider = CodexProvider()
        assert provider.supports_model("codex-anything") is True

    def test_rejects_claude_model(self) -> None:
        """supports_model('claude-sonnet-4-5-20250929') returns False (Task 7.6)."""
        provider = CodexProvider()
        assert provider.supports_model("claude-sonnet-4-5-20250929") is False

    def test_rejects_gemini_model(self) -> None:
        """supports_model('gemini-2.5-flash') returns False (Task 7.7)."""
        provider = CodexProvider()
        assert provider.supports_model("gemini-2.5-flash") is False

    def test_rejects_arbitrary_string(self) -> None:
        """supports_model('random-model') returns False (Task 7.8)."""
        provider = CodexProvider()
        assert provider.supports_model("random-model") is False

    def test_rejects_empty_string(self) -> None:
        """supports_model('') returns False (Task 7.9)."""
        provider = CodexProvider()
        assert provider.supports_model("") is False


# ============================================================================
# TestErrors -- CLI not found, auth errors, empty response (Task 8)
# ============================================================================


class TestErrors:
    """Test error conditions: CLI not found, exit codes, empty response."""

    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    def test_cli_not_found(self, mock_resolve_cli: MagicMock) -> None:
        """resolve_cli_path raises ProviderError when codex not found (Task 8.1)."""
        mock_resolve_cli.side_effect = ProviderError("codex CLI not found")

        provider = CodexProvider()
        with pytest.raises(ProviderError, match="codex CLI not found"):
            provider.invoke("test", timeout=300)

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_file_not_found_error_wrapped(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Popen raises FileNotFoundError -> ProviderError (Task 8.2)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        mock_popen.side_effect = FileNotFoundError("No such file")

        provider = CodexProvider()
        with pytest.raises(ProviderError, match="Codex CLI binary not found"):
            provider.invoke("test", timeout=300)

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_auth_error_exit_code(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Non-zero exit with auth error in stderr -> ProviderExitCodeError (Task 8.3)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        process = create_mock_process(
            stdout_content="",
            stderr_content="Error: authentication failed - please run codex auth",
            returncode=1,
        )
        process.wait.return_value = 1
        mock_popen.return_value = process

        provider = CodexProvider()
        with pytest.raises(ProviderExitCodeError) as exc_info:
            provider.invoke("test", timeout=300)

        assert exc_info.value.exit_code == 1
        assert "authentication" in exc_info.value.stderr

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_rate_limit_exit_code(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Non-zero exit with rate limit in stderr -> ProviderExitCodeError (Task 8.4)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        process = create_mock_process(
            stdout_content="",
            stderr_content="Error: rate limit exceeded, please try again later",
            returncode=1,
        )
        process.wait.return_value = 1
        mock_popen.return_value = process

        provider = CodexProvider()
        with pytest.raises(ProviderExitCodeError) as exc_info:
            provider.invoke("test", timeout=300)

        assert exc_info.value.exit_code == 1
        assert "rate limit" in exc_info.value.stderr

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_network_failure_exit_code(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Non-zero exit with network error -> ProviderExitCodeError (Task 8.5)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        process = create_mock_process(
            stdout_content="",
            stderr_content="Error: network connection failed, unable to reach API",
            returncode=1,
        )
        process.wait.return_value = 1
        mock_popen.return_value = process

        provider = CodexProvider()
        with pytest.raises(ProviderExitCodeError) as exc_info:
            provider.invoke("test", timeout=300)

        assert exc_info.value.exit_code == 1
        assert "network" in exc_info.value.stderr

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_empty_response_returns_empty_string(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Zero exit code but empty stdout -> ProviderResult with empty stdout (Task 8.6)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        process = create_mock_process(stdout_content="", returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert result.stdout == ""
        assert result.exit_code == 0

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_stderr_truncated_in_error_message(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Long stderr (> 200 chars) is truncated in error message (Task 8.7)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        long_stderr = "x" * 500
        process = create_mock_process(
            stdout_content="",
            stderr_content=long_stderr,
            returncode=1,
        )
        process.wait.return_value = 1
        mock_popen.return_value = process

        provider = CodexProvider()
        with pytest.raises(ProviderExitCodeError) as exc_info:
            provider.invoke("test", timeout=300)

        # The full stderr is stored in the exception attribute (includes trailing newline)
        assert len(exc_info.value.stderr.strip()) == 500
        # The error message only contains the truncated version (200 chars)
        error_msg = str(exc_info.value)
        assert "x" * 200 in error_msg
        assert "x" * 201 not in error_msg


# ============================================================================
# TestProviderProperties -- Basic provider properties (Task 9)
# ============================================================================


class TestProviderProperties:
    """Test basic provider property implementations."""

    def test_provider_name(self) -> None:
        """provider_name returns 'codex' (Task 9.1)."""
        provider = CodexProvider()
        assert provider.provider_name == "codex"

    def test_default_model(self) -> None:
        """default_model returns 'codex-mini-latest' (Task 9.2)."""
        provider = CodexProvider()
        assert provider.default_model == "codex-mini-latest"

    def test_is_base_provider_subclass(self) -> None:
        """isinstance(provider, BaseProvider) is True (Task 9.3)."""
        provider = CodexProvider()
        assert isinstance(provider, BaseProvider)

    def test_initial_process_is_none(self) -> None:
        """New instance has _current_process=None (Task 9.4)."""
        provider = CodexProvider()
        assert provider._current_process is None

    def test_initial_threads_are_none(self) -> None:
        """New instance has _stdout_thread=None, _stderr_thread=None (Task 9.5)."""
        provider = CodexProvider()
        assert provider._stdout_thread is None
        assert provider._stderr_thread is None
