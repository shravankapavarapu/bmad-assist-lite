"""Tests for ClaudeSDKProvider graceful timeout migration (Story 7.4).

Covers all acceptance criteria:
- AC #1: Normal completion → timed_out=False, full response, _cleanup() called
- AC #2: Active streaming timeout → grace period granted by base class
- AC #3: Timeout with 500+ chars → ProviderResult with timed_out=True
- AC #4: _cleanup() terminates orphan process or logs warning
- AC #5: Timeout with no response → ProviderTimeoutError raised
- AC #6: ResultCollector fed from multiple AssistantMessage/TextBlock objects
"""

import asyncio
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from claude_agent_sdk import CLINotFoundError as SDKCLINotFoundError
from claude_agent_sdk import ProcessError as SDKProcessError

from bmad_assist_lite.core.exceptions import ProviderError, ProviderTimeoutError
from bmad_assist_lite.providers.base import BaseProvider, ProviderResult
from bmad_assist_lite.providers.claude_sdk import (
    SUPPORTED_MODELS,
    ClaudeSDKProvider,
    _is_benign_success_error,
)
from bmad_assist_lite.providers.result_collector import ResultCollector

# ============================================================================
# Helpers — Message factories using real SDK types
# ============================================================================


def make_msg(texts: list[str]) -> AssistantMessage:
    """Create an AssistantMessage with TextBlock content blocks."""
    return AssistantMessage(
        content=[TextBlock(text=t) for t in texts],
        model="sonnet",
    )


class FakeOtherMessage:
    """Fake non-AssistantMessage to verify filtering."""

    def __init__(self) -> None:
        """Initialize with empty content list."""
        self.content: list[object] = []


async def make_fake_query(
    messages: list[AssistantMessage | FakeOtherMessage],
):
    """Async generator yielding messages."""
    for msg in messages:
        yield msg


# ============================================================================
# TestNormalInvocation — Success path (AC #1, Task 6.2)
# ============================================================================


