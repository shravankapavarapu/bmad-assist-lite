"""CLI entry point for bmad-assist-lite.

Commands:
    run         Run the BMAD development loop
    init        Initialize a new project
    compile     Compile a workflow (debug/inspect)
    reset-lock  Remove stale lock file
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import typer

from bmad_assist_lite import __version__

app = typer.Typer(
    name="bmad-assist-lite",
    help="Lightweight BMAD methodology automation with Multi-LLM orchestration.",
    no_args_is_help=True,
)


def _setup_logging(verbosity: int) -> None:
    """Configure logging based on verbosity level."""
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"bmad-assist-lite {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Bmad-assist-lite: Lightweight BMAD automation."""
    pass


@app.command()
def run(
    project: Path = typer.Option(
        Path("."),
        "--project",
        "-p",
        help="Path to project directory.",
        exists=True,
        dir_okay=True,
        file_okay=False,
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (default: bmad-assist-lite.yaml).",
    ),
    epic: Optional[int] = typer.Option(
        None,
        "--epic",
        "-e",
        help="Specific epic number to run.",
    ),
    story: Optional[int] = typer.Option(
        None,
        "--story",
        "-s",
        help="Specific story number to start from.",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase verbosity (-v for INFO, -vv for DEBUG).",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        "-r",
        help="Resume from saved state.",
    ),
) -> None:
    """Run the BMAD development loop.

    Executes the 7-phase BMAD loop: create story -> validate -> synthesize ->
    implement -> code-review -> synthesize-review -> retrospective.
    """
    _setup_logging(verbose)
    logger = logging.getLogger("bmad_assist_lite")

    project = project.resolve()
    logger.info("Project path: %s", project)

    # Load config
    from bmad_assist_lite.core.config import load_config_with_project

    project_config = project / "bmad-assist-lite.yaml"
    if not project_config.exists() and config is None:
        typer.echo(f"Config not found: {project_config}", err=True)
        typer.echo("Run 'bmad-assist-lite init' to create a config file.", err=True)
        raise typer.Exit(1)

    try:
        app_config = load_config_with_project(project)
    except Exception as e:
        typer.echo(f"Config error: {e}", err=True)
        raise typer.Exit(1)

    # Initialize paths
    from bmad_assist_lite.core.paths import init_paths

    init_paths(project)

    # Load epics and stories
    from bmad_assist_lite.bmad import parse_epic_file

    docs_dir = project / "docs"
    if not docs_dir.exists():
        typer.echo(f"Docs directory not found: {docs_dir}", err=True)
        raise typer.Exit(1)

    # Find epic files
    epic_files = sorted(docs_dir.glob("epic*.md")) + sorted(docs_dir.glob("epics*.md"))
    epics_dir = docs_dir / "epics"
    if epics_dir.exists():
        epic_files.extend(sorted(epics_dir.glob("*.md")))

    if not epic_files:
        typer.echo("No epic files found in docs/", err=True)
        raise typer.Exit(1)

    # Load sprint status for done-story filtering
    from bmad_assist_lite.core.sprint_status import (
        get_sprint_status_path,
        load_sprint_status,
    )

    sprint_status = load_sprint_status(get_sprint_status_path(project))

    _DONE_STATUSES = {"done", "complete", "completed"}

    epics_list: list[int] = []
    stories_for_epic: dict[int, list[str]] = {}

    for ef in epic_files:
        epic_doc = parse_epic_file(ef)
        if epic_doc.stories:
            e_num = epic_doc.epic_number
            if epic and e_num != epic:
                continue

            # Build story list, filtering out done stories
            story_ids: list[str] = []
            for s in epic_doc.stories:
                # Skip if done in markdown metadata
                if s.status.lower() in _DONE_STATUSES:
                    continue
                # Skip if done in sprint-status.yaml
                if sprint_status.is_story_done(s.number):
                    continue
                story_ids.append(s.number)

            if story:
                # Filter to start from specific story
                story_key = f"{e_num}.{story}"
                if story_key in story_ids:
                    idx = story_ids.index(story_key)
                    story_ids = story_ids[idx:]

            if story_ids:
                epics_list.append(e_num)
                stories_for_epic[e_num] = story_ids

    if not epics_list:
        typer.echo("No epics with stories found.", err=True)
        raise typer.Exit(1)

    epics_list.sort()

    typer.echo(
        f"Found {len(epics_list)} epic(s) with "
        f"{sum(len(s) for s in stories_for_epic.values())} stories"
    )

    # Check for resume state
    from bmad_assist_lite.core.state import get_state_path, load_state

    resume_state = None
    if resume:
        resume_state = load_state(get_state_path(project))
        if resume_state.current_phase is not None:
            typer.echo(
                f"Resuming from: epic={resume_state.current_epic} "
                f"story={resume_state.current_story}"
            )

            # Validate resume state against sprint-status
            from bmad_assist_lite.core.resume_validation import validate_resume_state

            validation = validate_resume_state(
                resume_state, project, epics_list, stories_for_epic,
                app_config.loop.story,
            )
            if validation.advanced:
                typer.echo(f"Resume validation: {validation.summary()}")
                resume_state = validation.state
            if validation.project_complete:
                typer.echo("All work is already complete!")
                raise typer.Exit(0)
        else:
            typer.echo("No saved state found, starting fresh.")
            resume_state = None

    # Run the loop
    from bmad_assist_lite.loop.runner import run_loop
    from bmad_assist_lite.loop.types import LoopExitReason

    exit_reason = run_loop(
        config=app_config,
        project_path=project,
        epics=epics_list,
        stories_for_epic=stories_for_epic,
        resume_state=resume_state,
    )

    if exit_reason == LoopExitReason.COMPLETED:
        typer.echo("\nAll epics completed successfully!")
    elif exit_reason == LoopExitReason.INTERRUPTED:
        typer.echo("\nLoop interrupted. Use --resume to continue.")
        raise typer.Exit(130)
    elif exit_reason == LoopExitReason.ERROR:
        typer.echo("\nLoop failed with errors.", err=True)
        raise typer.Exit(1)


