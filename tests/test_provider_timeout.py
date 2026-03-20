"""Tests for BaseProvider graceful timeout contract (Story 7.3).

Covers all acceptance criteria:
- AC #1: Normal completion → timed_out=False, _cleanup() called
- AC #2: Active-streaming timeout → grace period granted
- AC #3: Silent-stall timeout → no grace period, immediate failure
- AC #4: Timeout with >= 200 chars → ProviderResult with timed_out=True
- AC #5: Timeout with < 200 chars → ProviderTimeoutError raised
- AC #6: _cleanup() always called on success, timeout, error
- AC #7: Future providers inherit behavior via _do_invoke()/_cleanup()
"""

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from bmad_assist_lite.core.exceptions import ProviderTimeoutError
from bmad_assist_lite.providers.base import (
    ACTIVE_STREAM_THRESHOLD,
    DEFAULT_TIMEOUT,
    GRACE_PERIOD_RATIO,
    MIN_GRACE_PERIOD_SECONDS,
    MIN_USEFUL_RESPONSE_CHARS,
    BaseProvider,
    ProviderResult,
)
from bmad_assist_lite.providers.result_collector import ResultCollector


# ============================================================================
# FakeProvider — Configurable test double (Task 6.1)
# ============================================================================


class FakeProvider(BaseProvider):
    """Concrete test double implementing BaseProvider contract.

    Configurable to simulate: normal completion, timeout with active streaming,
    timeout while silent, timeout with partial results.
    """

    def __init__(
        self,
        *,
        simulate_timeout: bool = False,
        chunks_before_timeout: list[str] | None = None,
        simulate_error: Exception | None = None,
        result_text: str = "full response",
    ) -> None:
        """Initialize FakeProvider with behavior configuration."""
        self._simulate_timeout = simulate_timeout
        self._chunks_before_timeout = chunks_before_timeout or []
        self._simulate_error = simulate_error
        self._result_text = result_text
        self._cleanup_called = False
        self._cleanup_call_count = 0
        self._do_invoke_called = False
        self._received_collector: ResultCollector | None = None

    @property
    def provider_name(self) -> str:
        """Return test provider name."""
        return "fake"

    def _do_invoke(
        self,
        prompt: str,
        *,
        collector: ResultCollector,
        model: str | None = None,
        timeout: int = 300,
        settings_file: Path | None = None,
        cwd: Path | None = None,
        allowed_tools: list[str] | None = None,
        color_index: int | None = None,
    ) -> ProviderResult:
        """Simulate provider invocation with configurable behavior."""
        self._do_invoke_called = True
        self._received_collector = collector

        if self._simulate_error is not None:
            raise self._simulate_error

        # Add any chunks before a potential timeout
        for chunk in self._chunks_before_timeout:
            collector.add(chunk)

        if self._simulate_timeout:
            raise TimeoutError("Simulated timeout")

        # Normal completion
        collector.add(self._result_text)
        return ProviderResult(
            stdout=collector.text,
            stderr="",
            exit_code=0,
            duration_ms=100,
            model=model,
            command=("fake", model or "default"),
        )

    def _cleanup(self) -> None:
        """Track cleanup calls."""
        self._cleanup_called = True
        self._cleanup_call_count += 1

    def parse_output(self, result: ProviderResult) -> str:
        """Return stdout as-is."""
        return result.stdout

    def supports_model(self, model: str) -> bool:
        """Support all models."""
        return True


# ============================================================================
# TestProviderResultTimedOut — timed_out field (Task 6.10)
# ============================================================================


