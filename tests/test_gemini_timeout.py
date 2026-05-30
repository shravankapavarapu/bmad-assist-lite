"""Tests for GeminiProvider graceful timeout migration (Story 7.5).

Covers all acceptance criteria:
- AC #1: Normal completion → timed_out=False, full response, _cleanup() called
- AC #2: Active streaming timeout → grace period granted by base class
- AC #3: Timeout with partial text → ProviderResult with timed_out=True
- AC #4: _cleanup() terminates subprocess via kill_process()
- AC #5: CLAUDE.md documentation accuracy (verified manually)
- AC #6: Provider implementor reference in CLAUDE.md (verified manually)

Test numbering per story:
- 7.1: Normal invocation
- 7.2: Collector feeding from JSON stream
- 7.3: Timeout propagation (TimeoutExpired → TimeoutError)
- 7.4: _cleanup() kills running process
- 7.5: _cleanup() skips dead process
- 7.6: _cleanup() handles None process
- 7.7: Retry logic preserved
- 7.8: timeout<=0 raises ValueError
- 7.9: invoke() is NOT overridden (base class version used)
- 7.10: Tool restriction prompt
- 7.11: FileNotFoundError wrapped in ProviderError
"""

import json
from subprocess import TimeoutExpired
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.core.exceptions import (
    ProviderError,
    ProviderExitCodeError,
    ProviderTimeoutError,
)
from bmad_assist_lite.providers.base import (
    BaseProvider,
    ProviderResult,
)
from bmad_assist_lite.providers.gemini import GeminiProvider
from bmad_assist_lite.providers.result_collector import ResultCollector


# ============================================================================
# Helpers — JSON stream simulation
# ============================================================================


def make_json_line(msg_type: str, **kwargs: object) -> str:
    """Create a JSON line mimicking Gemini CLI stream-json output."""
    data: dict[str, object] = {"type": msg_type, **kwargs}
    return json.dumps(data) + "\n"


def make_assistant_message(content: str) -> str:
    """Create an assistant message JSON line."""
    return make_json_line("message", role="assistant", content=content)


def make_init_message(session_id: str = "test-session") -> str:
    """Create an init message JSON line."""
    return make_json_line("init", session_id=session_id)


def make_result_message(total_tokens: int = 100, duration_ms: int = 500) -> str:
    """Create a result message JSON line."""
    return make_json_line(
        "result", stats={"total_tokens": total_tokens, "duration_ms": duration_ms}
    )


def build_full_stream(*messages: str) -> str:
    """Combine multiple JSON lines into a complete stream."""
    return "".join(messages)


def create_mock_process(
    stdout_content: str = "",
    stderr_content: str = "",
    returncode: int = 0,
    wait_side_effect: object = None,
) -> MagicMock:
    """Create a mock subprocess.Popen with configurable behavior."""
    process = MagicMock()
    process.stdin = MagicMock()
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
# TestNormalInvocation — Success path (AC #1, Test 7.1)
# ============================================================================


