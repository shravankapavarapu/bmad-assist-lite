"""Claude Agent SDK-based provider implementation.

Implements the BaseProvider Template Method contract (Story 7.3):
- _do_invoke() feeds a ResultCollector during async streaming
- _cleanup() terminates orphan claude processes on timeout
- invoke() is inherited from BaseProvider (not overridden)
"""

import asyncio
import logging
import math
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    TextBlock,
    query,
)

from bmad_assist_lite.core.exceptions import ProviderError
from bmad_assist_lite.providers._windows import is_pid_alive, terminate_process
from bmad_assist_lite.providers.base import (
    BaseProvider,
    ProviderResult,
    resolve_cli_path,
    validate_settings_file,
)
from bmad_assist_lite.providers.result_collector import CallMetrics, ResultCollector

logger = logging.getLogger(__name__)

SUPPORTED_MODELS: frozenset[str] = frozenset({"opus", "sonnet", "haiku"})

# claude-agent-sdk >= 0.2.x escalation quirk: when the CLI emits a result with
# is_error=true but subtype="success" and an empty errors array, then exits
# non-zero, the SDK builds the message
# f"Claude Code returned an error result: {subtype}" and re-raises it as a bare
# Exception (claude_agent_sdk/_internal/query.py:340-344, 851-852). The turn
# actually succeeded; the non-zero process exit is ancillary. Genuine failures
# carry a different subtype (e.g. error_max_turns) or joined error messages, so
# matching the prefix AND requiring the subtype to be exactly "success" keeps
# this narrow and lets real errors propagate.
_SDK_ERROR_RESULT_PREFIX = "Claude Code returned an error result:"
_SDK_BENIGN_SUCCESS_SUBTYPE = "success"


def _is_benign_success_error(exc: Exception) -> bool:
    """Return True if exc is the SDK's benign "error result: success" escalation.

    Only matches when the trailing subtype is exactly "success"; any other
    subtype (real error) or joined error message does not match, so genuine
    failures are never swallowed.
    """
    message = str(exc).strip()
    if not message.startswith(_SDK_ERROR_RESULT_PREFIX):
        return False
    subtype = message[len(_SDK_ERROR_RESULT_PREFIX) :].strip()
    return subtype == _SDK_BENIGN_SUCCESS_SUBTYPE


# ============================================================================
# Per-call metric capture
# ============================================================================


# Token keys read out of the raw ``usage`` dict, in lookup order. The CLI passes
# ``usage`` through verbatim from the API, which is snake_case; the camelCase
# spellings mirror the SDK's ``ModelUsage`` TypedDict and are accepted
# defensively in case the CLI ever forwards that shape here instead.
_USAGE_TOKEN_KEYS: dict[str, tuple[str, ...]] = {
    "input_tokens": ("input_tokens", "inputTokens"),
    "output_tokens": ("output_tokens", "outputTokens"),
    "cache_read_tokens": ("cache_read_input_tokens", "cacheReadInputTokens"),
    "cache_creation_tokens": ("cache_creation_input_tokens", "cacheCreationInputTokens"),
}


def _empty_tokens() -> dict[str, int | None]:
    """Return the token map with every entry unavailable."""
    return dict.fromkeys(_USAGE_TOKEN_KEYS, None)


def _read_attr(message: ResultMessage, name: str) -> object:
    """Return ``message.name``, degrading to None and a WARNING if access raises.

    Attribute access is the one step that can fail before any type check runs
    (a hostile or incompatible envelope may raise from a property), so it is
    isolated per field: one unreadable attribute must not cost the others.

    Args:
        message: The SDK result envelope.
        name: The attribute to read.

    Returns:
        The attribute value, or None if it could not be read.

    """
    try:
        return getattr(message, name)
    except Exception as e:
        logger.warning(
            "Claude SDK result field %r could not be read (%s); that metric is "
            "unavailable for this call.",
            name,
            e,
        )
        return None


