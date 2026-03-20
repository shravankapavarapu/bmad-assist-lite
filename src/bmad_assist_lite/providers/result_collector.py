"""Thread-safe partial result accumulator for streaming provider responses.

Tracks accumulated text content and streaming activity timestamps to enable
timeout logic to capture partial results and distinguish active-streaming
from silent-stall scenarios.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class ResultCollector:
    """Accumulate streaming text chunks with thread-safe access and activity tracking.

    Each provider invocation creates a new instance. Chunks are appended via add(),
    and the accumulated text is available via the text property. Activity tracking
    via last_chunk_at and is_active() supports timeout decisions in the provider layer.

    Thread safety is provided by threading.Lock (not asyncio.Lock) so the collector
    works from both sync and async contexts.
    """

    def __init__(self) -> None:
        """Initialize empty collector with lock, chunk list, and counters."""
        self._lock = threading.Lock()
        self._chunks: list[str] = []
        self._last_chunk_at: float | None = None
        self._chunk_count: int = 0

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
