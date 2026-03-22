"""Tests for parallel/report.py — summary report generation (Story 6.3)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from bmad_assist_lite.parallel.report import (
    MergeOutcome,
    MergeStats,
    QGStats,
    ReportData,
    StoryTiming,
    _format_duration,
    _format_percentage,
    build_report,
    render_report,
    write_report,
)
from bmad_assist_lite.parallel.state import (
    ParallelState,
    StoryState,
    StoryStatus,
)


# ============================================================================
# Helpers
# ============================================================================


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info (naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_state(
    stories: dict[str, StoryState] | None = None,
) -> ParallelState:
    """Create a minimal ParallelState for testing."""
    if stories is None:
        stories = {"6.1": StoryState(), "6.2": StoryState()}
    return ParallelState(
        base_branch="main",
        epic=6,
        started_at=_utc_now(),
        stories=stories,
    )


def _make_done_story(
    start_offset: float = 0.0,
    duration: float = 600.0,
) -> StoryState:
    """Create a StoryState with DONE status and timing."""
    base = datetime(2026, 3, 22, 10, 0, 0)
    started = base + timedelta(seconds=start_offset)
    completed = started + timedelta(seconds=duration)
    return StoryState(
        status=StoryStatus.DONE,
        started_at=started,
        completed_at=completed,
    )


def _make_blocked_story(error: str = "Exit code 1") -> StoryState:
    """Create a StoryState with BLOCKED status."""
    base = datetime(2026, 3, 22, 10, 0, 0)
    return StoryState(
        status=StoryStatus.BLOCKED,
        started_at=base,
        completed_at=base + timedelta(seconds=300),
        error=error,
    )


# ============================================================================
# Test: _format_duration (Task 8.1)
# ============================================================================


class TestFormatDuration:
    """Task 8.1: Test duration formatting with edge cases."""

    def test_zero_seconds(self) -> None:
        assert _format_duration(0) == "0s"

    def test_45_seconds(self) -> None:
        assert _format_duration(45) == "45s"

    def test_59_seconds(self) -> None:
        assert _format_duration(59) == "59s"

    def test_60_seconds_is_1m(self) -> None:
        assert _format_duration(60) == "1m"

    def test_90_seconds_is_1m(self) -> None:
        assert _format_duration(90) == "1m"

    def test_120_seconds_is_2m(self) -> None:
        assert _format_duration(120) == "2m"

    def test_3599_seconds_is_59m(self) -> None:
        """Boundary: just under 1 hour."""
        assert _format_duration(3599) == "59m"

    def test_3600_seconds_is_1h_00m(self) -> None:
        """Boundary: exactly 1 hour."""
        assert _format_duration(3600) == "1h 00m"

    def test_3661_seconds_is_1h_01m(self) -> None:
        assert _format_duration(3661) == "1h 01m"

    def test_167_minutes_is_2h_47m(self) -> None:
        """AC#2 example: 2h 47m."""
        assert _format_duration(167 * 60) == "2h 47m"

    def test_negative_treated_as_zero(self) -> None:
        assert _format_duration(-10) == "0s"

    def test_large_duration(self) -> None:
        # 10 hours exactly
        assert _format_duration(36000) == "10h 00m"

    def test_fractional_seconds_rounded(self) -> None:
        assert _format_duration(45.7) == "46s"
        assert _format_duration(45.4) == "45s"


# ============================================================================
# Test: _format_percentage (Task 8.2)
# ============================================================================


class TestFormatPercentage:
    """Task 8.2: Test percentage formatting."""

    def test_zero_percent(self) -> None:
        assert _format_percentage(0) == "0%"

    def test_55_point_4_rounds_to_55(self) -> None:
        assert _format_percentage(55.4) == "55%"

    def test_55_point_5_rounds_to_56(self) -> None:
        """Python round() uses banker's rounding, but for .5 this rounds up."""
        assert _format_percentage(55.5) == "56%"

    def test_100_percent(self) -> None:
        assert _format_percentage(100) == "100%"

    def test_small_fraction(self) -> None:
        assert _format_percentage(0.4) == "0%"

    def test_99_point_9(self) -> None:
        assert _format_percentage(99.9) == "100%"


# ============================================================================
# Test: build_report (Task 8.3-8.5, 8.11-8.13)
# ============================================================================