class TestProviderResultTimedOut:
    """Test ProviderResult.timed_out field behavior."""

    def test_default_timed_out_is_false(self) -> None:
        """ProviderResult.timed_out defaults to False (Task 6.10)."""
        result = ProviderResult(
            stdout="test",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model=None,
            command=("test",),
        )
        assert result.timed_out is False

    def test_timed_out_can_be_set_true(self) -> None:
        """ProviderResult can be constructed with timed_out=True (Task 6.10)."""
        result = ProviderResult(
            stdout="partial",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model=None,
            command=("test",),
            timed_out=True,
        )
        assert result.timed_out is True

    def test_frozen_dataclass_semantics(self) -> None:
        """ProviderResult is frozen — cannot modify timed_out after construction."""
        result = ProviderResult(
            stdout="test",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model=None,
            command=("test",),
        )
        with pytest.raises(AttributeError):
            result.timed_out = True  # type: ignore[misc]

    def test_backward_compatible_without_timed_out(self) -> None:
        """Existing callers that don't pass timed_out still work (Task 1.2)."""
        result = ProviderResult(
            stdout="test",
            stderr="",
            exit_code=0,
            duration_ms=100,
            model="gpt-4",
            command=("test",),
            provider_session_id="sess-123",
        )
        assert result.timed_out is False
        assert result.provider_session_id == "sess-123"


# ============================================================================
# TestNormalInvocation — Success path (Task 6.2, AC #1)
# ============================================================================


class TestNormalInvocation:
    """Test normal invocation path through BaseProvider."""

    def test_normal_completion_returns_result(self) -> None:
        """_do_invoke() completes → result has timed_out=False (AC #1)."""
        provider = FakeProvider()
        result = provider.invoke("test prompt")
        assert result.timed_out is False
        assert "full response" in result.stdout

    def test_cleanup_called_on_success(self) -> None:
        """_cleanup() is called even on successful completion (AC #6)."""
        provider = FakeProvider()
        provider.invoke("test prompt")
        assert provider._cleanup_called is True

    def test_do_invoke_receives_collector(self) -> None:
        """_do_invoke() receives the ResultCollector created by invoke() (Task 6.11)."""
        provider = FakeProvider()
        provider.invoke("test prompt")
        assert provider._do_invoke_called is True
        assert provider._received_collector is not None
        assert isinstance(provider._received_collector, ResultCollector)

    def test_invoke_with_model_parameter(self) -> None:
        """invoke() passes model parameter through to _do_invoke()."""
        provider = FakeProvider()
        result = provider.invoke("test prompt", model="test-model")
        assert result.model == "test-model"


# ============================================================================
# TestTimeoutWithActiveStreaming — Grace period (Task 6.3, AC #2)
# ============================================================================


class TestTimeoutWithActiveStreaming:
    """Test timeout behavior when stream is still active."""

    def test_grace_period_granted_when_active(self) -> None:
        """Active stream at timeout → grace period granted (AC #2)."""
        # Create provider that times out after adding chunks
        large_text = "x" * 300  # >= MIN_USEFUL_RESPONSE_CHARS
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[large_text],
        )

        # Mock _wait_for_grace to verify it's called and avoid actual waiting
        with patch.object(provider, "_wait_for_grace") as mock_grace:
            # Make the collector appear active at the time _handle_timeout checks
            with patch.object(
                ResultCollector,
                "is_active",
                return_value=True,
            ):
                result = provider.invoke("test", timeout=600)

        mock_grace.assert_called_once()
        assert result.timed_out is True

    def test_grace_period_calculation_600(self) -> None:
        """600s timeout → max(60, 600*0.25) = 150s grace (Task 6.9)."""
        grace = max(MIN_GRACE_PERIOD_SECONDS, int(600 * GRACE_PERIOD_RATIO))
        assert grace == 150

    def test_grace_period_calculation_1200(self) -> None:
        """1200s timeout → max(60, 1200*0.25) = 300s grace (Task 6.9)."""
        grace = max(MIN_GRACE_PERIOD_SECONDS, int(1200 * GRACE_PERIOD_RATIO))
        assert grace == 300

    def test_grace_period_calculation_200(self) -> None:
        """200s timeout → max(60, 200*0.25) = 60s grace (floor) (Task 6.9)."""
        grace = max(MIN_GRACE_PERIOD_SECONDS, int(200 * GRACE_PERIOD_RATIO))
        assert grace == 60

    def test_grace_period_calculation_100(self) -> None:
        """100s timeout → max(60, 100*0.25) = 60s grace (floor) (Task 6.9)."""
        grace = max(MIN_GRACE_PERIOD_SECONDS, int(100 * GRACE_PERIOD_RATIO))
        assert grace == 60


