"""Manage git worktrees for parallel story execution.

Provides functions to create, clean up, list, and prune git worktrees
used for filesystem isolation during parallel story execution. All git
operations use the ``_run_git()`` wrapper from ``git_ops``.

``cleanup_worktree()`` is guarded: it consults
:func:`~bmad_assist_lite.parallel.merge_guard.branch_deletion_decision`
before removing anything, and the three deletion primitives refuse to run
without a cleared decision.  The guard lives here, at the deletion site,
rather than in any failure handler, because the deletions are reached from
the *success* path — a merge cleans up before its post-merge gate runs, so
a guard in that gate's failure handler would fire after the branch was
already gone.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.parallel.git_ops import _run_git
from bmad_assist_lite.parallel.merge_guard import (
    DeletionDecision,
    assert_deletion_allowed,
    branch_deletion_decision,
)

logger = logging.getLogger(__name__)


# ============================================================================
# WorktreeInfo Model
# ============================================================================


class WorktreeInfo(BaseModel):
    """Structured representation of a single git worktree entry.

    Parsed from ``git worktree list --porcelain`` output stanzas.
    """

    model_config = ConfigDict(frozen=True)

    path: Path
    branch: str | None
    commit: str


# ============================================================================
# Story ID Normalization Helpers
# ============================================================================


def _normalize_story_id(story_id: str) -> str:
    """Replace dots with dashes in a story ID for path/branch safety.

    Args:
        story_id: Raw story identifier (e.g. ``"3.1"``).

    Returns:
        Normalized identifier with dots replaced by dashes (e.g. ``"3-1"``).

    """
    return story_id.replace(".", "-")


def _worktree_path(story_id: str, base_dir: Path, repo_name: str) -> Path:
    """Compute the worktree directory path for a story.

    Args:
        story_id: Raw story identifier.
        base_dir: Parent directory under which the worktree is created.
        repo_name: Name of the repository (used as prefix for uniqueness
            when multiple repos share the same parent directory).

    Returns:
        Resolved ``Path`` of the form
        ``base_dir / "{repo_name}-parallel-{normalized}"``.

    """
    normalized = _normalize_story_id(story_id)
    return (base_dir / f"{repo_name}-parallel-{normalized}").resolve()


def _branch_name(story_id: str) -> str:
    """Compute the git branch name for a story worktree.

    Args:
        story_id: Raw story identifier.

    Returns:
        Branch name of the form ``"parallel/{normalized}"``.

    """
    normalized = _normalize_story_id(story_id)
    return f"parallel/{normalized}"


# ============================================================================
# Worktree Operations
# ============================================================================


def create_worktree(
    story_id: str,
    project_root: Path,
    base_dir: Path | None = None,
) -> Path:
    """Create a git worktree for isolated parallel story execution.

    Creates a new worktree directory with a dedicated branch checked out
    from the current HEAD.

    Args:
        story_id: Story identifier (e.g. ``"3.1"``).
        project_root: Path to the main git repository.
        base_dir: Parent directory for the worktree. Defaults to
            ``project_root.parent`` when ``None``.

    Returns:
        Resolved ``Path`` to the created worktree directory.

    Raises:
        ParallelError: If the git worktree add command fails.

    """
    if base_dir is None:
        base_dir = project_root.parent

    repo_name = project_root.resolve().name
    wt_path = _worktree_path(story_id, base_dir, repo_name)
    branch = _branch_name(story_id)

    _run_git(
        ["worktree", "add", "-b", branch, str(wt_path)],
        cwd=project_root,
    )

    logger.info("[ORCHESTRATOR] Created worktree %s on branch %s", wt_path, branch)
    return wt_path


def _remove_worktree(project_root: Path, wt_path: Path, decision: DeletionDecision) -> None:
    """Detach a worktree, refusing without a cleared deletion decision."""
    assert_deletion_allowed(decision, f"worktree {wt_path}")
    result = _run_git(
        ["worktree", "remove", "--force", str(wt_path)],
        cwd=project_root,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            "[ORCHESTRATOR] git worktree remove failed (rc=%d): %s",
            result.returncode,
            result.stderr.strip(),
        )


def _delete_branch(project_root: Path, branch: str, decision: DeletionDecision) -> None:
    """Force-delete a branch, refusing without a cleared deletion decision."""
    assert_deletion_allowed(decision, f"branch {branch}")
    result = _run_git(
        ["branch", "-D", branch],
        cwd=project_root,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            "[ORCHESTRATOR] git branch -D failed (rc=%d): %s",
            result.returncode,
            result.stderr.strip(),
        )


def _remove_worktree_dir(wt_path: Path, decision: DeletionDecision) -> None:
    """Remove a persisting worktree directory from disk."""
    assert_deletion_allowed(decision, f"worktree directory {wt_path}")
    logger.warning(
        "[ORCHESTRATOR] Worktree directory %s persisted after git remove; "
        "removing via shutil.rmtree",
        wt_path,
    )
    shutil.rmtree(wt_path, ignore_errors=True)


def cleanup_worktree(
    story_id: str,
    project_root: Path,
    base_dir: Path | None = None,
    integration_ref: str = "HEAD",
) -> None:
    """Remove a git worktree and its associated branch, if nothing is lost.

    The guard runs first.  When the story branch holds commits that are not
    reachable from ``integration_ref``, **nothing is deleted** — neither the
    branch nor the worktree — and the caller gets a warning naming both, so
    the work stays retrievable.  A branch with zero unmerged commits is
    cleaned up exactly as before, so the guard cannot leak worktrees.

    When the guard clears the deletion, the same idempotent three-step
    cleanup runs as before:

    1. ``git worktree remove --force`` to detach the worktree.
    2. ``git branch -D`` to delete the branch.
    3. ``shutil.rmtree`` as a fallback if the directory persists on disk.

    Failures in steps 1 and 2 are logged as warnings but do not prevent
    subsequent steps from executing.

    Args:
        story_id: Story identifier (e.g. ``"3.1"``).
        project_root: Path to the main git repository.
        base_dir: Parent directory for the worktree. Defaults to
            ``project_root.parent`` when ``None``.
        integration_ref: Reference the branch's commits must be reachable
            from for deletion to be safe. Defaults to ``HEAD``.

    """
    if base_dir is None:
        base_dir = project_root.parent

    repo_name = project_root.resolve().name
    wt_path = _worktree_path(story_id, base_dir, repo_name)
    branch = _branch_name(story_id)

    decision = branch_deletion_decision(project_root, branch, integration_ref)
    if not decision.safe:
        logger.warning(
            "[ORCHESTRATOR] Refusing to clean up story %s: %s. "
            "Branch %s and worktree %s are preserved.",
            story_id,
            decision.reason,
            branch,
            wt_path,
        )
        return

    _remove_worktree(project_root, wt_path, decision)
    _delete_branch(project_root, branch, decision)

    if wt_path.exists():
        _remove_worktree_dir(wt_path, decision)

    logger.info("[ORCHESTRATOR] Cleaned up worktree %s and branch %s", wt_path, branch)


def list_worktrees(project_root: Path) -> list[WorktreeInfo]:
    """List all git worktrees in the repository.

    Parses the machine-readable output of ``git worktree list --porcelain``
    into structured ``WorktreeInfo`` instances.

    Args:
        project_root: Path to the main git repository.

    Returns:
        List of ``WorktreeInfo`` instances, one per worktree stanza.

    Raises:
        ParallelError: If the git worktree list command fails.

    """
    result = _run_git(["worktree", "list", "--porcelain"], cwd=project_root)
    output = result.stdout.strip()

    if not output:
        return []

    worktrees: list[WorktreeInfo] = []
    stanzas = output.split("\n\n")

    for stanza in stanzas:
        if not stanza.strip():
            continue

        path: Path | None = None
        commit = ""
        branch: str | None = None

        for line in stanza.strip().splitlines():
            if line.startswith("worktree "):
                path = Path(line[len("worktree "):])
            elif line.startswith("HEAD "):
                commit = line[len("HEAD "):]
            elif line.startswith("branch "):
                ref = line[len("branch "):]
                prefix = "refs/heads/"
                if ref.startswith(prefix):
                    branch = ref[len(prefix):]
                else:
                    branch = ref

        if path is not None and commit:
            worktrees.append(
                WorktreeInfo(path=path, branch=branch, commit=commit)
            )

    return worktrees


def prune_worktrees(project_root: Path) -> None:
    """Prune stale worktree references.

    Runs ``git worktree prune`` to clean up references to worktree
    directories that no longer exist on disk.

    Args:
        project_root: Path to the main git repository.

    Raises:
        ParallelError: If the git worktree prune command fails.

    """
    _run_git(["worktree", "prune"], cwd=project_root)
    logger.debug("[ORCHESTRATOR] Pruned stale worktree references")
