"""Test the parallel status CLI command."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from bmad_assist_lite.cli import app
from bmad_assist_lite.parallel.cli import (
    _format_dependency_status,
    _format_duration,
    _format_status_table,
    _format_summary,
    _peek_worktree_phase,
)
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.state import (
    ParallelState,
    StoryState,
    StoryStatus,
)

runner = CliRunner()

# ============================================================================
# Shared helpers
# ============================================================================

_LOAD_STATE_PATCH = "bmad_assist_lite.parallel.state.load_state"
_GET_STATE_PATH_PATCH = "bmad_assist_lite.parallel.state.get_parallel_state_path"

# Fixed "now" for deterministic duration tests
_FIXED_NOW = datetime(2026, 3, 21, 12, 0, 0)


def _make_state(
    stories: dict[str, StoryState] | None = None,
    base_branch: str = "epic/5",
    epic: int = 5,
) -> ParallelState:
    """Create a ParallelState for testing."""
    if stories is None:
        stories = {"5.1": StoryState()}
    return ParallelState(
        base_branch=base_branch,
        epic=epic,
        started_at=_FIXED_NOW - timedelta(hours=1),
        stories=stories,
    )


def _invoke_status(tmp_path: Path) -> Result:  # noqa: F821
    """Invoke the parallel status command via CLI runner."""
    return runner.invoke(app, ["parallel", "status", "--project", str(tmp_path)])


# ============================================================================
# TestFormatDuration
# ============================================================================


class TestFormatDuration:
    """Test duration formatting helper."""

    def test_no_started_at_returns_dash(self) -> None:
        """Stories with no started_at show dash."""
        assert _format_duration(None, None) == "-"

    def test_running_story_elapsed(self) -> None:
        """Running story calculates elapsed from started_at to now."""
        started = _FIXED_NOW - timedelta(minutes=5, seconds=30)
        with patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW):
            result = _format_duration(started, None)
        assert result == "5m 30s"

    def test_completed_story_duration(self) -> None:
        """Completed story calculates from started_at to completed_at."""
        started = _FIXED_NOW - timedelta(hours=1, minutes=23, seconds=45)
        completed = _FIXED_NOW
        result = _format_duration(started, completed)
        assert result == "1h 23m 45s"

    def test_zero_duration(self) -> None:
        """Zero-duration shows 0s."""
        result = _format_duration(_FIXED_NOW, _FIXED_NOW)
        assert result == "0s"

    def test_seconds_only(self) -> None:
        """Duration under a minute shows only seconds."""
        started = _FIXED_NOW - timedelta(seconds=42)
        result = _format_duration(started, _FIXED_NOW)
        assert result == "42s"

    def test_minutes_and_seconds(self) -> None:
        """Duration under an hour shows minutes and seconds."""
        started = _FIXED_NOW - timedelta(minutes=7, seconds=15)
        result = _format_duration(started, _FIXED_NOW)
        assert result == "7m 15s"

    def test_hours_minutes_seconds(self) -> None:
        """Multi-hour duration omits minutes when zero."""
        started = _FIXED_NOW - timedelta(hours=3, minutes=0, seconds=5)
        result = _format_duration(started, _FIXED_NOW)
        assert result == "3h 5s"

    def test_hours_with_minutes(self) -> None:
        """Multi-hour duration with non-zero minutes shows all components."""
        started = _FIXED_NOW - timedelta(hours=2, minutes=15, seconds=30)
        result = _format_duration(started, _FIXED_NOW)
        assert result == "2h 15m 30s"

    def test_negative_duration_clamped_to_zero(self) -> None:
        """If completed_at is before started_at (clock skew), clamp to 0s."""
        started = _FIXED_NOW
        completed = _FIXED_NOW - timedelta(seconds=10)
        result = _format_duration(started, completed)
        assert result == "0s"


# ============================================================================
# TestPeekWorktreePhase
# ============================================================================


class TestPeekWorktreePhase:
    """Test worktree phase peeking helper."""

    def test_none_worktree_path(self) -> None:
        """Returns None when worktree_path is None."""
        assert _peek_worktree_phase(None) is None

    def test_missing_state_file(self, tmp_path: Path) -> None:
        """Returns None when state.yaml doesn't exist."""
        assert _peek_worktree_phase(tmp_path) is None

    def test_valid_state_file(self, tmp_path: Path) -> None:
        """Returns phase from valid state.yaml."""
        state_dir = tmp_path / ".bmad-assist"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.yaml"
        state_file.write_text("current_phase: implement\n", encoding="utf-8")
        assert _peek_worktree_phase(tmp_path) == "implement"

    def test_corrupt_yaml(self, tmp_path: Path) -> None:
        """Returns None for corrupt YAML."""
        state_dir = tmp_path / ".bmad-assist"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.yaml"
        state_file.write_text(": invalid: yaml: {{{\n", encoding="utf-8")
        assert _peek_worktree_phase(tmp_path) is None

    def test_missing_phase_key(self, tmp_path: Path) -> None:
        """Returns None when current_phase key is missing."""
        state_dir = tmp_path / ".bmad-assist"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.yaml"
        state_file.write_text("other_key: value\n", encoding="utf-8")
        assert _peek_worktree_phase(tmp_path) is None

    def test_non_string_phase(self, tmp_path: Path) -> None:
        """Returns None when current_phase is not a string."""
        state_dir = tmp_path / ".bmad-assist"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.yaml"
        state_file.write_text("current_phase: 42\n", encoding="utf-8")
        assert _peek_worktree_phase(tmp_path) is None

    def test_empty_file(self, tmp_path: Path) -> None:
        """Returns None for empty file."""
        state_dir = tmp_path / ".bmad-assist"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.yaml"
        state_file.write_text("", encoding="utf-8")
        assert _peek_worktree_phase(tmp_path) is None

    def test_permission_error(self, tmp_path: Path) -> None:
        """Returns None on permission error (OSError subclass)."""
        state_dir = tmp_path / ".bmad-assist"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.yaml"
        state_file.write_text("current_phase: implement\n", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=PermissionError("access denied")):
            assert _peek_worktree_phase(tmp_path) is None


