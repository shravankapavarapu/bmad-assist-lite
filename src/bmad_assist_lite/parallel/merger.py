"""Sequential merge queue and git merge for parallel story execution.

Provides a ``MergeQueue`` that ensures stories are merged one at a time
into the base branch, and a ``merge_story()`` function that performs the
actual git merge with conflict detection and guaranteed abort on failure.

Conflict resolution via Claude CLI is opt-in: when enabled, merge conflicts
are sent to ``claude --print`` with full story context, and the resolved
content is applied, validated, and committed automatically.

All git operations use ``_run_git()`` from ``git_ops`` — never raw
``subprocess``.  This module does **not** write to ``parallel-state.yaml``;
state transitions are the orchestrator's responsibility.
"""

import asyncio
import logging
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

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

logger = logging.getLogger(__name__)


# ============================================================================
# MergeResult Model
# ============================================================================


class MergeResult(BaseModel):
    """Immutable result of a single merge attempt.

    Attributes:
        success: ``True`` when the merge completed without conflicts.
        story_id: The story identifier that was merged.
        conflict_files: List of conflicting file paths (empty on success).
        error: Human-readable error description, or ``None`` on success.

    """

    model_config = ConfigDict(frozen=True)

    success: bool
    story_id: str
    conflict_files: list[str] = []
    error: str | None = None


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
# MergeQueue — Async Sequential Queue
# ============================================================================


class MergeQueue:
    """Async queue that enforces one-at-a-time merge execution.

    Stories are enqueued as they complete, and ``process_next()``
    dequeues and merges them sequentially under an ``asyncio.Lock``.

    Args:
        project_root: Path to the main git repository.

    """

    def __init__(self, project_root: Path) -> None:
        """Initialise the merge queue for the given repository."""
        self._project_root = project_root
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._lock = asyncio.Lock()

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
                result = await asyncio.to_thread(
                    merge_story, story_id, self._project_root
                )
                return result
            finally:
                self._queue.task_done()
