"""Evidence Score system for Multi-LLM validation and code review.

Implements the deterministic Evidence Score calculation system:
- Mathematical scoring: CRITICAL (+3), IMPORTANT (+1), MINOR (+0.3), CLEAN PASS (-0.5)
- Deterministic verdict thresholds (>=6 REJECT, 4-6 REWORK, <=3 PASS, <=-3 EXCELLENT)
- Finding deduplication using difflib.SequenceMatcher (ratio >= 0.85)
- Aggregate calculation across multiple validators with consensus tracking

Public API:
    Severity: Enum for finding severity levels
    Verdict: Enum for deterministic verdicts with context-aware display
    EvidenceFinding: Dataclass for individual findings
    EvidenceScoreReport: Per-validator Evidence Score report
    EvidenceScoreAggregate: Aggregate across multiple validators
    calculate_evidence_score: Calculate score from findings
    determine_verdict: Map score to verdict
    parse_evidence_findings: Parse Evidence Score from report content
    aggregate_evidence_scores: Aggregate multiple validator reports
    format_evidence_score_context: Format for synthesis prompt injection
"""

from __future__ import annotations

import difflib
import logging
import re
import string
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from bmad_assist_lite.core.exceptions import BmadAssistError

logger = logging.getLogger(__name__)

__all__ = [
    "Severity",
    "Verdict",
    "EvidenceFinding",
    "EvidenceScoreReport",
    "EvidenceScoreAggregate",
    "SEVERITY_SCORES",
    "calculate_evidence_score",
    "determine_verdict",
    "parse_evidence_findings",
    "aggregate_evidence_scores",
    "format_evidence_score_context",
    "EvidenceScoreError",
    "AllValidatorsFailedError",
]


# =============================================================================
# Exceptions
# =============================================================================


class EvidenceScoreError(BmadAssistError):
    """Base exception for Evidence Score module."""

    pass


class AllValidatorsFailedError(EvidenceScoreError):
    """Raised when all validators fail to produce parseable Evidence Score reports."""

    pass


# =============================================================================
# Enums
# =============================================================================


class Severity(StrEnum):
    """Finding severity levels for Evidence Score system."""

    CRITICAL = "CRITICAL"
    IMPORTANT = "IMPORTANT"
    MINOR = "MINOR"


class Verdict(StrEnum):
    """Deterministic verdict based on Evidence Score thresholds.

    Canonical values used for serialization.
    Use display_name() for context-aware display.
    """

    REJECT = "REJECT"
    MAJOR_REWORK = "MAJOR_REWORK"
    PASS = "PASS"  # Display: READY (validation) / APPROVE (code_review)
    EXCELLENT = "EXCELLENT"  # Display: EXCELLENT (validation) / EXEMPLARY (code_review)

    def display_name(self, context: Literal["validation", "code_review"]) -> str:
        """Get context-aware display name for verdict."""
        if context == "code_review":
            return {"PASS": "APPROVE", "EXCELLENT": "EXEMPLARY"}.get(
                self.value, self.value.replace("_", " ")
            )
        # validation context
        return {"PASS": "READY", "MAJOR_REWORK": "MAJOR REWORK"}.get(self.value, self.value)


# =============================================================================
# Constants
# =============================================================================

SEVERITY_SCORES: dict[str, float] = {
    "CRITICAL": 3.0,
    "IMPORTANT": 1.0,
    "MINOR": 0.3,
    "CLEAN_PASS": -0.5,
}

# Similarity threshold for finding deduplication
_DEDUP_SIMILARITY_THRESHOLD = 0.85

# Safety limits for parsing LLM output
_MAX_CONTENT_LENGTH = 500_000  # 500KB max LLM output to parse
_MAX_DESCRIPTION_LENGTH = 1_000  # Truncate finding descriptions for dedup matching
_MAX_FINDINGS_PER_REPORT = 100  # Cap findings per validator to prevent abuse


