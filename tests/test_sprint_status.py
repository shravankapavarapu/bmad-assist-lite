"""Tests for sprint status model, YAML I/O, key resolution, and backlog discovery."""

import pytest
import yaml

from bmad_assist_lite.core.sprint_status import (
    SprintStatus,
    get_sprint_status_path,
    load_sprint_status,
    save_sprint_status,
)


class TestSprintStatusModel:
    """Tests for the SprintStatus Pydantic model."""

    def test_empty_model(self):
        """Fresh SprintStatus has empty development_status."""
        ss = SprintStatus()
        assert ss.development_status == {}
        assert ss.generated is not None

    def test_set_and_get_story_status(self):
        """Setting a story status and retrieving it."""
        ss = SprintStatus()
        ss.set_story_status("1.2", "in-progress")
        assert ss.get_story_status("1.2") == "in-progress"

    def test_get_story_status_missing(self):
        """Getting status for a non-existent story returns None."""
        ss = SprintStatus()
        assert ss.get_story_status("99.99") is None

    def test_is_story_done(self):
        """is_story_done returns True for done/complete/completed."""
        ss = SprintStatus()
        ss.set_story_status("1.1", "done")
        ss.set_story_status("1.2", "complete")
        ss.set_story_status("1.3", "completed")
        ss.set_story_status("1.4", "in-progress")

        assert ss.is_story_done("1.1") is True
        assert ss.is_story_done("1.2") is True
        assert ss.is_story_done("1.3") is True
        assert ss.is_story_done("1.4") is False
        assert ss.is_story_done("1.5") is False

    def test_dot_to_dash_key_resolution(self):
        """Dot notation 1.2 maps to story-1-2 key."""
        ss = SprintStatus()
        ss.set_story_status("1.2", "review")
        assert "story-1-2" in ss.development_status
        assert ss.get_story_status("1.2") == "review"

    def test_set_and_get_epic_status(self):
        """Setting and getting epic status."""
        ss = SprintStatus()
        ss.set_epic_status(1, "in-progress")
        assert ss.get_epic_status(1) == "in-progress"

    def test_is_epic_done(self):
        """is_epic_done returns True for done statuses."""
        ss = SprintStatus()
        ss.set_epic_status(1, "done")
        ss.set_epic_status(2, "in-progress")

        assert ss.is_epic_done(1) is True
        assert ss.is_epic_done(2) is False
        assert ss.is_epic_done(3) is False

    def test_prefix_key_search(self):
        """Key prefix search finds story-1-2-title when searching for 1.2."""
        ss = SprintStatus(development_status={"story-1-2-setup-auth": "done"})
        assert ss.get_story_status("1.2") == "done"
        assert ss.is_story_done("1.2") is True


