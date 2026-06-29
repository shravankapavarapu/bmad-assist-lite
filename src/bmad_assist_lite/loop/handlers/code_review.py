"""CODE_REVIEW phase handler with multi-LLM parallel review."""

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
from bmad_assist_lite.providers.base import READ_ONLY_TOOLS, write_progress

logger = logging.getLogger(__name__)


class CodeReviewHandler(BaseHandler):
    """Multi-LLM code review with Evidence Score aggregation."""

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "code_review"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context for this phase."""
        return self._build_common_context(state)

    def _calculate_evidence_aggregate(
        self,
        reviews: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Parse evidence scores from reviewer outputs and aggregate.

        Returns serializable dict for cache storage, or None if parsing fails.
        """
        try:
            from bmad_assist_lite.validation.evidence_score import (
                Severity,
                aggregate_evidence_scores,
                parse_evidence_findings,
            )

            reports = []
            for r in reviews:
                if r.get("exit_code") != 0:
                    continue
                content = r.get("response", "")
                reviewer_id = r.get("reviewer", "Unknown")
                report = parse_evidence_findings(content, reviewer_id)
                if report is not None:
                    reports.append(report)
                    logger.debug(
                        "Parsed evidence report for %s: score=%.1f",
                        reviewer_id,
                        report.total_score,
                    )

            if not reports:
                logger.warning("No valid Evidence Score reports found in code reviews")
                return None

            aggregate = aggregate_evidence_scores(reports)
            logger.info(
                "Evidence Score aggregate: total=%.1f, verdict=%s, reviewers=%d",
                aggregate.total_score,
                aggregate.verdict.value,
                len(reports),
            )

            return {
                "total_score": aggregate.total_score,
                "verdict": aggregate.verdict.value,
                "per_reviewer": {
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
        """Run parallel multi-LLM code review with Evidence Score aggregation."""
        try:
            prompt = self.render_prompt(state)

            multi_configs = self.config.providers.multi
            if not multi_configs:
                return super().execute(state)

            import asyncio
            import concurrent.futures

            providers_desc = ", ".join(
                f"{mc.provider}({mc.model or 'default'})" for mc in multi_configs
            )
            write_progress(
                f"  Running {len(multi_configs)} reviewers in parallel: {providers_desc}"
            )

            async def _run_reviews() -> list[dict[str, Any]]:
                loop = asyncio.get_event_loop()
                timeout = get_phase_timeout(self.config, self.phase_name)

                # Read-only tools: multi-LLM safety constraint. Bash is
                # excluded so reviewers cannot run shell commands (e.g. git)
                # during parallel review. See READ_ONLY_TOOLS in providers.base.
                read_only_tools = list(READ_ONLY_TOOLS)

                def _make_invoker(
                    p: Any, m: str, t: int, e: str | None
                ) -> Any:
                    return lambda: p.invoke(
                        prompt,
                        model=m,
                        timeout=t,
                        cwd=self.project_path,
                        allowed_tools=read_only_tools,
                        effort=e,
                    )

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(multi_configs)
                ) as executor:
                    futures = []
                    providers: list[Any] = []
                    for mc in multi_configs:
                        provider = get_provider(mc.provider)
                        providers.append(provider)
                        futures.append(
                            loop.run_in_executor(
                                executor,
                                _make_invoker(provider, mc.model, timeout, mc.effort),
                            )
                        )

                    raw_results = await asyncio.gather(*futures, return_exceptions=True)

                results: list[dict[str, Any]] = []
                for i, raw in enumerate(raw_results):
                    label = f"Reviewer-{i + 1}"
                    if isinstance(raw, BaseException):
                        logger.warning("%s failed: %s", label, raw)
                        results.append(
                            {"reviewer": label, "error": str(raw), "exit_code": 1}
                        )
                    else:
                        response = providers[i].parse_output(raw)
                        results.append(
                            {
                                "reviewer": label,
                                "response": response,
                                "exit_code": raw.exit_code,
                            }
                        )
                return results

            reviews = run_async_in_thread(_run_reviews())

            successful = [r for r in reviews if r.get("exit_code") == 0]
            write_progress(
                f"  Reviewers complete: {len(successful)}/{len(multi_configs)} succeeded"
            )

            if not successful:
                return PhaseResult.fail("All reviewers failed")

            # Calculate Evidence Score aggregate from reviewer outputs
            evidence_aggregate = self._calculate_evidence_aggregate(reviews)
            if evidence_aggregate:
                write_progress(
                    f"  Evidence Score: {evidence_aggregate['total_score']:.1f}"
                    f" -> {evidence_aggregate['verdict']}"
                )
            else:
                write_progress("  Evidence Score: could not be calculated")

            # Save reviews and evidence aggregate for synthesis
            cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)

            cache_data = {
                "reviews": reviews,
                "evidence_score": evidence_aggregate,
            }
            cache_file = cache_dir / "reviews.json"
            temp_file = cache_file.with_suffix(".json.tmp")
            temp_file.write_text(json.dumps(cache_data, indent=2))
            os.replace(temp_file, cache_file)

            outputs: dict[str, Any] = {
                "review_count": len(successful),
                "total_reviewers": len(multi_configs),
            }
            if evidence_aggregate:
                outputs["evidence_score"] = evidence_aggregate["total_score"]
                outputs["evidence_verdict"] = evidence_aggregate["verdict"]

            return PhaseResult.ok(outputs)

        except Exception as e:
            logger.error("Code review handler failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Code review failed: {e}")
