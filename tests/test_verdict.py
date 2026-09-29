"""Tests for core.verdict — the three-witness "review owns done" machinery."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bmad_assist_lite.core.verdict import (
    ReviewVerdictRecord,
    load_review_verdict,
    parse_audit_table,
    read_story_status,
    seed_blocks_done,
    verdict_blocks_done,
    verdict_path,
    write_review_verdict,
)

STORY = "3.1"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _record(**overrides: object) -> ReviewVerdictRecord:
    """An approving record; overrides flip individual witnesses."""
    data: dict[str, object] = {
        "story_id": STORY,
        "verdict": "PASS",
        "outcome": "clean",
        "review_iteration": 2,
        "full_pass": True,
        "audit_required": True,
        "audit_ran": True,
        "audit_passed": True,
        "evidence_score": 1.5,
        "timestamp": _now(),
    }
    data.update(overrides)
    return ReviewVerdictRecord.model_validate(data)


def _write_story(project: Path, status: str, story: str = STORY) -> Path:
    stories = project / "_bmad-output" / "implementation-artifacts"
    stories.mkdir(parents=True, exist_ok=True)
    epic, num = story.split(".")
    path = stories / f"story-{epic}.{num}.md"
    path.write_text(
        f"# Story {story}: something\n\nStatus: {status}\n\n## Body\n",
        encoding="utf-8",
    )
    return path


# ============================================================================
# Record persistence
# ============================================================================


class TestRecordPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        record = _record()
        path = write_review_verdict(record, tmp_path)
        assert path == verdict_path(tmp_path, STORY)
        loaded = load_review_verdict(tmp_path, STORY)
        assert loaded == record

    def test_missing_reads_as_none(self, tmp_path: Path) -> None:
        assert load_review_verdict(tmp_path, STORY) is None

    def test_corrupt_artifact_reads_as_none(self, tmp_path: Path) -> None:
        path = verdict_path(tmp_path, STORY)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("verdict: [unclosed", encoding="utf-8")
        assert load_review_verdict(tmp_path, STORY) is None

    def test_path_is_committed_content_not_state_dir(self, tmp_path: Path) -> None:
        """The artifact must survive a parallel merge, so it lives under
        implementation-artifacts, not the gitignored state dir."""
        path = verdict_path(tmp_path, STORY)
        assert "_bmad-output" in path.parts
        assert ".bmad-assist-lite" not in path.parts


# ============================================================================
# Witness two: the story file's Status line
# ============================================================================


class TestReadStoryStatus:
    def test_reads_the_template_line(self, tmp_path: Path) -> None:
        path = _write_story(tmp_path, "done")
        assert read_story_status(path) == "done"

    def test_tolerates_bold_markup_and_case(self, tmp_path: Path) -> None:
        path = tmp_path / "story.md"
        path.write_text("# T\n\n**Status:** Ready-For-Dev\n", encoding="utf-8")
        assert read_story_status(path) == "ready-for-dev"

    def test_missing_file_reads_as_none(self, tmp_path: Path) -> None:
        assert read_story_status(tmp_path / "absent.md") is None

    def test_no_status_line_reads_as_none(self, tmp_path: Path) -> None:
        path = tmp_path / "story.md"
        path.write_text("# T\n\nno status here\n", encoding="utf-8")
        assert read_story_status(path) is None

    def test_status_deep_in_the_body_is_not_the_witness(self, tmp_path: Path) -> None:
        """Only the head of the file testifies — a 'Status:' in prose far
        down the document is not the story's own status line."""
        body = "# T\n" + ("filler\n" * 40) + "Status: done\n"
        path = tmp_path / "story.md"
        path.write_text(body, encoding="utf-8")
        assert read_story_status(path) is None


# ============================================================================
# Witness three helper: audit table parsing
# ============================================================================


class TestParseAuditTable:
    def test_all_complete_passes(self) -> None:
        text = (
            "| AC | verdict | evidence |\n"
            "| AC-1 | COMPLETE | src/a.py:12 |\n"
            "| AC-2 | complete | src/b.py:9 |\n"
        )
        assert parse_audit_table(text) is True

    def test_any_partial_or_missing_fails(self) -> None:
        text = (
            "| AC-1 | COMPLETE | ok |\n"
            "| AC-2 | PARTIAL | clause without evidence |\n"
        )
        assert parse_audit_table(text) is False
        assert parse_audit_table("| AC-1 | MISSING | nothing |") is False

    def test_bold_verdict_cells_are_recognised(self) -> None:
        assert parse_audit_table("| AC-1 | **COMPLETE** | ok |") is True

    def test_no_table_is_unknown_not_a_pass(self) -> None:
        assert parse_audit_table("I looked at everything, seems fine.") is None
        assert parse_audit_table("") is None


# ============================================================================
# The promotion gate — absence of evidence is dissent
# ============================================================================


