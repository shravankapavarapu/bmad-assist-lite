"""Tests for ResultMessage metric capture in ClaudeSDKProvider (REQ-10.1).

Covers:
- A ResultMessage carrying full metrics populates every ProviderResult field
- Cache-read / cache-creation token capture, in both key casings
- A stream with no ResultMessage leaves the metric fields None (never 0)
- Malformed ``usage`` keys and values are rejected rather than coerced
- Every rejected value is warned about, so no metric goes missing silently
- One malformed field never discards the others
- Non-finite costs are rejected
- A duplicate ResultMessage keeps the first and warns
- Instrumentation never raises, whatever the envelope does
- provider_session_id is sourced from ResultMessage.session_id
- The other three providers' ProviderResult construction sites still work
"""

import dataclasses
import inspect
import logging
import math
from typing import Any
from unittest.mock import MagicMock, patch

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from bmad_assist_lite.providers.base import ProviderResult
from bmad_assist_lite.providers.claude_sdk import ClaudeSDKProvider

# ============================================================================
# Helpers
# ============================================================================


def make_msg(texts: list[str]) -> AssistantMessage:
    """Create an AssistantMessage with TextBlock content blocks."""
    return AssistantMessage(content=[TextBlock(text=t) for t in texts], model="sonnet")


def make_result_message(
    *,
    usage: dict[str, Any] | None = None,
    total_cost_usd: float | None = 0.0421,
    duration_api_ms: int = 8500,
    session_id: str = "sess-abc-123",
) -> ResultMessage:
    """Create a ResultMessage with the fields the SDK 0.2.x actually declares."""
    return ResultMessage(
        subtype="success",
        duration_ms=9000,
        duration_api_ms=duration_api_ms,
        is_error=False,
        num_turns=3,
        session_id=session_id,
        total_cost_usd=total_cost_usd,
        usage=usage,
    )


class RaisingResultMessage(ResultMessage):
    """A ResultMessage whose usage access explodes, to prove capture never raises.

    Instances are made by re-classing a real ResultMessage (the dataclass
    ``__init__`` cannot assign through a read-only property).
    """

    @property  # type: ignore[override]
    def usage(self) -> dict[str, Any]:
        """Raise to simulate a hostile/incompatible SDK envelope."""
        raise RuntimeError("boom")


class HostileUsage(dict):  # type: ignore[type-arg]
    """A dict whose membership test explodes, to exercise the last-resort backstop."""

    def __contains__(self, key: object) -> bool:
        """Raise to simulate a payload hostile enough to break key lookup."""
        raise RuntimeError("hostile membership test")


async def make_fake_query(messages: list[Any]) -> Any:
    """Async generator yielding the given messages."""
    for msg in messages:
        yield msg


def warning_texts(caplog: Any) -> list[str]:
    """Return the messages of every WARNING record captured."""
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


# ============================================================================
# TestFullMetricCapture
# ============================================================================


