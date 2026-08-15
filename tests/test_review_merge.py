"""Tests for the deterministic cross-reviewer finding merge (SP-1, goal-run6)."""

from __future__ import annotations

from bmad_assist_lite.loop.review_merge import (
    ADJUDICATION_CLOSE_MARKER,
    ADJUDICATION_OPEN_MARKER,
    apply_adjudication,
    high_severity_preserved,
    merge_findings,
    parse_adjudication,
    parse_reviewer_findings,
    render_adjudication_candidates,
)
from bmad_assist_lite.validation.findings import (
    Bucket,
    Finding,
    Severity,
    render_findings_block,
)


def _finding(
    file: str,
    title: str,
    severity: Severity = Severity.MEDIUM,
    bucket: Bucket = Bucket.PATCH,
    anchor: str = "",
    source: str = "",
) -> Finding:
    return Finding(
        file=file,
        title=title,
        severity=severity,
        bucket=bucket,
        anchor=anchor,
        source=source,
    )


def _review(reviewer: str, findings: list[Finding], exit_code: int = 0) -> dict:
    return {
        "reviewer": reviewer,
        "response": "Some prose.\n\n" + render_findings_block(findings),
        "exit_code": exit_code,
    }


class TestParseReviewerFindings:
    def test_parses_each_lane_and_tags_source(self):
        reviews = [
            _review("Reviewer-1", [_finding("a.py", "bug A", Severity.HIGH)]),
            _review("Reviewer-2", [_finding("b.py", "bug B", Severity.LOW)]),
        ]
        findings, notes = parse_reviewer_findings(reviews)
        assert notes == []
        assert {f.source for f in findings} == {"Reviewer-1", "Reviewer-2"}
        assert {f.title for f in findings} == {"bug A", "bug B"}

    def test_missing_block_is_noted_not_fatal(self):
        reviews = [
            {"reviewer": "Reviewer-1", "response": "no block here", "exit_code": 0},
            _review("Reviewer-2", [_finding("b.py", "bug B", Severity.HIGH)]),
        ]
        findings, notes = parse_reviewer_findings(reviews)
        assert len(findings) == 1
        assert findings[0].title == "bug B"
        assert len(notes) == 1 and "Reviewer-1" in notes[0]

    def test_failed_lane_skipped(self):
        reviews = [
            {"reviewer": "Reviewer-1", "error": "boom", "exit_code": 1},
            _review("Reviewer-2", [_finding("b.py", "bug B")]),
        ]
        findings, notes = parse_reviewer_findings(reviews)
        assert [f.title for f in findings] == ["bug B"]
        assert notes == []

    def test_missing_exit_code_is_noted_not_silently_skipped(self):
        # A lane dict WITHOUT the exit_code key means a producer-side change;
        # skipping it silently would quietly disable the structured path.
        reviews = [
            {
                "reviewer": "Reviewer-1",
                "response": render_findings_block([_finding("a.py", "bug A")]),
            }
        ]
        findings, notes = parse_reviewer_findings(reviews)
        assert findings == []
        assert len(notes) == 1 and "exit_code" in notes[0]

    def test_validator_key_supported(self):
        reviews = [
            {
                "validator": "Validator-1",
                "response": render_findings_block([_finding("s.md", "gap")]),
                "exit_code": 0,
            }
        ]
        findings, _ = parse_reviewer_findings(reviews)
        assert findings[0].source == "Validator-1"


class TestMergeFindings:
    def test_identical_findings_collapse_and_union_sources(self):
        f1 = _finding("a.py", "same bug", Severity.HIGH, anchor="def foo", source="Reviewer-1")
        f2 = _finding("a.py", "same bug", Severity.HIGH, anchor="def foo", source="Reviewer-2")
        merged = merge_findings([f1, f2])
        assert len(merged) == 1
        assert set(merged[0].source.split(", ")) == {"Reviewer-1", "Reviewer-2"}

    def test_distinct_findings_all_kept(self):
        inputs = [
            _finding("a.py", "bug A", Severity.HIGH),
            _finding("b.py", "bug B", Severity.MEDIUM),
            _finding("a.py", "bug A", Severity.LOW),  # different severity => distinct
        ]
        merged = merge_findings(inputs)
        assert len(merged) == 3

    def test_deterministic_severity_desc_order(self):
        inputs = [
            _finding("z.py", "low one", Severity.LOW),
            _finding("a.py", "high one", Severity.HIGH),
            _finding("m.py", "med one", Severity.MEDIUM),
        ]
        merged = merge_findings(inputs)
        assert [f.severity for f in merged] == [Severity.HIGH, Severity.MEDIUM, Severity.LOW]

    def test_order_independent(self):
        a = _finding("a.py", "bug A", Severity.HIGH)
        b = _finding("b.py", "bug B", Severity.MEDIUM)
        assert [f.digest() for f in merge_findings([a, b])] == [
            f.digest() for f in merge_findings([b, a])
        ]


