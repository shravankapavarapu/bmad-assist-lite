"""Tests for sprint-status-driven story discovery.

Tests the filtering and epic file validation logic used in cli.py
to build the story queue from sprint-status.yaml.
"""

import pytest

from bmad_assist_lite.cli import _find_epic_file, _cache_story_queue, load_story_queue_cache
from bmad_assist_lite.core.sprint_status import SprintStatus


class TestFindEpicFile:
    """Tests for _find_epic_file epic file discovery."""

    def test_finds_specific_epic_file(self, tmp_path):
        """Finds epic-1.md by specific pattern."""
        (tmp_path / "epic-1.md").write_text("# Epic 1")
        result = _find_epic_file(tmp_path, 1)
        assert result is not None
        assert result.name == "epic-1.md"

    def test_finds_epic_no_dash(self, tmp_path):
        """Finds epic1.md (no dash) pattern."""
        (tmp_path / "epic1.md").write_text("# Epic 1")
        result = _find_epic_file(tmp_path, 1)
        assert result is not None
        assert result.name == "epic1.md"

    def test_finds_master_epics_file(self, tmp_path):
        """Falls back to epics.md master file."""
        (tmp_path / "epics.md").write_text("# All Epics")
        result = _find_epic_file(tmp_path, 1)
        assert result is not None
        assert result.name == "epics.md"

    def test_finds_wildcard_epic_file(self, tmp_path):
        """Falls back to *epic*.md wildcard pattern."""
        (tmp_path / "my-epic-doc.md").write_text("# Epic Stuff")
        result = _find_epic_file(tmp_path, 1)
        assert result is not None
        assert result.name == "my-epic-doc.md"

    def test_returns_none_when_no_file(self, tmp_path):
        """Returns None when no epic file exists."""
        result = _find_epic_file(tmp_path, 1)
        assert result is None

    def test_returns_none_when_dir_missing(self, tmp_path):
        """Returns None when planning directory doesn't exist."""
        result = _find_epic_file(tmp_path / "nonexistent", 1)
        assert result is None

    def test_prefers_specific_over_master(self, tmp_path):
        """Specific epic-1.md is preferred over epics.md."""
        (tmp_path / "epic-1.md").write_text("# Epic 1")
        (tmp_path / "epics.md").write_text("# All Epics")
        result = _find_epic_file(tmp_path, 1)
        assert result is not None
        assert result.name == "epic-1.md"


class TestBacklogStoryDiscovery:
    """Tests for sprint-status-based story discovery."""

    def test_backlog_stories_grouped_by_epic(self):
        """Backlog stories are correctly parsed and ordered."""
        ss = SprintStatus(development_status={
            "epic-1": "backlog",
            "1-1-setup": "backlog",
            "1-2-auth": "backlog",
            "epic-2": "backlog",
            "2-1-api": "backlog",
        })
        stories = ss.find_backlog_stories()
        assert len(stories) == 3
        assert stories[0] == (1, 1, "1-1-setup")
        assert stories[1] == (1, 2, "1-2-auth")
        assert stories[2] == (2, 1, "2-1-api")

    def test_no_backlog_stories_returns_empty(self):
        """Returns empty when all stories are done."""
        ss = SprintStatus(development_status={
            "1-1-setup": "done",
            "1-2-auth": "done",
        })
        stories = ss.find_backlog_stories()
        assert stories == []

    def test_missing_epic_file_stops_immediately(self, tmp_path):
        """Epic file missing for a needed epic returns None from _find_epic_file."""
        # No files in planning dir
        planning_dir = tmp_path / "planning"
        planning_dir.mkdir()
        result = _find_epic_file(planning_dir, 1)
        assert result is None


class TestStoryQueueCache:
    """Tests for cached story queue write/read."""

    def test_cache_round_trip(self, tmp_path):
        """Cache write and read preserves data."""
        cache_dir = tmp_path / "cache"
        epics = [1, 2]
        stories = {1: ["1.1", "1.2"], 2: ["2.1"]}
        key_map = {"1.1": "1-1-setup", "1.2": "1-2-auth", "2.1": "2-1-api"}
        epic_files = {1: tmp_path / "epic-1.md", 2: tmp_path / "epic-2.md"}

        _cache_story_queue(cache_dir, epics, stories, key_map, epic_files)

        loaded = load_story_queue_cache(cache_dir)
        assert loaded is not None
        assert loaded["epics"] == [1, 2]
        assert loaded["stories_for_epic"] == {1: ["1.1", "1.2"], 2: ["2.1"]}
        assert loaded["story_key_map"]["1.1"] == "1-1-setup"

    def test_cache_missing_returns_none(self, tmp_path):
        """Loading from non-existent cache returns None."""
        result = load_story_queue_cache(tmp_path / "nonexistent")
        assert result is None

    def test_cache_corrupt_returns_none(self, tmp_path):
        """Loading from corrupt cache returns None."""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "story-queue.yaml").write_text(": [\ninvalid")
        result = load_story_queue_cache(cache_dir)
        assert result is None