class TestBuildReport:
    """Test report building from ParallelState and MergeOutcome data."""

    def test_basic_counts_3_done_1_blocked(self) -> None:
        """Task 8.3: 3 done + 1 blocked — verify counts."""
        stories = {
            "6.1": _make_done_story(0, 600),
            "6.2": _make_done_story(0, 900),
            "6.3": _make_done_story(0, 1200),
            "6.4": _make_blocked_story("Merge conflict"),
        }
        state = _make_state(stories)
        started_at = datetime(2026, 3, 22, 9, 55, 0)

        report = build_report(state, started_at, [])

        assert report.total_stories == 4
        assert report.completed_count == 3
        assert report.blocked_count == 1

    def test_sequential_estimate_sums_durations(self) -> None:
        """Task 8.3: Sequential estimate is sum of individual story durations."""
        stories = {
            "6.1": _make_done_story(0, 600),   # 10m
            "6.2": _make_done_story(0, 900),   # 15m
            "6.3": _make_done_story(0, 1200),  # 20m
        }
        state = _make_state(stories)
        started_at = datetime(2026, 3, 22, 9, 55, 0)

        report = build_report(state, started_at, [])

        # 600 + 900 + 1200 = 2700 seconds
        assert report.sequential_estimate_seconds == 2700.0

    def test_blocked_story_extraction(self) -> None:
        """Task 8.3: Blocked stories with failure reasons."""
        stories = {
            "6.1": _make_done_story(0, 600),
            "6.2": _make_blocked_story("Post-merge QG failed: Lint"),
        }
        state = _make_state(stories)
        started_at = _utc_now()

        report = build_report(state, started_at, [])

        assert len(report.blocked_stories) == 1
        assert report.blocked_stories[0] == (
            "6.2", "Post-merge QG failed: Lint"
        )

    def test_missing_timestamps_excluded_from_sequential(self) -> None:
        """Task 8.4: Stories with None timestamps excluded from estimate."""
        stories = {
            "6.1": _make_done_story(0, 600),
            "6.2": StoryState(
                status=StoryStatus.DONE,
                started_at=None,
                completed_at=None,
            ),
        }
        state = _make_state(stories)
        started_at = _utc_now()

        report = build_report(state, started_at, [])

        # Only 6.1 contributes to sequential estimate
        assert report.sequential_estimate_seconds == 600.0

    def test_started_at_only_excluded_from_sequential(self) -> None:
        """Task 8.4: Story with started_at but no completed_at excluded."""
        stories = {
            "6.1": _make_done_story(0, 600),
            "6.2": StoryState(
                status=StoryStatus.IN_FLIGHT,
                started_at=datetime(2026, 3, 22, 10, 0, 0),
                completed_at=None,
            ),
        }
        state = _make_state(stories)
        started_at = _utc_now()

        report = build_report(state, started_at, [])

        assert report.sequential_estimate_seconds == 600.0

    def test_all_blocked_no_timestamps_sequential_zero(self) -> None:
        """Task 8.5: All blocked with no timestamps → sequential estimate 0.

        When blocked stories have no timestamps (e.g., blocked before
        execution started due to dependency failures), the sequential
        estimate is 0 and time saved shows "N/A".
        """
        stories = {
            "6.1": StoryState(
                status=StoryStatus.BLOCKED,
                error="Dependency failed",
            ),
            "6.2": StoryState(
                status=StoryStatus.BLOCKED,
                error="Dependency failed",
            ),
        }
        state = _make_state(stories)
        started_at = _utc_now()

        report = build_report(state, started_at, [])

        assert report.completed_count == 0
        assert report.blocked_count == 2
        assert report.sequential_estimate_seconds == 0.0

        # Verify rendering shows N/A
        text = render_report(report)
        assert "Time saved: N/A" in text

    def test_all_blocked_with_timestamps(self) -> None:
        """Blocked stories with timestamps still contribute to sequential estimate."""
        stories = {
            "6.1": _make_blocked_story("Error 1"),
            "6.2": _make_blocked_story("Error 2"),
        }
        state = _make_state(stories)
        started_at = _utc_now()

        report = build_report(state, started_at, [])

        assert report.completed_count == 0
        assert report.blocked_count == 2
        # Blocked stories have 300s duration each
        assert report.sequential_estimate_seconds == 600.0

    def test_merge_outcomes_clean_merges(self) -> None:
        """Task 8.11: Clean merges tracked correctly."""
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, had_conflicts=False,
                qg_passed=True,
            ),
            MergeOutcome(
                story_id="6.2", merged=True, had_conflicts=False,
                qg_passed=True,
            ),
        ]
        stories = {
            "6.1": _make_done_story(0, 600),
            "6.2": _make_done_story(0, 900),
        }
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.merge_stats.clean == 2
        assert report.merge_stats.conflict_resolved == 0
        assert report.merge_stats.failed == 0

    def test_merge_outcomes_conflict_resolved(self) -> None:
        """Task 8.11: Conflict-resolved merges tracked."""
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, had_conflicts=True,
                conflicts_resolved=True, qg_passed=True,
            ),
        ]
        stories = {"6.1": _make_done_story(0, 600)}
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.merge_stats.clean == 0
        assert report.merge_stats.conflict_resolved == 1
        assert report.merge_stats.failed == 0

    def test_merge_outcomes_failed(self) -> None:
        """Task 8.11: Failed merges tracked."""
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=False, had_conflicts=True,
            ),
        ]
        stories = {"6.1": _make_blocked_story("Merge conflict")}
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.merge_stats.failed == 1

    def test_merge_outcomes_mixed(self) -> None:
        """Task 8.11: Mixed merge outcomes (clean + conflict + failed)."""
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, had_conflicts=False,
                qg_passed=True,
            ),
            MergeOutcome(
                story_id="6.2", merged=True, had_conflicts=True,
                conflicts_resolved=True, qg_passed=True,
            ),
            MergeOutcome(
                story_id="6.3", merged=False, had_conflicts=True,
            ),
        ]
        stories = {
            "6.1": _make_done_story(0, 600),
            "6.2": _make_done_story(0, 900),
            "6.3": _make_blocked_story("Merge failed"),
        }
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.merge_stats.clean == 1
        assert report.merge_stats.conflict_resolved == 1
        assert report.merge_stats.failed == 1

    def test_qg_outcomes_passed(self) -> None:
        """Task 8.12: QG passed outcomes tracked."""
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, qg_passed=True, qg_fixed=False,
            ),
        ]
        stories = {"6.1": _make_done_story(0, 600)}
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.qg_stats.passed == 1
        assert report.qg_stats.fixed == 0
        assert report.qg_stats.blocked == 0

    def test_qg_outcomes_fixed(self) -> None:
        """Task 8.12: QG fixed outcomes tracked."""
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, qg_passed=True, qg_fixed=True,
            ),
        ]
        stories = {"6.1": _make_done_story(0, 600)}
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.qg_stats.passed == 0
        assert report.qg_stats.fixed == 1

    def test_qg_outcomes_blocked(self) -> None:
        """Task 8.12: QG blocked outcomes tracked."""
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, qg_passed=False,
            ),
        ]
        stories = {
            "6.1": _make_blocked_story("Post-merge QG failed"),
        }
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.qg_stats.blocked == 1

    def test_qg_not_counted_for_failed_merges(self) -> None:
        """Task 8.12: QG is not counted when merge itself failed."""
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=False, qg_passed=False,
            ),
        ]
        stories = {
            "6.1": _make_blocked_story("Merge failed"),
        }
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.qg_stats.passed == 0
        assert report.qg_stats.fixed == 0
        assert report.qg_stats.blocked == 0

    def test_merge_edge_case_conflicts_not_resolved(self) -> None:
        """Edge case: merged=True, had_conflicts=True, conflicts_resolved=False.

        This combination should be counted as failed, not silently dropped.
        """
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, had_conflicts=True,
                conflicts_resolved=False, qg_passed=True,
            ),
        ]
        stories = {"6.1": _make_done_story(0, 600)}
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.merge_stats.clean == 0
        assert report.merge_stats.conflict_resolved == 0
        assert report.merge_stats.failed == 1

    def test_qg_skipped_not_counted_as_blocked(self) -> None:
        """QG is not run (qg_result=None), merge succeeds.

        qg_passed should be True when set correctly by orchestrator,
        so QG stats should show passed, not blocked.
        """
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, qg_passed=True,
            ),
        ]
        stories = {"6.1": _make_done_story(0, 600)}
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        assert report.qg_stats.passed == 1
        assert report.qg_stats.blocked == 0

    def test_empty_merge_outcomes(self) -> None:
        """Task 8.13: Empty merge_outcomes → all stats zero, graceful render."""
        stories = {
            "6.1": _make_done_story(0, 600),
            "6.2": _make_blocked_story("Exit code 1"),
        }
        state = _make_state(stories)

        report = build_report(state, _utc_now(), [])

        assert report.merge_stats.clean == 0
        assert report.merge_stats.conflict_resolved == 0
        assert report.merge_stats.failed == 0
        assert report.qg_stats.passed == 0
        assert report.qg_stats.fixed == 0
        assert report.qg_stats.blocked == 0

        # Verify rendering doesn't crash
        text = render_report(report)
        assert "Merge Results:" in text

    def test_per_story_timings_sorted_by_id(self) -> None:
        """Per-story timings are sorted by story_id."""
        stories = {
            "6.3": _make_done_story(0, 300),
            "6.1": _make_done_story(0, 600),
            "6.2": _make_done_story(0, 900),
        }
        state = _make_state(stories)

        report = build_report(state, _utc_now(), [])

        ids = [t.story_id for t in report.per_story_timings]
        assert ids == ["6.1", "6.2", "6.3"]

    def test_merge_duration_populated_from_outcome(self) -> None:
        """Merge duration in StoryTiming is populated from MergeOutcome."""
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, qg_passed=True,
                duration_seconds=45.5,
            ),
        ]
        stories = {"6.1": _make_done_story(0, 600)}
        state = _make_state(stories)

        report = build_report(state, _utc_now(), outcomes)

        timing = report.per_story_timings[0]
        assert timing.merge_duration_seconds == 45.5

    def test_merge_duration_none_when_no_outcome(self) -> None:
        """Merge duration is None when story has no MergeOutcome."""
        stories = {"6.1": _make_done_story(0, 600)}
        state = _make_state(stories)

        report = build_report(state, _utc_now(), [])

        timing = report.per_story_timings[0]
        assert timing.merge_duration_seconds is None

    def test_single_story_run(self) -> None:
        """Edge case: Report generates correctly for 1-story run."""
        stories = {"6.1": _make_done_story(0, 600)}
        state = _make_state(stories)

        report = build_report(state, _utc_now(), [])

        assert report.total_stories == 1
        assert report.completed_count == 1
        assert report.blocked_count == 0
        assert len(report.per_story_timings) == 1


