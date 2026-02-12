"""Tests for the Evidence Score system."""

import pytest

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


# =============================================================================
# Severity and Verdict Enum Tests
# =============================================================================


class TestSeverity:
    def test_values(self):
        assert Severity.CRITICAL == "CRITICAL"
        assert Severity.IMPORTANT == "IMPORTANT"
        assert Severity.MINOR == "MINOR"

    def test_from_string(self):
        assert Severity("CRITICAL") is Severity.CRITICAL
        assert Severity("IMPORTANT") is Severity.IMPORTANT
        assert Severity("MINOR") is Severity.MINOR


class TestVerdict:
    def test_values(self):
        assert Verdict.REJECT == "REJECT"
        assert Verdict.MAJOR_REWORK == "MAJOR_REWORK"
        assert Verdict.PASS == "PASS"
        assert Verdict.EXCELLENT == "EXCELLENT"

    def test_display_name_validation(self):
        assert Verdict.PASS.display_name("validation") == "READY"
        assert Verdict.EXCELLENT.display_name("validation") == "EXCELLENT"
        assert Verdict.MAJOR_REWORK.display_name("validation") == "MAJOR REWORK"
        assert Verdict.REJECT.display_name("validation") == "REJECT"

    def test_display_name_code_review(self):
        assert Verdict.PASS.display_name("code_review") == "APPROVE"
        assert Verdict.EXCELLENT.display_name("code_review") == "EXEMPLARY"
        assert Verdict.MAJOR_REWORK.display_name("code_review") == "MAJOR REWORK"
        assert Verdict.REJECT.display_name("code_review") == "REJECT"


# =============================================================================
# EvidenceFinding Tests
# =============================================================================


class TestEvidenceFinding:
    def test_normalized_description_auto_computed(self):
        finding = EvidenceFinding(
            severity=Severity.CRITICAL,
            score=3.0,
            description="Missing input VALIDATION!",
            source="api.py:42",
            validator_id="Validator A",
        )
        assert finding.normalized_description == "missing input validation"

    def test_frozen(self):
        finding = EvidenceFinding(
            severity=Severity.MINOR,
            score=0.3,
            description="Test",
            source="",
            validator_id="V1",
        )
        with pytest.raises(AttributeError):
            finding.severity = Severity.CRITICAL  # type: ignore[misc]


# =============================================================================
# Calculate Evidence Score Tests
# =============================================================================


class TestCalculateEvidenceScore:
    def test_basic_calculation(self):
        findings = [
            EvidenceFinding(Severity.CRITICAL, 3.0, "bug", "", "V1"),
            EvidenceFinding(Severity.IMPORTANT, 1.0, "issue", "", "V1"),
        ]
        # 3.0 + 1.0 + (2 * -0.5) = 3.0
        assert calculate_evidence_score(findings, clean_passes=2) == 3.0

    def test_clean_passes_only(self):
        assert calculate_evidence_score([], clean_passes=6) == -3.0

    def test_no_findings_no_passes(self):
        assert calculate_evidence_score([], clean_passes=0) == 0.0

    def test_mixed_severities(self):
        findings = [
            EvidenceFinding(Severity.CRITICAL, 3.0, "a", "", "V1"),
            EvidenceFinding(Severity.CRITICAL, 3.0, "b", "", "V1"),
            EvidenceFinding(Severity.IMPORTANT, 1.0, "c", "", "V1"),
            EvidenceFinding(Severity.MINOR, 0.3, "d", "", "V1"),
        ]
        # 3 + 3 + 1 + 0.3 + (4 * -0.5) = 7.3 - 2 = 5.3
        assert calculate_evidence_score(findings, clean_passes=4) == 5.3


# =============================================================================
# Determine Verdict Tests
# =============================================================================