# =============================================================================
# Data Classes
# =============================================================================


@dataclass(frozen=True)
class EvidenceFinding:
    """Individual finding with Evidence Score severity."""

    severity: Severity
    score: float
    description: str
    source: str
    validator_id: str
    normalized_description: str = ""

    def __post_init__(self) -> None:
        """Compute normalized_description if not provided."""
        if not self.normalized_description:
            object.__setattr__(
                self, "normalized_description", _normalize_description(self.description)
            )


@dataclass(frozen=True)
class EvidenceScoreReport:
    """Per-validator Evidence Score report."""

    validator_id: str
    findings: tuple[EvidenceFinding, ...]
    clean_passes: int
    total_score: float
    verdict: Verdict
    parse_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceScoreAggregate:
    """Aggregate Evidence Score across multiple validators."""

    total_score: float
    verdict: Verdict
    per_validator_scores: dict[str, float]
    per_validator_verdicts: dict[str, Verdict]
    findings_by_severity: dict[Severity, int]
    total_findings: int
    total_clean_passes: int
    consensus_findings: tuple[EvidenceFinding, ...]
    unique_findings: tuple[EvidenceFinding, ...]
    consensus_ratio: float


# =============================================================================
# Calculation Functions
# =============================================================================


def calculate_evidence_score(
    findings: list[EvidenceFinding] | tuple[EvidenceFinding, ...],
    clean_passes: int,
) -> float:
    """Calculate Evidence Score from findings and clean passes.

    Formula: sum(finding.score for each finding) + (clean_passes * -0.5)
    """
    findings_score = sum(f.score for f in findings)
    clean_pass_score = clean_passes * SEVERITY_SCORES["CLEAN_PASS"]
    total = findings_score + clean_pass_score
    return round(total, 1)


def determine_verdict(score: float) -> Verdict:
    """Determine verdict from Evidence Score.

    Thresholds:
    - >= 6.0: REJECT
    - 4.0 - 5.9: MAJOR_REWORK
    - -2.9 - 3.9: PASS (READY/APPROVE)
    - <= -3.0: EXCELLENT (EXCELLENT/EXEMPLARY)
    """
    if score >= 6.0:
        return Verdict.REJECT
    elif score >= 4.0:
        return Verdict.MAJOR_REWORK
    elif score <= -3.0:
        return Verdict.EXCELLENT
    else:
        return Verdict.PASS


# =============================================================================
# Parsing Functions
# =============================================================================

# Regex patterns for Evidence Score parsing
# Pattern for Evidence Score table format:
# | 🔴 CRITICAL | description | source | +3 |
_FINDING_TABLE_PATTERN = re.compile(
    r"\|\s*(?:🔴|🟠|🟡)\s*(CRITICAL|IMPORTANT|MINOR)\s*\|"
    r"\s*([^|]+)\s*\|"  # description
    r"\s*([^|]*)\s*\|"  # source (optional)
    r"\s*\+?(\d+(?:\.\d+)?)\s*\|",  # score
    re.IGNORECASE | re.MULTILINE,
)

# Alternative pattern: bullet point format
# - 🔴 **CRITICAL** (+3): Description [source]
_FINDING_BULLET_PATTERN = re.compile(
    r"[-*]\s*(?:🔴|🟠|🟡)?\s*\*?\*?(CRITICAL|IMPORTANT|MINOR)\*?\*?\s*"
    r"\(\+?(\d+(?:\.\d+)?)\):\s*"
    r"([^\[]+)"  # description
    r"(?:\[([^\]]+)\])?",  # source (optional)
    re.IGNORECASE | re.MULTILINE,
)

# Pattern for CLEAN PASS count
# | 🟢 CLEAN PASS | 5 |
_CLEAN_PASS_TABLE_PATTERN = re.compile(r"\|\s*🟢\s*CLEAN PASS\s*\|\s*(\d+)\s*\|", re.IGNORECASE)

