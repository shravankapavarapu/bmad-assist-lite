"""Comprehensive tests for crash recovery and resume in-flight stories."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.parallel.recovery import (
    _cleanup_temp_files,
    prune_and_clean_orphaned_worktrees,
    recover_state,
)
from bmad_assist_lite.parallel.state import (
    ParallelState,
    StoryState,
    StoryStatus,
)
from bmad_assist_lite.parallel.worktree_manager import WorktreeInfo


# ============================================================================
# Helper factories
# ============================================================================


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info (naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


def _make_state(
    stories: dict[str, StoryState] | None = None,
    base_branch: str = "main",
    epic: int = 3,
) -> ParallelState:
    """Create a ParallelState with test-friendly defaults."""
    if stories is None:
        stories = {}
    return ParallelState(
        base_branch=base_branch,
        epic=epic,
        started_at=_utc_now(),
        stories=stories,
    )


def _make_story(
    status: StoryStatus = StoryStatus.BACKLOG,
    worktree_path: Path | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    error: str | None = None,
) -> StoryState:
    """Create a StoryState with test-friendly defaults."""
    return StoryState(
        status=status,
        worktree_path=worktree_path,
        started_at=started_at,
        completed_at=completed_at,
        error=error,
    )


def _make_worktree_info(
    path: Path,
    branch: str | None = None,
    commit: str = "abc123",
) -> WorktreeInfo:
    """Create a WorktreeInfo with test-friendly defaults."""
    return WorktreeInfo(path=path, branch=branch, commit=commit)


# ============================================================================
# TestRecoverState — In-flight stories
# ============================================================================


class TestRecoverStateInFlight:
    """Test recovery behavior for in-flight stories."""

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_in_flight_with_existing_worktree_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """In-flight story with existing worktree remains in_flight (AC #1)."""
        wt_path = Path("/worktrees/parallel-3-1").resolve()
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=wt_path,
                    started_at=_utc_now(),
                ),
            }
        )
        mock_list_wt.return_value = [
            _make_worktree_info(path=wt_path, branch="parallel/3-1"),
        ]

        result = recover_state(state, Path("/project"))

        assert result.stories["3.1"].status == StoryStatus.IN_FLIGHT
        assert result.stories["3.1"].worktree_path == wt_path

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_in_flight_with_missing_worktree_reset_to_backlog(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """In-flight story with missing worktree is reset to backlog (AC #2)."""
        wt_path = Path("/worktrees/parallel-3-1").resolve()
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=wt_path,
                    started_at=_utc_now(),
                ),
            }
        )
        mock_list_wt.return_value = []  # No worktrees on disk

        with caplog.at_level(logging.WARNING):
            result = recover_state(state, Path("/project"))

        assert result.stories["3.1"].status == StoryStatus.BACKLOG
        assert result.stories["3.1"].worktree_path is None
        assert result.stories["3.1"].started_at is None
        assert result.stories["3.1"].error is None
        assert any(
            "3.1" in r.message and "reset to backlog" in r.message
            for r in caplog.records
        )

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_in_flight_with_none_worktree_path_reset_to_backlog(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """In-flight story with worktree_path=None is treated as missing (edge case)."""
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=None,
                    started_at=_utc_now(),
                ),
            }
        )
        mock_list_wt.return_value = []

        result = recover_state(state, Path("/project"))

        assert result.stories["3.1"].status == StoryStatus.BACKLOG


# ============================================================================
# TestRecoverState — Terminal statuses
# ============================================================================


class TestRecoverStateTerminalStatuses:
    """Test recovery preserves terminal statuses (AC #3)."""

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_done_stories_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Done stories remain done after recovery (AC #3)."""
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.DONE,
                    completed_at=_utc_now(),
                ),
            }
        )
        mock_list_wt.return_value = []

        result = recover_state(state, Path("/project"))

        assert result.stories["3.1"].status == StoryStatus.DONE

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_blocked_stories_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Blocked stories remain blocked after recovery (AC #3)."""
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.BLOCKED,
                    error="Exit code 1",
                    completed_at=_utc_now(),
                ),
            }
        )
        mock_list_wt.return_value = []

        result = recover_state(state, Path("/project"))

        assert result.stories["3.1"].status == StoryStatus.BLOCKED
        assert result.stories["3.1"].error == "Exit code 1"


# ============================================================================
# TestRecoverState — Merging stories
# ============================================================================


