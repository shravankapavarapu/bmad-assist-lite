"""VALIDATE_STORY_SYNTHESIS phase handler.

Master LLM synthesizes Multi-LLM validation reports with pre-calculated
Evidence Score context injected into the prompt.
"""

import difflib
import glob as glob_mod
import json
import logging
from pathlib import Path
from typing import Any

from bmad_assist_lite.core.paths import get_paths
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.autonomy import AutonomyLevel
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.review_merge import (
    high_severity_preserved,
    merge_findings,
    parse_reviewer_findings,
    render_adjudication_candidates,
)
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers.base import write_progress

logger = logging.getLogger(__name__)


class ValidateStorySynthesisHandler(BaseHandler):
    """Master LLM synthesizes multi-LLM validation reports."""

    autonomy = AutonomyLevel.EXECUTE
    """Single master; unrestricted today."""

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "validate_story_synthesis"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context for this phase."""
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

    def _structured_reports_block(
        self, validations: list[dict[str, Any]]
    ) -> tuple[str, int] | None:
        """SP-1: merged candidate block replacing the inlined validator prose.

        The synthesis keeps its full legacy flow (evidence context, story diff
        tracking, outputs); only the ``<validation-reports>`` prose — the bulk of
        the prompt and the driver of the re-derivation — is replaced by the
        deterministically merged, de-duplicated candidate set plus a terse-output
        directive.

        Returns ``(block_text, merged_count)``, or None to fall back to the full
        prose reports — when any successful lane lacks a parseable findings
        block (its findings would be lost to the merge), when no lane succeeded,
        or when the merge guard would drop a >= high finding.
        """
        raw_findings, notes = parse_reviewer_findings(validations)
        if notes:
            for note in notes:
                logger.warning("structured_review: %s", note)
            write_progress(
                "  structured_review: validator lane(s) without a usable findings "
                f"block ({'; '.join(notes)}) — using full validation reports so "
                "no finding is lost"
            )
            return None
        if not any(v.get("exit_code") == 0 for v in validations):
            return None

        merged = merge_findings(raw_findings)
        if not high_severity_preserved(raw_findings, merged):
            logger.error(
                "structured merge would drop a >= high validator finding; "
                "using full validation reports"
            )
            write_progress(
                "  structured_review: merge guard tripped — using full validation reports"
            )
            return None

        candidates, _id_map = render_adjudication_candidates(merged)
        if merged:
            write_progress(
                f"  Structured validation: {len(raw_findings)} validator finding(s) -> "
                f"{len(merged)} merged candidate(s)"
            )
        else:
            candidates = (
                "(no candidates — every validator reported a clean validation)"
            )
            write_progress(
                "  Structured validation: all validator lanes clean"
            )
        block = (
            "<merged-validator-findings>\n"
            "The findings below are ALL validators' findings, already "
            "de-duplicated for you — you need not re-derive the cross-validator "
            "comparison, but you MUST still judge each against the story and "
            "assign the final disposition centrally yourself.\n"
            f"{candidates}\n"
            "</merged-validator-findings>\n\n"
            "<output-economy>\n"
            "Be terse: no per-validator recap, no restating the story. Keep the "
            "written synthesis to a short summary plus the required story "
            "updates.\n"
            "</output-economy>"
        )
        return block, len(merged)

    def _find_story_file(self, state: State) -> Path | None:
        """Find story file for before/after diff."""
        paths = get_paths()
        epic_num = state.current_epic
        story_num = self._extract_story_num(state.current_story)
        if not epic_num or not story_num:
            return None
        pattern = str(paths.implementation_artifacts / f"*{epic_num}*{story_num}*.md")
        matches = glob_mod.glob(pattern)
        return Path(matches[0]) if matches else None

    def _log_story_diff(
        self, before: str, after: str, story_id: str, cache_dir: Path
    ) -> dict[str, Any]:
        """Compute and log diff between story file before/after synthesis.

        Returns diff stats dict for inclusion in phase outputs.
        """
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)

        diff = list(
            difflib.unified_diff(before_lines, after_lines, fromfile="before", tofile="after", n=3)
        )

        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        size_before = len(before)
        size_after = len(after)

        stats: dict[str, Any] = {
            "lines_added": added,
            "lines_removed": removed,
            "size_before": size_before,
            "size_after": size_after,
            "size_delta": size_after - size_before,
            "changed": added > 0 or removed > 0,
        }

        if added == 0 and removed == 0:
            write_progress("  Story diff: NO CHANGES made by synthesis")
            logger.info("validate_story_synthesis: story file unchanged for %s", story_id)
        else:
            write_progress(
                f"  Story diff: +{added} -{removed} lines"
                f" ({size_before} -> {size_after} bytes, delta={size_after - size_before:+d})"
            )
            logger.info(
                "validate_story_synthesis diff for %s: +%d -%d lines, %d -> %d bytes",
                story_id,
                added,
                removed,
                size_before,
                size_after,
            )

        # Save full diff to cache for review
        diff_file = cache_dir / f"synthesis-diff-validate-{story_id}.patch"
        diff_content = "".join(diff) if diff else "(no changes)\n"
        diff_file.write_text(diff_content, encoding="utf-8")
        logger.debug("Wrote synthesis diff to %s", diff_file)

        return stats

    def execute(self, state: State) -> PhaseResult:
        """Execute synthesis with cached validations and Evidence Score context."""
        try:
            # Load cached validations
            cache_file = self.project_path / ".bmad-assist-lite" / "cache" / "validations.json"
            if not cache_file.exists():
                return PhaseResult.fail("No cached validations found for synthesis")

            cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
            validations = cache_data.get("validations", cache_data)
            evidence_data = cache_data.get("evidence_score")

            # Handle legacy format (list instead of dict)
            if isinstance(validations, list):
                pass  # Already a list
            elif isinstance(validations, dict):
                validations = validations.get("validations", [])

            # Capture story file before synthesis for diff
            story_file = self._find_story_file(state)
            story_before = ""
            if story_file and story_file.exists():
                story_before = story_file.read_text(encoding="utf-8")

            # Build synthesis prompt with validations embedded
            prompt = self.render_prompt(state)

            # Format Evidence Score context for injection
            evidence_context = self._format_evidence_context(evidence_data)

            # Build the reports section: SP-1 swaps the inlined validator prose
            # for the merged candidate set (falls back to prose on any lane
            # parse failure so no finding can be lost).
            structured: tuple[str, int] | None = None
            if self.config.speed.structured_review:
                structured = self._structured_reports_block(validations)
            if structured is not None:
                reports_block, merged_count = structured
            else:
                merged_count = -1
                validation_text = "\n\n".join(
                    f"=== {v.get('validator', 'Unknown')} ===\n"
                    f"{v.get('response', v.get('error', 'No output'))}"
                    for v in validations
                )
                reports_block = (
                    f"<validation-reports>\n{validation_text}\n</validation-reports>"
                )

            # Compose full prompt with evidence context + validation reports
            full_prompt = (
                f"{prompt}\n\n"
                f"{evidence_context}\n\n"
                f"{reports_block}"
            )

            # Log prompt composition breakdown
            prompt_tokens = len(full_prompt) // 4
            base_tokens = len(prompt) // 4
            evidence_tokens = len(evidence_context) // 4
            validation_tokens = len(reports_block) // 4
            logger.info(
                "validate_story_synthesis prompt: total=~%d tokens "
                "(base=%d + evidence=%d + validations=%d)",
                prompt_tokens,
                base_tokens,
                evidence_tokens,
                validation_tokens,
            )
            write_progress(
                f"  Prompt breakdown: base=~{base_tokens} + evidence=~{evidence_tokens}"
                f" + validations=~{validation_tokens} = ~{prompt_tokens} tokens"
            )

            # Log per-validator response sizes
            for v in validations:
                vid = v.get("validator", "Unknown")
                resp = v.get("response", "")
                logger.info(
                    "  %s response: %d chars (~%d tokens)",
                    vid,
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
                "validate_story_synthesis LLM output: %d chars (~%d tokens)",
                len(result.stdout),
                len(result.stdout) // 4,
            )

            # Compute and log story file diff
            diff_stats: dict[str, Any] = {}
            cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            story_id = state.current_story or "unknown"

            # Save LLM response to cache for review
            response_file = cache_dir / f"synthesis-response-validate-{story_id}.md"
            response_file.write_text(result.stdout, encoding="utf-8")

            if story_file and story_file.exists():
                story_after = story_file.read_text(encoding="utf-8")
                diff_stats = self._log_story_diff(
                    story_before, story_after, story_id, cache_dir
                )

            outputs: dict[str, Any] = {
                "response": result.stdout,
                "model": result.model,
                "duration_ms": result.duration_ms,
                "validations_synthesized": len(validations),
                "prompt_tokens_estimate": prompt_tokens,
                "story_diff": diff_stats,
            }
            if structured is not None:
                outputs["structured_review"] = True
                outputs["merged_findings"] = merged_count
            if evidence_data:
                outputs["evidence_score"] = evidence_data.get("total_score")
                outputs["evidence_verdict"] = evidence_data.get("verdict")

            return PhaseResult.ok(outputs)

        except Exception as e:
            logger.error("Synthesis handler failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Synthesis failed: {e}")
