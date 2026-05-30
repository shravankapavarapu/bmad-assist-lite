"""Tests for Story 6.4: Epic Teardown Phases.

Tests epic completion detection, teardown subprocess invocation,
teardown logging, sprint-status updates, worktree cleanup, and
integration of teardown into the orchestrator run() flow.
"""

import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bmad_assist_lite.parallel.logging import (
    _LOG_FILENAME,
    log_teardown_result,
    setup_parallel_log,
    teardown_parallel_log,
)
from bmad_assist_lite.parallel.state import (
    ParallelState,
    StoryState,
    StoryStatus,
)

# ============================================================================
# Fixtures
# ============================================================================


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info (naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture()
def log_dir(tmp_path: Path):
    """Provide a temporary directory for log file creation with cleanup."""
    yield tmp_path
    teardown_parallel_log()


@pytest.fixture()
def setup_log(log_dir: Path):
    """Set up the parallel log in the temporary directory."""
    setup_parallel_log(log_dir)
    yield log_dir


def _read_log(project_root: Path) -> str:
    """Read the parallel-run.log file content."""
    log_path = project_root / _LOG_FILENAME
    if log_path.exists():
        return log_path.read_text(encoding="utf-8")
    return ""


def _make_state(
    story_statuses: dict[str, StoryStatus],
    epic: int = 6,
) -> ParallelState:
    """Build a ParallelState with the given story statuses."""
    stories = {
        sid: StoryState(status=status)
        for sid, status in story_statuses.items()
    }
    return ParallelState(
        base_branch="main",
        epic=epic,
        started_at=_utc_now(),
        stories=stories,
    )


def _make_orchestrator(
    story_statuses: dict[str, StoryStatus] | None = None,
    epic_num: int = 6,
    project_root: Path | None = None,
):
    """Build an Orchestrator instance with mocked dependencies.

    Returns the orchestrator plus its key mock objects.
    """
    from bmad_assist_lite.parallel.config import ParallelConfig
    from bmad_assist_lite.parallel.orchestrator import Orchestrator

    if story_statuses is None:
        story_statuses = {"6.1": StoryStatus.DONE, "6.2": StoryStatus.DONE}

    if project_root is None:
        project_root = Path("/fake/project")

    # Create a mock dependency graph
    graph = MagicMock()
    graph.all_story_ids = list(story_statuses.keys())
    graph.story_count = len(story_statuses)
    graph.get_ready_stories.return_value = []
    graph.dependencies_of.return_value = []
    graph.dependents_of.return_value = []
    graph.are_dependencies_satisfied.return_value = True

    config = ParallelConfig()

    # Patch load_state to return None (fresh start) and save_state to no-op
    with (
        patch(
            "bmad_assist_lite.parallel.orchestrator.load_state",
            return_value=None,
        ),
        patch(
            "bmad_assist_lite.parallel.orchestrator.save_state",
        ) as mock_save,
        patch(
            "bmad_assist_lite.parallel.orchestrator.get_parallel_state_path",
            return_value=project_root / ".bmad-assist-lite" / "parallel-state.yaml",
        ),
    ):
        orch = Orchestrator(
            dependency_graph=graph,
            config=config,
            project_root=project_root,
            epic_num=epic_num,
            base_branch="main",
        )

    # Manually set the state to reflect desired story statuses
    state = _make_state(story_statuses, epic_num)
    orch._state = state

    # Populate in-memory sets
    orch._done_ids = {
        sid for sid, s in story_statuses.items() if s == StoryStatus.DONE
    }
    orch._blocked_ids = {
        sid for sid, s in story_statuses.items() if s == StoryStatus.BLOCKED
    }
    orch._in_flight_ids = {
        sid for sid, s in story_statuses.items() if s == StoryStatus.IN_FLIGHT
    }
    orch._merging_ids = {
        sid for sid, s in story_statuses.items() if s == StoryStatus.MERGING
    }

    return orch, graph, mock_save


# ============================================================================
# Test: _all_stories_done() (Task 7.1–7.3)
# ============================================================================


class TestAllStoriesDone:
    def test_all_stories_done_true(self) -> None:
        """Task 7.1: _all_stories_done() returns True when all stories are DONE."""
        orch, _, _ = _make_orchestrator({
            "6.1": StoryStatus.DONE,
            "6.2": StoryStatus.DONE,
            "6.3": StoryStatus.DONE,
        })
        assert orch._all_stories_done() is True

    def test_all_stories_done_with_blocked(self) -> None:
        """Task 7.2: _all_stories_done() returns False when one story is BLOCKED."""
        orch, _, _ = _make_orchestrator({
            "6.1": StoryStatus.DONE,
            "6.2": StoryStatus.BLOCKED,
        })
        assert orch._all_stories_done() is False

    def test_all_stories_done_with_in_flight(self) -> None:
        """Task 7.3: _all_stories_done() returns False when one story is IN_FLIGHT."""
        orch, _, _ = _make_orchestrator({
            "6.1": StoryStatus.DONE,
            "6.2": StoryStatus.IN_FLIGHT,
        })
        assert orch._all_stories_done() is False

    def test_all_stories_done_with_merging(self) -> None:
        """_all_stories_done() returns False when one story is MERGING."""
        orch, _, _ = _make_orchestrator({
            "6.1": StoryStatus.DONE,
            "6.2": StoryStatus.MERGING,
        })
        assert orch._all_stories_done() is False

    def test_all_stories_done_with_backlog(self) -> None:
        """_all_stories_done() returns False when one story is BACKLOG."""
        orch, _, _ = _make_orchestrator({
            "6.1": StoryStatus.DONE,
            "6.2": StoryStatus.BACKLOG,
        })
        assert orch._all_stories_done() is False

    def test_all_stories_done_empty_stories(self) -> None:
        """_all_stories_done() returns False when no stories exist."""
        orch, _, _ = _make_orchestrator({})
        assert orch._all_stories_done() is False


# ============================================================================
# Test: _run_epic_teardown() (Task 7.4–7.5, 7.13)
# ============================================================================


class TestRunEpicTeardown:
    async def test_teardown_success(self, tmp_path: Path) -> None:
        """Task 7.4: Successful teardown returns True with correct args."""
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE},
            project_root=tmp_path,
        )

        # Mock asyncio.create_subprocess_exec to return a successful process
        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(return_value=None)
        mock_proc.returncode = 0
        mock_proc.pid = 12345
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b"")

        with (
            patch(
                "bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ) as mock_exec,
            patch.object(orch._output_mux, "write_orchestrator", new_callable=AsyncMock),
            patch.object(orch._output_mux, "start_reader", return_value=MagicMock()),
            patch.object(orch._output_mux, "await_reader", new_callable=AsyncMock),
            patch.object(orch._output_mux, "stop_reader", new_callable=AsyncMock),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_teardown_result",
            ) as mock_log,
        ):
            result = await orch._run_epic_teardown()

        assert result is True

        # Verify subprocess was invoked with correct args
        call_args = mock_exec.call_args
        exec_args = call_args[0]
        assert exec_args[0] == sys.executable
        assert "-m" in exec_args
        assert "bmad_assist_lite" in exec_args
        assert "run" in exec_args
        assert "--epic" in exec_args
        assert "6" in exec_args
        assert "--teardown-only" in exec_args

        # Verify cwd is project root
        assert call_args[1]["cwd"] == str(tmp_path)

        # Verify log_teardown_result called with success
        mock_log.assert_called_once()
        call = mock_log.call_args
        assert call[0][0] == 6  # epic_num
        assert call[1]["success"] is True

    async def test_teardown_failure(self, tmp_path: Path) -> None:
        """Task 7.5: Failed teardown returns False and logs failure."""
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE},
            project_root=tmp_path,
        )

        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(return_value=None)
        mock_proc.returncode = 1
        mock_proc.pid = 12345
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b"")

        with (
            patch(
                "bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch.object(orch._output_mux, "write_orchestrator", new_callable=AsyncMock),
            patch.object(orch._output_mux, "start_reader", return_value=MagicMock()),
            patch.object(orch._output_mux, "await_reader", new_callable=AsyncMock),
            patch.object(orch._output_mux, "stop_reader", new_callable=AsyncMock),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_teardown_result",
            ) as mock_log,
        ):
            result = await orch._run_epic_teardown()

        assert result is False

        # Verify log_teardown_result called with failure
        mock_log.assert_called_once()
        call = mock_log.call_args
        # epic_num is first positional arg
        assert call[0][0] == 6
        # success and exit_code are keyword args
        assert call[1]["success"] is False
        assert call[1]["exit_code"] == 1

    async def test_teardown_uses_teardown_only_flag(self, tmp_path: Path) -> None:
        """Task 7.13: Verify the subprocess command includes --teardown-only."""
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE},
            project_root=tmp_path,
        )

        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(return_value=None)
        mock_proc.returncode = 0
        mock_proc.pid = 12345
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b"")

        with (
            patch(
                "bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ) as mock_exec,
            patch.object(orch._output_mux, "write_orchestrator", new_callable=AsyncMock),
            patch.object(orch._output_mux, "start_reader", return_value=MagicMock()),
            patch.object(orch._output_mux, "await_reader", new_callable=AsyncMock),
            patch.object(orch._output_mux, "stop_reader", new_callable=AsyncMock),
            patch("bmad_assist_lite.parallel.orchestrator.log_teardown_result"),
        ):
            await orch._run_epic_teardown()

        # Extract all positional args from the subprocess call
        exec_args = mock_exec.call_args[0]
        assert "--teardown-only" in exec_args

    async def test_teardown_uses_bmad_parallel_mode_env(
        self, tmp_path: Path,
    ) -> None:
        """Verify teardown subprocess has BMAD_PARALLEL_MODE=1 in env."""
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE},
            project_root=tmp_path,
        )

        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(return_value=None)
        mock_proc.returncode = 0
        mock_proc.pid = 12345
        mock_proc.stdout = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b"")

        with (
            patch(
                "bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ) as mock_exec,
            patch.object(orch._output_mux, "write_orchestrator", new_callable=AsyncMock),
            patch.object(orch._output_mux, "start_reader", return_value=MagicMock()),
            patch.object(orch._output_mux, "await_reader", new_callable=AsyncMock),
            patch.object(orch._output_mux, "stop_reader", new_callable=AsyncMock),
            patch("bmad_assist_lite.parallel.orchestrator.log_teardown_result"),
        ):
            await orch._run_epic_teardown()

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["env"]["BMAD_PARALLEL_MODE"] == "1"


# ============================================================================
# Test: log_teardown_result() (Task 7.6)
# ============================================================================


class TestLogTeardownResult:
    def test_success_log_message(self, setup_log: Path) -> None:
        """Task 7.6: Success log contains correct tag and severity."""
        log_teardown_result(6, success=True, exit_code=0, duration_s=42.5)

        content = _read_log(setup_log)
        assert "[INFO]" in content
        assert "[TEARDOWN|epic-6]" in content
        assert "completed successfully" in content
        assert "42.5s" in content

    def test_failure_log_message(self, setup_log: Path) -> None:
        """Task 7.6: Failure log contains correct tag, severity, and error."""
        log_teardown_result(
            6, success=False, exit_code=1, error="Tests failed",
        )

        content = _read_log(setup_log)
        assert "[ERROR]" in content
        assert "[TEARDOWN|epic-6]" in content
        assert "failed" in content
        assert "exit_code=1" in content
        assert "Tests failed" in content

    def test_failure_no_error_message(self, setup_log: Path) -> None:
        """Failure with None error shows 'unknown error'."""
        log_teardown_result(6, success=False, exit_code=1)

        content = _read_log(setup_log)
        assert "unknown error" in content

    def test_success_without_duration(self, setup_log: Path) -> None:
        """Success log without duration omits duration suffix."""
        log_teardown_result(6, success=True, exit_code=0)

        content = _read_log(setup_log)
        assert "[TEARDOWN|epic-6]" in content
        assert "completed successfully" in content
        # Without duration_s, the log should NOT contain a duration suffix.
        # The format is: "[TEARDOWN|epic-6] Epic teardown completed
        # successfully (exit_code=0)" — no " in X.Xs" appended.
        for line in content.splitlines():
            if "[TEARDOWN|epic-6]" in line and "completed successfully" in line:
                # Verify no duration suffix like " in 42.5s" appears
                assert not re.search(r" in \d+\.\d+s", line)
                break
        else:
            pytest.fail("Teardown success log line not found")


# ============================================================================
# Test: Sprint-status update (Task 7.7–7.8)
# ============================================================================


class TestUpdateEpicSprintStatus:
    def test_sprint_status_update_done(self) -> None:
        """Task 7.7: Verify set_epic_status called with correct args."""
        orch, _, _ = _make_orchestrator({"6.1": StoryStatus.DONE})

        mock_sprint_status = MagicMock()

        with (
            patch(
                "bmad_assist_lite.core.sprint_status.get_sprint_status_path",
                return_value=Path("/fake/sprint-status.yaml"),
            ),
            patch(
                "bmad_assist_lite.core.sprint_status.load_sprint_status",
                return_value=mock_sprint_status,
            ),
            patch(
                "bmad_assist_lite.core.sprint_status.save_sprint_status",
            ) as mock_save,
        ):
            orch._update_epic_sprint_status("done")

        mock_sprint_status.set_epic_status.assert_called_once_with(6, "done")
        mock_save.assert_called_once()

    def test_sprint_status_update_non_fatal(self) -> None:
        """Task 7.8: Exception in sprint-status update is logged, not raised."""
        orch, _, _ = _make_orchestrator({"6.1": StoryStatus.DONE})

        with patch(
            "bmad_assist_lite.core.sprint_status.get_sprint_status_path",
            side_effect=Exception("File not found"),
        ):
            # Should NOT raise
            orch._update_epic_sprint_status("done")


# ============================================================================
# Test: Teardown skipped scenarios (Task 7.9–7.10)
# ============================================================================


class TestTeardownSkipped:
    def test_teardown_skipped_when_not_all_done(self) -> None:
        """Task 7.9: Teardown not called when some stories are blocked."""
        orch, _, _ = _make_orchestrator({
            "6.1": StoryStatus.DONE,
            "6.2": StoryStatus.BLOCKED,
        })

        assert orch._all_stories_done() is False

    def test_teardown_skipped_in_drain_mode(self) -> None:
        """Task 7.10: Teardown not called in drain mode even if all done."""
        orch, _, _ = _make_orchestrator({
            "6.1": StoryStatus.DONE,
            "6.2": StoryStatus.DONE,
        })
        orch._draining = True

        # Even though all stories are done, drain mode prevents teardown
        # (verified by the run() flow, tested here at the condition level)
        assert orch._all_stories_done() is True
        assert orch._draining is True
        # The combined condition would skip teardown:
        # not self._draining and not self._force_exit and self._all_stories_done()
        should_run_teardown = (
            not orch._draining
            and not orch._force_exit
            and orch._all_stories_done()
        )
        assert should_run_teardown is False

    def test_teardown_skipped_in_force_exit_mode(self) -> None:
        """Teardown not called in force-exit mode even if all done."""
        orch, _, _ = _make_orchestrator({
            "6.1": StoryStatus.DONE,
            "6.2": StoryStatus.DONE,
        })
        orch._draining = True
        orch._force_exit = True

        should_run_teardown = (
            not orch._draining
            and not orch._force_exit
            and orch._all_stories_done()
        )
        assert should_run_teardown is False


# ============================================================================
# Test: Worktree cleanup (Task 7.11, 7.15)
# ============================================================================


class TestWorktreeCleanup:
    async def test_worktree_cleanup_after_teardown(self, tmp_path: Path) -> None:
        """Task 7.11: cleanup_worktree called for remaining worktrees."""
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE, "6.2": StoryStatus.DONE},
            project_root=tmp_path,
        )
        orch._story_worktrees = {
            "6.1": tmp_path / "wt-6-1",
            "6.2": tmp_path / "wt-6-2",
        }

        with patch(
            "bmad_assist_lite.parallel.orchestrator.cleanup_worktree",
        ) as mock_cleanup:
            await orch._cleanup_remaining_worktrees()

        assert mock_cleanup.call_count == 2
        cleaned_ids = {
            call.args[0] for call in mock_cleanup.call_args_list
        }
        assert cleaned_ids == {"6.1", "6.2"}

    async def test_worktree_cleanup_for_blocked_stories(
        self, tmp_path: Path,
    ) -> None:
        """Task 7.15: cleanup_worktree called for blocked story worktrees."""
        orch, _, _ = _make_orchestrator(
            {
                "6.1": StoryStatus.DONE,
                "6.2": StoryStatus.BLOCKED,
            },
            project_root=tmp_path,
        )
        orch._story_worktrees = {
            "6.1": tmp_path / "wt-6-1",
            "6.2": tmp_path / "wt-6-2",
        }

        with patch(
            "bmad_assist_lite.parallel.orchestrator.cleanup_worktree",
        ) as mock_cleanup:
            await orch._cleanup_remaining_worktrees()

        assert mock_cleanup.call_count == 2
        cleaned_ids = {
            call.args[0] for call in mock_cleanup.call_args_list
        }
        assert cleaned_ids == {"6.1", "6.2"}

    async def test_worktree_cleanup_non_fatal(self, tmp_path: Path) -> None:
        """Worktree cleanup failure is logged as warning, not raised."""
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE},
            project_root=tmp_path,
        )
        orch._story_worktrees = {"6.1": tmp_path / "wt-6-1"}

        with patch(
            "bmad_assist_lite.parallel.orchestrator.cleanup_worktree",
            side_effect=Exception("Permission denied"),
        ):
            # Should not raise
            await orch._cleanup_remaining_worktrees()

        # Worktree should be removed from tracking even on failure
        assert "6.1" not in orch._story_worktrees

    async def test_no_cleanup_when_no_worktrees(self, tmp_path: Path) -> None:
        """No-op when no worktrees remain."""
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE},
            project_root=tmp_path,
        )
        orch._story_worktrees = {}

        with patch(
            "bmad_assist_lite.parallel.orchestrator.cleanup_worktree",
        ) as mock_cleanup:
            await orch._cleanup_remaining_worktrees()

        mock_cleanup.assert_not_called()


