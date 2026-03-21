"""Implement parallel CLI commands: run and status."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer
import yaml

if TYPE_CHECKING:
    from bmad_assist_lite.parallel.state import ParallelState

logger = logging.getLogger(__name__)


# ============================================================================
# Parallel run command
# ============================================================================


def parallel_run(
    project: Path = typer.Option(
        Path("."),
        "--project",
        "-p",
        help="Path to project directory.",
        exists=True,
        dir_okay=True,
        file_okay=False,
    ),
    epic_num: int = typer.Option(
        ...,
        "--epic",
        "-e",
        help="Epic number to run in parallel.",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase verbosity (-v for INFO, -vv for DEBUG).",
    ),
) -> None:
    """Start the parallel story orchestrator with branch safety.

    Reads the epic file, builds a dependency graph, and begins parallel
    execution of stories via git worktrees.
    """
    from bmad_assist_lite.cli import _setup_logging

    _setup_logging(verbose)
    project = project.resolve()

    # ------------------------------------------------------------------
    # Branch guard
    # ------------------------------------------------------------------
    from bmad_assist_lite.parallel.exceptions import ParallelError
    from bmad_assist_lite.parallel.git_ops import get_current_branch, is_protected_branch

    try:
        current_branch = get_current_branch(project)
    except ParallelError as exc:
        typer.echo(f"Git error: {exc}", err=True)
        raise typer.Exit(1) from None

    if is_protected_branch(current_branch):
        typer.echo(
            "Parallel mode cannot run on main/master. "
            "Create a feature branch first.",
            err=True,
        )
        raise typer.Exit(1)

    if current_branch == "HEAD":
        typer.echo(
            "Parallel mode cannot run in detached HEAD state. "
            "Check out a feature branch first.",
            err=True,
        )
        raise typer.Exit(1)

    # ------------------------------------------------------------------
    # Load configuration
    # ------------------------------------------------------------------
    from bmad_assist_lite.core.config import load_config_with_project
    from bmad_assist_lite.core.exceptions import ConfigError
    from bmad_assist_lite.parallel.config import ParallelConfig

    try:
        app_config = load_config_with_project(project)
    except ConfigError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(1) from None

    parallel_config = app_config.parallel or ParallelConfig()

    # ------------------------------------------------------------------
    # Parse epic file and build dependency graph
    # ------------------------------------------------------------------
    from bmad_assist_lite.core.paths import init_paths

    paths = init_paths(project)
    planning_dir = paths.planning_artifacts

    from bmad_assist_lite.cli import _find_epic_file, _is_dedicated_epic_file

    epic_file = _find_epic_file(planning_dir, epic_num)
    if epic_file is None or not _is_dedicated_epic_file(epic_file, epic_num):
        typer.echo(
            f"No dedicated epic file for epic {epic_num} "
            f"(e.g. epic-{epic_num}.md) in {planning_dir}.",
            err=True,
        )
        raise typer.Exit(1)

    from bmad_assist_lite.bmad.parser import parse_epic_file
    from bmad_assist_lite.core.exceptions import ParserError
    from bmad_assist_lite.parallel.dependency_graph import DependencyGraph

    try:
        epic_doc = parse_epic_file(epic_file, epic_number=epic_num)
    except ParserError as exc:
        typer.echo(f"Epic parse error: {exc}", err=True)
        raise typer.Exit(1) from None

    try:
        graph = DependencyGraph(epic_doc.stories)
    except ParallelError as exc:
        typer.echo(f"Dependency graph error: {exc}", err=True)
        raise typer.Exit(1) from None

    # ------------------------------------------------------------------
    # Startup settings summary
    # ------------------------------------------------------------------
    ready_stories = graph.get_ready_stories(
        done_ids=set(),
        in_flight_ids=set(),
        blocked_ids=set(),
    )

    typer.echo(f"Max concurrency: {parallel_config.max_concurrency}")
    typer.echo(f"Stagger delay: {parallel_config.stagger_delay}s")
    typer.echo(f"Base branch: {current_branch}")
    typer.echo(f"Epic: {epic_num}")
    typer.echo(f"Total stories: {graph.story_count}")
    typer.echo(f"Ready stories: {len(ready_stories)}")

    # ------------------------------------------------------------------
    # Acquire lock and start orchestrator
    # ------------------------------------------------------------------
    from bmad_assist_lite.core.exceptions import StateError
    from bmad_assist_lite.loop.locking import running_lock

    try:
        with running_lock(project):
            from bmad_assist_lite.parallel.orchestrator import Orchestrator

            orchestrator = Orchestrator(
                dependency_graph=graph,
                config=parallel_config,
                project_root=project,
                epic_num=epic_num,
                base_branch=current_branch,
            )
            try:
                asyncio.run(orchestrator.run())
            except ParallelError as exc:
                typer.echo(f"Parallel run error: {exc}", err=True)
                raise typer.Exit(1) from None
            except (KeyboardInterrupt, asyncio.CancelledError):
                # NOTE: With the orchestrator's custom SIGINT handler
                # installed via signal.signal(), Python's default
                # SIGINT→KeyboardInterrupt translation is overridden.
                # Additionally, asyncio.run() installs its own SIGINT
                # handler which the orchestrator overrides. After the
                # orchestrator handles shutdown internally (drain or
                # force-exit), run() returns normally — KeyboardInterrupt
                # is never raised. This block is kept as a safety net
                # for edge cases (e.g., signal during startup before
                # handlers are installed).
                typer.echo("Parallel run interrupted.", err=True)
                raise typer.Exit(130) from None
    except StateError:
        typer.echo(
            "Another bmad-assist-lite instance is already running. "
            "If no other instance is running, the lock may be stale. "
            "Delete `.bmad-assist-lite/running.lock` in the project root and retry.",
            err=True,
        )
        raise typer.Exit(1) from None


# ============================================================================
# Timestamp helper
# ============================================================================


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info (naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


# ============================================================================
# Phase peeking helper (Task 1b)
# ============================================================================


def _peek_worktree_phase(worktree_path: Path | None) -> str | None:
    """Read the current phase from a worktree's state.yaml.

    Returns the phase string or None if the file is missing, corrupt,
    or the path is None. Performs read-only access only.
    """
    if worktree_path is None:
        return None

    state_file = worktree_path / ".bmad-assist" / "state.yaml"
    try:
        # Size limit: skip files over 1 MB to avoid blocking
        if state_file.exists() and state_file.stat().st_size > 1_048_576:
            return None

        content = state_file.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            phase = data.get("current_phase")
            if isinstance(phase, str):
                return phase
    except (OSError, yaml.YAMLError, UnicodeDecodeError, ValueError):
        pass

    return None


# ============================================================================
# Duration calculation helper (Task 2)
# ============================================================================


def _format_duration(
    started_at: datetime | None,
    completed_at: datetime | None,
) -> str:
    """Format a duration string from start/end timestamps.

    For running stories: elapsed from started_at to now.
    For done/blocked stories: elapsed from started_at to completed_at.
    For stories not yet started: returns "-".
    """
    if started_at is None:
        return "-"

    end = completed_at if completed_at is not None else _utc_now()
    delta = end - started_at
    total_seconds = max(0, int(delta.total_seconds()))

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if hours > 0:
        if minutes > 0:
            return f"{hours}h {minutes}m {seconds}s"
        return f"{hours}h {seconds}s"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


# ============================================================================
# Table formatting (Task 3)
# ============================================================================


def _format_status_table(state: "ParallelState") -> str:
    """Build a human-readable aligned text table of story statuses.

    Columns: Story ID, Status, Phase, Duration, Blocked By, Info
    """
    from bmad_assist_lite.parallel.state import StoryStatus

    # Column headers
    headers = ["Story ID", "Status", "Phase", "Duration", "Blocked By", "Info"]

    # Build rows
    rows: list[list[str]] = []
    for story_id, story in sorted(state.stories.items()):
        status_val = story.status.value

        # Phase column
        phase: str = "-"
        if story.status == StoryStatus.IN_FLIGHT:
            # For running stories, peek at worktree state.yaml
            peeked = _peek_worktree_phase(story.worktree_path)
            if peeked is not None:
                phase = peeked
        # For non-running stories, no last_phase field exists in model

        # Duration
        duration = _format_duration(story.started_at, story.completed_at)

        # Blocked By — not available in current model, leave empty
        blocked_by = ""

        # Info column — show error for blocked/failed stories
        info = ""
        if story.error:
            if len(story.error) > 80:
                info = story.error[:77] + "..."
            else:
                info = story.error

        rows.append([story_id, status_val, phase, duration, blocked_by, info])

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Build the table
    lines: list[str] = []

    # Header row
    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)

    # Separator
    sep_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
    lines.append(sep_line)

    # Data rows
    for row in rows:
        data_line = "  ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row))
        lines.append(data_line)

    return "\n".join(lines)


# ============================================================================
# Summary counts (Task 4)
# ============================================================================


def _format_summary(state: "ParallelState") -> str:
    """Build summary counts and overall status display."""
    from bmad_assist_lite.parallel.state import StoryStatus

    counts: dict[str, int] = {
        "done": 0,
        "in_flight": 0,
        "merging": 0,
        "blocked": 0,
        "backlog": 0,
    }

    for story in state.stories.values():
        status_key = story.status.value
        if status_key in counts:
            counts[status_key] += 1

    lines: list[str] = []

    # Header with epic and base branch
    lines.append(f"Epic: {state.epic}  |  Base branch: {state.base_branch}")
    lines.append("")

    # Status counts
    count_parts = [
        f"Done: {counts['done']}",
        f"In-flight: {counts['in_flight']}",
        f"Merging: {counts['merging']}",
        f"Blocked: {counts['blocked']}",
        f"Backlog: {counts['backlog']}",
    ]
    lines.append(" | ".join(count_parts))

    # All done message (guard against vacuous truth on empty dict)
    if state.stories and all(
        s.status == StoryStatus.DONE for s in state.stories.values()
    ):
        lines.append("")
        lines.append("All stories complete!")

    # Blocked stories warning
    blocked_count = counts["blocked"]
    if blocked_count > 0:
        suffix = "y" if blocked_count == 1 else "ies"
        lines.append("")
        lines.append(f"\u26a0 {blocked_count} stor{suffix} blocked \u2014 see Info column")

    return "\n".join(lines)


# ============================================================================
# Parallel status command (Task 1)
# ============================================================================


def parallel_status(
    project: Path = typer.Option(
        Path("."),
        "--project",
        "-p",
        help="Path to project directory.",
        exists=True,
        dir_okay=True,
        file_okay=False,
    ),
) -> None:
    """Show the current state of a parallel run.

    Reads parallel-state.yaml and displays a human-readable table
    showing story statuses, durations, and diagnostic info. This
    command is read-only and safe to run from another terminal.
    """
    from bmad_assist_lite.parallel.exceptions import ParallelError
    from bmad_assist_lite.parallel.state import get_parallel_state_path, load_state

    project = project.resolve()
    state_path = get_parallel_state_path(project)

    try:
        state = load_state(state_path)
    except ParallelError as exc:
        typer.echo(f"Error reading state file: {exc}", err=True)
        raise typer.Exit(1) from None

    if state is None:
        typer.echo("No parallel run state found")
        return

    # Display summary header
    summary = _format_summary(state)
    typer.echo(summary)
    typer.echo("")

    # Display table
    table = _format_status_table(state)
    typer.echo(table)