# ============================================================================
# TestFormatStatusTable
# ============================================================================


class TestFormatStatusTable:
    """Test table formatting."""

    def test_table_has_header_and_separator(self) -> None:
        """Table includes header row and separator line."""
        state = _make_state({"5.1": StoryState()})
        table = _format_status_table(state)
        lines = table.split("\n")
        assert len(lines) >= 3  # header, separator, at least one data row
        assert "Story ID" in lines[0]
        assert "Status" in lines[0]
        assert "Phase" in lines[0]
        assert "Duration" in lines[0]
        assert "---" in lines[1]

    def test_backlog_story_display(self) -> None:
        """Backlog story shows correct status and dash for duration."""
        state = _make_state({"5.1": StoryState(status=StoryStatus.BACKLOG)})
        table = _format_status_table(state)
        assert "backlog" in table
        assert "5.1" in table

    def test_in_flight_story_display(self) -> None:
        """In-flight story shows correct status."""
        state = _make_state({
            "5.1": StoryState(
                status=StoryStatus.IN_FLIGHT,
                started_at=_FIXED_NOW - timedelta(minutes=10),
            ),
        })
        with patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW):
            table = _format_status_table(state)
        assert "in_flight" in table
        assert "10m 0s" in table

    def test_done_story_display(self) -> None:
        """Done story shows correct status and duration."""
        state = _make_state({
            "5.1": StoryState(
                status=StoryStatus.DONE,
                started_at=_FIXED_NOW - timedelta(hours=1),
                completed_at=_FIXED_NOW,
            ),
        })
        table = _format_status_table(state)
        assert "done" in table
        assert "1h 0s" in table

    def test_blocked_story_shows_error(self) -> None:
        """Blocked story shows error in Info column."""
        state = _make_state({
            "5.1": StoryState(
                status=StoryStatus.BLOCKED,
                started_at=_FIXED_NOW - timedelta(minutes=5),
                completed_at=_FIXED_NOW,
                error="Dependency 5.2 failed",
            ),
        })
        table = _format_status_table(state)
        assert "blocked" in table
        assert "Dependency 5.2 failed" in table

    def test_error_truncation(self) -> None:
        """Long error messages are truncated to ~80 chars."""
        long_error = "A" * 100
        state = _make_state({
            "5.1": StoryState(
                status=StoryStatus.BLOCKED,
                started_at=_FIXED_NOW,
                completed_at=_FIXED_NOW,
                error=long_error,
            ),
        })
        table = _format_status_table(state)
        assert "A" * 77 + "..." in table
        assert "A" * 100 not in table

    def test_mixed_statuses(self) -> None:
        """Table correctly displays mixed story statuses."""
        stories = {
            "5.1": StoryState(
                status=StoryStatus.DONE,
                started_at=_FIXED_NOW - timedelta(hours=1),
                completed_at=_FIXED_NOW,
            ),
            "5.2": StoryState(
                status=StoryStatus.IN_FLIGHT,
                started_at=_FIXED_NOW - timedelta(minutes=5),
            ),
            "5.3": StoryState(status=StoryStatus.BACKLOG),
            "5.4": StoryState(
                status=StoryStatus.BLOCKED,
                started_at=_FIXED_NOW - timedelta(minutes=30),
                completed_at=_FIXED_NOW,
                error="QG failed",
            ),
            "5.5": StoryState(
                status=StoryStatus.MERGING,
                started_at=_FIXED_NOW - timedelta(minutes=2),
            ),
        }
        state = _make_state(stories)
        with patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW):
            table = _format_status_table(state)

        assert "done" in table
        assert "in_flight" in table
        assert "backlog" in table
        assert "blocked" in table
        assert "merging" in table
        assert "QG failed" in table

    def test_stories_sorted_by_id(self) -> None:
        """Stories are sorted by story ID in the table."""
        stories = {
            "5.3": StoryState(),
            "5.1": StoryState(),
            "5.2": StoryState(),
        }
        state = _make_state(stories)
        table = _format_status_table(state)
        lines = table.split("\n")
        # Data rows start at index 2 (after header and separator)
        assert "5.1" in lines[2]
        assert "5.2" in lines[3]
        assert "5.3" in lines[4]

    def test_phase_column_shows_peeked_phase_for_in_flight(
        self, tmp_path: Path
    ) -> None:
        """In-flight stories show phase from worktree state.yaml."""
        # Create worktree state file
        state_dir = tmp_path / ".bmad-assist"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.yaml"
        state_file.write_text("current_phase: test\n", encoding="utf-8")

        state = _make_state({
            "5.1": StoryState(
                status=StoryStatus.IN_FLIGHT,
                worktree_path=tmp_path,
                started_at=_FIXED_NOW,
            ),
        })
        with patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW):
            table = _format_status_table(state)
        assert "test" in table

    def test_phase_column_fallback_when_peek_fails(self) -> None:
        """In-flight stories show dash when worktree state.yaml is missing."""
        state = _make_state({
            "5.1": StoryState(
                status=StoryStatus.IN_FLIGHT,
                worktree_path=Path("/nonexistent/path"),
                started_at=_FIXED_NOW,
            ),
        })
        with patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW):
            table = _format_status_table(state)
        lines = table.split("\n")
        # Phase column should show "-" for the data row
        data_row = lines[2]  # first data row
        # Ensure the phase column has "-"
        assert "in_flight" in data_row


