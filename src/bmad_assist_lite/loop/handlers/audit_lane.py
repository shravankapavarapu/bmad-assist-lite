"""The AC-completeness audit lane's prompt, shared by its two call sites.

The audit lane has two places it can run:

1. Inside ``code_review``, as one more parallel lane beside the reviewers,
   when the trigger fires for the round (``ac_audit.enabled``, or ``auto``
   with a risk signal, an escalation, or the final round).
2. On exit, from ``code_review_synthesis``, when a clean FULL round is about
   to become the promoting round but the trigger stayed quiet on it. A clean
   round-1 exit happens at ``review_iteration == 0``, where the ``auto``
   trigger's final-round rule never fires; without this second call site the
   story's only round carries no audit and the three-witness gate
   (:mod:`bmad_assist_lite.core.verdict`) parks it for "audit did not run on
   the final round" — a park caused by the trigger's timing, not by anything
   wrong with the story.

Both call sites must send the SAME prompt: the audit is a gate, and a gate
that asks a different question depending on which path reached it is two
gates. So the prompt is built here, once.

The diff is taken as an argument rather than read here. Each caller reads it
through its own module's ``git_diff`` binding, which keeps the orientation
block patchable per call site in tests and keeps this module free of git.
"""

from __future__ import annotations

from bmad_assist_lite.loop.review_merge import reviewer_findings_addendum

__all__ = ["MAX_INLINE_DIFF_CHARS", "build_audit_prompt", "cap_inline_diff"]

#: Hard cap on a diff inlined into a reviewer prompt (~15K tokens). A
#: lockfile-sized change set must not blow the very prompt these levers exist
#: to shrink; reviewers can Read the files for anything past the cap.
MAX_INLINE_DIFF_CHARS = 60_000


def cap_inline_diff(diff: str) -> str:
    """Truncate an inlined diff at the cap, with an explicit marker."""
    if len(diff) <= MAX_INLINE_DIFF_CHARS:
        return diff
    return (
        diff[:MAX_INLINE_DIFF_CHARS]
        + "\n... [diff truncated for length — read the remaining files directly]\n"
    )


def build_audit_prompt(
    rendered_workflow: str,
    diff: str | None,
    *,
    structured_review: bool,
) -> str:
    """Build the AC-completeness audit prompt (ac_audit lever).

    Always the FULL audit, never a delta-scoped one: the audit must
    re-verify every criterion end to end, because a fix can complete one
    acceptance criterion while leaving another still partial.

    Args:
        rendered_workflow: The compiled ``ac-audit`` workflow, from the
            caller's ``render_prompt(state, workflow_name="ac-audit")``. That
            workflow REQUIRES the epic file, and a resolution failure raises a
            ``ConfigError`` at the caller before this runs, so the audit never
            silently goes out without the authoritative criteria.
        diff: The working-tree diff, or None/blank when there is none. It is
            orientation only: the audit target is the code as it is now.
        structured_review: Whether ``speed.structured_review`` is on, in which
            case the structured-findings contract is appended so the lane's
            findings survive the deterministic merge.

    """
    prompt = rendered_workflow
    if diff and diff.strip():
        prompt = (
            f"{prompt}\n\n"
            f"<changed-code-diff>\n{cap_inline_diff(diff)}\n</changed-code-diff>\n"
            "The diff above shows what this story changed, for ORIENTATION only. "
            "Your audit target is the code as it is NOW — evidence for a criterion "
            "may live in files the diff never touched, and a file the diff should "
            "have touched but did not is exactly what you exist to catch.\n"
        )
    if structured_review:
        prompt = f"{prompt}\n\n{reviewer_findings_addendum()}"
    return prompt