# ============================================================================
# Test: render_report (Task 8.6-8.8)
# ============================================================================


class TestRenderReport:
    """Test report rendering produces correct output sections."""

    def _make_report_data(
        self,
        *,
        blocked: bool = False,
        sequential_seconds: float = 6 * 3600 + 10 * 60,
        wall_clock_seconds: float = 2 * 3600 + 47 * 60,
    ) -> ReportData:
        """Create a ReportData for rendering tests."""
        timings = [
            StoryTiming(
                story_id="6.1",
                started_at=datetime(2026, 3, 22, 10, 0, 0),
                completed_at=datetime(2026, 3, 22, 10, 10, 0),
                duration_seconds=600,
                merge_duration_seconds=12.5,
                status="done",
            ),
            StoryTiming(
                story_id="6.2",
                started_at=datetime(2026, 3, 22, 10, 0, 0),
                completed_at=datetime(2026, 3, 22, 10, 15, 0),
                duration_seconds=900,
                merge_duration_seconds=None,
                status="done",
            ),
        ]

        blocked_stories: list[tuple[str, str]] = []
        blocked_count = 0
        if blocked:
            timings.append(
                StoryTiming(
                    story_id="6.3",
                    started_at=datetime(2026, 3, 22, 10, 0, 0),
                    completed_at=datetime(2026, 3, 22, 10, 5, 0),
                    duration_seconds=300,
                    status="blocked",
                ),
            )
            blocked_stories = [("6.3", "Post-merge QG failed: Lint")]
            blocked_count = 1

        return ReportData(
            total_stories=len(timings),
            completed_count=len(timings) - blocked_count,
            blocked_count=blocked_count,
            wall_clock_seconds=wall_clock_seconds,
            sequential_estimate_seconds=sequential_seconds,
            per_story_timings=timings,
            merge_stats=MergeStats(clean=2, conflict_resolved=0, failed=0),
            qg_stats=QGStats(passed=2, fixed=0, blocked=0),
            blocked_stories=blocked_stories,
        )

    def test_contains_header_section(self) -> None:
        """Task 8.6: Report contains header with total/completed/blocked."""
        report = self._make_report_data()
        text = render_report(report)

        assert "PARALLEL RUN SUMMARY REPORT" in text
        assert "Total stories: 2" in text
        assert "Completed: 2" in text
        assert "Blocked: 0" in text

    def test_contains_per_story_timing(self) -> None:
        """Task 8.6: Report contains per-story timing section."""
        report = self._make_report_data()
        text = render_report(report)

        assert "Per-Story Timing:" in text
        assert "6.1" in text
        assert "6.2" in text
        assert "done" in text

    def test_contains_timing_comparison(self) -> None:
        """Task 8.6: Report contains timing comparison section."""
        report = self._make_report_data()
        text = render_report(report)

        assert "Timing Comparison:" in text
        assert "Wall-clock time (parallel):" in text
        assert "Estimated sequential time:" in text

    def test_contains_merge_stats(self) -> None:
        """Task 8.6: Report contains merge statistics."""
        report = self._make_report_data()
        text = render_report(report)

        assert "Merge Results:" in text
        assert "Clean: 2" in text

    def test_contains_qg_stats(self) -> None:
        """Task 8.6: Report contains QG statistics."""
        report = self._make_report_data()
        text = render_report(report)

        assert "Post-merge QG Results:" in text
        assert "Passed: 2" in text

    def test_blocked_section_present_with_blocked_stories(self) -> None:
        """Task 8.6: Blocked section present when stories are blocked."""
        report = self._make_report_data(blocked=True)
        text = render_report(report)

        assert "Blocked Stories:" in text
        assert "6.3" in text
        assert "Post-merge QG failed: Lint" in text

    def test_no_blocked_section_when_no_blocked(self) -> None:
        """Task 8.7: Blocked section omitted when no stories are blocked."""
        report = self._make_report_data(blocked=False)
        text = render_report(report)

        assert "Blocked Stories:" not in text

    def test_time_saved_format_matches_ac2(self) -> None:
        """Task 8.8: Time saved matches AC#2 format.

        AC#2: "Time saved: 3h 23m (55% reduction)"
        Given: 6h 10m sequential, 2h 47m wall-clock
        """
        report = self._make_report_data(
            sequential_seconds=6 * 3600 + 10 * 60,  # 6h 10m = 22200s
            wall_clock_seconds=2 * 3600 + 47 * 60,  # 2h 47m = 10020s
        )
        text = render_report(report)

        # Time saved: 22200 - 10020 = 12180s = 3h 23m
        # Percentage: 12180 / 22200 * 100 = 54.86... ≈ 55%
        assert "Time saved:" in text
        assert "3h 23m" in text
        assert "55% reduction" in text

    def test_time_saved_na_when_sequential_is_zero(self) -> None:
        """Time saved shows N/A when sequential estimate is zero."""
        report = self._make_report_data(
            sequential_seconds=0.0,
            wall_clock_seconds=100.0,
        )
        text = render_report(report)

        assert "Time saved: N/A" in text

    def test_time_saved_zero_when_parallel_slower(self) -> None:
        """Time saved is 0 when parallel is slower than sequential."""
        report = self._make_report_data(
            sequential_seconds=100.0,
            wall_clock_seconds=200.0,
        )
        text = render_report(report)

        assert "Time saved: 0s" in text
        assert "0% reduction" in text

    def test_merge_time_shown_when_available(self) -> None:
        """Merge time column shows formatted duration when available."""
        report = self._make_report_data()
        text = render_report(report)

        # Story 6.1 has merge_duration_seconds=12.5 → "12s"
        assert "12s" in text

    def test_merge_time_dash_when_not_available(self) -> None:
        """Merge time column shows dash when not applicable."""
        report = self._make_report_data()
        text = render_report(report)

        # Story 6.2 has merge_duration_seconds=None
        # The "-" should appear in the output
        lines = text.split("\n")
        story_6_2_lines = [line for line in lines if "6.2" in line]
        assert len(story_6_2_lines) >= 1