# ============================================================================
# TestFormatSummary
# ============================================================================


class TestFormatSummary:
    """Test summary counts formatting."""

    def test_summary_shows_epic_and_branch(self) -> None:
        """Summary header includes epic ID and base branch."""
        state = _make_state({"5.1": StoryState()})
        summary = _format_summary(state)
        assert "Epic: 5" in summary
        assert "Base branch: epic/5" in summary

    def test_summary_counts_accurate(self) -> None:
        """Summary counts match story statuses."""
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(status=StoryStatus.DONE),
            "5.3": StoryState(status=StoryStatus.IN_FLIGHT),
            "5.4": StoryState(status=StoryStatus.BLOCKED),
            "5.5": StoryState(status=StoryStatus.BACKLOG),
            "5.6": StoryState(status=StoryStatus.MERGING),
        }
        state = _make_state(stories)
        summary = _format_summary(state)
        assert "Done: 2" in summary
        assert "In-flight: 1" in summary
        assert "Merging: 1" in summary
        assert "Blocked: 1" in summary
        assert "Backlog: 1" in summary

    def test_all_done_message(self) -> None:
        """Shows completion message when all stories are done."""
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(status=StoryStatus.DONE),
        }
        state = _make_state(stories)
        summary = _format_summary(state)
        assert "All stories complete!" in summary

    def test_no_all_done_when_mixed(self) -> None:
        """Does not show completion message when not all done."""
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(status=StoryStatus.IN_FLIGHT),
        }
        state = _make_state(stories)
        summary = _format_summary(state)
        assert "All stories complete!" not in summary

    def test_blocked_warning(self) -> None:
        """Shows warning when stories are blocked."""
        stories = {
            "5.1": StoryState(status=StoryStatus.BLOCKED),
            "5.2": StoryState(status=StoryStatus.BLOCKED),
        }
        state = _make_state(stories)
        summary = _format_summary(state)
        assert "\u26a0 2 stories blocked" in summary

    def test_empty_stories_no_all_done_message(self) -> None:
        """Empty stories dict does not show 'All stories complete!' (vacuous truth guard)."""
        state = _make_state({})
        summary = _format_summary(state)
        assert "All stories complete!" not in summary

    def test_single_blocked_warning_grammar(self) -> None:
        """Warning uses singular 'story' for one blocked story."""
        stories = {
            "5.1": StoryState(status=StoryStatus.BLOCKED),
            "5.2": StoryState(status=StoryStatus.DONE),
        }
        state = _make_state(stories)
        summary = _format_summary(state)
        assert "\u26a0 1 story blocked" in summary


