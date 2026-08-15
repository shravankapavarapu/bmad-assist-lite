"""Tests for per-call metric survival across the provider timeout boundary.

A timed-out invocation must not silently report zero tokens and zero cost. The
slowest, most expensive phases are the ones most likely to time out, so dropping
them from the sample biases any token aggregate toward a false reduction.

Covers:
- Metrics recorded on the collector before a timeout reach the partial result.
- Metrics recorded before a timeout reach the result attached to
  ProviderTimeoutError.
- With no metrics recorded, every metric field is None — never 0 — and the call
  is still flagged timed_out=True so a measurement consumer can reject it.
- ResultCollector.metrics is per-invocation state that cannot leak between calls.
"""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from bmad_assist_lite.core.exceptions import ProviderTimeoutError
from bmad_assist_lite.providers import claude_sdk
from bmad_assist_lite.providers.base import (
    MIN_USEFUL_RESPONSE_CHARS,
    BaseProvider,
    ProviderResult,
)
from bmad_assist_lite.providers.claude_sdk import ClaudeSDKProvider
from bmad_assist_lite.providers.result_collector import CallMetrics, ResultCollector

# Metric fields that must survive the timeout path onto ProviderResult.
_METRIC_FIELDS = (
    "api_duration_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "total_cost_usd",
)

FULL_METRICS = CallMetrics(
    api_duration_ms=4200,
    input_tokens=120,
    output_tokens=340,
    cache_read_tokens=90_000,
    cache_creation_tokens=1_500,
    total_cost_usd=0.37,
    session_id="sess-timeout",
)


@pytest.fixture(autouse=True)
def no_grace_period() -> Iterator[None]:
    """Report the stream as silent so no grace period is waited out.

    These tests are about which metrics reach the result, not about grace timing;
    without this the 60s floor would be slept through on every case.
    """
    with patch.object(ResultCollector, "is_active", return_value=False):
        yield


class TimingOutProvider(BaseProvider):
    """Provider double that optionally records metrics, then times out."""

    def __init__(self, *, text: str, metrics: CallMetrics | None) -> None:
        """Store the partial text to emit and the metrics to record, if any."""
        self._text = text
        self._metrics = metrics

    @property
    def provider_name(self) -> str:
        """Return test provider name."""
        return "timing-out"

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
        effort: str | None = None,
        color_index: int | None = None,
        system_prompt: str | None = None,
        resume: str | None = None,
    ) -> ProviderResult:
        """Emit partial text, optionally record metrics, then time out."""
        if self._text:
            collector.add(self._text)
        if self._metrics is not None:
            collector.record_metrics(self._metrics)
        raise TimeoutError("simulated timeout")

    def _cleanup(self) -> None:
        """No resources to release."""

    def parse_output(self, result: ProviderResult) -> str:
        """Return stdout as-is."""
        return result.stdout

    def supports_model(self, model: str) -> bool:
        """Support all models."""
        return True


# ============================================================================
# CallMetrics transport on ResultCollector
# ============================================================================


class TestCollectorMetrics:
    """ResultCollector carries per-invocation metrics for the timeout path."""

    def test_fresh_collector_has_no_metrics(self) -> None:
        """A new collector reports metrics=None, not an all-zero record."""
        assert ResultCollector().metrics is None

    def test_record_metrics_round_trips(self) -> None:
        """Recorded metrics are returned unchanged."""
        collector = ResultCollector()
        collector.record_metrics(FULL_METRICS)

        assert collector.metrics == FULL_METRICS

    def test_metrics_do_not_leak_between_collectors(self) -> None:
        """Each invocation gets a fresh collector, so metrics cannot carry over.

        This is the property that makes the collector the right transport: storing
        metrics on the provider instance would let a previous call's numbers be
        reported for a later timed-out call.
        """
        first = ResultCollector()
        first.record_metrics(FULL_METRICS)

        assert ResultCollector().metrics is None

    def test_call_metrics_defaults_are_all_none(self) -> None:
        """An unpopulated CallMetrics reports None everywhere, never 0."""
        empty = CallMetrics()

        for field in _METRIC_FIELDS:
            assert getattr(empty, field) is None
        assert empty.session_id is None


# ============================================================================
# Metrics survive into the partial ProviderResult
# ============================================================================


class TestTimeoutCarriesMetrics:
    """A timed-out call reports whatever metrics were available."""

    def test_partial_result_carries_recorded_metrics(self) -> None:
        """Metrics recorded before the timeout reach the returned ProviderResult."""
        provider = TimingOutProvider(
            text="x" * (MIN_USEFUL_RESPONSE_CHARS + 10), metrics=FULL_METRICS
        )

        result = provider.invoke("prompt", timeout=1)

        assert result.timed_out is True
        assert result.api_duration_ms == 4200
        assert result.input_tokens == 120
        assert result.output_tokens == 340
        assert result.cache_read_tokens == 90_000
        assert result.cache_creation_tokens == 1_500
        assert result.total_cost_usd == 0.37
        assert result.provider_session_id == "sess-timeout"

    def test_timeout_error_partial_result_carries_recorded_metrics(self) -> None:
        """Metrics reach the ProviderResult attached to ProviderTimeoutError."""
        provider = TimingOutProvider(text="short", metrics=FULL_METRICS)

        with pytest.raises(ProviderTimeoutError) as exc_info:
            provider.invoke("prompt", timeout=1)

        partial = exc_info.value.partial_result
        assert partial is not None
        assert partial.timed_out is True
        assert partial.input_tokens == 120
        assert partial.cache_read_tokens == 90_000
        assert partial.total_cost_usd == 0.37
        assert partial.provider_session_id == "sess-timeout"