# ============================================================================
# TestTimeoutWhileSilent — No grace period (Task 6.4, AC #3)
# ============================================================================


class TestTimeoutWhileSilent:
    """Test timeout behavior when stream is silent/stalled."""

    def test_no_grace_when_silent(self) -> None:
        """Silent stream at timeout → no grace period (AC #3)."""
        large_text = "x" * 300
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[large_text],
        )

        with patch.object(provider, "_wait_for_grace") as mock_grace:
            # Make the collector appear NOT active (stale)
            with patch.object(
                ResultCollector,
                "is_active",
                return_value=False,
            ):
                result = provider.invoke("test", timeout=600)

        # _wait_for_grace should NOT be called when stream is silent
        mock_grace.assert_not_called()
        assert result.timed_out is True

    def test_no_grace_when_no_chunks(self) -> None:
        """No chunks added → no grace period, raises ProviderTimeoutError (AC #3, #5)."""
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[],
        )

        with patch.object(provider, "_wait_for_grace") as mock_grace:
            with pytest.raises(ProviderTimeoutError):
                provider.invoke("test", timeout=600)

        mock_grace.assert_not_called()


# ============================================================================
# TestPartialResult — Partial text capture (Task 6.5, 6.6, 6.7, AC #4, #5)
# ============================================================================


class TestPartialResult:
    """Test partial result capture on timeout."""

    def test_partial_result_returned_when_enough_text(self) -> None:
        """Timeout with >= 200 chars → ProviderResult with timed_out=True (AC #4)."""
        large_text = "a" * 250
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[large_text],
        )

        # Collector not active (silent stall) → skip grace, go to partial check
        with patch.object(ResultCollector, "is_active", return_value=False):
            result = provider.invoke("test", timeout=300)

        assert result.timed_out is True
        assert result.stdout == large_text

    def test_partial_result_too_small_raises_error(self) -> None:
        """Timeout with < 200 chars → ProviderTimeoutError (AC #5)."""
        small_text = "a" * 50  # < MIN_USEFUL_RESPONSE_CHARS
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[small_text],
        )

        with patch.object(ResultCollector, "is_active", return_value=False):
            with pytest.raises(ProviderTimeoutError) as exc_info:
                provider.invoke("test", timeout=300)

        # The exception should have a partial result attached
        assert exc_info.value.partial_result is not None
        assert exc_info.value.partial_result.timed_out is True

    def test_empty_collector_raises_timeout_error(self) -> None:
        """Empty collector on timeout → ProviderTimeoutError (Task 6.7)."""
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[],
        )

        with pytest.raises(ProviderTimeoutError) as exc_info:
            provider.invoke("test", timeout=300)

        # May or may not have partial_result depending on implementation
        assert exc_info.value.partial_result is None or (
            exc_info.value.partial_result is not None
            and exc_info.value.partial_result.timed_out is True
        )

    def test_boundary_exactly_200_chars(self) -> None:
        """Exactly 200 chars → should return partial result (boundary test)."""
        boundary_text = "b" * 200
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[boundary_text],
        )

        with patch.object(ResultCollector, "is_active", return_value=False):
            result = provider.invoke("test", timeout=300)

        assert result.timed_out is True
        assert result.stdout == boundary_text

    def test_boundary_199_chars_raises_error(self) -> None:
        """199 chars → should raise ProviderTimeoutError (boundary test)."""
        small_text = "c" * 199
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[small_text],
        )

        with patch.object(ResultCollector, "is_active", return_value=False):
            with pytest.raises(ProviderTimeoutError):
                provider.invoke("test", timeout=300)


# ============================================================================
# TestCleanupGuarantee — _cleanup() always called (Task 6.8, AC #6)
# ============================================================================