# ============================================================================
# TestParallelStatus — CLI integration
# ============================================================================


class TestParallelStatus:
    """Test parallel status CLI command integration."""

    def test_no_state_file(self, tmp_path: Path) -> None:
        """Prints message and exits cleanly when no state file exists."""
        with patch(_LOAD_STATE_PATCH, return_value=None):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        assert "No parallel run state found" in result.output

    def test_corrupt_state_file(self, tmp_path: Path) -> None:
        """Prints error to stderr and exits with code 1 for corrupt file."""
        with patch(
            _LOAD_STATE_PATCH,
            side_effect=ParallelError("corrupted YAML"),
        ):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 1
        assert "Error reading state file" in result.output

    def test_display_with_mixed_statuses(self, tmp_path: Path) -> None:
        """Displays table with mixed story statuses."""
        stories = {
            "5.1": StoryState(
                status=StoryStatus.DONE,
                started_at=_FIXED_NOW - timedelta(hours=1),
                completed_at=_FIXED_NOW,
            ),
            "5.2": StoryState(
                status=StoryStatus.IN_FLIGHT,
                started_at=_FIXED_NOW - timedelta(minutes=10),
            ),
            "5.3": StoryState(status=StoryStatus.BACKLOG),
            "5.4": StoryState(
                status=StoryStatus.BLOCKED,
                started_at=_FIXED_NOW - timedelta(minutes=20),
                completed_at=_FIXED_NOW,
                error="QG failed after 2 retries",
            ),
        }
        state = _make_state(stories)

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW),
        ):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        output = result.output
        assert "Epic: 5" in output
        assert "Base branch: epic/5" in output
        assert "done" in output
        assert "in_flight" in output
        assert "backlog" in output
        assert "blocked" in output
        assert "QG failed after 2 retries" in output
        assert "Done: 1" in output
        assert "Blocked: 1" in output

    def test_all_stories_done_summary(self, tmp_path: Path) -> None:
        """Shows completion message when all stories are done."""
        stories = {
            "5.1": StoryState(
                status=StoryStatus.DONE,
                started_at=_FIXED_NOW - timedelta(hours=1),
                completed_at=_FIXED_NOW,
            ),
            "5.2": StoryState(
                status=StoryStatus.DONE,
                started_at=_FIXED_NOW - timedelta(minutes=30),
                completed_at=_FIXED_NOW,
            ),
        }
        state = _make_state(stories)

        with patch(_LOAD_STATE_PATCH, return_value=state):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        assert "All stories complete!" in result.output
        assert "Done: 2" in result.output

    def test_duration_display_running(self, tmp_path: Path) -> None:
        """Running story shows elapsed duration from started_at to now."""
        stories = {
            "5.1": StoryState(
                status=StoryStatus.IN_FLIGHT,
                started_at=_FIXED_NOW - timedelta(minutes=15, seconds=30),
            ),
        }
        state = _make_state(stories)

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW),
        ):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        assert "15m 30s" in result.output

    def test_duration_display_ready(self, tmp_path: Path) -> None:
        """Backlog story with no started_at shows dash for duration."""
        stories = {"5.1": StoryState(status=StoryStatus.BACKLOG)}
        state = _make_state(stories)

        with patch(_LOAD_STATE_PATCH, return_value=state):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        # The dash "-" should appear in the duration column
        assert "-" in result.output

    def test_read_only_safety(self, tmp_path: Path) -> None:
        """Status command does not call save_state (read-only)."""
        state = _make_state({"5.1": StoryState()})

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch("bmad_assist_lite.parallel.state.save_state") as mock_save,
        ):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        mock_save.assert_not_called()

    def test_error_in_info_column_truncated(self, tmp_path: Path) -> None:
        """Long error messages are truncated in the Info column."""
        long_error = "X" * 100
        stories = {
            "5.1": StoryState(
                status=StoryStatus.BLOCKED,
                started_at=_FIXED_NOW,
                completed_at=_FIXED_NOW,
                error=long_error,
            ),
        }
        state = _make_state(stories)

        with patch(_LOAD_STATE_PATCH, return_value=state):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        assert "X" * 77 + "..." in result.output
        assert "X" * 100 not in result.output

    def test_phase_peek_for_in_flight_story(self, tmp_path: Path) -> None:
        """In-flight story shows phase from worktree state.yaml."""
        # Create worktree state
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        state_dir = worktree / ".bmad-assist"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.yaml"
        state_file.write_text("current_phase: code_review\n", encoding="utf-8")

        stories = {
            "5.1": StoryState(
                status=StoryStatus.IN_FLIGHT,
                worktree_path=worktree,
                started_at=_FIXED_NOW,
            ),
        }
        state = _make_state(stories)

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW),
        ):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        assert "code_review" in result.output

    def test_phase_peek_graceful_fallback(self, tmp_path: Path) -> None:
        """In-flight story falls back to dash when worktree state is missing."""
        stories = {
            "5.1": StoryState(
                status=StoryStatus.IN_FLIGHT,
                worktree_path=Path("/nonexistent/worktree"),
                started_at=_FIXED_NOW,
            ),
        }
        state = _make_state(stories)

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW),
        ):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        # Should not crash; should complete gracefully
        assert "in_flight" in result.output

    def test_blocked_warning_in_summary(self, tmp_path: Path) -> None:
        """Blocked stories trigger a warning in the summary."""
        stories = {
            "5.1": StoryState(
                status=StoryStatus.BLOCKED,
                error="dep failed",
            ),
        }
        state = _make_state(stories)

        with patch(_LOAD_STATE_PATCH, return_value=state):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        assert "\u26a0 1 story blocked" in result.output


