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
from bmad_assist_lite.loop.handlers.ac_audit_trigger import (
    record_audit_trigger,
    resolve_ac_audit_enabled,
)
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.review_merge import reviewer_findings_addendum
from bmad_assist_lite.loop.story_paths import resolve_story_path
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers import get_provider
from bmad_assist_lite.providers.base import READ_ONLY_TOOLS, write_progress

logger = logging.getLogger(__name__)

#: Hard cap on a diff inlined into a reviewer prompt (~15K tokens). A
#: lockfile-sized change set must not blow the very prompt these levers exist
#: to shrink; reviewers can Read the files for anything past the cap.
_MAX_INLINE_DIFF_CHARS = 60_000


def _cap_inline_diff(diff: str) -> str:
    """Truncate an inlined diff at the cap, with an explicit marker."""
    if len(diff) <= _MAX_INLINE_DIFF_CHARS:
        return diff
    return (
        diff[:_MAX_INLINE_DIFF_CHARS]
        + "\n... [diff truncated for length — read the remaining files directly]\n"
    )


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

    def _is_delta_round(self, state: State) -> bool:
        """True when this call is a round-2+ re-review of the CURRENT story.

        ``review_iteration`` is only reset by the synthesis handler when it
        notices a story change — and the synthesis runs AFTER this phase. On the
        first review of a new story the counter therefore still holds the
        previous story's rounds; the ``review_story_id`` guard keeps that stale
        counter from turning a fresh story's round-1 into a scoped delta review.
        """
        return (
            self.config.speed.delta_round2
            and state.review_iteration >= 1
            and state.review_story_id == state.current_story
        )

    def _review_prompt(self, state: State) -> str:
        """Select and decorate the reviewer prompt for this round.

        Round-1 (or SP-2 off): the full compiled workflow. Round-2 with SP-2 on:
        a scoped delta prompt — unless the round-1 findings are unavailable, in
        which case the full prompt is used so the re-review is never vacuous.
        SP-1 appends the structured-findings contract in either case.
        """
        delta_prompt = (
            self._build_delta_review_prompt(state)
            if self._is_delta_round(state)
            else None
        )
        if delta_prompt is not None:
            # The delta prompt is already lean + diff-scoped; no lean addendum.
            prompt = delta_prompt
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

    def _audit_prompt(self, state: State) -> str:
        """Build the AC-completeness audit lane's prompt (ac_audit lever).

        Always the FULL audit — never the SP-2 delta prompt, even on round-2:
        after a fix round the audit must re-verify every criterion end-to-end,
        because a fix can complete one AC while leaving another still partial.
        The compiled ac-audit workflow REQUIRES the epic file; a resolution
        failure surfaces as a ConfigError, failing the phase loudly before any
        lane spends tokens — the audit never silently runs without the
        authoritative acceptance criteria.
        """
        prompt = self.render_prompt(state, workflow_name="ac-audit")
        diff = git_diff(self.project_path)
        if diff and diff.strip():
            prompt = (
                f"{prompt}\n\n"
                f"<changed-code-diff>\n{_cap_inline_diff(diff)}\n</changed-code-diff>\n"
                "The diff above shows what this story changed, for ORIENTATION only. "
                "Your audit target is the code as it is NOW — evidence for a criterion "
                "may live in files the diff never touched, and a file the diff should "
                "have touched but did not is exactly what you exist to catch.\n"
            )
        if self.config.speed.structured_review:
            prompt = f"{prompt}\n\n{reviewer_findings_addendum()}"
        return prompt

    #: Lane label for the AC-completeness auditor; also its findings `source` tag.
    AUDIT_LANE_LABEL = "AC-Auditor"

    def _build_lanes(self, state: State) -> list[dict[str, Any]]:
        """Assemble the parallel review lanes: N reviewers, plus the AC auditor.

        Whether the AC-auditor lane is appended is resolved per story by
        :func:`~bmad_assist_lite.loop.handlers.ac_audit_trigger.resolve_ac_audit_enabled`
        (goal-run11): ``enabled`` forces it on, ``auto`` decides from worktree-local
        signals, and both-off is byte-identical to the pre-lever reviewer set
        (same prompts, same efforts, no signal gathering, no record). The audit
        lane runs on the MASTER provider config at the master's effort: it is a
        gate, not a reviewer, so the SP-3 lean-review effort notch does not apply.
        """
        prompt = self._review_prompt(state)
        lanes: list[dict[str, Any]] = []
        for idx, mc in enumerate(self.config.providers.multi):
            lane_effort = (
                notch_down_effort(mc.effort)
                if self.config.speed.lean_review
                else mc.effort
            )
            lanes.append(
                {
                    "label": f"Reviewer-{idx + 1}",
                    "provider": mc.provider,
                    "model": mc.model,
                    "effort": lane_effort,
                    "prompt": prompt,
                }
            )
        decision = resolve_ac_audit_enabled(self.config, state, self.project_path)
        record_audit_trigger(decision, state)
        if decision.fire and lanes:
            master = self.config.providers.master
            lanes.append(
                {
                    "label": self.AUDIT_LANE_LABEL,
                    "provider": master.provider,
                    "model": master.model,
                    "effort": master.effort,
                    "prompt": self._audit_prompt(state),
                }
            )
        return lanes

    def _lean_review_addendum(self) -> str:
        """SP-3: inline the changed-code diff + findings-only, diff-scoped guidance."""
        diff = git_diff(self.project_path)
        if diff and diff.strip():
            diff_block = (
                f"<changed-code-diff>\n{_cap_inline_diff(diff)}\n</changed-code-diff>\n\n"
            )
            scope_text = (
                "Review the changed-code diff above; open a file with Read ONLY when "
                "the diff is insufficient to judge a specific finding. This "
                "supersedes any earlier instruction to read every file in the File "
                "List. Do not sweep the whole tree.\n"
            )
        else:
            # No diff to scope to (work already committed, or git unavailable):
            # keep the findings-only economy but leave discovery instructions alone.
            diff_block = ""
            scope_text = (
                "Scope your reading to the story's changed files; do not sweep the "
                "whole tree.\n"
            )
        return (
            f"{diff_block}"
            "<lean-review>\n"
            "Output findings ONLY — no step-by-step narration, no file-by-file "
            "walkthrough, no restating the story. Each finding is a specific "
            "file:line plus <= 25 words of claim/evidence.\n"
            f"{scope_text}"
            "</lean-review>"
        )

    def _build_delta_review_prompt(self, state: State) -> str | None:
        """SP-2: a fresh, scoped round-2 review prompt (no full artifacts, no resume).

        Reviewers are read-only and cannot run git, so the round-1 instruction to
        "use git to discover changes" is dead on round-2; this inlines the fix diff
        instead. The re-review checks only "were the round-1 blocking findings
        fixed, and did the fix break anything in the diff", per the review-ROI
        evidence that round-2 changes decisions without needing full context.

        Returns None when the round-1 findings are missing or empty — a scoped
        "verify the fixes" prompt with nothing to verify is worse than a full
        re-review, so the caller falls back to the full prompt.
        """
        story_id = state.current_story or "unknown"
        cache = self.project_path / ".bmad-assist-lite" / "cache"
        findings_text = ""
        findings_path = cache / f"review-findings-{story_id}.md"
        try:
            if findings_path.is_file():
                findings_text = findings_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("delta round-2: could not read round-1 findings: %s", exc)
        if not findings_text.strip():
            logger.warning(
                "delta round-2: no round-1 findings for story %s; "
                "falling back to the full review prompt",
                story_id,
            )
            write_progress(
                "  Round-2: round-1 findings unavailable — running a full re-review"
            )
            return None
        diff = git_diff(self.project_path)
        diff = _cap_inline_diff(diff) if diff else "(no uncommitted diff detected)"
        story_text = ""
        try:
            story_path = resolve_story_path(story_id)
        except RuntimeError:
            # Paths not initialized (tests / standalone tooling): skip the story
            # block rather than fail the phase.
            story_path = None
        if story_path is not None:
            try:
                story_text = story_path.read_text(encoding="utf-8")[:16000]
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("delta round-2: could not read story file: %s", exc)
        else:
            logger.warning("delta round-2: no story file resolved for %s", story_id)
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
            system_prompt = self.build_system_prompt(state)

            multi_configs = self.config.providers.multi
            if not multi_configs:
                if self.config.ac_audit.enabled or self.config.ac_audit.auto:
                    msg = (
                        "ac_audit is active (enabled or auto) but providers.multi "
                        "is empty — the AC-completeness audit lane only runs "
                        "alongside the multi-reviewer path and was SKIPPED. Fix: "
                        "configure providers.multi with at least one reviewer."
                    )
                    logger.warning("%s", msg)
                    write_progress(f"  WARNING: {msg}")
                return super().execute(state)

            lanes = self._build_lanes(state)

            import asyncio
            import concurrent.futures

            lanes_desc = ", ".join(
                f"{lane['label']}={lane['provider']}({lane['model'] or 'default'})"
                for lane in lanes
            )
            write_progress(
                f"  Running {len(lanes)} review lanes in parallel: {lanes_desc}"
            )

            async def _run_reviews() -> list[dict[str, Any]]:
                loop = asyncio.get_event_loop()
                timeout = get_phase_timeout(self.config, self.phase_name)

                # Read-only tools: multi-LLM safety constraint. Bash is
                # excluded so reviewers cannot run shell commands (e.g. git)
                # during parallel review. See READ_ONLY_TOOLS in providers.base.
                read_only_tools = list(READ_ONLY_TOOLS)

                def _make_invoker(
                    p: Any, lane_prompt: str, m: str, t: int, e: str | None
                ) -> Any:
                    return lambda: p.invoke(
                        lane_prompt,
                        model=m,
                        timeout=t,
                        cwd=self.project_path,
                        allowed_tools=read_only_tools,
                        effort=e,
                        system_prompt=system_prompt,
                    )

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(lanes)
                ) as executor:
                    futures = []
                    providers: list[Any] = []
                    # Stagger reviewer starts so reviewer-1 can warm the shared
                    # cached system prompt before the others begin (SP-4 drops it).
                    stagger = self._reviewer_stagger(system_prompt)
                    for idx, lane in enumerate(lanes):
                        provider = get_provider(lane["provider"])
                        providers.append(provider)
                        if idx > 0 and stagger > 0:
                            await asyncio.sleep(stagger)
                        futures.append(
                            loop.run_in_executor(
                                executor,
                                _make_invoker(
                                    provider,
                                    lane["prompt"],
                                    lane["model"],
                                    timeout,
                                    lane["effort"],
                                ),
                            )
                        )

                    raw_results = await asyncio.gather(*futures, return_exceptions=True)

                results: list[dict[str, Any]] = []
                for i, raw in enumerate(raw_results):
                    label = lanes[i]["label"]
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
                f"  Review lanes complete: {len(successful)}/{len(lanes)} succeeded"
            )

            if not successful:
                return PhaseResult.fail("All reviewers failed")

            # The audit lane is a GATE, not a reviewer: proceeding without it
            # would silently recreate the blind spot it exists to close (an
            # unverified AC reads as a clean review). A failed audit lane fails
            # the phase; --resume re-runs it.
            failed_audit = next(
                (
                    r
                    for r in reviews
                    if r.get("reviewer") == self.AUDIT_LANE_LABEL
                    and r.get("exit_code") != 0
                ),
                None,
            )
            if failed_audit is not None:
                return PhaseResult.fail(
                    "AC-completeness audit lane failed: "
                    f"{failed_audit.get('error', 'nonzero exit')} — "
                    "the review cannot be trusted without it (ac_audit)"
                )

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
                "total_reviewers": len(lanes),
            }
            if evidence_aggregate:
                outputs["evidence_score"] = evidence_aggregate["total_score"]
                outputs["evidence_verdict"] = evidence_aggregate["verdict"]

            return PhaseResult.ok(outputs)

        except Exception as e:
            logger.error("Code review handler failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Code review failed: {e}")
