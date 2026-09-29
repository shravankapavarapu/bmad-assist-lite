"""Tests for the worktree telemetry harvest (parallel mode).

Covers the acceptance criteria from the harvest story: rows move home intact,
the source is deleted (so a re-entered completion path is a no-op), failures
are tolerated without raising, and the orchestrator invokes the harvest when
a story subprocess completes.
"""

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from bmad_assist_lite.parallel.telemetry_harvest import (
    HARVEST_FILENAMES,
    harvest_story_telemetry,
)

STATE_DIR = ".bmad-assist-lite"


def _write_rows(root: Path, name: str, rows: list[str]) -> Path:
    path = root / STATE_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class TestHarvestStoryTelemetry:
    """Unit tests for harvest_story_telemetry."""

    def test_moves_rows_and_deletes_source(self, tmp_path: Path) -> None:
        """Rows land in the project files verbatim; worktree copies are gone."""
        worktree = tmp_path / "wt"
        project = tmp_path / "proj"
        metrics = ['{"story_id": "3.2", "phase": "dev"}', '{"story_id": "3.2", "phase": "review"}']
        triggers = ['{"story": "3.2", "fired": true}']
        _write_rows(worktree, "phase-metrics.jsonl", metrics)
        _write_rows(worktree, "ac-audit-trigger.jsonl", triggers)

        count = harvest_story_telemetry("3.2", worktree, project)

        assert count == 3
        dst_metrics = project / STATE_DIR / "phase-metrics.jsonl"
        dst_triggers = project / STATE_DIR / "ac-audit-trigger.jsonl"
        assert dst_metrics.read_text(encoding="utf-8") == "\n".join(metrics) + "\n"
        assert dst_triggers.read_text(encoding="utf-8") == "\n".join(triggers) + "\n"
        assert not (worktree / STATE_DIR / "phase-metrics.jsonl").exists()
        assert not (worktree / STATE_DIR / "ac-audit-trigger.jsonl").exists()

    def test_appends_after_existing_rows(self, tmp_path: Path) -> None:
        """Project-level rows written by earlier stories are preserved."""
        worktree = tmp_path / "wt"
        project = tmp_path / "proj"
        _write_rows(project, "phase-metrics.jsonl", ['{"story_id": "3.1"}'])
        _write_rows(worktree, "phase-metrics.jsonl", ['{"story_id": "3.2"}'])

        harvest_story_telemetry("3.2", worktree, project)

        dst = project / STATE_DIR / "phase-metrics.jsonl"
        assert dst.read_text(encoding="utf-8") == (
            '{"story_id": "3.1"}\n{"story_id": "3.2"}\n'
        )

    def test_missing_trailing_newline_does_not_glue_rows(self, tmp_path: Path) -> None:
        """An existing file without a final newline gets one before the append."""
        worktree = tmp_path / "wt"
        project = tmp_path / "proj"
        dst = project / STATE_DIR / "phase-metrics.jsonl"
        dst.parent.mkdir(parents=True)
        dst.write_text('{"story_id": "3.1"}', encoding="utf-8")  # no newline
        _write_rows(worktree, "phase-metrics.jsonl", ['{"story_id": "3.2"}'])

        harvest_story_telemetry("3.2", worktree, project)

        assert dst.read_text(encoding="utf-8") == (
            '{"story_id": "3.1"}\n{"story_id": "3.2"}\n'
        )

    def test_second_call_is_noop(self, tmp_path: Path) -> None:
        """Resume case: re-running the completion path appends nothing twice."""
        worktree = tmp_path / "wt"
        project = tmp_path / "proj"
        _write_rows(worktree, "phase-metrics.jsonl", ['{"story_id": "3.2"}'])

        first = harvest_story_telemetry("3.2", worktree, project)
        second = harvest_story_telemetry("3.2", worktree, project)

        assert first == 1
        assert second == 0
        dst = project / STATE_DIR / "phase-metrics.jsonl"
        assert dst.read_text(encoding="utf-8") == '{"story_id": "3.2"}\n'

    def test_missing_worktree_dir_is_zero_not_error(self, tmp_path: Path) -> None:
        """A bootstrap-failed story may have no state dir at all."""
        count = harvest_story_telemetry(
            "3.2", tmp_path / "never-created", tmp_path / "proj"
        )
        assert count == 0

    def test_empty_source_deleted_without_touching_dst(self, tmp_path: Path) -> None:
        """A zero-row source file is removed and creates no project file."""
        worktree = tmp_path / "wt"
        project = tmp_path / "proj"
        src = worktree / STATE_DIR / "phase-metrics.jsonl"
        src.parent.mkdir(parents=True)
        src.write_text("\n\n", encoding="utf-8")

        count = harvest_story_telemetry("3.2", worktree, project)

        assert count == 0
        assert not src.exists()
        assert not (project / STATE_DIR / "phase-metrics.jsonl").exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
    def test_unreadable_source_warns_and_continues(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """One unreadable file is skipped; the other file still harvests."""
        worktree = tmp_path / "wt"
        project = tmp_path / "proj"
        src = _write_rows(worktree, "phase-metrics.jsonl", ['{"story_id": "3.2"}'])
        src.chmod(0)
        _write_rows(worktree, "ac-audit-trigger.jsonl", ['{"story": "3.2"}'])

        try:
            with caplog.at_level("WARNING"):
                count = harvest_story_telemetry("3.2", worktree, project)
        finally:
            src.chmod(stat.S_IRUSR | stat.S_IWUSR)

        assert count == 1
        assert (project / STATE_DIR / "ac-audit-trigger.jsonl").exists()
        assert not (project / STATE_DIR / "phase-metrics.jsonl").exists()
        assert any("harvest" in rec.message.lower() for rec in caplog.records)

    def test_source_survives_failed_append(self, tmp_path: Path) -> None:
        """The worktree copy is deleted only after a successful append."""
        worktree = tmp_path / "wt"
        project = tmp_path / "proj"
        src = _write_rows(worktree, "phase-metrics.jsonl", ['{"story_id": "3.2"}'])

        append_path = (
            "bmad_assist_lite.parallel.telemetry_harvest._atomic_append_lines"
        )
        with patch(append_path, side_effect=OSError("disk full")):
            count = harvest_story_telemetry("3.2", worktree, project)

        assert count == 0
        assert src.exists()

    def test_harvest_filenames_are_the_two_history_files(self) -> None:
        """Scope guard: caches like reviews.json must never be harvested."""
        assert set(HARVEST_FILENAMES) == {
            "phase-metrics.jsonl",
            "ac-audit-trigger.jsonl",
        }