class TestNormalInvocation:
    """Test normal invocation path through the Template Method."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_normal_completion_returns_result(self, mock_query: MagicMock) -> None:
        """Full response → ProviderResult with timed_out=False (AC #1)."""
        messages = [make_msg(["Hello ", "World"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        result = provider.invoke("test prompt", timeout=300)

        assert result.timed_out is False
        assert result.stdout == "Hello World"
        assert result.model == "sonnet"
        assert result.exit_code == 0

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_cleanup_called_on_success(self, mock_query: MagicMock) -> None:
        """_cleanup() is called even on successful completion (AC #1)."""
        messages = [make_msg(["response"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        with patch.object(provider, "_cleanup") as mock_cleanup:
            provider.invoke("test prompt")
            mock_cleanup.assert_called_once()

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_command_tuple_format(self, mock_query: MagicMock) -> None:
        """Command tuple uses (provider_name, model or 'default') matching base class."""
        messages = [make_msg(["ok"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        result = provider.invoke("test", model="opus")

        assert result.command == ("claude", "opus")

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_duration_ms_is_non_negative(self, mock_query: MagicMock) -> None:
        """Duration is measured and non-negative."""
        messages = [make_msg(["ok"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        result = provider.invoke("test")

        assert result.duration_ms >= 0


# ============================================================================
# TestCollectorFeeding — Multi-message multi-block (AC #6, Task 6.5)
# ============================================================================


class TestCollectorFeeding:
    """Test that all TextBlock.text values from multiple AssistantMessages are captured."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_multiple_messages_multiple_blocks(self, mock_query: MagicMock) -> None:
        """Multiple AssistantMessages with multiple TextBlocks → all captured (AC #6)."""
        messages = [
            make_msg(["A1", "A2"]),
            make_msg(["B1", "B2"]),
            make_msg(["C1"]),
        ]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        result = provider.invoke("test")

        assert result.stdout == "A1A2B1B2C1"

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_non_assistant_messages_ignored(self, mock_query: MagicMock) -> None:
        """Non-AssistantMessage objects are skipped (AC #6)."""
        messages: list[AssistantMessage | FakeOtherMessage] = [
            FakeOtherMessage(),
            make_msg(["kept"]),
            FakeOtherMessage(),
        ]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        result = provider.invoke("test")

        assert result.stdout == "kept"

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_collector_receives_all_chunks(self, mock_query: MagicMock) -> None:
        """Verify the collector accumulates all text chunks (Task 6.11)."""
        messages = [make_msg(["chunk1", "chunk2"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        # Patch _do_invoke to capture the collector reference
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
        assert captured_collector[0].text == "chunk1chunk2"


# ============================================================================
# TestTimeoutPropagation — TimeoutError to base class (AC #2, #3, #5, Task 6.3)
# ============================================================================


class TestTimeoutPropagation:
    """Test that timeout propagates to base class for grace period handling."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_timeout_with_enough_text_returns_partial(self, mock_query: MagicMock) -> None:
        """Timeout with 500+ chars → partial result via base class (AC #3)."""
        large_text = "x" * 600

        async def stalling_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield make_msg([large_text])
            await asyncio.sleep(999999)

        mock_query.return_value = stalling_query()

        provider = ClaudeSDKProvider()
        # The base class catches TimeoutError → _handle_timeout()
        # With 600 chars and inactive stream → returns partial result
        with patch.object(ResultCollector, "is_active", return_value=False):
            result = provider.invoke("test", timeout=1)

        assert result.timed_out is True
        assert len(result.stdout) >= 500

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_timeout_with_no_response_raises_error(self, mock_query: MagicMock) -> None:
        """Timeout with no response → ProviderTimeoutError (AC #5)."""

        async def stalling_query(**kwargs: object):  # type: ignore[no-untyped-def]
            await asyncio.sleep(999999)
            yield  # pragma: no cover — never reached

        mock_query.return_value = stalling_query()

        provider = ClaudeSDKProvider()
        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test", timeout=1)

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_timeout_error_propagates_to_base_class(self, mock_query: MagicMock) -> None:
        """TimeoutError propagates from _do_invoke to base class invoke()."""

        async def stalling_query(**kwargs: object):  # type: ignore[no-untyped-def]
            await asyncio.sleep(999999)
            yield  # pragma: no cover

        mock_query.return_value = stalling_query()

        provider = ClaudeSDKProvider()
        # The base class handles timeout and raises ProviderTimeoutError for empty
        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test", timeout=1)


# ============================================================================
# TestCleanup — PID-based process cleanup (AC #4, Task 6.6, 6.7, 6.8)
# ============================================================================


class TestCleanup:
    """Test _cleanup() process termination behavior."""

    def test_cleanup_terminates_alive_process(self) -> None:
        """_cleanup() calls terminate_process() when PID is set and alive (AC #4)."""
        provider = ClaudeSDKProvider()
        provider._current_pid = 12345

        with (
            patch(
                "bmad_assist_lite.providers.claude_sdk.is_pid_alive", return_value=True
            ) as mock_alive,
            patch(
                "bmad_assist_lite.providers.claude_sdk.terminate_process", return_value=True
            ) as mock_term,
        ):
            provider._cleanup()

        mock_alive.assert_called_once_with(12345)
        mock_term.assert_called_once_with(12345)
        assert provider._current_pid is None

    def test_cleanup_skips_dead_process(self) -> None:
        """_cleanup() does not terminate already-dead process."""
        provider = ClaudeSDKProvider()
        provider._current_pid = 12345

        with (
            patch("bmad_assist_lite.providers.claude_sdk.is_pid_alive", return_value=False),
            patch("bmad_assist_lite.providers.claude_sdk.terminate_process") as mock_term,
        ):
            provider._cleanup()

        mock_term.assert_not_called()
        assert provider._current_pid is None

    def test_cleanup_logs_warning_when_no_pid(self, caplog: pytest.LogCaptureFixture) -> None:
        """_cleanup() logs DEBUG when PID is None (AC #4, Task 6.7)."""
        provider = ClaudeSDKProvider()
        provider._current_pid = None

        with caplog.at_level(logging.DEBUG):
            provider._cleanup()

        assert "No PID tracked" in caplog.text
        debug_records = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "No PID tracked" in r.message
        ]
        assert len(debug_records) == 1

    def test_cleanup_handles_terminate_failure(self) -> None:
        """_cleanup() does not raise when terminate_process() returns False (Task 6.8)."""
        provider = ClaudeSDKProvider()
        provider._current_pid = 12345

        with (
            patch("bmad_assist_lite.providers.claude_sdk.is_pid_alive", return_value=True),
            patch("bmad_assist_lite.providers.claude_sdk.terminate_process", return_value=False),
        ):
            # Should not raise
            provider._cleanup()

        assert provider._current_pid is None

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_cleanup_exception_caught_by_base_class(self, mock_query: MagicMock) -> None:
        """Base class wraps _cleanup() in try/except — exceptions don't mask results."""
        messages = [make_msg(["ok"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        provider._current_pid = 12345

        with (
            patch("bmad_assist_lite.providers.claude_sdk.is_pid_alive", return_value=True),
            patch(
                "bmad_assist_lite.providers.claude_sdk.terminate_process",
                side_effect=OSError("access denied"),
            ),
        ):
            # invoke() succeeds despite _cleanup() raising OSError,
            # because base class wraps _cleanup() in try/except
            result = provider.invoke("test")

        assert result.timed_out is False
        assert result.stdout == "ok"
        assert provider._current_pid is None

    def test_cleanup_resets_pid(self) -> None:
        """_cleanup() resets _current_pid to None after attempt."""
        provider = ClaudeSDKProvider()
        provider._current_pid = 99999

        with (
            patch("bmad_assist_lite.providers.claude_sdk.is_pid_alive", return_value=True),
            patch("bmad_assist_lite.providers.claude_sdk.terminate_process", return_value=True),
        ):
            provider._cleanup()

        assert provider._current_pid is None


# ============================================================================
# TestSDKErrorWrapping — CLINotFoundError and ProcessError (Task 6.9)
# ============================================================================


class TestSDKErrorWrapping:
    """Test that SDK-specific errors are wrapped in ProviderError."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_cli_not_found_wrapped(self, mock_query: MagicMock) -> None:
        """CLINotFoundError is wrapped in ProviderError."""

        async def failing_query(**kwargs: object):  # type: ignore[no-untyped-def]
            raise SDKCLINotFoundError("not found")
            yield  # pragma: no cover — make it an async generator

        mock_query.return_value = failing_query()

        provider = ClaudeSDKProvider()
        with pytest.raises(ProviderError, match="Claude Code not found"):
            provider.invoke("test")

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_process_error_wrapped(self, mock_query: MagicMock) -> None:
        """ProcessError is wrapped in ProviderError with exit code details."""
        error = SDKProcessError("failed", exit_code=1, stderr="some error output")

        async def failing_query(**kwargs: object):  # type: ignore[no-untyped-def]
            raise error
            yield  # pragma: no cover

        mock_query.return_value = failing_query()

        provider = ClaudeSDKProvider()
        with pytest.raises(ProviderError, match="exit code 1"):
            provider.invoke("test")

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_unexpected_error_wrapped(self, mock_query: MagicMock) -> None:
        """Unexpected exceptions are wrapped in ProviderError."""

        async def failing_query(**kwargs: object):  # type: ignore[no-untyped-def]
            raise RuntimeError("kaboom")
            yield  # pragma: no cover

        mock_query.return_value = failing_query()

        provider = ClaudeSDKProvider()
        with pytest.raises(ProviderError, match="Unexpected SDK error"):
            provider.invoke("test")


# ============================================================================
# TestBenignSuccessEscalation — SDK 0.2.x "error result: success" quirk
# ============================================================================


async def query_yield_then_raise(
    messages: list[AssistantMessage | FakeOtherMessage],
    exc: Exception,
):
    """Async generator that yields messages then raises — mirrors the 0.2.x SDK.

    The 0.2.x read task converts a benign non-zero CLI exit into a stream
    {"type": "error"} message, which receive_messages re-raises as a bare
    Exception AFTER the turn's AssistantMessages have already been streamed.
    """
    for msg in messages:
        yield msg
    raise exc


class TestBenignSuccessPredicate:
    """Unit tests for the _is_benign_success_error signal matcher."""

    def test_exact_success_matches(self) -> None:
        """The literal benign escalation string matches."""
        exc = Exception("Claude Code returned an error result: success")
        assert _is_benign_success_error(exc) is True

    def test_trailing_whitespace_tolerated(self) -> None:
        """Surrounding whitespace does not break the match."""
        exc = Exception("Claude Code returned an error result: success  ")
        assert _is_benign_success_error(exc) is True

    def test_real_error_subtype_does_not_match(self) -> None:
        """A genuine error subtype must not be swallowed."""
        exc = Exception("Claude Code returned an error result: error_max_turns")
        assert _is_benign_success_error(exc) is False

    def test_joined_error_messages_do_not_match(self) -> None:
        """Joined error messages (errors array) must not be swallowed."""
        exc = Exception("Claude Code returned an error result: tool failed; rate limited")
        assert _is_benign_success_error(exc) is False

    def test_unrelated_exception_does_not_match(self) -> None:
        """An unrelated exception is not the benign escalation."""
        assert _is_benign_success_error(RuntimeError("kaboom")) is False

    def test_prefix_must_lead_the_message(self) -> None:
        """The marker must start the message, not appear mid-string."""
        exc = Exception("wrapped: Claude Code returned an error result: success")
        assert _is_benign_success_error(exc) is False


class TestBenignSuccessEscalation:
    """Behavioral tests for swallowing the benign escalation in _do_invoke()."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_benign_success_with_text_returns_success(
        self, mock_query: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Benign 'error result: success' after real output → success result."""
        mock_query.return_value = query_yield_then_raise(
            [make_msg(["real ", "output"])],
            Exception("Claude Code returned an error result: success"),
        )

        provider = ClaudeSDKProvider()
        with caplog.at_level(logging.WARNING):
            result = provider.invoke("test")

        assert result.timed_out is False
        assert result.exit_code == 0
        assert result.stdout == "real output"
        assert "known CLI/SDK 0.2.x quirk" in caplog.text

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_benign_success_without_text_raises(self, mock_query: MagicMock) -> None:
        """Benign escalation with no collected output → does not silently succeed."""
        mock_query.return_value = query_yield_then_raise(
            [],
            Exception("Claude Code returned an error result: success"),
        )

        provider = ClaudeSDKProvider()
        with pytest.raises(ProviderError, match="Unexpected SDK error"):
            provider.invoke("test")

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_genuine_error_result_with_text_still_raises(self, mock_query: MagicMock) -> None:
        """A real error subtype is never swallowed, even with partial output."""
        mock_query.return_value = query_yield_then_raise(
            [make_msg(["partial work"])],
            Exception("Claude Code returned an error result: error_max_turns"),
        )

        provider = ClaudeSDKProvider()
        with pytest.raises(ProviderError, match="Unexpected SDK error"):
            provider.invoke("test")


# ============================================================================
# TestInvokeNotOverridden — Template Method pattern (Task 6.10)
# ============================================================================


class TestInvokeNotOverridden:
    """Test that ClaudeSDKProvider does not override invoke()."""

    def test_invoke_is_base_class_method(self) -> None:
        """ClaudeSDKProvider.invoke is BaseProvider.invoke (not overridden)."""
        assert ClaudeSDKProvider.invoke is BaseProvider.invoke

    def test_invoke_not_in_class_dict(self) -> None:
        """invoke() is not defined in ClaudeSDKProvider's own __dict__."""
        assert "invoke" not in ClaudeSDKProvider.__dict__


# ============================================================================
# TestModelResolution — Model parameter handling
# ============================================================================


class TestModelResolution:
    """Test model resolution and validation in _do_invoke()."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_default_model_is_sonnet(self, mock_query: MagicMock) -> None:
        """model=None resolves to 'sonnet'."""
        messages = [make_msg(["ok"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        result = provider.invoke("test", model=None)

        assert result.model == "sonnet"

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_explicit_model_used(self, mock_query: MagicMock) -> None:
        """Explicit model parameter is used."""
        messages = [make_msg(["ok"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        result = provider.invoke("test", model="opus")

        assert result.model == "opus"

    def test_timeout_zero_raises_value_error(self) -> None:
        """timeout=0 raises ValueError (restored behavioral consistency)."""
        provider = ClaudeSDKProvider()
        with pytest.raises(ValueError, match="timeout must be positive"):
            provider.invoke("test", timeout=0)

    def test_timeout_negative_raises_value_error(self) -> None:
        """Negative timeout raises ValueError."""
        provider = ClaudeSDKProvider()
        with pytest.raises(ValueError, match="timeout must be positive"):
            provider.invoke("test", timeout=-5)

    def test_unsupported_model_raises_error(self) -> None:
        """Unsupported model raises ProviderError."""
        provider = ClaudeSDKProvider()
        with pytest.raises(ProviderError, match="Unsupported model"):
            provider.invoke("test", model="gpt-4")

    def test_claude_prefix_model_supported(self) -> None:
        """Models starting with 'claude-' are supported."""
        provider = ClaudeSDKProvider()
        assert provider.supports_model("claude-3-opus-20240229") is True

    def test_supported_models_set(self) -> None:
        """SUPPORTED_MODELS contains expected values."""
        assert frozenset({"opus", "sonnet", "haiku"}) == SUPPORTED_MODELS


# ============================================================================
# TestPIDTracking — PID initialization and state (Task 5)
# ============================================================================


class TestPIDTracking:
    """Test PID tracking initialization and state management."""

    def test_initial_pid_is_none(self) -> None:
        """New provider instance has _current_pid=None."""
        provider = ClaudeSDKProvider()
        assert provider._current_pid is None

    def test_pid_reset_after_cleanup(self) -> None:
        """_cleanup() always resets _current_pid to None."""
        provider = ClaudeSDKProvider()
        provider._current_pid = 42

        with patch("bmad_assist_lite.providers.claude_sdk.is_pid_alive", return_value=False):
            provider._cleanup()

        assert provider._current_pid is None

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_cleanup_called_after_invoke(self, mock_query: MagicMock) -> None:
        """_cleanup() is called in finally block after invoke completes."""
        messages = [make_msg(["ok"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        cleanup_calls: list[bool] = []
        original_cleanup = provider._cleanup

        def tracking_cleanup() -> None:
            cleanup_calls.append(True)
            original_cleanup()

        with patch.object(provider, "_cleanup", side_effect=tracking_cleanup):
            provider.invoke("test")

        assert len(cleanup_calls) == 1


# ============================================================================
# TestSettingsValidation — Settings file handling
# ============================================================================


class TestSettingsValidation:
    """Test settings file validation in _do_invoke()."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_settings_file_validated(self, mock_query: MagicMock, tmp_path: Path) -> None:
        """Settings file is validated before invocation."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}")

        messages = [make_msg(["ok"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        result = provider.invoke("test", settings_file=settings_file)

        assert result.timed_out is False

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_no_settings_file_ok(self, mock_query: MagicMock) -> None:
        """No settings file → None passed to SDK."""
        messages = [make_msg(["ok"])]
        mock_query.return_value = make_fake_query(messages)

        provider = ClaudeSDKProvider()
        result = provider.invoke("test")

        assert result.timed_out is False


# ============================================================================
# TestProviderProperties — Basic provider properties
# ============================================================================


class TestProviderProperties:
    """Test basic provider property implementations."""

    def test_provider_name(self) -> None:
        """provider_name returns 'claude'."""
        provider = ClaudeSDKProvider()
        assert provider.provider_name == "claude"

    def test_default_model(self) -> None:
        """default_model returns 'sonnet'."""
        provider = ClaudeSDKProvider()
        assert provider.default_model == "sonnet"

    def test_parse_output_strips(self) -> None:
        """parse_output() strips whitespace from stdout."""
        provider = ClaudeSDKProvider()
        result = ProviderResult(
            stdout="  hello world  ",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="sonnet",
            command=("claude", "sonnet"),
        )
        assert provider.parse_output(result) == "hello world"

    def test_is_base_provider_subclass(self) -> None:
        """ClaudeSDKProvider is a BaseProvider subclass."""
        provider = ClaudeSDKProvider()
        assert isinstance(provider, BaseProvider)


# ============================================================================
# TestEmptyResponse — No SDK response (edge case)
# ============================================================================


class TestEmptyResponse:
    """Test behavior when SDK returns no text content."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_no_text_blocks_raises_error(self, mock_query: MagicMock) -> None:
        """No TextBlocks in response → ProviderError."""

        async def empty_query(**kwargs: object):  # type: ignore[no-untyped-def]
            yield FakeOtherMessage()

        mock_query.return_value = empty_query()

        provider = ClaudeSDKProvider()
        with pytest.raises(ProviderError, match="No response received"):
            provider.invoke("test")

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_empty_stream_raises_error(self, mock_query: MagicMock) -> None:
        """Empty async generator → ProviderError."""

        async def empty_query(**kwargs: object):  # type: ignore[no-untyped-def]
            return
            yield  # pragma: no cover — make it async generator

        mock_query.return_value = empty_query()

        provider = ClaudeSDKProvider()
        with pytest.raises(ProviderError, match="No response received"):
            provider.invoke("test")
