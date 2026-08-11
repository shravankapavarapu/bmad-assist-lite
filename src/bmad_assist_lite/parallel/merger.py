"""Sequential merge queue and git merge for parallel story execution.

Provides a ``MergeQueue`` that ensures stories are merged one at a time
into the base branch, and a ``merge_story()`` function that performs the
actual git merge with conflict detection and guaranteed abort on failure.

Conflict resolution via Claude CLI is opt-in: when enabled, merge conflicts
are sent to ``claude --print`` with full story context, and the resolved
content is applied, validated, and committed automatically.

Post-merge quality gates (``run_post_merge_qg()``) run lint, typecheck,
build, and test on the base branch after each successful merge so that
integration issues between parallel stories are caught immediately.

Post-merge fix (``run_post_merge_fix()``) invokes Claude CLI with the
``fix-quality-gate`` workflow to auto-fix integration failures on the base
branch.  ``update_sprint_status_done()`` marks a story as done in
``sprint-status.yaml``.

This module does **not** write to ``parallel-state.yaml``; state transitions
are the orchestrator's responsibility.

All git operations use ``_run_git()`` from ``git_ops`` — never raw
``subprocess``.  Shell quality-gate commands use ``run_command()`` from
``core/command_runner``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.core.command_runner import clean_test_output
from bmad_assist_lite.core.gate_runner import (
    GateClassification,
    GateCommand,
    make_base_bootstrap,
    run_gates,
)
from bmad_assist_lite.core.quality_gates import QualityGateEntry
from bmad_assist_lite.core.toolchain import detect_toolchain
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.git_ops import _run_git
from bmad_assist_lite.parallel.worktree_manager import (
    _branch_name,
    cleanup_worktree,
)
from bmad_assist_lite.providers._windows import (
    get_subprocess_kwargs,
    kill_process,
)

if TYPE_CHECKING:
    from bmad_assist_lite.core.config import Config
    from bmad_assist_lite.parallel.config import ParallelConfig

logger = logging.getLogger(__name__)


# ============================================================================
# MergeResult Model
# ============================================================================


class GateResult(BaseModel):
    """Immutable result of a single quality gate command execution.

    Attributes:
        name: Gate name (e.g. ``"Lint"``, ``"Typecheck"``).
        command: Shell command that was executed.
        passed: ``True`` when the command exited with code 0.
        exit_code: Raw process exit code.
        stdout: Captured standard output.
        stderr: Captured standard error.
        duration_ms: Wall-clock execution time in milliseconds.
        classification: Env-vs-real classification from the shared gate runner
            (``real``, ``env`` or ``env-blocked``). Empty when the gate passed.

    """

    model_config = ConfigDict(frozen=True)

    name: str
    command: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    classification: str = ""

    @property
    def status_label(self) -> str:
        """Return ``PASS`` or ``FAIL`` \u2014 never ``unknown``."""
        return "PASS" if self.passed else "FAIL"


class PostMergeQGResult(BaseModel):
    """Immutable result of the post-merge quality gate run.

    Attributes:
        all_passed: ``True`` only when every gate passed.
        story_id: The story identifier the QG ran for.
        gate_results: Per-gate execution results.
        duration_ms: Total wall-clock time across all gates.
        env_blocked: ``True`` when the run ended blocked on the environment
            rather than on the code.
        classification: Overall ``real``/``env`` classification, or ``None``
            when nothing failed.
        failure_reason: Why the run failed when no gate command ever ran.
            Never empty on a failed run \u2014 that emptiness is what produced
            "failed gates: unknown".

    """

    model_config = ConfigDict(frozen=True)

    all_passed: bool
    story_id: str
    gate_results: list[GateResult] = []
    duration_ms: int = 0
    env_blocked: bool = False
    classification: str | None = None
    failure_reason: str = ""


class MergeResult(BaseModel):
    """Immutable result of a single merge attempt.

    Attributes:
        success: ``True`` when the merge completed without conflicts.
        story_id: The story identifier that was merged.
        conflict_files: List of conflicting file paths (empty on success).
        error: Human-readable error description, or ``None`` on success.
        qg_result: Post-merge quality gate result, or ``None`` when QG
            was not run (e.g. merge failed).

    """

    model_config = ConfigDict(frozen=True)

    success: bool
    story_id: str
    conflict_files: list[str] = []
    error: str | None = None
    qg_result: PostMergeQGResult | None = None


# ============================================================================
# ConflictResolutionResult Model
# ============================================================================


class ConflictResolutionResult(BaseModel):
    """Immutable result of a conflict resolution attempt via Claude CLI.

    Attributes:
        resolved: ``True`` when all conflicts were successfully resolved.
        files_resolved: List of file paths that were resolved.
        files_with_residual_markers: Files still containing conflict markers.
        error: Human-readable error description, or ``None`` on success.

    """

    model_config = ConfigDict(frozen=True)

    resolved: bool
    files_resolved: list[str] = []
    files_with_residual_markers: list[str] = []
    error: str | None = None


# ============================================================================
# Conflict Resolution via Claude CLI
# ============================================================================


def _build_resolution_prompt(
    story_context: str,
    conflict_files: list[str],
    file_contents: dict[str, str],
) -> str:
    """Build a structured prompt for Claude CLI conflict resolution.

    Args:
        story_context: Story title/description for context.
        conflict_files: List of conflicted file paths.
        file_contents: Mapping of file path to raw conflicted content.

    Returns:
        A structured prompt string for Claude CLI.

    """
    file_sections = []
    for filepath in conflict_files:
        content = file_contents.get(filepath, "")
        file_sections.append(f"File: {filepath}\n```\n{content}\n```")

    files_block = "\n\n".join(file_sections)

    return (
        "You are resolving git merge conflicts for a story implementation.\n\n"
        f"Story Context:\n{story_context}\n\n"
        f"The following files have merge conflicts:\n{files_block}\n\n"
        "Instructions:\n"
        "- Resolve each conflict by combining changes appropriately\n"
        "- Output the fully resolved content for EVERY file listed above\n"
        "- Wrap each file's content with these EXACT delimiters:\n"
        "  --- FILE: path/to/file ---\n"
        "  (resolved content here)\n"
        "  --- END FILE ---\n"
        "- Do NOT include conflict markers (<<<<<<<, =======, >>>>>>>) "
        "in your output\n"
        "- Do NOT include markdown code fences or explanatory text "
        "inside the file delimiters\n"
    )


def _parse_resolution_output(
    output: str,
    conflict_files: list[str],
) -> dict[str, str]:
    """Parse Claude CLI output into per-file resolved content.

    Extracts content between ``--- FILE: <path> ---`` and
    ``--- END FILE ---`` delimiters.  Validates that every file from
    ``conflict_files`` has a corresponding output section.

    Args:
        output: Raw Claude CLI stdout.
        conflict_files: Expected list of conflicted file paths.

    Returns:
        Mapping of file path to resolved content.

    Raises:
        ParallelError: If any expected file is missing from output.

    """
    pattern = r"---\s*FILE:\s*(.+?)\s*---\r?\n(.*?)---\s*END FILE\s*---"
    matches = re.findall(pattern, output, re.DOTALL)

    # Build a lookup keyed by normalized (forward-slash) paths
    parsed: dict[str, str] = {}
    for filepath, content in matches:
        normalized = filepath.strip().replace("\\", "/")
        parsed[normalized] = content

    # Map original conflict_files keys → resolved content using
    # normalized comparison, so callers can access by original key.
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for original in conflict_files:
        normalized = original.replace("\\", "/")
        if normalized in parsed:
            resolved[original] = parsed[normalized]
        else:
            missing.append(original)

    if missing:
        raise ParallelError(
            f"Claude CLI output missing resolution for files: {missing}"
        )

    return resolved


def _has_residual_markers(content: str) -> bool:
    """Check whether content still contains git conflict markers.

    A file has residual conflicts if it contains BOTH ``<<<<<<<`` AND
    ``>>>>>>>``.  The ``=======`` alone is too ambiguous (appears in
    markdown/docs).

    Args:
        content: File content to check.

    Returns:
        ``True`` if residual conflict markers are detected.

    """
    return "<<<<<<<" in content and ">>>>>>>" in content


def resolve_conflicts(
    story_id: str,
    project_root: Path,
    conflict_files: list[str],
    story_context: str,
    timeout: int = 120,
) -> ConflictResolutionResult:
    """Resolve merge conflicts using Claude CLI.

    Reads conflicted files, builds a structured prompt with story context,
    invokes ``claude --print`` via subprocess, parses the output, validates
    for residual conflict markers, and commits the resolution.

    On any failure, ``git merge --abort`` is guaranteed via ``try...finally``
    to prevent leaving the repository in a dirty merge state.

    Args:
        story_id: Story identifier (e.g. ``"4.2"``).
        project_root: Path to the main git repository.
        conflict_files: List of file paths with merge conflicts.
        story_context: Story title and description for prompt context.
        timeout: Timeout in seconds for Claude CLI invocation.

    Returns:
        A ``ConflictResolutionResult`` describing the outcome.

    Raises:
        ParallelError: If Claude CLI is not found on PATH.

    """
    tag = f"[MERGE|{story_id}]"

    if not conflict_files:
        logger.warning("%s resolve_conflicts called with empty file list", tag)
        return ConflictResolutionResult(
            resolved=False,
            error="No conflict files provided",
        )

    logger.info("%s Attempting conflict resolution for %d file(s)", tag, len(conflict_files))

    resolved_ok = False
    try:
        # ------------------------------------------------------------------
        # Step 1: Read conflicted file contents
        # ------------------------------------------------------------------
        file_contents: dict[str, str] = {}
        for filepath in conflict_files:
            full_path = project_root / filepath
            try:
                file_contents[filepath] = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.error("%s Failed to read conflict file %s: %s", tag, filepath, exc)
                return ConflictResolutionResult(
                    resolved=False,
                    error=f"Failed to read conflict file {filepath}: {exc}",
                )

        # ------------------------------------------------------------------
        # Step 2: Build prompt and invoke Claude CLI via Popen for
        # proper process-tree cleanup on timeout (Architecture Rule 5)
        # ------------------------------------------------------------------
        prompt = _build_resolution_prompt(story_context, conflict_files, file_contents)
        logger.info("%s Invoking Claude CLI with timeout=%ds", tag, timeout)

        try:
            proc = subprocess.Popen(
                ["claude", "--print"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(project_root),
                text=True,
                encoding="utf-8",
                **get_subprocess_kwargs(),
            )
        except FileNotFoundError as exc:
            raise ParallelError(
                f"{tag} Claude CLI ('claude') not found on PATH. "
                "Ensure Claude CLI is installed and available."
            ) from exc

        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("%s Claude CLI timed out after %ds — killing process tree", tag, timeout)
            kill_process(proc)
            proc.wait()
            return ConflictResolutionResult(
                resolved=False,
                error=f"Claude CLI timed out after {timeout}s",
            )

        if proc.returncode != 0:
            error_msg = stderr.strip() or stdout.strip()
            logger.error("%s Claude CLI failed (rc=%d): %s", tag, proc.returncode, error_msg)
            return ConflictResolutionResult(
                resolved=False,
                error=f"Claude CLI failed (rc={proc.returncode}): {error_msg}",
            )

        # ------------------------------------------------------------------
        # Step 3: Parse output and extract resolved content
        # ------------------------------------------------------------------
        try:
            resolved_contents = _parse_resolution_output(stdout, conflict_files)
        except ParallelError as exc:
            logger.error("%s Resolution output parsing failed: %s", tag, exc)
            return ConflictResolutionResult(
                resolved=False,
                error=str(exc),
            )

        # ------------------------------------------------------------------
        # Step 4: Write resolved content and check for residual markers
        # ------------------------------------------------------------------
        files_with_residual: list[str] = []
        for filepath in conflict_files:
            content = resolved_contents[filepath]
            if _has_residual_markers(content):
                files_with_residual.append(filepath)

        if files_with_residual:
            logger.warning(
                "%s Residual conflict markers in: %s", tag, files_with_residual
            )
            return ConflictResolutionResult(
                resolved=False,
                files_with_residual_markers=files_with_residual,
                error=(
                    f"Residual conflict markers in {len(files_with_residual)} "
                    f"file(s): {files_with_residual}"
                ),
            )

        # Write resolved content
        for filepath in conflict_files:
            full_path = project_root / filepath
            full_path.write_text(resolved_contents[filepath], encoding="utf-8")
            logger.info("%s Wrote resolved content to %s", tag, filepath)

        # ------------------------------------------------------------------
        # Step 5: Stage and commit
        # ------------------------------------------------------------------
        for filepath in conflict_files:
            _run_git(["add", filepath], cwd=project_root)

        _run_git(["commit", "--no-edit"], cwd=project_root)
        logger.info("%s Conflict resolution committed successfully", tag)

        resolved_ok = True
        return ConflictResolutionResult(
            resolved=True,
            files_resolved=list(conflict_files),
        )

    finally:
        if not resolved_ok:
            logger.info("%s Aborting merge after failed resolution", tag)
            _run_git(["merge", "--abort"], cwd=project_root, check=False)


# ============================================================================
# Post-Merge Cleanup Helper
# ============================================================================


def _cleanup_after_merge(
    story_id: str,
    branch: str,
    project_root: Path,
    tag: str,
) -> None:
    """Delete the story branch and clean up its worktree (best-effort).

    Args:
        story_id: Story identifier.
        branch: Branch name to delete.
        project_root: Path to the main git repository.
        tag: Logging tag prefix.

    """
    del_result = _run_git(["branch", "-d", branch], cwd=project_root, check=False)
    if del_result.returncode != 0:
        logger.warning(
            "%s Branch deletion failed (rc=%d): %s (non-fatal)",
            tag,
            del_result.returncode,
            del_result.stderr.strip(),
        )

    try:
        cleanup_worktree(story_id, project_root)
        logger.info("%s Worktree cleaned up for %s", tag, story_id)
    except Exception:
        logger.warning(
            "%s Worktree cleanup failed for %s (non-fatal)",
            tag,
            story_id,
            exc_info=True,
        )


# ============================================================================
# Core Merge Function
# ============================================================================


def merge_story(
    story_id: str,
    project_root: Path,
    *,
    expected_branch: str | None = None,
    resolve: bool = False,
    story_context: str = "",
    conflict_resolution_timeout: int = 120,
) -> MergeResult:
    """Merge a story branch into the current base branch.

    Performs the following sequence:

    1. Verify HEAD is on the expected base branch (not detached or wrong branch).
    2. ``git merge --no-edit parallel/{id}`` with ``check=False``.
    3. On success: delete the branch, clean up the worktree, return success.
    4. On conflict: if ``resolve=True``, attempt Claude CLI resolution;
       otherwise capture conflict files and guarantee ``git merge --abort``.

    Args:
        story_id: Story identifier (e.g. ``"3.1"``).
        project_root: Path to the main git repository.
        expected_branch: If provided, verify HEAD is on this branch before
            merging.  When ``None``, only a detached-HEAD check is performed.
        resolve: If ``True``, attempt Claude CLI conflict resolution
            instead of immediately aborting on conflict.
        story_context: Story title/description for conflict resolution
            prompt context.  Only used when ``resolve=True``.
        conflict_resolution_timeout: Timeout in seconds for Claude CLI
            invocation.  Only used when ``resolve=True``.

    Returns:
        A ``MergeResult`` describing the outcome.

    Raises:
        ParallelError: If HEAD is detached, on the wrong branch, or if a
            fatal (non-conflict) git error occurs.

    """
    branch = _branch_name(story_id)
    tag = f"[MERGE|{story_id}]"

    # ------------------------------------------------------------------
    # Step 0: Verify we are on the expected base branch
    # ------------------------------------------------------------------
    head_result = _run_git(
        ["rev-parse", "--abbrev-ref", "HEAD"],
        cwd=project_root,
    )
    current_branch = head_result.stdout.strip()

    if current_branch == "HEAD":
        raise ParallelError(
            f"{tag} Cannot merge: HEAD is detached (expected a base branch)"
        )

    if expected_branch is not None and current_branch != expected_branch:
        raise ParallelError(
            f"{tag} Cannot merge: on branch '{current_branch}' "
            f"but expected '{expected_branch}'"
        )

    logger.info("%s Merging branch %s into %s", tag, branch, current_branch)

    # ------------------------------------------------------------------
    # Step 0.5: Ensure clean working tree before merge
    # ------------------------------------------------------------------
    # Git refuses to merge when tracked files have uncommitted changes that
    # the merge would modify.  Common culprit: sprint-status.yaml left dirty
    # by _update_epic_sprint_status() from a previous parallel run.
    status_result = _run_git(["status", "--porcelain"], cwd=project_root, check=False)
    if status_result.stdout.strip():
        logger.warning(
            "%s Dirty working tree detected before merge — auto-committing tracked changes",
            tag,
        )
        _run_git(["add", "-u"], cwd=project_root, check=False)
        pre_commit = _run_git(
            ["commit", "-m", "chore: auto-commit dirty files before parallel merge"],
            cwd=project_root,
            check=False,
        )
        if pre_commit.returncode == 0:
            logger.info("%s Pre-merge auto-commit succeeded", tag)
        else:
            logger.warning(
                "%s Pre-merge auto-commit returned rc=%d: %s — proceeding anyway",
                tag,
                pre_commit.returncode,
                pre_commit.stderr.strip(),
            )

    # ------------------------------------------------------------------
    # Step 1: Attempt the merge
    # ------------------------------------------------------------------
    merge_result = _run_git(
        ["merge", "--no-edit", branch],
        cwd=project_root,
        check=False,
    )

    # ------------------------------------------------------------------
    # Step 2: Handle success (returncode 0)
    # ------------------------------------------------------------------
    if merge_result.returncode == 0:
        logger.info("%s Merge succeeded — cleaning up branch %s", tag, branch)
        _cleanup_after_merge(story_id, branch, project_root, tag)
        return MergeResult(success=True, story_id=story_id)

    # ------------------------------------------------------------------
    # Step 3: Handle non-zero exit — distinguish conflict from fatal error
    # ------------------------------------------------------------------
    merge_head = project_root / ".git" / "MERGE_HEAD"
    stdout_text = merge_result.stdout or ""
    stderr_text = merge_result.stderr or ""
    combined = stdout_text + stderr_text

    is_conflict = merge_head.exists() or "CONFLICT" in combined

    if not is_conflict:
        logger.error(
            "%s git merge failed (not a conflict): %s", tag, combined.strip(),
        )
        raise ParallelError(
            f"{tag} git merge failed (not a conflict): {combined.strip()}"
        )

    # ------------------------------------------------------------------
    # Step 4: Conflict path — capture files, then resolve or abort
    # ------------------------------------------------------------------
    logger.warning("%s Merge conflict detected for branch %s", tag, branch)
    conflict_files: list[str] = []

    try:
        diff_result = _run_git(
            ["diff", "--name-only", "--diff-filter=U"],
            cwd=project_root,
            check=False,
        )
        raw_files = diff_result.stdout.strip()
        if raw_files:
            conflict_files = raw_files.splitlines()
        logger.info("%s Conflict files: %s", tag, conflict_files)

        # ----------------------------------------------------------
        # Step 5: Attempt resolution if enabled
        # ----------------------------------------------------------
        if resolve and conflict_files:
            logger.info("%s Attempting Claude CLI conflict resolution", tag)
            # resolve_conflicts() guarantees merge abort via its own
            # try...finally — no double-abort risk here.
            resolution = resolve_conflicts(
                story_id=story_id,
                project_root=project_root,
                conflict_files=conflict_files,
                story_context=story_context,
                timeout=conflict_resolution_timeout,
            )

            if resolution.resolved:
                logger.info(
                    "%s Conflict resolution succeeded — cleaning up branch %s",
                    tag,
                    branch,
                )
                _cleanup_after_merge(story_id, branch, project_root, tag)
                return MergeResult(success=True, story_id=story_id)

            # Resolution failed — merge already aborted by resolve_conflicts()
            return MergeResult(
                success=False,
                story_id=story_id,
                conflict_files=conflict_files,
                error=resolution.error or "Conflict resolution failed",
            )

    except ParallelError:
        # resolve_conflicts() already aborted merge in its finally block;
        # just re-raise (e.g. Claude CLI not found).
        raise

    finally:
        # Only abort if we did NOT go through the resolution path
        # (resolve_conflicts handles its own abort guarantee).
        merge_head = project_root / ".git" / "MERGE_HEAD"
        if merge_head.exists():
            logger.info("%s Aborting merge to restore clean state", tag)
            abort_result = _run_git(
                ["merge", "--abort"], cwd=project_root, check=False
            )
            if abort_result.returncode != 0:
                logger.warning(
                    "%s git merge --abort failed (rc=%d): %s — "
                    "repository may be in a dirty state",
                    tag,
                    abort_result.returncode,
                    abort_result.stderr.strip(),
                )

    return MergeResult(
        success=False,
        story_id=story_id,
        conflict_files=conflict_files,
        error=(
            f"Merge conflict in {len(conflict_files)} file(s)"
            if conflict_files
            else "Merge conflict detected (conflict files could not be determined)"
        ),
    )


# ============================================================================
# Post-Merge Quality Gate
# ============================================================================


def _resolve_qg_commands(
    project_root: Path,
    config: Config | None = None,
) -> list[QualityGateEntry]:
    """Resolve quality gate commands from config or auto-detected toolchain.

    Priority order:

    1. ``config.quality_gate`` section — build entries from ``lint``,
       ``typecheck``, ``build``, and **test** fields.  For the test command
       we prefer ``test`` (full suite) over ``test_unit`` because post-merge
       QG runs at the project level for integration validation.
    2. ``detect_toolchain(project_root)`` — auto-detected commands.

    Returns an empty list when no commands are found (the caller treats
    this as an all-pass).

    Args:
        project_root: Path to the project root directory.
        config: Optional loaded configuration.

    Returns:
        Ordered list of :class:`QualityGateEntry` objects.

    """
    # Priority 1: Config quality_gate section
    if config is not None and config.quality_gate is not None:
        qg = config.quality_gate
        entries: list[QualityGateEntry] = []
        if qg.lint:
            entries.append(QualityGateEntry(name="Lint", command=qg.lint, status="PENDING"))
        if qg.typecheck:
            entries.append(
                QualityGateEntry(name="Typecheck", command=qg.typecheck, status="PENDING")
            )
        if qg.build:
            entries.append(QualityGateEntry(name="Build", command=qg.build, status="PENDING"))
        # Post-merge QG prefers full test suite over unit-only
        test_cmd = qg.test or qg.test_unit
        if test_cmd:
            entries.append(QualityGateEntry(name="Tests", command=test_cmd, status="PENDING"))
        if entries:
            return entries

    # Priority 2: Auto-detect from project root
    tc = detect_toolchain(project_root)
    entries = []
    if tc.lint:
        entries.append(QualityGateEntry(name="Lint", command=tc.lint, status="PENDING"))
    if tc.typecheck:
        entries.append(
            QualityGateEntry(name="Typecheck", command=tc.typecheck, status="PENDING")
        )
    if tc.build:
        entries.append(QualityGateEntry(name="Build", command=tc.build, status="PENDING"))
    test_cmd = tc.test or tc.test_unit
    if test_cmd:
        entries.append(QualityGateEntry(name="Tests", command=test_cmd, status="PENDING"))
    return entries


def _write_post_merge_failure_report(
    story_id: str,
    project_root: Path,
    qg_result: PostMergeQGResult,
) -> Path:
    """Write a failure report for post-merge quality gate failures.

    Creates a Markdown report at
    ``.bmad-assist-lite/cache/post-merge-qg-failures-{story_id}.md``
    containing per-gate details (command, exit code, stdout/stderr).

    Args:
        story_id: The story identifier.
        project_root: Path to the project root directory.
        qg_result: The post-merge QG result containing gate details.

    Returns:
        Path to the written report file.

    """
    cache_dir = project_root / ".bmad-assist-lite" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    report_path = cache_dir / f"post-merge-qg-failures-{story_id}.md"

    lines = [f"# Post-Merge Quality Gate Failures — Story {story_id}\n"]
    for gate in qg_result.gate_results:
        if not gate.passed:
            lines.append(f"\n## Failed: {gate.name}\n")
            lines.append(f"**Command:** `{gate.command}`\n")
            lines.append(f"**Exit Code:** {gate.exit_code}\n")
            raw_output = ((gate.stdout or "") + "\n" + (gate.stderr or "")).strip()
            output = clean_test_output(raw_output)
            lines.append(f"**Output:**\n```\n{output}\n```\n")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    tag = f"[QG|post-merge|{story_id}]"
    logger.info("%s Wrote failure report to %s", tag, report_path)
    return report_path


def run_post_merge_qg(
    story_id: str,
    project_root: Path,
    config: Config | None = None,
    command_timeout: int = 120,
) -> PostMergeQGResult:
    """Run post-merge quality gates on the base branch.

    Executes lint, typecheck, build, and test commands on the base branch
    after a successful merge.  Commands are sourced from the config
    ``quality_gate`` section first, falling back to auto-detected toolchain.

    Normal gate failures are captured in the return model — they are **not**
    raised as exceptions.

    Args:
        story_id: Story identifier that was just merged.
        project_root: Path to the project root (base branch checkout).
        config: Optional loaded configuration for command sourcing.
        command_timeout: Per-command timeout in seconds; overridden by
            ``config.quality_gate.command_timeout`` when available.

    Returns:
        A :class:`PostMergeQGResult` describing the outcome.

    """
    tag = f"[QG|post-merge|{story_id}]"

    commands = _resolve_qg_commands(project_root, config)
    if not commands:
        logger.info("%s No QG commands found — passing by default", tag)
        return PostMergeQGResult(
            all_passed=True,
            story_id=story_id,
            gate_results=[],
            duration_ms=0,
        )

    # Resolve command_timeout from config if available
    if config is not None and config.quality_gate is not None:
        command_timeout = config.quality_gate.command_timeout

    # The merge target was never bootstrapped before its gates ran — the verified
    # root cause of a post-merge gate failing for an environment reason and being
    # reported as a code failure. Reuses the worktree bootstrap pipeline.
    run = run_gates(
        [GateCommand(name=e.name, command=e.command) for e in commands],
        project_root,
        timeout=command_timeout,
        label=f"post-merge:{story_id}",
        bootstrap=make_base_bootstrap(project_root, config),
        bootstrap_first=True,
        report=False,
    )

    gate_results: list[GateResult] = [
        GateResult(
            name=o.name,
            command=o.command,
            passed=o.passed,
            exit_code=o.exit_code,
            stdout=o.stdout,
            stderr=o.stderr,
            duration_ms=o.duration_ms,
            classification=run.classification_label(o),
        )
        for o in run.outcomes
    ]
    for gate in gate_results:
        icon = "\u2714" if gate.passed else "\u2718"
        logger.info(
            "%s %s %s: %s [%s] command: %s",
            tag,
            icon,
            gate.name,
            gate.status_label,
            gate.classification,
            gate.command,
        )

    result = PostMergeQGResult(
        all_passed=run.all_passed,
        story_id=story_id,
        gate_results=gate_results,
        duration_ms=run.duration_ms,
        env_blocked=run.env_blocked,
        classification=(
            run.overall_classification.value
            if run.overall_classification is not None
            else None
        ),
    )

    if not result.all_passed:
        try:
            _write_post_merge_failure_report(story_id, project_root, result)
        except OSError:
            logger.warning(
                "%s Failed to write failure report (non-fatal)", tag, exc_info=True
            )

    return result



def _assert_fix_routable(qg_result: PostMergeQGResult) -> bool:
    """Return True when a failed post-merge gate may reach the LLM fixer.

    Runtime invariant, asserted where the routing decision is made rather than only
    proven statically: an ``env``-classified failure is never handed to the fixer,
    but it is always reported.

    Args:
        qg_result: The failed post-merge quality gate result.

    Returns:
        ``True`` when the failure is a real code failure.

    """
    if qg_result.classification == GateClassification.ENV.value or qg_result.env_blocked:
        logger.warning(
            "[QG|post-merge|%s] env-blocked \u2014 not routed to the fixer: %s",
            qg_result.story_id,
            "; ".join(
                f"{g.name} [{g.classification}] command: {g.command}"
                for g in qg_result.gate_results
                if not g.passed
            )
            or qg_result.failure_reason,
        )
        return False
    return True


# ============================================================================
# Post-Merge Fix via Claude CLI
# ============================================================================


def run_post_merge_fix(
    story_id: str,
    project_root: Path,
    config: Config | None = None,
    attempt: int = 1,
    timeout: int = 300,
) -> PostMergeQGResult:
    """Attempt to fix post-merge quality gate failures via subprocess.

    Spawns ``bmad-assist-lite run --fix-post-merge`` which runs the
    ``fix_quality_gate`` handler through the full provider infrastructure
    (master LLM with tool use — read, edit, run commands).  After the
    subprocess exits, any file changes are committed and the quality gate
    is re-run to verify the fix.

    Args:
        story_id: Story identifier (e.g. ``"4.4"``).
        project_root: Path to the main git repository (base branch).
        config: Optional configuration for QG command sourcing.
        attempt: Current fix attempt number (1-based).  When ``> 1``,
            the handler receives retry context to try a different strategy.
        timeout: Timeout in seconds for the fix subprocess.

    Returns:
        A :class:`PostMergeQGResult` from the re-run quality gate.
        ``all_passed=False`` when the fix subprocess fails or produces
        no changes.

    """
    tag = f"[FIX-QG|post-merge|{story_id}]"
    logger.info("%s Starting fix attempt #%d", tag, attempt)

    # ------------------------------------------------------------------
    # Step 1: Copy failure report to the path the handler expects
    # ------------------------------------------------------------------
    cache_dir = project_root / ".bmad-assist-lite" / "cache"
    post_merge_report = cache_dir / f"post-merge-qg-failures-{story_id}.md"
    handler_report = cache_dir / f"qa-failures-{story_id}.md"

    if post_merge_report.exists():
        try:
            import shutil

            shutil.copy2(post_merge_report, handler_report)
            logger.info(
                "%s Copied failure report to %s", tag, handler_report,
            )
        except OSError as exc:
            logger.warning(
                "%s Failed to copy failure report: %s (handler will see 'no report')",
                tag,
                exc,
            )
    else:
        logger.warning("%s No failure report at %s", tag, post_merge_report)

    # ------------------------------------------------------------------
    # Step 2: Parse story_id into epic and story numbers
    # ------------------------------------------------------------------
    parts = story_id.split(".")
    if len(parts) != 2:  # noqa: PLR2004
        logger.error("%s Cannot parse story_id %r into epic.story", tag, story_id)
        return PostMergeQGResult(
            all_passed=False, story_id=story_id, failure_reason=f"cannot parse story_id {story_id!r} into epic.story"
        )
    epic_num, story_num = parts[0], parts[1]

    # ------------------------------------------------------------------
    # Step 3: Spawn fix subprocess via Popen
    # ------------------------------------------------------------------
    import sys

    exec_args = [
        sys.executable, "-m", "bmad_assist_lite", "run",
        "--project", str(project_root),
        "--epic", epic_num,
        "--story", story_num,
        "--fix-post-merge",
        "--attempt", str(attempt),
    ]

    env = {
        **os.environ,
        "BMAD_PARALLEL_MODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }

    logger.info("%s Spawning fix subprocess (timeout=%ds)", tag, timeout)
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            exec_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(project_root),
            text=True,
            encoding="utf-8",
            env=env,
            **get_subprocess_kwargs(),
        )
    except FileNotFoundError as exc:
        raise ParallelError(
            f"{tag} Python executable not found: {sys.executable}"
        ) from exc

    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning(
            "%s Fix subprocess timed out after %ds — killing", tag, timeout,
        )
        kill_process(proc)
        proc.wait()
        return PostMergeQGResult(
            all_passed=False, story_id=story_id, failure_reason=f"fix subprocess timed out after {timeout}s"
        )
    finally:
        if proc is not None and proc.poll() is None:
            kill_process(proc)

    if stdout:
        for line in stdout.strip().splitlines():
            logger.info("%s [subprocess] %s", tag, line)

    if proc.returncode != 0:
        logger.error(
            "%s Fix subprocess failed (rc=%d)", tag, proc.returncode,
        )
        return PostMergeQGResult(
            all_passed=False, story_id=story_id, failure_reason=f"fix subprocess failed (rc={proc.returncode})"
        )

    logger.info("%s Fix subprocess completed successfully", tag)

    # ------------------------------------------------------------------
    # Step 4: Check for changes, commit if any
    # ------------------------------------------------------------------
    status_result = _run_git(
        ["status", "--porcelain"], cwd=project_root, check=False,
    )
    if not status_result.stdout.strip():
        logger.warning(
            "%s Fix subprocess produced no changes — treating as failed attempt",
            tag,
        )
        return PostMergeQGResult(
            all_passed=False, story_id=story_id, failure_reason="fix subprocess produced no changes"
        )

    commit_msg = f"fix: post-merge integration fix for story {story_id}"
    try:
        _run_git(["add", "-A"], cwd=project_root)
        _run_git(["commit", "-m", commit_msg], cwd=project_root)
        logger.info("%s Committed fix: %s", tag, commit_msg)
    except ParallelError as exc:
        logger.error("%s Git commit failed: %s", tag, exc)
        return PostMergeQGResult(
            all_passed=False, story_id=story_id, failure_reason=f"git commit of the fix failed: {exc}"
        )

    # ------------------------------------------------------------------
    # Step 5: Re-run quality gate to verify fix
    # ------------------------------------------------------------------
    logger.info("%s Re-running post-merge quality gate", tag)
    qg_result = run_post_merge_qg(story_id, project_root, config)
    if qg_result.all_passed:
        logger.info("%s Quality gate passed after fix attempt #%d", tag, attempt)
    else:
        logger.warning(
            "%s Quality gate still failing after fix attempt #%d", tag, attempt,
        )

    return qg_result


# ============================================================================
# Sprint Status Update Helper
# ============================================================================


def update_sprint_status_done(
    story_id: str,
    project_root: Path,
    all_done_ids: set[str] | None = None,
) -> None:
    """Mark stories as ``done`` in ``sprint-status.yaml`` and commit.

    Marks ``story_id`` plus any previously completed stories in
    ``all_done_ids`` as done.  This prevents a merge from overwriting
    earlier stories' statuses back to ``backlog`` — each story branch
    carries a stale snapshot of sprint-status.yaml from when it was
    created, and merging it can revert other stories' progress.

    After writing, commits ``sprint-status.yaml`` so the working tree is
    clean before the next story merge.

    Sprint-status update failures are **non-fatal**: any exception is caught,
    logged as a warning, and not re-raised.

    Args:
        story_id: Story identifier being marked done (e.g. ``"4.4"``).
        project_root: Path to the project root directory.
        all_done_ids: Set of all story IDs that have completed so far.
            Each is re-marked ``done`` to repair any stale status
            introduced by the merge.

    """
    tag = f"[SPRINT|{story_id}]"
    try:
        from bmad_assist_lite.core.sprint_status import (
            get_sprint_status_path,
            load_sprint_status,
            save_sprint_status,
        )

        path = get_sprint_status_path(project_root)
        sprint_status = load_sprint_status(path)

        ids_to_mark = {story_id}
        if all_done_ids:
            ids_to_mark |= all_done_ids

        for sid in ids_to_mark:
            sprint_status.set_story_status(sid, "done")

        save_sprint_status(sprint_status, path)
        logger.info("%s Updated sprint-status: %s → done", tag, sorted(ids_to_mark))

        _run_git(["add", str(path)], cwd=project_root, check=False)
        _run_git(
            ["commit", "-m", f"chore: mark story {story_id} done in sprint-status"],
            cwd=project_root,
            check=False,
        )
    except Exception:
        logger.warning(
            "%s Failed to update sprint-status (non-fatal)",
            tag,
            exc_info=True,
        )


# ============================================================================
# MergeQueue — Async Sequential Queue
# ============================================================================


class MergeQueue:
    """Async queue that enforces one-at-a-time merge execution.

    Stories are enqueued as they complete, and ``process_next()``
    dequeues and merges them sequentially under an ``asyncio.Lock``.

    Args:
        project_root: Path to the main git repository.
        config: Optional configuration for post-merge quality gate
            command sourcing.
        parallel_config: Optional parallel execution configuration
            for accessing ``post_merge_fix_retries``.

    """

    def __init__(
        self,
        project_root: Path,
        config: Config | None = None,
        parallel_config: ParallelConfig | None = None,
    ) -> None:
        """Initialise the merge queue for the given repository."""
        self._project_root = project_root
        self._config = config
        self._parallel_config = parallel_config
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._done_ids: set[str] = set()

    async def enqueue(self, story_id: str) -> None:
        """Add a story to the merge queue.

        Args:
            story_id: Story identifier to queue for merge.

        """
        logger.info("[MERGE|%s] Enqueued for merge", story_id)
        await self._queue.put(story_id)

    async def process_next(self) -> MergeResult | None:
        """Dequeue and merge the next story, if any.

        Acquires the internal lock to guarantee only one merge runs at
        a time.  Uses ``get_nowait()`` to avoid blocking on an empty
        queue.

        After a successful merge, runs post-merge quality gates on the
        base branch.  The ``qg_result`` field on the returned
        ``MergeResult`` conveys the quality gate outcome to the caller.

        Returns:
            A ``MergeResult`` on success/conflict, or ``None`` when the
            queue is empty.

        """
        async with self._lock:
            try:
                story_id = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None

            logger.info("[MERGE|%s] Processing merge", story_id)
            try:
                cr_timeout = (
                    self._parallel_config.conflict_resolution_timeout
                    if self._parallel_config is not None
                    else 120
                )
                try:
                    result = await asyncio.to_thread(
                        merge_story,
                        story_id,
                        self._project_root,
                        resolve=True,
                        conflict_resolution_timeout=cr_timeout,
                    )
                except ParallelError as exc:
                    logger.error(
                        "[MERGE|%s] Fatal merge error: %s", story_id, exc,
                    )
                    return MergeResult(
                        success=False,
                        story_id=story_id,
                        error=str(exc),
                    )
                except Exception as exc:
                    logger.error(
                        "[MERGE|%s] Unexpected merge error: %s",
                        story_id,
                        exc,
                        exc_info=True,
                    )
                    return MergeResult(
                        success=False,
                        story_id=story_id,
                        error=f"Unexpected error: {exc}",
                    )

                # Run post-merge QG only on successful merge
                if result.success:
                    try:
                        qg_result = await asyncio.to_thread(
                            run_post_merge_qg,
                            story_id,
                            self._project_root,
                            self._config,
                        )
                        result = result.model_copy(update={"qg_result": qg_result})
                    except Exception:
                        logger.error(
                            "[MERGE|%s] Post-merge QG failed with unexpected error "
                            "(merge was successful, returning result without QG)",
                            story_id,
                            exc_info=True,
                        )

                return result
            finally:
                self._queue.task_done()

    async def process_merge_with_fix(self) -> MergeResult | None:
        """Dequeue and merge the next story, with automatic fix retries.

        Wraps :meth:`process_next` to add a post-merge quality gate fix
        loop.  When the quality gate fails after merge, ``run_post_merge_fix()``
        is invoked up to ``post_merge_fix_retries`` times (from
        ``ParallelConfig``).

        On success (QG passes, with or without fix), the story is marked
        as ``done`` in ``sprint-status.yaml`` via
        :func:`update_sprint_status_done`.

        Returns:
            A ``MergeResult`` on success/failure, or ``None`` when the
            queue is empty.

        """
        result = await self.process_next()
        if result is None:
            return None

        # If merge itself failed, skip fix entirely
        if not result.success:
            return result

        # If QG passed (or was not run), no fix needed
        if result.qg_result is None or result.qg_result.all_passed:
            if result.qg_result is not None and result.qg_result.all_passed:
                update_sprint_status_done(
                    result.story_id, self._project_root, self._done_ids,
                )
                self._done_ids.add(result.story_id)
            return result

        # An environment failure has no code for the fixer to fix. Routing is
        # downgraded; visibility is not — the failure is still reported.
        if not _assert_fix_routable(result.qg_result):
            return result

        # Determine max retries
        max_retries = 1
        if self._parallel_config is not None:
            max_retries = self._parallel_config.post_merge_fix_retries

        # If retries is 0, skip fix loop entirely
        if max_retries == 0:
            return result

        # Enter fix loop
        story_id = result.story_id
        latest_qg: PostMergeQGResult = result.qg_result
        for attempt in range(1, max_retries + 1):
            logger.info(
                "[FIX-QG|post-merge|%s] Fix attempt %d of %d",
                story_id,
                attempt,
                max_retries,
            )
            try:
                fix_qg_result = await asyncio.to_thread(
                    run_post_merge_fix,
                    story_id,
                    self._project_root,
                    self._config,
                    attempt,
                )
            except ParallelError:
                # Claude CLI not found — propagate
                raise
            except Exception:
                logger.error(
                    "[FIX-QG|post-merge|%s] Fix attempt %d failed with unexpected error",
                    story_id,
                    attempt,
                    exc_info=True,
                )
                break

            latest_qg = fix_qg_result
            if fix_qg_result.all_passed:
                logger.info(
                    "[FIX-QG|post-merge|%s] Fix succeeded on attempt %d",
                    story_id,
                    attempt,
                )
                break

        result = result.model_copy(update={"qg_result": latest_qg})

        if latest_qg.all_passed:
            update_sprint_status_done(
                story_id, self._project_root, self._done_ids,
            )
            self._done_ids.add(story_id)

        return result