def _as_int(value: object, field: str) -> int | None:
    """Return value as an int, or None with a WARNING if it is not a plain integer.

    Args:
        value: The candidate value, of unknown type.
        field: The field name, for the warning.

    Returns:
        The integer value, or None. Booleans are rejected — ``True`` is not a
        token count. A value of None means "not supplied" and is not warned
        about; any other unusable shape is a coercion failure and is warned
        about, so a dropped metric is never silent.

    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        logger.warning(
            "Claude SDK metric %r is %s, not an integer; it is unavailable for this call.",
            field,
            type(value).__name__,
        )
        return None
    return value


def _as_float(value: object, field: str) -> float | None:
    """Return value as a finite float, or None with a WARNING otherwise.

    Args:
        value: The candidate value, of unknown type.
        field: The field name, for the warning.

    Returns:
        The float value, or None. Booleans, non-numerics, values too large to
        convert, and non-finite results (``inf`` / ``nan``) are all rejected —
        a non-finite value corrupts an aggregate worse than the ``0`` that the
        None-never-zero rule exists to prevent. Only None passes silently.

    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        logger.warning(
            "Claude SDK metric %r is %s, not numeric; it is unavailable for this call.",
            field,
            type(value).__name__,
        )
        return None
    try:
        coerced = float(value)
    except Exception as e:
        logger.warning(
            "Claude SDK metric %r could not be converted to a float (%s); it is "
            "unavailable for this call.",
            field,
            e,
        )
        return None
    if not math.isfinite(coerced):
        logger.warning(
            "Claude SDK metric %r is non-finite (%r); it is unavailable for this call.",
            field,
            coerced,
        )
        return None
    return coerced


def _as_str(value: object, field: str) -> str | None:
    """Return value as a str, or None with a WARNING if it is not one.

    Args:
        value: The candidate value, of unknown type.
        field: The field name, for the warning.

    Returns:
        The string value, or None. Only None passes silently.

    """
    if value is None:
        return None
    if not isinstance(value, str):
        logger.warning(
            "Claude SDK metric %r is %s, not a string; it is unavailable for this call.",
            field,
            type(value).__name__,
        )
        return None
    return value


def _describe_keys(usage: dict[Any, Any]) -> list[str]:
    """Return the payload's keys as sorted reprs.

    Sorting the keys directly raises ``TypeError`` on a payload with mixed key
    types, which would abort the extraction the warning exists to describe.
    Sorting their reprs is total.

    Args:
        usage: The usage payload.

    Returns:
        The keys, rendered and sorted.

    """
    return sorted(repr(key) for key in usage)


def _extract_usage_tokens(usage: object) -> dict[str, int | None]:
    """Read every known token count out of a raw ``usage`` payload.

    Args:
        usage: The ``ResultMessage.usage`` value, of unknown shape.

    Returns:
        A map of ``_USAGE_TOKEN_KEYS`` names to counts, None where unavailable.
        Each key is read independently: one unusable count never discards the
        others.

    """
    tokens = _empty_tokens()
    if usage is None:
        return tokens
    if not isinstance(usage, dict):
        logger.warning(
            "Claude SDK usage payload is %s, not a dict; token metrics for this "
            "call are unavailable.",
            type(usage).__name__,
        )
        return tokens

    for field, aliases in _USAGE_TOKEN_KEYS.items():
        for alias in aliases:
            if alias in usage:
                tokens[field] = _as_int(usage[alias], alias)
                break

    if tokens["input_tokens"] is None or tokens["output_tokens"] is None:
        logger.warning(
            "Claude SDK usage payload lacks usable prompt/completion token counts "
            "(keys=%s); those token metrics for this call are unavailable.",
            _describe_keys(usage),
        )
    return tokens


