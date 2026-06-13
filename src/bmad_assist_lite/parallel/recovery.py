"""Crash recovery for parallel orchestrator state.

Reconciles persisted ``parallel-state.yaml`` against actual on-disk git
worktrees after an unexpected process interruption. In-flight or merging
stories whose worktrees no longer exist are reset to backlog so they can
be re-attempted. Stories with existing worktrees are preserved for resume.
Orphaned ``*.tmp`` files in the ``.bmad-assist-lite/`` directory are
cleaned up during recovery. Orphaned worktrees (no state record or
``done`` status) are detected and cleaned up.
"""

import logging
import re
from pathlib import Path

from bmad_assist_lite.parallel.state import (
    STATE_DIR,
    ParallelState,
    StoryStatus,
    get_parallel_state_path,
    save_state,
)
from bmad_assist_lite.parallel.worktree_manager import (
    WorktreeInfo,
    cleanup_worktree,
    list_worktrees,
    prune_worktrees,
)

logger = logging.getLogger(__name__)

__all__ = ["recover_state"]

# Pattern to validate story ID format after reverse mapping from branch name.
# Matches numeric IDs like "3.4", "10.1", etc.
_STORY_ID_RE = re.compile(r"^\d+\.\d+$")


# ============================================================================
# Temp file cleanup
# ============================================================================


def _cleanup_temp_files(project_root: Path) -> int:
    """Recursively remove orphaned ``*.tmp`` files from ``.bmad-assist-lite/``.

    Scans the ``.bmad-assist-lite/`` directory (including ``cache/``
    subdirectory) for ``*.tmp`` files using ``Path.rglob("*.tmp")``
    and removes them. Each removed file is logged at warning level.

    Args:
        project_root: Project root directory containing ``.bmad-assist-lite/``.

    Returns:
        The number of temp files successfully removed.

    """
    bmad_dir = project_root / STATE_DIR
    if not bmad_dir.is_dir():
        return 0

    removed_count = 0
    try:
        tmp_files = list(bmad_dir.rglob("*.tmp"))
    except OSError as exc:
        logger.warning(
            "[ORCHESTRATOR] Failed to scan for temp files in %s: %s",
            bmad_dir,
            exc,
        )
        return 0

    for tmp_file in tmp_files:
        try:
            tmp_file.unlink()
            removed_count += 1
            logger.warning(
                "[ORCHESTRATOR] Removed orphaned temp file: %s", tmp_file,
            )
        except OSError as exc:
            logger.warning(
                "[ORCHESTRATOR] Failed to remove temp file %s: %s",
                tmp_file,
                exc,
            )

    return removed_count


# ============================================================================
# Orphan detection and worktree pruning
# ============================================================================


def prune_and_clean_orphaned_worktrees(
    state: ParallelState,
    project_root: Path,
    worktrees: list[WorktreeInfo],
    base_dir: Path | None = None,
) -> int:
    """Detect and clean orphaned parallel worktrees.

    Filters the pre-fetched worktree list to those with ``parallel/``
    prefixed branches, reverse-maps the branch name to a story ID, and
    identifies orphans: worktrees with no matching story in state, or
    whose story status is ``done``.

    Each identified orphan is cleaned up via ``cleanup_worktree()``.
    Cleanup failures are logged but do not abort processing of remaining
    orphans.

    Args:
        state: The reconciled ``ParallelState``.
        project_root: Path to the main git repository.
        worktrees: Pre-fetched worktree list from ``list_worktrees()``.
        base_dir: Parent directory for worktrees. Defaults to
            ``project_root.parent`` when ``None``.

    Returns:
        The number of orphaned worktrees successfully cleaned.

    """
    cleaned_count = 0

    for wt in worktrees:
        # Task 1.2: Only consider parallel/* branches
        if wt.branch is None or not wt.branch.startswith("parallel/"):
            continue

        # Task 2.1: Reverse-map branch name to story ID
        # parallel/3-4 → 3.4 (strip prefix, replace - with .)
        raw_story_id = wt.branch[len("parallel/"):].replace("-", ".")

        # Validate story ID format (e.g., "3.4", "10.1")
        if not _STORY_ID_RE.match(raw_story_id):
            logger.warning(
                "[ORCHESTRATOR] Skipping worktree with non-standard "
                "branch %s (mapped to %r, not a valid story ID)",
                wt.branch,
                raw_story_id,
            )
            continue

        story_id = raw_story_id

        # Task 2.2 / 2.3: Determine if orphaned
        story_state = state.stories.get(story_id)
        if story_state is None:
            reason = "no_state_record"
        elif story_state.status == StoryStatus.DONE:
            reason = "done"
        else:
            # Not orphaned — active story (in_flight, merging, backlog, blocked)
            continue

        # Task 3.1 / 3.2: Clean up the orphan
        try:
            cleanup_worktree(story_id, project_root, base_dir)
            cleaned_count += 1
            # Task 3.3: Log warning for each orphan cleaned
            logger.warning(
                "[ORCHESTRATOR] Cleaned orphaned worktree for story %s "
                "(reason: %s)",
                story_id,
                reason,
            )
        except Exception as exc:
            logger.warning(
                "[ORCHESTRATOR] Failed to clean orphaned worktree for "
                "story %s: %s",
                story_id,
                exc,
            )

    return cleaned_count