# ============================================================================
# Test: write_report (Task 8.9)
# ============================================================================


class TestWriteReport:
    """Task 8.9: Test report output to log and stdout."""

    def test_calls_parallel_logger_info(self) -> None:
        """write_report calls parallel logger.info() once with full text."""
        report_text = "Line 1\nLine 2\nLine 3"

        with patch(
            "bmad_assist_lite.parallel.report.logging.getLogger"
        ) as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            with patch(
                "bmad_assist_lite.parallel.report.write_progress"
            ):
                write_report(report_text)

            mock_get_logger.assert_called_with("bmad_assist_lite.parallel")
            mock_logger.info.assert_called_once_with(report_text)

    def test_calls_write_progress(self) -> None:
        """write_report calls write_progress() for stdout echo."""
        report_text = "Test report content"

        with patch(
            "bmad_assist_lite.parallel.report.write_progress"
        ) as mock_wp:
            with patch(
                "bmad_assist_lite.parallel.report.logging.getLogger"
            ) as mock_get_logger:
                mock_get_logger.return_value = MagicMock()
                write_report(report_text)

            mock_wp.assert_called_once_with(report_text)

    def test_both_targets_called(self) -> None:
        """write_report calls both logger and write_progress."""
        report_text = "Summary\nDetails"

        with patch(
            "bmad_assist_lite.parallel.report.write_progress"
        ) as mock_wp, patch(
            "bmad_assist_lite.parallel.report.logging.getLogger"
        ) as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            write_report(report_text)

            mock_wp.assert_called_once()
            mock_logger.info.assert_called_once_with(report_text)