@app.command()
def init(
    project: Path = typer.Option(
        Path("."),
        "--project",
        "-p",
        help="Path to project directory.",
    ),
) -> None:
    """Initialize a project for bmad-assist-lite.

    Creates bmad-assist-lite.yaml config and required directory structure.
    """
    project = project.resolve()

    config_path = project / "bmad-assist-lite.yaml"
    if config_path.exists():
        typer.echo(f"Config already exists: {config_path}")
        return

    # Create default config
    default_config = """\
# bmad-assist-lite configuration
providers:
  master:
    provider: claude
    model: opus
  multi:
    - provider: gemini
      model: gemini-2.5-flash
    - provider: claude
      model: sonnet

loop:
  story:
    - create_story
    - validate_story
    - validate_story_synthesis
    - dev_story
    - code_review
    - code_review_synthesis
  epic_teardown:
    - retrospective

timeouts:
  default: 300
  dev_story: 600

paths:
  output_folder: _bmad-output
"""
    config_path.write_text(default_config)
    typer.echo(f"Created config: {config_path}")

    # Create docs directory
    docs_dir = project / "docs"
    docs_dir.mkdir(exist_ok=True)
    typer.echo(f"Created docs directory: {docs_dir}")

    # Create output directory
    output_dir = project / "_bmad-output"
    output_dir.mkdir(exist_ok=True)
    typer.echo(f"Created output directory: {output_dir}")

    typer.echo("\nProject initialized! Next steps:")
    typer.echo("  1. Add your epic files to docs/ (e.g., docs/epic-1.md)")
    typer.echo("  2. Run: bmad-assist-lite run")


@app.command()
def compile(
    workflow: str = typer.Argument(help="Workflow name (e.g., create-story)."),
    project: Path = typer.Option(
        Path("."),
        "--project",
        "-p",
        help="Path to project directory.",
    ),
    epic_num: int = typer.Option(1, "--epic", "-e", help="Epic number."),
    story_num: int = typer.Option(1, "--story", "-s", help="Story number."),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase verbosity.",
    ),
) -> None:
    """Compile a workflow and print the output.

    Useful for debugging and inspecting compiled prompts.
    """
    _setup_logging(verbose)

    project = project.resolve()

    from bmad_assist_lite.compiler import compile_workflow
    from bmad_assist_lite.compiler.types import CompilerContext
    from bmad_assist_lite.core.paths import init_paths

    paths = init_paths(project)

    context = CompilerContext(
        project_root=project,
        output_folder=paths.output_folder,
        project_knowledge=paths.project_knowledge,
        resolved_variables={
            "epic_num": epic_num,
            "story_num": story_num,
        },
    )

    try:
        compiled = compile_workflow(workflow, context)
        typer.echo(compiled.context)
        typer.echo(f"\n--- Token estimate: ~{compiled.token_estimate} ---")
    except Exception as e:
        typer.echo(f"Compilation failed: {e}", err=True)
        raise typer.Exit(1)


@app.command(name="reset-lock")
def reset_lock(
    project: Path = typer.Option(
        Path("."),
        "--project",
        "-p",
        help="Path to project directory.",
    ),
) -> None:
    """Remove stale lock file.

    Use this if a previous run crashed and left a lock file behind.
    """
    project = project.resolve()
    lock_path = project / ".bmad-assist-lite" / "running.lock"

    if lock_path.exists():
        lock_path.unlink()
        typer.echo(f"Removed lock file: {lock_path}")
    else:
        typer.echo("No lock file found.")