# Alternative: "CLEAN PASS: 5" or "5 CLEAN PASS"
_CLEAN_PASS_TEXT_PATTERN = re.compile(
    r"(?:CLEAN PASS(?:ES)?:?\s*(\d+)|(\d+)\s*CLEAN PASS(?:ES)?)", re.IGNORECASE
)

# Pattern for total Evidence Score
# | **Evidence Score** | **3.5** | or Evidence Score: 3.5
_EVIDENCE_SCORE_PATTERN = re.compile(
    r"(?:Evidence Score:?\s*\*?\*?(-?\d+(?:\.\d+)?)\*?\*?"
    r"|\|\s*\*?\*?Evidence Score\*?\*?\s*\|\s*\*?\*?(-?\d+(?:\.\d+)?)\*?\*?\s*\|)",
    re.IGNORECASE,
)


def _normalize_description(description: str) -> str:
    """Normalize description for deduplication matching."""
    text = description.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = " ".join(text.split())
    return text


def parse_evidence_findings(
    content: str,
    validator_id: str,
) -> EvidenceScoreReport | None:
    """Parse Evidence Score from validator report.

    Fallback strategy:
    1. Primary: Parse structured Evidence Score table with severity columns
    2. Fallback 1: Parse individual CRITICAL/IMPORTANT/MINOR bullet points
    3. Fallback 2: Parse Evidence Score total from heading/footer
    4. If none work: return None (validator excluded from aggregate)
    """
    # Truncate excessively large content to prevent DoS
    if len(content) > _MAX_CONTENT_LENGTH:
        logger.warning(
            "Validator %s output exceeds %d chars, truncating for parsing",
            validator_id,
            _MAX_CONTENT_LENGTH,
        )
        content = content[:_MAX_CONTENT_LENGTH]

    findings: list[EvidenceFinding] = []
    clean_passes = 0
    parse_warnings: list[str] = []
    parsed_score: float | None = None

    # Try table format first
    table_matches = _FINDING_TABLE_PATTERN.findall(content)
    for severity_str, description, source, score_str in table_matches:
        if len(findings) >= _MAX_FINDINGS_PER_REPORT:
            parse_warnings.append(
                f"Findings capped at {_MAX_FINDINGS_PER_REPORT}, remaining ignored"
            )
            break
        try:
            severity = Severity(severity_str.upper())
            score = float(score_str)
            findings.append(
                EvidenceFinding(
                    severity=severity,
                    score=score,
                    description=description.strip()[:_MAX_DESCRIPTION_LENGTH],
                    source=source.strip() if source else "",
                    validator_id=validator_id,
                )
            )
        except (ValueError, KeyError) as e:
            parse_warnings.append(f"Failed to parse table finding: {e}")

    # Try bullet format if no table findings
    if not findings:
        bullet_matches = _FINDING_BULLET_PATTERN.findall(content)
        for severity_str, score_str, description, source in bullet_matches:
            if len(findings) >= _MAX_FINDINGS_PER_REPORT:
                parse_warnings.append(
                    f"Findings capped at {_MAX_FINDINGS_PER_REPORT}, remaining ignored"
                )
                break
            try:
                severity = Severity(severity_str.upper())
                score = float(score_str)
                findings.append(
                    EvidenceFinding(
                        severity=severity,
                        score=score,
                        description=description.strip()[:_MAX_DESCRIPTION_LENGTH],
                        source=source.strip() if source else "",
                        validator_id=validator_id,
                    )
                )
            except (ValueError, KeyError) as e:
                parse_warnings.append(f"Failed to parse bullet finding: {e}")

    # Parse CLEAN PASS count
    clean_pass_match = _CLEAN_PASS_TABLE_PATTERN.search(content)
    if clean_pass_match:
        clean_passes = int(clean_pass_match.group(1))
    else:
        clean_pass_text = _CLEAN_PASS_TEXT_PATTERN.search(content)
        if clean_pass_text:
            clean_passes = int(clean_pass_text.group(1) or clean_pass_text.group(2))

    # Try to extract reported score for validation
    score_match = _EVIDENCE_SCORE_PATTERN.search(content)
    if score_match:
        parsed_score = float(score_match.group(1) or score_match.group(2))

    # If no findings and no clean passes and no score, return None
    if not findings and clean_passes == 0 and parsed_score is None:
        logger.warning(
            "Failed to parse Evidence Score from %s: no findings, clean passes, or score found",
            validator_id,
        )
        return None

    # Calculate score
    calculated_score = calculate_evidence_score(findings, clean_passes)

    # If we parsed a score and it differs significantly from calculated, log warning
    if parsed_score is not None and abs(parsed_score - calculated_score) > 0.2:
        parse_warnings.append(
            f"Parsed score ({parsed_score}) differs from calculated ({calculated_score})"
        )
        # Use parsed score if we couldn't extract findings but have a score
        if not findings and clean_passes == 0:
            calculated_score = parsed_score

    # Determine verdict
    verdict = determine_verdict(calculated_score)

    return EvidenceScoreReport(
        validator_id=validator_id,
        findings=tuple(findings),
        clean_passes=clean_passes,
        total_score=calculated_score,
        verdict=verdict,
        parse_warnings=tuple(parse_warnings),
    )


