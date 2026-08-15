"""The structured finding record, its severity axis, and the finding-set hash.

REQ-08.0 is the substrate REQ-08.3 (hashing) and REQ-08.4 (severity) both
operate on. It is buildable and testable with no loop present, and the last
test in this file is what keeps the A14 dependency cycle from re-forming.
"""

from __future__ import annotations

import pytest

from bmad_assist_lite.validation.findings import (
    Bucket,
    Finding,
    FindingParseError,
    FindingSet,
    Severity,
    finding_set_hash,
    followup_review_recommended,
    parse_findings,
)


def _finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "file": "src/bmad_assist_lite/core/config.py",
        "anchor": "def load_config",
        "line": 42,
        "severity": Severity.MEDIUM,
        "bucket": Bucket.PATCH,
        "title": "Config path is not validated",
    }
    base.update(overrides)
    return Finding(**base)  # type: ignore[arg-type]


def _block(payload: str) -> str:
    return (
        "Some synthesis prose the model wrote first.\n\n"
        "<!-- BMAD-FINDINGS -->\n```json\n" + payload + "\n```\n<!-- /BMAD-FINDINGS -->\n"
    )


# ---------------------------------------------------------------------------
# REQ-08.0 — one record shape, one parser
# ---------------------------------------------------------------------------


class TestFindingRecord:
    def test_record_is_frozen(self) -> None:
        finding = _finding()
        with pytest.raises(Exception):
            finding.title = "mutated"  # type: ignore[misc]

    def test_hash_fields_are_declared_on_the_record(self) -> None:
        """REQ-08.0 crit 4 — hashing and severity cannot drift apart."""
        assert "severity" in Finding.HASH_FIELDS
        assert "bucket" in Finding.HASH_FIELDS
        assert "file" in Finding.HASH_FIELDS
        assert "line" not in Finding.HASH_FIELDS
        assert "line" in Finding.EXCLUDED_FROM_HASH
        assert set(Finding.HASH_FIELDS) & set(Finding.EXCLUDED_FROM_HASH) == set()

    def test_parser_produces_records(self) -> None:
        text = _block(
            '[{"file": "a.py", "line": 3, "severity": "high", "bucket": "patch",'
            ' "title": "Unchecked index", "anchor": "items[0]"}]'
        )
        found = parse_findings(text)
        assert len(found.findings) == 1
        assert found.findings[0].severity is Severity.HIGH
        assert found.findings[0].bucket is Bucket.PATCH

    def test_an_explicitly_empty_block_is_a_clean_review(self) -> None:
        found = parse_findings(_block("[]"))
        assert found.findings == ()
        assert found.is_clean

    def test_neg_a_parse_failure_is_not_a_silently_empty_finding_set(self) -> None:
        """REQ-08.0 crit 3 (load-bearing).

        An empty finding set reads as both "clean review" and "the hash
        converged" at once, so a swallowed parse error would assert two
        contradictory good-news facts simultaneously.
        """
        with pytest.raises(FindingParseError):
            parse_findings(_block("[{this is not json"))

    def test_neg_a_missing_block_is_a_parse_failure_not_a_clean_review(self) -> None:
        with pytest.raises(FindingParseError, match="no finding block"):
            parse_findings("The code looks great to me! No issues found.")

    def test_neg_an_unknown_severity_is_a_parse_failure(self) -> None:
        with pytest.raises(FindingParseError):
            parse_findings(
                _block('[{"file": "a.py", "severity": "catastrophic", '
                       '"bucket": "patch", "title": "x"}]')
            )

    def test_neg_the_record_does_not_depend_on_the_loop(self) -> None:
        """REQ-08.0 crit 5 — the test that keeps the A14 cycle from re-forming."""
        import bmad_assist_lite.validation.findings as findings_module

        source = findings_module.__file__
        assert source is not None
        text = open(source, encoding="utf-8").read()
        for forbidden in ("review_loop", "fix_review", "loop.handlers", "review_max_iterations"):
            assert forbidden not in text, (
                f"the finding record imports or references {forbidden!r}; it must be "
                "buildable before the loop exists"
            )

        # And it round-trips with no loop, no cap, no fix handler present.
        found = FindingSet(findings=(_finding(), _finding(title="Another")))
        assert finding_set_hash(found.findings)


# ---------------------------------------------------------------------------
# REQ-08.4 — severity, the orthogonal bucket axis, and the threshold cull
# ---------------------------------------------------------------------------


class TestSeverityAndBucket:
    def test_severity_is_ordered(self) -> None:
        assert Severity.LOW < Severity.MEDIUM < Severity.HIGH

    def test_bucket_is_orthogonal_to_severity(self) -> None:
        """A `defer`/`reject` finding never blocks, whatever its severity."""
        deferred = _finding(severity=Severity.HIGH, bucket=Bucket.DEFER)
        rejected = _finding(severity=Severity.HIGH, bucket=Bucket.REJECT)
        found = FindingSet(findings=(deferred, rejected))
        assert found.blocking(Severity.LOW) == ()

    def test_threshold_culls_below_threshold_findings(self) -> None:
        low = _finding(severity=Severity.LOW, title="Nit")
        high = _finding(severity=Severity.HIGH, title="Real bug")
        found = FindingSet(findings=(low, high))

        blocking = found.blocking(Severity.MEDIUM)
        assert [f.title for f in blocking] == ["Real bug"]

    def test_a_below_threshold_finding_is_still_recorded(self) -> None:
        """REQ-08.4 crit 4 — culled from the loop, not from the record."""
        low = _finding(severity=Severity.LOW, title="Nit")
        found = FindingSet(findings=(low,))
        assert found.blocking(Severity.MEDIUM) == ()
        assert low in found.findings