class TestFullMetricCapture:
    """A complete ResultMessage populates every metric field."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_all_metric_fields_populated(self, mock_query: MagicMock) -> None:
        """Full metrics land on ProviderResult."""
        result_msg = make_result_message(
            usage={
                "input_tokens": 1234,
                "output_tokens": 567,
                "cache_read_input_tokens": 89012,
                "cache_creation_input_tokens": 3456,
            },
        )
        mock_query.return_value = make_fake_query([make_msg(["Hello ", "World"]), result_msg])

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.stdout == "Hello World"
        assert result.api_duration_ms == 8500
        assert result.input_tokens == 1234
        assert result.output_tokens == 567
        assert result.cache_read_tokens == 89012
        assert result.cache_creation_tokens == 3456
        assert result.total_cost_usd == 0.0421
        assert result.provider_session_id == "sess-abc-123"

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_session_id_populated_from_result_message(self, mock_query: MagicMock) -> None:
        """provider_session_id is sourced from ResultMessage.session_id."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(session_id="sess-xyz-999")]
        )

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.provider_session_id == "sess-xyz-999"

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_metrics_logged_when_present(self, mock_query: MagicMock, caplog: Any) -> None:
        """The completion log keeps duration=%dms and gains tokens/cost."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(
                    usage={
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 30,
                        "cache_creation_input_tokens": 40,
                    }
                ),
            ]
        )

        with caplog.at_level(logging.INFO, logger="bmad_assist_lite.providers.claude_sdk"):
            ClaudeSDKProvider().invoke("prompt", timeout=300)

        completion = [r for r in caplog.records if "Claude SDK completed" in r.getMessage()]
        assert len(completion) == 1
        message = completion[0].getMessage()
        assert "duration=" in message and "ms" in message
        assert "input_tokens=10" in message
        assert "output_tokens=20" in message
        assert "cache_read_tokens=30" in message
        assert "cache_creation_tokens=40" in message
        assert "cost_usd=" in message


# ============================================================================
# TestMissingResultMessage — NEG: absence yields None, never 0
# ============================================================================


class TestMissingResultMessage:
    """A stream with no ResultMessage must not break the invocation."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_no_result_message_leaves_metrics_none(self, mock_query: MagicMock) -> None:
        """Text still returned; every metric field is None, not 0."""
        mock_query.return_value = make_fake_query([make_msg(["Hello ", "World"])])

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.stdout == "Hello World"
        assert result.exit_code == 0
        assert result.api_duration_ms is None
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.cache_read_tokens is None
        assert result.cache_creation_tokens is None
        assert result.total_cost_usd is None
        assert result.provider_session_id is None
        # Wall-clock duration is measured locally and is unaffected.
        assert result.duration_ms >= 0

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_absent_optional_fields_yield_none_not_zero(self, mock_query: MagicMock) -> None:
        """A ResultMessage with no usage/cost yields None, never 0."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(usage=None, total_cost_usd=None)]
        )

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.cache_read_tokens is None
        assert result.cache_creation_tokens is None
        assert result.total_cost_usd is None
        assert result.api_duration_ms == 8500


# ============================================================================
# TestMalformedUsage — NEG: unusable usage shapes are rejected, not coerced
# ============================================================================


class TestMalformedUsage:
    """Unusable ``usage`` shapes yield None and a warning rather than a coerced value.

    These exercise the type-rejection guards, which return early — they do *not*
    reach (and so do not test) the last-resort ``except`` that backs the
    never-raise guarantee. That guarantee is tested by
    ``TestNeverRaises``, whose inputs raise inside the real provider path.
    """

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_usage_missing_token_keys_warns_and_does_not_raise(
        self, mock_query: MagicMock, caplog: Any
    ) -> None:
        """Missing token keys leave fields None and log a warning."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(usage={"cache_read_input_tokens": 5})]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.stdout == "text"
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_usage_wrong_type_warns_and_does_not_raise(
        self, mock_query: MagicMock, caplog: Any
    ) -> None:
        """A non-dict usage leaves fields None and logs a warning."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(usage="not-a-dict")]  # type: ignore[arg-type]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.stdout == "text"
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_usage_non_numeric_values_yield_none(self, mock_query: MagicMock) -> None:
        """Non-numeric token values are rejected rather than coerced."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(usage={"input_tokens": "many", "output_tokens": None}),
            ]
        )

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.input_tokens is None
        assert result.output_tokens is None


# ============================================================================
# TestCacheTokenCapture — D4: the cached share is the dominant one
# ============================================================================