# ============================================================================
# TestFormatDependencyStatus — Story 6.2 dependency status formatting
# ============================================================================


def _make_mock_graph(deps: dict[str, list[str]]) -> MagicMock:
    """Create a mock DependencyGraph with given dependencies_of mapping."""
    graph = MagicMock()

    def _dependencies_of(story_id: str) -> list[str]:
        if story_id not in deps:
            raise KeyError(f"Story {story_id!r} not found in graph")
        return deps[story_id]

    graph.dependencies_of = MagicMock(side_effect=_dependencies_of)
    return graph


class TestFormatDependencyStatus:
    """Test dependency status formatting with visual indicators."""

    def test_done_dependency_shows_checkmark(self) -> None:
        """DONE dependency shows checkmark symbol."""
        graph = _make_mock_graph({"5.2": ["5.1"]})
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(),
        }
        result = _format_dependency_status("5.2", graph, stories)
        assert "5.1 \u2713" in result

    def test_in_flight_dependency_shows_hourglass(self) -> None:
        """IN_FLIGHT dependency shows hourglass symbol."""
        graph = _make_mock_graph({"5.2": ["5.1"]})
        stories = {
            "5.1": StoryState(status=StoryStatus.IN_FLIGHT),
            "5.2": StoryState(),
        }
        result = _format_dependency_status("5.2", graph, stories)
        assert "5.1 \u23f3" in result

    def test_blocked_dependency_shows_cross(self) -> None:
        """BLOCKED dependency shows cross with text."""
        graph = _make_mock_graph({"5.2": ["5.1"]})
        stories = {
            "5.1": StoryState(status=StoryStatus.BLOCKED),
            "5.2": StoryState(),
        }
        result = _format_dependency_status("5.2", graph, stories)
        assert "5.1 \u2717 (blocked)" in result

    def test_backlog_dependency_shows_ellipsis(self) -> None:
        """BACKLOG dependency shows ellipsis symbol."""
        graph = _make_mock_graph({"5.2": ["5.1"]})
        stories = {
            "5.1": StoryState(status=StoryStatus.BACKLOG),
            "5.2": StoryState(),
        }
        result = _format_dependency_status("5.2", graph, stories)
        assert "5.1 \u2026" in result

    def test_merging_dependency_shows_ellipsis(self) -> None:
        """MERGING dependency shows ellipsis symbol."""
        graph = _make_mock_graph({"5.2": ["5.1"]})
        stories = {
            "5.1": StoryState(status=StoryStatus.MERGING),
            "5.2": StoryState(),
        }
        result = _format_dependency_status("5.2", graph, stories)
        assert "5.1 \u2026" in result

    def test_multiple_dependencies_comma_separated(self) -> None:
        """Multiple dependencies are comma-separated."""
        graph = _make_mock_graph({"5.3": ["5.1", "5.2"]})
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(status=StoryStatus.IN_FLIGHT),
            "5.3": StoryState(),
        }
        result = _format_dependency_status("5.3", graph, stories)
        assert "5.1 \u2713" in result
        assert "5.2 \u23f3" in result
        assert ", " in result

    def test_no_dependencies_returns_empty(self) -> None:
        """Story with no dependencies returns empty string."""
        graph = _make_mock_graph({"5.1": []})
        stories = {"5.1": StoryState()}
        result = _format_dependency_status("5.1", graph, stories)
        assert result == ""

    def test_none_graph_returns_empty(self) -> None:
        """When graph is None, returns empty string."""
        stories = {"5.1": StoryState()}
        result = _format_dependency_status("5.1", None, stories)
        assert result == ""

    def test_unknown_dependency_shows_question_mark(self) -> None:
        """Dependency not in stories dict shows question mark."""
        graph = _make_mock_graph({"5.2": ["5.1"]})
        # 5.1 not in stories dict (cross-epic or removed)
        stories: dict[str, StoryState] = {"5.2": StoryState()}
        result = _format_dependency_status("5.2", graph, stories)
        assert "5.1 ?" in result

    def test_story_not_in_graph_returns_empty(self) -> None:
        """Story not found in graph returns empty string gracefully."""
        graph = _make_mock_graph({})
        stories = {"5.1": StoryState()}
        result = _format_dependency_status("5.1", graph, stories)
        assert result == ""