# =============================================================================
# Aggregation Functions
# =============================================================================


def _are_findings_similar(f1: EvidenceFinding, f2: EvidenceFinding) -> bool:
    """Check if two findings are similar enough to be deduplicated."""
    desc1 = f1.normalized_description[:_MAX_DESCRIPTION_LENGTH]
    desc2 = f2.normalized_description[:_MAX_DESCRIPTION_LENGTH]
    if desc1 == desc2:
        return True
    ratio = difflib.SequenceMatcher(None, desc1, desc2).ratio()
    return ratio >= _DEDUP_SIMILARITY_THRESHOLD


def _deduplicate_findings(
    all_findings: list[EvidenceFinding],
) -> tuple[list[EvidenceFinding], dict[str, int]]:
    """Deduplicate findings across validators.

    When deduplicating: keep highest severity (CRITICAL > IMPORTANT > MINOR).
    Track consensus count for each deduplicated finding.
    """
    if not all_findings:
        return [], {}

    severity_priority = {Severity.CRITICAL: 3, Severity.IMPORTANT: 2, Severity.MINOR: 1}
    deduped: list[EvidenceFinding] = []
    consensus_counts: dict[str, int] = {}
    seen_validators: dict[str, set[str]] = {}

    for finding in all_findings:
        norm_desc = finding.normalized_description

        matched = False
        for existing in deduped:
            if _are_findings_similar(finding, existing):
                existing_norm = existing.normalized_description
                if finding.validator_id not in seen_validators.get(existing_norm, set()):
                    seen_validators.setdefault(existing_norm, set()).add(finding.validator_id)
                    consensus_counts[existing_norm] = len(seen_validators[existing_norm])

                if severity_priority[finding.severity] > severity_priority[existing.severity]:
                    idx = deduped.index(existing)
                    deduped[idx] = finding
                    new_norm = finding.normalized_description
                    if new_norm != existing_norm:
                        seen_validators[new_norm] = seen_validators.pop(existing_norm)
                        consensus_counts[new_norm] = consensus_counts.pop(existing_norm)
                matched = True
                break

        if not matched:
            deduped.append(finding)
            seen_validators[norm_desc] = {finding.validator_id}
            consensus_counts[norm_desc] = 1

    return deduped, consensus_counts


