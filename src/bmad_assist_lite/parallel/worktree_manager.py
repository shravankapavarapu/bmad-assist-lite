"""Manage git worktrees for parallel story execution.

Provides functions to create, clean up, list, and prune git worktrees
used for filesystem isolation during parallel story execution. All git
operations use the ``_run_git()`` wrapper from ``git_ops``.
"""

import logging
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.parallel.git_ops import _run_git

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


def cleanup_worktree(
    story_id: str,
    project_root: Path,
    base_dir: Path | None = None,
) -> None:
    """Remove a git worktree and its associated branch.

    Perform an idempotent three-step cleanup that is safe to call even
    when the worktree or branch has already been partially removed:

    1. ``git worktree remove --force`` to detach the worktree.
    2. ``git branch -D`` to force-delete the branch (handles unmerged branches).
    3. ``shutil.rmtree`` as a fallback if the directory persists on disk.

    Failures in steps 1 and 2 are logged as warnings but do not prevent
    subsequent steps from executing.

    Args:
        story_id: Story identifier (e.g. ``"3.1"``).
        project_root: Path to the main git repository.
        base_dir: Parent directory for the worktree. Defaults to
            ``project_root.parent`` when ``None``.

    """
    if base_dir is None:
        base_dir = project_root.parent

    repo_name = project_root.resolve().name
    wt_path = _worktree_path(story_id, base_dir, repo_name)
    branch = _branch_name(story_id)

    # Step 1: Remove worktree — use check=False so branch deletion and
    # directory cleanup always execute even when the worktree is already
    # removed, locked, or in an unexpected state.
    wt_result = _run_git(
        ["worktree", "remove", "--force", str(wt_path)],
        cwd=project_root,
        check=False,
    )
    if wt_result.returncode != 0:
        logger.warning(
            "[ORCHESTRATOR] git worktree remove failed (rc=%d): %s",
            wt_result.returncode,
            wt_result.stderr.strip(),
        )

    # Step 2: Force-delete the branch — use check=False because the branch
    # may already be deleted (e.g., retry after partial cleanup).
    br_result = _run_git(
        ["branch", "-D", branch],
        cwd=project_root,
        check=False,
    )
    if br_result.returncode != 0:
        logger.warning(
            "[ORCHESTRATOR] git branch -D failed (rc=%d): %s",
            br_result.returncode,
            br_result.stderr.strip(),
        )

    # Step 3: Fallback directory removal — ensures the directory is gone
    # even when git worktree remove couldn't handle it (e.g., file locks).
    if wt_path.exists():
        logger.warning(
            "[ORCHESTRATOR] Worktree directory %s persisted after git remove; "
            "removing via shutil.rmtree",
            wt_path,
        )
        shutil.rmtree(wt_path, ignore_errors=True)

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