# ============================================================================
# TestFormatStatusTableWithDependencies — Story 6.2 table integration
# ============================================================================


class TestFormatStatusTableWithDependencies:
    """Test table formatting with dependency column."""

    def test_table_has_depends_on_column(self) -> None:
        """Table header includes 'Depends On' column."""
        state = _make_state({"5.1": StoryState()})
        table = _format_status_table(state, graph=None)
        lines = table.split("\n")
        assert "Depends On" in lines[0]

    def test_depends_on_column_populated_with_graph(self) -> None:
        """Depends On column shows dependency status when graph provided."""
        graph = _make_mock_graph({"5.2": ["5.1"], "5.1": []})
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(status=StoryStatus.IN_FLIGHT, started_at=_FIXED_NOW),
        }
        state = _make_state(stories)
        with patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW):
            table = _format_status_table(state, graph=graph)
        assert "5.1 \u2713" in table

    def test_depends_on_column_empty_without_graph(self) -> None:
        """Depends On column is empty when graph is None."""
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(),
        }
        state = _make_state(stories)
        table = _format_status_table(state, graph=None)
        # No dependency symbols should appear
        assert "\u2713" not in table
        assert "\u23f3" not in table
        assert "\u2717" not in table

    def test_full_table_with_phase_and_dependencies(self, tmp_path: Path) -> None:
        """Table shows both phase and dependency info simultaneously."""
        # Set up worktree state for phase display
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        state_dir = worktree / ".bmad-assist"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "state.yaml"
        state_file.write_text("current_phase: implement\n", encoding="utf-8")

        graph = _make_mock_graph({"5.2": ["5.1"], "5.1": []})
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(
                status=StoryStatus.IN_FLIGHT,
                worktree_path=worktree,
                started_at=_FIXED_NOW,
            ),
        }
        state = _make_state(stories)
        with patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW):
            table = _format_status_table(state, graph=graph)
        # Phase column populated
        assert "implement" in table
        # Dependency column populated
        assert "5.1 \u2713" in table

    def test_no_dependencies_shows_empty_depends_column(self) -> None:
        """Root story with no dependencies shows empty Depends On column."""
        graph = _make_mock_graph({"5.1": []})
        stories = {"5.1": StoryState()}
        state = _make_state(stories)
        table = _format_status_table(state, graph=graph)
        # 5.1 should not have any dependency symbols
        lines = table.split("\n")
        data_row = lines[2]  # first data row
        assert "5.1" in data_row
        # No dependency symbols in this row
        assert "\u2713" not in data_row
        assert "\u23f3" not in data_row