# ============================================================================
# Absence is explicit and detectable
# ============================================================================


class TestTimeoutWithoutMetrics:
    """No metrics available means None everywhere — never a silent zero."""

    def test_partial_result_reports_none_not_zero(self) -> None:
        """Unavailable metrics are None, so no aggregate can count them as 0."""
        provider = TimingOutProvider(text="y" * (MIN_USEFUL_RESPONSE_CHARS + 10), metrics=None)

        result = provider.invoke("prompt", timeout=1)

        for field in _METRIC_FIELDS:
            value = getattr(result, field)
            assert value is None, f"{field} is {value!r}; unavailable must be None, never 0"
        assert result.provider_session_id is None

    def test_partial_result_is_flagged_timed_out(self) -> None:
        """timed_out=True survives, so a measurement consumer can reject the sample."""
        provider = TimingOutProvider(text="y" * (MIN_USEFUL_RESPONSE_CHARS + 10), metrics=None)

        result = provider.invoke("prompt", timeout=1)

        assert result.timed_out is True

    def test_timeout_error_partial_result_reports_none_not_zero(self) -> None:
        """The error-attached result is equally explicit about absence."""
        provider = TimingOutProvider(text="short", metrics=None)

        with pytest.raises(ProviderTimeoutError) as exc_info:
            provider.invoke("prompt", timeout=1)

        partial = exc_info.value.partial_result
        assert partial is not None
        assert partial.timed_out is True
        for field in _METRIC_FIELDS:
            value = getattr(partial, field)
            assert value is None, f"{field} is {value!r}; unavailable must be None, never 0"

    def test_partially_available_metrics_are_carried_verbatim(self) -> None:
        """A half-populated record is carried as-is: present fields kept, absent None."""
        provider = TimingOutProvider(
            text="z" * (MIN_USEFUL_RESPONSE_CHARS + 10),
            metrics=CallMetrics(input_tokens=7, total_cost_usd=None),
        )

        result = provider.invoke("prompt", timeout=1)

        assert result.timed_out is True
        assert result.input_tokens == 7
        assert result.output_tokens is None
        assert result.total_cost_usd is None


# ============================================================================
# End-to-end: the real Claude timeout path
# ============================================================================


class TestClaudeSdkTimeoutCarriesMetrics:
    """The mechanism is wired into the real provider, not just the base class.

    The window this closes: ResultMessage is the terminal envelope, so a timeout
    normally fires before it arrives. But the stream can hang *after* it during
    teardown — an un-reaped subprocess is the observed case in this project's own
    forensics — and until now those metrics were discarded with the cancelled
    coroutine.
    """

    def _stream(self, *, with_result_message: bool) -> Any:
        async def fake_query(**kwargs: Any) -> Any:
            yield AssistantMessage(
                content=[TextBlock(text="x" * (MIN_USEFUL_RESPONSE_CHARS + 10))],
                model="opus",
            )
            if with_result_message:
                yield ResultMessage(
                    subtype="success",
                    duration_ms=9000,
                    duration_api_ms=8500,
                    is_error=False,
                    num_turns=3,
                    session_id="sess-hung",
                    total_cost_usd=0.0421,
                    usage={
                        "input_tokens": 120,
                        "output_tokens": 340,
                        "cache_read_input_tokens": 90_000,
                        "cache_creation_input_tokens": 1_500,
                    },
                )
            # The stream never finishes — teardown hangs, so wait_for cancels us.
            await asyncio.Event().wait()

        return fake_query

    def test_metrics_recorded_before_the_hang_survive(self) -> None:
        """A ResultMessage seen before the hang reaches the timed-out result."""
        provider = ClaudeSDKProvider()

        with patch.object(claude_sdk, "query", self._stream(with_result_message=True)):
            result = provider.invoke("prompt", model="opus", timeout=1)

        assert result.timed_out is True
        assert result.input_tokens == 120
        assert result.output_tokens == 340
        assert result.cache_read_tokens == 90_000
        assert result.cache_creation_tokens == 1_500
        assert result.total_cost_usd == 0.0421
        assert result.api_duration_ms == 8500
        assert result.provider_session_id == "sess-hung"

    def test_timeout_before_any_envelope_reports_none(self) -> None:
        """No envelope seen means every metric is None — the honest common case."""
        provider = ClaudeSDKProvider()

        with patch.object(claude_sdk, "query", self._stream(with_result_message=False)):
            result = provider.invoke("prompt", model="opus", timeout=1)

        assert result.timed_out is True
        for field in _METRIC_FIELDS:
            value = getattr(result, field)
            assert value is None, f"{field} is {value!r}; unavailable must be None, never 0"
