"""Comprehensive tests for crash recovery and resume in-flight stories."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.parallel.recovery import recover_state, _cleanup_temp_files
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_in_flight_with_existing_worktree_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_in_flight_with_missing_worktree_reset_to_backlog(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_in_flight_with_none_worktree_path_reset_to_backlog(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_done_stories_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_blocked_stories_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_merging_with_missing_worktree_reset_to_backlog(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_merging_with_existing_worktree_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_backlog_stories_preserved(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_empty_stories_dict(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
    ) -> None:
        """Recovery with empty stories dict is a no-op (edge case)."""
        state = _make_state(stories={})
        mock_list_wt.return_value = []

        result = recover_state(state, Path("/project"))

        assert result.stories == {}

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_all_stories_done(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_mixed_statuses_reconciled_correctly(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    @patch("bmad_assist_lite.parallel.recovery.get_parallel_state_path")
    def test_state_saved_after_recovery(
        self,
        mock_get_path: MagicMock,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_logs_warning_for_reset_story(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_logs_recovery_summary(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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
    """Test recovery gracefully handles list_worktrees failure."""

    @patch("bmad_assist_lite.parallel.recovery.list_worktrees")
    @patch("bmad_assist_lite.parallel.recovery.save_state")
    def test_list_worktrees_error_returns_state_unchanged(
        self,
        mock_save: MagicMock,
        mock_list_wt: MagicMock,
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

    @patch("bmad_assist_lite.parallel.orchestrator.load_state", return_value=None)
    @patch("bmad_assist_lite.parallel.orchestrator.save_state")
    async def test_run_respawns_stale_in_flight_stories(
        self,
        mock_save: MagicMock,
        mock_load: MagicMock,
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
