"""VALIDATE_STORY_SYNTHESIS phase handler.

Master LLM synthesizes Multi-LLM validation reports with pre-calculated
Evidence Score context injected into the prompt.
"""

import json
import logging
from typing import Any

from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.types import PhaseResult

logger = logging.getLogger(__name__)


class ValidateStorySynthesisHandler(BaseHandler):
    """Master LLM synthesizes multi-LLM validation reports."""

    @property
    def phase_name(self) -> str:
        return "validate_story_synthesis"

    def build_context(self, state: State) -> dict[str, Any]:
        return self._build_common_context(state)

    def _format_evidence_context(self, evidence_data: dict[str, Any] | None) -> str:
        """Format pre-calculated Evidence Score for synthesis prompt injection.

        Uses the full format_evidence_score_context() if we can reconstruct
        the aggregate, otherwise formats from the serialized cache data.
        """
        if evidence_data is None:
            return ""

        try:
            from bmad_assist_lite.validation.evidence_score import (
                EvidenceScoreAggregate,
                Severity,
                Verdict,
                format_evidence_score_context,
            )

            # Reconstruct the aggregate from cached data
            per_validator_scores = {
                vid: data["score"] for vid, data in evidence_data.get("per_validator", {}).items()
            }
            per_validator_verdicts = {
                vid: Verdict(data["verdict"])
                for vid, data in evidence_data.get("per_validator", {}).items()
            }
            findings_summary = evidence_data.get("findings_summary", {})
            findings_by_severity = {
                Severity.CRITICAL: findings_summary.get("CRITICAL", 0),
                Severity.IMPORTANT: findings_summary.get("IMPORTANT", 0),
                Severity.MINOR: findings_summary.get("MINOR", 0),
            }

            aggregate = EvidenceScoreAggregate(
                total_score=evidence_data["total_score"],
                verdict=Verdict(evidence_data["verdict"]),
                per_validator_scores=per_validator_scores,
                per_validator_verdicts=per_validator_verdicts,
                findings_by_severity=findings_by_severity,
                total_findings=evidence_data.get("total_findings", 0),
                total_clean_passes=evidence_data.get("total_clean_passes", 0),
                consensus_findings=(),  # Not serialized to cache
                unique_findings=(),  # Not serialized to cache
                consensus_ratio=evidence_data.get("consensus_ratio", 0.0),
            )

            return format_evidence_score_context(aggregate, context="validation")

        except Exception as e:
            logger.warning("Failed to format Evidence Score context: %s", e)
            # Fallback: simple text format
            score = evidence_data.get("total_score", "?")
            verdict = evidence_data.get("verdict", "?")
            return (
                f"\n\n<!-- PRE-CALCULATED EVIDENCE SCORE -->\n"
                f"## Evidence Score: {score} -> {verdict}\n"
                f"<!-- END PRE-CALCULATED EVIDENCE SCORE -->\n"
            )

    def execute(self, state: State) -> PhaseResult:
        """Execute synthesis with cached validations and Evidence Score context."""
        try:
            # Load cached validations
            cache_file = self.project_path / ".bmad-assist-lite" / "cache" / "validations.json"
            if not cache_file.exists():
                return PhaseResult.fail("No cached validations found for synthesis")

            cache_data = json.loads(cache_file.read_text())
            validations = cache_data.get("validations", cache_data)
            evidence_data = cache_data.get("evidence_score")

            # Handle legacy format (list instead of dict)
            if isinstance(validations, list):
                pass  # Already a list
            elif isinstance(validations, dict):
                validations = validations.get("validations", [])

            # Build synthesis prompt with validations embedded
            prompt = self.render_prompt(state)

            # Format Evidence Score context for injection
            evidence_context = self._format_evidence_context(evidence_data)

            # Build validation reports text
            validation_text = "\n\n".join(
                f"=== {v.get('validator', 'Unknown')} ===\n"
                f"{v.get('response', v.get('error', 'No output'))}"
                for v in validations
            )

            # Compose full prompt with evidence context + validation reports
            full_prompt = (
                f"{prompt}\n\n"
                f"{evidence_context}\n\n"
                f"<validation-reports>\n{validation_text}\n</validation-reports>"
            )

            result = self.invoke_provider(full_prompt)

            if result.exit_code != 0:
                return PhaseResult.fail(
                    result.stderr or f"Provider exited with code {result.exit_code}"
                )

            outputs: dict[str, Any] = {
                "response": result.stdout,
                "model": result.model,
                "duration_ms": result.duration_ms,
                "validations_synthesized": len(validations),
            }
            if evidence_data:
                outputs["evidence_score"] = evidence_data.get("total_score")
                outputs["evidence_verdict"] = evidence_data.get("verdict")

            return PhaseResult.ok(outputs)

        except Exception as e:
            logger.error("Synthesis handler failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Synthesis failed: {e}")