class TestHighSeverityPreserved:
    def test_guard_holds_for_merge(self):
        inputs = [
            _finding("a.py", "bug A", Severity.HIGH, source="Reviewer-1"),
            _finding("a.py", "bug A", Severity.HIGH, source="Reviewer-2"),  # dup
            _finding("b.py", "bug B", Severity.LOW),
        ]
        merged = merge_findings(inputs)
        assert high_severity_preserved(inputs, merged)

    def test_guard_fails_if_high_dropped(self):
        inputs = [_finding("a.py", "bug A", Severity.HIGH)]
        assert not high_severity_preserved(inputs, [])


class TestRenderCandidates:
    def test_stable_ids_and_map(self):
        merged = merge_findings(
            [
                _finding("a.py", "high one", Severity.HIGH),
                _finding("b.py", "med one", Severity.MEDIUM),
            ]
        )
        text, id_map = render_adjudication_candidates(merged)
        assert set(id_map) == {"F1", "F2"}
        assert "F1" in text and "F2" in text
        assert id_map["F1"].severity == Severity.HIGH  # sorted highest first


class TestParseAdjudication:
    def test_parses_marked_block(self):
        text = (
            "verdict prose\n"
            f"{ADJUDICATION_OPEN_MARKER}\n"
            "```json\n"
            '{"verdict": "one blocking bug", '
            '"decisions": [{"id": "F1", "bucket": "patch"}, '
            '{"id": "F2", "bucket": "defer"}]}\n'
            "```\n"
            f"{ADJUDICATION_CLOSE_MARKER}\n"
        )
        result = parse_adjudication(text)
        assert result is not None
        assert result.verdict == "one blocking bug"
        assert result.decisions == {"F1": Bucket.PATCH, "F2": Bucket.DEFER}

    def test_bare_fence_tolerated(self):
        text = '```json\n{"decisions": [{"id": "F1", "bucket": "reject"}]}\n```'
        result = parse_adjudication(text)
        assert result is not None and result.decisions == {"F1": Bucket.REJECT}

    def test_invalid_bucket_skipped(self):
        text = f'{ADJUDICATION_OPEN_MARKER}{{"decisions": [{{"id": "F1", "bucket": "nonsense"}}]}}'
        result = parse_adjudication(text)
        assert result is not None and result.decisions == {}

    def test_unparseable_returns_none(self):
        assert parse_adjudication("no json at all") is None
        assert parse_adjudication("```json\nnot json\n```") is None


class TestApplyAdjudication:
    def test_bucket_override_applied(self):
        merged = merge_findings([_finding("a.py", "bug", Severity.HIGH, bucket=Bucket.PATCH)])
        _text, id_map = render_adjudication_candidates(merged)
        result = parse_adjudication(
            f'{ADJUDICATION_OPEN_MARKER}{{"decisions":[{{"id":"F1","bucket":"defer"}}]}}'
        )
        fs = apply_adjudication(id_map, result)
        assert len(fs.findings) == 1
        assert fs.findings[0].bucket == Bucket.DEFER
        # A high finding deferred is no longer blocking (defer never blocks).
        assert fs.blocking(Severity.MEDIUM) == ()

    def test_none_keeps_reviewer_buckets(self):
        merged = merge_findings(
            [_finding("a.py", "bug", Severity.HIGH, bucket=Bucket.PATCH)]
        )
        _text, id_map = render_adjudication_candidates(merged)
        fs = apply_adjudication(id_map, None)
        assert fs.findings[0].bucket == Bucket.PATCH
        assert len(fs.blocking(Severity.MEDIUM)) == 1

    def test_membership_fixed_by_id_map(self):
        merged = merge_findings(
            [
                _finding("a.py", "bug A", Severity.HIGH),
                _finding("b.py", "bug B", Severity.MEDIUM),
            ]
        )
        _text, id_map = render_adjudication_candidates(merged)
        # A decision referencing an unknown id cannot add a finding.
        result = parse_adjudication(
            f'{ADJUDICATION_OPEN_MARKER}{{"decisions":[{{"id":"F99","bucket":"patch"}}]}}'
        )
        fs = apply_adjudication(id_map, result)
        assert len(fs.findings) == 2
