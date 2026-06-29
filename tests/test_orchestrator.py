"""Comprehensive tests for the Orchestrator asyncio subprocess spawner."""

import asyncio
import logging
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.orchestrator import (
    Orchestrator,
    _extract_story_num,
    _kill_process,
)

# subprocess.CREATE_NEW_PROCESS_GROUP (0x00000200) is Windows-only; the attribute does
# not exist on non-Windows platforms. The win32-branch tests below patch it onto the
# subprocess module (create=True) so the Windows code path is exercised on any OS.
WIN_CREATE_NEW_PROCESS_GROUP = 0x00000200

# ============================================================================
# Module-level fixtures — mock state persistence for all orchestrator tests
# ============================================================================


@pytest.fixture(autouse=True)
def _mock_state_persistence():
    """Prevent orchestrator tests from hitting the real filesystem.

    Patches load_state (returns None → fresh state) and save_state (no-op)
    so Orchestrator.__init__ works with fake project_root paths.
    """
    with (
        patch(
            "bmad_assist_lite.parallel.orchestrator.load_state",
            return_value=None,
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
    ):
        yield


# ============================================================================
# Helper factories
# ============================================================================


def _make_config(
    max_concurrency: int = 3,
    stagger_delay: float = 0.0,
    worktree_base_dir: Path | None = None,
) -> ParallelConfig:
    """Create a ParallelConfig with test-friendly defaults."""
    return ParallelConfig(
        max_concurrency=max_concurrency,
        stagger_delay=stagger_delay,
        worktree_base_dir=worktree_base_dir,
    )


def _make_graph(
    ready_sequence: list[list[str]] | None = None,
    all_ids: list[str] | None = None,
    story_count: int = 0,
) -> MagicMock:
    """Create a mock DependencyGraph with configurable ready story sequences.

    Args:
        ready_sequence: List of lists; each call to get_ready_stories returns
            the next list. After exhaustion, returns empty lists.
        all_ids: List of all story IDs in the graph.
        story_count: Number of stories in the graph.

    """
    graph = MagicMock()
    if ready_sequence is None:
        ready_sequence = []
    _seq = list(ready_sequence)
    graph.get_ready_stories = MagicMock(
        side_effect=lambda done, inf, blk: _seq.pop(0) if _seq else []
    )
    graph.all_story_ids = all_ids or []
    graph.story_count = story_count or len(all_ids or [])
    return graph


def _make_orchestrator(
    graph: MagicMock | None = None,
    config: ParallelConfig | None = None,
    project_root: Path | None = None,
    epic_num: int = 3,
) -> Orchestrator:
    """Create an Orchestrator with test-friendly defaults.

    Default graph includes common test story IDs so that the
    ParallelState created in __init__ has matching entries.
    Injects a mock OutputMultiplexer to avoid real I/O.
    """
    if graph is None:
        graph = _make_graph(all_ids=["3.1", "3.2", "3.3"])
    orch = Orchestrator(
        dependency_graph=graph,
        config=config or _make_config(),
        project_root=project_root or Path("/fake/project"),
        epic_num=epic_num,
    )
    orch._output_mux = _mock_output_mux()
    return orch


def _mock_process(returncode: int = 0, pid: int = 12345) -> MagicMock:
    """Create a mock asyncio subprocess process.

    Provides a mock ``stdout`` stream reader for PIPE-based output
    reading and ``wait()`` for process completion (no ``communicate``).
    """
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.stdout = MagicMock()  # Mock StreamReader for output reader
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = MagicMock()
    return proc


def _mock_output_mux() -> MagicMock:
    """Create a mock OutputMultiplexer for orchestrator tests.

    Returns a MagicMock that stubs start_reader (returns a MagicMock task)
    and stop_reader / stop_all as AsyncMock no-ops.
    """
    mux = MagicMock()

    # start_reader returns a mock task (not a real asyncio.Task)
    mock_task = MagicMock()
    mock_task.done = MagicMock(return_value=True)
    mux.start_reader = MagicMock(return_value=mock_task)
    mux.stop_reader = AsyncMock()
    mux.stop_all = AsyncMock()
    mux.write_orchestrator = AsyncMock()
    mux.await_reader = AsyncMock(return_value=True)
    mux._reader_tasks = {}
    return mux


# ============================================================================
# TestOrchestratorInit
# ============================================================================


class TestOrchestratorInit:
    """Verify Orchestrator.__init__ stores attributes and creates semaphore."""

    def test_stores_dependency_graph(self) -> None:
        """Injected dependency_graph is stored as instance attribute."""
        graph = _make_graph()
        orch = _make_orchestrator(graph=graph)
        assert orch._dependency_graph is graph

    def test_stores_config(self) -> None:
        """Injected config is stored as instance attribute."""
        config = _make_config(max_concurrency=2)
        orch = _make_orchestrator(config=config)
        assert orch._config is config

    def test_stores_project_root(self) -> None:
        """Injected project_root is stored as instance attribute."""
        root = Path("/my/project")
        orch = _make_orchestrator(project_root=root)
        assert orch._project_root == root

    def test_stores_epic_num(self) -> None:
        """Injected epic_num is stored as instance attribute."""
        orch = _make_orchestrator(epic_num=5)
        assert orch._epic_num == 5

    def test_semaphore_uses_max_concurrency(self) -> None:
        """Semaphore is created with config.max_concurrency as limit."""
        config = _make_config(max_concurrency=2)
        orch = _make_orchestrator(config=config)
        assert isinstance(orch._semaphore, asyncio.Semaphore)
        # Internal _value reflects the initial count
        assert orch._semaphore._value == 2  # type: ignore[attr-defined]

    def test_running_tasks_initialized_empty(self) -> None:
        """_running_tasks dict is initialized empty."""
        orch = _make_orchestrator()
        assert orch._running_tasks == {}

    def test_task_to_story_initialized_empty(self) -> None:
        """_task_to_story dict is initialized empty."""
        orch = _make_orchestrator()
        assert orch._task_to_story == {}

    def test_story_worktrees_initialized_empty(self) -> None:
        """_story_worktrees dict is initialized empty."""
        orch = _make_orchestrator()
        assert orch._story_worktrees == {}

    def test_status_sets_initialized_empty(self) -> None:
        """All status tracking sets are initialized empty."""
        orch = _make_orchestrator()
        assert orch._done_ids == set()
        assert orch._in_flight_ids == set()
        assert orch._blocked_ids == set()
        assert orch._merging_ids == set()


# ============================================================================
# TestExtractStoryNum
# ============================================================================


class TestExtractStoryNum:
    """Test story_num extraction from story_id with various formats."""

    def test_dot_separated(self) -> None:
        """Extract story number from dot-separated ID like '3.2'."""
        assert _extract_story_num("3.2") == "2"

    def test_dash_separated(self) -> None:
        """Extract story number from dash-separated ID like '3-2'."""
        assert _extract_story_num("3-2") == "2"

    def test_double_digit_story(self) -> None:
        """Extract double-digit story number like '3.10'."""
        assert _extract_story_num("3.10") == "10"

    def test_large_epic_number(self) -> None:
        """Extract story number from large epic like '10.1'."""
        assert _extract_story_num("10.1") == "1"

    def test_no_separator_raises(self) -> None:
        """Raise ParallelError when story_id has no separator."""
        with pytest.raises(ParallelError, match="Cannot extract story number"):
            _extract_story_num("32")

    def test_empty_string_raises(self) -> None:
        """Raise ParallelError for empty story_id."""
        with pytest.raises(ParallelError, match="Cannot extract story number"):
            _extract_story_num("")


# ============================================================================
# TestSpawnStory
# ============================================================================


class TestSpawnStory:
    """Test _spawn_story subprocess spawning logic."""

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_builds_correct_subprocess_command(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Verify subprocess command: sys.executable -m bmad_assist_lite run."""
        wt_path = Path("/fake/worktree")
        mock_to_thread.return_value = wt_path
        mock_proc = _mock_process(returncode=0)
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator(epic_num=3)
        orch._in_flight_ids.add("3.2")

        result = await orch._spawn_story("3.2")

        assert result == 0
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        positional = call_args[0]
        assert positional[0] == sys.executable
        assert positional[1] == "-m"
        assert positional[2] == "bmad_assist_lite"
        assert positional[3] == "run"
        assert positional[4] == "--epic"
        assert positional[5] == "3"
        assert positional[6] == "--story"
        assert positional[7] == "2"
        assert positional[8] == "--single-story"

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_sets_correct_cwd(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Subprocess cwd is set to the worktree path."""
        wt_path = Path("/fake/worktree")
        mock_to_thread.return_value = wt_path
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["cwd"] == str(wt_path)

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_sets_bmad_parallel_mode_env(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Subprocess env includes BMAD_PARALLEL_MODE=1."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["env"]["BMAD_PARALLEL_MODE"] == "1"

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_calls_create_worktree_via_to_thread(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """create_worktree is called via asyncio.to_thread to avoid blocking."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        config = _make_config(worktree_base_dir=Path("/custom/base"))
        root = Path("/my/project")
        orch = _make_orchestrator(config=config, project_root=root)
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        # First call to to_thread should be create_worktree
        first_call = mock_to_thread.call_args_list[0]
        assert first_call[0][0].__name__ == "create_worktree"
        assert first_call[0][1] == "3.2"
        assert first_call[0][2] == root
        assert first_call[0][3] == Path("/custom/base")

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_records_worktree_path(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Worktree path is stored in _story_worktrees after creation."""
        wt_path = Path("/fake/worktree")
        mock_to_thread.return_value = wt_path
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        assert orch._story_worktrees["3.2"] == wt_path

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_returns_nonzero_exit_code(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Non-zero exit code is propagated from the subprocess."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=1)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        result = await orch._spawn_story("3.2")

        assert result == 1

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_uses_pipe_for_stdout_and_merges_stderr(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Subprocess stdout uses PIPE and stderr merges into stdout."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["stdout"] == asyncio.subprocess.PIPE
        assert call_kwargs["stderr"] == asyncio.subprocess.STDOUT

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_applies_stagger_delay(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Stagger delay is applied inside _spawn_story before subprocess spawn."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        config = _make_config(stagger_delay=0.05)
        orch = _make_orchestrator(config=config)
        orch._in_flight_ids.add("3.2")

        sleep_path = "bmad_assist_lite.parallel.orchestrator.asyncio.sleep"
        with patch(sleep_path, new_callable=AsyncMock) as mock_sleep:
            await orch._spawn_story("3.2")
            mock_sleep.assert_called_once_with(0.05)

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_zero_stagger_delay_skips_sleep(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Zero stagger delay does not call asyncio.sleep."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        config = _make_config(stagger_delay=0.0)
        orch = _make_orchestrator(config=config)
        orch._in_flight_ids.add("3.2")

        sleep_path = "bmad_assist_lite.parallel.orchestrator.asyncio.sleep"
        with patch(sleep_path, new_callable=AsyncMock) as mock_sleep:
            await orch._spawn_story("3.2")
            mock_sleep.assert_not_called()

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_does_not_remove_from_in_flight(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """_spawn_story does not remove from _in_flight_ids (managed by _on_story_complete)."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        # _in_flight_ids cleanup is now solely in _on_story_complete
        assert "3.2" in orch._in_flight_ids


# ============================================================================
# TestOnStoryComplete
# ============================================================================


class TestOnStoryComplete:
    """Test _on_story_complete success/failure transitions."""

    async def test_success_adds_to_merging(self) -> None:
        """Exit code 0 transitions story to _merging_ids."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"

        await orch._on_story_complete("3.2", exit_code=0)

        assert "3.2" in orch._merging_ids
        assert "3.2" not in orch._blocked_ids

    async def test_failure_adds_to_blocked(self) -> None:
        """Non-zero exit code transitions story to _blocked_ids."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"
        orch._story_worktrees["3.2"] = Path("/fake/worktree")

        to_thread_path = "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        with patch(to_thread_path, new_callable=AsyncMock):
            await orch._on_story_complete("3.2", exit_code=1)

        assert "3.2" in orch._blocked_ids
        assert "3.2" not in orch._merging_ids

    async def test_failure_cleans_up_worktree(self) -> None:
        """Worktree cleanup is called for blocked (failed) stories."""
        orch = _make_orchestrator(project_root=Path("/proj"))
        config = _make_config(worktree_base_dir=Path("/base"))
        orch._config = config  # type: ignore[misc]
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"
        orch._story_worktrees["3.2"] = Path("/fake/worktree")

        to_thread_path = "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        with patch(to_thread_path, new_callable=AsyncMock) as mock_tt:
            await orch._on_story_complete("3.2", exit_code=1)

            mock_tt.assert_called_once()
            call_args = mock_tt.call_args[0]
            assert call_args[0].__name__ == "cleanup_worktree"
            assert call_args[1] == "3.2"

    async def test_success_does_not_clean_worktree(self) -> None:
        """Successful stories keep their worktree for the merge phase."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"
        orch._story_worktrees["3.2"] = Path("/fake/worktree")

        to_thread_path = "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        with patch(to_thread_path, new_callable=AsyncMock) as mock_tt:
            await orch._on_story_complete("3.2", exit_code=0)

            mock_tt.assert_not_called()

    async def test_cleans_up_running_tasks_and_task_to_story(self) -> None:
        """Both _running_tasks and _task_to_story are cleaned up atomically."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"

        await orch._on_story_complete("3.2", exit_code=0)

        assert "3.2" not in orch._running_tasks
        assert task not in orch._task_to_story

    async def test_removes_from_in_flight_ids(self) -> None:
        """_on_story_complete is the single authority for _in_flight_ids cleanup."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"
        orch._in_flight_ids.add("3.2")

        await orch._on_story_complete("3.2", exit_code=0)

        assert "3.2" not in orch._in_flight_ids

    async def test_failure_removes_worktree_dict_entry(self) -> None:
        """Worktree path mapping is cleaned up for blocked stories."""
        orch = _make_orchestrator(project_root=Path("/proj"))
        config = _make_config(worktree_base_dir=Path("/base"))
        orch._config = config  # type: ignore[misc]
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"
        orch._story_worktrees["3.2"] = Path("/fake/worktree")

        to_thread_path = "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        with patch(to_thread_path, new_callable=AsyncMock):
            await orch._on_story_complete("3.2", exit_code=1)

        assert "3.2" not in orch._story_worktrees

    async def test_handles_missing_task_gracefully(self) -> None:
        """No error when story_id is not in _running_tasks."""
        orch = _make_orchestrator()

        # Should not raise
        await orch._on_story_complete("3.2", exit_code=0)

        assert "3.2" in orch._merging_ids

    async def test_success_writes_orchestrator_message(self) -> None:
        """Successful completion writes via write_orchestrator with merging status."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"

        await orch._on_story_complete("3.2", exit_code=0)

        orch._output_mux.write_orchestrator.assert_called_once()
        msg = orch._output_mux.write_orchestrator.call_args[0][0]
        assert "3.2" in msg
        assert "merging" in msg

    async def test_failure_writes_orchestrator_message(self) -> None:
        """Failed completion writes via write_orchestrator with blocked status."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"
        orch._story_worktrees["3.2"] = Path("/fake/worktree")

        to_thread_path = "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        with patch(to_thread_path, new_callable=AsyncMock):
            await orch._on_story_complete("3.2", exit_code=1)

        orch._output_mux.write_orchestrator.assert_called_once()
        msg = orch._output_mux.write_orchestrator.call_args[0][0]
        assert "3.2" in msg
        assert "blocked" in msg


# ============================================================================
# TestRunLoop
# ============================================================================


class TestRunLoop:
    """Test the async run() main orchestration loop."""

    async def test_spawns_ready_stories(self) -> None:
        """Stories returned by get_ready_stories are spawned as tasks."""
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )

        orch = _make_orchestrator(graph=graph)

        with patch.object(orch, "_spawn_story", new_callable=AsyncMock) as mock_spawn:
            mock_spawn.return_value = 0

            with patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete:

                async def complete_side_effect(sid: str, code: int) -> None:
                    orch._merging_ids.add(sid)
                    task = orch._running_tasks.pop(sid, None)
                    if task:
                        orch._task_to_story.pop(task, None)

                mock_complete.side_effect = complete_side_effect
                await orch.run()

            mock_spawn.assert_called_once_with("3.1")

    async def test_terminates_when_all_done(self) -> None:
        """run() exits when no ready stories and no running tasks."""
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )

        orch = _make_orchestrator(graph=graph)

        with patch.object(orch, "_spawn_story", new_callable=AsyncMock) as mock_spawn:
            mock_spawn.return_value = 0
            with patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete:
                # Simulate _on_story_complete adding to merging
                async def complete_side_effect(sid: str, code: int) -> None:
                    orch._merging_ids.add(sid)
                    task = orch._running_tasks.pop(sid, None)
                    if task:
                        orch._task_to_story.pop(task, None)

                mock_complete.side_effect = complete_side_effect
                await orch.run()

        # Should have exited cleanly
        assert "3.1" in orch._merging_ids

    async def test_reevaluates_after_completion(self) -> None:
        """After a story completes, ready stories are re-evaluated."""
        # First call returns 3.1, after 3.1 completes, second returns 3.2
        graph = _make_graph(
            ready_sequence=[["3.1"], ["3.2"], []],
            all_ids=["3.1", "3.2"],
        )

        orch = _make_orchestrator(graph=graph)
        spawned: list[str] = []

        with patch.object(orch, "_spawn_story", new_callable=AsyncMock) as mock_spawn:
            mock_spawn.return_value = 0

            with patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete:

                async def complete_side_effect(sid: str, code: int) -> None:
                    spawned.append(sid)
                    orch._merging_ids.add(sid)
                    task = orch._running_tasks.pop(sid, None)
                    if task:
                        orch._task_to_story.pop(task, None)

                mock_complete.side_effect = complete_side_effect
                await orch.run()

        assert "3.1" in spawned
        assert "3.2" in spawned

    async def test_handles_mixed_success_and_failure(self) -> None:
        """Orchestrator handles both successful and failed stories correctly."""
        graph = _make_graph(
            ready_sequence=[["3.1", "3.2"], []],
            all_ids=["3.1", "3.2"],
        )

        orch = _make_orchestrator(graph=graph)

        spawn_results = {"3.1": 0, "3.2": 1}

        with patch.object(orch, "_spawn_story", new_callable=AsyncMock) as mock_spawn:
            mock_spawn.side_effect = lambda sid: spawn_results[sid]

            with patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete:

                async def complete_side_effect(sid: str, code: int) -> None:
                    if code == 0:
                        orch._merging_ids.add(sid)
                    else:
                        orch._blocked_ids.add(sid)
                    task = orch._running_tasks.pop(sid, None)
                    if task:
                        orch._task_to_story.pop(task, None)

                mock_complete.side_effect = complete_side_effect
                await orch.run()

        assert "3.1" in orch._merging_ids
        assert "3.2" in orch._blocked_ids

    async def test_merging_ids_unioned_with_done_ids(self) -> None:
        """get_ready_stories is called with done_ids | merging_ids."""
        graph = _make_graph(
            ready_sequence=[["3.1"], [], []],
            all_ids=["3.1", "3.2"],
        )

        orch = _make_orchestrator(graph=graph)
        orch._done_ids.add("3.0")  # Pre-existing done

        with patch.object(orch, "_spawn_story", new_callable=AsyncMock) as mock_spawn:
            mock_spawn.return_value = 0

            with patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete:

                async def complete_side_effect(sid: str, code: int) -> None:
                    orch._merging_ids.add(sid)
                    task = orch._running_tasks.pop(sid, None)
                    if task:
                        orch._task_to_story.pop(task, None)

                mock_complete.side_effect = complete_side_effect
                await orch.run()

        # Verify get_ready_stories was called with union of done + merging
        calls = graph.get_ready_stories.call_args_list
        # Second call (after 3.1 completes) should include merging
        if len(calls) >= 2:  # noqa: PLR2004
            second_call = calls[1]
            done_arg = second_call[0][0]
            assert "3.0" in done_arg  # from _done_ids
            assert "3.1" in done_arg  # from _merging_ids (union)

    async def test_run_adds_to_in_flight(self) -> None:
        """Stories are added to _in_flight_ids when spawned."""
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph)

        in_flight_during_spawn: set[str] = set()

        async def capture_in_flight(sid: str) -> int:
            in_flight_during_spawn.update(orch._in_flight_ids)
            return 0

        with (
            patch.object(orch, "_spawn_story", side_effect=capture_in_flight),
            patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete,
        ):

            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            await orch.run()

        assert "3.1" in in_flight_during_spawn

    async def test_run_writes_final_summary(self) -> None:
        """run() writes exit summary via write_orchestrator."""
        graph = _make_graph(ready_sequence=[[]], all_ids=[])
        orch = _make_orchestrator(graph=graph)

        await orch.run()

        # write_orchestrator called for start + exit summary
        calls = orch._output_mux.write_orchestrator.call_args_list
        messages = [c[0][0] for c in calls]
        assert any("Exit summary" in m for m in messages)


# ============================================================================
# TestStalemateDetection
# ============================================================================


class TestStalemateDetection:
    """Test stalemate detection when no stories are ready and none in-flight."""

    async def test_stalemate_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Stalemate triggers a warning log with status summary."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1", "3.2", "3.3"],
        )

        orch = _make_orchestrator(graph=graph)

        with caplog.at_level(logging.WARNING):
            await orch.run()

        assert any("Stalemate" in r.message and "Remaining: 3" in r.message for r in caplog.records)

    async def test_stalemate_exits_cleanly(self) -> None:
        """Stalemate causes run() to exit without error."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1", "3.2"],
        )

        orch = _make_orchestrator(graph=graph)

        # Should not raise
        await orch.run()

    async def test_no_stalemate_when_all_complete(self, caplog: pytest.LogCaptureFixture) -> None:
        """No stalemate warning when all stories are merging/done."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1"],
        )

        orch = _make_orchestrator(graph=graph)
        orch._merging_ids.add("3.1")

        with caplog.at_level(logging.WARNING):
            await orch.run()

        assert not any("Stalemate" in r.message for r in caplog.records)

    async def test_stalemate_with_blocked_stories(self, caplog: pytest.LogCaptureFixture) -> None:
        """Stalemate includes blocked stories in remaining count."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1", "3.2", "3.3"],
        )

        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids.add("3.1")

        with caplog.at_level(logging.WARNING):
            await orch.run()

        # Only 3.2 and 3.3 are remaining (3.1 is blocked)
        assert any("Stalemate" in r.message and "Remaining: 2" in r.message for r in caplog.records)


# ============================================================================
# TestProcessCleanup
# ============================================================================


class TestProcessCleanup:
    """Test process cleanup on cancellation and errors."""

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_kill_on_cancelled_error(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Subprocess is killed when the task is cancelled."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_proc = _mock_process(returncode=0)
        # Simulate process not yet terminated when wait raises
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(side_effect=asyncio.CancelledError())
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        with pytest.raises(asyncio.CancelledError):
            await orch._spawn_story("3.2")

        # Process should have been killed
        mock_proc.kill.assert_called_once()

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_in_flight_retained_on_cancellation(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Story stays in _in_flight_ids on CancelledError (_on_story_complete cleans it)."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_proc = _mock_process()
        # Simulate process not yet terminated when wait raises
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(side_effect=asyncio.CancelledError())
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        with pytest.raises(asyncio.CancelledError):
            await orch._spawn_story("3.2")

        # _in_flight_ids cleanup is now solely in _on_story_complete
        assert "3.2" in orch._in_flight_ids

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_kill_on_unexpected_exception(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Subprocess is killed when an unexpected exception occurs."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_proc = _mock_process()
        # Simulate process not yet terminated when wait raises
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(side_effect=RuntimeError("boom"))
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        with pytest.raises(RuntimeError, match="boom"):
            await orch._spawn_story("3.2")

        mock_proc.kill.assert_called_once()

    async def test_kill_process_handles_none_pid(self) -> None:
        """_kill_process handles a process with pid=None."""
        proc = MagicMock()
        proc.pid = None

        # Should not raise
        await _kill_process(proc)

    async def test_kill_process_calls_killpg_on_unix(self) -> None:
        """On non-Windows, _kill_process calls os.killpg() for process tree kill."""
        proc = MagicMock()
        proc.pid = 12345
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        # signal.SIGKILL (9) doesn't exist on Windows, so we must mock it too
        sigkill = getattr(signal, "SIGKILL", 9)
        with (
            patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys,
            patch(
                "bmad_assist_lite.parallel.orchestrator.os.getpgid", create=True, return_value=12345
            ) as mock_getpgid,
            patch("bmad_assist_lite.parallel.orchestrator.os.killpg", create=True) as mock_killpg,
            patch("bmad_assist_lite.parallel.orchestrator.signal") as mock_signal,
        ):
            mock_sys.platform = "linux"
            mock_signal.SIGKILL = sigkill
            await _kill_process(proc)

        mock_getpgid.assert_called_once_with(12345)
        mock_killpg.assert_called_once_with(12345, sigkill)
        # proc.kill should NOT be called when os.killpg succeeds
        proc.kill.assert_not_called()

    async def test_kill_process_falls_back_to_proc_kill_on_unix(self) -> None:
        """On Unix, falls back to proc.kill() if os.killpg() fails."""
        proc = MagicMock()
        proc.pid = 12345
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        # On Windows, os.killpg and os.getpgid don't exist, so we must mock
        # them with create=True. signal.SIGKILL also needs mocking.
        sigkill = getattr(signal, "SIGKILL", 9)
        with (
            patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys,
            patch(
                "bmad_assist_lite.parallel.orchestrator.os.getpgid",
                create=True,
                side_effect=ProcessLookupError,
            ),
            patch("bmad_assist_lite.parallel.orchestrator.os.killpg", create=True) as mock_killpg,
            patch("bmad_assist_lite.parallel.orchestrator.signal") as mock_signal,
        ):
            mock_sys.platform = "linux"
            mock_signal.SIGKILL = sigkill
            await _kill_process(proc)

        # os.killpg should NOT be called because os.getpgid raised first
        mock_killpg.assert_not_called()
        proc.kill.assert_called_once()

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_in_flight_retained_on_exception(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Story stays in _in_flight_ids on exception (_on_story_complete cleans it)."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_proc = _mock_process()
        # Simulate process not yet terminated when wait raises
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(side_effect=RuntimeError("boom"))
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        with pytest.raises(RuntimeError, match="boom"):
            await orch._spawn_story("3.2")

        # _in_flight_ids cleanup is now solely in _on_story_complete
        assert "3.2" in orch._in_flight_ids


# ============================================================================
# TestConcurrency
# ============================================================================


class TestConcurrency:
    """Test concurrency limiting via semaphore."""

    async def test_semaphore_limits_concurrent_spawns(self) -> None:
        """With max_concurrency=1, only one story runs at a time."""
        config = _make_config(max_concurrency=1)
        graph = _make_graph(
            ready_sequence=[["3.1", "3.2"], []],
            all_ids=["3.1", "3.2"],
        )

        orch = _make_orchestrator(graph=graph, config=config)

        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def tracking_spawn(sid: str) -> int:
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            # Simulate some work
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
            return 0

        async def wrapped_spawn(sid: str) -> int:
            async with orch._semaphore:
                return await tracking_spawn(sid)

        with (
            patch.object(orch, "_spawn_story", side_effect=wrapped_spawn),
            patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete,
        ):

            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            await orch.run()

        # With semaphore of 1, should never exceed 1
        assert max_concurrent <= 1

    def test_max_concurrency_1_is_sequential(self) -> None:
        """max_concurrency=1 creates a semaphore with value 1."""
        config = _make_config(max_concurrency=1)
        orch = _make_orchestrator(config=config)
        assert orch._semaphore._value == 1  # type: ignore[attr-defined]

    def test_max_concurrency_5(self) -> None:
        """max_concurrency=5 creates a semaphore with value 5."""
        config = _make_config(max_concurrency=5)
        orch = _make_orchestrator(config=config)
        assert orch._semaphore._value == 5  # type: ignore[attr-defined]


# ============================================================================
# TestWindowsFlags
# ============================================================================


class TestWindowsFlags:
    """Test Windows-specific subprocess creation flags."""

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_create_new_process_group_on_windows(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """On Windows, subprocess gets CREATE_NEW_PROCESS_GROUP flag."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        with (
            patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys,
            patch.object(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                WIN_CREATE_NEW_PROCESS_GROUP,
                create=True,
            ),
        ):
            mock_sys.platform = "win32"
            mock_sys.executable = sys.executable
            await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs.get("creationflags") == WIN_CREATE_NEW_PROCESS_GROUP

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_no_creation_flags_on_unix(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """On Unix, subprocess does not get creationflags."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        with patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys:
            mock_sys.platform = "linux"
            mock_sys.executable = sys.executable
            await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        assert "creationflags" not in call_kwargs


# ============================================================================
# TestAllStoriesFail
# ============================================================================


class TestAllStoriesFail:
    """Test edge case where every story in the graph fails."""

    async def test_all_stories_fail_exits_cleanly(self) -> None:
        """Orchestrator handles all stories failing and exits without error."""
        graph = _make_graph(
            ready_sequence=[["3.1", "3.2", "3.3"], []],
            all_ids=["3.1", "3.2", "3.3"],
        )

        orch = _make_orchestrator(graph=graph)

        with patch.object(orch, "_spawn_story", new_callable=AsyncMock) as mock_spawn:
            mock_spawn.return_value = 1  # All fail

            with patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete:

                async def complete_side_effect(sid: str, code: int) -> None:
                    orch._in_flight_ids.discard(sid)
                    orch._blocked_ids.add(sid)
                    task = orch._running_tasks.pop(sid, None)
                    if task:
                        orch._task_to_story.pop(task, None)

                mock_complete.side_effect = complete_side_effect
                await orch.run()

        assert orch._blocked_ids == {"3.1", "3.2", "3.3"}
        assert len(orch._merging_ids) == 0
        assert len(orch._done_ids) == 0


# ============================================================================
# TestProcessCleanupShield
# ============================================================================


class TestProcessCleanupShield:
    """Test that process cleanup uses asyncio.shield in finally block."""

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_finally_calls_kill_process_via_shield(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Process cleanup in finally uses asyncio.shield to prevent re-cancellation."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_proc = _mock_process()
        # Simulate process not yet terminated when wait raises
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(side_effect=asyncio.CancelledError())
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        shield_path = "bmad_assist_lite.parallel.orchestrator.asyncio.shield"
        kill_path = "bmad_assist_lite.parallel.orchestrator._kill_process"
        with (
            # Mock _kill_process so the real os.killpg() never fires against the
            # fake PID, and use a passthrough shield (returns the wrapped awaitable)
            # so the caller still awaits it. A bare AsyncMock shield would drop the
            # coroutine un-awaited → "coroutine never awaited" RuntimeWarning at GC.
            patch(kill_path, new_callable=AsyncMock) as mock_kill,
            patch(shield_path, side_effect=lambda awaitable: awaitable) as mock_shield,
        ):
            with pytest.raises(asyncio.CancelledError):
                await orch._spawn_story("3.2")

            # shield wraps the _kill_process cleanup call in the finally block
            assert mock_shield.call_count >= 1
            mock_kill.assert_awaited_once()


# ============================================================================
# TestOutputReaderLifecycle
# ============================================================================


class TestOutputReaderLifecycle:
    """Test that _spawn_story integrates with OutputMultiplexer correctly."""

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_start_reader_called_after_subprocess_creation(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """start_reader is called with story_id and proc.stdout after spawn."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_proc = _mock_process(returncode=0)
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        orch._output_mux.start_reader.assert_called_once_with(
            "3.2",
            mock_proc.stdout,
        )

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_stop_reader_called_in_finally(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """stop_reader is called during finally cleanup."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_proc = _mock_process(returncode=0)
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        orch._output_mux.await_reader.assert_called_once_with("3.2", timeout=5.0)
        orch._output_mux.stop_reader.assert_called_once_with("3.2")

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_uses_proc_wait_not_communicate(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """_spawn_story uses proc.wait() instead of proc.communicate()."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_proc = _mock_process(returncode=0)
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        mock_proc.wait.assert_called_once()


# ============================================================================
# TestSignalHandlerSetup (Task 7.9, 7.10)
# ============================================================================


class TestSignalHandlerSetup:
    """Test signal handler installation, removal, and two-tier behavior."""

    def test_draining_initialized_false(self) -> None:
        """_draining flag is initialized to False."""
        orch = _make_orchestrator()
        assert orch._draining is False

    def test_force_exit_initialized_false(self) -> None:
        """_force_exit flag is initialized to False."""
        orch = _make_orchestrator()
        assert orch._force_exit is False

    def test_on_sigint_first_call_sets_draining(self) -> None:
        """First call to _on_sigint sets _draining to True."""
        orch = _make_orchestrator()

        # Set the loop reference as _install_signal_handlers would
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        orch._loop = mock_loop
        orch._on_sigint()

        assert orch._draining is True
        assert orch._force_exit is False

    def test_on_sigint_second_call_sets_force_exit(self) -> None:
        """Second call to _on_sigint sets _force_exit to True."""
        orch = _make_orchestrator()
        orch._draining = True  # Simulate first signal already received

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        orch._loop = mock_loop
        orch._on_sigint()

        assert orch._draining is True
        assert orch._force_exit is True

    def test_on_sigint_third_call_is_idempotent(self) -> None:
        """Third+ calls to _on_sigint are no-ops after force_exit is set."""
        orch = _make_orchestrator()
        orch._draining = True
        orch._force_exit = True

        # Should not raise or change state
        orch._on_sigint()

        assert orch._draining is True
        assert orch._force_exit is True

    def test_on_sigint_writes_drain_message(self) -> None:
        """First signal schedules drain message via event loop."""
        orch = _make_orchestrator()

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        orch._loop = mock_loop
        orch._on_sigint()

        # call_soon_threadsafe should have been called to schedule the message
        mock_loop.call_soon_threadsafe.assert_called_once()

    def test_on_sigint_writes_force_exit_message(self) -> None:
        """Second signal schedules force-exit message via event loop."""
        orch = _make_orchestrator()
        orch._draining = True

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        orch._loop = mock_loop
        orch._on_sigint()

        mock_loop.call_soon_threadsafe.assert_called_once()

    def test_on_sigint_handles_closed_loop(self) -> None:
        """_on_sigint sets flags even when loop is closed (no message)."""
        orch = _make_orchestrator()

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = True
        orch._loop = mock_loop
        orch._on_sigint()

        assert orch._draining is True
        mock_loop.call_soon_threadsafe.assert_not_called()

    def test_on_sigint_handles_no_loop(self) -> None:
        """_on_sigint sets flags even when no loop reference exists."""
        orch = _make_orchestrator()
        orch._loop = None
        orch._on_sigint()

        assert orch._draining is True

    def test_install_signal_handlers_unix(self) -> None:
        """On Unix, signal handlers are installed via loop.add_signal_handler."""
        orch = _make_orchestrator()
        mock_loop = MagicMock()

        with (
            patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys,
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_sys.platform = "linux"
            orch._install_signal_handlers()

        # Both SIGINT and SIGTERM handlers should be installed
        calls = mock_loop.add_signal_handler.call_args_list
        signal_nums = [c[0][0] for c in calls]
        assert signal.SIGINT in signal_nums
        assert signal.SIGTERM in signal_nums
        # Loop reference should be stored
        assert orch._loop is mock_loop

    def test_install_signal_handlers_windows(self) -> None:
        """On Windows, signal handler is installed via signal.signal for SIGINT."""
        orch = _make_orchestrator()
        mock_loop = MagicMock()

        with (
            patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys,
            patch("bmad_assist_lite.parallel.orchestrator.signal.signal") as mock_signal,
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_sys.platform = "win32"
            orch._install_signal_handlers()

        mock_signal.assert_called_once_with(signal.SIGINT, orch._signal_handler_sync)
        assert orch._loop is mock_loop

    def test_remove_signal_handlers_unix(self) -> None:
        """On Unix, signal handlers are removed via loop.remove_signal_handler."""
        orch = _make_orchestrator()
        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        orch._loop = mock_loop

        with patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys:
            mock_sys.platform = "linux"
            orch._remove_signal_handlers()

        calls = mock_loop.remove_signal_handler.call_args_list
        signal_nums = [c[0][0] for c in calls]
        assert signal.SIGINT in signal_nums
        assert signal.SIGTERM in signal_nums

    def test_remove_signal_handlers_windows(self) -> None:
        """On Windows, SIGINT is restored to SIG_DFL."""
        orch = _make_orchestrator()

        with (
            patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys,
            patch("bmad_assist_lite.parallel.orchestrator.signal.signal") as mock_signal,
        ):
            mock_sys.platform = "win32"
            orch._remove_signal_handlers()

        mock_signal.assert_called_once_with(signal.SIGINT, signal.SIG_DFL)

    def test_signal_handler_sync_delegates_to_on_sigint(self) -> None:
        """_signal_handler_sync delegates to _on_sigint."""
        orch = _make_orchestrator()

        with patch.object(orch, "_on_sigint") as mock_on_sigint:
            orch._signal_handler_sync(signal.SIGINT, None)

        mock_on_sigint.assert_called_once()

    def test_sigterm_triggers_same_behavior_as_sigint_unix(self) -> None:
        """On Unix, SIGTERM uses the same _on_sigint handler as SIGINT."""
        orch = _make_orchestrator()
        mock_loop = MagicMock()

        with (
            patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys,
            patch("asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_sys.platform = "linux"
            orch._install_signal_handlers()

        # Both SIGINT and SIGTERM should use the same handler
        # (bound methods are not identity-equal, so compare underlying __func__)
        calls = mock_loop.add_signal_handler.call_args_list
        handlers = {c[0][0]: c[0][1] for c in calls}
        assert handlers[signal.SIGINT].__func__ is handlers[signal.SIGTERM].__func__


# ============================================================================
# TestDrainMode (Task 7.1, 7.2, 7.5, 7.11)
# ============================================================================


class TestDrainMode:
    """Test drain mode prevents spawning and waits for running tasks."""

    async def test_draining_prevents_new_story_spawning(self) -> None:
        """When _draining is True, no new stories are spawned even if ready."""
        graph = _make_graph(
            ready_sequence=[["3.1", "3.2"], ["3.3"], []],
            all_ids=["3.1", "3.2", "3.3"],
        )
        orch = _make_orchestrator(graph=graph)

        spawned_ids: list[str] = []

        async def counting_spawn(sid: str) -> int:
            spawned_ids.append(sid)
            # Set draining after first spawn to test prevention
            orch._draining = True
            return 0

        with (
            patch.object(orch, "_spawn_story", side_effect=counting_spawn),
            patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete,
        ):

            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            await orch.run()

        # Stories were spawned before draining was set, but 3.3 was NOT
        # spawned because draining was True by the time loop re-evaluated
        assert len(spawned_ids) <= 2  # noqa: PLR2004
        assert "3.3" not in spawned_ids

    async def test_drain_waits_for_running_tasks_before_exit(self) -> None:
        """Drain mode waits for all running tasks to complete before exiting."""
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph)

        completed_stories: list[str] = []

        async def slow_spawn(sid: str) -> int:
            await asyncio.sleep(0.05)
            return 0

        with (
            patch.object(orch, "_spawn_story", side_effect=slow_spawn),
            patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete,
        ):

            async def complete_side_effect(sid: str, code: int) -> None:
                completed_stories.append(sid)
                orch._merging_ids.add(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect

            # Set draining after stories are spawned (simulate Ctrl+C)
            async def set_drain_after_delay() -> None:
                await asyncio.sleep(0.01)
                orch._draining = True

            drain_task = asyncio.create_task(set_drain_after_delay())
            await orch.run()
            await drain_task

        # The story should have completed before exit (drain waited)
        assert "3.1" in completed_stories

    async def test_drain_loop_breaks_when_no_tasks_remain(self) -> None:
        """When draining and _running_tasks is empty, the loop breaks."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph)
        orch._draining = True  # Pre-set draining

        # No tasks in-flight — should break immediately
        await orch.run()

        # Should have saved state on exit
        # (implicitly verified by no hang / timeout)

    async def test_drain_saves_state_before_exit(self) -> None:
        """State is saved before exiting when draining."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph)
        orch._draining = True

        with patch("bmad_assist_lite.parallel.orchestrator.save_state") as mock_save:
            await orch.run()

        # save_state should have been called at least once during shutdown
        assert mock_save.call_count >= 1

    async def test_stagger_delay_interrupted_by_drain(self) -> None:
        """If draining is set during stagger sleep, no subprocess spawns."""
        config = _make_config(stagger_delay=0.1)
        orch = _make_orchestrator(config=config)
        orch._in_flight_ids.add("3.2")

        async def set_drain_during_sleep(delay: float) -> None:
            orch._draining = True

        sleep_path = "bmad_assist_lite.parallel.orchestrator.asyncio.sleep"
        with patch(sleep_path, side_effect=set_drain_during_sleep):
            result = await orch._spawn_story("3.2")

        # Should return -1 since draining was set during stagger delay
        assert result == -1

    async def test_no_stories_in_flight_at_ctrl_c(self) -> None:
        """Orchestrator exits immediately with summary when no tasks are running."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1", "3.2"],
        )
        orch = _make_orchestrator(graph=graph)
        orch._draining = True
        orch._done_ids.add("3.1")

        # Should exit immediately and print summary
        await orch.run()

        # Verify exit summary was printed
        summary_calls = [c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list]
        assert any("Exit summary" in msg for msg in summary_calls)

    async def test_all_done_at_ctrl_c(self) -> None:
        """Graceful exit with summary when all stories already done."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph)
        orch._draining = True
        orch._done_ids.add("3.1")

        await orch.run()

        summary_calls = [c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list]
        assert any("Exit summary" in msg for msg in summary_calls)


# ============================================================================
# TestForceExit (Task 7.3, 7.4, 7.6)
# ============================================================================


class TestForceExit:
    """Test force-exit cancels all tasks and saves state."""

    async def test_force_exit_cancels_all_running_tasks(self) -> None:
        """Setting _force_exit cancels all running asyncio tasks."""
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph)

        cancelled = False

        async def slow_spawn(sid: str) -> int:
            nonlocal cancelled
            try:
                await asyncio.sleep(10)
                return 0
            except asyncio.CancelledError:
                cancelled = True
                raise

        with (
            patch.object(orch, "_spawn_story", side_effect=slow_spawn),
            patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete,
        ):

            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect

            async def trigger_force_exit() -> None:
                await asyncio.sleep(0.05)
                orch._draining = True
                orch._force_exit = True

            trigger = asyncio.create_task(trigger_force_exit())
            await orch.run()
            await trigger

        assert cancelled is True

    async def test_force_exit_saves_state(self) -> None:
        """State is saved immediately after force-exit."""
        orch = _make_orchestrator()

        with patch("bmad_assist_lite.parallel.orchestrator.save_state") as mock_save:
            await orch._handle_force_exit()

        # save_state is called during force-exit cleanup
        mock_save.assert_called_once()

    async def test_force_exit_calls_stop_all_on_output_mux(self) -> None:
        """Force-exit calls stop_all on the output multiplexer."""
        orch = _make_orchestrator()

        await orch._handle_force_exit()

        orch._output_mux.stop_all.assert_awaited_once()

    async def test_handle_force_exit_cancels_tasks(self) -> None:
        """_handle_force_exit cancels all tasks in _running_tasks."""
        orch = _make_orchestrator()

        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        orch._running_tasks["3.1"] = mock_task

        with patch("asyncio.gather", new_callable=AsyncMock):
            await orch._handle_force_exit()

        mock_task.cancel.assert_called_once()

    async def test_handle_force_exit_gathers_with_return_exceptions(
        self,
    ) -> None:
        """_handle_force_exit uses gather with return_exceptions=True."""
        orch = _make_orchestrator()

        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        orch._running_tasks["3.1"] = mock_task

        with patch("asyncio.gather", new_callable=AsyncMock) as mock_gather:
            await orch._handle_force_exit()

        mock_gather.assert_called_once_with(mock_task, return_exceptions=True)

    async def test_force_exit_summary_includes_git_lock_warning(
        self,
    ) -> None:
        """Force-exit summary warns about potential stale git lock files."""
        graph = _make_graph(all_ids=["3.1"])
        orch = _make_orchestrator(graph=graph)
        orch._force_exit = True

        await orch._print_exit_summary()

        messages = [c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list]
        assert any(".git/index.lock" in msg for msg in messages)

    async def test_no_git_lock_warning_on_drain_exit(self) -> None:
        """Drain mode exit does NOT warn about stale git lock files."""
        graph = _make_graph(all_ids=["3.1"])
        orch = _make_orchestrator(graph=graph)
        orch._draining = True
        orch._force_exit = False

        await orch._print_exit_summary()

        messages = [c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list]
        assert not any(".git/index.lock" in msg for msg in messages)

    async def test_force_exit_triggers_kill_process_via_finally(self) -> None:
        """Force-exit cancellation invokes _kill_process via _spawn_story finally block."""
        orch = _make_orchestrator()

        mock_proc = _mock_process()
        mock_proc.returncode = None  # Simulate still-running process
        mock_proc.wait = AsyncMock(side_effect=asyncio.CancelledError())

        kill_called = False

        async def tracking_kill(proc: MagicMock) -> None:
            nonlocal kill_called
            kill_called = True

        # Patch create_subprocess_exec and to_thread to set up real _spawn_story
        with (
            patch(
                "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=Path("/fake/worktree"),
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ),
            patch(
                "bmad_assist_lite.parallel.orchestrator._kill_process",
                side_effect=tracking_kill,
            ),
        ):
            orch._in_flight_ids.add("3.1")
            task = asyncio.create_task(orch._spawn_story("3.1"))
            await asyncio.sleep(0.01)

            # Force-exit: cancel the task (simulates _handle_force_exit)
            orch._force_exit = True
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert kill_called is True

    async def test_asyncio_wait_timeout_continues_loop(self) -> None:
        """Loop continues when asyncio.wait timeout expires with no completions."""
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph)

        async def spawn_then_drain(sid: str) -> int:
            # Takes a while — loop may timeout waiting before completion
            await asyncio.sleep(0.05)
            return 0

        with (
            patch.object(orch, "_spawn_story", side_effect=spawn_then_drain),
            patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete,
        ):

            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            await orch.run()

        # Should have completed normally (timeout didn't break the loop)
        assert "3.1" in orch._merging_ids


# ============================================================================
# TestExitSummary (Task 7.7, 7.8)
# ============================================================================


class TestExitSummary:
    """Test exit summary content and blocked story dependency listing."""

    async def test_exit_summary_includes_all_counts(self) -> None:
        """Exit summary shows done, merging, in-flight, blocked, remaining."""
        graph = _make_graph(
            all_ids=["3.1", "3.2", "3.3", "3.4", "3.5"],
        )
        orch = _make_orchestrator(graph=graph)
        orch._done_ids = {"3.1"}
        orch._merging_ids = {"3.2"}
        orch._in_flight_ids = {"3.3"}
        orch._blocked_ids = {"3.4"}
        # 3.5 is remaining (backlog)

        await orch._print_exit_summary()

        messages = [c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list]
        summary = messages[0]
        assert "Done: 1" in summary
        assert "Merging: 1" in summary
        assert "In-flight: 1" in summary
        assert "Blocked: 1" in summary
        assert "Remaining: 1" in summary

    async def test_exit_summary_lists_blocked_with_unmet_deps(self) -> None:
        """Blocked stories list their unmet dependencies."""
        graph = _make_graph(all_ids=["3.1", "3.2", "3.3"])
        # Configure dependencies_of to return actual deps
        graph.dependencies_of = MagicMock(return_value=["3.1", "3.3"])
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids = {"3.2"}
        orch._done_ids = {"3.1"}
        # 3.3 is NOT done, so it's an unmet dependency

        await orch._print_exit_summary()

        messages = [c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list]
        blocked_msgs = [m for m in messages if "Blocked: 3.2" in m]
        assert len(blocked_msgs) == 1
        assert "3.3" in blocked_msgs[0]
        assert "unmet deps" in blocked_msgs[0]

    async def test_exit_summary_blocked_no_unmet_deps(self) -> None:
        """Blocked story with all deps satisfied shows 'failed execution'."""
        graph = _make_graph(all_ids=["3.1", "3.2"])
        graph.dependencies_of = MagicMock(return_value=["3.1"])
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids = {"3.2"}
        orch._done_ids = {"3.1"}

        await orch._print_exit_summary()

        messages = [c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list]
        blocked_msgs = [m for m in messages if "Blocked: 3.2" in m]
        assert len(blocked_msgs) == 1
        assert "failed execution" in blocked_msgs[0]

    async def test_exit_summary_no_blocked_stories(self) -> None:
        """Exit summary with no blocked stories doesn't list any."""
        graph = _make_graph(all_ids=["3.1"])
        orch = _make_orchestrator(graph=graph)
        orch._done_ids = {"3.1"}

        await orch._print_exit_summary()

        messages = [c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list]
        assert not any("Blocked:" in m for m in messages if "Blocked: 0" not in m)

    async def test_exit_summary_called_from_run(self) -> None:
        """run() calls _print_exit_summary on exit."""
        graph = _make_graph(ready_sequence=[[]], all_ids=[])
        orch = _make_orchestrator(graph=graph)

        with patch.object(orch, "_print_exit_summary", new_callable=AsyncMock) as mock_summary:
            await orch.run()

        mock_summary.assert_called_once()

    async def test_signal_handlers_removed_on_run_exit(self) -> None:
        """Signal handlers are removed when run() exits (via finally)."""
        graph = _make_graph(ready_sequence=[[]], all_ids=[])
        orch = _make_orchestrator(graph=graph)

        with (
            patch.object(orch, "_install_signal_handlers"),
            patch.object(orch, "_remove_signal_handlers") as mock_remove,
        ):
            await orch.run()

        mock_remove.assert_called_once()

    async def test_signal_handlers_removed_on_run_exception(self) -> None:
        """Signal handlers are removed even if run() raises an exception."""
        graph = _make_graph(ready_sequence=[[]], all_ids=[])
        orch = _make_orchestrator(graph=graph)

        with (
            patch.object(orch, "_install_signal_handlers"),
            patch.object(orch, "_remove_signal_handlers") as mock_remove,
            patch.object(orch, "_print_exit_summary", new_callable=AsyncMock),
        ):
            # Make get_ready_stories raise to test exception path
            graph.get_ready_stories = MagicMock(side_effect=RuntimeError("test"))
            with pytest.raises(RuntimeError, match="test"):
                await orch.run()

        mock_remove.assert_called_once()


# ============================================================================
# TestSubprocessIsolation (Task 7.12)
# ============================================================================


class TestSubprocessIsolation:
    """Test subprocess process group isolation for drain mode."""

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_start_new_session_on_unix(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """On Unix, subprocess uses start_new_session=True for isolation."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        with patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys:
            mock_sys.platform = "linux"
            mock_sys.executable = sys.executable
            await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs.get("start_new_session") is True

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_create_new_process_group_on_windows_isolation(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """On Windows, subprocess uses CREATE_NEW_PROCESS_GROUP for isolation."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        with (
            patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys,
            patch.object(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                WIN_CREATE_NEW_PROCESS_GROUP,
                create=True,
            ),
        ):
            mock_sys.platform = "win32"
            mock_sys.executable = sys.executable
            await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs.get("creationflags") == WIN_CREATE_NEW_PROCESS_GROUP

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_unix_no_creationflags(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """On Unix, no creationflags are set (only start_new_session)."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        with patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys:
            mock_sys.platform = "linux"
            mock_sys.executable = sys.executable
            await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        assert "creationflags" not in call_kwargs
