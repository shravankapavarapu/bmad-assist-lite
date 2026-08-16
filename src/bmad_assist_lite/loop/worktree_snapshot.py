"""Pre-dev worktree snapshot / restore for the SP-A1 lean-first fallback.

The adaptive retry re-runs a story from its pre-dev state, so it must capture the
working tree exactly as it stands before ``dev_story`` writes anything and
restore it byte-for-byte — including untracked prior-phase artifacts (the story
doc) — while never touching gitignored build output (``node_modules``).

git plumbing does this precisely where a stash cannot:

- ``snapshot``: ``git add -A`` stages everything the repo would track (gitignored
  paths are excluded by definition), ``git write-tree`` records the index as a
  tree object, then ``git reset`` returns the index to HEAD so snapshotting has
  no staging side effect. The returned tree sha captures tracked + untracked
  content but no gitignored content.
- ``restore``: ``git read-tree`` loads the snapshot into the index,
  ``git checkout-index -a -f`` overwrites modified files and recreates deleted
  ones, ``git clean -fd`` (no ``-x``) removes files created since — leaving
  gitignored build output in place — and ``git reset`` returns the index to HEAD.

Both are best-effort: any git failure returns a falsy result and is logged, so a
snapshot that could not be taken degrades the fallback to "retry in place"
rather than crashing the run.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["snapshot_worktree", "restore_worktree"]


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def snapshot_worktree(project_path: Path) -> str | None:
    """Capture the working tree (incl. untracked, excl. gitignored) as a tree sha.

    Leaves the index at HEAD and the working tree untouched. Returns the tree
    sha, or None if this is not a git repo or any git step fails.
    """
    try:
        add = _git(["add", "-A"], project_path)
        if add.returncode != 0:
            logger.warning("snapshot_worktree: git add failed: %s", add.stderr.strip())
            return None
        wt = _git(["write-tree"], project_path)
        if wt.returncode != 0:
            logger.warning("snapshot_worktree: git write-tree failed: %s", wt.stderr.strip())
            _git(["reset", "-q"], project_path)
            return None
        tree = wt.stdout.strip()
        # Return the index to HEAD: snapshotting must not leave everything staged.
        _git(["reset", "-q"], project_path)
        return tree or None
    except OSError as e:
        logger.warning("snapshot_worktree: %s", e)
        return None


def restore_worktree(project_path: Path, tree_sha: str) -> bool:
    """Restore the working tree to a snapshot tree captured by ``snapshot_worktree``.

    Overwrites modified tracked files, recreates deleted ones, and removes files
    created since (``git clean -fd`` — no ``-x``, so gitignored build output
    survives). Leaves the index at HEAD. Returns True on success.
    """
    try:
        read = _git(["read-tree", tree_sha], project_path)
        if read.returncode != 0:
            logger.warning("restore_worktree: git read-tree failed: %s", read.stderr.strip())
            return False
        checkout = _git(["checkout-index", "-a", "-f"], project_path)
        if checkout.returncode != 0:
            logger.warning(
                "restore_worktree: git checkout-index failed: %s", checkout.stderr.strip()
            )
        clean = _git(["clean", "-fd"], project_path)
        if clean.returncode != 0:
            logger.warning("restore_worktree: git clean failed: %s", clean.stderr.strip())
        # Return the index to HEAD so the restore does not leave the snapshot staged.
        _git(["reset", "-q"], project_path)
        return checkout.returncode == 0
    except OSError as e:
        logger.warning("restore_worktree: %s", e)
        return False