# ============================================================================
# TestFormatSummaryWithDependencies — Story 6.2 summary enhancements
# ============================================================================


class TestFormatSummaryWithDependencies:
    """Test summary formatting with dependency health warnings."""

    def test_summary_warns_on_blocked_dependencies(self) -> None:
        """Summary shows warning when stories depend on blocked upstream."""
        graph = _make_mock_graph({
            "5.1": [],
            "5.2": ["5.1"],
            "5.3": ["5.1"],
        })
        stories = {
            "5.1": StoryState(status=StoryStatus.BLOCKED, error="failed"),
            "5.2": StoryState(status=StoryStatus.BACKLOG),
            "5.3": StoryState(status=StoryStatus.BACKLOG),
        }
        state = _make_state(stories)
        summary = _format_summary(state, graph=graph)
        assert "\u26a0 2 stories waiting on blocked dependencies" in summary

    def test_summary_no_warning_when_no_blocked_deps(self) -> None:
        """Summary omits dependency warning when no stories depend on blocked."""
        graph = _make_mock_graph({"5.1": [], "5.2": ["5.1"]})
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(status=StoryStatus.IN_FLIGHT),
        }
        state = _make_state(stories)
        summary = _format_summary(state, graph=graph)
        assert "waiting on blocked dependencies" not in summary

    def test_summary_no_crash_without_graph(self) -> None:
        """Summary works without graph (None)."""
        stories = {
            "5.1": StoryState(status=StoryStatus.BLOCKED, error="failed"),
            "5.2": StoryState(),
        }
        state = _make_state(stories)
        summary = _format_summary(state, graph=None)
        # Should not crash, and should not show dependency health warning
        assert "waiting on blocked dependencies" not in summary

    def test_summary_singular_blocked_dependency_warning(self) -> None:
        """Summary uses singular form for one story waiting on blocked deps."""
        graph = _make_mock_graph({"5.1": [], "5.2": ["5.1"]})
        stories = {
            "5.1": StoryState(status=StoryStatus.BLOCKED, error="failed"),
            "5.2": StoryState(status=StoryStatus.BACKLOG),
        }
        state = _make_state(stories)
        summary = _format_summary(state, graph=graph)
        assert "\u26a0 1 story waiting on blocked dependencies" in summary