class TestBacklogDiscovery:
    """Tests for find_next_backlog_story and find_backlog_stories."""

    def test_find_backlog_stories_basic(self):
        """Finds backlog stories in insertion order."""
        ss = SprintStatus(
            development_status={
                "epic-1": "backlog",
                "1-1-setup": "backlog",
                "1-2-auth": "backlog",
                "epic-1-retrospective": "optional",
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 1, "1-1-setup"), (1, 2, "1-2-auth")]

    def test_find_backlog_stories_skips_done(self):
        """Skips stories that are not backlog."""
        ss = SprintStatus(
            development_status={
                "epic-1": "in-progress",
                "1-1-setup": "done",
                "1-2-auth": "backlog",
                "1-3-api": "in-progress",
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 2, "1-2-auth")]

    def test_find_backlog_stories_skips_epic_entries(self):
        """Skips entries starting with 'epic-'."""
        ss = SprintStatus(
            development_status={
                "epic-1": "backlog",
                "1-1-setup": "backlog",
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 1, "1-1-setup")]

    def test_find_backlog_stories_skips_retrospective(self):
        """Skips entries containing 'retrospective'."""
        ss = SprintStatus(
            development_status={
                "1-1-setup": "backlog",
                "epic-1-retrospective": "backlog",
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 1, "1-1-setup")]

    def test_find_backlog_stories_empty(self):
        """Returns empty list when no backlog stories."""
        ss = SprintStatus(
            development_status={
                "1-1-setup": "done",
                "1-2-auth": "in-progress",
            }
        )
        result = ss.find_backlog_stories()
        assert result == []

    def test_find_backlog_stories_multi_epic(self):
        """Finds backlog stories across multiple epics."""
        ss = SprintStatus(
            development_status={
                "epic-1": "done",
                "1-1-setup": "done",
                "epic-2": "backlog",
                "2-1-api": "backlog",
                "2-2-ui": "backlog",
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(2, 1, "2-1-api"), (2, 2, "2-2-ui")]

    def test_find_next_backlog_story(self):
        """Returns the first backlog story."""
        ss = SprintStatus(
            development_status={
                "1-1-setup": "done",
                "1-2-auth": "backlog",
                "1-3-api": "backlog",
            }
        )
        result = ss.find_next_backlog_story()
        assert result == (1, 2, "1-2-auth")

    def test_find_next_backlog_story_none(self):
        """Returns None when no backlog stories."""
        ss = SprintStatus(
            development_status={
                "1-1-setup": "done",
            }
        )
        result = ss.find_next_backlog_story()
        assert result is None

    def test_find_backlog_stories_invalid_key_format(self):
        """Skips keys that don't match expected format."""
        ss = SprintStatus(
            development_status={
                "invalid-key": "backlog",
                "1-2-auth": "backlog",
                "abc": "backlog",
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 2, "1-2-auth")]

    def test_parse_story_key_no_title(self):
        """Parses keys with just epic-story numbers (no title)."""
        ss = SprintStatus(
            development_status={
                "1-2": "backlog",
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 2, "1-2")]


class TestSprintStatusIO:
    """Tests for YAML round-trip persistence."""

    def test_save_and_load_round_trip(self, tmp_path):
        """Save then load preserves all data."""
        ss = SprintStatus()
        ss.set_story_status("1.1", "done")
        ss.set_story_status("1.2", "in-progress")
        ss.set_epic_status(1, "in-progress")

        path = tmp_path / "sprint-status.yaml"
        save_sprint_status(ss, path)

        loaded = load_sprint_status(path)
        assert loaded.get_story_status("1.1") == "done"
        assert loaded.get_story_status("1.2") == "in-progress"
        assert loaded.get_epic_status(1) == "in-progress"

    def test_load_missing_file(self, tmp_path):
        """Loading from a missing file returns empty SprintStatus."""
        path = tmp_path / "missing.yaml"
        loaded = load_sprint_status(path)
        assert loaded.development_status == {}

    def test_load_empty_file(self, tmp_path):
        """Loading from an empty file returns empty SprintStatus."""
        path = tmp_path / "empty.yaml"
        path.write_text("")
        loaded = load_sprint_status(path)
        assert loaded.development_status == {}

    def test_load_corrupt_file(self, tmp_path):
        """Loading from a corrupt file returns empty SprintStatus."""
        path = tmp_path / "corrupt.yaml"
        path.write_text(": [\ninvalid yaml")
        loaded = load_sprint_status(path)
        assert loaded.development_status == {}


class TestRichDictFormat:
    """Tests for rich dict format entries (e.g., bmad-assist style)."""

    def test_rich_dict_get_story_status(self):
        """Extracts status from a dict entry."""
        ss = SprintStatus(
            development_status={
                "1-1-setup": {"epic": "lp-v2", "title": "Setup", "status": "done"},
            }
        )
        assert ss.get_story_status("1.1") is None  # no story- prefix match
        status = ss._extract_status(ss.development_status["1-1-setup"])
        assert status == "done"

    def test_rich_dict_find_backlog_stories(self):
        """Finds backlog stories from rich dict entries."""
        ss = SprintStatus(
            development_status={
                "epic-1": {"title": "Epic One", "status": "in-progress"},
                "1-1-setup": {"epic": 1, "status": "done", "title": "Setup"},
                "1-2-auth": {"epic": 1, "status": "backlog", "title": "Auth"},
                "1-3-api": {"epic": 1, "status": "in-progress", "title": "API"},
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 2, "1-2-auth")]

    def test_rich_dict_is_story_done(self):
        """is_story_done works with rich dict entries via _find_key."""
        ss = SprintStatus(
            development_status={
                "story-1-1": {"status": "done", "title": "Setup"},
                "story-1-2": {"status": "backlog", "title": "Auth"},
            }
        )
        assert ss.is_story_done("1.1") is True
        assert ss.is_story_done("1.2") is False

    def test_rich_dict_is_epic_done(self):
        """is_epic_done works with rich dict entries."""
        ss = SprintStatus(
            development_status={
                "epic-1": {"title": "Epic One", "status": "done"},
                "epic-2": {"title": "Epic Two", "status": "in-progress"},
            }
        )
        assert ss.is_epic_done(1) is True
        assert ss.is_epic_done(2) is False

    def test_rich_dict_set_story_preserves_dict(self):
        """Setting status on a rich dict entry updates status field, keeps other fields."""
        ss = SprintStatus(
            development_status={
                "story-1-1": {"status": "backlog", "title": "Setup", "points": 3},
            }
        )
        ss.set_story_status("1.1", "in-progress")
        entry = ss.development_status["story-1-1"]
        assert isinstance(entry, dict)
        assert entry["status"] == "in-progress"
        assert entry["title"] == "Setup"
        assert entry["points"] == 3

    def test_rich_dict_set_epic_preserves_dict(self):
        """Setting status on a rich dict epic entry preserves other fields."""
        ss = SprintStatus(
            development_status={
                "epic-1": {"title": "Epic One", "status": "backlog", "stories": 5},
            }
        )
        ss.set_epic_status(1, "done")
        entry = ss.development_status["epic-1"]
        assert isinstance(entry, dict)
        assert entry["status"] == "done"
        assert entry["title"] == "Epic One"
        assert entry["stories"] == 5

    def test_mixed_format(self):
        """Handles a mix of string and dict entries."""
        ss = SprintStatus(
            development_status={
                "epic-1": "in-progress",
                "1-1-setup": {"status": "done", "title": "Setup"},
                "1-2-auth": "backlog",
                "1-3-api": {"status": "backlog", "title": "API"},
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 2, "1-2-auth"), (1, 3, "1-3-api")]

    def test_rich_dict_missing_status_key(self):
        """Dict entry without a status key returns None."""
        ss = SprintStatus(
            development_status={
                "1-1-setup": {"title": "Setup", "points": 3},
            }
        )
        result = ss.find_backlog_stories()
        assert result == []

    def test_rich_dict_round_trip(self, tmp_path):
        """Save and load preserves rich dict entries."""
        ss = SprintStatus(
            development_status={
                "epic-1": {"title": "Epic One", "status": "done"},
                "1-1-setup": {"epic": 1, "status": "done", "title": "Setup"},
                "1-2-auth": "backlog",
            }
        )
        path = tmp_path / "sprint-status.yaml"
        save_sprint_status(ss, path)

        loaded = load_sprint_status(path)
        assert loaded._extract_status(loaded.development_status["epic-1"]) == "done"
        assert loaded._extract_status(loaded.development_status["1-1-setup"]) == "done"
        assert loaded._extract_status(loaded.development_status["1-2-auth"]) == "backlog"
        result = loaded.find_backlog_stories()
        assert result == [(1, 2, "1-2-auth")]


class TestSprintStatusPath:
    """Tests for path resolution."""

    def test_get_sprint_status_path(self, tmp_path):
        """Sprint status path resolves to implementation-artifacts/sprint-status.yaml."""
        path = get_sprint_status_path(tmp_path)
        assert path.name == "sprint-status.yaml"
        assert path.parent.name == "implementation-artifacts"
        assert path.parent.parent.name == "_bmad-output"
