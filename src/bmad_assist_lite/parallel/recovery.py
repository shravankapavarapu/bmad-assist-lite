"""Crash recovery for parallel orchestrator state.

Reconciles persisted ``parallel-state.yaml`` against actual on-disk git
worktrees after an unexpected process interruption. In-flight or merging
stories whose worktrees no longer exist are reset to backlog so they can
be re-attempted. Stories with existing worktrees are preserved for resume.
Orphaned ``*.tmp`` files in the ``.bmad-assist-lite/`` directory are
cleaned up during recovery.
"""

import logging
from pathlib import Path

from bmad_assist_lite.parallel.state import (
    STATE_DIR,
    ParallelState,
    StoryStatus,
    get_parallel_state_path,
    save_state,
)
from bmad_assist_lite.parallel.worktree_manager import list_worktrees

logger = logging.getLogger(__name__)

__all__ = ["recover_state"]


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

    After reconciliation, the updated state is atomically saved and orphaned
    ``*.tmp`` files are cleaned up.

    Args:
        state: The loaded ``ParallelState`` to reconcile.
        project_root: Path to the main git repository.
        worktree_base_dir: Reserved for future use.

    Returns:
        The reconciled ``ParallelState``.

    """
    # Clean up temp files first (AC #5)
    temp_files_cleaned = _cleanup_temp_files(project_root)

    # Get actual worktrees on disk (Task 2)
    try:
        worktrees = list_worktrees(project_root)
    except Exception as exc:
        logger.error(
            "[ORCHESTRATOR] Recovery failed to list worktrees: %s. "
            "Returning state unchanged.",
            exc,
        )
        return state

    # Build O(1) lookup set of resolved worktree paths (Task 2.2)
    on_disk_paths: set[Path] = {wt.path.resolve() for wt in worktrees}

    # Build branch name set for cross-referencing (Task 2.3)
    on_disk_branches: set[str] = {  # noqa: F841 — reserved for future cross-ref
        wt.branch for wt in worktrees if wt.branch is not None
    }

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

    # Persist recovered state (AC #6, Task 5)
    state_path = get_parallel_state_path(project_root)
    save_state(recovered_state, state_path)

    # Log recovery summary (Task 5.2)
    logger.info(
        "[ORCHESTRATOR] Recovery complete: %d stories reset to backlog, "
        "%d stories preserved, %d temp files cleaned",
        reset_count,
        preserved_count,
        temp_files_cleaned,
    )

    return recovered_state