class TestNormalInvocation:
    """Test normal invocation path through the Template Method."""

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_normal_completion_returns_result(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Full response → ProviderResult with timed_out=False (AC #1)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"

        stream = build_full_stream(
            make_init_message("sess-1"),
            make_assistant_message("Hello World"),
            make_result_message(),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test prompt", timeout=300)

        assert result.timed_out is False
        assert "Hello World" in result.stdout
        assert result.model == "gemini-2.5-flash"
        assert result.exit_code == 0

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_cleanup_called_on_success(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """_cleanup() is called even on successful completion (AC #1)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(make_assistant_message("response"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        with patch.object(provider, "_cleanup") as mock_cleanup:
            provider.invoke("test prompt")
            mock_cleanup.assert_called_once()

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_duration_ms_is_non_negative(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Duration is measured and non-negative."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(make_assistant_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test")

        assert result.duration_ms >= 0

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_session_id_captured(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Session ID from init message is captured in ProviderResult."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(
            make_init_message("my-session-42"),
            make_assistant_message("hello"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test")

        assert result.provider_session_id == "my-session-42"

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_command_tuple_is_cli_command(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Command tuple on success path uses actual CLI command list (Task 4.1)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(make_assistant_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test", model="gemini-2.5-pro")

        # Success path uses tuple(command) — the full CLI command list
        assert "/usr/bin/gemini" in result.command
        assert "-m" in result.command
        assert "gemini-2.5-pro" in result.command

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_explicit_model_used(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Explicit model parameter is used."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(make_assistant_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test", model="gemini-2.5-pro")

        assert result.model == "gemini-2.5-pro"


# ============================================================================
# TestCollectorFeeding — Multi-message JSON stream (AC #1, #3, Test 7.2)
# ============================================================================


class TestCollectorFeeding:
    """Test that all assistant content chunks are fed to the collector."""

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_multiple_assistant_messages_captured(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Multiple assistant messages → all content captured (Test 7.2)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(
            make_assistant_message("chunk1"),
            make_assistant_message("chunk2"),
            make_assistant_message("chunk3"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test")

        assert "chunk1" in result.stdout
        assert "chunk2" in result.stdout
        assert "chunk3" in result.stdout

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_collector_receives_all_chunks(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Verify the collector accumulates all text chunks from JSON stream."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(
            make_assistant_message("A"),
            make_assistant_message("B"),
            make_assistant_message("C"),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()

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
            provider.invoke("test")

        assert len(captured_collector) == 1
        assert "A" in captured_collector[0].text
        assert "B" in captured_collector[0].text
        assert "C" in captured_collector[0].text

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_non_assistant_messages_not_in_text(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Non-assistant messages (tool_use, result) don't appear in response text."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(
            make_init_message(),
            make_assistant_message("real content"),
            make_json_line("tool_use", tool_name="read_file", parameters={}),
            make_result_message(),
        )
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test")

        assert result.stdout == "real content"


# ============================================================================
# TestTimeoutPropagation — TimeoutExpired → TimeoutError (AC #2, #3, Test 7.3)
# ============================================================================


class TestTimeoutPropagation:
    """Test that timeout propagates to base class for grace period handling."""

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_timeout_with_enough_text_returns_partial(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Timeout with >= 200 chars → partial result via base class (AC #3)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        large_text = "x" * 300
        stream = build_full_stream(make_assistant_message(large_text))
        process = create_mock_process(
            stdout_content=stream,
            wait_side_effect=TimeoutExpired(cmd="gemini", timeout=10),
        )
        mock_popen.return_value = process

        provider = GeminiProvider()
        # Base class catches TimeoutError → _handle_timeout()
        with patch.object(ResultCollector, "is_active", return_value=False):
            result = provider.invoke("test", timeout=10)

        assert result.timed_out is True
        assert len(result.stdout) >= 200

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_timeout_with_no_response_raises_error(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Timeout with no response → ProviderTimeoutError."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        process = create_mock_process(
            stdout_content="",
            wait_side_effect=TimeoutExpired(cmd="gemini", timeout=10),
        )
        mock_popen.return_value = process

        provider = GeminiProvider()
        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test", timeout=10)

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_timeout_expired_becomes_timeout_error(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """TimeoutExpired from process.wait() → TimeoutError for base class (Test 7.3)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        process = create_mock_process(
            stdout_content="",
            wait_side_effect=TimeoutExpired(cmd="gemini", timeout=10),
        )
        mock_popen.return_value = process

        provider = GeminiProvider()
        # Base class catches TimeoutError and handles via _handle_timeout
        # With no content, ProviderTimeoutError is raised
        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test", timeout=10)

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_timeout_collector_has_partial_content(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Collector has partial content from chunks delivered before timeout."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(
            make_assistant_message("partial1"),
            make_assistant_message("partial2"),
        )

        # Simulate timeout after some chunks are delivered
        # The stdout thread reads chunks, then process.wait times out
        process = create_mock_process(
            stdout_content=stream,
            wait_side_effect=TimeoutExpired(cmd="gemini", timeout=10),
        )
        mock_popen.return_value = process

        provider = GeminiProvider()
        # The partial text is < 200 chars so ProviderTimeoutError is raised
        # but we can verify the error has partial_result
        with pytest.raises(ProviderTimeoutError) as exc_info:
            provider.invoke("test", timeout=10)

        # The partial result exists and contains the streamed text
        assert exc_info.value.partial_result is not None
        assert exc_info.value.partial_result.timed_out is True
        assert "partial1" in exc_info.value.partial_result.stdout
        assert "partial2" in exc_info.value.partial_result.stdout

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_cleanup_called_on_timeout_path(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """_cleanup() is called on timeout path via base class finally (AC #4)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        process = create_mock_process(
            stdout_content="",
            wait_side_effect=TimeoutExpired(cmd="gemini", timeout=10),
        )
        mock_popen.return_value = process

        provider = GeminiProvider()
        cleanup_calls: list[bool] = []
        original_cleanup = provider._cleanup

        def tracking_cleanup() -> None:
            cleanup_calls.append(True)
            original_cleanup()

        with patch.object(provider, "_cleanup", side_effect=tracking_cleanup):
            with pytest.raises(ProviderTimeoutError):
                provider.invoke("test", timeout=10)

        assert len(cleanup_calls) == 1


# ============================================================================
# TestCleanup — Process termination (AC #4, Tests 7.4, 7.5, 7.6)
# ============================================================================


class TestCleanup:
    """Test _cleanup() process termination behavior."""

    def test_cleanup_kills_running_process(self) -> None:
        """_cleanup() calls kill_process() when process is still running (Test 7.4)."""
        provider = GeminiProvider()
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process still running

        provider._current_process = mock_process

        with patch(
            "bmad_assist_lite.providers.gemini.kill_process"
        ) as mock_kill:
            provider._cleanup()

        mock_kill.assert_called_once_with(mock_process)
        assert provider._current_process is None

    def test_cleanup_skips_dead_process(self) -> None:
        """_cleanup() skips kill if process already exited (Test 7.5)."""
        provider = GeminiProvider()
        mock_process = MagicMock()
        mock_process.poll.return_value = 0  # Process already exited

        provider._current_process = mock_process

        with patch(
            "bmad_assist_lite.providers.gemini.kill_process"
        ) as mock_kill:
            provider._cleanup()

        mock_kill.assert_not_called()
        assert provider._current_process is None

    def test_cleanup_handles_none_process(self) -> None:
        """_cleanup() with no process → no exception, no kill (Test 7.6)."""
        provider = GeminiProvider()
        provider._current_process = None

        with patch(
            "bmad_assist_lite.providers.gemini.kill_process"
        ) as mock_kill:
            provider._cleanup()  # Should not raise

        mock_kill.assert_not_called()

    def test_cleanup_joins_threads(self) -> None:
        """_cleanup() joins stdout/stderr reader threads (Task 2.4)."""
        provider = GeminiProvider()
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
        """_cleanup() resets _current_process and thread refs to None (Task 2.5)."""
        provider = GeminiProvider()
        provider._current_process = MagicMock()
        provider._current_process.poll.return_value = 0
        provider._stdout_thread = MagicMock()
        provider._stderr_thread = MagicMock()

        provider._cleanup()

        assert provider._current_process is None
        assert provider._stdout_thread is None
        assert provider._stderr_thread is None

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_cleanup_called_after_invoke(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """_cleanup() is called in finally block after invoke completes."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(make_assistant_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        cleanup_calls: list[bool] = []
        original_cleanup = provider._cleanup

        def tracking_cleanup() -> None:
            cleanup_calls.append(True)
            original_cleanup()

        with patch.object(provider, "_cleanup", side_effect=tracking_cleanup):
            provider.invoke("test")

        assert len(cleanup_calls) == 1

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_cleanup_exception_caught_by_base_class(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Base class wraps _cleanup() in try/except — exceptions don't mask results."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(make_assistant_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()

        with patch.object(
            provider, "_cleanup", side_effect=OSError("cleanup failed")
        ):
            # invoke() succeeds despite _cleanup() raising
            result = provider.invoke("test")

        assert result.timed_out is False
        assert "ok" in result.stdout


# ============================================================================
# TestRetryLogic — Transient error retry (Test 7.7)
# ============================================================================


class TestRetryLogic:
    """Test that retry logic for transient Gemini errors is preserved."""

    @patch("bmad_assist_lite.providers.gemini.time.sleep")
    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_retry_on_transient_error(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Transient error (exit_code!=0, empty stderr) → retry, then succeed (Test 7.7)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"

        # First attempt: fail with exit_code=1, empty stderr (transient)
        fail_process = create_mock_process(
            stdout_content="", stderr_content="", returncode=1
        )
        fail_process.wait.return_value = 1

        # Second attempt: succeed
        stream = build_full_stream(make_assistant_message("success after retry"))
        success_process = create_mock_process(stdout_content=stream, returncode=0)

        mock_popen.side_effect = [fail_process, success_process]

        provider = GeminiProvider()
        result = provider.invoke("test", timeout=300)

        assert result.timed_out is False
        assert "success after retry" in result.stdout
        assert mock_popen.call_count == 2

    @patch("bmad_assist_lite.providers.gemini.time.sleep")
    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_retry_collector_not_contaminated(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """Collector only contains data from successful attempt, not failed retries."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"

        # First attempt: streams "garbage_from_attempt_0" then fails
        fail_stream = build_full_stream(
            make_assistant_message("garbage_from_attempt_0")
        )
        fail_process = create_mock_process(
            stdout_content=fail_stream, stderr_content="", returncode=1
        )
        fail_process.wait.return_value = 1

        # Second attempt: streams clean "success_content"
        success_stream = build_full_stream(
            make_assistant_message("success_content")
        )
        success_process = create_mock_process(
            stdout_content=success_stream, returncode=0
        )

        mock_popen.side_effect = [fail_process, success_process]

        provider = GeminiProvider()
        result = provider.invoke("test", timeout=300)

        # Verify success content is present
        assert "success_content" in result.stdout
        # Verify garbage from failed attempt is NOT in the result
        assert "garbage_from_attempt_0" not in result.stdout

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_no_retry_on_non_transient_error(
        self,
        mock_popen: MagicMock,
        mock_resolve_cli: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Non-transient error (exit_code!=0, has stderr) → no retry, raises."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"

        process = create_mock_process(
            stdout_content="", stderr_content="real error output", returncode=1
        )
        process.wait.return_value = 1
        mock_popen.return_value = process

        provider = GeminiProvider()
        with pytest.raises(ProviderExitCodeError):
            provider.invoke("test", timeout=300)

        # Only one attempt (no retry)
        assert mock_popen.call_count == 1


# ============================================================================
# TestEdgeCases — timeout<=0, invoke override, tool restriction (Tests 7.8-7.11)
# ============================================================================


class TestEdgeCases:
    """Test edge cases and validation."""

    def test_timeout_zero_raises_value_error(self) -> None:
        """timeout=0 raises ValueError (Test 7.8)."""
        provider = GeminiProvider()
        with pytest.raises(ValueError, match="timeout must be positive"):
            provider.invoke("test", timeout=0)

    def test_timeout_negative_raises_value_error(self) -> None:
        """Negative timeout raises ValueError (Test 7.8)."""
        provider = GeminiProvider()
        with pytest.raises(ValueError, match="timeout must be positive"):
            provider.invoke("test", timeout=-5)

    def test_invoke_is_base_class_method(self) -> None:
        """GeminiProvider.invoke is BaseProvider.invoke (not overridden) (Test 7.9)."""
        assert GeminiProvider.invoke is BaseProvider.invoke

    def test_invoke_not_in_class_dict(self) -> None:
        """invoke() is not defined in GeminiProvider's own __dict__ (Test 7.9)."""
        assert "invoke" not in GeminiProvider.__dict__

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_tool_restriction_prompt(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """allowed_tools parameter produces restriction warning (Test 7.10)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(make_assistant_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test prompt", allowed_tools=["Read", "Glob", "Grep"])

        # Verify the prompt was modified with restriction warning
        # Check stdin.write was called with the restriction text
        written_prompt = process.stdin.write.call_args[0][0]
        assert "TOOL ACCESS RESTRICTIONS" in written_prompt
        assert "FORBIDDEN" in written_prompt
        assert "Read" in written_prompt

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_file_not_found_wrapped_in_provider_error(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """FileNotFoundError from Popen → ProviderError (Test 7.11)."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        mock_popen.side_effect = FileNotFoundError("No such file")

        provider = GeminiProvider()
        with pytest.raises(ProviderError, match="Gemini CLI binary not found"):
            provider.invoke("test")

    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    def test_gemini_not_in_path_raises_error(self, mock_resolve_cli: MagicMock) -> None:
        """resolve_cli_path raises ProviderError when gemini not found."""
        mock_resolve_cli.side_effect = ProviderError("gemini CLI not found")

        provider = GeminiProvider()
        with pytest.raises(ProviderError, match="gemini CLI not found"):
            provider.invoke("test")


# ============================================================================
# TestProviderProperties — Basic provider properties
# ============================================================================


class TestProviderProperties:
    """Test basic provider property implementations."""

    def test_provider_name(self) -> None:
        """provider_name returns 'gemini'."""
        provider = GeminiProvider()
        assert provider.provider_name == "gemini"

    def test_default_model(self) -> None:
        """default_model returns 'gemini-2.5-flash'."""
        provider = GeminiProvider()
        assert provider.default_model == "gemini-2.5-flash"

    def test_supports_model_always_true(self) -> None:
        """supports_model() returns True for any model (validated by Gemini CLI)."""
        provider = GeminiProvider()
        assert provider.supports_model("gemini-2.5-flash") is True
        assert provider.supports_model("anything") is True

    def test_parse_output_strips(self) -> None:
        """parse_output() strips whitespace from stdout."""
        provider = GeminiProvider()
        result = ProviderResult(
            stdout="  hello world  ",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="gemini-2.5-flash",
            command=("gemini", "gemini-2.5-flash"),
        )
        assert provider.parse_output(result) == "hello world"

    def test_is_base_provider_subclass(self) -> None:
        """GeminiProvider is a BaseProvider subclass."""
        provider = GeminiProvider()
        assert isinstance(provider, BaseProvider)

    def test_initial_process_is_none(self) -> None:
        """New provider instance has _current_process=None."""
        provider = GeminiProvider()
        assert provider._current_process is None

    def test_initial_threads_are_none(self) -> None:
        """New provider instance has thread refs=None."""
        provider = GeminiProvider()
        assert provider._stdout_thread is None
        assert provider._stderr_thread is None


# ============================================================================
# TestEmptyAndMalformedJSON — Edge cases for JSON stream parsing
# ============================================================================


class TestEmptyAndMalformedJSON:
    """Test behavior with empty or malformed JSON streams."""

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_empty_json_stream_returns_empty_result(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Empty JSON stream → empty stdout in result."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        process = create_mock_process(stdout_content="", returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test")

        assert result.stdout == ""
        assert result.timed_out is False

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_malformed_json_lines_skipped(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """Malformed JSON lines are skipped without error."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = "not valid json\n" + make_assistant_message("valid content")
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test")

        assert "valid content" in result.stdout

    @patch("bmad_assist_lite.providers.gemini.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.providers.gemini.resolve_cli_path")
    @patch("bmad_assist_lite.providers.gemini.Popen")
    def test_model_none_resolves_to_default(
        self, mock_popen: MagicMock, mock_resolve_cli: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        """model=None resolves to default 'gemini-2.5-flash'."""
        mock_resolve_cli.return_value = "/usr/bin/gemini"
        stream = build_full_stream(make_assistant_message("ok"))
        process = create_mock_process(stdout_content=stream, returncode=0)
        mock_popen.return_value = process

        provider = GeminiProvider()
        result = provider.invoke("test", model=None)

        assert result.model == "gemini-2.5-flash"
