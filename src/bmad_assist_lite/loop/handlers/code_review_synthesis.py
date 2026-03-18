"""CODE_REVIEW_SYNTHESIS phase handler.

Master LLM synthesizes Multi-LLM code review reports with pre-calculated
Evidence Score context injected into the prompt.
"""

import json
import logging
import re
import subprocess
from typing import Any

from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers.base import write_progress

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

    def _capture_git_diff_stat(self) -> str | None:
        """Capture git diff --stat to show what files changed."""
        try:
            result = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    def _capture_git_diff(self) -> str | None:
        """Capture full git diff for saving to cache."""
        try:
            result = subprocess.run(
                ["git", "diff"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.stdout if result.returncode == 0 else None
        except Exception:
            return None

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

            diff_stat_after = self._capture_git_diff_stat()
            full_diff = self._capture_git_diff()

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

            return PhaseResult.ok(outputs)

        except Exception as e:
            logger.error("Code review synthesis failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Code review synthesis failed: {e}")
