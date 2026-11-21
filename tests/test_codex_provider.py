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
from bmad_assist_lite.providers.codex import CodexProvider, _extract_agent_message_text
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


def make_item_completed_agent_message_flat(text: str) -> str:
    """Create an item.completed line in the current CLI's flat shape.

    codex-cli 0.166.0 (observed) emits the message as a top-level ``text``
    field with no ``content`` array::

        {"type": "item.completed", "item": {"id": "item_0",
         "type": "agent_message", "text": "..."}}
    """
    return make_ndjson_line({
        "type": "item.completed",
        "item": {"id": "item_0", "type": "agent_message", "text": text},
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
    """Create a mock subprocess.Popen with configurable behavior."""
    process = MagicMock()
    process.pid = 12345

    # stdin mock (Codex now uses stdin=PIPE to avoid Windows command line length limits)
    process.stdin = MagicMock()
    process.stdin.write = MagicMock()
    process.stdin.close = MagicMock()

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
        provider.invoke("test prompt", model="gpt-5.3-codex", timeout=300)

        call_args = mock_popen.call_args
        command = call_args[0][0]
        assert command[0] == "/usr/bin/codex"
        assert "exec" in command
        assert "--json" in command
        assert "--model" in command
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "gpt-5.3-codex"
        assert "test prompt" not in command
        process.stdin.write.assert_called_once_with("test prompt")

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_effort_forwarded_as_reasoning_effort_override(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """effort=high becomes -c model_reasoning_effort="high" on the command."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test prompt", effort="high", timeout=300)

        command = mock_popen.call_args[0][0]
        assert "-c" in command
        c_idx = command.index("-c")
        assert command[c_idx + 1] == 'model_reasoning_effort="high"'

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_effort_max_maps_to_xhigh(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Codex has no 'max' level; it caps at xhigh."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test prompt", effort="max", timeout=300)

        command = mock_popen.call_args[0][0]
        c_idx = command.index("-c")
        assert command[c_idx + 1] == 'model_reasoning_effort="xhigh"'

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_no_effort_means_no_reasoning_override(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Unset effort adds no -c flag: the CLI default applies."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test prompt", timeout=300)

        command = mock_popen.call_args[0][0]
        assert "-c" not in command

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_unrecognised_effort_falls_back_to_cli_default(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """An unknown effort value is dropped (warning), not passed through."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test prompt", effort="turbo", timeout=300)

        command = mock_popen.call_args[0][0]
        assert "-c" not in command

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_sandbox_read_only_when_tools_restricted(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Read-only phases (allowed_tools set) get --sandbox read-only."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke(
            "test prompt",
            allowed_tools=["Read", "Glob", "Grep"],
            timeout=300,
        )

        command = mock_popen.call_args[0][0]
        assert "--sandbox" in command
        sandbox_idx = command.index("--sandbox")
        assert command[sandbox_idx + 1] == "read-only"

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_sandbox_workspace_write_when_unrestricted(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Write phases (allowed_tools None) get --sandbox workspace-write."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test prompt", timeout=300)

        command = mock_popen.call_args[0][0]
        assert "--sandbox" in command
        sandbox_idx = command.index("--sandbox")
        assert command[sandbox_idx + 1] == "workspace-write"

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
        """When model=None, uses 'gpt-5.3-codex' default (Task 2.3)."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        call_args = mock_popen.call_args
        command = call_args[0][0]
        model_idx = command.index("--model")
        assert command[model_idx + 1] == "gpt-5.3-codex"
        assert result.model == "gpt-5.3-codex"

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_stdin_pipe_prompt(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Verify prompt is written to stdin=PIPE (avoids Windows cmd length limits)."""
        import subprocess

        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test prompt", timeout=300)

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["stdin"] == subprocess.PIPE
        process.stdin.write.assert_called_once_with("test prompt")
        process.stdin.close.assert_called_once()

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
        """Read-only call with schema file present includes --output-schema (Task 2.5)."""
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
            provider.invoke(
                "test",
                timeout=300,
                cwd=Path("/tmp/test-project"),
                allowed_tools=["Read", "Glob", "Grep"],
            )

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
            provider.invoke(
                "test",
                timeout=300,
                cwd=Path("/tmp/test-project"),
                allowed_tools=["Read", "Glob", "Grep"],
            )

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
        """allowed_tools produces the sandbox-aware READ-ONLY REVIEW text (Task 2.10).

        The wording must authorize read-only shell inspection: Codex reads
        files via shell commands, so a Claude-vocabulary "FORBIDDEN: Bash"
        warning made reviewers decline to open any file (webdozo 2026-11-06).
        """
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        provider.invoke("test prompt", allowed_tools=["Read", "Glob", "Grep"], timeout=300)

        # Prompt with restriction text is written to stdin, not command args
        written_prompt = process.stdin.write.call_args[0][0]
        assert "READ-ONLY REVIEW" in written_prompt
        assert "MAY run read-only shell commands" in written_prompt
        assert "MUST NOT modify" in written_prompt
        assert "FORBIDDEN" not in written_prompt

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


class TestFlatAgentMessageShape:
    """The current CLI's flat agent_message shape streams text (codex-cli 0.166.0).

    Regression guard for the webdozo run of 2026-11-06: every agent message on
    a current CLI extracted as "" because only the legacy ``content``-blocks
    shape was handled, so write-mode calls returned empty responses.
    """

    def test_flat_text_extracted(self) -> None:
        """Top-level ``text`` is the message."""
        item = {"id": "item_0", "type": "agent_message", "text": "hello world"}
        assert _extract_agent_message_text(item) == "hello world"

    def test_flat_text_wins_over_content_blocks(self) -> None:
        """When both shapes are present, the top-level field is authoritative."""
        item = {
            "type": "agent_message",
            "text": "flat",
            "content": [{"type": "output_text", "text": "blocks"}],
        }
        assert _extract_agent_message_text(item) == "flat"

    def test_empty_flat_text_falls_back_to_content_blocks(self) -> None:
        """An empty top-level field defers to the legacy block walk."""
        item = {
            "type": "agent_message",
            "text": "",
            "content": [{"type": "output_text", "text": "blocks"}],
        }
        assert _extract_agent_message_text(item) == "blocks"

    def test_non_string_flat_text_falls_back(self) -> None:
        """A non-string ``text`` value is ignored, not str()-ified."""
        item = {
            "type": "agent_message",
            "text": ["not", "a", "string"],
            "content": [{"type": "output_text", "text": "blocks"}],
        }
        assert _extract_agent_message_text(item) == "blocks"

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_flat_shape_reaches_result_stdout(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """End to end: a 0.166.0-shaped stream lands in result.stdout."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(
            make_ndjson_line({"type": "thread.started", "thread_id": "t1"}),
            make_ndjson_line({"type": "turn.started"}),
            make_item_completed_agent_message_flat("hello world"),
            make_ndjson_line(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 14953,
                        "cached_input_tokens": 12160,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 6,
                        "reasoning_output_tokens": 0,
                    },
                }
            ),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = CodexProvider()
        result = provider.invoke("test", timeout=300)

        assert result.stdout == "hello world"
        assert result.input_tokens == 14953 - 12160
        assert result.cache_read_tokens == 12160
        assert result.cache_creation_tokens == 0
        assert result.output_tokens == 6


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
            model="gpt-5.3-codex",
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
            model="gpt-5.3-codex",
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
            model="gpt-5.3-codex",
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
            model="gpt-5.3-codex",
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
            model="gpt-5.3-codex",
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
            model="gpt-5.3-codex",
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
            model="gpt-5.3-codex",
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
            model="gpt-5.3-codex",
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
            model="gpt-5.3-codex",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert "src/core/config.py" in output

    def test_verdict_and_score_appended(self) -> None:
        """Evidence score and verdict appended at end of formatted text."""
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
            model="gpt-5.3-codex",
            command=("codex",),
        )

        output = provider.parse_output(result)
        assert "### Evidence Score:" in output
        # A clean pass over all nine categories scores -4.5, which the shared
        # thresholds call EXCELLENT (<= -3.0).
        assert "**EXCELLENT**" in output
        # Summary field is intentionally excluded to prevent double-counting
        assert "**Summary:**" not in output


# ============================================================================
# Shared helpers for the scoring / findings-block / telemetry tests
# ============================================================================


def _review_json(findings: list[dict] | None = None, verdict: str = "PASS") -> str:
    """Build a Codex review JSON string matching codex-review-schema.json."""
    return json.dumps({
        "findings": findings or [],
        "overall_verdict": verdict,
        "summary": "summary text",
    })


def _schema_finding(
    title: str,
    priority: str,
    *,
    body: str = "explanation",
    file_path: str | None = "src/app/module.py",
    line_range: list[int] | None = None,
) -> dict:
    """Build one finding in the shape codex-review-schema.json requires."""
    finding: dict = {
        "title": title,
        "body": body,
        "priority": priority,
        "confidence_score": 0.9,
    }
    if file_path is not None:
        finding["code_location"] = {
            "file_path": file_path,
            "line_range": line_range if line_range is not None else [10, 12],
        }
    return finding


def _parse_with(provider: CodexProvider, structured: str) -> str:
    """Run parse_output() over cached structured output."""
    provider._structured_output = structured
    return provider.parse_output(
        ProviderResult(
            stdout="",
            stderr="",
            exit_code=0,
            duration_ms=1,
            model="gpt-5.3-codex",
            command=("codex",),
        )
    )


# ============================================================================
# TestCleanPassScale -- nine-category scale, unclamped total, shared verdict
# ============================================================================


class TestCleanPassScale:
    """The Codex evidence table scores on the same scale as Claude lanes."""

    def test_clean_pass_counts_nine_categories(self) -> None:
        """A clean review credits nine categories and totals -4.5."""
        from bmad_assist_lite.validation.evidence_score import parse_evidence_findings

        output = _parse_with(CodexProvider(), _review_json())

        assert "| 9 categories |" in output
        assert "### Evidence Score: -4.5" in output
        report = parse_evidence_findings(output, "codex")
        assert report is not None
        assert report.clean_passes == 9
        assert report.total_score == -4.5
        assert report.parse_warnings == ()

    def test_partial_credit_is_nine_minus_findings(self) -> None:
        """Two findings leave seven clean categories."""
        from bmad_assist_lite.validation.evidence_score import parse_evidence_findings

        output = _parse_with(
            CodexProvider(),
            _review_json(
                [_schema_finding("a", "P2"), _schema_finding("b", "P3")],
                verdict="NEEDS_WORK",
            ),
        )

        assert "| 7 categories |" in output
        report = parse_evidence_findings(output, "codex")
        assert report is not None
        assert report.clean_passes == 7
        # 2 * 0.3 + 7 * -0.5 = -2.9: negative, and printed as such (no clamp)
        assert "### Evidence Score: -2.9" in output
        assert report.total_score == -2.9
        assert report.parse_warnings == ()

    def test_nine_or_more_findings_get_no_clean_credit(self) -> None:
        """Nine findings exhaust the categories, so there is no CLEAN PASS row."""
        findings = [_schema_finding(f"f{i}", "P3") for i in range(9)]
        output = _parse_with(CodexProvider(), _review_json(findings, verdict="NEEDS_WORK"))
        assert "CLEAN PASS" not in output

    @pytest.mark.parametrize(
        ("priorities", "expected"),
        [
            ([], "EXCELLENT"),  # -4.5
            (["P1"], "EXCELLENT"),  # 1.0 - 4.0 = -3.0, boundary
            (["P1", "P1"], "PASS"),  # 2.0 - 3.5 = -1.5
            (["P0", "P0", "P1", "P1"], "MAJOR_REWORK"),  # 8.0 - 2.5 = 5.5
            (["P0", "P0", "P0", "P0"], "REJECT"),  # 12.0 - 2.5 = 9.5
        ],
    )
    def test_verdict_row_uses_shared_thresholds(
        self, priorities: list[str], expected: str
    ) -> None:
        """The table's verdict matches determine_verdict() on the parsed score."""
        from bmad_assist_lite.validation.evidence_score import (
            determine_verdict,
            parse_evidence_findings,
        )

        findings = [_schema_finding(f"issue {i}", p) for i, p in enumerate(priorities)]
        verdict = "PASS" if not findings else "NEEDS_WORK"
        output = _parse_with(CodexProvider(), _review_json(findings, verdict=verdict))

        assert f"**{expected}**" in output
        report = parse_evidence_findings(output, "codex")
        assert report is not None
        assert report.parse_warnings == ()
        assert determine_verdict(report.total_score).value == expected


# ============================================================================
# TestFindingsBlock -- structured findings block for the review merge
# ============================================================================


class TestFindingsBlock:
    """parse_output() appends a block that validation.findings can parse."""

    def test_round_trip_through_both_parsers(self) -> None:
        """The same text parses cleanly as findings and as an evidence score."""
        from bmad_assist_lite.validation.evidence_score import parse_evidence_findings
        from bmad_assist_lite.validation.findings import (
            Bucket,
            Severity,
            parse_findings,
        )

        findings = [
            _schema_finding("Null deref in loader", "P0", body="crashes", line_range=[42, 44]),
            _schema_finding("Missing negative test", "P1", file_path="tests/test_x.py"),
            _schema_finding("Naming nit", "P2"),
            _schema_finding("Trailing whitespace", "P3"),
        ]
        output = _parse_with(CodexProvider(), _review_json(findings, verdict="NEEDS_WORK"))

        parsed = parse_findings(output)
        assert [f.severity for f in parsed.findings] == [
            Severity.HIGH,
            Severity.MEDIUM,
            Severity.LOW,
            Severity.LOW,
        ]
        first = parsed.findings[0]
        assert first.file == "src/app/module.py"
        assert first.line == 42
        assert first.title == "Null deref in loader"
        assert first.anchor == "Null deref in loader"
        assert first.detail == "crashes"
        assert all(f.bucket is Bucket.PATCH for f in parsed.findings)
        assert parsed.findings[1].file == "tests/test_x.py"

        report = parse_evidence_findings(output, "codex")
        assert report is not None
        assert len(report.findings) == 4
        assert report.clean_passes == 5
        assert report.parse_warnings == ()

    def test_block_follows_evidence_table(self) -> None:
        """The machine block comes after the human-readable table."""
        from bmad_assist_lite.validation.findings import (
            FINDINGS_CLOSE_MARKER,
            FINDINGS_OPEN_MARKER,
        )

        output = _parse_with(CodexProvider(), _review_json([_schema_finding("x", "P1")]))
        assert output.index("### Evidence Score:") < output.index(FINDINGS_OPEN_MARKER)
        assert output.rstrip().endswith(FINDINGS_CLOSE_MARKER)
        assert "```json" in output

    def test_clean_review_emits_empty_list(self) -> None:
        """No findings is an explicit empty list, which reads as a clean review."""
        from bmad_assist_lite.validation.findings import parse_findings

        output = _parse_with(CodexProvider(), _review_json())
        assert parse_findings(output).is_clean

    def test_missing_code_location_gives_empty_file_and_no_line(self) -> None:
        """A finding without code_location still parses (file is required, may be empty)."""
        from bmad_assist_lite.validation.findings import parse_findings

        output = _parse_with(
            CodexProvider(),
            _review_json([_schema_finding("General concern", "P1", file_path=None)]),
        )
        (finding,) = parse_findings(output).findings
        assert finding.file == ""
        assert finding.line is None

    def test_unknown_priority_maps_to_low(self) -> None:
        """An off-schema priority degrades to low, like MINOR in the table."""
        from bmad_assist_lite.validation.findings import Severity, parse_findings

        output = _parse_with(CodexProvider(), _review_json([_schema_finding("odd", "P9")]))
        (finding,) = parse_findings(output).findings
        assert finding.severity is Severity.LOW

    def test_non_review_json_has_no_block(self) -> None:
        """Raw non-review JSON is returned unchanged, with no block added."""
        from bmad_assist_lite.validation.findings import FINDINGS_OPEN_MARKER

        raw = json.dumps({"something": "else"})
        output = _parse_with(CodexProvider(), raw)
        assert output == raw
        assert FINDINGS_OPEN_MARKER not in output

    def test_review_merge_accepts_codex_lane(self) -> None:
        """A Codex lane no longer forces the legacy prose-merge fallback."""
        from bmad_assist_lite.loop.review_merge import parse_reviewer_findings

        output = _parse_with(
            CodexProvider(),
            _review_json([_schema_finding("Race in writer", "P0")], verdict="NEEDS_WORK"),
        )
        findings, notes = parse_reviewer_findings(
            [{"reviewer": "Codex", "exit_code": 0, "response": output}]
        )
        assert notes == []
        assert [f.title for f in findings] == ["Race in writer"]


# ============================================================================
# TestOutputSchemaScope -- schema only on read-only (reviewer/validator) calls
# ============================================================================


def _schema_present() -> MagicMock:
    """Return a mock schema Path that exists."""
    schema_mock = MagicMock(spec=Path)
    schema_mock.is_file.return_value = True
    schema_mock.__str__ = lambda self: "/path/to/schema.json"
    return schema_mock


class TestOutputSchemaScope:
    """--output-schema / --output-last-message only when allowed_tools is set."""

    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_write_mode_runs_without_schema(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """allowed_tools=None (master/write phase) never forces the review schema."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("free form"))
        mock_popen.return_value = create_mock_process(stdout_content=stream)

        with patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _schema_present()):
            provider = CodexProvider()
            result = provider.invoke("test", timeout=300, cwd=tmp_path)

        command = mock_popen.call_args[0][0]
        assert "--output-schema" not in command
        assert "--output-last-message" not in command
        assert provider.parse_output(result) == "free form"

    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_read_only_mode_uses_schema(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A list of allowed_tools (reviewer/validator lane) adds both flags."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
        mock_popen.return_value = create_mock_process(stdout_content=stream)

        with patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _schema_present()):
            CodexProvider().invoke("test", timeout=300, cwd=tmp_path, allowed_tools=["Read"])

        command = mock_popen.call_args[0][0]
        assert "--output-schema" in command
        assert "--output-last-message" in command

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_missing_schema_warns_only_on_read_only_path(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The missing-schema warning fires for reviewers, not for write phases."""
        mock_resolve_cli.return_value = "/usr/bin/codex"

        def fresh_process(*_args: object, **_kwargs: object) -> MagicMock:
            stream = build_ndjson_stream(make_item_completed_agent_message("ok"))
            return create_mock_process(stdout_content=stream)

        mock_popen.side_effect = fresh_process

        with caplog.at_level("WARNING", logger="bmad_assist_lite.providers.codex"):
            CodexProvider().invoke("test", timeout=300)
        assert "Review schema not found" not in caplog.text

        with caplog.at_level("WARNING", logger="bmad_assist_lite.providers.codex"):
            CodexProvider().invoke("test", timeout=300, allowed_tools=["Read"])
        assert "Review schema not found" in caplog.text

    @patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA)
    @patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.codex.resolve_cli_path")
    @patch("bmad_assist_lite.providers.codex.Popen")
    def test_stale_structured_output_cleared_on_next_call(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """A reused instance never returns an earlier call's review JSON."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        stream = build_ndjson_stream(make_item_completed_agent_message("fresh text"))
        mock_popen.return_value = create_mock_process(stdout_content=stream)

        provider = CodexProvider()
        provider._structured_output = _review_json()
        result = provider.invoke("test", timeout=300)

        assert provider.parse_output(result) == "fresh text"


# ============================================================================
# TestUsageTelemetry -- token usage from NDJSON events
# ============================================================================


def _turn_completed(usage: object) -> str:
    """Build a turn.completed NDJSON line carrying *usage*."""
    return make_ndjson_line({"type": "turn.completed", "usage": usage})


class TestUsageTelemetry:
    """Usage events populate ProviderResult token fields, never raising."""

    def _invoke(self, stream: str) -> ProviderResult:
        """Run one invocation over *stream* with the CLI mocked out."""
        with (
            patch("bmad_assist_lite.providers.codex._REVIEW_SCHEMA_PATH", _NO_SCHEMA),
            patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={}),
            patch(
                "bmad_assist_lite.providers.codex.resolve_cli_path",
                return_value="/usr/bin/codex",
            ),
            patch("bmad_assist_lite.providers.codex.Popen") as mock_popen,
        ):
            mock_popen.return_value = create_mock_process(stdout_content=stream)
            return CodexProvider().invoke("test", timeout=300)

    def test_turn_completed_usage_populates_result(self) -> None:
        """cached_input_tokens -> cache_read_tokens; input_tokens is the uncached rest."""
        result = self._invoke(
            build_ndjson_stream(
                make_item_completed_agent_message("hello"),
                _turn_completed(
                    {"input_tokens": 24763, "cached_input_tokens": 24448, "output_tokens": 122}
                ),
            )
        )
        assert result.stdout == "hello"
        assert result.input_tokens == 24763 - 24448
        assert result.cache_read_tokens == 24448
        assert result.output_tokens == 122
        assert result.cache_creation_tokens is None

    def test_cache_write_tokens_recorded_as_cache_creation(self) -> None:
        """cache_write_input_tokens -> cache_creation_tokens, verbatim."""
        result = self._invoke(
            _turn_completed(
                {
                    "input_tokens": 1000,
                    "cached_input_tokens": 600,
                    "cache_write_input_tokens": 250,
                    "output_tokens": 50,
                }
            )
        )
        assert result.input_tokens == 400
        assert result.cache_read_tokens == 600
        assert result.cache_creation_tokens == 250
        assert result.output_tokens == 50

    def test_usage_without_cached_keeps_raw_input(self) -> None:
        """No cached count means input_tokens is reported as given."""
        result = self._invoke(_turn_completed({"input_tokens": 500, "output_tokens": 20}))
        assert result.input_tokens == 500
        assert result.cache_read_tokens is None
        assert result.output_tokens == 20

    def test_no_usage_event_leaves_metrics_none(self) -> None:
        """A stream without usage reports None, never zero."""
        result = self._invoke(make_item_completed_agent_message("hi"))
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.cache_read_tokens is None

    @pytest.mark.parametrize(
        "usage",
        ["not a dict", 42, None, ["input_tokens", 5]],
    )
    def test_non_dict_usage_degrades_to_none(self, usage: object) -> None:
        """A malformed usage payload records nothing and does not kill the reader."""
        result = self._invoke(
            build_ndjson_stream(
                _turn_completed(usage),
                make_item_completed_agent_message("still captured"),
            )
        )
        assert result.stdout == "still captured"
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_bad_field_values_degrade_per_field(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Booleans, strings and negatives drop only their own field, with a warning."""
        with caplog.at_level("WARNING", logger="bmad_assist_lite.providers.codex"):
            result = self._invoke(
                _turn_completed(
                    {"input_tokens": "100", "cached_input_tokens": True, "output_tokens": 7}
                )
            )
        assert result.input_tokens is None
        assert result.cache_read_tokens is None
        assert result.output_tokens == 7
        assert "input_tokens" in caplog.text

        negative = self._invoke(_turn_completed({"input_tokens": 10, "output_tokens": -1}))
        assert negative.input_tokens == 10
        assert negative.output_tokens is None

    def test_cached_exceeding_input_drops_uncached_count(self) -> None:
        """An inconsistent payload cannot produce a negative uncached count."""
        result = self._invoke(
            _turn_completed({"input_tokens": 10, "cached_input_tokens": 50, "output_tokens": 3})
        )
        assert result.input_tokens is None
        assert result.cache_read_tokens == 50
        assert result.output_tokens == 3

    def test_top_level_usage_on_other_event_accepted(self) -> None:
        """A usage object on a non-turn event is read too."""
        stream = make_ndjson_line(
            {"type": "token_count", "usage": {"input_tokens": 9, "output_tokens": 4}}
        )
        result = self._invoke(stream)
        assert result.input_tokens == 9
        assert result.output_tokens == 4

    def test_last_usage_envelope_wins(self) -> None:
        """Later usable envelopes replace earlier ones; malformed ones do not."""
        result = self._invoke(
            build_ndjson_stream(
                _turn_completed({"input_tokens": 1, "output_tokens": 1}),
                _turn_completed({"input_tokens": 2, "output_tokens": 2}),
                _turn_completed("garbage"),
            )
        )
        assert result.input_tokens == 2
        assert result.output_tokens == 2

    def test_non_object_json_lines_are_skipped(self) -> None:
        """Scalar or list JSON lines do not crash the NDJSON reader."""
        result = self._invoke(
            build_ndjson_stream(
                "42\n",
                "[1, 2]\n",
                make_ndjson_line({"type": "item.completed", "item": "not a dict"}),
                make_item_completed_agent_message("survived"),
            )
        )
        assert result.stdout == "survived"

    def test_metrics_reach_phase_recorder(self) -> None:
        """BaseProvider._record_call_metrics forwards the Codex token counts."""
        with patch(
            "bmad_assist_lite.core.phase_metrics.record_provider_call"
        ) as mock_record:
            self._invoke(
                _turn_completed(
                    {"input_tokens": 300, "cached_input_tokens": 100, "output_tokens": 50}
                )
            )
        kwargs = mock_record.call_args.kwargs
        assert kwargs["input_tokens"] == 200
        assert kwargs["cache_read_tokens"] == 100
        assert kwargs["output_tokens"] == 50


# ============================================================================
# TestSupportsModel -- Accepted and rejected model names (Task 7)
# ============================================================================


class TestSupportsModel:
    """Test supports_model() acceptance and rejection."""

    def test_accepts_codex_mini_latest(self) -> None:
        """supports_model('gpt-5.3-codex') returns True (Task 7.1)."""
        provider = CodexProvider()
        assert provider.supports_model("gpt-5.3-codex") is True

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

    def test_accepts_o_series_models(self) -> None:
        """supports_model accepts o1/o3/o4 series models."""
        provider = CodexProvider()
        assert provider.supports_model("gpt-5.3-codex") is True
        assert provider.supports_model("o3-mini") is True
        assert provider.supports_model("o1-mini") is True

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
        """Long stderr (> 200 chars) shows the tail in error message."""
        mock_resolve_cli.return_value = "/usr/bin/codex"
        # Mock adds trailing \n per line, so use 301 b's to guarantee
        # 200+ b's survive after [-200:].strip()
        long_stderr = "a" * 300 + "b" * 301
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
        assert len(exc_info.value.stderr.strip()) == 601
        # The error message shows the LAST 200 chars (tail) with ellipsis prefix
        error_msg = str(exc_info.value)
        assert "..." in error_msg
        assert "b" * 199 in error_msg
        assert "a" * 100 not in error_msg


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
        """default_model returns 'gpt-5.3-codex' (Task 9.2)."""
        provider = CodexProvider()
        assert provider.default_model == "gpt-5.3-codex"

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
