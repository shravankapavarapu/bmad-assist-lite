"""The bounded review -> fix -> re-review decision, as one pure function.

Three stop conditions, and the order they are checked in is the design:

1. **Nothing blocking** — the happy path costs exactly zero extra iterations.
2. **The finding set repeated** — the fixer is *stuck*. Bail immediately with
   ``non-convergent`` rather than spending the rest of the cap. A cap answers
   "how long do we pay?"; the hash answers "are we still buying anything?".
3. **The cap** — the fixer is *slow*. A backstop, not the primary control.

The hash check deliberately precedes the cap check. When both conditions hold
at once the honest reading is the more specific one: "stuck" is a bug report,
"slow" is not, and collapsing them would hide the only one worth acting on.

Then, and only then, the deterministic follow-up score decides whether the
iteration is spent **at all**. With the shipped cap of 1 that score is what
keeps the loop nearly free in the common case — a story whose findings are a
handful of nits pays nothing.

Kept as a pure function over explicit arguments rather than as handler state
so it is exercisable without a provider, a config file, or a story on disk.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.core.config import ReviewConfig
from bmad_assist_lite.core.state import Phase
from bmad_assist_lite.validation.findings import (
    FindingSet,
    followup_review_recommended,
)

__all__ = ["ReviewDecision", "ReviewOutcome", "decide_review_loop"]


class ReviewOutcome(StrEnum):
    """Why the review loop did what it did."""

    CLEAN = "clean"
    DISABLED = "disabled"
    NOT_WORTH_IT = "not-worth-it"
    FIX = "fix"
    CAP_EXHAUSTED = "cap-exhausted"
    NON_CONVERGENT = "non-convergent"
    PARSE_FAILED = "parse-failed"


#: Outcomes that stop the loop having achieved nothing, and therefore owe the
#: operator an explanation on the console.
_BLOCKING_OUTCOMES = frozenset(
    {
        ReviewOutcome.CAP_EXHAUSTED,
        ReviewOutcome.NON_CONVERGENT,
        ReviewOutcome.PARSE_FAILED,
    }
)


class ReviewDecision(BaseModel):
    """What the loop decided, and what to tell the operator about it."""

    model_config = ConfigDict(frozen=True)

    outcome: ReviewOutcome
    reason: str
    finding_hash: str = ""
    blocking_count: int = 0
    console_line: str = ""

    @property
    def blocked(self) -> bool:
        """Whether this story is marked blocked and moved on from."""
        return self.outcome in _BLOCKING_OUTCOMES

    @property
    def proceeds(self) -> bool:
        """Whether the loop leaves the review phase rather than iterating."""
        return self.outcome is not ReviewOutcome.FIX

    @property
    def next_phase(self) -> Phase | None:
        """The ``next_phase`` override, or ``None`` to advance normally.

        The fix phase is reachable only this way — it is never a member of
        ``loop.story``, exactly as ``fix_quality_gate`` is not.
        """
        return Phase.FIX_REVIEW if self.outcome is ReviewOutcome.FIX else None


def _blocked_console_line(
    outcome: ReviewOutcome, story_id: str, max_iterations: int, detail: str
) -> str:
    """Explain a blocked story in the terms the operator will next ask about.

    An exit code is the machine's answer. These runs are long and unattended,
    so a story that stopped iterating is otherwise indistinguishable from one
    that never had findings — output simply moves on. The two questions are
    always the same: what ran out, and how do I continue.
    """
    return (
        f"  REVIEW LOOP STOPPED [{outcome.value}] story {story_id}: {detail}\n"
        f"  This is a clean stop, not a failure — the run continued to the next story.\n"
        f"  The story is marked blocked; its findings are recorded in the review "
        f"artifact.\n"
        f"  To allow more fix rounds, raise `loop.review_max_iterations` "
        f"(currently {max_iterations}) in bmad-assist-lite.yaml."
    )


def decide_review_loop(
    findings: FindingSet | None,
    *,
    iteration: int,
    max_iterations: int,
    previous_hashes: Sequence[str],
    review: ReviewConfig,
    story_id: str,
) -> ReviewDecision:
    """Decide whether to spend another review -> fix round.

    Args:
        findings: The parsed findings from this pass, or ``None`` when parsing
            failed. ``None`` is never equivalent to an empty set.
        iteration: Fix rounds already spent on this story.
        max_iterations: The configured cap. ``0`` disables the loop entirely.
        previous_hashes: Finding-set hashes from this story's earlier passes.
        review: Severity threshold and follow-up score weights.
        story_id: The story, named in the console line.

    Returns:
        A :class:`ReviewDecision`.

    Raises:
        ValueError: If no cap was supplied. The loop must not default to
            unbounded — an uncapped loop is the failure this exists to prevent.

    """
    if max_iterations is None or not isinstance(max_iterations, int):
        raise ValueError(
            "the review loop requires an explicit iteration cap "
            "(loop.review_max_iterations); it must never default to unbounded"
        )
    if max_iterations < 0:
        raise ValueError(f"the review loop cap must be >= 0, got {max_iterations}")

    if findings is None:
        detail = (
            "the review response could not be parsed into findings, so the result "
            "is neither 'clean' nor 'converged' and must not be read as either"
        )
        return ReviewDecision(
            outcome=ReviewOutcome.PARSE_FAILED,
            reason=detail,
            console_line=_blocked_console_line(
                ReviewOutcome.PARSE_FAILED, story_id, max_iterations, detail
            ),
        )

    finding_hash = findings.hash

    if max_iterations == 0:
        return ReviewDecision(
            outcome=ReviewOutcome.DISABLED,
            reason="review loop disabled (loop.review_max_iterations = 0)",
            finding_hash=finding_hash,
            blocking_count=len(findings.blocking(review.blocking_severity)),
        )

    blocking = findings.blocking(review.blocking_severity)
    if not blocking:
        return ReviewDecision(
            outcome=ReviewOutcome.CLEAN,
            reason=(
                f"no findings at or above {review.blocking_severity.label} in an "
                "actionable bucket"
            ),
            finding_hash=finding_hash,
        )

    if finding_hash in previous_hashes:
        detail = (
            f"the same {len(blocking)} blocking finding(s) came back unchanged after "
            "a fix round — the fixer is not moving the reviewer"
        )
        return ReviewDecision(
            outcome=ReviewOutcome.NON_CONVERGENT,
            reason=detail,
            finding_hash=finding_hash,
            blocking_count=len(blocking),
            console_line=_blocked_console_line(
                ReviewOutcome.NON_CONVERGENT, story_id, max_iterations, detail
            ),
        )

    if iteration >= max_iterations:
        detail = (
            f"{len(blocking)} blocking finding(s) remain after {iteration} fix "
            f"round(s), which is the configured cap"
        )
        return ReviewDecision(
            outcome=ReviewOutcome.CAP_EXHAUSTED,
            reason=detail,
            finding_hash=finding_hash,
            blocking_count=len(blocking),
            console_line=_blocked_console_line(
                ReviewOutcome.CAP_EXHAUSTED, story_id, max_iterations, detail
            ),
        )

    if not followup_review_recommended(
        findings.findings,
        medium_weight=review.followup_medium_weight,
        low_weight=review.followup_low_weight,
        threshold=review.followup_threshold,
    ):
        return ReviewDecision(
            outcome=ReviewOutcome.NOT_WORTH_IT,
            reason=(
                f"{len(blocking)} blocking finding(s), but the follow-up score is "
                f"below {review.followup_threshold} — recorded, not re-reviewed"
            ),
            finding_hash=finding_hash,
            blocking_count=len(blocking),
        )

    return ReviewDecision(
        outcome=ReviewOutcome.FIX,
        reason=f"{len(blocking)} blocking finding(s) worth one fix round",
        finding_hash=finding_hash,
        blocking_count=len(blocking),
    )
