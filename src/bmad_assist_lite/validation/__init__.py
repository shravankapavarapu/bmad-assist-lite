"""Validation module for Evidence Score system.

Provides deterministic Evidence Score calculation, parsing from LLM output,
aggregation across multiple validators, and formatting for synthesis prompt injection.
"""

from bmad_assist_lite.validation.evidence_score import (
    AllValidatorsFailedError,
    EvidenceFinding,
    EvidenceScoreAggregate,
    EvidenceScoreError,
    EvidenceScoreReport,
    Severity,
    Verdict,
    aggregate_evidence_scores,
    calculate_evidence_score,
    determine_verdict,
    format_evidence_score_context,
    parse_evidence_findings,
)

__all__ = [
    "Severity",
    "Verdict",
    "EvidenceFinding",
    "EvidenceScoreReport",
    "EvidenceScoreAggregate",
    "calculate_evidence_score",
    "determine_verdict",
    "parse_evidence_findings",
    "aggregate_evidence_scores",
    "format_evidence_score_context",
    "EvidenceScoreError",
    "AllValidatorsFailedError",
]