class TestCacheTokenCapture:
    """Cache-read and cache-creation tokens are captured alongside the plain counts."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_snake_case_cache_keys_captured(self, mock_query: MagicMock) -> None:
        """The CLI's snake_case usage keys populate the cache token fields."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(
                    usage={
                        "input_tokens": 12,
                        "output_tokens": 34,
                        "cache_read_input_tokens": 56789,
                        "cache_creation_input_tokens": 1011,
                    }
                ),
            ]
        )

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.cache_read_tokens == 56789
        assert result.cache_creation_tokens == 1011

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_camel_case_cache_keys_captured(self, mock_query: MagicMock) -> None:
        """A camelCase payload (the SDK's ModelUsage shape) is read defensively."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(
                    usage={
                        "inputTokens": 12,
                        "outputTokens": 34,
                        "cacheReadInputTokens": 56789,
                        "cacheCreationInputTokens": 1011,
                    }
                ),
            ]
        )

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.input_tokens == 12
        assert result.output_tokens == 34
        assert result.cache_read_tokens == 56789
        assert result.cache_creation_tokens == 1011

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_absent_cache_keys_yield_none_not_zero(self, mock_query: MagicMock) -> None:
        """A usage payload with no cache keys yields None, never 0."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(usage={"input_tokens": 12, "output_tokens": 34}),
            ]
        )

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.cache_read_tokens is None
        assert result.cache_creation_tokens is None

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_non_integer_cache_value_warns_and_yields_none(
        self, mock_query: MagicMock, caplog: Any
    ) -> None:
        """A non-integer cache count is rejected loudly, not coerced."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(
                    usage={
                        "input_tokens": 12,
                        "output_tokens": 34,
                        "cache_read_input_tokens": "lots",
                    }
                ),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.cache_read_tokens is None
        assert result.input_tokens == 12
        assert any("cache_read_input_tokens" in text for text in warning_texts(caplog))


# ============================================================================
# TestDegradationIsAlwaysWarned — D3: a silent None is indistinguishable from
# "not supplied", which is exactly the confusion the None-never-0 rule prevents
# ============================================================================


class TestDegradationIsAlwaysWarned:
    """Every rejected metric value produces a WARNING naming the field."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_float_duration_warns(self, mock_query: MagicMock, caplog: Any) -> None:
        """A float duration_api_ms is rejected loudly, not silently."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(duration_api_ms=8500.0)]  # type: ignore[arg-type]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.api_duration_ms is None
        assert any("duration_api_ms" in text for text in warning_texts(caplog))

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_string_cost_warns(self, mock_query: MagicMock, caplog: Any) -> None:
        """A stringly-typed cost is rejected loudly, not silently."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(total_cost_usd="1.5")]  # type: ignore[arg-type]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.total_cost_usd is None
        assert any("total_cost_usd" in text for text in warning_texts(caplog))

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_non_string_session_id_warns(self, mock_query: MagicMock, caplog: Any) -> None:
        """A non-string session_id is rejected loudly, not silently."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(session_id=12345)]  # type: ignore[arg-type]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.provider_session_id is None
        assert any("session_id" in text for text in warning_texts(caplog))

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_absent_values_are_not_warned_about(self, mock_query: MagicMock, caplog: Any) -> None:
        """A cost the provider simply did not supply is not a coercion failure."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(
                    usage={"input_tokens": 1, "output_tokens": 2}, total_cost_usd=None
                ),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.total_cost_usd is None
        assert not any("total_cost_usd" in text for text in warning_texts(caplog))


# ============================================================================
# TestNonFiniteCost — D5: inf/nan corrupt an aggregate worse than 0
# ============================================================================


class TestNonFiniteCost:
    """Non-finite costs are rejected rather than propagated onto ProviderResult."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_infinite_cost_rejected(self, mock_query: MagicMock, caplog: Any) -> None:
        """An infinite cost yields None and a warning."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(total_cost_usd=float("inf"))]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.total_cost_usd is None
        assert any("total_cost_usd" in text for text in warning_texts(caplog))

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_nan_cost_rejected(self, mock_query: MagicMock) -> None:
        """A NaN cost yields None, never a NaN that poisons every later sum."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(total_cost_usd=float("nan"))]
        )

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.total_cost_usd is None

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_finite_cost_still_accepted(self, mock_query: MagicMock) -> None:
        """The finiteness guard does not reject legitimate costs."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(total_cost_usd=0.25)]
        )

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.total_cost_usd == 0.25
        assert math.isfinite(result.total_cost_usd)


# ============================================================================
# TestPerFieldIsolation — D1/D2: one bad field must not discard the others
# ============================================================================