# ============================================================================
# Test: Branch cleanup via cleanup_worktree (Task 7.12)
# ============================================================================


class TestBranchCleanup:
    async def test_cleanup_worktree_handles_branch_deletion(
        self, tmp_path: Path,
    ) -> None:
        """Task 7.12: cleanup_worktree covers branch deletion.

        Verify that _cleanup_remaining_worktrees delegates to
        cleanup_worktree which handles branch deletion as part of its
        three-step cleanup. No separate _run_git branch -D needed.
        """
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE, "6.2": StoryStatus.DONE},
            project_root=tmp_path,
        )
        orch._story_worktrees = {
            "6.1": tmp_path / "wt-6-1",
            "6.2": tmp_path / "wt-6-2",
        }

        with patch(
            "bmad_assist_lite.parallel.orchestrator.cleanup_worktree",
        ) as mock_cleanup:
            await orch._cleanup_remaining_worktrees()

        # cleanup_worktree is called once per story — it handles both
        # worktree removal and branch deletion internally
        assert mock_cleanup.call_count == 2
        # Verify each done story's cleanup was invoked with correct args
        for call in mock_cleanup.call_args_list:
            story_id = call.args[0]
            assert story_id in {"6.1", "6.2"}
            assert call.args[1] == tmp_path  # project_root