class TestCleanupGuarantee:
    """Test _cleanup() is always called regardless of outcome."""

    def test_cleanup_on_success(self) -> None:
        """_cleanup() called on normal completion (AC #6)."""
        provider = FakeProvider()
        provider.invoke("test")
        assert provider._cleanup_called is True

    def test_cleanup_on_timeout(self) -> None:
        """_cleanup() called when timeout occurs (AC #6)."""
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=["x" * 300],
        )

        with patch.object(ResultCollector, "is_active", return_value=False):
            provider.invoke("test", timeout=300)

        assert provider._cleanup_called is True

    def test_cleanup_on_timeout_error(self) -> None:
        """_cleanup() called when ProviderTimeoutError is raised (AC #6)."""
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[],
        )

        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test", timeout=300)

        assert provider._cleanup_called is True

    def test_cleanup_on_unexpected_error(self) -> None:
        """_cleanup() called on unexpected exceptions (AC #6)."""
        provider = FakeProvider(
            simulate_error=RuntimeError("unexpected"),
        )

        with pytest.raises(RuntimeError, match="unexpected"):
            provider.invoke("test")

        assert provider._cleanup_called is True

    def test_cleanup_called_exactly_once(self) -> None:
        """_cleanup() is called exactly once per invoke() call."""
        provider = FakeProvider()
        provider.invoke("test")
        assert provider._cleanup_call_count == 1


# ============================================================================
# TestTimeoutNoneResolution — timeout=None path (Task 6.12)
# ============================================================================


class TestTimeoutNoneResolution:
    """Test that timeout=None is resolved to default before grace math."""

    def test_timeout_none_resolves_to_default(self) -> None:
        """invoke() with timeout=None does not cause TypeError in grace math (Task 6.12)."""
        large_text = "x" * 300
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[large_text],
        )

        # Should not raise TypeError from None * 0.25
        with patch.object(ResultCollector, "is_active", return_value=True):
            with patch.object(provider, "_wait_for_grace"):
                result = provider.invoke("test")  # timeout=None → default

        assert result.timed_out is True

    def test_timeout_none_cleanup_still_called(self) -> None:
        """_cleanup() is called when timeout=None and timeout occurs."""
        provider = FakeProvider(
            simulate_timeout=True,
            chunks_before_timeout=[],
        )

        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test")  # timeout=None

        assert provider._cleanup_called is True


# ============================================================================
# TestWaitForGrace — Grace period polling (Task 5)
# ============================================================================


class TestWaitForGrace:
    """Test _wait_for_grace() polling behavior."""

    def test_wait_for_grace_returns_none(self) -> None:
        """_wait_for_grace() always returns None (Task 5.5)."""
        provider = FakeProvider()
        collector = ResultCollector()

        # Mock time.sleep to avoid actual waiting
        with patch("bmad_assist_lite.providers.base.time.sleep"):
            with patch.object(collector, "is_active", return_value=False):
                result = provider._wait_for_grace(collector, 60)

        assert result is None

    def test_wait_for_grace_exits_early_on_stall(self) -> None:
        """Grace period exits early when collector stops being active (Task 5.3)."""
        provider = FakeProvider()
        collector = ResultCollector()

        with patch("bmad_assist_lite.providers.base.time.sleep"):
            # First call active, then stalled
            with patch.object(
                collector, "is_active", side_effect=[True, False]
            ):
                provider._wait_for_grace(collector, 120)
                # Should have exited after seeing stall

    def test_wait_for_grace_respects_duration(self) -> None:
        """Grace period does not exceed grace_seconds (Task 5.1)."""
        provider = FakeProvider()
        collector = ResultCollector()

        with patch("bmad_assist_lite.providers.base.time.sleep"):
            with patch("bmad_assist_lite.providers.base.time.monotonic", side_effect=[
                0.0,   # start time
                2.0,   # first check
                4.0,   # second check
                100.0, # exceeds grace_seconds=60
            ]):
                with patch.object(collector, "is_active", return_value=True):
                    provider._wait_for_grace(collector, 60)


# ============================================================================
# TestConstants — Module-level constants (Task 2)
# ============================================================================


