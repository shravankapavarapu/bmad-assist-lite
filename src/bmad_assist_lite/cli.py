"""CLI entry point for bmad-assist-lite.

Commands:
    run         Run the BMAD development loop
    init        Initialize a new project
    compile     Compile a workflow (debug/inspect)
    reset-lock  Remove stale lock file
    fetch-docs  Pre-fetch library documentation from Context7
"""

import logging
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

import typer
import yaml

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
    # Tag the console handler so _add_file_log_handler can preserve its level
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.StreamHandler):
            h.setLevel(level)


def _add_file_log_handler(logs_dir: Path) -> logging.FileHandler | None:
    """Attach a FileHandler to the root logger that captures all messages.

    Writes to ``logs_dir/run-{local_timestamp}.log``.  Always logs at
    DEBUG level regardless of console verbosity so the file captures
    everything.

    Returns the handler (for teardown) or None on failure.
    """
    from datetime import datetime

    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = logs_dir / f"run-{ts}.log"

    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logging.getLogger().addHandler(fh)
        # Also ensure root logger level allows DEBUG through
        root = logging.getLogger()
        if root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        logging.getLogger("bmad_assist_lite").info("Run log: %s", log_path)
        return fh
    except OSError:
        return None


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
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file (default: bmad-assist-lite.yaml).",
    ),
    epic: int | None = typer.Option(
        None,
        "--epic",
        "-e",
        help="Specific epic number to run.",
    ),
    story: int | None = typer.Option(
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

    Executes the 10-phase BMAD loop: create story -> validate -> synthesize ->
    implement -> code-review -> synthesize-review -> quality-gate ->
    (fix-quality-gate) -> epic-quality-gate -> retrospective.
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
        raise typer.Exit(1) from None

    # Initialize paths
    from bmad_assist_lite.core.paths import init_paths

    paths = init_paths(project)
    file_handler = _add_file_log_handler(paths.logs_dir)

    # --- Sprint-status-driven story discovery ---
    from bmad_assist_lite.core.sprint_status import load_sprint_status

    ss_path = paths.sprint_status_file
    if not ss_path.exists():
        typer.echo(
            f"Sprint status not found: {ss_path}\n\n"
            "Create sprint-status.yaml with your stories:\n\n"
            '  generated: "2026-02-12"\n'
            "  development_status:\n"
            "    epic-1: backlog\n"
            "    1-1-story-title: backlog\n"
            "    1-2-another-story: backlog\n"
            "    epic-1-retrospective: optional\n",
            err=True,
        )
        raise typer.Exit(1)

    sprint_status = load_sprint_status(ss_path)
    backlog_stories = sprint_status.find_backlog_stories()

    if not backlog_stories:
        typer.echo("No backlog stories found in sprint-status.yaml")
        raise typer.Exit(0)

    # Group by epic, maintaining insertion order
    epic_stories: OrderedDict[int, list[tuple[int, int, str]]] = OrderedDict()
    for epic_num, story_num, full_key in backlog_stories:
        if epic and epic_num != epic:
            continue
        epic_stories.setdefault(epic_num, []).append((epic_num, story_num, full_key))

    if not epic_stories:
        typer.echo("No backlog stories match the specified filters.", err=True)
        raise typer.Exit(1)

    # Filter by --story option (start from specific story number)
    if story:
        for e_num, story_list in list(epic_stories.items()):
            filtered = [(en, sn, fk) for en, sn, fk in story_list if sn >= story]
            if filtered:
                epic_stories[e_num] = filtered
            else:
                del epic_stories[e_num]

    # Validate epic files exist for each epic
    planning_dir = paths.planning_artifacts
    epic_file_map: dict[int, Path] = {}

    skipped_epics: list[int] = []
    for e_num in epic_stories:
        epic_file = _find_epic_file(planning_dir, e_num)
        if epic_file is None or not _is_dedicated_epic_file(epic_file, e_num):
            typer.echo(
                f"Warning: No dedicated epic file for epic {e_num} "
                f"(e.g. epic-{e_num}.md) in {planning_dir} — skipping.",
                err=True,
            )
            skipped_epics.append(e_num)
            continue
        epic_file_map[e_num] = epic_file

    # Remove skipped epics from the story map
    for e_num in skipped_epics:
        del epic_stories[e_num]

    if not epic_stories:
        typer.echo("No epics with dedicated epic files found. Cannot continue.", err=True)
        raise typer.Exit(1)

    # Build epics_list and stories_for_epic for the loop
    epics_list: list[int] = list(epic_stories.keys())
    stories_for_epic: dict[int, list[str]] = {}
    story_key_map: dict[str, str] = {}  # story_id -> full sprint-status key

    for e_num, story_tuples in epic_stories.items():
        story_ids: list[str] = []
        for _, s_num, full_key in story_tuples:
            story_id = f"{e_num}.{s_num}"
            story_ids.append(story_id)
            story_key_map[story_id] = full_key
        stories_for_epic[e_num] = story_ids

    # Cache resolved story queue for create-story workflow
    _cache_story_queue(
        paths.cache_dir,
        epics_list,
        stories_for_epic,
        story_key_map,
        epic_file_map,
    )

    # Pre-fetch library documentation if context_docs is enabled
    if app_config.context_docs is not None and app_config.context_docs.enabled:
        _resolve_context_docs(
            app_config, project, paths, epics_list, epic_file_map, logger
        )

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
                resume_state,
                project,
                epics_list,
                stories_for_epic,
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

    # Close file log handler
    if file_handler is not None:
        file_handler.flush()
        file_handler.close()
        logging.getLogger().removeHandler(file_handler)

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
    model: claude-opus-4-6   # Pin Opus 4.6. To use 4.7, set `model: claude-opus-4-7` and add `effort: max` (4.7-only thinking effort: low|medium|high|xhigh|max).
  multi:
    - provider: gemini
      model: gemini-3.1-pro-preview
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
    - quality_gate
  epic_teardown:
    - epic_quality_gate
    - retrospective

timeouts:
  default: 300
  dev_story: 1200

paths:
  output_folder: _bmad-output

# Uncomment to enable library documentation fetching from Context7.
# Fetches up-to-date API docs for detected project dependencies and injects
# them into dev-story and code-review-synthesis prompts.
# context_docs:
#   enabled: true
#   max_libs: 8              # max libraries to fetch docs for
#   max_tokens_per_lib: 5000 # max tokens of docs per library
"""
    config_path.write_text(default_config)
    typer.echo(f"Created config: {config_path}")

    # Create BMAD directory structure
    planning_dir = project / "_bmad-output" / "planning-artifacts"
    planning_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"Created planning artifacts: {planning_dir}")

    impl_dir = project / "_bmad-output" / "implementation-artifacts"
    impl_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"Created implementation artifacts: {impl_dir}")

    typer.echo("\nProject initialized! Next steps:")
    typer.echo("  1. Add your epic files to _bmad-output/planning-artifacts/ (e.g., epic-1.md)")
    typer.echo("  2. Create sprint-status.yaml in _bmad-output/implementation-artifacts/")
    typer.echo("  3. Run: bmad-assist-lite run")


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
            "planning_artifacts": str(paths.planning_artifacts),
            "implementation_artifacts": str(paths.implementation_artifacts),
        },
    )

    try:
        compiled = compile_workflow(workflow, context)
        typer.echo(compiled.context)
        typer.echo(f"\n--- Token estimate: ~{compiled.token_estimate} ---")
    except Exception as e:
        typer.echo(f"Compilation failed: {e}", err=True)
        raise typer.Exit(1) from None


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


# --- Helper functions for sprint-status-driven discovery ---


def _is_dedicated_epic_file(epic_file: Path, epic_num: int) -> bool:
    """Check if an epic file is specifically for this epic (not a master fallback)."""
    stem = epic_file.stem.lower()
    return f"epic-{epic_num}" in stem or f"epic{epic_num}" in stem


def _find_epic_file(planning_dir: Path, epic_num: int) -> Path | None:
    """Find an epic file for a given epic number in planning-artifacts.

    Search order:
        1. epic-{N}.md or epic{N}.md (specific file)
        2. epics.md or *epic*.md (master file containing all epics)

    Returns the first match or None.
    """
    if not planning_dir.exists():
        return None

    # Specific epic file patterns
    for pattern in [f"epic-{epic_num}.md", f"epic{epic_num}.md"]:
        matches = list(planning_dir.glob(pattern))
        if matches:
            return matches[0]

    # Master epic file patterns
    for pattern in ["epics.md", "*epic*.md"]:
        matches = sorted(planning_dir.glob(pattern))
        if matches:
            return matches[0]

    return None


def _cache_story_queue(
    cache_dir: Path,
    epics_list: list[int],
    stories_for_epic: dict[int, list[str]],
    story_key_map: dict[str, str],
    epic_file_map: dict[int, Path],
) -> None:
    """Cache resolved story queue to .bmad-assist-lite/cache/story-queue.yaml."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "story-queue.yaml"
    temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")

    data: dict[str, Any] = {
        "epics": epics_list,
        "stories_for_epic": stories_for_epic,
        "story_key_map": story_key_map,
        "epic_file_map": {str(k): str(v) for k, v in epic_file_map.items()},
    }

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(temp_path, cache_path)
    except OSError:
        if temp_path.exists():
            temp_path.unlink()


def load_story_queue_cache(cache_dir: Path) -> dict[str, Any] | None:
    """Load cached story queue from .bmad-assist-lite/cache/story-queue.yaml."""
    cache_path = cache_dir / "story-queue.yaml"
    if not cache_path.exists():
        return None
    try:
        content = cache_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        return data if isinstance(data, dict) else None
    except (yaml.YAMLError, OSError):
        return None


def _resolve_context_docs(
    app_config: Any,
    project: Path,
    paths: Any,
    epics_list: list[int],
    epic_file_map: dict[int, Path],
    logger: logging.Logger,
) -> None:
    """Pre-fetch library documentation from Context7 for each epic."""
    from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

    ctx_cfg = app_config.context_docs
    arch_file = paths.architecture_file if paths.architecture_file.exists() else None

    typer.echo("Context7: fetching library docs...")
    for epic_num in epics_list:
        epic_file = epic_file_map.get(epic_num)
        # Skip epics that don't have a dedicated file (e.g. epic-6.md).
        # Without a dedicated file, resolve_epic_docs would auto-detect from
        # package.json, pulling in irrelevant libraries for every epic.
        if not epic_file or not _is_dedicated_epic_file(epic_file, epic_num):
            continue
        try:
            docs = resolve_epic_docs(
                epic_num=epic_num,
                project_root=project,
                cache_dir=paths.cache_dir,
                epic_file=epic_file,
                architecture_file=arch_file,
                max_libs=ctx_cfg.max_libs,
                max_tokens_per_lib=ctx_cfg.max_tokens_per_lib,
            )
            if docs:
                typer.echo(
                    f"  Epic {epic_num}: fetched docs for {len(docs)} libraries"
                    f" ({', '.join(docs.keys())})"
                )
            else:
                typer.echo(f"  Epic {epic_num}: no library docs fetched")
        except Exception as e:
            typer.echo(f"  Epic {epic_num}: context docs failed ({e})", err=True)


@app.command(name="fetch-docs")
def fetch_docs(
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
        help="Epic number to fetch docs for.",
    ),
    max_libs: int = typer.Option(8, "--max-libs", help="Maximum libraries to fetch."),
    max_tokens: int = typer.Option(5000, "--max-tokens", help="Max tokens per library."),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase verbosity.",
    ),
) -> None:
    """Pre-fetch library documentation from Context7.

    Detects project libraries and fetches their documentation for use
    during dev-story and code-review-synthesis phases.
    """
    _setup_logging(verbose)

    project = project.resolve()

    from bmad_assist_lite.core.paths import init_paths

    paths = init_paths(project)

    # Find epic and architecture files
    planning_dir = paths.planning_artifacts
    epic_file = _find_epic_file(planning_dir, epic_num)
    arch_file = paths.architecture_file if paths.architecture_file.exists() else None

    from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

    try:
        docs = resolve_epic_docs(
            epic_num=epic_num,
            project_root=project,
            cache_dir=paths.cache_dir,
            epic_file=epic_file,
            architecture_file=arch_file,
            max_libs=max_libs,
            max_tokens_per_lib=max_tokens,
        )
    except ImportError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e

    if docs:
        typer.echo(f"Fetched documentation for {len(docs)} libraries:")
        for name in docs:
            typer.echo(f"  - {name}")
    else:
        typer.echo("No library documentation found or fetched.")
