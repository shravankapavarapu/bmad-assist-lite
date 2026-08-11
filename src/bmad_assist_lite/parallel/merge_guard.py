"""Runtime invariants for the merge protocol.

Two invariants live here, both of which exist because the static checks
that describe them can be defeated by one helper indirection:

**No provider call inside the merge critical section.**  The merge lock is
held around a short, bounded sequence and expensive LLM work runs outside
it.  An AST walk of the ``async with self._lock`` body cannot see a
provider call two hops below an ``asyncio.to_thread`` boundary, so
:func:`assert_merge_lock_not_held` checks lock ownership at the moment of
invocation instead.  The flag is a :class:`contextvars.ContextVar`, which
``asyncio.to_thread`` propagates into the worker thread — the same
indirection that hides the call from the AST carries the invariant to it.

**No deletion of a branch or worktree that still holds commits.**  Every
deletion site takes a :class:`DeletionDecision` produced by
:func:`branch_deletion_decision` and refuses to act without one.  The guard
sits at the deletion site rather than in any failure handler because the
deletion is reached from the *success* path — a merge cleans up before its
post-merge gate has run, so a guard in the gate's failure handler would
execute after the branch was already gone.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.git_ops import _run_git, count_unmerged_commits

logger = logging.getLogger(__name__)

__all__ = [
    "DeletionDecision",
    "assert_deletion_allowed",
    "assert_merge_lock_not_held",
    "branch_deletion_decision",
    "enter_merge_lock",
    "exit_merge_lock",
    "merge_lock_held",
]


# ============================================================================
# Merge-lock ownership sentinel
# ============================================================================

_MERGE_LOCK_HELD: ContextVar[bool] = ContextVar("bmad_merge_lock_held", default=False)


def enter_merge_lock() -> Token[bool]:
    """Mark the merge critical section as entered for this context.

    Returns:
        The token to pass back to :func:`exit_merge_lock`.

    """
    return _MERGE_LOCK_HELD.set(True)


def exit_merge_lock(token: Token[bool]) -> None:
    """Mark the merge critical section as left.

    Args:
        token: The token returned by :func:`enter_merge_lock`.

    """
    _MERGE_LOCK_HELD.reset(token)


def merge_lock_held() -> bool:
    """Return ``True`` when the caller is inside the merge critical section."""
    return _MERGE_LOCK_HELD.get()


def assert_merge_lock_not_held(where: str) -> None:
    """Refuse to proceed when called from inside the merge critical section.

    Called immediately before any provider invocation on the merge path.
    Holding the merge lock across an LLM call blocks every other finished
    story for the whole resolution budget, so this is a hard error rather
    than a warning.

    Args:
        where: Short description of the call site, used in the message.

    Raises:
        ParallelError: If the merge critical section is currently held.

    """
    if merge_lock_held():
        raise ParallelError(
            f"merge-protocol invariant violated: {where} was invoked while the merge "
            "critical section was held. Conflict resolution must run outside the lock."
        )


# ============================================================================
# No-data-loss guard
# ============================================================================


class DeletionDecision(BaseModel):
    """Immutable verdict on whether a branch and its worktree may be removed.

    Attributes:
        branch: The branch the decision was computed for.
        integration_ref: The reference reachability was measured against.
        safe: ``True`` only when nothing would be lost by deleting.
        unmerged_commits: Commits reachable from ``branch`` but not from
            ``integration_ref``.  ``0`` whenever ``safe`` is ``True``.
        reason: Human-readable explanation for the verdict.

    """

    model_config = ConfigDict(frozen=True)

    branch: str
    integration_ref: str
    safe: bool
    unmerged_commits: int = 0
    reason: str = ""


def branch_deletion_decision(
    project_root: Path,
    branch: str,
    integration_ref: str = "HEAD",
) -> DeletionDecision:
    """Decide whether ``branch`` may be deleted without losing commits.

    Costs a single ``git rev-list --count``.  This runs on every successful
    merge, not only on failures, so it must stay cheap.

    An indeterminate answer is treated as **unsafe**: preserving work that
    might already be merged is recoverable, deleting work that was not is
    not.  A branch that does not exist is safe by definition — there is
    nothing left to lose — which keeps cleanup idempotent.

    Args:
        project_root: Path to the main git repository.
        branch: The branch under consideration.
        integration_ref: Reference the commits must be reachable from.

    Returns:
        A :class:`DeletionDecision`.

    """
    try:
        # Asked directly, not through the tolerant ref_exists(): here an
        # unreachable repository must read as "cannot tell", never as
        # "nothing to lose".
        probe = _run_git(
            ["rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}"],
            cwd=project_root,
            check=False,
        )
        if probe.returncode != 0:
            return DeletionDecision(
                branch=branch,
                integration_ref=integration_ref,
                safe=True,
                reason="branch does not exist",
            )
        unmerged = count_unmerged_commits(project_root, branch, integration_ref)
    except (ParallelError, ValueError) as exc:
        return DeletionDecision(
            branch=branch,
            integration_ref=integration_ref,
            safe=False,
            reason=f"could not determine reachability ({exc}) — refusing to delete",
        )

    if unmerged > 0:
        return DeletionDecision(
            branch=branch,
            integration_ref=integration_ref,
            safe=False,
            unmerged_commits=unmerged,
            reason=(
                f"{unmerged} commit(s) on {branch} are not reachable from "
                f"{integration_ref}"
            ),
        )

    return DeletionDecision(
        branch=branch,
        integration_ref=integration_ref,
        safe=True,
        reason=f"fully reachable from {integration_ref}",
    )


def assert_deletion_allowed(decision: object, what: str) -> DeletionDecision:
    """Refuse a deletion that the guard has not cleared.

    Args:
        decision: The value a deletion site was handed.  Anything that is
            not a cleared :class:`DeletionDecision` is rejected.
        what: Short description of what would be deleted.

    Returns:
        The validated decision.

    Raises:
        ParallelError: If the guard was not consulted or said no.

    """
    if not isinstance(decision, DeletionDecision):
        raise ParallelError(
            f"no-data-loss invariant violated: {what} was about to be deleted without "
            "consulting branch_deletion_decision()"
        )
    if not decision.safe:
        raise ParallelError(
            f"no-data-loss invariant violated: refusing to delete {what} — "
            f"{decision.reason}"
        )
    return decision
