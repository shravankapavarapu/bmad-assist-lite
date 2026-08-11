"""Durable per-phase provider metrics.

The provider layer measures every call — API time, token counts, cost — but a
measurement that is not written down dies with the process. This module is the
one place those numbers become durable, so a run's cost can be read off disk
afterwards instead of inferred from log lines.

Shape of the mechanism
----------------------
``phase_metrics_context()`` marks a phase as open. While it is open, every
provider invocation reports itself through ``record_provider_call()`` — one hook
in ``BaseProvider.invoke()``, which is the template method that all four
providers and the multi-LLM fan-out share. On close, the calls are folded into a
single :class:`PhaseMetricRecord` and appended to a JSON Lines file.

Why one record per phase, aggregated
------------------------------------
A phase is the unit the loop schedules and the unit any comparison is made in.
Master-driven phases make exactly one call, so aggregation is the identity there.
Multi-LLM phases fan out, and for those ``call_count`` says how many calls the
sums cover and ``model`` lists them in call order — a sum whose arity is unknown
is not a measurement.

``None`` versus ``0``
---------------------
Every metric is ``X | None``. ``None`` means "not reported"; ``0`` means "measured
as zero". Collapsing the two silently corrupts any aggregate built from these
records, so a value that cannot be read as a finite number is stored as ``None``
rather than coerced. Summation follows the same rule: a field no call reported
stays ``None``, it does not become ``0``.

Relationship to ``core.state``'s timing API
-------------------------------------------
``state.start_phase_timing`` / ``get_phase_duration_ms`` and the
``phase_started_at`` / ``story_started_at`` fields are dead code (finding F-01)
and are **deliberately left untouched** here. ADR-0003 rejects standing a second
timing path up beside this one: three mechanisms that can disagree is worse than
one that is dead. Wiring or deleting that API is its own decision with its own
record — this module does not pre-empt it, and adds no third mechanism.

Failure posture
---------------
This instrumentation sits on the path every phase takes. It must never be the
reason a multi-hour run ends, so recording never raises and persistence failures
are logged and dropped. Reading is the opposite: a corrupt record is raised,
because a harness that silently skips records measures a run that did not happen.
"""

import json
import logging
import math
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from bmad_assist_lite.core.exceptions import MetricsError

logger = logging.getLogger(__name__)

__all__ = [
    "PHASE_METRIC_FIELDS",
    "PhaseMetricRecord",
    "PhaseMetricsHandle",
    "append_record",
    "cost_since",
    "load_records",
    "phase_metrics_context",
    "record_count",
    "record_provider_call",
]