class TestDetermineVerdict:
    def test_reject(self):
        assert determine_verdict(6.0) is Verdict.REJECT
        assert determine_verdict(10.0) is Verdict.REJECT

    def test_major_rework(self):
        assert determine_verdict(4.0) is Verdict.MAJOR_REWORK
        assert determine_verdict(5.9) is Verdict.MAJOR_REWORK

    def test_pass(self):
        assert determine_verdict(0.0) is Verdict.PASS
        assert determine_verdict(3.9) is Verdict.PASS
        assert determine_verdict(-2.9) is Verdict.PASS

    def test_excellent(self):
        assert determine_verdict(-3.0) is Verdict.EXCELLENT
        assert determine_verdict(-5.0) is Verdict.EXCELLENT


# =============================================================================
# Parse Evidence Findings Tests
# =============================================================================


class TestParseEvidenceFindings:
    def test_parse_table_format(self):
        content = """
## Evidence Score Summary

| Severity | Description | Source | Score |
|----------|-------------|--------|-------|
| 🔴 CRITICAL | SQL injection in login | auth.py:42 | +3 |
| 🟠 IMPORTANT | Missing error handling | api.py:10 | +1 |
| 🟡 MINOR | Inconsistent naming | utils.py:5 | +0.3 |
| 🟢 CLEAN PASS | 4 |

### Evidence Score: 1.8
"""
        report = parse_evidence_findings(content, "Validator A")
        assert report is not None
        assert len(report.findings) == 3
        assert report.findings[0].severity == Severity.CRITICAL
        assert report.findings[0].score == 3.0
        assert report.findings[0].description == "SQL injection in login"
        assert report.findings[1].severity == Severity.IMPORTANT
        assert report.findings[2].severity == Severity.MINOR
        assert report.clean_passes == 4
        assert report.validator_id == "Validator A"

    def test_parse_bullet_format(self):
        content = """
- **CRITICAL** (+3): Missing authentication check [auth.py:10]
- **IMPORTANT** (+1): N+1 query pattern [db.py:20]
- **MINOR** (+0.3): Missing docstring

CLEAN PASS: 3
"""
        report = parse_evidence_findings(content, "Validator B")
        assert report is not None
        assert len(report.findings) == 3
        assert report.findings[0].severity == Severity.CRITICAL
        assert report.clean_passes == 3

    def test_parse_no_findings_returns_none(self):
        content = "This is a generic review with no structured evidence score."
        report = parse_evidence_findings(content, "Validator C")
        assert report is None

    def test_parse_score_only_fallback(self):
        content = """
No structured findings but:
Evidence Score: 2.5
"""
        report = parse_evidence_findings(content, "Validator D")
        assert report is not None
        assert report.total_score == 2.5

    def test_verdict_determined_from_score(self):
        content = """
| 🔴 CRITICAL | Big problem | file.py | +3 |
| 🔴 CRITICAL | Another big problem | file.py | +3 |
"""
        report = parse_evidence_findings(content, "V1")
        assert report is not None
        assert report.total_score == 6.0
        assert report.verdict == Verdict.REJECT


# =============================================================================
# Aggregate Evidence Scores Tests
# =============================================================================


