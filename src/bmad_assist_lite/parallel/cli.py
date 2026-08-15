"""Implement parallel CLI commands: run and status."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import typer
import yaml

if TYPE_CHECKING:
    from bmad_assist_lite.parallel.dependency_graph import DependencyGraph
    from bmad_assist_lite.parallel.state import ParallelState, StoryState, StoryStatus

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
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume a previous parallel run (reuse existing state).",
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
    # Pre-compute done stories from sprint-status for accurate display
    from bmad_assist_lite.core.sprint_status import (
        get_sprint_status_path,
        load_sprint_status,
    )

    ss_path = get_sprint_status_path(project)
    done_ids: set[str] = set()
    if ss_path.exists():
        try:
            _ss = load_sprint_status(ss_path)
            for sid in graph.all_story_ids:
                if _ss.is_story_done(sid):
                    done_ids.add(sid)
        except Exception:
            pass  # Non-fatal; orchestrator will also seed

    ready_stories = graph.get_ready_stories(
        done_ids=done_ids,
        in_flight_ids=set(),
        blocked_ids=set(),
    )

    remaining = graph.story_count - len(done_ids)

    typer.echo(f"Max concurrency: {parallel_config.max_concurrency}")
    typer.echo(f"Stagger delay: {parallel_config.stagger_delay}s")
    typer.echo(f"Base branch: {current_branch}")
    typer.echo(f"Epic: {epic_num}")
    typer.echo(f"Total stories: {graph.story_count}")
    if done_ids:
        typer.echo(f"Already done: {len(done_ids)}")
        typer.echo(f"Remaining: {remaining}")
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
                resume=resume,
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
# Dependency status formatting (Story 6.2 — Task 3)
# ============================================================================

# Dependency status symbols — hoisted to module level to avoid per-call allocation.
# Lazily populated on first use (after StoryStatus import).
_DEP_SYMBOLS: dict["StoryStatus", str] | None = None


def _get_dep_symbols() -> dict["StoryStatus", str]:
    """Return the dependency status symbol mapping, building it on first call."""
    global _DEP_SYMBOLS  # noqa: PLW0603
    if _DEP_SYMBOLS is None:
        from bmad_assist_lite.parallel.state import StoryStatus

        _DEP_SYMBOLS = {
            StoryStatus.DONE: "\u2713",
            StoryStatus.IN_FLIGHT: "\u23f3",
            StoryStatus.BLOCKED: "\u2717 (blocked)",
            StoryStatus.BACKLOG: "\u2026",
            StoryStatus.MERGING: "\u2026",
        }
    return _DEP_SYMBOLS


def _format_dependency_status(
    story_id: str,
    graph: "DependencyGraph | None",
    stories: dict[str, "StoryState"],
) -> str:
    """Format dependency status with visual indicators for a story.

    Returns a comma-separated string of dependency statuses, e.g.
    ``"3.1 ✓, 3.2 ⏳"``. Returns empty string when no graph is
    available or the story has no dependencies.
    """
    if graph is None:
        return ""

    try:
        deps = graph.dependencies_of(story_id)
    except KeyError:
        return ""

    if not deps:
        return ""

    symbols = _get_dep_symbols()

    parts: list[str] = []
    for dep_id in deps:
        dep_story = stories.get(dep_id)
        if dep_story is None:
            parts.append(f"{dep_id} ?")
        else:
            symbol = symbols.get(dep_story.status, "?")
            parts.append(f"{dep_id} {symbol}")

    return ", ".join(parts)


# ============================================================================
# Table formatting (Task 3)
# ============================================================================


def _format_status_table(
    state: "ParallelState",
    graph: "DependencyGraph | None" = None,
) -> str:
    """Build a human-readable aligned text table of story statuses.

    Columns: Story ID, Status, Phase, Duration, Depends On, Info
    """
    from bmad_assist_lite.parallel.state import StoryStatus

    # Column headers
    headers = ["Story ID", "Status", "Phase", "Duration", "Depends On", "Info"]

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

        # Depends On — show formatted dependency status with symbols
        depends_on = _format_dependency_status(story_id, graph, state.stories)

        # Info column — show error for blocked/failed stories
        info = ""
        if story.error:
            if len(story.error) > 80:
                info = story.error[:77] + "..."
            else:
                info = story.error

        rows.append([story_id, status_val, phase, duration, depends_on, info])

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


def _format_summary(
    state: "ParallelState",
    graph: "DependencyGraph | None" = None,
) -> str:
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

    # Dependency health: stories waiting on blocked dependencies
    if graph is not None:
        blocked_ids = {
            sid
            for sid, story in state.stories.items()
            if story.status == StoryStatus.BLOCKED
        }
        if blocked_ids:
            waiting_count = 0
            for sid, story in state.stories.items():
                # Exclude stories that are themselves blocked
                if story.status == StoryStatus.BLOCKED:
                    continue
                try:
                    deps = graph.dependencies_of(sid)
                except KeyError:
                    continue
                if any(dep_id in blocked_ids for dep_id in deps):
                    waiting_count += 1
            if waiting_count > 0:
                suffix = "y" if waiting_count == 1 else "ies"
                lines.append("")
                lines.append(
                    f"\u26a0 {waiting_count} stor{suffix} waiting on"
                    " blocked dependencies"
                )

    return "\n".join(lines)


# ============================================================================
# Dependency graph loader for status command (Story 6.2 — Task 1)
# ============================================================================


def _load_dependency_graph(
    project: Path,
    state: "ParallelState",
    epic_file_override: Path | None = None,
) -> "DependencyGraph | None":
    """Attempt to load a DependencyGraph for the running epic.

    Uses the epic number from *state* to discover and parse the epic
    file.  Returns ``None`` (graceful degradation) when the epic file
    cannot be found or parsed — the status command must never crash
    due to missing epic data.
    """
    from bmad_assist_lite.core.exceptions import ParserError
    from bmad_assist_lite.parallel.exceptions import ParallelError

    try:
        from bmad_assist_lite.bmad.parser import parse_epic_file
        from bmad_assist_lite.parallel.dependency_graph import DependencyGraph

        resolved_file = epic_file_override
        if resolved_file is None:
            from bmad_assist_lite.cli import _find_epic_file
            from bmad_assist_lite.core.paths import init_paths

            paths = init_paths(project)
            planning_dir = paths.planning_artifacts
            resolved_file = _find_epic_file(planning_dir, state.epic)

        if resolved_file is None:
            logger.warning(
                "Could not find epic file for epic %d; "
                "dependency display disabled",
                state.epic,
            )
            return None

        epic_doc = parse_epic_file(resolved_file, epic_number=state.epic)
        return DependencyGraph(epic_doc.stories)
    except (ParallelError, ParserError, OSError, ValueError, yaml.YAMLError):
        logger.warning(
            "Failed to build dependency graph for epic %d; "
            "dependency display disabled",
            state.epic,
            exc_info=True,
        )
        return None


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
    epic_file: Path | None = typer.Option(
        None,
        "--epic-file",
        help="Path to epic file for dependency display.",
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

    # ------------------------------------------------------------------
    # Build dependency graph for dependency status display (Story 6.2)
    # ------------------------------------------------------------------
    graph = _load_dependency_graph(project, state, epic_file)

    # Display summary header
    summary = _format_summary(state, graph=graph)
    typer.echo(summary)
    typer.echo("")

    # Display table
    table = _format_status_table(state, graph=graph)
    typer.echo(table)


# ============================================================================
# Parallel unblock command
# ============================================================================


def parallel_unblock(
    story_id: str = typer.Argument(help="Story ID to unblock (e.g. 3.2)."),
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
    """Reset a blocked story to backlog so the orchestrator picks it up on the next run."""
    from bmad_assist_lite.parallel.exceptions import ParallelError
    from bmad_assist_lite.parallel.state import (
        StoryStatus,
        get_parallel_state_path,
        load_state,
        save_state,
    )

    project = project.resolve()

    # Check for orchestrator lock file
    lock_path = project / ".bmad-assist-lite" / "running.lock"
    if lock_path.exists():
        typer.echo(
            "Cannot unblock while orchestrator is running (lock file exists). "
            "Stop the orchestrator first. "
            "If the orchestrator crashed, run `bmad-assist-lite reset-lock` to remove the stale lock.",
            err=True,
        )
        raise typer.Exit(1)

    state_path = get_parallel_state_path(project)

    try:
        state = load_state(state_path)
    except ParallelError as exc:
        typer.echo(f"Error reading state file: {exc}", err=True)
        raise typer.Exit(1) from None

    if state is None:
        typer.echo("No parallel run state found", err=True)
        raise typer.Exit(1)

    if story_id not in state.stories:
        typer.echo(f"Story {story_id} not found in parallel state", err=True)
        raise typer.Exit(1)

    current_status = state.stories[story_id].status
    if current_status != StoryStatus.BLOCKED:
        typer.echo(
            f"Story {story_id} is not blocked (status: {current_status.value})",
            err=True,
        )
        raise typer.Exit(1)

    new_state = state.with_story_status(story_id, StoryStatus.BACKLOG)

    try:
        save_state(new_state, state_path)
    except ParallelError as exc:
        typer.echo(f"Failed to save state: {exc}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Story {story_id} unblocked -- will be picked up on next parallel run")


# ============================================================================
# Parked-merge listing
# ============================================================================


def parallel_list_parked(
    project: Path = typer.Option(
        Path("."),
        "--project",
        "-p",
        help="Path to project directory.",
        exists=True,
        file_okay=False,
        dir_okay=True,
    ),
) -> None:
    """List merges parked by the merge ladder, with reason and age.

    A parked merge keeps its branch and worktree instead of being deleted.
    This is how an operator finds them without knowing they exist.
    """
    from bmad_assist_lite.parallel.parked import get_parked_dir, list_parked_merges

    records = list_parked_merges(project)
    if not records:
        typer.echo("No parked merges.")
        return

    now = _utc_now()
    typer.echo(f"Parked merges ({len(records)}) — records in {get_parked_dir(project)}\n")
    for record in records:
        age_hours = max((now - record.parked_at).total_seconds(), 0.0) / 3600.0
        typer.echo(f"  Story {record.story_id}")
        typer.echo(f"    branch:   {record.branch}")
        typer.echo(f"    worktree: {record.worktree_path or '(not checked out)'}")
        typer.echo(f"    reason:   {record.reason or '(unspecified)'}")
        typer.echo(f"    age:      {age_hours:.1f}h (parked {record.parked_at:%Y-%m-%d %H:%M} UTC)")
        if record.attempts:
            ladder = " -> ".join(f"{a.tier.value}:{a.outcome}" for a in record.attempts)
            typer.echo(f"    ladder:   {ladder}")
        typer.echo("")

    typer.echo(
        "To un-park: fix the work on the branch, delete its record under "
        f"{get_parked_dir(project)}, set the story back to 'backlog' in "
        "parallel-state.yaml, and re-run with --resume."
    )
