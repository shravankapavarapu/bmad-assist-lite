"""Test the parallel unblock CLI command."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from typer.testing import CliRunner

from bmad_assist_lite.cli import app
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.state import (
    ParallelState,
    StoryState,
    StoryStatus,
    create_initial_state,
)

if TYPE_CHECKING:
    from click.testing import Result

runner = CliRunner()

# ============================================================================
# Shared helpers
# ============================================================================

_LOAD_STATE_PATCH = "bmad_assist_lite.parallel.state.load_state"
_SAVE_STATE_PATCH = "bmad_assist_lite.parallel.state.save_state"
_GET_STATE_PATH_PATCH = "bmad_assist_lite.parallel.state.get_parallel_state_path"

_FIXED_NOW = datetime(2026, 3, 21, 12, 0, 0)


def _make_state(
    stories: dict[str, StoryState] | None = None,
    base_branch: str = "epic/3",
    epic: int = 3,
) -> ParallelState:
    """Create a ParallelState for testing."""
    if stories is None:
        stories = {"3.1": StoryState(), "3.2": StoryState()}
    return ParallelState(
        base_branch=base_branch,
        epic=epic,
        started_at=_FIXED_NOW - timedelta(hours=1),
        stories=stories,
    )


def _make_blocked_state(
    story_id: str = "3.2",
    error: str | None = "Quality gate failed",
) -> ParallelState:
    """Create a ParallelState with one blocked story."""
    state = create_initial_state(
        base_branch="epic/3",
        epic=3,
        story_ids=["3.1", "3.2", "3.3"],
    )
    return state.with_story_status(
        story_id,
        StoryStatus.BLOCKED,
        error=error,
        started_at=_FIXED_NOW - timedelta(minutes=30),
        completed_at=_FIXED_NOW,
    )


def _invoke_unblock(tmp_path: Path, story_id: str = "3.2") -> Result:
    """Invoke the parallel unblock command via CLI runner."""
    return runner.invoke(app, ["parallel", "unblock", story_id, "--project", str(tmp_path)])


# ============================================================================
# TestParallelUnblock — function-level tests
# ============================================================================


class TestParallelUnblock:
    """Test parallel_unblock function behavior."""

    def test_blocked_story_successfully_unblocked(self, tmp_path: Path) -> None:
        """Blocked story transitions to backlog with confirmation message."""
        state = _make_blocked_state("3.2")
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 0
        assert "Story 3.2 unblocked -- will be picked up on next parallel run" in result.output
        mock_save.assert_called_once()

    def test_stale_fields_cleared_on_unblock(self, tmp_path: Path) -> None:
        """After unblock, error, completed_at, worktree_path, started_at are None."""
        state = _make_blocked_state("3.2", error="dep failed")
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 0
        saved_state = mock_save.call_args[0][0]
        story = saved_state.stories["3.2"]
        assert story.status == StoryStatus.BACKLOG
        assert story.error is None
        assert story.completed_at is None
        assert story.worktree_path is None
        assert story.started_at is None

    def test_non_blocked_story_done_rejected(self, tmp_path: Path) -> None:
        """Story with status 'done' is rejected with correct error message."""
        state = _make_state({
            "3.2": StoryState(
                status=StoryStatus.DONE,
                started_at=_FIXED_NOW - timedelta(hours=1),
                completed_at=_FIXED_NOW,
            ),
        })
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 1
        assert "Story 3.2 is not blocked (status: done)" in result.output
        mock_save.assert_not_called()

    def test_non_blocked_story_backlog_rejected(self, tmp_path: Path) -> None:
        """Story with status 'backlog' is rejected."""
        state = _make_state({"3.2": StoryState(status=StoryStatus.BACKLOG)})
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 1
        assert "Story 3.2 is not blocked (status: backlog)" in result.output
        mock_save.assert_not_called()

    def test_non_blocked_story_in_flight_rejected(self, tmp_path: Path) -> None:
        """Story with status 'in_flight' is rejected."""
        state = _make_state({
            "3.2": StoryState(
                status=StoryStatus.IN_FLIGHT,
                started_at=_FIXED_NOW,
            ),
        })
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 1
        assert "Story 3.2 is not blocked (status: in_flight)" in result.output
        mock_save.assert_not_called()

    def test_non_blocked_story_merging_rejected(self, tmp_path: Path) -> None:
        """Story with status 'merging' is rejected."""
        state = _make_state({
            "3.2": StoryState(
                status=StoryStatus.MERGING,
                started_at=_FIXED_NOW,
            ),
        })
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 1
        assert "Story 3.2 is not blocked (status: merging)" in result.output
        mock_save.assert_not_called()

    def test_unknown_story_rejected(self, tmp_path: Path) -> None:
        """Unknown story ID produces correct error message."""
        state = _make_state({"3.1": StoryState()})
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.99")

        assert result.exit_code == 1
        assert "Story 3.99 not found in parallel state" in result.output
        mock_save.assert_not_called()

    def test_no_state_file(self, tmp_path: Path) -> None:
        """Prints error and exits with code 1 when no state file exists."""
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=None),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 1
        assert "No parallel run state found" in result.output
        mock_save.assert_not_called()

    def test_corrupt_state_file(self, tmp_path: Path) -> None:
        """Prints error and exits with code 1 for corrupt state file."""
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(
                _LOAD_STATE_PATCH,
                side_effect=ParallelError("corrupted YAML"),
            ),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 1
        assert "Error reading state file" in result.output
        mock_save.assert_not_called()

    def test_save_state_failure_prints_error(self, tmp_path: Path) -> None:
        """save_state raising ParallelError produces clean error, exit code 1."""
        state = _make_blocked_state("3.2")
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(
                _SAVE_STATE_PATCH,
                side_effect=ParallelError("disk full"),
            ),
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 1
        assert "Failed to save state" in result.output
        assert "disk full" in result.output

    def test_save_state_receives_correct_path(self, tmp_path: Path) -> None:
        """save_state is called with the path from get_parallel_state_path."""
        state = _make_blocked_state("3.2")
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 0
        mock_save.assert_called_once()
        saved_path = mock_save.call_args[0][1]
        assert saved_path == state_path

    def test_blocked_story_with_error_message_cleared(self, tmp_path: Path) -> None:
        """After unblocking a story with an error, the error field is cleared."""
        state = _make_blocked_state("3.2", error="Merge conflict on main.py")
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 0
        saved_state = mock_save.call_args[0][0]
        assert saved_state.stories["3.2"].error is None

    def test_lock_file_guard(self, tmp_path: Path) -> None:
        """Command refuses to run when orchestrator lock file exists."""
        lock_dir = tmp_path / ".bmad-assist-lite"
        lock_dir.mkdir(parents=True)
        lock_file = lock_dir / "running.lock"
        lock_file.write_text("locked", encoding="utf-8")

        with (
            patch(_LOAD_STATE_PATCH) as mock_load,
            patch(_SAVE_STATE_PATCH) as mock_save,
        ):
            result = _invoke_unblock(tmp_path, "3.2")

        assert result.exit_code == 1
        assert "Cannot unblock while orchestrator is running" in result.output
        assert "Stop the orchestrator first" in result.output
        mock_load.assert_not_called()
        mock_save.assert_not_called()


# ============================================================================
# TestUnblockCLI — CLI integration tests
# ============================================================================


class TestUnblockCLI:
    """Test parallel unblock via CliRunner integration."""

    def test_cli_success_through_main_app(self, tmp_path: Path) -> None:
        """Invoke through main app: parallel unblock 3.2 succeeds."""
        state = _make_blocked_state("3.2")
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH),
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = runner.invoke(
                app, ["parallel", "unblock", "3.2", "--project", str(tmp_path)]
            )

        assert result.exit_code == 0
        assert "Story 3.2 unblocked -- will be picked up on next parallel run" in result.output

    def test_cli_error_unknown_story(self, tmp_path: Path) -> None:
        """Invoke with unknown story ID produces error."""
        state = _make_state({"3.1": StoryState()})
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = runner.invoke(
                app, ["parallel", "unblock", "3.99", "--project", str(tmp_path)]
            )

        assert result.exit_code == 1
        assert "Story 3.99 not found in parallel state" in result.output
        mock_save.assert_not_called()

    def test_cli_error_not_blocked(self, tmp_path: Path) -> None:
        """Invoke with non-blocked story produces error."""
        state = _make_state({
            "3.2": StoryState(
                status=StoryStatus.DONE,
                started_at=_FIXED_NOW,
                completed_at=_FIXED_NOW,
            ),
        })
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = runner.invoke(
                app, ["parallel", "unblock", "3.2", "--project", str(tmp_path)]
            )

        assert result.exit_code == 1
        assert "Story 3.2 is not blocked (status: done)" in result.output
        mock_save.assert_not_called()

    def test_cli_no_state_file(self, tmp_path: Path) -> None:
        """Invoke when no state file exists produces error."""
        state_path = tmp_path / ".bmad-assist-lite" / "parallel-state.yaml"

        with (
            patch(_LOAD_STATE_PATCH, return_value=None),
            patch(_SAVE_STATE_PATCH) as mock_save,
            patch(_GET_STATE_PATH_PATCH, return_value=state_path),
        ):
            result = runner.invoke(
                app, ["parallel", "unblock", "3.2", "--project", str(tmp_path)]
            )

        assert result.exit_code == 1
        assert "No parallel run state found" in result.output
        mock_save.assert_not_called()

    def test_cli_lock_file_guard(self, tmp_path: Path) -> None:
        """Invoke when lock file exists produces error."""
        lock_dir = tmp_path / ".bmad-assist-lite"
        lock_dir.mkdir(parents=True)
        lock_file = lock_dir / "running.lock"
        lock_file.write_text("locked", encoding="utf-8")

        with (
            patch(_LOAD_STATE_PATCH) as mock_load,
            patch(_SAVE_STATE_PATCH) as mock_save,
        ):
            result = runner.invoke(
                app, ["parallel", "unblock", "3.2", "--project", str(tmp_path)]
            )

        assert result.exit_code == 1
        assert "Cannot unblock while orchestrator is running" in result.output
        mock_load.assert_not_called()
        mock_save.assert_not_called()