def _extract_usage_tokens_safe(usage: object) -> dict[str, int | None]:
    """Call :func:`_extract_usage_tokens`, degrading to an empty map on any error.

    Last-resort backstop for a payload hostile enough to raise from ``in`` or
    iteration. Losing the token counts is acceptable; failing the invocation is
    not.

    Args:
        usage: The ``ResultMessage.usage`` value, of unknown shape.

    Returns:
        The token map, all-None if extraction raised.

    """
    try:
        return _extract_usage_tokens(usage)
    except Exception as e:
        logger.warning("Failed to extract Claude SDK usage tokens: %s", e)
        return _empty_tokens()


def _extract_metrics(message: ResultMessage | None) -> CallMetrics:
    """Extract per-call metrics from a ``ResultMessage`` without ever raising.

    Instrumentation must not be able to fail a real invocation, so every
    unexpected shape — an attribute that raises, a non-dict ``usage``, absent or
    non-numeric token keys, an unconvertible or non-finite cost — degrades to
    ``None`` **and** a WARNING rather than propagating. A value the provider
    simply did not supply (``None``) is not a failure and is not warned about.

    Every field is read independently, so one malformed value costs only itself:
    a bad ``total_cost_usd`` must not take the token counts down with it.

    The guarantee is made structural by a top-level ``except``. Every input
    reachable today is already caught by an inner guard (``_read_attr`` per
    attribute, ``_extract_usage_tokens_safe`` around the token path), so the
    backstop is unreachable on current code. It exists for the next field read
    added here: a read placed outside those helpers would otherwise propagate and
    fail a real invocation, contradicting the guarantee above. Fields gathered
    before such a raise are still returned — a partial metric set beats none.

    When a stream carries more than one ``ResultMessage``, the **first** is the
    one extracted here — see :meth:`_invoke_async_with_collector`.

    Args:
        message: The terminal result message from the SDK stream, or None when
            the stream carried no result envelope.

    Returns:
        The extracted metrics, with None for anything unavailable.

    """
    if message is None:
        return CallMetrics()

    tokens = _empty_tokens()
    api_duration_ms: int | None = None
    total_cost_usd: float | None = None
    session_id: str | None = None

    try:
        tokens = _extract_usage_tokens_safe(_read_attr(message, "usage"))
        api_duration_ms = _as_int(_read_attr(message, "duration_api_ms"), "duration_api_ms")
        total_cost_usd = _as_float(_read_attr(message, "total_cost_usd"), "total_cost_usd")
        session_id = _as_str(_read_attr(message, "session_id"), "session_id")
    except Exception as e:
        logger.warning(
            "Claude SDK metric extraction raised past its inner guards (%s); "
            "reporting only the metrics gathered before the failure. This means a "
            "field is being read outside a guarded helper — fix that read.",
            e,
        )

    return CallMetrics(
        api_duration_ms=api_duration_ms,
        input_tokens=tokens["input_tokens"],
        output_tokens=tokens["output_tokens"],
        cache_read_tokens=tokens["cache_read_tokens"],
        cache_creation_tokens=tokens["cache_creation_tokens"],
        total_cost_usd=total_cost_usd,
        session_id=session_id,
    )


def _format_metric_suffix(metrics: CallMetrics) -> str:
    """Render the available metrics as a log suffix.

    Args:
        metrics: The captured metrics.

    Returns:
        A leading-comma suffix such as ``", input_tokens=10"``, or an empty
        string when no metric was captured.

    """
    parts: list[str] = []
    if metrics.api_duration_ms is not None:
        parts.append(f"api_duration={metrics.api_duration_ms}ms")
    if metrics.input_tokens is not None:
        parts.append(f"input_tokens={metrics.input_tokens}")
    if metrics.output_tokens is not None:
        parts.append(f"output_tokens={metrics.output_tokens}")
    if metrics.cache_read_tokens is not None:
        parts.append(f"cache_read_tokens={metrics.cache_read_tokens}")
    if metrics.cache_creation_tokens is not None:
        parts.append(f"cache_creation_tokens={metrics.cache_creation_tokens}")
    if metrics.total_cost_usd is not None:
        parts.append(f"cost_usd={metrics.total_cost_usd:.6f}")
    return ", " + ", ".join(parts) if parts else ""