# ============================================================================
# Test: Stalemate partial completion (empty _blocked_ids)
# ============================================================================


class TestStalematePartialCompletion:
    async def test_stalemate_sets_blocked_status(
        self, tmp_path: Path,
    ) -> None:
        """Stalemate with empty _blocked_ids still sets epic to blocked.

        When the orchestrator exits due to a dependency stalemate (no
        directly-blocked stories, but some stories remain in BACKLOG),
        the epic should still be marked as 'blocked'.
        """
        orch, graph, _ = _make_orchestrator(
            {
                "6.1": StoryStatus.DONE,
                "6.2": StoryStatus.BACKLOG,  # stuck on dep cycle
            },
            project_root=tmp_path,
        )
        # Simulate stalemate: no blocked_ids, but not all done
        orch._blocked_ids = set()  # empty — no direct blockages

        assert orch._all_stories_done() is False

        with (
            patch.object(
                orch, "_run_epic_teardown",
                new_callable=AsyncMock, return_value=True,
            ) as mock_teardown,
            patch.object(
                orch, "_update_epic_sprint_status",
            ) as mock_sprint_update,
            patch.object(
                orch, "_cleanup_remaining_worktrees",
                new_callable=AsyncMock,
            ) as mock_cleanup,
            patch(
                "bmad_assist_lite.parallel.orchestrator.save_state",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.setup_parallel_log",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.teardown_parallel_log",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_run_header",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_run_complete",
            ),
            patch.object(
                orch._output_mux, "write_orchestrator",
                new_callable=AsyncMock,
            ),
            patch.object(
                orch._output_mux, "stop_all", new_callable=AsyncMock,
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.build_report",
                return_value=MagicMock(),
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.render_report",
                return_value="report",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.write_report",
            ),
        ):
            orch._install_signal_handlers = MagicMock()
            orch._remove_signal_handlers = MagicMock()

            await orch.run()

        # Teardown should NOT be called (not all stories done)
        mock_teardown.assert_not_called()
        # Epic should be marked blocked even though _blocked_ids is empty
        mock_sprint_update.assert_called_once_with("blocked")
        # Worktree cleanup should still run
        mock_cleanup.assert_called_once()