class TestVerdictBlocksDone:
    def test_all_three_witnesses_agree_permits_done(self, tmp_path: Path) -> None:
        write_review_verdict(_record(), tmp_path)
        _write_story(tmp_path, "done")
        assert verdict_blocks_done(tmp_path, STORY) is None

    def test_no_record_blocks(self, tmp_path: Path) -> None:
        _write_story(tmp_path, "done")
        reason = verdict_blocks_done(tmp_path, STORY)
        assert reason is not None
        assert "no recorded review verdict" in reason

    def test_rejecting_verdict_blocks(self, tmp_path: Path) -> None:
        write_review_verdict(_record(verdict="REJECT"), tmp_path)
        _write_story(tmp_path, "done")
        reason = verdict_blocks_done(tmp_path, STORY)
        assert reason is not None
        assert "REJECT" in reason

    def test_missing_aggregate_verdict_blocks(self, tmp_path: Path) -> None:
        write_review_verdict(_record(verdict=None), tmp_path)
        _write_story(tmp_path, "done")
        assert verdict_blocks_done(tmp_path, STORY) is not None

    def test_delta_round_verdict_cannot_promote(self, tmp_path: Path) -> None:
        """A story rejected for six criteria that fixes two must not come
        back approved on the delta — only a full pass promotes."""
        write_review_verdict(_record(full_pass=False), tmp_path)
        _write_story(tmp_path, "done")
        reason = verdict_blocks_done(tmp_path, STORY)
        assert reason is not None
        assert "delta" in reason

    def test_story_file_not_done_blocks(self, tmp_path: Path) -> None:
        write_review_verdict(_record(), tmp_path)
        _write_story(tmp_path, "in-progress")
        reason = verdict_blocks_done(tmp_path, STORY)
        assert reason is not None
        assert "in-progress" in reason

    def test_missing_story_file_blocks(self, tmp_path: Path) -> None:
        write_review_verdict(_record(), tmp_path)
        assert verdict_blocks_done(tmp_path, STORY) is not None

    def test_audit_that_did_not_run_blocks(self, tmp_path: Path) -> None:
        write_review_verdict(
            _record(audit_ran=False, audit_passed=None), tmp_path
        )
        _write_story(tmp_path, "done")
        reason = verdict_blocks_done(tmp_path, STORY)
        assert reason is not None
        assert "did not run" in reason

    def test_failing_audit_blocks(self, tmp_path: Path) -> None:
        write_review_verdict(_record(audit_passed=False), tmp_path)
        _write_story(tmp_path, "done")
        assert verdict_blocks_done(tmp_path, STORY) is not None

    def test_unparseable_audit_is_dissent_not_a_pass(self, tmp_path: Path) -> None:
        write_review_verdict(_record(audit_passed=None), tmp_path)
        _write_story(tmp_path, "done")
        reason = verdict_blocks_done(tmp_path, STORY)
        assert reason is not None
        assert "unparseable" in reason

    def test_audit_witness_is_vacuous_when_lever_is_off(self, tmp_path: Path) -> None:
        """A config with ac_audit fully off cannot produce audit evidence;
        demanding it would park every story for an unsatisfiable reason."""
        write_review_verdict(
            _record(audit_required=False, audit_ran=False, audit_passed=None),
            tmp_path,
        )
        _write_story(tmp_path, "done")
        assert verdict_blocks_done(tmp_path, STORY) is None

    def test_excellent_verdict_also_promotes(self, tmp_path: Path) -> None:
        write_review_verdict(_record(verdict="EXCELLENT"), tmp_path)
        _write_story(tmp_path, "done")
        assert verdict_blocks_done(tmp_path, STORY) is None

    def test_display_form_approve_is_accepted(self, tmp_path: Path) -> None:
        write_review_verdict(_record(verdict="APPROVE"), tmp_path)
        _write_story(tmp_path, "done")
        assert verdict_blocks_done(tmp_path, STORY) is None


# ============================================================================
# The seeding gate — blocks only on positive disagreement
# ============================================================================


class TestSeedBlocksDone:
    def test_incident_shape_story_file_disagrees(self, tmp_path: Path) -> None:
        """The epic-11 resume seeded a story done from the tracker while its
        story file said otherwise. That exact shape must block."""
        _write_story(tmp_path, "blocked")
        reason = seed_blocks_done(tmp_path, STORY)
        assert reason is not None
        assert "not corroborated" in reason

    def test_absence_of_all_evidence_permits_seeding(self, tmp_path: Path) -> None:
        """Pre-feature done rows have no artifacts; refusing them would
        re-run finished work on upgrade."""
        assert seed_blocks_done(tmp_path, STORY) is None

    def test_story_file_done_is_the_senior_witness(self, tmp_path: Path) -> None:
        """The operator's out-of-band pass promotes a parked story by editing
        the story file; a stale in-loop REJECT record must not overrule that
        promotion on every later resume."""
        _write_story(tmp_path, "done")
        write_review_verdict(_record(verdict="REJECT"), tmp_path)
        assert seed_blocks_done(tmp_path, STORY) is None

    def test_dissenting_record_blocks_when_story_file_cannot_testify(
        self, tmp_path: Path
    ) -> None:
        write_review_verdict(_record(verdict="REJECT"), tmp_path)
        reason = seed_blocks_done(tmp_path, STORY)
        assert reason is not None
        assert "REJECT" in reason

    def test_approving_record_with_no_story_file_permits(self, tmp_path: Path) -> None:
        write_review_verdict(_record(), tmp_path)
        assert seed_blocks_done(tmp_path, STORY) is None