class TestRecoverStateMerging:
    """Test recovery behavior for merging stories (AC #7)."""

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_merging_with_missing_worktree_reset_to_backlog(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Merging story with missing worktree is reset to backlog (AC #7)."""
        wt_path = Path("/worktrees/parallel-3-2").resolve()
        state = _make_state(
            stories={
                "3.2": _make_story(
                    status=StoryStatus.MERGING,
                    worktree_path=wt_path,
                    completed_at=_utc_now(),
                ),
            }
        )
        mock_list_wt.return_value = []

        with caplog.at_level(logging.WARNING):
            result = recover_state(state, Path("/project"))

        assert result.stories["3.2"].status == StoryStatus.BACKLOG
        assert result.stories["3.2"].worktree_path is None
        assert any(
            "3.2" in r.message and "reset to backlog" in r.message
            for r in caplog.records
        )

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_merging_with_existing_worktree_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Merging story with existing worktree remains merging (AC #7)."""
        wt_path = Path("/worktrees/parallel-3-2").resolve()
        state = _make_state(
            stories={
                "3.2": _make_story(
                    status=StoryStatus.MERGING,
                    worktree_path=wt_path,
                    completed_at=_utc_now(),
                ),
            }
        )
        mock_list_wt.return_value = [
            _make_worktree_info(path=wt_path, branch="parallel/3-2"),
        ]

        result = recover_state(state, Path("/project"))

        assert result.stories["3.2"].status == StoryStatus.MERGING
        assert result.stories["3.2"].worktree_path == wt_path


# ============================================================================
# TestRecoverState — Backlog stories
# ============================================================================


class TestRecoverStateBacklog:
    """Test recovery preserves backlog stories unchanged."""

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_backlog_stories_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Backlog stories pass through recovery unchanged."""
        state = _make_state(
            stories={
                "3.1": _make_story(status=StoryStatus.BACKLOG),
            }
        )
        mock_list_wt.return_value = []

        result = recover_state(state, Path("/project"))

        assert result.stories["3.1"].status == StoryStatus.BACKLOG


# ============================================================================
# TestRecoverState — Edge cases
# ============================================================================


class TestRecoverStateEdgeCases:
    """Test recovery edge cases."""

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_empty_stories_dict(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Recovery with empty stories dict is a no-op (edge case)."""
        state = _make_state(stories={})
        mock_list_wt.return_value = []

        result = recover_state(state, Path("/project"))

        assert result.stories == {}

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_all_stories_done(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Recovery with all stories done is a no-op."""
        state = _make_state(
            stories={
                "3.1": _make_story(status=StoryStatus.DONE, completed_at=_utc_now()),
                "3.2": _make_story(status=StoryStatus.DONE, completed_at=_utc_now()),
            }
        )
        mock_list_wt.return_value = []

        result = recover_state(state, Path("/project"))

        assert result.stories["3.1"].status == StoryStatus.DONE
        assert result.stories["3.2"].status == StoryStatus.DONE

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_mixed_statuses_reconciled_correctly(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Mixed statuses are all reconciled correctly."""
        wt_path_1 = Path("/worktrees/parallel-3-1").resolve()
        wt_path_3 = Path("/worktrees/parallel-3-3").resolve()
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=wt_path_1,
                    started_at=_utc_now(),
                ),
                "3.2": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=Path("/missing/worktree").resolve(),
                    started_at=_utc_now(),
                ),
                "3.3": _make_story(
                    status=StoryStatus.MERGING,
                    worktree_path=wt_path_3,
                    completed_at=_utc_now(),
                ),
                "3.4": _make_story(status=StoryStatus.DONE, completed_at=_utc_now()),
                "3.5": _make_story(
                    status=StoryStatus.BLOCKED,
                    error="Exit code 1",
                    completed_at=_utc_now(),
                ),
                "3.6": _make_story(status=StoryStatus.BACKLOG),
            }
        )
        mock_list_wt.return_value = [
            _make_worktree_info(path=wt_path_1, branch="parallel/3-1"),
            _make_worktree_info(path=wt_path_3, branch="parallel/3-3"),
        ]

        result = recover_state(state, Path("/project"))

        assert result.stories["3.1"].status == StoryStatus.IN_FLIGHT  # preserved
        assert result.stories["3.2"].status == StoryStatus.BACKLOG    # reset
        assert result.stories["3.3"].status == StoryStatus.MERGING    # preserved
        assert result.stories["3.4"].status == StoryStatus.DONE       # preserved
        assert result.stories["3.5"].status == StoryStatus.BLOCKED    # preserved
        assert result.stories["3.6"].status == StoryStatus.BACKLOG    # preserved


# ============================================================================
# TestRecoverState — State persistence
# ============================================================================


class TestRecoverStatePersistence:
    """Test state persistence after recovery (AC #6)."""

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    @patch("bmad_assist_lite.parallel.recovery.get_parallel_state_path")
    def test_state_saved_after_recovery(
        self,
        mock_get_path: MagicMock,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """save_state is called after recovery reconciliation (AC #6)."""
        state_path = Path("/project/.bmad-assist-lite/parallel-state.yaml")
        mock_get_path.return_value = state_path
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=Path("/missing").resolve(),
                ),
            }
        )
        mock_list_wt.return_value = []

        result = recover_state(state, Path("/project"))

        mock_save.assert_called_once_with(result, state_path)


# ============================================================================
# TestRecoverState — Recovery summary logging
# ============================================================================