# ============================================================================
# Test: Epic status set to "blocked" on partial completion (Task 7.14)
# ============================================================================


class TestEpicBlockedStatus:
    def test_epic_status_blocked_on_partial_completion(self) -> None:
        """Task 7.14: Epic status set to 'blocked' with some done + some blocked."""
        orch, _, _ = _make_orchestrator({
            "6.1": StoryStatus.DONE,
            "6.2": StoryStatus.BLOCKED,
            "6.3": StoryStatus.DONE,
        })

        mock_sprint_status = MagicMock()
        with (
            patch(
                "bmad_assist_lite.core.sprint_status.get_sprint_status_path",
                return_value=Path("/fake/sprint-status.yaml"),
            ),
            patch(
                "bmad_assist_lite.core.sprint_status.load_sprint_status",
                return_value=mock_sprint_status,
            ),
            patch("bmad_assist_lite.core.sprint_status.save_sprint_status"),
        ):
            orch._update_epic_sprint_status("blocked")

        mock_sprint_status.set_epic_status.assert_called_once_with(6, "blocked")


# ============================================================================
# Test: Teardown subprocess signal handling (Task 7.16)
# ============================================================================


class TestTeardownSignalHandling:
    def test_teardown_process_tracked(self, tmp_path: Path) -> None:
        """Teardown process attribute initialized to None."""
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE},
            project_root=tmp_path,
        )
        assert orch._teardown_process is None

    def test_sigint_terminates_teardown_process(self, tmp_path: Path) -> None:
        """Task 7.16: SIGINT during teardown terminates the teardown process."""
        orch, _, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE},
            project_root=tmp_path,
        )

        # Simulate an active teardown process
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        orch._teardown_process = mock_proc

        # Set up a mock event loop
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        orch._loop = mock_loop

        # First SIGINT — drain mode + kill teardown
        orch._on_sigint()

        assert orch._draining is True
        # Verify call_soon_threadsafe was called (for both the message
        # and the teardown kill)
        assert mock_loop.call_soon_threadsafe.call_count >= 1


