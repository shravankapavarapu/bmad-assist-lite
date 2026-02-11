"""Tests for story status filtering at load time.

Tests the filtering logic used in cli.py to exclude done stories
from the loop based on markdown status and sprint-status.yaml.
"""

import pytest

from bmad_assist_lite.bmad.parser import EpicStory
from bmad_assist_lite.core.sprint_status import SprintStatus

# Done-status set matching cli.py
_DONE_STATUSES = {"done", "complete", "completed"}


def _filter_stories(
    stories: list[EpicStory],
    sprint_status: SprintStatus,
) -> list[str]:
    """Replicate the filtering logic from cli.py run command."""
    result: list[str] = []
    for s in stories:
        if s.status.lower() in _DONE_STATUSES:
            continue
        if sprint_status.is_story_done(s.number):
            continue
        result.append(s.number)
    return result


class TestStoryFiltering:
    """Tests for story status filtering."""

    def test_filter_done_by_markdown_status(self):
        """Stories with status 'done' in markdown are filtered out."""
        stories = [
            EpicStory(number="1.1", title="A", status="done"),
            EpicStory(number="1.2", title="B", status=""),
            EpicStory(number="1.3", title="C", status="complete"),
        ]
        ss = SprintStatus()
        result = _filter_stories(stories, ss)
        assert result == ["1.2"]

    def test_filter_done_by_sprint_status(self):
        """Stories marked done in sprint-status.yaml are filtered out."""
        stories = [
            EpicStory(number="1.1", title="A", status=""),
            EpicStory(number="1.2", title="B", status=""),
            EpicStory(number="1.3", title="C", status=""),
        ]
        ss = SprintStatus()
        ss.set_story_status("1.1", "done")
        result = _filter_stories(stories, ss)
        assert result == ["1.2", "1.3"]

    def test_filter_mixed(self):
        """Stories done by either source are filtered."""
        stories = [
            EpicStory(number="1.1", title="A", status="done"),
            EpicStory(number="1.2", title="B", status=""),
            EpicStory(number="1.3", title="C", status=""),
        ]
        ss = SprintStatus()
        ss.set_story_status("1.2", "completed")
        result = _filter_stories(stories, ss)
        assert result == ["1.3"]

    def test_filter_empty_sprint_status_keeps_all(self):
        """Empty sprint status keeps all non-done stories."""
        stories = [
            EpicStory(number="1.1", title="A", status=""),
            EpicStory(number="1.2", title="B", status=""),
        ]
        ss = SprintStatus()
        result = _filter_stories(stories, ss)
        assert result == ["1.1", "1.2"]

    def test_filter_case_insensitive(self):
        """Status comparison is case-insensitive."""
        stories = [
            EpicStory(number="1.1", title="A", status="Done"),
            EpicStory(number="1.2", title="B", status="COMPLETED"),
        ]
        ss = SprintStatus()
        result = _filter_stories(stories, ss)
        assert result == []
