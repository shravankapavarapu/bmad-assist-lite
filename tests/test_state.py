"""Tests for bmad_assist_lite.core.state."""

import pytest

from bmad_assist_lite.core.exceptions import StateError
from bmad_assist_lite.core.state import (
    Phase,
    State,
    advance_state,
    get_state_path,
    load_state,
    mark_story_completed,
    save_state,
    update_position,
)


# ============================================================================
# State model defaults
# ============================================================================


class TestStateDefaults:
    """Tests for fresh State() construction."""

    def test_fresh_state(self):
        """A default State() has all None/empty defaults."""
        s = State()
        assert s.current_epic is None
        assert s.current_story is None
        assert s.current_phase is None
        assert s.completed_stories == []
        assert s.completed_epics == []
        assert s.failed_qa_stories == []
        assert s.qa_retry_count == 0
        assert s.started_at is None
        assert s.updated_at is None
        assert s.story_started_at is None
        assert s.phase_started_at is None


# ============================================================================
# Phase enum
# ============================================================================


class TestPhaseEnum:
    """Tests for the Phase enum."""

    def test_phase_enum_values(self):
        """Phase enum has exactly 10 members with expected string values."""
        assert len(Phase) == 10
        expected = {
            "CREATE_STORY": "create_story",
            "VALIDATE_STORY": "validate_story",
            "VALIDATE_STORY_SYNTHESIS": "validate_story_synthesis",
            "DEV_STORY": "dev_story",
            "CODE_REVIEW": "code_review",
            "CODE_REVIEW_SYNTHESIS": "code_review_synthesis",
            "QUALITY_GATE": "quality_gate",
            "FIX_QUALITY_GATE": "fix_quality_gate",
            "EPIC_QUALITY_GATE": "epic_quality_gate",
            "RETROSPECTIVE": "retrospective",
        }
        for name, value in expected.items():
            assert Phase[name].value == value


# ============================================================================
# save / load round-trip
# ============================================================================


class TestSaveAndLoad:
    """Tests for save_state and load_state persistence."""

    def test_save_and_load_state(self, tmp_path):
        """Round-trip: save then load preserves all fields."""
        state_file = tmp_path / "state.yaml"
        original = State(
            current_epic=1,
            current_story="story-1",
            current_phase=Phase.DEV_STORY,
            completed_stories=["story-0"],
            completed_epics=[],
        )
        save_state(original, state_file)
        loaded = load_state(state_file)

        assert loaded.current_epic == original.current_epic
        assert loaded.current_story == original.current_story
        assert loaded.current_phase == original.current_phase
        assert loaded.completed_stories == original.completed_stories
        assert loaded.completed_epics == original.completed_epics

    def test_load_missing_file(self, tmp_path):
        """Loading from a nonexistent file returns a fresh State()."""
        state_file = tmp_path / "nonexistent" / "state.yaml"
        s = load_state(state_file)
        assert s.current_epic is None
        assert s.current_story is None
        assert s.current_phase is None
        assert s.completed_stories == []

    def test_load_empty_file(self, tmp_path):
        """Loading from an empty file returns a fresh State()."""
        state_file = tmp_path / "state.yaml"
        state_file.write_text("", encoding="utf-8")
        s = load_state(state_file)
        assert s.current_epic is None
        assert s.completed_stories == []

    def test_load_corrupted_yaml(self, tmp_path):
        """Loading a file with invalid YAML raises StateError."""
        state_file = tmp_path / "state.yaml"
        state_file.write_text(":\n  :\n- :\n{{{bad", encoding="utf-8")
        with pytest.raises(StateError, match="corrupted"):
            load_state(state_file)


# ============================================================================
# update_position
# ============================================================================


class TestUpdatePosition:
    """Tests for update_position."""

    def test_update_position(self):
        """update_position sets epic, story, and phase on the state."""
        s = State()
        update_position(s, epic=2, story="story-5", phase=Phase.CODE_REVIEW)
        assert s.current_epic == 2
        assert s.current_story == "story-5"
        assert s.current_phase == Phase.CODE_REVIEW
        assert s.started_at is not None
        assert s.updated_at is not None

    def test_update_position_partial(self):
        """update_position with only some kwargs updates only those fields."""
        s = State()
        update_position(s, epic=1)
        assert s.current_epic == 1
        assert s.current_story is None
        assert s.current_phase is None