class TestConstants:
    """Test timeout-related module-level constants."""

    def test_min_grace_period_seconds(self) -> None:
        """MIN_GRACE_PERIOD_SECONDS is 60."""
        assert MIN_GRACE_PERIOD_SECONDS == 60

    def test_grace_period_ratio(self) -> None:
        """GRACE_PERIOD_RATIO is 0.25."""
        assert GRACE_PERIOD_RATIO == 0.25

    def test_active_stream_threshold(self) -> None:
        """ACTIVE_STREAM_THRESHOLD is 30.0."""
        assert ACTIVE_STREAM_THRESHOLD == 30.0

    def test_min_useful_response_chars(self) -> None:
        """MIN_USEFUL_RESPONSE_CHARS is 200."""
        assert MIN_USEFUL_RESPONSE_CHARS == 200

    def test_default_timeout(self) -> None:
        """DEFAULT_TIMEOUT is 300."""
        assert DEFAULT_TIMEOUT == 300


# ============================================================================
# TestHandleTimeout — _handle_timeout() logic (Task 4)
# ============================================================================


class TestHandleTimeout:
    """Test _handle_timeout() decision logic."""

    def test_active_stream_triggers_grace(self) -> None:
        """Active collector → _wait_for_grace() called (AC #2)."""
        provider = FakeProvider()
        collector = ResultCollector()
        collector.add("x" * 300)
        start_time = time.monotonic()

        with patch.object(collector, "is_active", return_value=True):
            with patch.object(provider, "_wait_for_grace") as mock_grace:
                result = provider._handle_timeout(
                    collector, 600, "test-model", ("fake", "test-model"), start_time
                )

        mock_grace.assert_called_once()

    def test_silent_stream_skips_grace(self) -> None:
        """Inactive collector → _wait_for_grace() NOT called (AC #3)."""
        provider = FakeProvider()
        collector = ResultCollector()
        collector.add("x" * 300)
        start_time = time.monotonic()

        with patch.object(collector, "is_active", return_value=False):
            with patch.object(provider, "_wait_for_grace") as mock_grace:
                result = provider._handle_timeout(
                    collector, 600, "test-model", ("fake", "test-model"), start_time
                )

        mock_grace.assert_not_called()
        assert result.timed_out is True

    def test_enough_text_returns_result(self) -> None:
        """>= 200 chars → returns ProviderResult with timed_out=True (AC #4)."""
        provider = FakeProvider()
        collector = ResultCollector()
        collector.add("y" * 250)
        start_time = time.monotonic()

        with patch.object(collector, "is_active", return_value=False):
            result = provider._handle_timeout(
                collector, 300, None, ("fake", "default"), start_time
            )

        assert result.timed_out is True
        assert result.stdout == "y" * 250
        assert result.exit_code == 0
        assert result.duration_ms >= 0

    def test_too_little_text_raises_error(self) -> None:
        """< 200 chars → raises ProviderTimeoutError (AC #5)."""
        provider = FakeProvider()
        collector = ResultCollector()
        collector.add("z" * 50)
        start_time = time.monotonic()

        with patch.object(collector, "is_active", return_value=False):
            with pytest.raises(ProviderTimeoutError) as exc_info:
                provider._handle_timeout(
                    collector, 300, None, ("fake", "default"), start_time
                )

        assert exc_info.value.partial_result is not None
        assert exc_info.value.partial_result.timed_out is True

    def test_empty_collector_raises_error(self) -> None:
        """Empty collector → raises ProviderTimeoutError with no useful partial."""
        provider = FakeProvider()
        collector = ResultCollector()
        start_time = time.monotonic()

        with pytest.raises(ProviderTimeoutError):
            provider._handle_timeout(
                collector, 300, None, ("fake", "default"), start_time
            )

    def test_grace_period_values_passed_correctly(self) -> None:
        """Verify grace_seconds = max(60, int(timeout * 0.25)) passed to _wait_for_grace."""
        provider = FakeProvider()
        collector = ResultCollector()
        collector.add("x" * 300)
        start_time = time.monotonic()

        with patch.object(collector, "is_active", return_value=True):
            with patch.object(provider, "_wait_for_grace") as mock_grace:
                provider._handle_timeout(
                    collector, 600, None, ("fake", "default"), start_time
                )

        # Grace should be max(60, int(600 * 0.25)) = 150
        call_args = mock_grace.call_args
        assert call_args[0][1] == 150  # second positional arg is grace_seconds

    def test_partial_result_exit_code_zero(self) -> None:
        """Successful partial result has exit_code=0 for handler compatibility."""
        provider = FakeProvider()
        collector = ResultCollector()
        collector.add("x" * 300)
        start_time = time.monotonic()

        with patch.object(collector, "is_active", return_value=False):
            result = provider._handle_timeout(
                collector, 300, None, ("fake", "default"), start_time
            )

        assert result.exit_code == 0
        assert result.timed_out is True

    def test_duration_ms_recorded_on_timeout(self) -> None:
        """Duration is recorded in timeout ProviderResult, not hardcoded to 0."""
        provider = FakeProvider()
        collector = ResultCollector()
        collector.add("x" * 300)
        start_time = time.monotonic() - 5.0  # simulate 5s elapsed

        with patch.object(collector, "is_active", return_value=False):
            result = provider._handle_timeout(
                collector, 300, None, ("fake", "default"), start_time
            )

        assert result.duration_ms >= 4000  # at least ~5s worth


