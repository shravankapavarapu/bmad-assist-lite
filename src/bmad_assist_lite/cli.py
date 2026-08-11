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

# Exit codes are a public CLI contract: 0 = completed, 1 = failed,
# 130 = interrupted, and this one for "a run budget stopped the run cleanly".
# It is deliberately distinct from 1 so an unattended run's non-zero exit can
# be told apart from a crash without reading the log.
BUDGET_EXHAUSTED_EXIT_CODE = 3

app = typer.Typer(
    name="bmad-assist-lite",
    help="Lightweight BMAD methodology automation with Multi-LLM orchestration.",
    no_args_is_help=True,
)

parallel_app = typer.Typer(
    name="parallel",
    help="Parallel story execution commands.",
    no_args_is_help=True,
)
app.add_typer(parallel_app, name="parallel")

from bmad_assist_lite.parallel.cli import parallel_run, parallel_status, parallel_unblock  # noqa: E402, I001

parallel_app.command(name="run")(parallel_run)
parallel_app.command(name="status")(parallel_status)
parallel_app.command(name="unblock")(parallel_unblock)


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


def _add_file_log_handler(
    logs_dir: Path, *, label: str = "run",
) -> logging.FileHandler | None:
    """Attach a FileHandler to the root logger that captures all messages.

    Writes to ``logs_dir/{label}-{local_timestamp}.log``.  Always logs at
    DEBUG level regardless of console verbosity so the file captures
    everything.

    Args:
        logs_dir: Directory to write the log file in.
        label: Prefix for the log filename (e.g. ``"run"`` or ``"story-2.1"``).

    Returns:
        The handler (for teardown) or None on failure.

    """
    from datetime import datetime

    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = logs_dir / f"{label}-{ts}.log"

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
        help="Run only this story number (implies --single-story).",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help="Increase verbosity (-v for INFO, -vv for DEBUG).",
    ),
    single_story: bool = typer.Option(
        False,
        "--single-story",
        help="Exit after completing a single story.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        "-r",
        help="Resume from saved state.",
    ),
    teardown_only: bool = typer.Option(
        False,
        "--teardown-only",
        help="Skip story discovery and run epic teardown phases directly.",
    ),
    fix_post_merge: bool = typer.Option(
        False,
        "--fix-post-merge",
        help="Run fix-quality-gate phase for a post-merge QG failure.",
    ),
    attempt: int = typer.Option(
        1,
        "--attempt",
        help="Fix attempt number (1-based) for retry context.",
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
    parallel_logs_dir = os.environ.get("BMAD_PARALLEL_LOGS_DIR")
    logs_dir = Path(parallel_logs_dir) if parallel_logs_dir else paths.logs_dir
    log_label = "run"
    if parallel_logs_dir and epic is not None and story is not None:
        log_label = f"story-{epic}.{story}"
    file_handler = _add_file_log_handler(logs_dir, label=log_label)

    # --- Teardown-only mode: bypass story discovery, run epic teardown directly ---
    if teardown_only:
        if epic is None:
            typer.echo("--teardown-only requires --epic to be specified.", err=True)
            raise typer.Exit(1)

        from bmad_assist_lite.core.state import Phase, State
        from bmad_assist_lite.loop.runner import run_loop
        from bmad_assist_lite.loop.types import LoopExitReason

        epic_teardown_phases = app_config.loop.epic_teardown
        if not epic_teardown_phases:
            typer.echo("No epic teardown phases configured.", err=True)
            raise typer.Exit(1)

        # Construct a resume state starting at the first teardown phase
        teardown_state = State(
            current_epic=epic,
            current_story=None,
            current_phase=Phase(epic_teardown_phases[0]),
        )

        typer.echo(
            f"Running epic teardown for epic {epic} "
            f"(phases: {', '.join(epic_teardown_phases)})"
        )

        exit_reason = run_loop(
            config=app_config,
            project_path=project,
            epics=[epic],
            stories_for_epic={epic: []},
            resume_state=teardown_state,
            single_story=False,
        )

        # Close file log handler
        if file_handler is not None:
            file_handler.flush()
            file_handler.close()
            logging.getLogger().removeHandler(file_handler)

        if exit_reason == LoopExitReason.COMPLETED:
            typer.echo("\nEpic teardown completed successfully!")
        elif exit_reason == LoopExitReason.BUDGET_EXHAUSTED:
            typer.echo("\nRun budget exhausted — stopped cleanly. Use --resume to continue.")
            raise typer.Exit(BUDGET_EXHAUSTED_EXIT_CODE)
        elif exit_reason == LoopExitReason.INTERRUPTED:
            typer.echo("\nTeardown interrupted.", err=True)
            raise typer.Exit(130)
        elif exit_reason == LoopExitReason.ERROR:
            typer.echo("\nTeardown failed with errors.", err=True)
            raise typer.Exit(1)
        return

    # --- Fix-post-merge mode: run fix_quality_gate handler directly ---
    if fix_post_merge:
        if epic is None or story is None:
            typer.echo(
                "--fix-post-merge requires both --epic and --story.", err=True,
            )
            raise typer.Exit(1)

        from bmad_assist_lite.core.state import Phase, State
        from bmad_assist_lite.loop.dispatch import execute_phase, init_handlers

        init_handlers(app_config, project)

        story_id = f"{epic}.{story}"
        fix_state = State(
            current_epic=epic,
            current_story=story_id,
            current_phase=Phase.FIX_QUALITY_GATE,
            qa_retry_count=attempt,
        )

        typer.echo(
            f"Running fix-quality-gate for story {story_id} (attempt {attempt})"
        )

        result = execute_phase(fix_state)

        if file_handler is not None:
            file_handler.flush()
            file_handler.close()
            logging.getLogger().removeHandler(file_handler)

        if result.success:
            typer.echo("Fix phase completed successfully.")
        else:
            typer.echo("Fix phase failed.", err=True)
            raise typer.Exit(1)
        return

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
        logger.error("No backlog stories match the specified filters.")
        typer.echo("No backlog stories match the specified filters.", err=True)
        raise typer.Exit(1)

    # Filter by --story option — specifying a story implies single-story mode
    if story:
        single_story = True
        for e_num, story_list in list(epic_stories.items()):
            filtered = [(en, sn, fk) for en, sn, fk in story_list if sn == story]
            if filtered:
                epic_stories[e_num] = filtered
            else:
                del epic_stories[e_num]

        if not epic_stories:
            target_epic = epic if epic else "any epic"
            story_id = f"{epic}.{story}" if epic else str(story)
            current_status = sprint_status.get_story_status(story_id)
            if current_status:
                msg = (
                    f"Story {story_id} is not in backlog "
                    f"(current status: {current_status})."
                )
            else:
                msg = f"Story {story} not found in {target_epic} backlog stories."
            logger.error(msg)
            typer.echo(msg, err=True)
            raise typer.Exit(1)

    # Validate epic files exist for each epic
    planning_dir = paths.planning_artifacts
    epic_file_map: dict[int, Path] = {}

    skipped_epics: list[int] = []
    for e_num in epic_stories:
        epic_file = _find_epic_file(planning_dir, e_num)
        if epic_file is None or not _is_dedicated_epic_file(epic_file, e_num):
            logger.warning(
                "No dedicated epic file for epic %d (e.g. epic-%d.md) in %s — skipping.",
                e_num, e_num, planning_dir,
            )
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
        logger.error("No epics with dedicated epic files found. Cannot continue.")
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
        single_story=single_story,
        resume=resume_state is not None,
    )

    # Close file log handler
    if file_handler is not None:
        file_handler.flush()
        file_handler.close()
        logging.getLogger().removeHandler(file_handler)

    if exit_reason == LoopExitReason.COMPLETED:
        typer.echo("\nAll epics completed successfully!")
    elif exit_reason == LoopExitReason.BUDGET_EXHAUSTED:
        typer.echo("\nRun budget exhausted — stopped cleanly. Use --resume to continue.")
        raise typer.Exit(BUDGET_EXHAUSTED_EXIT_CODE)
    elif exit_reason == LoopExitReason.INTERRUPTED:
        typer.echo("\nLoop interrupted. Use --resume to continue.")
        raise typer.Exit(130)
    elif exit_reason == LoopExitReason.ERROR:
        logger.error("Loop failed with errors.")
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

    # Detect project toolchain for quality_gate and parallel defaults
    from bmad_assist_lite.core.toolchain import detect_install_command, detect_toolchain

    toolchain = detect_toolchain(project)
    install_cmd = detect_install_command(project)

    # Build quality_gate section — active if detected, commented out otherwise
    if toolchain.lint or toolchain.typecheck or toolchain.test:
        qg_lines = ["quality_gate:"]
        if toolchain.lint:
            qg_lines.append(f'  lint: "{toolchain.lint}"')
        if toolchain.typecheck:
            qg_lines.append(f'  typecheck: "{toolchain.typecheck}"')
        if toolchain.build:
            qg_lines.append(f'  build: "{toolchain.build}"')
        if toolchain.test:
            qg_lines.append(f'  test: "{toolchain.test}"')
        qg_lines.append("  command_timeout: 120     # per-command timeout in seconds")
        quality_gate_block = "\n".join(qg_lines)
    else:
        quality_gate_block = """\
# Fallback quality gate commands (auto-detected from build system if omitted).
# quality_gate:
#   lint: "ruff check src/"
#   typecheck: "mypy src/"
#   test: "pytest -q --tb=short --no-header"
#   command_timeout: 120     # per-command timeout in seconds"""

    # Build parallel section — use detected install command if available
    if install_cmd:
        parallel_block = f"""\
# Parallel story execution via git worktrees.
# parallel:
#   max_concurrency: 3               # max concurrent stories (1-5)
#   stagger_delay: 10.0              # seconds between spawns
#   copy_to_worktree:                # files/dirs to copy into each worktree
#     - ".env"
#     - "bmad-assist-lite.yaml"
#   setup_commands:                   # sequential shell commands run in each worktree
#     - "{install_cmd}"
#   validation_command: null          # smoke test command (e.g., "pytest -q -x")
#   bootstrap_timeout: 120           # per-command timeout in seconds for setup/validation"""
    else:
        parallel_block = """\
# Parallel story execution via git worktrees.
# parallel:
#   max_concurrency: 3               # max concurrent stories (1-5)
#   stagger_delay: 10.0              # seconds between spawns
#   copy_to_worktree:                # files/dirs to copy into each worktree
#     - ".env"
#     - "bmad-assist-lite.yaml"
#   setup_commands: []                # sequential shell commands run in each worktree
#   validation_command: null          # smoke test command (e.g., "pytest -q -x")
#   bootstrap_timeout: 120           # per-command timeout in seconds for setup/validation"""

    detected_label = ""
    if toolchain.lint or install_cmd:
        parts = []
        if toolchain.lint:
            # Infer language from the detected commands
            if "ruff" in (toolchain.lint or ""):
                parts.append("Python")
            elif "cargo" in (toolchain.lint or ""):
                parts.append("Rust")
            else:
                parts.append("Node.js")
        elif install_cmd:
            if "pip" in install_cmd:
                parts.append("Python")
            elif "cargo" in install_cmd:
                parts.append("Rust")
            else:
                parts.append("Node.js")
        detected_label = f" (detected: {', '.join(parts)} project)"

    default_config = f"""\
# bmad-assist-lite configuration{detected_label}
providers:
  master:
    provider: claude          # claude, gemini, codex, cursor
    model: claude-opus-4-6    # Pin Opus 4.6. To use 4.7, set `model: claude-opus-4-7` and add `effort: max`.
  multi:                      # independent reviewers/validators.
                              # Leaving this empty makes the master review its own
                              # work, which is not a review. Keep at least one entry
                              # that differs from `master` above.
    - provider: gemini
      model: gemini-3.1-pro-preview
    - provider: claude
      model: sonnet
  # cli_paths:               # Override CLI binary paths (useful when venv strips PATH)
  #   codex: "C:/path/to/codex.exe"
  #   gemini: "C:/path/to/gemini.cmd"
  #   cursor: "C:/path/to/cursor-agent.exe"

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

{quality_gate_block}

# Auto-commit story changes after quality gate pass/fail.
# auto_commit:
#   enabled: true            # default

# Uncomment to enable library documentation fetching from Context7.
# context_docs:
#   enabled: true
#   max_libs: 8              # max libraries to fetch docs for
#   max_tokens_per_lib: 5000 # max tokens of docs per library

{parallel_block}
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
    arch_files = paths.architecture_files or None

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
                architecture_files=arch_files,
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
    arch_files = paths.architecture_files or None

    from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

    try:
        docs = resolve_epic_docs(
            epic_num=epic_num,
            project_root=project,
            cache_dir=paths.cache_dir,
            epic_file=epic_file,
            architecture_files=arch_files,
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


if __name__ == "__main__":
    app()
