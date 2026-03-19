"""Tests for parallel state persistence (Story 3.3).

Covers StoryStatus enum, StoryState / ParallelState frozen models,
atomic save/load round-trips, orphan temp-file cleanup, initial state
creation, and the get_parallel_state_path() utility.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.state import (
    ParallelState,
    StoryState,
    StoryStatus,
    _utc_now,
    create_initial_state,
    get_parallel_state_path,
    load_state,
    save_state,
)


# ============================================================================
# StoryStatus enum
# ============================================================================


class TestStoryStatus:
    """Test StoryStatus enum values and snake_case serialization."""

    def test_backlog_value(self) -> None:
        assert StoryStatus.BACKLOG.value == "backlog"

    def test_in_flight_value(self) -> None:
        assert StoryStatus.IN_FLIGHT.value == "in_flight"

    def test_merging_value(self) -> None:
        assert StoryStatus.MERGING.value == "merging"

    def test_done_value(self) -> None:
        assert StoryStatus.DONE.value == "done"

    def test_blocked_value(self) -> None:
        assert StoryStatus.BLOCKED.value == "blocked"

    def test_all_statuses_snake_case(self) -> None:
        for status in StoryStatus:
            assert "_" not in status.value or status.value == "in_flight"
            assert status.value == status.value.lower()


# ============================================================================
# StoryState frozen model
# ============================================================================


class TestStoryState:
    """Test StoryState frozen Pydantic model (Tasks 2, 10.2)."""

    def test_default_values(self) -> None:
        state = StoryState()
        assert state.status == StoryStatus.BACKLOG
        assert state.worktree_path is None
        assert state.started_at is None
        assert state.completed_at is None
        assert state.error is None

    def test_custom_values(self) -> None:
        now = _utc_now()
        state = StoryState(
            status=StoryStatus.IN_FLIGHT,
            worktree_path=Path("/tmp/wt"),
            started_at=now,
        )
        assert state.status == StoryStatus.IN_FLIGHT
        assert state.worktree_path == Path("/tmp/wt")
        assert state.started_at == now

    def test_frozen_raises_on_direct_mutation(self) -> None:
        state = StoryState()
        with pytest.raises(ValidationError):
            state.status = StoryStatus.IN_FLIGHT  # type: ignore[misc]

    def test_frozen_raises_on_worktree_path_mutation(self) -> None:
        state = StoryState()
        with pytest.raises(ValidationError):
            state.worktree_path = Path("/new")  # type: ignore[misc]

    def test_frozen_raises_on_error_mutation(self) -> None:
        state = StoryState()
        with pytest.raises(ValidationError):
            state.error = "fail"  # type: ignore[misc]

    def test_model_copy_produces_new_instance(self) -> None:
        original = StoryState()
        updated = original.model_copy(update={"status": StoryStatus.IN_FLIGHT})
        assert updated.status == StoryStatus.IN_FLIGHT
        assert original.status == StoryStatus.BACKLOG

    def test_naive_utc_timestamps(self) -> None:
        now = _utc_now()
        state = StoryState(started_at=now)
        assert state.started_at is not None
        assert state.started_at.tzinfo is None


# ============================================================================
# ParallelState frozen model
# ============================================================================


class TestParallelState:
    """Test ParallelState frozen model (Tasks 1, 3, 10.1)."""

    def _make_state(self) -> ParallelState:
        """Create a minimal valid ParallelState for testing."""
        return ParallelState(
            base_branch="main",
            epic=3,
            started_at=_utc_now(),
            stories={
                "3.1": StoryState(),
                "3.2": StoryState(),
            },
        )

    def test_creation(self) -> None:
        state = self._make_state()
        assert state.base_branch == "main"
        assert state.epic == 3
        assert state.started_at.tzinfo is None
        assert len(state.stories) == 2

    def test_frozen_raises_on_mutation(self) -> None:
        state = self._make_state()
        with pytest.raises(ValidationError):
            state.base_branch = "develop"  # type: ignore[misc]

    def test_frozen_raises_on_epic_mutation(self) -> None:
        state = self._make_state()
        with pytest.raises(ValidationError):
            state.epic = 5  # type: ignore[misc]


# ============================================================================
# with_story_status transitions
# ============================================================================


class TestWithStoryStatus:
    """Test ParallelState.with_story_status() transitions (Task 10.10)."""

    def _make_state(self) -> ParallelState:
        return ParallelState(
            base_branch="main",
            epic=3,
            started_at=_utc_now(),
            stories={
                "3.1": StoryState(),
                "3.2": StoryState(),
            },
        )

    def test_returns_new_instance(self) -> None:
        original = self._make_state()
        updated = original.with_story_status("3.1", StoryStatus.IN_FLIGHT)
        assert updated is not original
        assert updated.stories["3.1"].status == StoryStatus.IN_FLIGHT
        assert original.stories["3.1"].status == StoryStatus.BACKLOG

    def test_original_stories_dict_not_mutated(self) -> None:
        original = self._make_state()
        original_stories_id = id(original.stories)
        updated = original.with_story_status("3.1", StoryStatus.IN_FLIGHT)
        assert id(updated.stories) != original_stories_id
        assert original.stories["3.1"].status == StoryStatus.BACKLOG

    def test_other_stories_preserved(self) -> None:
        original = self._make_state()
        updated = original.with_story_status("3.1", StoryStatus.IN_FLIGHT)
        assert updated.stories["3.2"].status == StoryStatus.BACKLOG

    def test_with_additional_kwargs(self) -> None:
        original = self._make_state()
        now = _utc_now()
        updated = original.with_story_status(
            "3.1",
            StoryStatus.IN_FLIGHT,
            started_at=now,
            worktree_path=Path("/tmp/wt"),
        )
        assert updated.stories["3.1"].started_at == now
        assert updated.stories["3.1"].worktree_path == Path("/tmp/wt")

    def test_unknown_story_id_raises_key_error(self) -> None:
        state = self._make_state()
        with pytest.raises(KeyError, match="9.9"):
            state.with_story_status("9.9", StoryStatus.IN_FLIGHT)

    def test_backlog_transition_clears_stale_fields(self) -> None:
        state = self._make_state()
        # First make it blocked with error
        blocked = state.with_story_status(
            "3.1",
            StoryStatus.BLOCKED,
            error="some error",
            worktree_path=Path("/tmp/wt"),
            completed_at=_utc_now(),
            started_at=_utc_now(),
        )
        assert blocked.stories["3.1"].error == "some error"
        assert blocked.stories["3.1"].worktree_path is not None
        assert blocked.stories["3.1"].completed_at is not None
        assert blocked.stories["3.1"].started_at is not None

        # Transition back to backlog — stale fields should be cleared
        retried = blocked.with_story_status("3.1", StoryStatus.BACKLOG)
        assert retried.stories["3.1"].error is None
        assert retried.stories["3.1"].worktree_path is None
        assert retried.stories["3.1"].completed_at is None
        assert retried.stories["3.1"].started_at is None

    def test_backlog_transition_allows_kwargs_override(self) -> None:
        state = self._make_state()
        blocked = state.with_story_status(
            "3.1", StoryStatus.BLOCKED, error="some error",
        )
        retried = blocked.with_story_status(
            "3.1", StoryStatus.BACKLOG, error="kept error",
        )
        assert retried.stories["3.1"].error == "kept error"

    def test_chained_transitions(self) -> None:
        state = self._make_state()
        s2 = state.with_story_status("3.1", StoryStatus.IN_FLIGHT)
        s3 = s2.with_story_status("3.1", StoryStatus.MERGING)
        s4 = s3.with_story_status("3.1", StoryStatus.DONE)
        assert s4.stories["3.1"].status == StoryStatus.DONE
        # All intermediaries unchanged
        assert state.stories["3.1"].status == StoryStatus.BACKLOG
        assert s2.stories["3.1"].status == StoryStatus.IN_FLIGHT
        assert s3.stories["3.1"].status == StoryStatus.MERGING

    def test_base_branch_preserved_after_transition(self) -> None:
        state = self._make_state()
        updated = state.with_story_status("3.1", StoryStatus.IN_FLIGHT)
        assert updated.base_branch == state.base_branch
        assert updated.epic == state.epic
        assert updated.started_at == state.started_at


# ============================================================================
# save_state / load_state round-trip
# ============================================================================


class TestSaveLoadRoundTrip:
    """Test save_state() and load_state() round-trip (Tasks 10.3, 10.4)."""

    def test_round_trip_produces_identical_state(self, tmp_path: Path) -> None:
        state = create_initial_state("main", 3, ["3.1", "3.2", "3.3"])
        path = tmp_path / "parallel-state.yaml"

        save_state(state, path)
        loaded = load_state(path)

        assert loaded is not None
        assert loaded == state

    def test_round_trip_with_complex_state(self, tmp_path: Path) -> None:
        now = _utc_now()
        state = ParallelState(
            base_branch="develop",
            epic=5,
            started_at=now,
            stories={
                "5.1": StoryState(
                    status=StoryStatus.DONE,
                    worktree_path=Path("/tmp/wt-5-1"),
                    started_at=now,
                    completed_at=now,
                ),
                "5.2": StoryState(
                    status=StoryStatus.BLOCKED,
                    error="merge conflict",
                ),
                "5.3": StoryState(status=StoryStatus.IN_FLIGHT, started_at=now),
            },
        )
        path = tmp_path / "parallel-state.yaml"

        save_state(state, path)
        loaded = load_state(path)

        assert loaded is not None
        assert loaded == state
        assert loaded.stories["5.1"].status == StoryStatus.DONE
        assert loaded.stories["5.2"].error == "merge conflict"
        assert loaded.stories["5.3"].status == StoryStatus.IN_FLIGHT

    def test_save_creates_parent_directories(self, tmp_path: Path) -> None:
        state = create_initial_state("main", 1, ["1.1"])
        path = tmp_path / "sub" / "dir" / "parallel-state.yaml"

        save_state(state, path)

        assert path.exists()
        loaded = load_state(path)
        assert loaded is not None
        assert loaded == state

    def test_save_writes_valid_yaml(self, tmp_path: Path) -> None:
        state = create_initial_state("main", 3, ["3.1"])
        path = tmp_path / "parallel-state.yaml"

        save_state(state, path)

        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert isinstance(data, dict)
        assert data["base_branch"] == "main"
        assert data["epic"] == 3
        assert "3.1" in data["stories"]


# ============================================================================
# save_state atomic write safety
# ============================================================================


class TestSaveStateAtomicWrite:
    """Test save_state() atomic write behavior (Task 10.4)."""

    def test_no_partial_write_on_error(self, tmp_path: Path) -> None:
        state = create_initial_state("main", 3, ["3.1"])
        path = tmp_path / "parallel-state.yaml"

        # Simulate error during os.replace by making it raise OSError
        with patch("bmad_assist_lite.parallel.state.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(ParallelError, match="Failed to save"):
                save_state(state, path)

        # No partial YAML file should remain
        assert not path.exists()

    def test_temp_file_cleaned_on_error(self, tmp_path: Path) -> None:
        state = create_initial_state("main", 3, ["3.1"])
        path = tmp_path / "parallel-state.yaml"
        temp_path = path.with_suffix(path.suffix + ".tmp")

        with patch("bmad_assist_lite.parallel.state.os.replace", side_effect=OSError("fail")):
            with pytest.raises(ParallelError):
                save_state(state, path)

        assert not temp_path.exists()


# ============================================================================
# load_state — missing file
# ============================================================================


class TestLoadStateMissing:
    """Test load_state() with missing file (Task 10.5)."""

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.yaml"
        result = load_state(path)
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        result = load_state(path)
        assert result is None

    def test_returns_none_for_whitespace_only_file(self, tmp_path: Path) -> None:
        path = tmp_path / "whitespace.yaml"
        path.write_text("   \n  \n  ", encoding="utf-8")
        result = load_state(path)
        assert result is None


# ============================================================================
# load_state — orphaned temp file cleanup
# ============================================================================


class TestLoadStateOrphanCleanup:
    """Test load_state() cleans up orphaned .tmp files (Task 10.6)."""

    def test_removes_orphaned_tmp_file(self, tmp_path: Path) -> None:
        path = tmp_path / "parallel-state.yaml"
        temp_path = path.with_suffix(path.suffix + ".tmp")

        # Create orphaned temp file
        temp_path.write_text("stale data", encoding="utf-8")

        # No main file exists, so load returns None
        result = load_state(path)
        assert result is None
        assert not temp_path.exists()

    def test_removes_orphaned_tmp_file_before_loading_state(self, tmp_path: Path) -> None:
        state = create_initial_state("main", 3, ["3.1"])
        path = tmp_path / "parallel-state.yaml"
        temp_path = path.with_suffix(path.suffix + ".tmp")

        # Save valid state, then create orphan temp file
        save_state(state, path)
        temp_path.write_text("orphaned data", encoding="utf-8")

        # Load should clean up temp and return valid state
        loaded = load_state(path)
        assert loaded is not None
        assert loaded == state
        assert not temp_path.exists()


# ============================================================================
# load_state — corrupt YAML
# ============================================================================


class TestLoadStateCorruptYaml:
    """Test load_state() raises ParallelError on corrupt YAML (Task 10.7)."""

    def test_corrupt_yaml_raises_parallel_error(self, tmp_path: Path) -> None:
        path = tmp_path / "parallel-state.yaml"
        path.write_text("{ invalid yaml: [", encoding="utf-8")

        with pytest.raises(ParallelError, match="invalid YAML"):
            load_state(path)

    def test_non_dict_yaml_raises_parallel_error(self, tmp_path: Path) -> None:
        path = tmp_path / "parallel-state.yaml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")

        with pytest.raises(ParallelError, match="expected dict"):
            load_state(path)

    def test_binary_file_raises_parallel_error(self, tmp_path: Path) -> None:
        path = tmp_path / "parallel-state.yaml"
        path.write_bytes(b"\x80\x81\x82\xff\xfe")

        with pytest.raises(ParallelError, match="not valid UTF-8"):
            load_state(path)


# ============================================================================
# load_state — invalid schema
# ============================================================================


class TestLoadStateInvalidSchema:
    """Test load_state() raises ParallelError on wrong schema (Task 10.8)."""

    def test_missing_required_fields_raises_parallel_error(self, tmp_path: Path) -> None:
        path = tmp_path / "parallel-state.yaml"
        path.write_text("base_branch: main\n", encoding="utf-8")

        with pytest.raises(ParallelError, match="validation failed"):
            load_state(path)

    def test_wrong_field_types_raises_parallel_error(self, tmp_path: Path) -> None:
        path = tmp_path / "parallel-state.yaml"
        data = {
            "base_branch": "main",
            "epic": "not_an_int",
            "started_at": "not-a-date",
            "stories": {},
        }
        path.write_text(yaml.dump(data), encoding="utf-8")

        with pytest.raises(ParallelError, match="validation failed"):
            load_state(path)

    def test_invalid_story_status_raises_parallel_error(self, tmp_path: Path) -> None:
        path = tmp_path / "parallel-state.yaml"
        data = {
            "base_branch": "main",
            "epic": 3,
            "started_at": _utc_now().isoformat(),
            "stories": {
                "3.1": {"status": "invalid_status"},
            },
        }
        path.write_text(yaml.dump(data), encoding="utf-8")

        with pytest.raises(ParallelError, match="validation failed"):
            load_state(path)


# ============================================================================
# create_initial_state
# ============================================================================


class TestCreateInitialState:
    """Test create_initial_state() helper (Task 10.9)."""

    def test_all_stories_start_as_backlog(self) -> None:
        state = create_initial_state("main", 3, ["3.1", "3.2", "3.3"])
        assert len(state.stories) == 3
        for story_state in state.stories.values():
            assert story_state.status == StoryStatus.BACKLOG

    def test_metadata_set_correctly(self) -> None:
        state = create_initial_state("develop", 5, ["5.1"])
        assert state.base_branch == "develop"
        assert state.epic == 5
        assert state.started_at is not None
        assert state.started_at.tzinfo is None

    def test_empty_story_list(self) -> None:
        state = create_initial_state("main", 1, [])
        assert len(state.stories) == 0
        assert state.base_branch == "main"

    def test_story_ids_preserved(self) -> None:
        ids = ["3.1", "3.2", "3.10"]
        state = create_initial_state("main", 3, ids)
        assert set(state.stories.keys()) == set(ids)

    def test_started_at_is_naive_utc(self) -> None:
        state = create_initial_state("main", 1, ["1.1"])
        assert state.started_at.tzinfo is None


# ============================================================================
# get_parallel_state_path
# ============================================================================


class TestGetParallelStatePath:
    """Test get_parallel_state_path() utility (Task 10.11)."""

    def test_returns_expected_path(self, tmp_path: Path) -> None:
        result = get_parallel_state_path(tmp_path)
        expected = (tmp_path / ".bmad-assist-lite" / "parallel-state.yaml").resolve()
        assert result == expected

    def test_returns_resolved_path(self, tmp_path: Path) -> None:
        result = get_parallel_state_path(tmp_path)
        assert result.is_absolute()

    def test_default_uses_cwd(self) -> None:
        result = get_parallel_state_path()
        expected = (Path.cwd() / ".bmad-assist-lite" / "parallel-state.yaml").resolve()
        assert result == expected


# ============================================================================
# _utc_now helper
# ============================================================================


class TestUtcNow:
    """Test the _utc_now helper produces naive UTC datetimes."""

    def test_returns_naive_datetime(self) -> None:
        now = _utc_now()
        assert isinstance(now, datetime)
        assert now.tzinfo is None

    def test_is_close_to_actual_utc(self) -> None:
        now = _utc_now()
        actual = datetime.now(timezone.utc).replace(tzinfo=None)
        delta = abs((actual - now).total_seconds())
        assert delta < 2  # within 2 seconds
