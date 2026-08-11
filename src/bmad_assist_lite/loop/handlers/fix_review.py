"""FIX_REVIEW phase handler — address blocking review findings, then re-review.

A detour, not a listed phase: structurally identical to ``fix_quality_gate``,
reached only via a ``next_phase`` override from ``code_review_synthesis`` and
never a member of ``loop.story``.

It returns to ``code_review`` rather than straight to synthesis, so the
re-review is a genuine fresh multi-model pass over the fixed code. Reviewing
only through synthesis would mean one model checking a fix it had just
recommended — review diversity has to come from different models, not from
the same model in a different chair.
"""

import logging
from typing import Any

from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.autonomy import AutonomyLevel
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.types import PhaseResult

logger = logging.getLogger(__name__)


class FixReviewHandler(BaseHandler):
    """Master LLM addresses blocking review findings."""

    autonomy = AutonomyLevel.EXECUTE
    """Fixes review findings and re-runs the checks."""

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "fix_review"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context for this phase."""
        return self._build_common_context(state)

    def execute(self, state: State) -> PhaseResult:
        """Read the recorded findings, invoke the fixer, return to re-review."""
        try:
            story_id = state.current_story or "unknown"
            findings_path = (
                self.project_path
                / ".bmad-assist-lite"
                / "cache"
                / f"review-findings-{story_id}.md"
            )

            if findings_path.exists():
                findings_report = findings_path.read_text(encoding="utf-8")
            else:
                logger.warning("No review findings artifact at %s", findings_path)
                findings_report = "No structured findings were recorded."

            prompt = self.render_prompt(state)
            retry_note = ""
            if state.review_iteration > 1:
                retry_note = (
                    "\n\n<retry-context>\n"
                    f"This is review fix attempt #{state.review_iteration}. A previous "
                    "attempt did not clear the findings below. If a finding has come "
                    "back unchanged, the previous approach did not address its cause — "
                    "read the flagged code and its call sites before changing anything, "
                    "and choose a different strategy rather than re-applying the same "
                    "edit.\n</retry-context>"
                )

            full_prompt = (
                f"{prompt}{retry_note}\n\n"
                f"<review-findings>\n{findings_report}\n</review-findings>"
            )

            result = self.invoke_provider(full_prompt)

            if result.exit_code != 0:
                return PhaseResult.fail(
                    result.stderr or f"Provider exited with code {result.exit_code}"
                )

            return PhaseResult(
                success=True,
                next_phase=Phase.CODE_REVIEW,
                outputs={
                    "response": result.stdout,
                    "model": result.model,
                    "duration_ms": result.duration_ms,
                    "review_iteration": state.review_iteration,
                },
            )

        except Exception as e:
            logger.error("Fix review failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Fix review failed: {e}")
