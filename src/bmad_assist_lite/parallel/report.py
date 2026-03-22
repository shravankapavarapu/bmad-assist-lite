"""Summary report generation for parallel story execution.

Builds a human-readable summary report when a parallel run completes,
showing per-story timing, wall-clock vs sequential time comparison,
merge statistics, QG results, and blocked story details.

The report is appended to ``parallel-run.log`` via the ``bmad_assist_lite.parallel``
logger namespace and echoed to stdout via ``write_progress()``.
"""

import logging
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.parallel.state import ParallelState, StoryStatus
from bmad_assist_lite.providers.base import write_progress

logger = logging.getLogger(__name__)


# ============================================================================
# Timestamp helper
# ============================================================================


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info (naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


# ============================================================================
# Data models (Task 1)
# ============================================================================


class StoryTiming(BaseModel):
    """Per-story timing breakdown for the summary report.

    Attributes:
        story_id: The story identifier (e.g. ``"3.2"``).
        started_at: When the story subprocess was spawned.
        completed_at: When the story finished (done or blocked).
        duration_seconds: Elapsed time from start to completion.
        merge_duration_seconds: Time spent in the merge phase, or ``None``
            if the story never reached merging.
        status: Final status string (e.g. ``"done"``, ``"blocked"``).

    """

    model_config = ConfigDict(frozen=True)

    story_id: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float = 0.0
    merge_duration_seconds: float | None = None
    status: str = ""


class MergeStats(BaseModel):
    """Aggregate merge outcome statistics.

    Attributes:
        clean: Number of merges that succeeded without conflicts.
        conflict_resolved: Number of merges where conflicts were resolved.
        failed: Number of merges that failed.

    """

    model_config = ConfigDict(frozen=True)

    clean: int = 0
    conflict_resolved: int = 0
    failed: int = 0


class QGStats(BaseModel):
    """Aggregate post-merge quality gate statistics.

    Attributes:
        passed: Number of stories where QG passed on first run.
        fixed: Number of stories where QG was fixed via retries.
        blocked: Number of stories blocked by QG failure.

    """

    model_config = ConfigDict(frozen=True)

    passed: int = 0
    fixed: int = 0
    blocked: int = 0


class MergeOutcome(BaseModel):
    """Record of a single merge attempt outcome.

    Accumulated by the orchestrator during ``_process_merge_queue()``
    to capture merge/QG details not preserved in ``ParallelState``.

    Attributes:
        story_id: The story identifier.
        merged: Whether the merge itself succeeded.
        had_conflicts: Whether conflicts were detected during merge.
        conflicts_resolved: Whether detected conflicts were resolved.
        qg_passed: Whether post-merge QG passed (first run or after fix).
        qg_fixed: ``True`` only when post-merge QG initially failed but
            was resolved via ``post_merge_fix_retries``. If the retry-fix
            flow is not yet implemented, set ``False`` always -- the field
            is forward-compatible.
        duration_seconds: Merge elapsed time from merge-start to
            merge-complete, used for per-story merge time in the report.

    """

    model_config = ConfigDict(frozen=True)

    story_id: str
    merged: bool
    had_conflicts: bool = False
    conflicts_resolved: bool = False
    qg_passed: bool = False
    qg_fixed: bool = False
    duration_seconds: float = 0.0


class ReportData(BaseModel):
    """Complete data model for the summary report.

    Contains all fields needed to render the human-readable report:
    story counts, per-story timings, wall-clock vs sequential comparison,
    merge and QG statistics, and blocked story details.

    """

    model_config = ConfigDict(frozen=True)

    total_stories: int
    completed_count: int
    blocked_count: int
    wall_clock_seconds: float
    sequential_estimate_seconds: float
    per_story_timings: list[StoryTiming]
    merge_stats: MergeStats
    qg_stats: QGStats
    blocked_stories: list[tuple[str, str]]


# ============================================================================
# Time formatting helpers (Task 3)
# ============================================================================


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string.

    Formatting rules:
    - Under 60 seconds: ``"45s"``
    - 60 to < 3600 seconds: ``"38m"``
    - >= 3600 seconds: ``"2h 47m"``, ``"1h 00m"``, ``"1h 05m"``

    Args:
        seconds: Duration in seconds (non-negative).

    Returns:
        Human-readable duration string.

    """
    if seconds < 0:
        seconds = 0.0

    total_seconds = round(seconds)

    if total_seconds < 60:  # noqa: PLR2004
        return f"{total_seconds}s"

    total_minutes = total_seconds // 60

    if total_seconds < 3600:  # noqa: PLR2004
        return f"{total_minutes}m"

    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes:02d}m"


def _format_percentage(value: float) -> str:
    """Format a float as a rounded integer percentage.

    Args:
        value: Percentage value (e.g. 55.4).

    Returns:
        Formatted string like ``"55%"``.

    """
    return f"{round(value)}%"


# ============================================================================
# Report builder (Task 2)
# ============================================================================


def build_report(
    state: ParallelState,
    orchestrator_started_at: datetime,
    merge_outcomes: list[MergeOutcome],
) -> ReportData:
    """Build report data from parallel state and merge outcomes.

    Extracts timing and status data from ``ParallelState.stories`` and
    the accumulated ``MergeOutcome`` records.

    Args:
        state: The final ``ParallelState`` after all stories completed.
        orchestrator_started_at: When the orchestrator ``run()`` started.
        merge_outcomes: List of ``MergeOutcome`` records accumulated
            during merge queue processing.

    Returns:
        A ``ReportData`` instance with all report fields populated.

    """
    now = _utc_now()

    # Wall-clock time
    wall_clock_seconds = (now - orchestrator_started_at).total_seconds()

    # Build merge outcome lookup by story_id
    merge_outcome_map: dict[str, MergeOutcome] = {
        mo.story_id: mo for mo in merge_outcomes
    }

    # Per-story timings
    per_story_timings: list[StoryTiming] = []
    completed_count = 0
    blocked_count = 0
    sequential_estimate = 0.0
    blocked_stories: list[tuple[str, str]] = []

    for story_id in sorted(state.stories.keys()):
        story = state.stories[story_id]

        duration = 0.0
        if story.started_at is not None and story.completed_at is not None:
            duration = (story.completed_at - story.started_at).total_seconds()
            sequential_estimate += duration

        # Look up merge duration from MergeOutcome
        merge_duration: float | None = None
        mo = merge_outcome_map.get(story_id)
        if mo is not None:
            merge_duration = mo.duration_seconds

        timing = StoryTiming(
            story_id=story_id,
            started_at=story.started_at,
            completed_at=story.completed_at,
            duration_seconds=duration,
            merge_duration_seconds=merge_duration,
            status=story.status.value,
        )
        per_story_timings.append(timing)

        if story.status == StoryStatus.DONE:
            completed_count += 1
        elif story.status == StoryStatus.BLOCKED:
            blocked_count += 1
            reason = story.error or "Unknown error"
            blocked_stories.append((story_id, reason))

    # Merge statistics
    clean = 0
    conflict_resolved = 0
    failed = 0
    for mo in merge_outcomes:
        if not mo.merged:
            failed += 1
        elif mo.had_conflicts and mo.conflicts_resolved:
            conflict_resolved += 1
        elif mo.had_conflicts and not mo.conflicts_resolved:
            # Merged with conflicts but not resolved — treat as failed
            failed += 1
        else:
            # Merged without conflicts — clean
            clean += 1

    merge_stats = MergeStats(
        clean=clean,
        conflict_resolved=conflict_resolved,
        failed=failed,
    )

    # QG statistics
    qg_passed = 0
    qg_fixed = 0
    qg_blocked = 0
    for mo in merge_outcomes:
        if not mo.merged:
            # Merge failed — QG was never run
            continue
        if mo.qg_passed and not mo.qg_fixed:
            qg_passed += 1
        elif mo.qg_passed and mo.qg_fixed:
            qg_fixed += 1
        elif not mo.qg_passed:
            qg_blocked += 1

    qg_stats = QGStats(
        passed=qg_passed,
        fixed=qg_fixed,
        blocked=qg_blocked,
    )

    return ReportData(
        total_stories=len(state.stories),
        completed_count=completed_count,
        blocked_count=blocked_count,
        wall_clock_seconds=wall_clock_seconds,
        sequential_estimate_seconds=sequential_estimate,
        per_story_timings=per_story_timings,
        merge_stats=merge_stats,
        qg_stats=qg_stats,
        blocked_stories=blocked_stories,
    )


# ============================================================================
# Report renderer (Task 4)
# ============================================================================


def render_report(report: ReportData) -> str:
    """Render the report data as a human-readable multi-line string.

    Includes sections for: header, per-story timing table, timing
    comparison, merge statistics, QG statistics, and blocked stories
    (when present).

    Args:
        report: The ``ReportData`` to render.

    Returns:
        The formatted report text.

    """
    lines: list[str] = []
    separator = "=" * 72

    # Header
    lines.append("")
    lines.append(separator)
    lines.append("  PARALLEL RUN SUMMARY REPORT")
    lines.append(separator)
    lines.append("")
    lines.append(
        f"  Total stories: {report.total_stories}  |  "
        f"Completed: {report.completed_count}  |  "
        f"Blocked: {report.blocked_count}"
    )
    lines.append("")

    # Per-story timing table
    lines.append("  Per-Story Timing:")
    lines.append(
        f"  {'Story'.ljust(12)} {'Status'.ljust(10)} "
        f"{'Duration'.ljust(12)} {'Merge'.ljust(12)} "
        f"{'Start'.ljust(22)} {'End'.ljust(22)}"
    )
    lines.append("  " + "-" * 90)

    for t in report.per_story_timings:
        dur_str = _format_duration(t.duration_seconds) if t.duration_seconds > 0 else "-"
        merge_str = (
            _format_duration(t.merge_duration_seconds)
            if t.merge_duration_seconds is not None
            else "-"
        )
        start_str = t.started_at.strftime("%Y-%m-%d %H:%M:%S") if t.started_at else "-"
        end_str = (
            t.completed_at.strftime("%Y-%m-%d %H:%M:%S") if t.completed_at else "-"
        )
        lines.append(
            f"  {t.story_id.ljust(12)} {t.status.ljust(10)} "
            f"{dur_str.ljust(12)} {merge_str.ljust(12)} "
            f"{start_str.ljust(22)} {end_str.ljust(22)}"
        )

    lines.append("")

    # Timing comparison
    lines.append("  Timing Comparison:")
    lines.append(
        "    Wall-clock time (parallel):  "
        f"{_format_duration(report.wall_clock_seconds)}"
    )
    lines.append(
        "    Estimated sequential time:   "
        f"{_format_duration(report.sequential_estimate_seconds)}"
    )

    # Time saved calculation
    if report.sequential_estimate_seconds > 0:
        time_saved = (
            report.sequential_estimate_seconds - report.wall_clock_seconds
        )
        if time_saved < 0:
            time_saved = 0.0
        pct = (time_saved / report.sequential_estimate_seconds) * 100
        lines.append(
            f"    Time saved: {_format_duration(time_saved)} "
            f"({_format_percentage(pct)} reduction)"
        )
    else:
        lines.append("    Time saved: N/A")

    lines.append("")

    # Merge statistics
    lines.append("  Merge Results:")
    lines.append(
        f"    Clean: {report.merge_stats.clean}  |  "
        f"Conflict-resolved: {report.merge_stats.conflict_resolved}  |  "
        f"Failed: {report.merge_stats.failed}"
    )
    lines.append("")

    # QG statistics
    lines.append("  Post-merge QG Results:")
    lines.append(
        f"    Passed: {report.qg_stats.passed}  |  "
        f"Fixed: {report.qg_stats.fixed}  |  "
        f"Blocked: {report.qg_stats.blocked}"
    )

    # Blocked stories (only if present)
    if report.blocked_stories:
        lines.append("")
        lines.append("  Blocked Stories:")
        for story_id, reason in report.blocked_stories:
            lines.append(f"    - {story_id}: {reason}")

    lines.append("")
    lines.append(separator)
    lines.append("")

    return "\n".join(lines)


# ============================================================================
# Report output (Task 5)
# ============================================================================


def write_report(report_text: str) -> None:
    """Write the rendered report to log file and stdout.

    Appends the report to ``parallel-run.log`` via the
    ``bmad_assist_lite.parallel`` logger (which has a FileHandler set up
    by ``setup_parallel_log()``). Echoes to stdout via ``write_progress()``
    for immediate user visibility.

    Note: ``write_progress()`` internally calls ``logger.info()`` on the
    ``bmad_assist_lite.providers.base`` logger, NOT the ``parallel``
    namespace, so there are no duplicate entries in ``parallel-run.log``.

    Args:
        report_text: The rendered report string.

    """
    # Write to parallel log file via the parallel logger namespace.
    # Log as a single message to keep the report contiguous in the log
    # file (prevents interleaving with concurrent log writes).
    parallel_logger = logging.getLogger("bmad_assist_lite.parallel")
    parallel_logger.info(report_text)

    # Echo to stdout via write_progress() for immediate user visibility
    write_progress(report_text)
