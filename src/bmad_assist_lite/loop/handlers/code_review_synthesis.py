"""CODE_REVIEW_SYNTHESIS phase handler.

Master LLM synthesizes Multi-LLM code review reports with pre-calculated
Evidence Score context injected into the prompt.
"""

import json
import logging
import re
from typing import Any

from bmad_assist_lite.core.git import git_diff
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.autonomy import AutonomyLevel
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.review_loop import ReviewDecision, decide_review_loop
from bmad_assist_lite.loop.review_merge import (
    high_severity_preserved,
    merge_findings,
    parse_reviewer_findings,
    render_adjudication_candidates,
)
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers.base import write_progress
from bmad_assist_lite.validation.findings import (
    FindingParseError,
    FindingSet,
    parse_findings,
    render_findings_block,
)

logger = logging.getLogger(__name__)

# Regex for headings whose body is pure file-exploration narration
_EXPLORATION_HEADING_RE = re.compile(
    r"^#{1,4}\s+(?:Step\s+\d+\s*[:\-–—]\s*)?"
    r"(?:Load|Discover|Examine|Read|Setup|Initial)\b",
    re.IGNORECASE,
)


def _strip_review_narration(text: str) -> str:
    """Remove file-exploration narration sections from a reviewer response.

    Strips sections whose heading matches common exploration patterns
    (e.g. "## Step 1: Load Story and Discover Changes") since their body
    is tool-use narration, not actionable review content.

    Returns the trimmed text.  No-op if no exploration sections found.
    """
    lines = text.split("\n")
    # Find and remove exploration sections
    i = 0
    kept: list[str] = []
    while i < len(lines):
        line = lines[i]
        if line.startswith("#") and _EXPLORATION_HEADING_RE.match(line):
            # Determine heading level
            match = re.match(r"^(#+)", line)
            level = len(match.group(1)) if match else 2
            # Skip until next heading at same or higher level
            i += 1
            while i < len(lines):
                if lines[i].startswith("#"):
                    m = re.match(r"^(#+)", lines[i])
                    if m and len(m.group(1)) <= level:
                        break
                i += 1
            continue
        kept.append(line)
        i += 1

    result = "\n".join(kept).strip()
    if len(result) < len(text.strip()):
        stripped = len(text.strip()) - len(result)
        logger.info(
            "Stripped %d chars of reviewer narration (~%d tokens)",
            stripped,
            stripped // 4,
        )
    return result


