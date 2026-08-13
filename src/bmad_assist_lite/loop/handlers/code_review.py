"""CODE_REVIEW phase handler with multi-LLM parallel review."""

import json
import logging
import os
from typing import Any

from bmad_assist_lite.core.async_utils import run_async_in_thread
from bmad_assist_lite.core.config import (
    get_phase_timeout,
    notch_down_effort,
    self_review_warning,
)
from bmad_assist_lite.core.git import git_diff
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.autonomy import AutonomyLevel
from bmad_assist_lite.loop.handlers import reviewer_reuse
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.review_merge import reviewer_findings_addendum
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers import get_provider
from bmad_assist_lite.providers.base import READ_ONLY_TOOLS, write_progress

logger = logging.getLogger(__name__)


class CodeReviewHandler(BaseHandler):
    """Multi-LLM code review with Evidence Score aggregation."""

    autonomy = AutonomyLevel.READ_ONLY
    """Multi-LLM and parallel: read-only checks only, no command execution."""

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "code_review"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context for this phase."""
        return self._build_common_context(state)

    def _warn_if_self_review(self) -> None:
        """Announce a degraded reviewer configuration at the point of harm."""
        warning = self_review_warning(self.config, phase=self.phase_name)
        if warning is None:
            return
        logger.warning("%s", warning)
        write_progress(f"  WARNING: {warning}")

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

    def _review_prompt(self, state: State) -> str:
        """Select and decorate the reviewer prompt for this round.

        Round-1 (or SP-2 off): the full compiled workflow. Round-2 with SP-2 on:
        a scoped delta prompt. SP-1 appends the structured-findings contract in
        either case.
        """
        if self.config.speed.delta_round2 and state.review_iteration >= 1:
            # The delta prompt is already lean + diff-scoped; no lean addendum.
            prompt = self._build_delta_review_prompt(state)
            write_progress(
                "  Round-2 delta review: scoped to the fix diff + round-1 findings"
            )
        else:
            prompt = self.render_prompt(state)
            # SP-3: inline the changed-code diff and demand findings-only,
            # diff-scoped reading (reviewers cannot run git themselves).
            if self.config.speed.lean_review:
                prompt = f"{prompt}\n\n{self._lean_review_addendum()}"
        if self.config.speed.structured_review:
            prompt = f"{prompt}\n\n{reviewer_findings_addendum()}"
        return prompt

    def _lean_review_addendum(self) -> str:
        """SP-3: inline the changed-code diff + findings-only, diff-scoped guidance."""
        diff = git_diff(self.project_path)
        diff_block = (
            f"<changed-code-diff>\n{diff}\n</changed-code-diff>\n\n" if diff else ""
        )
        return (
            f"{diff_block}"
            "<lean-review>\n"
            "Output findings ONLY — no step-by-step narration, no file-by-file "
            "walkthrough, no restating the story. Each finding is a specific "
            "file:line plus <= 25 words of claim/evidence.\n"
            "Review the changed-code diff above; open a file with Read ONLY when "
            "the diff is insufficient to judge a specific finding. Do not sweep the "
            "whole tree.\n"
            "</lean-review>"
        )

    def _build_delta_review_prompt(self, state: State) -> str:
        """SP-2: a fresh, scoped round-2 review prompt (no full artifacts, no resume).

        Reviewers are read-only and cannot run git, so the round-1 instruction to
        "use git to discover changes" is dead on round-2; this inlines the fix diff
        instead. The re-review checks only "were the round-1 blocking findings
        fixed, and did the fix break anything in the diff", per the review-ROI
        evidence that round-2 changes decisions without needing full context.
        """
        story_id = state.current_story or "unknown"
        cache = self.project_path / ".bmad-assist-lite" / "cache"
        findings_text = ""
        findings_path = cache / f"review-findings-{story_id}.md"
        try:
            if findings_path.is_file():
                findings_text = findings_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("delta round-2: could not read round-1 findings: %s", exc)
        diff = git_diff(self.project_path) or "(no uncommitted diff detected)"
        story_text = ""
        try:
            for path in sorted(self.project_path.glob(f"**/story-{story_id}.md")):
                if path.is_file():
                    story_text = path.read_text(encoding="utf-8")[:16000]
                    break
        except OSError as exc:
            logger.warning("delta round-2: could not read story file: %s", exc)
        story_block = f"<story>\n{story_text}\n</story>\n\n" if story_text else ""
        return (
            f"<mission>Round-2 re-review for story {story_id}. The round-1 findings "
            f"below were handed to a fixer. Your job is NARROW: verify each blocking "
            f"finding was actually fixed, and check the fix diff introduced no new "
            f"defects. Do NOT re-review the whole story from scratch.</mission>\n\n"
            f"<constraints>\n"
            f"- READ-ONLY: do not modify any files.\n"
            f"- Work from the inlined fix diff below; open a file only when the diff "
            f"is insufficient to judge a finding.\n"
            f"- Be concise: specific file:line findings only.\n"
            f"</constraints>\n\n"
            f"<round-1-findings>\n{findings_text}\n</round-1-findings>\n\n"
            f"<fix-diff>\n{diff}\n</fix-diff>\n\n"
            f"{story_block}"
        )

    def execute(self, state: State) -> PhaseResult:
        """Run parallel multi-LLM code review with Evidence Score aggregation."""
        try:
            self._warn_if_self_review()
            prompt = self._review_prompt(state)
            system_prompt = self.build_system_prompt(state)

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
                    p: Any, m: str, t: int, e: str | None, r: str | None
                ) -> Any:
                    return lambda: p.invoke(
                        prompt,
                        model=m,
                        timeout=t,
                        cwd=self.project_path,
                        allowed_tools=read_only_tools,
                        effort=e,
                        system_prompt=system_prompt,
                        resume=r,
                    )

                # L2: each reviewer lane may resume its OWN round-1 session on a
                # round-2 re-review. Keyed by story+phase+index+provider+model so
                # it can never cross a story/phase/lane boundary (F-13 structural).
                story_id = state.current_story
                lane_keys = [
                    reviewer_reuse.lane_key(
                        story_id, self.phase_name, idx, mc.provider, mc.model
                    )
                    for idx, mc in enumerate(multi_configs)
                ]

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(multi_configs)
                ) as executor:
                    futures = []
                    providers: list[Any] = []
                    # Stagger reviewer starts when a cached system prompt is in
                    # play so the first reviewer warms the shared stable-context
                    # cache before the next begins (otherwise concurrent reviewers
                    # race an unwarmed cache and each pays full price).
                    stagger = self.config.parallel_delay if system_prompt else 0.0
                    for idx, mc in enumerate(multi_configs):
                        provider = get_provider(mc.provider)
                        providers.append(provider)
                        resume_id = reviewer_reuse.resume_id_for(
                            state, self.config, provider=mc.provider, key=lane_keys[idx]
                        )
                        if idx > 0 and stagger > 0:
                            await asyncio.sleep(stagger)
                        # SP-3: reviewer lanes run one effort notch lower.
                        lane_effort = (
                            notch_down_effort(mc.effort)
                            if self.config.speed.lean_review
                            else mc.effort
                        )
                        futures.append(
                            loop.run_in_executor(
                                executor,
                                _make_invoker(
                                    provider, mc.model, timeout, lane_effort, resume_id
                                ),
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
                        # Carry this lane's session id forward for a round-2 resume.
                        reviewer_reuse.capture_session(
                            state,
                            self.config,
                            story_id=story_id,
                            provider=multi_configs[i].provider,
                            key=lane_keys[i],
                            result=raw,
                        )
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