class TestAggregateEvidenceScores:
    def _make_report(self, validator_id, findings_data, clean_passes=0):
        findings = []
        for sev, score, desc in findings_data:
            findings.append(EvidenceFinding(sev, score, desc, "", validator_id))
        total = calculate_evidence_score(findings, clean_passes)
        return EvidenceScoreReport(
            validator_id=validator_id,
            findings=tuple(findings),
            clean_passes=clean_passes,
            total_score=total,
            verdict=determine_verdict(total),
        )

    def test_single_validator(self):
        report = self._make_report(
            "V1",
            [
                (Severity.CRITICAL, 3.0, "SQL injection"),
                (Severity.MINOR, 0.3, "Naming issue"),
            ],
            clean_passes=2,
        )

        agg = aggregate_evidence_scores([report])
        assert agg.total_score == 2.3  # 3.0 + 0.3 - 1.0
        assert agg.verdict == Verdict.PASS
        assert agg.total_findings == 2
        assert len(agg.unique_findings) == 2
        assert len(agg.consensus_findings) == 0

    def test_consensus_detection(self):
        report_a = self._make_report(
            "VA",
            [
                (Severity.CRITICAL, 3.0, "SQL injection vulnerability"),
            ],
        )
        report_b = self._make_report(
            "VB",
            [
                (Severity.CRITICAL, 3.0, "SQL injection vulnerability"),
            ],
        )

        agg = aggregate_evidence_scores([report_a, report_b])
        # Both validators agree on same finding -> consensus
        assert len(agg.consensus_findings) == 1
        assert agg.consensus_ratio > 0

    def test_deduplication_keeps_highest_severity(self):
        report_a = self._make_report(
            "VA",
            [
                (Severity.MINOR, 0.3, "Missing input validation"),
            ],
        )
        report_b = self._make_report(
            "VB",
            [
                (Severity.CRITICAL, 3.0, "Missing input validation"),
            ],
        )

        agg = aggregate_evidence_scores([report_a, report_b])
        # Deduplicated to 1 finding, kept CRITICAL
        assert agg.total_findings == 1
        assert agg.findings_by_severity[Severity.CRITICAL] == 1
        assert agg.findings_by_severity[Severity.MINOR] == 0

    def test_empty_reports_raises(self):
        with pytest.raises(AllValidatorsFailedError):
            aggregate_evidence_scores([])

    def test_average_score(self):
        report_a = self._make_report(
            "VA",
            [
                (Severity.CRITICAL, 3.0, "Issue A only"),
            ],
        )
        report_b = self._make_report(
            "VB",
            [
                (Severity.IMPORTANT, 1.0, "Issue B only"),
            ],
        )

        agg = aggregate_evidence_scores([report_a, report_b])
        # Average: (3.0 + 1.0) / 2 = 2.0
        assert agg.total_score == 2.0


# =============================================================================
# Format Evidence Score Context Tests
# =============================================================================


class TestFormatEvidenceScoreContext:
    def test_validation_context(self):
        agg = EvidenceScoreAggregate(
            total_score=2.5,
            verdict=Verdict.PASS,
            per_validator_scores={"VA": 3.0, "VB": 2.0},
            per_validator_verdicts={"VA": Verdict.PASS, "VB": Verdict.PASS},
            findings_by_severity={
                Severity.CRITICAL: 1,
                Severity.IMPORTANT: 2,
                Severity.MINOR: 0,
            },
            total_findings=3,
            total_clean_passes=4,
            consensus_findings=(),
            unique_findings=(),
            consensus_ratio=0.33,
        )
        text = format_evidence_score_context(agg, context="validation")
        assert "PRE-CALCULATED EVIDENCE SCORE" in text
        assert "2.5" in text
        assert "READY" in text  # validation display for PASS
        assert "CRITICAL findings" in text
        assert "VA" in text
        assert "VB" in text

    def test_code_review_context(self):
        agg = EvidenceScoreAggregate(
            total_score=-3.5,
            verdict=Verdict.EXCELLENT,
            per_validator_scores={"R1": -3.5},
            per_validator_verdicts={"R1": Verdict.EXCELLENT},
            findings_by_severity={
                Severity.CRITICAL: 0,
                Severity.IMPORTANT: 0,
                Severity.MINOR: 0,
            },
            total_findings=0,
            total_clean_passes=7,
            consensus_findings=(),
            unique_findings=(),
            consensus_ratio=0.0,
        )
        text = format_evidence_score_context(agg, context="code_review")
        assert "EXEMPLARY" in text  # code_review display for EXCELLENT
        assert "-3.5" in text


# =============================================================================
# Exception Hierarchy Tests
# =============================================================================


class TestExceptions:
    def test_evidence_score_error_is_bmad_error(self):
        from bmad_assist_lite.core.exceptions import BmadAssistError

        assert issubclass(EvidenceScoreError, BmadAssistError)

    def test_all_validators_failed_is_evidence_error(self):
        assert issubclass(AllValidatorsFailedError, EvidenceScoreError)