class TestRecoverStateLogging:
    """Test recovery logging and summary."""

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_logs_warning_for_reset_story(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warning is logged when an in-flight story is reset to backlog."""
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=Path("/missing").resolve(),
                ),
            }
        )
        mock_list_wt.return_value = []

        with caplog.at_level(logging.WARNING):
            recover_state(state, Path("/project"))

        assert any(
            "3.1" in r.message
            and "worktree missing" in r.message
            and "reset to backlog" in r.message
            for r in caplog.records
        )

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_logs_recovery_summary(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Recovery logs a summary of actions taken including temp file count."""
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=Path("/missing").resolve(),
                ),
                "3.2": _make_story(status=StoryStatus.DONE, completed_at=_utc_now()),
            }
        )
        mock_list_wt.return_value = []

        with caplog.at_level(logging.INFO):
            recover_state(state, Path("/project"))

        summary_records = [
            r for r in caplog.records
            if "reset" in r.message.lower() and "preserved" in r.message.lower()
        ]
        assert len(summary_records) >= 1
        # Verify temp file count is included in summary (Task 5.2)
        assert "temp files cleaned" in summary_records[0].message.lower()
        # Verify orphan count is included in summary (Story 5.2, Task 4.3)
        assert "orphaned worktrees cleaned" in summary_records[0].message.lower()


# ============================================================================
# TestCleanupTempFiles
# ============================================================================