class CodeReviewSynthesisHandler(BaseHandler):
    """Master LLM synthesizes multi-LLM code review reports."""

    autonomy = AutonomyLevel.EXECUTE
    """Single master, so it is safe to run the build/test/lint commands."""

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "code_review_synthesis"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context for this phase."""
        return self._build_common_context(state)

    def _format_evidence_context(self, evidence_data: dict[str, Any] | None) -> str:
        """Format pre-calculated Evidence Score for synthesis prompt injection."""
        if evidence_data is None:
            return ""

        try:
            from bmad_assist_lite.validation.evidence_score import (
                EvidenceScoreAggregate,
                Severity,
                Verdict,
                format_evidence_score_context,
            )

            # Reconstruct aggregate from cached data
            # Code review uses "per_reviewer" key
            per_reviewer = evidence_data.get("per_reviewer", evidence_data.get("per_validator", {}))
            per_validator_scores = {vid: data["score"] for vid, data in per_reviewer.items()}
            per_validator_verdicts = {
                vid: Verdict(data["verdict"]) for vid, data in per_reviewer.items()
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
                consensus_findings=(),
                unique_findings=(),
                consensus_ratio=evidence_data.get("consensus_ratio", 0.0),
            )

            return format_evidence_score_context(aggregate, context="code_review")

        except Exception as e:
            logger.warning("Failed to format Evidence Score context: %s", e)
            score = evidence_data.get("total_score", "?")
            verdict = evidence_data.get("verdict", "?")
            return (
                f"\n\n<!-- PRE-CALCULATED EVIDENCE SCORE -->\n"
                f"## Evidence Score: {score} -> {verdict}\n"
                f"<!-- END PRE-CALCULATED EVIDENCE SCORE -->\n"
            )

    def _reset_review_state_for_story(self, state: State) -> None:
        """Start a fresh review-loop budget when the story changes.

        The hashes are per-story by definition — a finding set from the last
        story colliding with this one's would read as non-convergence.
        """
        story_id = state.current_story
        if state.review_story_id != story_id:
            state.review_story_id = story_id
            state.review_iteration = 0
            state.review_finding_hashes = []

    def _parse_review_findings(self, text: str) -> FindingSet | None:
        """Parse the synthesis response, returning ``None`` on a parse failure.

        ``None`` is not an empty finding set. An empty set means "clean
        review"; a parse failure means we do not know, and the caller must
        never collapse the two.
        """
        try:
            return parse_findings(text)
        except FindingParseError as exc:
            logger.warning(
                "Could not parse review findings for story: %s. The result is "
                "NOT being treated as a clean review.",
                exc,
            )
            return None

    def _record_findings_artifact(
        self, findings: FindingSet | None, decision: ReviewDecision, story_id: str
    ) -> None:
        """Persist the machine-readable finding set beside the human report.

        Written whatever the outcome, including for below-threshold findings:
        they are culled from the loop, not from the record.
        """
        cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"review-findings-{story_id}.md"

        lines = [
            f"# Review findings — story {story_id}",
            "",
            f"- outcome: `{decision.outcome.value}`",
            f"- reason: {decision.reason}",
            f"- finding-set hash: `{decision.finding_hash or '(none)'}`",
            f"- blocking findings: {decision.blocking_count}",
            "",
        ]
        if findings is None:
            lines.append(
                "The review response could not be parsed into findings. This is "
                "recorded as a parse failure, not as a clean review."
            )
        else:
            counts = findings.counts_by_severity()
            lines.append(
                "Counts by severity: "
                + ", ".join(f"{name}={count}" for name, count in counts.items())
            )
            lines.append("")
            lines.append(render_findings_block(findings.findings))

        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write review findings artifact: %s", exc)

    def _run_review_loop(self, state: State, response: str) -> ReviewDecision:
        """Decide whether this story earns another fix round, and record it."""
        findings = self._parse_review_findings(response)
        return self._decide_and_record(state, findings)

    def _decide_and_record(
        self, state: State, findings: FindingSet | None
    ) -> ReviewDecision:
        """Run the bounded review decision over an already-parsed finding set.

        Split from ``_run_review_loop`` so the decision core is independent of
        where the findings came from; every current caller (legacy and
        structured paths alike) parses a synthesis response first. ``None``
        findings mean a parse failure, never a clean review.
        """
        story_id = state.current_story or "unknown"
        self._reset_review_state_for_story(state)

        decision = decide_review_loop(
            findings,
            iteration=state.review_iteration,
            max_iterations=self.config.loop.review_max_iterations,
            previous_hashes=tuple(state.review_finding_hashes),
            review=self.config.review,
            story_id=story_id,
        )

        if decision.finding_hash:
            state.review_finding_hashes.append(decision.finding_hash)

        self._record_findings_artifact(findings, decision, story_id)

        if decision.proceeds:
            # The loop is exiting review: this round's verdict is the one the
            # three-witness "done" gate will read. Record it durably — phase
            # outputs and worker state do not survive a parallel merge.
            self._write_verdict_record(state, decision)

        if decision.blocked:
            if story_id not in state.review_blocked_stories:
                state.review_blocked_stories.append(story_id)
            logger.warning(
                "Review loop stopped for story %s: %s (%s)",
                story_id,
                decision.outcome.value,
                decision.reason,
            )
            write_progress(decision.console_line)
        else:
            logger.info(
                "Review loop for story %s: %s (%s)",
                story_id,
                decision.outcome.value,
                decision.reason,
            )
            write_progress(f"  Review loop: {decision.outcome.value} — {decision.reason}")

        return decision

    def _write_verdict_record(self, state: State, decision: ReviewDecision) -> None:
        """Persist the promoting round's verdict for the three-witness gate.

        Reads the round's shape (full pass or delta, audit lane presence and
        table verdict) from the ``round_meta`` the code_review handler saved
        beside its cached reviews, and the aggregate verdict from the same
        cache. A missing or story-mismatched meta is recorded conservatively
        (``full_pass=False``): the gate then parks the story in ``review``,
        which is recoverable, where a false ``done`` is the incident this
        machinery exists to prevent. Never raises — a story whose record
        cannot be written parks for the same reason.
        """
        story_id = state.current_story
        if not story_id:
            return
        try:
            from bmad_assist_lite.core.verdict import (
                ReviewVerdictRecord,
                write_review_verdict,
            )

            verdict: str | None = None
            score: float | None = None
            meta: dict[str, Any] = {}
            cache_file = (
                self.project_path / ".bmad-assist-lite" / "cache" / "reviews.json"
            )
            if cache_file.exists():
                cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
                evidence = cache_data.get("evidence_score") or {}
                verdict = evidence.get("verdict")
                score = evidence.get("total_score")
                raw_meta = cache_data.get("round_meta") or {}
                if raw_meta.get("story_id") == story_id:
                    meta = raw_meta

            from datetime import UTC, datetime

            record = ReviewVerdictRecord(
                story_id=story_id,
                verdict=verdict,
                outcome=decision.outcome.value,
                review_iteration=state.review_iteration,
                full_pass=bool(meta.get("full_pass", False)),
                audit_required=bool(meta.get("audit_required", False)),
                audit_ran=bool(meta.get("audit_ran", False)),
                audit_passed=meta.get("audit_passed"),
                evidence_score=score,
                timestamp=datetime.now(UTC).replace(tzinfo=None),
            )
            path = write_review_verdict(record, self.project_path)
            logger.info(
                "Recorded review verdict for story %s: %s (full_pass=%s, "
                "audit_passed=%s) at %s",
                story_id,
                verdict,
                record.full_pass,
                record.audit_passed,
                path,
            )
        except Exception as exc:
            logger.warning(
                "Could not write review verdict record for story %s "
                "(the story will park in 'review' rather than promote): %s",
                story_id,
                exc,
            )

    def _structured_synthesis(
        self,
        state: State,
        reviews: list[dict[str, Any]],
        evidence_data: dict[str, Any] | None,
    ) -> PhaseResult | None:
        """Feed the synthesis fixer a pre-merged candidate set + demand terse output.

        Keeps the synthesis as the round-1 fixer per the operator's quality
        decision — it still verifies, applies fixes, updates the story and emits
        the remaining findings (full EXECUTE tools), so the fix touchpoints
        (round-1 synthesis-fix -> fix_review -> round-2 synthesis-fix) are all
        preserved. The speed comes from removing the cross-reviewer re-derivation
        and the verbose report (the reviewers' findings arrive already merged and
        deduped) plus SP-3's lower effort.

        Returns None to fall back to the legacy path — each fallback site emits
        its own accurate operator message — when: any successful lane's findings
        block failed to parse (its findings would be LOST to the merge; the
        legacy path reads the raw prose and so cannot lose them — operator
        quality decision), when no lane succeeded at all, or when the merge
        guard would drop a >= high finding. A parsed-but-empty finding set from
        every lane is a CLEAN review and stays on the fast path.
        """
        story_id = state.current_story or "unknown"
        raw_findings, notes = parse_reviewer_findings(reviews)
        if notes:
            for note in notes:
                logger.warning("structured_review: %s", note)
            write_progress(
                "  structured_review: reviewer lane(s) without a usable findings "
                f"block ({'; '.join(notes)}) — using legacy synthesis so no "
                "finding is lost"
            )
            return None
        if not any(r.get("exit_code") == 0 for r in reviews):
            write_progress(
                "  structured_review: no successful reviewer lane — using legacy synthesis"
            )
            return None

        merged = merge_findings(raw_findings)
        # SP-1 quality guard, code-checkable: the deterministic merge must drop no
        # round-1 finding of severity >= high. True by construction; enforced so a
        # regression falls back to the safe path rather than shipping a silent drop.
        if not high_severity_preserved(raw_findings, merged):
            logger.error(
                "structured merge would drop a >= high finding; using legacy path"
            )
            write_progress(
                "  structured_review: merge guard tripped (would drop a >= high "
                "finding) — using legacy synthesis"
            )
            return None

        candidates, _id_map = render_adjudication_candidates(merged)
        if merged:
            write_progress(
                f"  Structured review: {len(raw_findings)} reviewer finding(s) -> "
                f"{len(merged)} merged candidate(s); synthesis fixes from the merged set"
            )
        else:
            # Every lane parsed clean ([]): keep the fast path — this is where
            # it is cheapest — and tell the synthesis exactly that.
            candidates = (
                "(no candidates — every reviewer reported a clean review; "
                "spot-check the changes and emit an empty findings block unless "
                "you find a defect yourself)"
            )
            write_progress(
                "  Structured review: all reviewer lanes clean — synthesis verifies and closes"
            )

        prompt = self.render_prompt(state)
        evidence_context = self._format_evidence_context(evidence_data)
        full_prompt = (
            f"{prompt}\n\n"
            f"{evidence_context}\n\n"
            "<merged-reviewer-findings>\n"
            "The findings below are ALL reviewers' findings, already de-duplicated "
            "for you — a starting set, so you need not re-derive the cross-reviewer "
            "comparison. But you MUST still assign severity and bucket CENTRALLY "
            "yourself after checking the code (step 9): do NOT just copy a "
            "reviewer's rating. Escalate a genuine spec ambiguity or unmet "
            "acceptance criterion to blocking (bad_spec / intent_gap) even if a "
            "reviewer marked it patch, exactly as a normal synthesis would. Verify "
            "each against the code, apply fixes (steps 4-7), then emit the REMAINING "
            f"findings in the required block.\n{candidates}\n"
            "</merged-reviewer-findings>\n\n"
            "<output-economy>\n"
            "Be terse: no step-by-step exploration narration, no file-by-file "
            "walkthrough, no restating the story. Keep the written synthesis report "
            "to a short summary. The machine BMAD-FINDINGS block is the required "
            "output.\n"
            "</output-economy>"
        )

        # Full tools (EXECUTE): the synthesis stays the fixer. It runs at the master
        # effort (NOT the SP-3 reviewer notch) so its central severity re-judgment
        # keeps W0's escalation rigor — the speed comes from the merged candidates +
        # terse report, not from the synthesis thinking less.
        result = self.invoke_provider(full_prompt)
        if result.exit_code != 0:
            return PhaseResult.fail(
                result.stderr or f"Provider exited with code {result.exit_code}"
            )

        logger.info(
            "code_review_synthesis (structured) output: %d chars (~%d tokens)",
            len(result.stdout),
            len(result.stdout) // 4,
        )

        cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        diff_stat = git_diff(self.project_path, stat=True)
        full_diff = git_diff(self.project_path)
        if diff_stat:
            write_progress(f"  Code changes by synthesis:\n{diff_stat}")
        if full_diff:
            (cache_dir / f"synthesis-diff-review-{story_id}.patch").write_text(
                full_diff, encoding="utf-8"
            )
        (cache_dir / f"synthesis-response-review-{story_id}.md").write_text(
            result.stdout, encoding="utf-8"
        )

        decision = self._run_review_loop(state, result.stdout)
        outputs: dict[str, Any] = {
            "response": result.stdout,
            "model": result.model,
            "duration_ms": result.duration_ms,
            "reviews_synthesized": len(reviews),
            "merged_findings": len(merged),
            "structured_review": True,
            "code_changes": diff_stat or "(none)",
            "review_outcome": decision.outcome.value,
            "review_finding_hash": decision.finding_hash,
            "review_blocking_findings": decision.blocking_count,
        }
        return PhaseResult(
            success=True,
            next_phase=decision.next_phase,
            outputs=outputs,
        )

    def execute(self, state: State) -> PhaseResult:
        """Execute synthesis with cached reviews and Evidence Score context."""
        try:
            cache_file = self.project_path / ".bmad-assist-lite" / "cache" / "reviews.json"
            if not cache_file.exists():
                return PhaseResult.fail("No cached reviews found for synthesis")

            cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
            reviews = cache_data.get("reviews", cache_data)
            evidence_data = cache_data.get("evidence_score")

            # Handle legacy format (list instead of dict)
            if isinstance(reviews, list):
                pass
            elif isinstance(reviews, dict):
                reviews = reviews.get("reviews", [])

            # SP-1: deterministic merge feeding the synthesis fixer a pre-merged
            # candidate set. Falls through to the legacy path on any lane parse
            # failure or guard trip — _structured_synthesis reports the specific
            # reason at each fallback site.
            if self.config.speed.structured_review:
                structured = self._structured_synthesis(state, reviews, evidence_data)
                if structured is not None:
                    return structured
                logger.info("structured_review: using the legacy synthesis path")

            prompt = self.render_prompt(state)

            # Format Evidence Score context for injection
            evidence_context = self._format_evidence_context(evidence_data)

            review_text = "\n\n".join(
                f"=== {r.get('reviewer', 'Unknown')} ===\n"
                f"{_strip_review_narration(r.get('response', r.get('error', 'No output')))}"
                for r in reviews
            )
            full_prompt = (
                f"{prompt}\n\n"
                f"{evidence_context}\n\n"
                f"<code-review-reports>\n{review_text}\n</code-review-reports>"
            )

            # Log prompt composition breakdown
            prompt_tokens = len(full_prompt) // 4
            base_tokens = len(prompt) // 4
            evidence_tokens = len(evidence_context) // 4
            review_tokens = len(review_text) // 4
            logger.info(
                "code_review_synthesis prompt: total=~%d tokens "
                "(base=%d + evidence=%d + reviews=%d)",
                prompt_tokens,
                base_tokens,
                evidence_tokens,
                review_tokens,
            )
            write_progress(
                f"  Prompt breakdown: base=~{base_tokens} + evidence=~{evidence_tokens}"
                f" + reviews=~{review_tokens} = ~{prompt_tokens} tokens"
            )

            # Log per-reviewer response sizes
            for r in reviews:
                rid = r.get("reviewer", "Unknown")
                resp = r.get("response", "")
                logger.info(
                    "  %s response: %d chars (~%d tokens)",
                    rid,
                    len(resp),
                    len(resp) // 4,
                )

            result = self.invoke_provider(full_prompt)

            if result.exit_code != 0:
                return PhaseResult.fail(
                    result.stderr or f"Provider exited with code {result.exit_code}"
                )

            # Log LLM response size
            logger.info(
                "code_review_synthesis LLM output: %d chars (~%d tokens)",
                len(result.stdout),
                len(result.stdout) // 4,
            )

            # Capture git diff after synthesis to show code changes made
            cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            story_id = state.current_story or "unknown"

            diff_stat_after = git_diff(self.project_path, stat=True)
            full_diff = git_diff(self.project_path)

            if diff_stat_after:
                write_progress(f"  Code changes by synthesis:\n{diff_stat_after}")
                logger.info(
                    "code_review_synthesis git diff stat for %s:\n%s",
                    story_id,
                    diff_stat_after,
                )
            else:
                write_progress("  Code changes: NO CODE CHANGES made by synthesis")
                logger.info("code_review_synthesis: no code changes for %s", story_id)

            # Save full diff to cache for review
            if full_diff:
                diff_file = cache_dir / f"synthesis-diff-review-{story_id}.patch"
                diff_file.write_text(full_diff, encoding="utf-8")
                logger.debug("Wrote code review synthesis diff to %s", diff_file)

            # Save LLM response to cache for review
            response_file = cache_dir / f"synthesis-response-review-{story_id}.md"
            response_file.write_text(result.stdout, encoding="utf-8")

            outputs: dict[str, Any] = {
                "response": result.stdout,
                "model": result.model,
                "duration_ms": result.duration_ms,
                "reviews_synthesized": len(reviews),
                "prompt_tokens_estimate": prompt_tokens,
                "code_changes": diff_stat_after or "(none)",
            }
            if evidence_data:
                outputs["evidence_score"] = evidence_data.get("total_score")
                outputs["evidence_verdict"] = evidence_data.get("verdict")

            decision = self._run_review_loop(state, result.stdout)
            outputs["review_outcome"] = decision.outcome.value
            outputs["review_finding_hash"] = decision.finding_hash
            outputs["review_blocking_findings"] = decision.blocking_count

            return PhaseResult(
                success=True,
                next_phase=decision.next_phase,
                outputs=outputs,
            )

        except Exception as e:
            logger.error("Code review synthesis failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Code review synthesis failed: {e}")