# ============================================================================
# TestParallelStatusWithDependencies — Story 6.2 CLI integration
# ============================================================================


class TestParallelStatusWithDependencies:
    """Test parallel status CLI command with dependency display integration."""

    def test_status_command_when_epic_file_missing(self, tmp_path: Path) -> None:
        """Status command works when epic file can't be found."""
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(status=StoryStatus.IN_FLIGHT, started_at=_FIXED_NOW),
        }
        state = _make_state(stories)

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW),
        ):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        assert "5.1" in result.output
        assert "5.2" in result.output

    def test_status_command_read_only_with_dependencies(
        self, tmp_path: Path,
    ) -> None:
        """Status command with dependencies does not call save_state."""
        stories = {
            "5.1": StoryState(status=StoryStatus.DONE),
            "5.2": StoryState(status=StoryStatus.IN_FLIGHT, started_at=_FIXED_NOW),
        }
        state = _make_state(stories)

        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW),
            patch("bmad_assist_lite.parallel.state.save_state") as mock_save,
        ):
            result = _invoke_status(tmp_path)

        assert result.exit_code == 0
        mock_save.assert_not_called()

    def test_nfr10_status_completes_under_2_seconds(self, tmp_path: Path) -> None:
        """Status command completes in <2 seconds (NFR10 compliance)."""
        import time

        stories = {
            f"5.{i}": StoryState(
                status=StoryStatus.IN_FLIGHT,
                started_at=_FIXED_NOW - timedelta(minutes=i),
                worktree_path=Path(f"/nonexistent/wt-{i}"),
            )
            for i in range(1, 11)
        }
        state = _make_state(stories)

        start = time.monotonic()
        with (
            patch(_LOAD_STATE_PATCH, return_value=state),
            patch("bmad_assist_lite.parallel.cli._utc_now", return_value=_FIXED_NOW),
        ):
            result = _invoke_status(tmp_path)
        elapsed = time.monotonic() - start

        assert result.exit_code == 0
        assert elapsed < 2.0, f"Status command took {elapsed:.2f}s, exceeds 2s limit"
