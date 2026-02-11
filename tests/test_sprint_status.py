"""Tests for sprint status model, YAML I/O, and key resolution."""

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


class TestSprintStatusPath:
    """Tests for path resolution."""

    def test_get_sprint_status_path(self, tmp_path):
        """Sprint status path resolves to .bmad-assist-lite/sprint-status.yaml."""
        path = get_sprint_status_path(tmp_path)
        assert path.name == "sprint-status.yaml"
        assert path.parent.name == ".bmad-assist-lite"