# ============================================================================
# TestInheritance — Template method pattern (AC #7)
# ============================================================================


class TestInheritance:
    """Test that subclasses automatically get timeout behavior."""

    def test_subclass_only_needs_do_invoke_and_cleanup(self) -> None:
        """A subclass implementing _do_invoke, _cleanup, parse_output, supports_model
        automatically gets grace period, partial capture, activity detection (AC #7)."""
        provider = FakeProvider()
        # Verify it can be used as a BaseProvider
        assert isinstance(provider, BaseProvider)
        # Verify invoke() is concrete (not abstract)
        result = provider.invoke("test")
        assert result is not None

    def test_abstract_methods_enforced(self) -> None:
        """Cannot instantiate BaseProvider without implementing abstract methods."""

        class IncompleteProvider(BaseProvider):  # type: ignore[abstract]
            @property
            def provider_name(self) -> str:
                return "incomplete"

            def parse_output(self, result: ProviderResult) -> str:
                return result.stdout

            def supports_model(self, model: str) -> bool:
                return True

            # Missing _do_invoke and _cleanup

        with pytest.raises(TypeError):
            IncompleteProvider()  # type: ignore[abstract]


# ============================================================================
# TestCleanupExceptionHandling — _cleanup() error resilience
# ============================================================================


class TestCleanupExceptionHandling:
    """Test that _cleanup() exceptions don't mask original results/errors."""

    def test_cleanup_exception_does_not_mask_success(self) -> None:
        """If _cleanup() raises, invoke() still returns the successful result."""

        class FailCleanupProvider(FakeProvider):
            def _cleanup(self) -> None:
                super()._cleanup()
                raise OSError("process already dead")

        provider = FailCleanupProvider()
        # Should not raise — _cleanup() exception is caught and logged
        result = provider.invoke("test")
        assert result.timed_out is False

    def test_cleanup_exception_does_not_mask_timeout_error(self) -> None:
        """If _cleanup() raises during timeout, ProviderTimeoutError still propagates."""

        class FailCleanupProvider(FakeProvider):
            def _cleanup(self) -> None:
                super()._cleanup()
                raise OSError("process already dead")

        provider = FailCleanupProvider(
            simulate_timeout=True,
            chunks_before_timeout=[],
        )
        with pytest.raises(ProviderTimeoutError):
            provider.invoke("test", timeout=300)


# ============================================================================
# TestHandleTimeoutIntegration — End-to-end without mocking _wait_for_grace
# ============================================================================


class TestHandleTimeoutIntegration:
    """Integration tests that exercise _handle_timeout() without mocking _wait_for_grace."""

    def test_active_stream_grace_period_with_stall(self) -> None:
        """Active stream at timeout → grace period → stall → partial result returned."""
        provider = FakeProvider()
        collector = ResultCollector()
        collector.add("x" * 300)
        start_time = time.monotonic()

        # Collector is active for the initial check, then stalls during grace
        with patch.object(
            collector, "is_active", side_effect=[True, False]
        ):
            with patch("bmad_assist_lite.providers.base.time.sleep"):
                result = provider._handle_timeout(
                    collector, 600, None, ("fake", "default"), start_time
                )

        assert result.timed_out is True
        assert result.stdout == "x" * 300
        assert result.exit_code == 0