class ClaudeSDKProvider(BaseProvider):
    """Claude Code SDK-based provider implementation.

    Uses the BaseProvider Template Method: invoke() is inherited and drives the
    full lifecycle (create collector -> _do_invoke() -> handle timeout -> _cleanup()).
    This class only implements the hooks: _do_invoke(), _cleanup(), parse_output(),
    and supports_model().
    """

    def __init__(self) -> None:
        """Initialize provider with PID tracking for orphan cleanup."""
        super().__init__()
        self._current_pid: int | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider identifier string."""
        return "claude"

    @property
    def default_model(self) -> str | None:
        """Return the default model identifier."""
        return "sonnet"

    def supports_model(self, model: str) -> bool:
        """Return True if the model is a known Claude model."""
        return model in SUPPORTED_MODELS or model.startswith("claude-")

    def _resolve_cli_path(self) -> str | None:
        """Resolve the system Claude Code CLI path, best-effort.

        Pointing the SDK at the system ``claude`` CLI (via
        ``ClaudeAgentOptions.cli_path``) avoids the older ``claude.exe`` the SDK
        ships in its ``_bundled`` directory, which has exhibited long-turn
        output truncation. Resolution is best-effort: if no system CLI is found,
        returns ``None`` so the SDK falls back to its bundled binary rather than
        failing the invocation.
        """
        try:
            cli_path = resolve_cli_path(self.provider_name)
            logger.debug("Using system Claude CLI for SDK: %s", cli_path)
            return cli_path
        except ProviderError:
            logger.debug("No system 'claude' CLI resolved; SDK will use its bundled binary.")
            return None

    async def _invoke_async_with_collector(
        self,
        prompt: str,
        model: str,
        settings: Path | None,
        cwd: Path | None,
        collector: ResultCollector,
        allowed_tools: list[str] | None = None,
        effort: str | None = None,
    ) -> str:
        """Stream the SDK query, returning the collected text.

        Metrics are not returned; they are recorded onto ``collector`` the moment
        the terminal envelope arrives, so that a call cancelled by the timeout
        still reports whatever it had learned.

        A well-formed stream carries exactly one ``ResultMessage``. If more than
        one arrives, the **first** is kept and each subsequent one is logged as a
        WARNING naming both: a second envelope is far more likely to be a
        protocol anomaly than a correction, and silently last-wins would swap a
        populated metric set for an empty one.

        Returns:
            The accumulated response text.

        """
        extra_args: dict[str, str | None] = {}
        if effort:
            extra_args["effort"] = effort

        options = ClaudeAgentOptions(
            model=model,
            permission_mode="acceptEdits",
            settings=str(settings) if settings is not None else None,
            cwd=cwd,
            tools=allowed_tools if allowed_tools is not None else None,
            extra_args=extra_args,
            cli_path=self._resolve_cli_path(),
        )

        result_message: ResultMessage | None = None

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collector.add(block.text)
                elif isinstance(message, ResultMessage):
                    if result_message is None:
                        result_message = message
                        # Record immediately rather than after the loop: if the
                        # stream hangs during teardown (an un-reaped subprocess
                        # is the observed case), wait_for cancels this coroutine
                        # and the local is lost. Only what is on the collector
                        # survives into _handle_timeout().
                        collector.record_metrics(_extract_metrics(message))
                    else:
                        logger.warning(
                            "Claude SDK stream carried more than one ResultMessage "
                            "(keeping session_id=%r, ignoring session_id=%r); a second "
                            "envelope is more likely a protocol anomaly than a "
                            "correction, so the first is kept.",
                            _read_attr(result_message, "session_id"),
                            _read_attr(message, "session_id"),
                        )
        except Exception as e:
            # Swallow only the benign "error result: success" escalation, and
            # only when the turn produced real output. Everything else (real
            # SDK errors, CLINotFoundError, empty-output benign quirk) re-raises
            # to the typed handlers in _do_invoke().
            if _is_benign_success_error(e) and collector.text.strip():
                logger.warning(
                    "Claude SDK reported a non-zero exit with subtype 'success' "
                    "after a completed turn (known CLI/SDK 0.2.x quirk); treating "
                    "%d chars of collected output as success.",
                    len(collector.text),
                )
            else:
                raise

        if collector.is_empty:
            raise ProviderError("No response received from SDK")

        return collector.text

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
    ) -> ProviderResult:
        """Execute Claude SDK with the given prompt and return the result."""
        _ = color_index

        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")

        effective_model = model or self.default_model or "sonnet"

        if not self.supports_model(effective_model):
            raise ProviderError(
                f"Unsupported model '{effective_model}' for Claude provider. "
                f"Supported: {', '.join(sorted(SUPPORTED_MODELS))} or claude-* identifiers"
            )

        validated_settings = (
            validate_settings_file(settings_file, self.provider_name, effective_model)
            if settings_file
            else None
        )

        logger.debug(
            "Invoking Claude SDK: model=%s, timeout=%ds, prompt_len=%d",
            effective_model,
            timeout,
            len(prompt),
        )

        start_time = time.monotonic()

        try:
            from bmad_assist_lite.core.async_utils import run_async_in_thread

            response_text = run_async_in_thread(
                asyncio.wait_for(
                    self._invoke_async_with_collector(
                        prompt,
                        effective_model,
                        validated_settings,
                        cwd,
                        collector,
                        allowed_tools,
                        effort,
                    ),
                    timeout=timeout,
                )
            )
        except CLINotFoundError as e:
            raise ProviderError("Claude Code not found. Is 'claude' installed and in PATH?") from e
        except ProcessError as e:
            exit_code = e.exit_code if e.exit_code is not None else 1
            stderr = e.stderr or ""
            raise ProviderError(
                f"Claude SDK failed with exit code {exit_code}: {stderr[:200]}"
            ) from e
        except (TimeoutError, ProviderError):
            raise
        except Exception as e:
            raise ProviderError(f"Unexpected SDK error: {e}") from e

        duration_ms = int((time.monotonic() - start_time) * 1000)
        # Extracted once, when the envelope arrived. None here means the stream
        # carried no result envelope, which is unavailable — not zero.
        metrics = collector.metrics or CallMetrics()

        logger.info(
            "Claude SDK completed: duration=%dms, response_len=%d%s",
            duration_ms,
            len(response_text),
            _format_metric_suffix(metrics),
        )

        return ProviderResult(
            stdout=response_text,
            stderr="",
            exit_code=0,
            duration_ms=duration_ms,
            model=effective_model,
            command=(self.provider_name, model or "default"),
            provider_session_id=metrics.session_id,
            api_duration_ms=metrics.api_duration_ms,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            cache_read_tokens=metrics.cache_read_tokens,
            cache_creation_tokens=metrics.cache_creation_tokens,
            total_cost_usd=metrics.total_cost_usd,
        )

    def _cleanup(self) -> None:
        """Terminate orphan claude process if PID is tracked and alive.

        Best-effort cleanup: if PID cannot be determined or termination fails,
        logs a warning but does not raise. The base class wraps this in try/except
        so exceptions from _cleanup() never mask the original error.
        """
        pid = self._current_pid
        self._current_pid = None

        if pid is None:
            logger.debug(
                "No PID tracked for cleanup — claude process may be orphaned. "
                "The SDK does not expose subprocess PIDs directly."
            )
            return

        if not is_pid_alive(pid):
            logger.debug("Tracked PID %d already terminated, no cleanup needed", pid)
            return

        logger.warning("Terminating orphan claude process PID %d", pid)
        success = terminate_process(pid)
        if not success:
            logger.warning("Failed to terminate claude process PID %d — may be orphaned", pid)

    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from provider result."""
        return result.stdout.strip()
