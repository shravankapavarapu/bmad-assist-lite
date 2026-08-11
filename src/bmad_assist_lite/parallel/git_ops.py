"""Git subprocess wrapper for parallel story execution.

Provides a platform-safe git command wrapper that all parallel components
use for consistent error handling. All git operations in the parallel
module MUST use ``_run_git()`` instead of raw ``subprocess.run()``.

Also provides the read-only git primitives the merge protocol is built
from — divergence counting, tree-SHA resolution and unmerged-commit
counting — plus ``rebase_branch()``, which replays a story branch onto a
moved integration head without ever leaving the branch in a half-rebased
state.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.providers._windows import get_subprocess_kwargs

logger = logging.getLogger(__name__)


def _run_git(
    args: list[str],
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute a git command with platform-safe subprocess settings.

    Args:
        args: Git subcommand and arguments (e.g. ``["status"]``).
        cwd: Working directory for the git command.
        check: If True (default), raise ``ParallelError`` on non-zero exit.

    Returns:
        The ``CompletedProcess`` result from ``subprocess.run``.

    Raises:
        ParallelError: If ``args`` is empty, if ``check=True`` and the
            command exits with a non-zero return code, or if the git
            executable cannot be found or executed.

    """
    if not args:
        raise ParallelError("_run_git requires at least one argument")

    logger.debug("git %s (cwd=%s)", " ".join(args), cwd)

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            **get_subprocess_kwargs(),
        )
    except FileNotFoundError as exc:
        raise ParallelError("git executable not found on PATH") from exc
    except OSError as exc:
        raise ParallelError(f"failed to execute git: {exc}") from exc

    if check and result.returncode != 0:
        raise ParallelError(f"git {args[0]} failed: {result.stderr.strip()}")

    return result


def get_current_branch(cwd: Path) -> str:
    """Return the name of the currently checked-out branch.

    Args:
        cwd: Repository working directory.

    Returns:
        Branch name as a stripped string. Returns the literal string
        ``"HEAD"`` when in detached HEAD state.

    Raises:
        ParallelError: If the underlying git command fails.

    """
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    return result.stdout.strip()


def is_protected_branch(branch: str) -> bool:
    """Check whether a branch name is protected.

    Args:
        branch: Branch name to check.

    Returns:
        True if the branch is ``main`` or ``master``, False otherwise.

    """
    return branch in ("main", "master")


# ============================================================================
# Read-only revision primitives
# ============================================================================


def ref_exists(cwd: Path, ref: str) -> bool:
    """Return ``True`` when ``ref`` resolves to an object in the repository.

    Args:
        cwd: Repository working directory.
        ref: Any git revision string (branch, tag, SHA).

    Returns:
        ``True`` when ``git rev-parse --verify`` succeeds.  A repository
        that cannot be reached at all answers ``False`` rather than
        raising — "does this ref exist" has a sensible answer there, and
        callers that must not fail open (the no-data-loss guard) treat an
        unreadable repository as unsafe on their own.

    """
    try:
        result = _run_git(
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=cwd, check=False
        )
    except ParallelError:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def rev_parse(cwd: Path, ref: str = "HEAD") -> str:
    """Resolve ``ref`` to a full commit SHA.

    Args:
        cwd: Repository working directory.
        ref: Revision to resolve. Defaults to ``HEAD``.

    Returns:
        The 40-character commit SHA.

    Raises:
        ParallelError: If the revision cannot be resolved.

    """
    return _run_git(["rev-parse", ref], cwd=cwd).stdout.strip()


def tree_sha(cwd: Path, ref: str = "HEAD") -> str:
    """Resolve ``ref`` to the SHA of the tree it points at.

    Gate and merge verdicts bind to this value rather than to the commit
    SHA: a rebase rewrites commit SHAs even when the resulting content is
    unchanged, so commit-SHA binding produces false invalidations, whereas
    the tree SHA changes only when the content does.

    Args:
        cwd: Repository working directory.
        ref: Revision whose tree is wanted. Defaults to ``HEAD``.

    Returns:
        The 40-character tree SHA.

    Raises:
        ParallelError: If the revision cannot be resolved.

    """
    return _run_git(["rev-parse", f"{ref}^{{tree}}"], cwd=cwd).stdout.strip()


def count_ahead_behind(cwd: Path, branch: str, base: str) -> tuple[int, int]:
    """Count how far ``branch`` has diverged from ``base``.

    Args:
        cwd: Repository working directory.
        branch: The branch being landed.
        base: The integration reference it will land on.

    Returns:
        A ``(ahead, behind)`` tuple: commits on ``branch`` that ``base``
        lacks, and commits on ``base`` that ``branch`` lacks.

    Raises:
        ParallelError: If either revision cannot be resolved.

    """
    result = _run_git(
        ["rev-list", "--left-right", "--count", f"{base}...{branch}"],
        cwd=cwd,
    )
    parts = result.stdout.split()
    if len(parts) != 2:  # noqa: PLR2004
        raise ParallelError(
            f"unexpected rev-list output for {base}...{branch}: {result.stdout!r}"
        )
    behind, ahead = int(parts[0]), int(parts[1])
    return ahead, behind