def aggregate_evidence_scores(
    reports: list[EvidenceScoreReport],
) -> EvidenceScoreAggregate:
    """Aggregate Evidence Score across multiple validators."""
    if not reports:
        raise AllValidatorsFailedError(
            "No valid Evidence Score reports to aggregate. "
            "All validators failed to produce parseable reports."
        )

    per_validator_scores: dict[str, float] = {}
    per_validator_verdicts: dict[str, Verdict] = {}
    all_findings: list[EvidenceFinding] = []
    total_clean_passes = 0

    for report in reports:
        per_validator_scores[report.validator_id] = report.total_score
        per_validator_verdicts[report.validator_id] = report.verdict
        all_findings.extend(report.findings)
        total_clean_passes += report.clean_passes

    deduped_findings, consensus_counts = _deduplicate_findings(all_findings)

    consensus_findings = [
        f for f in deduped_findings if consensus_counts.get(f.normalized_description, 1) >= 2
    ]
    unique_findings = [
        f for f in deduped_findings if consensus_counts.get(f.normalized_description, 1) < 2
    ]

    findings_by_severity: dict[Severity, int] = {
        Severity.CRITICAL: 0,
        Severity.IMPORTANT: 0,
        Severity.MINOR: 0,
    }
    for finding in deduped_findings:
        findings_by_severity[finding.severity] += 1

    avg_score = round(sum(per_validator_scores.values()) / len(per_validator_scores), 1)
    aggregate_verdict = determine_verdict(avg_score)

    total_findings = len(deduped_findings)
    consensus_ratio = len(consensus_findings) / total_findings if total_findings > 0 else 0.0

    return EvidenceScoreAggregate(
        total_score=avg_score,
        verdict=aggregate_verdict,
        per_validator_scores=per_validator_scores,
        per_validator_verdicts=per_validator_verdicts,
        findings_by_severity=findings_by_severity,
        total_findings=total_findings,
        total_clean_passes=total_clean_passes,
        consensus_findings=tuple(consensus_findings),
        unique_findings=tuple(unique_findings),
        consensus_ratio=round(consensus_ratio, 2),
    )


# =============================================================================
# Formatting Functions
# =============================================================================


def format_evidence_score_context(
    aggregate: EvidenceScoreAggregate,
    context: Literal["validation", "code_review"] = "validation",
) -> str:
    """Format Evidence Score aggregate for synthesis prompt injection."""
    verdict_display = aggregate.verdict.display_name(context)
    consensus_pct = int(aggregate.consensus_ratio * 100)

    lines = [
        "<!-- PRE-CALCULATED EVIDENCE SCORE (DETERMINISTIC - DO NOT RECALCULATE) -->",
        "## Aggregate Evidence Score",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| **Total Score** | {aggregate.total_score} |",
        f"| **Verdict** | {verdict_display} |",
        f"| CRITICAL findings | {aggregate.findings_by_severity.get(Severity.CRITICAL, 0)} |",
        f"| IMPORTANT findings | {aggregate.findings_by_severity.get(Severity.IMPORTANT, 0)} |",
        f"| MINOR findings | {aggregate.findings_by_severity.get(Severity.MINOR, 0)} |",
        f"| CLEAN PASS categories | {aggregate.total_clean_passes} |",
        f"| Consensus ratio | {consensus_pct}% |",
        "",
        "### Per-Validator Scores",
        "| Validator | Score | Verdict |",
        "|-----------|-------|---------|",
    ]

    for validator_id, score in aggregate.per_validator_scores.items():
        v_verdict = aggregate.per_validator_verdicts[validator_id]
        v_display = v_verdict.display_name(context)
        lines.append(f"| {validator_id} | {score} | {v_display} |")

    lines.extend(
        [
            "",
            "**IMPORTANT:** Use the above pre-calculated Evidence Score and Verdict in your synthesis.",
            "These values are deterministically computed - DO NOT recalculate.",
            "<!-- END PRE-CALCULATED EVIDENCE SCORE -->",
        ]
    )

    return "\n".join(lines)
