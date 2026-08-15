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

The safety predicate has **two** clauses and both are consulted on every
call:

1. does the branch hold commits not reachable from the integration head, and
2. does a live parked-merge record name this branch or worktree?

Either one answering yes means "not safe to remove".  The second clause
exists because a parked merge can have *zero* unmerged commits — the ladder
may have parked it after its commits were already reachable from the
integration head — while the operator is still expected to come back to it.
Under a one-clause predicate the stale-worktree reaper would have been
required to offer exactly the worktrees it is forbidden to remove.  Both
clauses live in this one function so that no call site can hold half the
rule, and :meth:`DeletionDecision.require_full_predicate` makes a
half-consulted verdict unusable rather than merely discouraged.
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
    "REQUIRED_CLAUSES",
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


#: The clauses the safety predicate must consult before any verdict is usable.
REQUIRED_CLAUSES: frozenset[str] = frozenset({"unmerged-commits", "parked-merge"})


class DeletionDecision(BaseModel):
    """Immutable verdict on whether a branch and its worktree may be removed.

    Attributes:
        branch: The branch the decision was computed for.
        integration_ref: The reference reachability was measured against.
        safe: ``True`` only when nothing would be lost by deleting.
        unmerged_commits: Commits reachable from ``branch`` but not from
            ``integration_ref``.  ``0`` whenever ``safe`` is ``True``.
        parked_story_id: The story whose parked-merge record protects this
            branch, when one does.
        clauses_consulted: Which clauses of the predicate actually ran.  A
            verdict that did not consult all of :data:`REQUIRED_CLAUSES` is
            rejected by :meth:`require_full_predicate`.
        reason: Human-readable explanation for the verdict.

    """

    model_config = ConfigDict(frozen=True)

    branch: str
    integration_ref: str
    safe: bool
    unmerged_commits: int = 0
    parked_story_id: str | None = None
    clauses_consulted: frozenset[str] = frozenset()
    reason: str = ""

    def require_full_predicate(self) -> DeletionDecision:
        """Return self, or refuse a verdict that skipped part of the predicate.

        Raises:
            ValueError: If any required clause was not consulted.

        """
        missing = REQUIRED_CLAUSES - self.clauses_consulted
        if missing:
            raise ValueError(
                "no-data-loss invariant violated: a deletion verdict for "
                f"{self.branch} was produced without consulting both clauses of "
                f"the safety predicate (missing: {sorted(missing)}). Unmerged "
                "commits and live parked-merge records each independently make "
                "a worktree unsafe to remove."
            )
        return self


def _parked_record_for(
    project_root: Path, branch: str, worktree_path: Path | str | None
) -> str | None:
    """Return the story id of a live parked merge naming this branch or worktree.

    Read-only.  An unreadable record store is treated as "cannot tell", which
    the caller resolves as unsafe — the same direction every other
    indeterminate answer in this module takes.
    """
    from bmad_assist_lite.parallel.parked import list_parked_merges

    wanted = str(Path(worktree_path).resolve()) if worktree_path is not None else None
    for record in list_parked_merges(project_root):
        if record.branch == branch:
            return record.story_id
        if (
            wanted is not None
            and record.worktree_path
            and str(Path(record.worktree_path).resolve()) == wanted
        ):
            return record.story_id
    return None


def branch_deletion_decision(
    project_root: Path,
    branch: str,
    integration_ref: str = "HEAD",
    worktree_path: Path | str | None = None,
) -> DeletionDecision:
    """Decide whether ``branch`` may be deleted without losing commits.

    Two clauses, both consulted on every call: unmerged commits, and a live
    parked-merge record.  Either one answering yes means "not safe".  Costs a
    single ``git rev-list --count`` plus a directory listing.  This runs on
    every successful merge, not only on failures, so it must stay cheap.

    An indeterminate answer is treated as **unsafe**: preserving work that
    might already be merged is recoverable, deleting work that was not is
    not.  A branch that does not exist is safe by definition — there is
    nothing left to lose — which keeps cleanup idempotent.

    Args:
        project_root: Path to the main git repository.
        branch: The branch under consideration.
        integration_ref: Reference the commits must be reachable from.
        worktree_path: The worktree the branch is checked out in, when known.
            Lets the parked-merge clause match a record by worktree as well as
            by branch name.

    Returns:
        A :class:`DeletionDecision` whose ``clauses_consulted`` names both
        clauses.

    """
    parked_story = _parked_record_for(project_root, branch, worktree_path)
    if parked_story is not None:
        return DeletionDecision(
            branch=branch,
            integration_ref=integration_ref,
            safe=False,
            parked_story_id=parked_story,
            clauses_consulted=REQUIRED_CLAUSES,
            reason=(
                f"story {parked_story} has a live parked-merge record naming "
                f"{branch}; the operator is expected to return to it. List them "
                "with `bmad-assist-lite parallel list-parked`."
            ),
        )

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
                clauses_consulted=REQUIRED_CLAUSES,
                reason="branch does not exist",
            )
        unmerged = count_unmerged_commits(project_root, branch, integration_ref)
    except (ParallelError, ValueError) as exc:
        return DeletionDecision(
            branch=branch,
            integration_ref=integration_ref,
            safe=False,
            clauses_consulted=REQUIRED_CLAUSES,
            reason=f"could not determine reachability ({exc}) — refusing to delete",
        )

    if unmerged > 0:
        return DeletionDecision(
            branch=branch,
            integration_ref=integration_ref,
            safe=False,
            unmerged_commits=unmerged,
            clauses_consulted=REQUIRED_CLAUSES,
            reason=(
                f"{unmerged} commit(s) on {branch} are not reachable from "
                f"{integration_ref}"
            ),
        )

    return DeletionDecision(
        branch=branch,
        integration_ref=integration_ref,
        safe=True,
        clauses_consulted=REQUIRED_CLAUSES,
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
    try:
        decision.require_full_predicate()
    except ValueError as exc:
        raise ParallelError(str(exc)) from exc
    if not decision.safe:
        raise ParallelError(
            f"no-data-loss invariant violated: refusing to delete {what} — "
            f"{decision.reason}"
        )
    return decision