# ---------------------------------------------------------------------------
# H1 — followup_review_recommended, the deterministic cost lever
# ---------------------------------------------------------------------------


class TestFollowupScore:
    def test_any_high_recommends_a_followup(self) -> None:
        assert followup_review_recommended((_finding(severity=Severity.HIGH),))

    def test_neg_a_low_severity_set_declines_the_iteration(self) -> None:
        """H1 is what makes 'ON at 1' nearly free in the common case."""
        lows = tuple(_finding(severity=Severity.LOW, title=f"Nit {i}") for i in range(4))
        assert followup_review_recommended(lows) is False

    def test_two_medium_reaches_the_threshold(self) -> None:
        """3 x 2 mediums = 6 >= 5."""
        mediums = tuple(_finding(severity=Severity.MEDIUM, title=f"M{i}") for i in range(2))
        assert followup_review_recommended(mediums) is True

    def test_one_medium_alone_does_not(self) -> None:
        """3 x 1 = 3 < 5 — the case that keeps ON-at-1 cheap."""
        assert followup_review_recommended((_finding(severity=Severity.MEDIUM),)) is False

    def test_one_medium_and_one_low_does_not(self) -> None:
        """3*1 + 1*1 = 4 < 5."""
        mixed = (
            _finding(severity=Severity.MEDIUM, title="M"),
            _finding(severity=Severity.LOW, title="L"),
        )
        assert followup_review_recommended(mixed) is False

    def test_non_actionable_buckets_do_not_count_toward_the_score(self) -> None:
        deferred = tuple(
            _finding(severity=Severity.HIGH, bucket=Bucket.DEFER, title=f"D{i}")
            for i in range(5)
        )
        assert followup_review_recommended(deferred) is False


# ---------------------------------------------------------------------------
# REQ-08.3 — the finding-set hash and its normalisation
# ---------------------------------------------------------------------------


class TestFindingSetHash:
    def test_hash_is_order_insensitive(self) -> None:
        """REQ-08.3 crit 4."""
        a = _finding(title="Alpha")
        b = _finding(title="Beta", file="src/other.py")
        assert finding_set_hash((a, b)) == finding_set_hash((b, a))

    def test_hash_is_insensitive_to_line_shifts(self) -> None:
        """REQ-08.3 crit 4 — a hash that changed because the fix moved a line by
        one would defeat the whole check."""
        before = _finding(line=42)
        after = _finding(line=57)
        assert finding_set_hash((before,)) == finding_set_hash((after,))

    def test_hash_is_insensitive_to_whitespace_and_case_and_markdown(self) -> None:
        plain = _finding(title="Config path is not validated")
        noisy = _finding(title="  **Config   path**  is NOT  `validated`.  ")
        assert finding_set_hash((plain,)) == finding_set_hash((noisy,))

    def test_hash_ignores_embedded_line_numbers_in_the_title(self) -> None:
        """The same defect re-reported after a shift must hash the same."""
        first = _finding(title="Unchecked index at config.py:120")
        second = _finding(title="Unchecked index at config.py:133")
        assert finding_set_hash((first,)) == finding_set_hash((second,))

    def test_hash_is_duplicate_insensitive(self) -> None:
        one = _finding()
        assert finding_set_hash((one,)) == finding_set_hash((one, one))

    def test_hash_changes_when_a_finding_is_fixed(self) -> None:
        """REQ-08.3 crit 3 — genuine progress must move the hash."""
        a = _finding(title="Alpha")
        b = _finding(title="Beta", file="src/other.py")
        assert finding_set_hash((a, b)) != finding_set_hash((a,))

    def test_hash_changes_when_severity_is_downgraded(self) -> None:
        """A downgrade is progress, so severity is inside the hash."""
        high = _finding(severity=Severity.HIGH)
        low = _finding(severity=Severity.LOW)
        assert finding_set_hash((high,)) != finding_set_hash((low,))

    def test_a_moved_defect_hashes_the_same_deliberately(self) -> None:
        """A hash blind to line numbers is also blind to a fix that only *moved*
        the defect — and that is the correct reading. Moving a defect is not
        fixing it, so the anchor (not the line) is what identifies it."""
        original = _finding(file="a.py", anchor="items[0]", line=10)
        relocated = _finding(file="a.py", anchor="items[0]", line=300)
        assert finding_set_hash((original,)) == finding_set_hash((relocated,))

        genuinely_fixed = _finding(file="a.py", anchor="items[0] if items else None", line=10)
        assert finding_set_hash((genuinely_fixed,)) != finding_set_hash((original,))

    def test_empty_set_has_a_stable_hash(self) -> None:
        assert finding_set_hash(()) == finding_set_hash(())
        assert finding_set_hash(()) != finding_set_hash((_finding(),))