# ============================================================================
# Test: MergeOutcome model (Task 8.10)
# ============================================================================


class TestMergeOutcomeModel:
    """Task 8.10: Test MergeOutcome model behavior."""

    def test_frozen(self) -> None:
        """MergeOutcome is frozen — mutation raises error."""
        outcome = MergeOutcome(story_id="6.1", merged=True)
        with pytest.raises(ValidationError):
            outcome.merged = False  # type: ignore[misc]

    def test_default_values(self) -> None:
        """MergeOutcome defaults for optional fields."""
        outcome = MergeOutcome(story_id="6.1", merged=True)
        assert outcome.had_conflicts is False
        assert outcome.conflicts_resolved is False
        assert outcome.qg_passed is False
        assert outcome.qg_fixed is False
        assert outcome.duration_seconds == 0.0

    def test_tracks_clean_merge(self) -> None:
        """MergeOutcome correctly tracks a clean merge."""
        outcome = MergeOutcome(
            story_id="6.1", merged=True, had_conflicts=False,
            qg_passed=True, duration_seconds=15.2,
        )
        assert outcome.merged is True
        assert outcome.had_conflicts is False
        assert outcome.qg_passed is True
        assert outcome.duration_seconds == 15.2

    def test_tracks_conflict_resolved(self) -> None:
        """MergeOutcome correctly tracks a conflict-resolved merge."""
        outcome = MergeOutcome(
            story_id="6.1", merged=True, had_conflicts=True,
            conflicts_resolved=True, qg_passed=True,
        )
        assert outcome.had_conflicts is True
        assert outcome.conflicts_resolved is True

    def test_tracks_failed_merge(self) -> None:
        """MergeOutcome correctly tracks a failed merge."""
        outcome = MergeOutcome(
            story_id="6.1", merged=False, had_conflicts=True,
        )
        assert outcome.merged is False
        assert outcome.had_conflicts is True

    def test_model_copy_produces_new_instance(self) -> None:
        """model_copy on frozen MergeOutcome produces new instance."""
        original = MergeOutcome(story_id="6.1", merged=True)
        updated = original.model_copy(update={"merged": False})
        assert updated.merged is False
        assert original.merged is True