def count_unmerged_commits(cwd: Path, branch: str, integration_ref: str) -> int:
    """Count commits on ``branch`` that are not reachable from ``integration_ref``.

    This is the single cheap question the no-data-loss guard asks before any
    branch or worktree is deleted.  It runs on every successful merge, so it
    is deliberately one ``git rev-list --count`` and nothing more.

    Args:
        cwd: Repository working directory.
        branch: The branch whose commits might be lost.
        integration_ref: The integration head to measure reachability from.

    Returns:
        The number of commits that exist only on ``branch``.

    Raises:
        ParallelError: If either revision cannot be resolved.

    """
    result = _run_git(
        ["rev-list", "--count", branch, f"^{integration_ref}"],
        cwd=cwd,
    )
    return int(result.stdout.strip())


def find_checkout_dir(project_root: Path, branch: str) -> Path | None:
    """Return the worktree directory where ``branch`` is checked out, if any.

    Args:
        project_root: Path to the main git repository.
        branch: Branch name to locate.

    Returns:
        The worktree path, or ``None`` when the branch is not checked out.

    """
    try:
        result = _run_git(["worktree", "list", "--porcelain"], cwd=project_root, check=False)
    except ParallelError:
        return None
    if result.returncode != 0:
        return None

    current: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):])
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch "):].removeprefix("refs/heads/")
            if ref == branch:
                return current
    return None


# ============================================================================
# Rebase
# ============================================================================


