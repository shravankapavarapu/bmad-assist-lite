"""FIX_QUALITY_GATE phase handler — LLM-based fix attempt.

Reads the quality gate failure report and asks the master LLM to fix the issues.
Always returns next_phase=QUALITY_GATE to re-run checks.
"""

import logging
from typing import Any

from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.types import PhaseResult

logger = logging.getLogger(__name__)


class FixQualityGateHandler(BaseHandler):
    """Master LLM fixes quality gate failures."""

    @property
    def phase_name(self) -> str:
        return "fix_quality_gate"

    def build_context(self, state: State) -> dict[str, Any]:
        return self._build_common_context(state)

    def execute(self, state: State) -> PhaseResult:
        """Read failure report, invoke LLM, return to quality_gate."""
        try:
            # Read failure report from cache
            story_id = state.current_story or "unknown"
            report_path = (
                self.project_path
                / ".bmad-assist-lite"
                / "cache"
                / f"qa-failures-{story_id}.md"
            )

            failure_report = ""
            if report_path.exists():
                failure_report = report_path.read_text(encoding="utf-8")
            else:
                logger.warning("No failure report found at %s", report_path)
                failure_report = "No failure report available."

            # Render prompt and append failure report
            prompt = self.render_prompt(state)
            full_prompt = (
                f"{prompt}\n\n"
                f"<qa-failure-report>\n{failure_report}\n</qa-failure-report>"
            )

            result = self.invoke_provider(full_prompt)

            if result.exit_code != 0:
                error_msg = result.stderr or f"Provider exited with code {result.exit_code}"
                return PhaseResult.fail(error_msg)

            # Always return to quality_gate for re-check
            return PhaseResult(
                success=True,
                next_phase=Phase.QUALITY_GATE,
                outputs={
                    "response": result.stdout,
                    "model": result.model,
                    "duration_ms": result.duration_ms,
                },
            )

        except Exception as e:
            logger.error("Fix quality gate failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Fix quality gate failed: {e}")