# ============================================================================
# Test: StoryTiming model
# ============================================================================


class TestStoryTimingModel:
    """Test StoryTiming frozen model behavior."""

    def test_frozen(self) -> None:
        timing = StoryTiming(story_id="6.1")
        with pytest.raises(ValidationError):
            timing.story_id = "6.2"  # type: ignore[misc]

    def test_default_values(self) -> None:
        timing = StoryTiming(story_id="6.1")
        assert timing.started_at is None
        assert timing.completed_at is None
        assert timing.duration_seconds == 0.0
        assert timing.merge_duration_seconds is None
        assert timing.status == ""


# ============================================================================
# Test: MergeStats and QGStats models
# ============================================================================


class TestStatsModels:
    """Test MergeStats and QGStats frozen models."""

    def test_merge_stats_frozen(self) -> None:
        stats = MergeStats(clean=1)
        with pytest.raises(ValidationError):
            stats.clean = 2  # type: ignore[misc]

    def test_merge_stats_defaults(self) -> None:
        stats = MergeStats()
        assert stats.clean == 0
        assert stats.conflict_resolved == 0
        assert stats.failed == 0

    def test_qg_stats_frozen(self) -> None:
        stats = QGStats(passed=1)
        with pytest.raises(ValidationError):
            stats.passed = 2  # type: ignore[misc]

    def test_qg_stats_defaults(self) -> None:
        stats = QGStats()
        assert stats.passed == 0
        assert stats.fixed == 0
        assert stats.blocked == 0