METRICS_FILENAME: str = "phase-metrics.jsonl"
TEMP_FILE_SUFFIX: str = ".tmp"


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info."""
    return datetime.now(UTC).replace(tzinfo=None)


# ============================================================================
# Record
# ============================================================================


class PhaseMetricRecord(BaseModel):
    """One phase's measurements, as written to disk.

    The field set is closed and every field is a scalar measurement or a short
    identifier. No prompt text, no provider output, no credential ever enters a
    record — the file is safe to commit as measurement evidence.

    Attributes:
        story_id: ``"{epic}.{story}"``, or None outside a story.
        phase: The phase name, matching ``Phase`` enum values.
        model: Models used, comma-separated in call order. Exactly one entry for
            master-driven phases; None when the phase made no provider call.
        timestamp: Naive UTC, recorded when the phase closed.
        duration_ms: Phase wall-clock, measured by the dispatcher.
        api_duration_ms: Provider-reported API time, summed over the phase's calls.
        input_tokens: Uncached prompt tokens. The full prompt is this plus
            ``cache_read_tokens`` plus ``cache_creation_tokens``; for this tool's
            large XML prompts the cached share usually dominates, so this field
            alone systematically under-reports prompt size.
        output_tokens: Completion tokens.
        cache_read_tokens: Prompt tokens served from the provider's prompt cache.
        cache_creation_tokens: Prompt tokens written into that cache.
        total_cost_usd: Provider-reported cost, summed over the phase's calls.
        timed_out: True when any call in the phase timed out. The phase's
            wall-clock is then censored data — a timeout ceiling, not a
            measurement — and the measurement protocol rejects any snapshot
            containing one.
        call_count: Provider calls the sums cover. Zero for non-LLM phases.

    """

    model_config = ConfigDict(frozen=True)

    story_id: str | None = None
    phase: str
    model: str | None = None
    timestamp: datetime
    duration_ms: int
    api_duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    total_cost_usd: float | None = None
    timed_out: bool = False
    call_count: int = 0


PHASE_METRIC_FIELDS: frozenset[str] = frozenset(PhaseMetricRecord.model_fields)
"""The complete, closed field set. A test asserts nothing else can be added."""


# ============================================================================
# Persistence
# ============================================================================


def append_record(record: PhaseMetricRecord, path: str | Path) -> None:
    """Append one record to a JSON Lines file, atomically.

    The whole file is rewritten through a temp file and ``os.replace()`` rather
    than opened in append mode: an interrupted append can leave a half-written
    final line, which makes every record after it unreadable. The rewrite costs
    a file copy per phase — a few dozen short lines per run — and buys the
    guarantee that the file on disk is always a complete set of records.

    Args:
        record: The record to append.
        path: Destination JSON Lines file. Parent directories are created.

    Raises:
        MetricsError: If the record cannot be written.

    """
    path = Path(path).expanduser()
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"

        line = json.dumps(record.model_dump(mode="json"), sort_keys=True)
        temp_path.write_text(existing + line + "\n", encoding="utf-8")
        os.replace(temp_path, path)

    except (OSError, ValueError) as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                logger.debug("Could not remove metrics temp file %s", temp_path)
        raise MetricsError(f"Failed to append phase metrics to {path}: {e}") from e


def load_records(path: str | Path) -> list[PhaseMetricRecord]:
    """Read every record from a JSON Lines metrics file.

    Args:
        path: The metrics file. A missing file reads as no records.

    Returns:
        Records in the order they were written.

    Raises:
        MetricsError: If the file cannot be read or any line is not a valid
            record. Skipping an unreadable line would silently shorten the run
            being measured, which is worse than failing to read it at all.

    """
    path = Path(path).expanduser()
    if not path.exists():
        return []

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise MetricsError(f"Cannot read phase metrics at {path}: {e}") from e

    records: list[PhaseMetricRecord] = []
    for lineno, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(PhaseMetricRecord.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            raise MetricsError(f"Corrupt phase metrics record at {path}:{lineno}: {e}") from e
    return records


def record_count(path: str | Path) -> int:
    """Count records currently on disk, tolerantly.

    Used to mark where a run starts in a file that outlives it. A file that
    cannot be read counts as empty: the caller is establishing a baseline, and
    a baseline it cannot establish must not be allowed to end the run.

    Args:
        path: The metrics file.

    Returns:
        The number of non-blank lines, or 0 if the file is missing or unreadable.

    """
    path = Path(path).expanduser()
    if not path.exists():
        return 0
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning("Cannot read phase metrics at %s to establish a baseline", path)
        return 0
    return sum(1 for line in content.splitlines() if line.strip())


def cost_since(path: str | Path, start_index: int) -> float | None:
    """Total recorded provider spend, in USD, from ``start_index`` onward.

    This is the *enforcement* reader, and its failure posture is deliberately
    the opposite of :func:`load_records`. Reading for measurement must raise on
    a corrupt record, because a harness that silently skips records reports a
    run that did not happen. Reading to enforce a budget must not: a corrupt
    metrics file is not a reason to kill a multi-hour run, and the honest answer
    is "spend is unknown" rather than a number that would be acted on.

    A record that reports no cost contributes nothing rather than being read as
    a claim that the phase was free — but a set of such records still totals
    ``0.0``, not ``None``. ``None`` means *unmeterable*; ``0.0`` means
    *measured as nothing spent*, and the caller must not conflate them.

    Args:
        path: The metrics file.
        start_index: Number of records that predate this run; they are skipped.

    Returns:
        Dollars recorded from ``start_index`` onward, or None if the file cannot
        be read.

    """
    path = Path(path).expanduser()
    if not path.exists():
        return 0.0

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning("Cannot read phase metrics at %s; cost budget not enforced", path)
        return None

    costs: list[float] = []
    lines = [line for line in content.splitlines() if line.strip()]
    for line in lines[start_index:]:
        try:
            value = _as_float(json.loads(line).get("total_cost_usd"))
        except (json.JSONDecodeError, AttributeError, TypeError):
            logger.warning("Corrupt phase metrics record at %s; cost budget not enforced", path)
            return None
        if value is not None:
            costs.append(value)

    return math.fsum(costs)


# ============================================================================
# Collection
# ============================================================================


def _as_int(value: Any) -> int | None:
    """Coerce to a finite int, or None. Never raises."""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _as_float(value: Any) -> float | None:
    """Coerce to a finite float, or None. Never raises.

    Non-finite values are rejected rather than stored: an ``inf`` propagates
    through a sum and destroys an aggregate more thoroughly than the ``0`` this
    module already refuses to substitute for a missing value.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _as_str(value: Any) -> str | None:
    """Coerce to a short identifier string, or None. Never raises."""
    if value is None or not isinstance(value, str):
        return None
    return value or None


