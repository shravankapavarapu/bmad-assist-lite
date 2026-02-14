"""VALIDATE_STORY phase handler with multi-LLM parallel validation."""

import json
import logging
import os
from typing import Any

from bmad_assist_lite.core.async_utils import run_async_in_thread
from bmad_assist_lite.core.config import get_phase_timeout
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers import get_provider
from bmad_assist_lite.providers.base import write_progress

logger = logging.getLogger(__name__)


class ValidateStoryHandler(BaseHandler):
    """Multi-LLM story validation with Evidence Score aggregation."""

    @property
    def phase_name(self) -> str:
        return "validate_story"

    def build_context(self, state: State) -> dict[str, Any]:
        return self._build_common_context(state)

    def _calculate_evidence_aggregate(
        self,
        validations: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Parse evidence scores from validator outputs and aggregate.

        Returns serializable dict for cache storage, or None if parsing fails.
        """
        try:
            from bmad_assist_lite.validation.evidence_score import (
                Severity,
                aggregate_evidence_scores,
                parse_evidence_findings,
            )

            reports = []
            for v in validations:
                if v.get("exit_code") != 0:
                    continue
                content = v.get("response", "")
                validator_id = v.get("validator", "Unknown")
                report = parse_evidence_findings(content, validator_id)
                if report is not None:
                    reports.append(report)
                    logger.debug(
                        "Parsed evidence report for %s: score=%.1f",
                        validator_id,
                        report.total_score,
                    )

            if not reports:
                logger.warning("No valid Evidence Score reports found in validations")
                return None

            aggregate = aggregate_evidence_scores(reports)
            logger.info(
                "Evidence Score aggregate: total=%.1f, verdict=%s, validators=%d",
                aggregate.total_score,
                aggregate.verdict.value,
                len(reports),
            )

            # Serialize for JSON cache storage
            return {
                "total_score": aggregate.total_score,
                "verdict": aggregate.verdict.value,
                "per_validator": {
                    vid: {
                        "score": aggregate.per_validator_scores[vid],
                        "verdict": aggregate.per_validator_verdicts[vid].value,
                    }
                    for vid in aggregate.per_validator_scores
                },
                "findings_summary": {
                    "CRITICAL": aggregate.findings_by_severity.get(Severity.CRITICAL, 0),
                    "IMPORTANT": aggregate.findings_by_severity.get(Severity.IMPORTANT, 0),
                    "MINOR": aggregate.findings_by_severity.get(Severity.MINOR, 0),
                },
                "total_findings": aggregate.total_findings,
                "total_clean_passes": aggregate.total_clean_passes,
                "consensus_ratio": aggregate.consensus_ratio,
                "consensus_count": len(aggregate.consensus_findings),
                "unique_count": len(aggregate.unique_findings),
            }
        except Exception as e:
            logger.warning("Evidence Score calculation failed: %s", e)
            return None

    def execute(self, state: State) -> PhaseResult:
        """Run parallel multi-LLM validation with Evidence Score aggregation."""
        try:
            prompt = self.render_prompt(state)

            # Get multi-provider configs
            multi_configs = self.config.providers.multi
            if not multi_configs:
                # Fallback to single master validation
                return super().execute(state)

            import asyncio
            import concurrent.futures

            providers_desc = ", ".join(
                f"{mc.provider}({mc.model or 'default'})" for mc in multi_configs
            )
            write_progress(
                f"  Running {len(multi_configs)} validators in parallel: {providers_desc}"
            )

            async def _run_validations() -> list[dict[str, Any]]:
                loop = asyncio.get_event_loop()
                timeout = get_phase_timeout(self.config, self.phase_name)

                def _make_invoker(
                    p: Any, m: str, t: int
                ) -> Any:
                    return lambda: p.invoke(prompt, model=m, timeout=t, cwd=self.project_path)

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(multi_configs)
                ) as executor:
                    futures = []
                    for mc in multi_configs:
                        provider = get_provider(mc.provider)
                        futures.append(
                            loop.run_in_executor(
                                executor,
                                _make_invoker(provider, mc.model, timeout),
                            )
                        )

                    raw_results = await asyncio.gather(*futures, return_exceptions=True)

                results: list[dict[str, Any]] = []
                for i, raw in enumerate(raw_results):
                    label = f"Validator-{i + 1}"
                    if isinstance(raw, BaseException):
                        logger.warning("%s failed: %s", label, raw)
                        results.append(
                            {"validator": label, "error": str(raw), "exit_code": 1}
                        )
                    else:
                        results.append(
                            {
                                "validator": label,
                                "response": raw.stdout,
                                "exit_code": raw.exit_code,
                            }
                        )
                return results

            validations = run_async_in_thread(_run_validations())

            successful = [v for v in validations if v.get("exit_code") == 0]
            write_progress(
                f"  Validators complete: {len(successful)}/{len(multi_configs)} succeeded"
            )

            if not successful:
                return PhaseResult.fail("All validators failed")

            # Calculate Evidence Score aggregate from validator outputs
            evidence_aggregate = self._calculate_evidence_aggregate(validations)
            if evidence_aggregate:
                write_progress(
                    f"  Evidence Score: {evidence_aggregate['total_score']:.1f}"
                    f" -> {evidence_aggregate['verdict']}"
                )
            else:
                write_progress("  Evidence Score: could not be calculated")

            # Save validations and evidence aggregate for synthesis
            cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)

            cache_data = {
                "validations": validations,
                "evidence_score": evidence_aggregate,
            }
            cache_file = cache_dir / "validations.json"
            temp_file = cache_file.with_suffix(".json.tmp")
            temp_file.write_text(json.dumps(cache_data, indent=2))
            os.replace(temp_file, cache_file)

            outputs: dict[str, Any] = {
                "validation_count": len(successful),
                "total_validators": len(multi_configs),
            }
            if evidence_aggregate:
                outputs["evidence_score"] = evidence_aggregate["total_score"]
                outputs["evidence_verdict"] = evidence_aggregate["verdict"]

            return PhaseResult.ok(outputs)

        except Exception as e:
            logger.error("Validation handler failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Validation failed: {e}")