class TestPerFieldIsolation:
    """A malformed value costs only itself; the extractable metrics survive."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_unsortable_usage_keys_preserve_tokens(
        self, mock_query: MagicMock, caplog: Any
    ) -> None:
        """Mixed-type usage keys do not abort the extraction that reports them.

        ``output_tokens`` is deliberately absent: that is what drives the
        missing-count warning, which is the only place the keys are rendered.
        Rendering them must not destroy the counts that *were* extractable.
        """
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(
                    usage={1: "a", "b": 2, "input_tokens": 10, "cache_read_input_tokens": 99}
                ),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.input_tokens == 10
        assert result.cache_read_tokens == 99
        assert result.output_tokens is None
        assert result.total_cost_usd == 0.0421
        assert result.api_duration_ms == 8500
        assert any(
            "lacks usable prompt/completion token counts" in t for t in warning_texts(caplog)
        )

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_unsortable_usage_keys_with_full_counts(self, mock_query: MagicMock) -> None:
        """Mixed-type keys alongside complete counts are simply ignored."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(
                    usage={1: "a", "b": 2, "input_tokens": 10, "output_tokens": 20}
                ),
            ]
        )

        result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.input_tokens == 10
        assert result.output_tokens == 20

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_unconvertible_cost_preserves_tokens(self, mock_query: MagicMock, caplog: Any) -> None:
        """A cost too large to convert to a float does not cost the token counts."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(
                    usage={"input_tokens": 10, "output_tokens": 20},
                    total_cost_usd=10**400,  # type: ignore[arg-type]
                ),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.total_cost_usd is None
        assert result.input_tokens == 10
        assert result.output_tokens == 20
        assert any("total_cost_usd" in text for text in warning_texts(caplog))

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_unreadable_usage_preserves_cost_and_session(
        self, mock_query: MagicMock, caplog: Any
    ) -> None:
        """An exploding usage attribute costs the tokens only."""
        hostile = make_result_message(session_id="sess-boom")
        hostile.__class__ = RaisingResultMessage
        mock_query.return_value = make_fake_query([make_msg(["text"]), hostile])

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.total_cost_usd == 0.0421
        assert result.api_duration_ms == 8500
        assert result.provider_session_id == "sess-boom"


# ============================================================================
# TestNeverRaises — the load-bearing NEG: instrumentation must never fail a run
# ============================================================================


class TestNeverRaises:
    """Envelopes that raise inside the real provider path are caught, not propagated.

    Unlike ``TestMalformedUsage``, every input here raises during extraction, so
    each one reaches (and therefore tests) a defensive ``except``.
    """

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_exception_during_extraction_warns_and_does_not_raise(
        self, mock_query: MagicMock, caplog: Any
    ) -> None:
        """An attribute that explodes is caught, warned about, and never propagated."""
        hostile = make_result_message(session_id="sess-boom")
        hostile.__class__ = RaisingResultMessage
        mock_query.return_value = make_fake_query([make_msg(["text"]), hostile])

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.stdout == "text"
        assert result.exit_code == 0
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert any("usage" in text for text in warning_texts(caplog))

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_hostile_usage_dict_warns_and_does_not_raise(
        self, mock_query: MagicMock, caplog: Any
    ) -> None:
        """A dict whose key lookup explodes hits the backstop instead of the caller."""
        mock_query.return_value = make_fake_query(
            [make_msg(["text"]), make_result_message(usage=HostileUsage(input_tokens=10))]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.stdout == "text"
        assert result.exit_code == 0
        assert result.input_tokens is None
        assert result.cache_read_tokens is None
        assert any("hostile membership test" in text for text in warning_texts(caplog))


# ============================================================================
# TestDuplicateResultMessage — D6: last-wins silently swaps metrics for nothing
# ============================================================================


class TestDuplicateResultMessage:
    """A second ResultMessage is a protocol anomaly: keep the first, warn about both."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_first_result_message_wins(self, mock_query: MagicMock, caplog: Any) -> None:
        """The first envelope's metrics survive a later, emptier one."""
        first = make_result_message(
            usage={"input_tokens": 10, "output_tokens": 20},
            total_cost_usd=0.5,
            duration_api_ms=8500,
            session_id="sess-first",
        )
        second = make_result_message(
            usage=None, total_cost_usd=None, duration_api_ms=0, session_id="sess-second"
        )
        mock_query.return_value = make_fake_query([make_msg(["text"]), first, second])

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            result = ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert result.input_tokens == 10
        assert result.output_tokens == 20
        assert result.total_cost_usd == 0.5
        assert result.api_duration_ms == 8500
        assert result.provider_session_id == "sess-first"

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_duplicate_warns_naming_both(self, mock_query: MagicMock, caplog: Any) -> None:
        """The warning names the kept and the ignored envelope."""
        mock_query.return_value = make_fake_query(
            [
                make_msg(["text"]),
                make_result_message(session_id="sess-first"),
                make_result_message(session_id="sess-second"),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            ClaudeSDKProvider().invoke("prompt", timeout=300)

        duplicates = [t for t in warning_texts(caplog) if "more than one ResultMessage" in t]
        assert len(duplicates) == 1
        assert "sess-first" in duplicates[0]
        assert "sess-second" in duplicates[0]

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_single_result_message_does_not_warn(self, mock_query: MagicMock, caplog: Any) -> None:
        """The normal single-envelope stream stays silent."""
        mock_query.return_value = make_fake_query([make_msg(["text"]), make_result_message()])

        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.providers.claude_sdk"):
            ClaudeSDKProvider().invoke("prompt", timeout=300)

        assert not any("more than one ResultMessage" in t for t in warning_texts(caplog))


# ============================================================================
# TestConstructionCompatibility — the other three providers must keep working
# ============================================================================

# The six fields every provider passes positionally-or-by-keyword, in order.
REQUIRED_FIELDS = ("stdout", "stderr", "exit_code", "duration_ms", "model", "command")


class TestConstructionCompatibility:
    """Every existing ProviderResult construction site still type-checks and runs.

    The keyword-set cases below are mirrors of the real call sites, cited by
    symbol rather than line number so they cannot go stale on an unrelated edit.
    ``test_optional_fields_all_default`` checks the property those mirrors depend
    on against the class itself, so a new required field is caught even if a
    mirror is never updated.
    """

    def test_optional_fields_all_default(self) -> None:
        """Every field beyond the required six is optional, so appends stay additive."""
        fields = dataclasses.fields(ProviderResult)
        names = [f.name for f in fields]

        assert tuple(names[: len(REQUIRED_FIELDS)]) == REQUIRED_FIELDS
        for field in fields[: len(REQUIRED_FIELDS)]:
            assert field.default is dataclasses.MISSING
        for field in fields[len(REQUIRED_FIELDS) :]:
            assert field.default is not dataclasses.MISSING

    def test_metric_fields_default_to_none_not_zero(self) -> None:
        """No metric field defaults to 0 — a zero would be a silent false measurement."""
        metric_names = (
            "api_duration_ms",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "total_cost_usd",
        )
        defaults = {f.name: f.default for f in dataclasses.fields(ProviderResult)}

        for name in metric_names:
            assert name in defaults, f"{name} missing from ProviderResult"
            assert defaults[name] is None

    def test_required_fields_are_positional(self) -> None:
        """The required six accept positional arguments, in the declared order."""
        params = list(inspect.signature(ProviderResult).parameters.values())

        for param in params[: len(REQUIRED_FIELDS)]:
            assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD

    def test_codex_style_construction(self) -> None:
        """codex.py CodexProvider._do_invoke keyword set — no session id, no metrics."""
        result = ProviderResult(
            stdout="out",
            stderr="err",
            exit_code=0,
            duration_ms=10,
            model="gpt-5.3-codex",
            command=("codex", "exec"),
        )

        assert result.provider_session_id is None
        assert result.api_duration_ms is None
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.cache_read_tokens is None
        assert result.cache_creation_tokens is None
        assert result.total_cost_usd is None
        assert result.timed_out is False

    def test_gemini_and_cursor_style_construction(self) -> None:
        """gemini.py / cursor.py _do_invoke keyword set — session id, no metrics."""
        result = ProviderResult(
            stdout="out",
            stderr="",
            exit_code=0,
            duration_ms=10,
            model="gemini-2.5-flash",
            command=("gemini", "-p"),
            provider_session_id="gemini-session",
        )

        assert result.provider_session_id == "gemini-session"
        assert result.api_duration_ms is None
        assert result.cache_read_tokens is None
        assert result.total_cost_usd is None

    def test_base_timeout_style_construction(self) -> None:
        """base.py BaseProvider._handle_timeout keyword set — timed_out partial results."""
        result = ProviderResult(
            stdout="partial",
            stderr="",
            exit_code=0,
            duration_ms=10,
            model="opus",
            command=("claude", "opus"),
            timed_out=True,
        )

        assert result.timed_out is True
        assert result.input_tokens is None
        assert result.cache_read_tokens is None

    def test_positional_construction_still_works(self) -> None:
        """The six required fields remain positional and in the same order."""
        result = ProviderResult("out", "err", 0, 42, "opus", ("claude",))

        assert result.stdout == "out"
        assert result.duration_ms == 42
        assert result.total_cost_usd is None