class RebaseOutcome(BaseModel):
    """Immutable result of a rebase-before-merge attempt.

    Attributes:
        status: ``skipped`` when the branch was already up to date,
            ``rebased`` on success, ``conflict`` when git reported
            conflicting files, ``error`` for any other failure.
        branch: The branch that was (or would have been) rebased.
        onto: The integration reference it was replayed onto.
        sha_before: Branch tip before the attempt.
        sha_after: Branch tip after the attempt.  Equal to ``sha_before``
            for every non-``rebased`` status.
        ahead: Commits on ``branch`` that ``onto`` lacked.
        behind: Commits on ``onto`` that ``branch`` lacked.
        conflict_files: Paths git reported as conflicting.
        error: Human-readable failure description, or ``None``.

    """

    model_config = ConfigDict(frozen=True)

    status: Literal["skipped", "rebased", "conflict", "error"]
    branch: str
    onto: str
    sha_before: str
    sha_after: str
    ahead: int = 0
    behind: int = 0
    conflict_files: list[str] = []
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Return ``True`` when the branch is ready to land."""
        return self.status in ("skipped", "rebased")


def _conflicting_paths(cwd: Path) -> list[str]:
    """List paths git currently reports as unmerged."""
    result = _run_git(
        ["diff", "--name-only", "--diff-filter=U"], cwd=cwd, check=False
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def rebase_branch(
    project_root: Path,
    branch: str,
    onto: str,
) -> RebaseOutcome:
    """Replay ``branch`` onto ``onto``, or report why it could not be.

    ``(ahead, behind)`` is computed first.  When ``behind == 0`` the branch
    already contains everything on the integration head and **no rebase is
    attempted** — the returned status is ``skipped``.

    The rebase itself runs in the worktree that has ``branch`` checked out
    when one exists, and otherwise in a throwaway detached worktree whose
    result is only written back to the branch ref on success.  Either way a
    failed rebase is aborted and the branch tip is restored, so a caller can
    rely on ``sha_after == sha_before`` for every non-``rebased`` status.

    Args:
        project_root: Path to the main git repository.
        branch: The story branch to replay.
        onto: The integration reference to replay onto.

    Returns:
        A :class:`RebaseOutcome` describing what happened.

    Raises:
        ParallelError: If ``branch`` or ``onto`` cannot be resolved.

    """
    sha_before = rev_parse(project_root, branch)
    ahead, behind = count_ahead_behind(project_root, branch, onto)

    if behind == 0:
        logger.info(
            "[REBASE|%s] behind=0 — already on top of %s, skipping rebase", branch, onto
        )
        return RebaseOutcome(
            status="skipped",
            branch=branch,
            onto=onto,
            sha_before=sha_before,
            sha_after=sha_before,
            ahead=ahead,
            behind=behind,
        )

    checkout_dir = find_checkout_dir(project_root, branch)
    if checkout_dir is not None and checkout_dir.exists():
        return _rebase_in_checkout(
            project_root, branch, onto, sha_before, ahead, behind, checkout_dir
        )
    return _rebase_detached(project_root, branch, onto, sha_before, ahead, behind)


def _restore_branch(project_root: Path, branch: str, sha_before: str) -> None:
    """Force ``branch`` back to ``sha_before`` if a failed rebase moved it."""
    try:
        if rev_parse(project_root, branch) == sha_before:
            return
    except ParallelError:
        pass
    logger.warning("[REBASE|%s] restoring branch tip to %s", branch, sha_before[:12])
    _run_git(
        ["update-ref", f"refs/heads/{branch}", sha_before], cwd=project_root, check=False
    )


def _rebase_in_checkout(
    project_root: Path,
    branch: str,
    onto: str,
    sha_before: str,
    ahead: int,
    behind: int,
    checkout_dir: Path,
) -> RebaseOutcome:
    """Rebase ``branch`` in the worktree that already has it checked out."""
    logger.info(
        "[REBASE|%s] replaying %d commit(s) onto %s in %s", branch, ahead, onto, checkout_dir
    )
    result = _run_git(["rebase", onto], cwd=checkout_dir, check=False)
    if result.returncode == 0:
        return RebaseOutcome(
            status="rebased",
            branch=branch,
            onto=onto,
            sha_before=sha_before,
            sha_after=rev_parse(project_root, branch),
            ahead=ahead,
            behind=behind,
        )

    conflicts = _conflicting_paths(checkout_dir)
    _run_git(["rebase", "--abort"], cwd=checkout_dir, check=False)
    _restore_branch(project_root, branch, sha_before)
    return _failed_outcome(branch, onto, sha_before, ahead, behind, conflicts, result)


def _rebase_detached(
    project_root: Path,
    branch: str,
    onto: str,
    sha_before: str,
    ahead: int,
    behind: int,
) -> RebaseOutcome:
    """Rebase ``branch`` in a throwaway detached worktree.

    The branch ref is only moved once the rebase has fully succeeded, so a
    conflict cannot leave the branch in a rewritten state.
    """
    tmp_parent = Path(tempfile.mkdtemp(prefix="bmad-rebase-"))
    tmp_wt = tmp_parent / "wt"
    try:
        add = _run_git(
            ["worktree", "add", "--detach", str(tmp_wt), branch],
            cwd=project_root,
            check=False,
        )
        if add.returncode != 0:
            return RebaseOutcome(
                status="error",
                branch=branch,
                onto=onto,
                sha_before=sha_before,
                sha_after=sha_before,
                ahead=ahead,
                behind=behind,
                error=f"could not create rebase worktree: {add.stderr.strip()}",
            )

        logger.info("[REBASE|%s] replaying %d commit(s) onto %s (detached)", branch, ahead, onto)
        result = _run_git(["rebase", onto], cwd=tmp_wt, check=False)
        if result.returncode != 0:
            conflicts = _conflicting_paths(tmp_wt)
            _run_git(["rebase", "--abort"], cwd=tmp_wt, check=False)
            _restore_branch(project_root, branch, sha_before)
            return _failed_outcome(branch, onto, sha_before, ahead, behind, conflicts, result)

        new_sha = rev_parse(tmp_wt)
        _run_git(["update-ref", f"refs/heads/{branch}", new_sha, sha_before], cwd=project_root)
        return RebaseOutcome(
            status="rebased",
            branch=branch,
            onto=onto,
            sha_before=sha_before,
            sha_after=new_sha,
            ahead=ahead,
            behind=behind,
        )
    finally:
        _run_git(
            ["worktree", "remove", "--force", str(tmp_wt)], cwd=project_root, check=False
        )
        shutil.rmtree(tmp_parent, ignore_errors=True)
        _run_git(["worktree", "prune"], cwd=project_root, check=False)


def _failed_outcome(
    branch: str,
    onto: str,
    sha_before: str,
    ahead: int,
    behind: int,
    conflicts: list[str],
    result: subprocess.CompletedProcess[str],
) -> RebaseOutcome:
    """Build the outcome for a rebase that git rejected."""
    combined = (result.stdout or "") + (result.stderr or "")
    is_conflict = bool(conflicts) or "CONFLICT" in combined
    logger.warning(
        "[REBASE|%s] rebase onto %s failed (%s)",
        branch,
        onto,
        "conflict" if is_conflict else "error",
    )
    return RebaseOutcome(
        status="conflict" if is_conflict else "error",
        branch=branch,
        onto=onto,
        sha_before=sha_before,
        sha_after=sha_before,
        ahead=ahead,
        behind=behind,
        conflict_files=conflicts,
        error=combined.strip() or None,
    )