class _Call:
    """One provider invocation's contribution to the open phase."""

    __slots__ = (
        "model",
        "api_duration_ms",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "total_cost_usd",
        "timed_out",
    )

    def __init__(
        self,
        *,
        model: str | None,
        api_duration_ms: int | None,
        input_tokens: int | None,
        output_tokens: int | None,
        cache_read_tokens: int | None,
        cache_creation_tokens: int | None,
        total_cost_usd: float | None,
        timed_out: bool,
    ) -> None:
        self.model = model
        self.api_duration_ms = api_duration_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_creation_tokens = cache_creation_tokens
        self.total_cost_usd = total_cost_usd
        self.timed_out = timed_out


class PhaseMetricsHandle:
    """Accumulator for the phase currently open.

    Thread-safe because the multi-LLM phases fan out across a thread pool and
    every worker reports through the same handle. Parallel *stories* run in
    separate processes, so one handle per process is the whole picture.
    """

    def __init__(self, *, story_id: str | None, phase: str) -> None:
        """Open an accumulator for one phase."""
        self._lock = threading.Lock()
        self._story_id = story_id
        self._phase = phase
        self._calls: list[_Call] = []
        self._duration_ms: int | None = None
        self._started = time.perf_counter()

    def set_duration_ms(self, duration_ms: int) -> None:
        """Record the phase wall-clock measured by the caller.

        The dispatcher already measures the phase; taking its number keeps the
        record and the phase's log line in agreement instead of reporting two
        slightly different durations for the same block.
        """
        value = _as_int(duration_ms)
        with self._lock:
            self._duration_ms = value

    def add_call(self, call: _Call) -> None:
        """Record one provider invocation."""
        with self._lock:
            self._calls.append(call)

    def build(self) -> PhaseMetricRecord:
        """Fold the phase's calls into the record to persist."""
        with self._lock:
            calls = list(self._calls)
            duration_ms = self._duration_ms
            if duration_ms is None:
                duration_ms = int((time.perf_counter() - self._started) * 1000)
            story_id = self._story_id
            phase = self._phase

        models: list[str] = []
        for call in calls:
            if call.model and call.model not in models:
                models.append(call.model)

        return PhaseMetricRecord(
            story_id=story_id,
            phase=phase,
            model=",".join(models) if models else None,
            timestamp=_utc_now(),
            duration_ms=duration_ms,
            api_duration_ms=_sum_int(call.api_duration_ms for call in calls),
            input_tokens=_sum_int(call.input_tokens for call in calls),
            output_tokens=_sum_int(call.output_tokens for call in calls),
            cache_read_tokens=_sum_int(call.cache_read_tokens for call in calls),
            cache_creation_tokens=_sum_int(call.cache_creation_tokens for call in calls),
            total_cost_usd=_sum_float(call.total_cost_usd for call in calls),
            timed_out=any(call.timed_out for call in calls),
            call_count=len(calls),
        )


