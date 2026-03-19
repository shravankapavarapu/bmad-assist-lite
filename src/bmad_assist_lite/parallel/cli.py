"""Implement parallel run CLI command with branch guard and orchestrator startup."""

import asyncio
import logging
from pathlib import Path

import typer

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
