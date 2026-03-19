"""Sequential merge queue and git merge for parallel story execution.

Provides a ``MergeQueue`` that ensures stories are merged one at a time
into the base branch, and a ``merge_story()`` function that performs the
actual git merge with conflict detection and guaranteed abort on failure.

All git operations use ``_run_git()`` from ``git_ops`` — never raw
``subprocess``.  This module does **not** write to ``parallel-state.yaml``;
state transitions are the orchestrator's responsibility.
"""

import asyncio
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.git_ops import _run_git
from bmad_assist_lite.parallel.worktree_manager import (
    _branch_name,
    cleanup_worktree,
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
# Core Merge Function
# ============================================================================


def merge_story(
    story_id: str,
    project_root: Path,
    *,
    expected_branch: str | None = None,
) -> MergeResult:
    """Merge a story branch into the current base branch.

    Performs the following sequence:

    1. Verify HEAD is on the expected base branch (not detached or wrong branch).
    2. ``git merge --no-edit parallel/{id}`` with ``check=False``.
    3. On success: delete the branch, clean up the worktree, return success.
    4. On conflict: capture conflict files, guarantee ``git merge --abort``.

    Args:
        story_id: Story identifier (e.g. ``"3.1"``).
        project_root: Path to the main git repository.
        expected_branch: If provided, verify HEAD is on this branch before
            merging.  When ``None``, only a detached-HEAD check is performed.

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
        logger.info("%s Merge succeeded — deleting branch %s", tag, branch)
        del_result = _run_git(["branch", "-d", branch], cwd=project_root, check=False)
        if del_result.returncode != 0:
            logger.warning(
                "%s Branch deletion failed (rc=%d): %s (non-fatal)",
                tag,
                del_result.returncode,
                del_result.stderr.strip(),
            )

        # Worktree cleanup (best-effort)
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
    # Step 4: Conflict path — capture files then abort (guaranteed)
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
    finally:
        logger.info("%s Aborting merge to restore clean state", tag)
        abort_result = _run_git(["merge", "--abort"], cwd=project_root, check=False)
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