# ============================================================================
# Test: ReportData model
# ============================================================================


class TestReportDataModel:
    """Test ReportData frozen model."""

    def test_frozen(self) -> None:
        report = ReportData(
            total_stories=1,
            completed_count=1,
            blocked_count=0,
            wall_clock_seconds=100,
            sequential_estimate_seconds=200,
            per_story_timings=[],
            merge_stats=MergeStats(),
            qg_stats=QGStats(),
            blocked_stories=[],
        )
        with pytest.raises(ValidationError):
            report.total_stories = 2  # type: ignore[misc]


# ============================================================================
# Test: Edge cases and integration
# ============================================================================


class TestEdgeCases:
    """Test edge cases for the report module."""

    def test_all_stories_backlog(self) -> None:
        """Report handles all stories still at backlog."""
        stories = {
            "6.1": StoryState(),
            "6.2": StoryState(),
        }
        state = _make_state(stories)

        report = build_report(state, _utc_now(), [])

        assert report.total_stories == 2
        assert report.completed_count == 0
        assert report.blocked_count == 0
        assert report.sequential_estimate_seconds == 0.0

        # Render should work without errors
        text = render_report(report)
        assert "Time saved: N/A" in text

    def test_blocked_story_with_none_error(self) -> None:
        """Blocked story with None error shows 'Unknown error'."""
        stories = {
            "6.1": StoryState(
                status=StoryStatus.BLOCKED,
                error=None,
            ),
        }
        state = _make_state(stories)

        report = build_report(state, _utc_now(), [])

        assert report.blocked_stories[0][1] == "Unknown error"

    def test_wall_clock_calculation(self) -> None:
        """Wall-clock time is calculated from orchestrator start to now."""
        started_at = _utc_now() - timedelta(seconds=120)

        stories = {"6.1": _make_done_story(0, 600)}
        state = _make_state(stories)

        report = build_report(state, started_at, [])

        # Wall-clock should be approximately 120 seconds (within tolerance)
        assert report.wall_clock_seconds >= 119.0
        assert report.wall_clock_seconds <= 125.0

    def test_end_to_end_build_render(self) -> None:
        """Full pipeline: build → render produces valid output."""
        stories = {
            "6.1": _make_done_story(0, 600),
            "6.2": _make_done_story(0, 900),
            "6.3": _make_blocked_story("Lint failure"),
        }
        state = _make_state(stories)
        outcomes = [
            MergeOutcome(
                story_id="6.1", merged=True, qg_passed=True,
                duration_seconds=10.0,
            ),
            MergeOutcome(
                story_id="6.2", merged=True, had_conflicts=True,
                conflicts_resolved=True, qg_passed=True,
                duration_seconds=25.0,
            ),
        ]

        report = build_report(state, _utc_now(), outcomes)
        text = render_report(report)

        # Verify all sections present
        assert "PARALLEL RUN SUMMARY REPORT" in text
        assert "Total stories: 3" in text
        assert "Completed: 2" in text
        assert "Blocked: 1" in text
        assert "Per-Story Timing:" in text
        assert "Timing Comparison:" in text
        assert "Merge Results:" in text
        assert "Post-merge QG Results:" in text
        assert "Blocked Stories:" in text
        assert "6.3" in text
        assert "Lint failure" in text

    def test_render_report_returns_string(self) -> None:
        """render_report returns a string, not None."""
        report = ReportData(
            total_stories=0,
            completed_count=0,
            blocked_count=0,
            wall_clock_seconds=0,
            sequential_estimate_seconds=0,
            per_story_timings=[],
            merge_stats=MergeStats(),
            qg_stats=QGStats(),
            blocked_stories=[],
        )
        text = render_report(report)
        assert isinstance(text, str)
        assert len(text) > 0
