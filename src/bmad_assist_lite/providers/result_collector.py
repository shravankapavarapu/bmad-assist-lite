"""Thread-safe partial result accumulator for streaming provider responses.

Tracks accumulated text content, per-call metrics, and streaming activity
timestamps to enable timeout logic to capture partial results and distinguish
active-streaming from silent-stall scenarios.
"""

import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallMetrics:
    """Provider-agnostic per-call metrics, recorded as they arrive.

    Every field is ``None`` when unavailable — never ``0``, which would be
    indistinguishable from a real measurement of zero and would silently corrupt
    any aggregate built from these values.

    ``input_tokens`` is the *uncached* prompt remainder. The full prompt size is
    ``input_tokens + cache_read_tokens + cache_creation_tokens``; for this tool's
    large XML prompts the cached share is usually the dominant one, so any
    aggregate built on ``input_tokens`` alone systematically under-reports.
    """

    api_duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    total_cost_usd: float | None = None
    session_id: str | None = None


class ResultCollector:
    """Accumulate streaming text chunks with thread-safe access and activity tracking.

    Each provider invocation creates a new instance. Chunks are appended via add(),
    and the accumulated text is available via the text property. Activity tracking
    via last_chunk_at and is_active() supports timeout decisions in the provider layer.

    Per-call metrics are recorded here too, via record_metrics(). The collector is
    created fresh by BaseProvider.invoke() for every invocation and handed to
    _do_invoke(), which makes it the one channel that carries partial state across
    the timeout boundary: a timed-out call is cancelled before it can build a
    ProviderResult, so anything the provider learned must already be on the
    collector for _handle_timeout() to report it. Storing metrics on the provider
    instance instead would risk a previous call's numbers being reported for a
    later timed-out call, since providers are reused across invocations.

    Thread safety is provided by threading.Lock (not asyncio.Lock) so the collector
    works from both sync and async contexts.
    """

    def __init__(self) -> None:
        """Initialize empty collector with lock, chunk list, counters, and metrics."""
        self._lock = threading.Lock()
        self._chunks: list[str] = []
        self._last_chunk_at: float | None = None
        self._chunk_count: int = 0
        self._metrics: CallMetrics | None = None

    def add(self, chunk: str) -> None:
        """Append a text chunk and update activity timestamp.

        Args:
            chunk: Text content to accumulate. Empty strings are accepted
                and increment the chunk count.

        """
        with self._lock:
            self._chunks.append(chunk)
            self._last_chunk_at = time.monotonic()
            self._chunk_count += 1

    def record_metrics(self, metrics: CallMetrics) -> None:
        """Record per-call metrics as soon as the provider learns them.

        Call this at the moment the metrics arrive, not at the end of the
        invocation: a call that later times out is cancelled before it can build
        its ProviderResult, and only what was recorded here survives.

        Args:
            metrics: The metrics observed for this call. A later call replaces an
                earlier one, so providers that see several metric envelopes should
                record only the one they intend to report.

        """
        with self._lock:
            self._metrics = metrics

    @property
    def metrics(self) -> CallMetrics | None:
        """Return metrics recorded for this call, or None if none were observed.

        None means "the provider never reported metrics", which is distinct from
        a CallMetrics whose fields happen to be None.
        """
        with self._lock:
            return self._metrics

    @property
    def text(self) -> str:
        """Return accumulated text as a single joined string."""
        with self._lock:
            return "".join(self._chunks)

    @property
    def last_chunk_at(self) -> float | None:
        """Return monotonic timestamp of the most recent add() call, or None."""
        with self._lock:
            return self._last_chunk_at

    @property
    def chunk_count(self) -> int:
        """Return the total number of chunks added."""
        with self._lock:
            return self._chunk_count

    @property
    def is_empty(self) -> bool:
        """Return True if no chunks have been added."""
        with self._lock:
            return self._chunk_count == 0

    def is_active(self, threshold_seconds: float = 30.0) -> bool:
        """Check whether streaming activity occurred within the threshold.

        Args:
            threshold_seconds: Maximum seconds since last chunk to consider active.
                Defaults to 30.0. Negative values always return False (no elapsed
                time can be less than a negative threshold with strict < comparison).
                Zero always returns False since any non-negative elapsed time fails
                the strict < 0.0 check.

        Returns:
            True if a chunk was added within threshold_seconds of now.
            False if no chunks exist or the last chunk is older than the threshold.

        """
        with self._lock:
            if self._last_chunk_at is None:
                return False
            return (time.monotonic() - self._last_chunk_at) < threshold_seconds