# ============================================================================
# advance_state
# ============================================================================


class TestAdvanceState:
    """Tests for advance_state."""

    STORY_PHASES = [
        "create_story",
        "validate_story",
        "validate_story_synthesis",
        "dev_story",
        "code_review",
        "code_review_synthesis",
    ]

    def test_advance_state(self):
        """advance_state moves to the next phase in the list."""
        s = State(current_phase=Phase.CREATE_STORY)
        result = advance_state(s, self.STORY_PHASES)
        assert result["transitioned"] is True
        assert result["epic_complete"] is False
        assert result["previous_phase"] == Phase.CREATE_STORY
        assert result["new_phase"] == Phase.VALIDATE_STORY
        assert s.current_phase == Phase.VALIDATE_STORY

    def test_advance_state_at_end(self):
        """advance_state returns epic_complete=True when at the last phase."""
        s = State(current_phase=Phase.CODE_REVIEW_SYNTHESIS)
        result = advance_state(s, self.STORY_PHASES)
        assert result["transitioned"] is False
        assert result["epic_complete"] is True
        # Phase should remain unchanged
        assert s.current_phase == Phase.CODE_REVIEW_SYNTHESIS

    def test_advance_state_no_phase_raises(self):
        """advance_state raises StateError when current_phase is None."""
        s = State()
        with pytest.raises(StateError, match="no current phase"):
            advance_state(s, self.STORY_PHASES)


# ============================================================================
# mark_story_completed
# ============================================================================


class TestMarkStoryCompleted:
    """Tests for mark_story_completed."""

    def test_mark_story_completed(self):
        """Marking a story completed adds it to completed_stories."""
        s = State(current_story="story-3")
        mark_story_completed(s)
        assert "story-3" in s.completed_stories
        assert s.updated_at is not None

    def test_mark_story_completed_idempotent(self):
        """Calling mark_story_completed twice does not duplicate the entry."""
        s = State(current_story="story-3")
        mark_story_completed(s)
        mark_story_completed(s)
        assert s.completed_stories.count("story-3") == 1

    def test_mark_story_completed_no_story_raises(self):
        """mark_story_completed raises StateError when no current story is set."""
        s = State()
        with pytest.raises(StateError, match="no current story"):
            mark_story_completed(s)


# ============================================================================
# with_phase / with_story immutable copies
# ============================================================================


class TestImmutableCopies:
    """Tests for State.with_phase() and State.with_story()."""

    def test_with_phase_returns_copy(self):
        """with_phase() returns a new State; original is unchanged."""
        original = State(current_phase=Phase.CREATE_STORY)
        new_state = original.with_phase(Phase.DEV_STORY)

        assert new_state is not original
        assert new_state.current_phase == Phase.DEV_STORY
        assert original.current_phase == Phase.CREATE_STORY
        assert new_state.updated_at is not None

    def test_with_story_returns_copy(self):
        """with_story() returns a new State; original is unchanged."""
        original = State(current_story="story-1")
        new_state = original.with_story("story-2")

        assert new_state is not original
        assert new_state.current_story == "story-2"
        assert original.current_story == "story-1"
        assert new_state.updated_at is not None


# ============================================================================
# get_state_path
# ============================================================================


class TestGetStatePath:
    """Tests for get_state_path."""

    def test_get_state_path_with_root(self, tmp_path):
        """get_state_path resolves under the given project root."""
        result = get_state_path(tmp_path)
        assert result == (tmp_path / ".bmad-assist-lite" / "state.yaml").resolve()

    def test_get_state_path_without_root(self):
        """get_state_path without args uses cwd."""
        from pathlib import Path

        result = get_state_path()
        assert result == (Path.cwd() / ".bmad-assist-lite" / "state.yaml").resolve()