def _sum_int(values: Iterator[int | None]) -> int | None:
    """Sum the reported values, or None when nothing was reported."""
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _sum_float(values: Iterator[float | None]) -> float | None:
    """Sum the reported values, or None when nothing was reported."""
    present = [v for v in values if v is not None]
    return math.fsum(present) if present else None


_ACTIVE_LOCK = threading.Lock()
_ACTIVE: PhaseMetricsHandle | None = None


def record_provider_call(
    *,
    model: Any = None,
    duration_ms: Any = None,
    api_duration_ms: Any = None,
    input_tokens: Any = None,
    output_tokens: Any = None,
    cache_read_tokens: Any = None,
    cache_creation_tokens: Any = None,
    total_cost_usd: Any = None,
    timed_out: bool = False,
) -> None:
    """Report one provider invocation to the open phase, if any.

    A no-op when no phase is open, which is the case for provider calls the loop
    does not schedule (merge-conflict resolution, ad-hoc tooling). Arguments are
    typed ``Any`` and coerced defensively: this runs inside every invocation, so
    a provider handing over an unexpected shape must degrade to ``None``, not
    raise. ``duration_ms`` is accepted for symmetry with the provider result and
    is not stored per call — the phase's wall-clock is the durable figure.

    Args:
        model: Model identifier for the call.
        duration_ms: Call wall-clock, accepted and not persisted per call.
        api_duration_ms: Provider-reported API time for the call.
        input_tokens: Uncached prompt tokens.
        output_tokens: Completion tokens.
        cache_read_tokens: Prompt tokens served from cache.
        cache_creation_tokens: Prompt tokens written to cache.
        total_cost_usd: Provider-reported cost in USD.
        timed_out: True when this call timed out.

    """
    try:
        with _ACTIVE_LOCK:
            handle = _ACTIVE
        if handle is None:
            return

        handle.add_call(
            _Call(
                model=_as_str(model),
                api_duration_ms=_as_int(api_duration_ms),
                input_tokens=_as_int(input_tokens),
                output_tokens=_as_int(output_tokens),
                cache_read_tokens=_as_int(cache_read_tokens),
                cache_creation_tokens=_as_int(cache_creation_tokens),
                total_cost_usd=_as_float(total_cost_usd),
                timed_out=bool(timed_out),
            )
        )
    except Exception:
        logger.warning("Failed to record provider call metrics", exc_info=True)


def _default_metrics_path() -> Path | None:
    """Resolve the metrics file from the paths singleton, or None if unavailable."""
    try:
        from bmad_assist_lite.core.paths import get_paths

        return get_paths().phase_metrics_file
    except Exception:
        logger.debug("Paths not initialised; phase metrics will not be persisted")
        return None


@contextmanager
def phase_metrics_context(
    *,
    story_id: str | None,
    phase: str,
    path: str | Path | None = None,
) -> Iterator[PhaseMetricsHandle]:
    """Open a phase for metric collection and persist its record on exit.

    The record is written whether the phase succeeded, failed or raised — a
    phase that blew up after twenty minutes still spent twenty minutes, and a
    measurement set that silently omits the expensive failures is misleading in
    exactly the direction that flatters the tool.

    Args:
        story_id: Story the phase belongs to, or None.
        phase: Phase name.
        path: Destination file. Defaults to the paths singleton's metrics file;
            when that is unavailable, collection still happens and persistence
            is skipped.

    Yields:
        The handle, so the caller can supply the phase wall-clock it measured.

    """
    global _ACTIVE

    handle = PhaseMetricsHandle(story_id=story_id, phase=phase)
    with _ACTIVE_LOCK:
        previous = _ACTIVE
        _ACTIVE = handle

    try:
        yield handle
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE = previous

        try:
            destination = Path(path) if path is not None else _default_metrics_path()
            if destination is not None:
                append_record(handle.build(), destination)
        except Exception:
            # Losing a measurement must never end a run.
            logger.warning("Failed to persist phase metrics for %s", phase, exc_info=True)