class TestCleanupTempFiles:
    """Test temp file cleanup during recovery (AC #5)."""

    def test_removes_tmp_files(self, tmp_path: Path) -> None:
        """Orphaned *.tmp files are removed from .bmad-assist-lite/ (AC #5)."""
        bmad_dir = tmp_path / ".bmad-assist-lite"
        bmad_dir.mkdir()
        tmp_file = bmad_dir / "stale-data.tmp"
        tmp_file.write_text("stale data", encoding="utf-8")

        _cleanup_temp_files(tmp_path)

        assert not tmp_file.exists()

    def test_removes_tmp_files_in_cache_subdir(self, tmp_path: Path) -> None:
        """Temp files inside cache/ subdirectory are also removed (recursive)."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)
        tmp_file = cache_dir / "data.tmp"
        tmp_file.write_text("cache temp", encoding="utf-8")

        _cleanup_temp_files(tmp_path)

        assert not tmp_file.exists()

    def test_handles_os_error_gracefully(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OSError on unlink is caught and logged, not propagated (AC #5)."""
        bmad_dir = tmp_path / ".bmad-assist-lite"
        bmad_dir.mkdir()
        tmp_file = bmad_dir / "locked.tmp"
        tmp_file.write_text("data", encoding="utf-8")

        with patch.object(
            Path, "unlink", side_effect=OSError("Permission denied")
        ):
            with caplog.at_level(logging.WARNING):
                # Should not raise
                _cleanup_temp_files(tmp_path)

        assert any("locked.tmp" in r.message for r in caplog.records)

    def test_no_bmad_dir_is_noop(self, tmp_path: Path) -> None:
        """No error when .bmad-assist-lite/ directory does not exist."""
        # Should not raise
        _cleanup_temp_files(tmp_path)

    def test_preserves_non_tmp_files(self, tmp_path: Path) -> None:
        """Non-tmp files in .bmad-assist-lite/ are not removed."""
        bmad_dir = tmp_path / ".bmad-assist-lite"
        bmad_dir.mkdir()
        yaml_file = bmad_dir / "parallel-state.yaml"
        yaml_file.write_text("data", encoding="utf-8")
        tmp_file = bmad_dir / "stale.tmp"
        tmp_file.write_text("temp", encoding="utf-8")

        _cleanup_temp_files(tmp_path)

        assert yaml_file.exists()
        assert not tmp_file.exists()

    def test_logs_each_removed_file(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Each removed temp file is logged at warning level."""
        bmad_dir = tmp_path / ".bmad-assist-lite"
        bmad_dir.mkdir()
        tmp1 = bmad_dir / "file1.tmp"
        tmp2 = bmad_dir / "file2.tmp"
        tmp1.write_text("a", encoding="utf-8")
        tmp2.write_text("b", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            _cleanup_temp_files(tmp_path)

        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_msgs) >= 2  # noqa: PLR2004

    def test_returns_removed_count(self, tmp_path: Path) -> None:
        """_cleanup_temp_files returns the number of files removed."""
        bmad_dir = tmp_path / ".bmad-assist-lite"
        bmad_dir.mkdir()
        (bmad_dir / "a.tmp").write_text("a", encoding="utf-8")
        (bmad_dir / "b.tmp").write_text("b", encoding="utf-8")

        count = _cleanup_temp_files(tmp_path)

        assert count == 2  # noqa: PLR2004

    def test_returns_zero_when_no_bmad_dir(self, tmp_path: Path) -> None:
        """Returns 0 when .bmad-assist-lite/ does not exist."""
        count = _cleanup_temp_files(tmp_path)
        assert count == 0

    def test_rglob_oserror_handled_gracefully(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OSError during rglob scan is caught and logged, not propagated."""
        bmad_dir = tmp_path / ".bmad-assist-lite"
        bmad_dir.mkdir()

        with patch.object(
            Path, "rglob", side_effect=OSError("Permission denied")
        ):
            with caplog.at_level(logging.WARNING):
                count = _cleanup_temp_files(tmp_path)

        assert count == 0
        assert any("scan" in r.message.lower() for r in caplog.records)


# ============================================================================
# TestRecoverState — list_worktrees error handling
# ============================================================================


class TestRecoverStateListWorktreesError:
    """Test recovery gracefully handles list_worktrees failure.

    Also covers Task 6.10: list_worktrees failure skips orphan detection
    since the early return bypasses both reconciliation and orphan cleanup.
    """

    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_list_worktrees_error_returns_state_unchanged(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If list_worktrees fails, recovery logs error and returns state as-is."""
        from bmad_assist_lite.parallel.exceptions import ParallelError

        mock_list_wt.side_effect = ParallelError("git not found")
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=Path("/worktrees/parallel-3-1").resolve(),
                ),
            }
        )

        with caplog.at_level(logging.ERROR):
            result = recover_state(state, Path("/project"))

        # State should be returned unchanged
        assert result.stories["3.1"].status == StoryStatus.IN_FLIGHT
        assert any("recovery" in r.message.lower() for r in caplog.records)
        # save_state should NOT be called when list_worktrees fails
        mock_save.assert_not_called()


# ============================================================================
# TestOrchestratorRecoveryIntegration — Task 6 integration tests
# ============================================================================


class TestOrchestratorRecoveryIntegration:
    """Test orchestrator uses recovered state for in-memory sets (Task 6)."""

    @patch("bmad_assist_lite.parallel.orchestrator.recover_state")
    @patch("bmad_assist_lite.parallel.orchestrator.save_state")
    @patch("bmad_assist_lite.parallel.orchestrator.load_state")
    def test_orchestrator_calls_recover_state_on_existing_state(
        self,
        mock_load: MagicMock,
        mock_save: MagicMock,
        mock_recover: MagicMock,
    ) -> None:
        """Orchestrator calls recover_state when loading existing state (Task 6.1)."""
        from bmad_assist_lite.parallel.config import ParallelConfig
        from bmad_assist_lite.parallel.orchestrator import Orchestrator

        # Set up a persisted state with an in-flight story
        wt_path = Path("/worktrees/parallel-3-1").resolve()
        existing_state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=wt_path,
                    started_at=_utc_now(),
                ),
                "3.2": _make_story(status=StoryStatus.BACKLOG),
            }
        )
        mock_load.return_value = existing_state

        # recover_state should return the same state (worktree exists)
        mock_recover.return_value = existing_state

        graph = MagicMock()
        graph.all_story_ids = ["3.1", "3.2"]
        graph.story_count = 2

        config = ParallelConfig()
        orch = Orchestrator(
            dependency_graph=graph,
            config=config,
            project_root=Path("/project"),
            epic_num=3,
            resume=True,
        )

        # Verify recover_state was called with the loaded state
        mock_recover.assert_called_once_with(
            existing_state, Path("/project"), config.worktree_base_dir,
        )

    @patch("bmad_assist_lite.parallel.orchestrator.recover_state")
    @patch("bmad_assist_lite.parallel.orchestrator.save_state")
    @patch("bmad_assist_lite.parallel.orchestrator.load_state")
    def test_orchestrator_populates_sets_from_recovered_state(
        self,
        mock_load: MagicMock,
        mock_save: MagicMock,
        mock_recover: MagicMock,
    ) -> None:
        """In-memory sets populated from recovered state, not raw loaded state (Task 6.2)."""
        from bmad_assist_lite.parallel.config import ParallelConfig
        from bmad_assist_lite.parallel.orchestrator import Orchestrator

        # Raw state has in-flight story with missing worktree
        raw_state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=Path("/missing").resolve(),
                ),
                "3.2": _make_story(status=StoryStatus.DONE, completed_at=_utc_now()),
            }
        )
        mock_load.return_value = raw_state

        # Recovered state resets 3.1 to backlog
        recovered_state = _make_state(
            stories={
                "3.1": _make_story(status=StoryStatus.BACKLOG),
                "3.2": _make_story(status=StoryStatus.DONE, completed_at=_utc_now()),
            }
        )
        mock_recover.return_value = recovered_state

        graph = MagicMock()
        graph.all_story_ids = ["3.1", "3.2"]
        graph.story_count = 2

        orch = Orchestrator(
            dependency_graph=graph,
            config=ParallelConfig(),
            project_root=Path("/project"),
            epic_num=3,
            resume=True,
        )

        # 3.1 should NOT be in _in_flight_ids (it was reset to backlog)
        assert "3.1" not in orch._in_flight_ids
        # 3.2 should be in _done_ids
        assert "3.2" in orch._done_ids

    @patch("bmad_assist_lite.parallel.orchestrator.load_state", return_value=None)
    @patch("bmad_assist_lite.parallel.orchestrator.save_state")
    def test_orchestrator_fresh_state_skips_recovery(
        self,
        mock_save: MagicMock,
        mock_load: MagicMock,
    ) -> None:
        """When no existing state, orchestrator creates fresh state (no recovery)."""
        from bmad_assist_lite.parallel.config import ParallelConfig
        from bmad_assist_lite.parallel.orchestrator import Orchestrator

        graph = MagicMock()
        graph.all_story_ids = ["3.1"]
        graph.story_count = 1

        orch = Orchestrator(
            dependency_graph=graph,
            config=ParallelConfig(),
            project_root=Path("/project"),
            epic_num=3,
        )

        # Should have created fresh state with all backlog
        assert "3.1" not in orch._in_flight_ids
        assert "3.1" not in orch._done_ids


# ============================================================================
# TestSpawnStoryResume — Task 6.3
# ============================================================================


class TestSpawnStoryResume:
    """Test _spawn_story with resume=True skips worktree creation (Task 6.3)."""

    @patch("bmad_assist_lite.parallel.orchestrator.load_state", return_value=None)
    @patch("bmad_assist_lite.parallel.orchestrator.save_state")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_resume_skips_create_worktree(
        self,
        mock_exec: MagicMock,
        mock_save: MagicMock,
        mock_load: MagicMock,
    ) -> None:
        """resume=True skips create_worktree and uses existing worktree path."""
        from unittest.mock import AsyncMock

        from bmad_assist_lite.parallel.config import ParallelConfig
        from bmad_assist_lite.parallel.orchestrator import Orchestrator

        graph = MagicMock()
        graph.all_story_ids = ["3.1"]
        graph.story_count = 1

        orch = Orchestrator(
            dependency_graph=graph,
            config=ParallelConfig(stagger_delay=0.0),
            project_root=Path("/project"),
            epic_num=3,
        )
        # Inject mock output mux
        mux = MagicMock()
        mux.write_orchestrator = AsyncMock()
        mux.start_reader = MagicMock()
        mux.stop_reader = AsyncMock()
        mux.await_reader = AsyncMock()
        orch._output_mux = mux

        # Set up existing worktree path
        wt_path = Path("/existing/worktree")
        orch._story_worktrees["3.1"] = wt_path
        orch._in_flight_ids.add("3.1")

        # Mock subprocess
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        to_thread_path = "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        with patch(to_thread_path, new_callable=AsyncMock) as mock_to_thread:
            result = await orch._spawn_story("3.1", resume=True)

        # create_worktree should NOT have been called via to_thread
        mock_to_thread.assert_not_called()
        assert result == 0

    @patch("bmad_assist_lite.parallel.orchestrator.load_state", return_value=None)
    @patch("bmad_assist_lite.parallel.orchestrator.save_state")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_resume_appends_resume_flag(
        self,
        mock_exec: MagicMock,
        mock_save: MagicMock,
        mock_load: MagicMock,
    ) -> None:
        """resume=True appends --resume to CLI args."""
        from unittest.mock import AsyncMock

        from bmad_assist_lite.parallel.config import ParallelConfig
        from bmad_assist_lite.parallel.orchestrator import Orchestrator

        graph = MagicMock()
        graph.all_story_ids = ["3.1"]
        graph.story_count = 1

        orch = Orchestrator(
            dependency_graph=graph,
            config=ParallelConfig(stagger_delay=0.0),
            project_root=Path("/project"),
            epic_num=3,
        )
        mux = MagicMock()
        mux.write_orchestrator = AsyncMock()
        mux.start_reader = MagicMock()
        mux.stop_reader = AsyncMock()
        mux.await_reader = AsyncMock()
        orch._output_mux = mux

        wt_path = Path("/existing/worktree")
        orch._story_worktrees["3.1"] = wt_path
        orch._in_flight_ids.add("3.1")

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        await orch._spawn_story("3.1", resume=True)

        # Check that --resume was passed
        call_args = mock_exec.call_args[0]
        assert "--resume" in call_args

    @patch("bmad_assist_lite.parallel.orchestrator.load_state", return_value=None)
    @patch("bmad_assist_lite.parallel.orchestrator.save_state")
    async def test_resume_no_worktree_path_returns_error(
        self,
        mock_save: MagicMock,
        mock_load: MagicMock,
    ) -> None:
        """resume=True with no worktree path returns -1."""
        from unittest.mock import AsyncMock

        from bmad_assist_lite.parallel.config import ParallelConfig
        from bmad_assist_lite.parallel.orchestrator import Orchestrator

        graph = MagicMock()
        graph.all_story_ids = ["3.1"]
        graph.story_count = 1

        orch = Orchestrator(
            dependency_graph=graph,
            config=ParallelConfig(stagger_delay=0.0),
            project_root=Path("/project"),
            epic_num=3,
        )
        mux = MagicMock()
        mux.write_orchestrator = AsyncMock()
        mux.start_reader = MagicMock()
        mux.stop_reader = AsyncMock()
        mux.await_reader = AsyncMock()
        orch._output_mux = mux

        # No worktree path in _story_worktrees or state
        orch._in_flight_ids.add("3.1")

        result = await orch._spawn_story("3.1", resume=True)

        assert result == -1


# ============================================================================
# TestRunResumeDetection — Task 6.3 run() integration
# ============================================================================


class TestRunResumeDetection:
    """Test run() detects in-flight stories without running tasks (Task 6.3)."""

    @patch("bmad_assist_lite.parallel.orchestrator.teardown_parallel_log")
    @patch("bmad_assist_lite.parallel.orchestrator.setup_parallel_log")
    @patch("bmad_assist_lite.parallel.orchestrator.load_state", return_value=None)
    @patch("bmad_assist_lite.parallel.orchestrator.save_state")
    async def test_run_respawns_stale_in_flight_stories(
        self,
        mock_save: MagicMock,
        mock_load: MagicMock,
        mock_setup_log: MagicMock,
        mock_teardown_log: MagicMock,
    ) -> None:
        """run() detects in_flight stories with no running task and re-spawns."""
        from unittest.mock import AsyncMock

        from bmad_assist_lite.parallel.config import ParallelConfig
        from bmad_assist_lite.parallel.orchestrator import Orchestrator

        graph = MagicMock()
        graph.all_story_ids = ["3.1"]
        graph.story_count = 1
        graph.get_ready_stories = MagicMock(return_value=[])

        orch = Orchestrator(
            dependency_graph=graph,
            config=ParallelConfig(stagger_delay=0.0),
            project_root=Path("/project"),
            epic_num=3,
        )
        mux = MagicMock()
        mux.write_orchestrator = AsyncMock()
        mux.start_reader = MagicMock()
        mux.stop_reader = AsyncMock()
        mux.stop_all = AsyncMock()
        mux.await_reader = AsyncMock()
        orch._output_mux = mux

        # Simulate recovered state: in_flight but no running task
        orch._in_flight_ids.add("3.1")
        orch._story_worktrees["3.1"] = Path("/existing/worktree")

        with patch.object(
            orch, "_spawn_story", new_callable=AsyncMock
        ) as mock_spawn:
            mock_spawn.return_value = 0

            with patch.object(
                orch, "_on_story_complete", new_callable=AsyncMock
            ) as mock_complete:
                async def complete_side_effect(sid: str, code: int) -> None:
                    orch._merging_ids.add(sid)
                    orch._in_flight_ids.discard(sid)
                    task = orch._running_tasks.pop(sid, None)
                    if task:
                        orch._task_to_story.pop(task, None)

                mock_complete.side_effect = complete_side_effect
                await orch.run()

        # Verify _spawn_story was called with resume=True
        assert mock_spawn.call_count >= 1
        # Check the first call had resume=True
        first_call = mock_spawn.call_args_list[0]
        assert first_call == (("3.1",), {"resume": True})


# ============================================================================
# Story 5.2 — Orphan Detection & Worktree Pruning
# ============================================================================


# ============================================================================
# TestPruneAndCleanOrphanedWorktrees — Direct unit tests (Tasks 6.2-6.8, 6.12-6.14)
# ============================================================================


class TestPruneAndCleanOrphanedWorktrees:
    """Test orphan detection and cleanup logic."""

    def test_orphan_no_state_record_cleaned(self) -> None:
        """Worktree with parallel/ branch and no state record is orphaned (Task 6.2)."""
        state = _make_state(stories={})  # No stories at all
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            count = prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees,
            )

        assert count == 1
        mock_cleanup.assert_called_once_with("3.1", Path("/project"), None)

    def test_orphan_done_status_cleaned(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Worktree with parallel/ branch and done status is orphaned (Task 6.3)."""
        state = _make_state(
            stories={
                "3.2": _make_story(
                    status=StoryStatus.DONE, completed_at=_utc_now(),
                ),
            }
        )
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-2").resolve(),
                branch="parallel/3-2",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            with caplog.at_level(logging.WARNING):
                count = prune_and_clean_orphaned_worktrees(
                    state, Path("/project"), worktrees,
                )

        assert count == 1
        mock_cleanup.assert_called_once_with("3.2", Path("/project"), None)
        # Task 3.3: Verify warning logged with reason
        assert any(
            "3.2" in r.message and "done" in r.message
            for r in caplog.records
        )

    def test_in_flight_not_orphaned(self) -> None:
        """Worktree for in_flight story is NOT cleaned up (Task 6.4)."""
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=Path("/worktrees/parallel-3-1").resolve(),
                    started_at=_utc_now(),
                ),
            }
        )
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            count = prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees,
            )

        assert count == 0
        mock_cleanup.assert_not_called()

    def test_merging_not_orphaned(self) -> None:
        """Worktree for merging story is NOT cleaned up (Task 6.5)."""
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.MERGING,
                    worktree_path=Path("/worktrees/parallel-3-1").resolve(),
                    completed_at=_utc_now(),
                ),
            }
        )
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            count = prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees,
            )

        assert count == 0
        mock_cleanup.assert_not_called()

    def test_blocked_not_orphaned(self) -> None:
        """Worktree for blocked story is NOT cleaned up (Task 6.6)."""
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.BLOCKED,
                    error="Exit code 1",
                    completed_at=_utc_now(),
                ),
            }
        )
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            count = prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees,
            )

        assert count == 0
        mock_cleanup.assert_not_called()

    def test_backlog_not_orphaned(self) -> None:
        """Worktree for backlog story is NOT cleaned up."""
        state = _make_state(
            stories={
                "3.1": _make_story(status=StoryStatus.BACKLOG),
            }
        )
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            count = prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees,
            )

        assert count == 0
        mock_cleanup.assert_not_called()

    def test_non_parallel_branch_skipped(self) -> None:
        """Worktree with non-parallel/ branch is skipped entirely (Task 6.7)."""
        state = _make_state(stories={})
        worktrees = [
            _make_worktree_info(
                path=Path("/project").resolve(), branch="main",
            ),
            _make_worktree_info(
                path=Path("/worktrees/feature-foo").resolve(),
                branch="feature/foo",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            count = prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees,
            )

        assert count == 0
        mock_cleanup.assert_not_called()

    def test_cleanup_failure_continues_to_next_orphan(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """cleanup_worktree failure is caught; remaining orphans still processed (Task 6.8)."""
        state = _make_state(stories={})  # No stories — all parallel are orphans
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-2").resolve(),
                branch="parallel/3-2",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            # First call fails, second succeeds
            mock_cleanup.side_effect = [RuntimeError("locked"), None]
            with caplog.at_level(logging.WARNING):
                count = prune_and_clean_orphaned_worktrees(
                    state, Path("/project"), worktrees,
                )

        # Only one succeeded
        assert count == 1
        # Both were attempted
        assert mock_cleanup.call_count == 2  # noqa: PLR2004
        # Failure was logged
        assert any("3.1" in r.message and "locked" in r.message for r in caplog.records)
        # Success was logged (Task 3.3: warning for each orphan cleaned)
        assert any(
            "3.2" in r.message and "no_state_record" in r.message
            for r in caplog.records
        )

    def test_empty_worktree_list_returns_zero(self) -> None:
        """Empty worktree list → no orphans, returns 0 (Task 6.12)."""
        state = _make_state(
            stories={"3.1": _make_story(status=StoryStatus.IN_FLIGHT)},
        )

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            count = prune_and_clean_orphaned_worktrees(
                state, Path("/project"), [],
            )

        assert count == 0
        mock_cleanup.assert_not_called()

    def test_all_parallel_worktrees_are_orphans(self) -> None:
        """All parallel worktrees are orphans → all cleaned, correct count (Task 6.13)."""
        state = _make_state(stories={})  # Empty state
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-2").resolve(),
                branch="parallel/3-2",
            ),
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-3").resolve(),
                branch="parallel/3-3",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            count = prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees,
            )

        assert count == 3  # noqa: PLR2004
        assert mock_cleanup.call_count == 3  # noqa: PLR2004

    def test_branch_to_story_id_reverse_mapping(self) -> None:
        """Verify parallel/3-4 correctly maps to story ID 3.4 (Task 6.14)."""
        state = _make_state(stories={})
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-4").resolve(),
                branch="parallel/3-4",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees,
            )

        # Verify the story_id passed to cleanup_worktree is "3.4"
        mock_cleanup.assert_called_once_with("3.4", Path("/project"), None)

    def test_non_standard_branch_name_skipped(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Branch like parallel/foo-bar is skipped with warning (Task 2.1 validation)."""
        state = _make_state(stories={})
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-foo-bar").resolve(),
                branch="parallel/foo-bar",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            with caplog.at_level(logging.WARNING):
                count = prune_and_clean_orphaned_worktrees(
                    state, Path("/project"), worktrees,
                )

        assert count == 0
        mock_cleanup.assert_not_called()
        assert any(
            "non-standard" in r.message and "foo-bar" in r.message
            for r in caplog.records
        )

    def test_none_branch_worktree_skipped(self) -> None:
        """Worktree with branch=None is skipped."""
        state = _make_state(stories={})
        worktrees = [
            _make_worktree_info(
                path=Path("/detached-head").resolve(), branch=None,
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            count = prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees,
            )

        assert count == 0
        mock_cleanup.assert_not_called()

    def test_base_dir_passed_through(self) -> None:
        """base_dir parameter is passed through to cleanup_worktree."""
        state = _make_state(stories={})
        base_dir = Path("/custom/base")
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
        ]

        with patch(
            "bmad_assist_lite.parallel.recovery.cleanup_worktree"
        ) as mock_cleanup:
            prune_and_clean_orphaned_worktrees(
                state, Path("/project"), worktrees, base_dir,
            )

        mock_cleanup.assert_called_once_with("3.1", Path("/project"), base_dir)

    def test_orphan_no_state_record_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Orphan with no state record logs warning with correct reason."""
        state = _make_state(stories={})
        worktrees = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
        ]

        with patch("bmad_assist_lite.parallel.recovery.cleanup_worktree"):
            with caplog.at_level(logging.WARNING):
                prune_and_clean_orphaned_worktrees(
                    state, Path("/project"), worktrees,
                )

        assert any(
            "3.1" in r.message and "no_state_record" in r.message
            for r in caplog.records
        )