# ============================================================================
# State recovery
# ============================================================================


def recover_state(
    state: ParallelState,
    project_root: Path,
    worktree_base_dir: Path | None = None,
) -> ParallelState:
    """Reconcile persisted state against actual on-disk worktrees.

    Checks each story's status and worktree path against the real git
    worktree list. Stories in ``in_flight`` or ``merging`` whose worktrees
    no longer exist are reset to ``backlog``. Terminal statuses (``done``,
    ``blocked``) and ``backlog`` are preserved unchanged.

    After reconciliation, orphaned worktrees (no state record or ``done``
    status) are detected and cleaned up. The updated state is then
    atomically saved.

    Args:
        state: The loaded ``ParallelState`` to reconcile.
        project_root: Path to the main git repository.
        worktree_base_dir: Parent directory for worktrees. Defaults to
            ``project_root.parent`` when ``None``.

    Returns:
        The reconciled ``ParallelState``.

    """
    # Clean up temp files first (AC #5)
    temp_files_cleaned = _cleanup_temp_files(project_root)

    # Task 4.1: Prune stale git worktree references BEFORE listing
    try:
        prune_worktrees(project_root)
    except Exception as exc:
        logger.warning(
            "[ORCHESTRATOR] Failed to prune stale worktree references: %s",
            exc,
        )

    # Get actual worktrees on disk (Task 2)
    try:
        worktrees = list_worktrees(project_root)
    except Exception as exc:
        logger.error(
            "[ORCHESTRATOR] Recovery failed to list worktrees: %s. "
            "Returning state unchanged. (%d temp files cleaned before failure)",
            exc,
            temp_files_cleaned,
        )
        return state

    # Build O(1) lookup set of resolved worktree paths (Task 2.2)
    on_disk_paths: set[Path] = {wt.path.resolve() for wt in worktrees}

    # Reconciliation loop (Task 3)
    recovered_state = state
    reset_count = 0
    preserved_count = 0

    for story_id, story_state in state.stories.items():
        if story_state.status in (
            StoryStatus.IN_FLIGHT,
            StoryStatus.MERGING,
        ):
            # Check if worktree exists on disk
            worktree_exists = (
                story_state.worktree_path is not None
                and story_state.worktree_path.resolve() in on_disk_paths
            )

            if worktree_exists:
                # Preserve as-is (AC #1 for in_flight, AC #7 for merging)
                preserved_count += 1
                logger.info(
                    "[ORCHESTRATOR] Story %s preserved as %s "
                    "(worktree exists at %s)",
                    story_id,
                    story_state.status.value,
                    story_state.worktree_path,
                )
            else:
                # Reset to backlog (AC #2 for in_flight, AC #7 for merging)
                recovered_state = recovered_state.with_story_status(
                    story_id,
                    StoryStatus.BACKLOG,
                )
                reset_count += 1
                logger.warning(
                    "[ORCHESTRATOR] Story %s was %s but worktree missing "
                    "-- reset to backlog",
                    story_id,
                    story_state.status.value,
                )

        elif story_state.status in (
            StoryStatus.DONE,
            StoryStatus.BLOCKED,
            StoryStatus.BACKLOG,
        ):
            # Pass through unchanged (AC #3)
            preserved_count += 1

    # Task 4.2: Orphan detection and cleanup (after reconciliation, before save)
    orphans_cleaned = prune_and_clean_orphaned_worktrees(
        recovered_state, project_root, worktrees, worktree_base_dir,
    )

    # Persist recovered state (AC #6, Task 5)
    state_path = get_parallel_state_path(project_root)
    save_state(recovered_state, state_path)

    # Log recovery summary (Task 5.2) — Task 4.3: include orphan count
    logger.info(
        "[ORCHESTRATOR] Recovery complete: %d %s reset to backlog, "
        "%d %s preserved, %d temp %s cleaned, "
        "%d orphaned %s cleaned",
        reset_count,
        "story" if reset_count == 1 else "stories",
        preserved_count,
        "story" if preserved_count == 1 else "stories",
        temp_files_cleaned,
        "file" if temp_files_cleaned == 1 else "files",
        orphans_cleaned,
        "worktree" if orphans_cleaned == 1 else "worktrees",
    )

    return recovered_state