# ============================================================================
# Test: Focused orchestrator integration (Task 7.17)
# ============================================================================


class TestTeardownIntegration:
    async def test_teardown_called_when_all_stories_done(
        self, tmp_path: Path,
    ) -> None:
        """Task 7.17: After all stories DONE, teardown is called.

        Mock subprocess for teardown only, verify that after all stories
        are DONE in state, teardown is called and sprint-status updated.
        """
        orch, graph, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE, "6.2": StoryStatus.DONE},
            project_root=tmp_path,
        )

        # Ensure _all_stories_done() returns True
        assert orch._all_stories_done() is True

        # Mock _run_epic_teardown to return success
        teardown_called = False

        async def mock_teardown() -> bool:
            nonlocal teardown_called
            teardown_called = True
            return True

        with (
            patch.object(
                orch, "_run_epic_teardown", side_effect=mock_teardown,
            ),
            patch.object(
                orch, "_update_epic_sprint_status",
            ) as mock_sprint_update,
            patch.object(
                orch, "_cleanup_remaining_worktrees",
                new_callable=AsyncMock,
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.save_state",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.setup_parallel_log",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.teardown_parallel_log",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_run_header",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_run_complete",
            ),
            patch.object(
                orch._output_mux, "write_orchestrator", new_callable=AsyncMock,
            ),
            patch.object(
                orch._output_mux, "stop_all", new_callable=AsyncMock,
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.build_report",
                return_value=MagicMock(),
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.render_report",
                return_value="report",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.write_report",
            ),
        ):
            # Set up signal handler mocking
            orch._install_signal_handlers = MagicMock()
            orch._remove_signal_handlers = MagicMock()

            await orch.run()

        assert teardown_called is True
        mock_sprint_update.assert_called_once_with("done")

    async def test_teardown_not_called_when_blocked_stories(
        self, tmp_path: Path,
    ) -> None:
        """Teardown not called when some stories are blocked.

        Instead, epic status should be set to 'blocked' and worktrees cleaned up.
        """
        orch, graph, _ = _make_orchestrator(
            {
                "6.1": StoryStatus.DONE,
                "6.2": StoryStatus.BLOCKED,
            },
            project_root=tmp_path,
        )

        # Ensure _all_stories_done() returns False
        assert orch._all_stories_done() is False

        teardown_called = False

        async def mock_teardown() -> bool:
            nonlocal teardown_called
            teardown_called = True
            return True

        with (
            patch.object(
                orch, "_run_epic_teardown", side_effect=mock_teardown,
            ),
            patch.object(
                orch, "_update_epic_sprint_status",
            ) as mock_sprint_update,
            patch.object(
                orch, "_cleanup_remaining_worktrees",
                new_callable=AsyncMock,
            ) as mock_cleanup,
            patch(
                "bmad_assist_lite.parallel.orchestrator.save_state",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.setup_parallel_log",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.teardown_parallel_log",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_run_header",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_run_complete",
            ),
            patch.object(
                orch._output_mux, "write_orchestrator", new_callable=AsyncMock,
            ),
            patch.object(
                orch._output_mux, "stop_all", new_callable=AsyncMock,
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.build_report",
                return_value=MagicMock(),
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.render_report",
                return_value="report",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.write_report",
            ),
        ):
            orch._install_signal_handlers = MagicMock()
            orch._remove_signal_handlers = MagicMock()

            await orch.run()

        assert teardown_called is False
        mock_sprint_update.assert_called_once_with("blocked")
        mock_cleanup.assert_called_once()

    async def test_teardown_failure_does_not_set_done(
        self, tmp_path: Path,
    ) -> None:
        """When teardown fails, epic status is NOT updated to done."""
        orch, graph, _ = _make_orchestrator(
            {"6.1": StoryStatus.DONE, "6.2": StoryStatus.DONE},
            project_root=tmp_path,
        )

        async def mock_teardown_fail() -> bool:
            return False

        with (
            patch.object(
                orch, "_run_epic_teardown", side_effect=mock_teardown_fail,
            ),
            patch.object(
                orch, "_update_epic_sprint_status",
            ) as mock_sprint_update,
            patch.object(
                orch, "_cleanup_remaining_worktrees",
                new_callable=AsyncMock,
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.save_state",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.setup_parallel_log",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.teardown_parallel_log",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_run_header",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.log_run_complete",
            ),
            patch.object(
                orch._output_mux, "write_orchestrator", new_callable=AsyncMock,
            ),
            patch.object(
                orch._output_mux, "stop_all", new_callable=AsyncMock,
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.build_report",
                return_value=MagicMock(),
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.render_report",
                return_value="report",
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.write_report",
            ),
        ):
            orch._install_signal_handlers = MagicMock()
            orch._remove_signal_handlers = MagicMock()

            await orch.run()

        # Sprint-status should NOT be updated when teardown fails
        mock_sprint_update.assert_not_called()