# ============================================================================
# TestRecoverStatePruneWorktreesOrder — Task 6.1, 6.9
# ============================================================================


class TestRecoverStatePruneWorktreesOrder:
    """Test prune_worktrees is called before list_worktrees in recover_state."""

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    def test_prune_called_before_list(
        self,
        mock_prune: MagicMock,
        mock_list_wt: MagicMock,
        mock_save: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """prune_worktrees() is called before list_worktrees() (Task 6.1)."""
        call_order: list[str] = []
        mock_prune.side_effect = lambda *a, **kw: call_order.append("prune")
        mock_list_wt.side_effect = lambda *a, **kw: (
            call_order.append("list") or []  # type: ignore[func-returns-value]
        )

        state = _make_state(stories={})
        recover_state(state, Path("/project"))

        assert call_order == ["prune", "list"]

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    def test_prune_failure_does_not_prevent_list(
        self,
        mock_prune: MagicMock,
        mock_list_wt: MagicMock,
        mock_save: MagicMock,
        mock_cleanup: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """prune_worktrees failure is caught; list_worktrees still proceeds (Task 6.9)."""
        from bmad_assist_lite.parallel.exceptions import ParallelError

        mock_prune.side_effect = ParallelError("git worktree prune failed")
        mock_list_wt.return_value = []

        state = _make_state(stories={})

        with caplog.at_level(logging.WARNING):
            result = recover_state(state, Path("/project"))

        # list_worktrees was still called
        mock_list_wt.assert_called_once()
        # Prune failure was logged
        assert any("prune" in r.message.lower() for r in caplog.records)
        # Recovery completed normally
        assert result.stories == {}


# ============================================================================
# TestRecoverStateOrphanIntegration — Task 6.11
# ============================================================================


class TestRecoverStateOrphanIntegration:
    """Test recover_state() includes orphan count in summary log."""

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_recovery_summary_includes_orphan_count(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Recovery summary includes orphan cleanup count (Task 6.11)."""
        state = _make_state(
            stories={
                "3.1": _make_story(status=StoryStatus.DONE, completed_at=_utc_now()),
            }
        )
        # Worktree exists for done story → orphan
        mock_list_wt.return_value = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
        ]

        with caplog.at_level(logging.INFO):
            recover_state(state, Path("/project"))

        # Find the summary record
        summary_records = [
            r for r in caplog.records
            if "recovery complete" in r.message.lower()
        ]
        assert len(summary_records) >= 1
        assert "orphaned" in summary_records[0].message.lower()
        assert "cleaned" in summary_records[0].message.lower()
        # Verify count: 1 orphan cleaned (singular grammar)
        assert "1 orphaned worktree cleaned" in summary_records[0].message

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_orphan_detection_uses_reconciled_state(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Orphan detection runs on reconciled state, not original state."""
        # Story is in_flight with missing worktree → will be reset to backlog
        # Worktree list includes the parallel branch for this story
        wt_path = Path("/worktrees/parallel-3-1").resolve()
        state = _make_state(
            stories={
                "3.1": _make_story(
                    status=StoryStatus.IN_FLIGHT,
                    worktree_path=Path("/other/missing").resolve(),
                    started_at=_utc_now(),
                ),
            }
        )
        mock_list_wt.return_value = [
            _make_worktree_info(path=wt_path, branch="parallel/3-1"),
        ]

        recover_state(state, Path("/project"))

        # After reconciliation, story 3.1 is reset to backlog
        # As a backlog story, it should NOT be orphaned
        mock_cleanup.assert_not_called()

    @patch("bmad_assist_lite.parallel.recovery.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.recovery.prune_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_worktree_base_dir_passed_to_orphan_cleanup(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
        mock_prune: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """worktree_base_dir is passed through to orphan cleanup (Task 4.4)."""
        state = _make_state(stories={})
        base_dir = Path("/custom/worktree/base")
        mock_list_wt.return_value = [
            _make_worktree_info(
                path=Path("/worktrees/parallel-3-1").resolve(),
                branch="parallel/3-1",
            ),
        ]

        recover_state(state, Path("/project"), worktree_base_dir=base_dir)

        mock_cleanup.assert_called_once_with("3.1", Path("/project"), base_dir)
