"""Tests for ResultCollector thread-safe partial result accumulator.

Covers all acceptance criteria for Story 7.2:
- Basic accumulation (AC #1)
- Timestamp tracking (AC #2)
- Activity detection (AC #3, #4)
- Thread safety (AC #5)
- Empty state (AC #6)
"""

import threading
import time
from unittest.mock import patch

from bmad_assist_lite.providers.result_collector import ResultCollector

# ============================================================================
# TestResultCollectorBasic — Accumulation and Empty State (AC #1, #6)
# ============================================================================


class TestResultCollectorBasic:
    """Test basic accumulation and empty state behavior."""

    def test_accumulation_two_chunks(self) -> None:
        """Add two chunks and verify text and chunk_count (AC #1)."""
        collector = ResultCollector()
        collector.add("hello ")
        collector.add("world")
        assert collector.text == "hello world"
        assert collector.chunk_count == 2

    def test_empty_state(self) -> None:
        """Verify all properties return correct defaults before any add() (AC #6)."""
        collector = ResultCollector()
        assert collector.is_empty is True
        assert collector.last_chunk_at is None
        assert collector.chunk_count == 0
        assert collector.text == ""

    def test_single_chunk(self) -> None:
        """Verify single chunk accumulation works."""
        collector = ResultCollector()
        collector.add("only")
        assert collector.text == "only"
        assert collector.chunk_count == 1
        assert collector.is_empty is False

    def test_empty_string_chunk(self) -> None:
        """Empty string chunk increments count and updates timestamp (Task 3.9)."""
        collector = ResultCollector()
        collector.add("")
        assert collector.chunk_count == 1
        assert collector.last_chunk_at is not None
        assert collector.text == ""
        assert collector.is_empty is False

    def test_large_number_of_chunks(self) -> None:
        """Verify 1000+ chunks accumulate correctly (Task 3.10)."""
        collector = ResultCollector()
        for i in range(1500):
            collector.add(f"chunk{i}")
        assert collector.chunk_count == 1500
        expected = "".join(f"chunk{i}" for i in range(1500))
        assert collector.text == expected


# ============================================================================
# TestResultCollectorTimestamp — Monotonic Timestamp Tracking (AC #2)
# ============================================================================


class TestResultCollectorTimestamp:
    """Test last_chunk_at monotonic timestamp tracking."""

    def test_last_chunk_at_returns_monotonic_timestamp(self) -> None:
        """last_chunk_at is a valid monotonic timestamp after add() (AC #2)."""
        before = time.monotonic()
        collector = ResultCollector()
        collector.add("data")
        after = time.monotonic()
        ts = collector.last_chunk_at
        assert ts is not None
        assert before <= ts <= after

    def test_second_add_updates_timestamp(self) -> None:
        """Second add() produces last_chunk_at >= first add time (AC #2)."""
        collector = ResultCollector()
        collector.add("first")
        first_ts = collector.last_chunk_at
        assert first_ts is not None

        collector.add("second")
        second_ts = collector.last_chunk_at
        assert second_ts is not None
        assert second_ts >= first_ts


# ============================================================================
# TestResultCollectorActivity — is_active() Detection (AC #3, #4)
# ============================================================================


class TestResultCollectorActivity:
    """Test is_active() activity detection with threshold."""

    def test_is_active_true_immediately_after_add(self) -> None:
        """is_active(30.0) returns True immediately after add() (AC #3)."""
        collector = ResultCollector()
        collector.add("recent")
        assert collector.is_active(threshold_seconds=30.0) is True

    def test_is_active_false_when_stale(self) -> None:
        """is_active(30.0) returns False when chunk is 60s old (AC #4)."""
        collector = ResultCollector()
        collector.add("old")
        # Mock time.monotonic to simulate 60 seconds elapsed
        real_ts = collector.last_chunk_at
        assert real_ts is not None
        with patch(
            "bmad_assist_lite.providers.result_collector.time.monotonic",
            return_value=real_ts + 60.0,
        ):
            assert collector.is_active(threshold_seconds=30.0) is False

    def test_is_active_false_when_no_chunks(self) -> None:
        """is_active() returns False when no chunks have been added."""
        collector = ResultCollector()
        assert collector.is_active(threshold_seconds=30.0) is False

    def test_is_active_default_threshold(self) -> None:
        """is_active() called without arguments uses default 30.0 threshold (Task 3.11)."""
        collector = ResultCollector()
        collector.add("chunk")
        # Just added, so default 30s threshold should mean active
        assert collector.is_active() is True

    def test_is_active_zero_threshold_returns_false(self) -> None:
        """is_active(threshold_seconds=0.0) returns False (Task 3.12)."""
        collector = ResultCollector()
        collector.add("chunk")
        # Strict < comparison: any non-negative elapsed time fails elapsed < 0.0
        assert collector.is_active(threshold_seconds=0.0) is False

    def test_is_active_negative_threshold_returns_false(self) -> None:
        """is_active with negative threshold always returns False."""
        collector = ResultCollector()
        collector.add("chunk")
        # Negative threshold: elapsed time can never be < negative value
        assert collector.is_active(threshold_seconds=-1.0) is False

    def test_is_active_true_within_threshold(self) -> None:
        """is_active returns True when chunk is 5s old with 30s threshold (AC #3)."""
        collector = ResultCollector()
        collector.add("data")
        real_ts = collector.last_chunk_at
        assert real_ts is not None
        with patch(
            "bmad_assist_lite.providers.result_collector.time.monotonic",
            return_value=real_ts + 5.0,
        ):
            assert collector.is_active(threshold_seconds=30.0) is True


# ============================================================================
# TestResultCollectorThreadSafety — Concurrent Access (AC #5)
# ============================================================================


class TestResultCollectorThreadSafety:
    """Test thread safety of concurrent add() calls."""

    def test_concurrent_add_no_data_corruption(self) -> None:
        """Concurrent add() from multiple threads captures all chunks (AC #5)."""
        collector = ResultCollector()
        num_threads = 10
        chunks_per_thread = 100
        barrier = threading.Barrier(num_threads, timeout=5.0)

        def worker() -> None:
            barrier.wait(timeout=5.0)
            for _ in range(chunks_per_thread):
                collector.add("x")

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected_count = num_threads * chunks_per_thread
        assert collector.chunk_count == expected_count
        assert collector.text == "x" * expected_count
        assert collector.is_empty is False
        assert collector.last_chunk_at is not None
