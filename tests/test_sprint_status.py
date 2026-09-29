"""Tests for sprint status model, YAML I/O, key resolution, and backlog discovery."""

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

    def test_bare_key_resolution(self):
        """Finds 6-1-blog-data-layer without story- prefix."""
        ss = SprintStatus(
            development_status={"6-1-blog-data-layer-unit-tests": "backlog"}
        )
        assert ss.get_story_status("6.1") == "backlog"

    def test_bare_key_set_updates_existing(self):
        """Setting status on bare key updates existing entry, not creates new."""
        ss = SprintStatus(
            development_status={"6-1-blog-data-layer": "backlog"}
        )
        ss.set_story_status("6.1", "in-progress")
        assert "6-1-blog-data-layer" in ss.development_status
        assert ss.development_status["6-1-blog-data-layer"] == "in-progress"
        # Should NOT create a story-6-1 key
        assert "story-6-1" not in ss.development_status

    def test_bare_key_no_false_match(self):
        """6-1 prefix doesn't falsely match 6-10-something."""
        ss = SprintStatus(
            development_status={
                "6-10-other-story": "backlog",
                "6-1-blog-data": "done",
            }
        )
        assert ss.get_story_status("6.1") == "done"
        assert ss.get_story_status("6.10") == "backlog"


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

    def test_find_backlog_stories_skips_inactive(self):
        """Skips done/blocked/deferred stories, includes active ones."""
        ss = SprintStatus(
            development_status={
                "epic-1": "in-progress",
                "1-1-setup": "done",
                "1-2-auth": "backlog",
                "1-3-api": "in-progress",
                "1-4-blocked": "blocked",
                "1-5-deferred": "deferred",
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 2, "1-2-auth"), (1, 3, "1-3-api")]

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
        """Returns empty list when all stories are inactive."""
        ss = SprintStatus(
            development_status={
                "1-1-setup": "done",
                "1-2-auth": "blocked",
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
        """Extracts status from a dict entry via bare key match."""
        ss = SprintStatus(
            development_status={
                "1-1-setup": {"epic": "lp-v2", "title": "Setup", "status": "done"},
            }
        )
        # Now matches bare key format 1-1-* for story_id 1.1
        assert ss.get_story_status("1.1") == "done"
        status = ss._extract_status(ss.development_status["1-1-setup"])
        assert status == "done"

    def test_rich_dict_find_backlog_stories(self):
        """Finds actionable stories from rich dict entries."""
        ss = SprintStatus(
            development_status={
                "epic-1": {"title": "Epic One", "status": "in-progress"},
                "1-1-setup": {"epic": 1, "status": "done", "title": "Setup"},
                "1-2-auth": {"epic": 1, "status": "backlog", "title": "Auth"},
                "1-3-api": {"epic": 1, "status": "in-progress", "title": "API"},
                "1-4-blocked": {"epic": 1, "status": "blocked", "title": "Blocked"},
            }
        )
        result = ss.find_backlog_stories()
        assert result == [(1, 2, "1-2-auth"), (1, 3, "1-3-api")]

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

    def test_extra_fields_preserved_on_round_trip(self, tmp_path):
        """Extra top-level fields (project, totals, etc.) survive load/save."""
        path = tmp_path / "sprint-status.yaml"
        path.write_text(
            'generated: "2026-02-09"\n'
            'project: "webdozo-v1"\n'
            "totals:\n"
            "  epics: 3\n"
            "  stories: 10\n"
            "current_sprint:\n"
            '  name: "Sprint 1"\n'
            "development_status:\n"
            "  1-1-setup: backlog\n"
        )

        loaded = load_sprint_status(path)
        loaded.set_story_status("1.1", "in-progress")
        save_sprint_status(loaded, path)

        # Re-read raw YAML to verify extra fields preserved

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["project"] == "webdozo-v1"
        assert raw["totals"]["epics"] == 3
        assert raw["totals"]["stories"] == 10
        assert raw["current_sprint"]["name"] == "Sprint 1"

    def test_generated_stays_date_format(self, tmp_path):
        """Generated field stays as YYYY-MM-DD, not ISO datetime."""
        path = tmp_path / "sprint-status.yaml"
        path.write_text(
            'generated: "2026-02-09"\n'
            "development_status:\n"
            "  1-1-setup: backlog\n"
        )

        loaded = load_sprint_status(path)
        loaded.set_story_status("1.1", "done")
        save_sprint_status(loaded, path)

        content = path.read_text(encoding="utf-8")
        # Should be a date string, not ISO datetime with time component
        import re

        match = re.search(r"generated: ['\"]?(\d{4}-\d{2}-\d{2})['\"]?", content)
        assert match is not None, f"Generated should be date format, got: {content[:100]}"


class TestSurgicalSave:
    """Tests for surgical YAML save preserving comments and formatting."""

    def test_comments_preserved(self, tmp_path):
        """Save preserves YAML comments."""
        path = tmp_path / "sprint-status.yaml"
        original = (
            "# Sprint Status Tracking\n"
            'generated: "2026-02-09"\n'
            "# Project info\n"
            'project: "webdozo-v1"\n'
            "\n"
            "development_status:\n"
            "  # Epic 1 stories\n"
            "  1-1-setup: backlog\n"
        )
        path.write_text(original)

        loaded = load_sprint_status(path)
        loaded.set_story_status("1.1", "in-progress")
        save_sprint_status(loaded, path)

        content = path.read_text(encoding="utf-8")
        assert "# Sprint Status Tracking" in content
        assert "# Project info" in content
        assert "# Epic 1 stories" in content

    def test_quoting_style_preserved(self, tmp_path):
        """Save preserves double-quoted strings."""
        path = tmp_path / "sprint-status.yaml"
        original = (
            'generated: "2026-02-09"\n'
            'project: "webdozo-v1"\n'
            "development_status:\n"
            "  1-1-setup: backlog\n"
        )
        path.write_text(original)

        loaded = load_sprint_status(path)
        loaded.set_story_status("1.1", "done")
        save_sprint_status(loaded, path)

        content = path.read_text(encoding="utf-8")
        # project should keep its double-quoting
        assert 'project: "webdozo-v1"' in content
        # status should be updated
        assert "1-1-setup: done" in content

    def test_rich_dict_status_updated(self, tmp_path):
        """Save updates status inside rich dict entries."""
        path = tmp_path / "sprint-status.yaml"
        original = (
            'generated: "2026-02-09"\n'
            "development_status:\n"
            "  epic-lp-v2:\n"
            '    title: "Landing Page V2"\n'
            "    status: in-progress\n"
            "    points: 36\n"
        )
        path.write_text(original)

        loaded = load_sprint_status(path)
        loaded.set_epic_status("lp-v2", "done")
        save_sprint_status(loaded, path)

        content = path.read_text(encoding="utf-8")
        assert "status: done" in content
        assert 'title: "Landing Page V2"' in content
        assert "points: 36" in content

    def test_section_separators_preserved(self, tmp_path):
        """Save preserves section separator comments."""
        path = tmp_path / "sprint-status.yaml"
        original = (
            'generated: "2026-02-09"\n'
            "\n"
            "# ============================================================\n"
            "# CURRENT STATE: Between Sprints\n"
            "# ============================================================\n"
            "\n"
            "development_status:\n"
            "  1-1-setup: backlog\n"
        )
        path.write_text(original)

        loaded = load_sprint_status(path)
        loaded.set_story_status("1.1", "done")
        save_sprint_status(loaded, path)

        content = path.read_text(encoding="utf-8")
        assert "# ============" in content
        assert "# CURRENT STATE: Between Sprints" in content

    def test_no_duplicate_keys_on_bare_format(self, tmp_path):
        """Save with bare key format doesn't create duplicate story- keys."""
        path = tmp_path / "sprint-status.yaml"
        original = (
            'generated: "2026-02-09"\n'
            "development_status:\n"
            "  6-1-blog-data-layer: backlog\n"
            "  6-2-blog-ui: backlog\n"
        )
        path.write_text(original)

        loaded = load_sprint_status(path)
        loaded.set_story_status("6.1", "ready-for-dev")
        save_sprint_status(loaded, path)

        content = path.read_text(encoding="utf-8")
        assert "6-1-blog-data-layer: ready-for-dev" in content
        assert "story-6-1" not in content
        assert "6-2-blog-ui: backlog" in content


class TestSprintStatusPath:
    """Tests for path resolution."""

    def test_get_sprint_status_path(self, tmp_path):
        """Sprint status path resolves to implementation-artifacts/sprint-status.yaml."""
        path = get_sprint_status_path(tmp_path)
        assert path.name == "sprint-status.yaml"
        assert path.parent.name == "implementation-artifacts"
        assert path.parent.parent.name == "_bmad-output"


class TestParkedStatuses:
    """Review-owns-done (2026-09-05): `review` and `decision` park a story."""

    def test_decision_is_a_valid_status(self):
        from bmad_assist_lite.core.sprint_status import VALID_STATUSES

        assert "decision" in VALID_STATUSES

    def test_review_and_decision_are_inactive(self):
        from bmad_assist_lite.core.sprint_status import INACTIVE_STATUSES

        assert "review" in INACTIVE_STATUSES
        assert "decision" in INACTIVE_STATUSES

    def test_backlog_discovery_skips_parked_rows(self):
        """A `review` row waits for the out-of-band review pass and a
        `decision` row waits on the operator — neither is re-picked."""
        ss = SprintStatus(
            development_status={
                "3-1-parked": "review",
                "3-2-waiting-on-operator": "decision",
                "3-3-workable": "backlog",
            }
        )
        stories = ss.find_backlog_stories()
        assert stories == [(3, 3, "3-3-workable")]

    def test_find_story_key_is_public(self):
        ss = SprintStatus(development_status={"3-1-parked": "review"})
        assert ss.find_story_key("3.1") == "3-1-parked"
        assert ss.find_story_key("9.9") is None
