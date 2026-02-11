"""Tests for crash recovery cleanup."""

import logging

import pytest

from bmad_assist_lite.core.state import Phase
from bmad_assist_lite.loop.cleanup import cleanup_for_phase


class TestCleanupForPhase:
    """Tests for cleanup_for_phase."""

    def test_cleans_tmp_files(self, tmp_path):
        """Removes *.tmp files from cache directory."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        (cache_dir / "partial.tmp").write_text("data")
        (cache_dir / "output.yaml").write_text("keep")

        cleaned = cleanup_for_phase(Phase.CREATE_STORY, tmp_path)

        assert len(cleaned) == 1
        assert "partial.tmp" in cleaned[0]
        assert not (cache_dir / "partial.tmp").exists()
        assert (cache_dir / "output.yaml").exists()

    def test_no_cache_dir(self, tmp_path):
        """No crash if cache dir doesn't exist."""
        cleaned = cleanup_for_phase(Phase.CREATE_STORY, tmp_path)
        assert cleaned == []

    def test_dev_story_warning(self, tmp_path, caplog):
        """DEV_STORY phase logs a warning about uncommitted git changes."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        with caplog.at_level(logging.WARNING):
            cleanup_for_phase(Phase.DEV_STORY, tmp_path)

        assert any("uncommitted git changes" in r.message for r in caplog.records)

    def test_returns_cleaned_paths(self, tmp_path):
        """Returns list of cleaned file paths."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "a.tmp").write_text("x")
        (cache_dir / "b.tmp").write_text("y")

        cleaned = cleanup_for_phase(Phase.VALIDATE_STORY, tmp_path)
        assert len(cleaned) == 2

    def test_multiple_tmp_files(self, tmp_path):
        """Multiple tmp files are all cleaned."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)
        for i in range(5):
            (cache_dir / f"file{i}.tmp").write_text(f"data{i}")

        cleaned = cleanup_for_phase(Phase.CODE_REVIEW, tmp_path)
        assert len(cleaned) == 5
        assert list(cache_dir.glob("*.tmp")) == []
