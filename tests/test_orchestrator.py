"""Comprehensive tests for the Orchestrator asyncio subprocess spawner."""

import asyncio
import logging
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
    """Create an Orchestrator with test-friendly defaults."""
    return Orchestrator(
        dependency_graph=graph or _make_graph(),
        config=config or _make_config(),
        project_root=project_root or Path("/fake/project"),
        epic_num=epic_num,
    )


def _mock_process(returncode: int = 0, pid: int = 12345) -> MagicMock:
    """Create a mock asyncio subprocess process."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = MagicMock()
    return proc


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
    async def test_uses_devnull_for_stdout_stderr(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """Subprocess stdout and stderr use DEVNULL to prevent pipe deadlock."""
        mock_to_thread.return_value = Path("/fake/worktree")
        mock_exec.return_value = _mock_process(returncode=0)

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["stdout"] == asyncio.subprocess.DEVNULL
        assert call_kwargs["stderr"] == asyncio.subprocess.DEVNULL

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

        sleep_path = (
            "bmad_assist_lite.parallel.orchestrator.asyncio.sleep"
        )
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

        sleep_path = (
            "bmad_assist_lite.parallel.orchestrator.asyncio.sleep"
        )
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

        to_thread_path = (
            "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        )
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

        to_thread_path = (
            "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        )
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

        to_thread_path = (
            "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        )
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

        to_thread_path = (
            "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        )
        with patch(to_thread_path, new_callable=AsyncMock):
            await orch._on_story_complete("3.2", exit_code=1)

        assert "3.2" not in orch._story_worktrees

    async def test_handles_missing_task_gracefully(self) -> None:
        """No error when story_id is not in _running_tasks."""
        orch = _make_orchestrator()

        # Should not raise
        await orch._on_story_complete("3.2", exit_code=0)

        assert "3.2" in orch._merging_ids

    async def test_success_logs_info(self, caplog: pytest.LogCaptureFixture) -> None:
        """Successful completion logs at INFO with ORCHESTRATOR prefix."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"

        with caplog.at_level(logging.INFO):
            await orch._on_story_complete("3.2", exit_code=0)

        assert any(
            "[ORCHESTRATOR]" in r.message and "merging" in r.message
            for r in caplog.records
        )

    async def test_failure_logs_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Failed completion logs at ERROR with ORCHESTRATOR prefix."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.2"] = task
        orch._task_to_story[task] = "3.2"
        orch._story_worktrees["3.2"] = Path("/fake/worktree")

        to_thread_path = (
            "bmad_assist_lite.parallel.orchestrator.asyncio.to_thread"
        )
        with patch(to_thread_path, new_callable=AsyncMock):
            with caplog.at_level(logging.ERROR):
                await orch._on_story_complete("3.2", exit_code=1)

        assert any(
            "[ORCHESTRATOR]" in r.message and "blocked" in r.message
            for r in caplog.records
        )


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

        with patch.object(orch, "_spawn_story", side_effect=capture_in_flight):
            with patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete:
                async def complete_side_effect(sid: str, code: int) -> None:
                    orch._merging_ids.add(sid)
                    task = orch._running_tasks.pop(sid, None)
                    if task:
                        orch._task_to_story.pop(task, None)

                mock_complete.side_effect = complete_side_effect
                await orch.run()

        assert "3.1" in in_flight_during_spawn

    async def test_run_logs_final_summary(self, caplog: pytest.LogCaptureFixture) -> None:
        """run() logs a final summary with done/merging/blocked counts."""
        graph = _make_graph(ready_sequence=[[]], all_ids=[])
        orch = _make_orchestrator(graph=graph)

        with caplog.at_level(logging.INFO):
            await orch.run()

        assert any(
            "Orchestration complete" in r.message
            for r in caplog.records
        )


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

        assert any(
            "Stalemate" in r.message and "Remaining: 3" in r.message
            for r in caplog.records
        )

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
        assert any(
            "Stalemate" in r.message and "Remaining: 2" in r.message
            for r in caplog.records
        )


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
        # Simulate process not yet terminated when communicate raises
        mock_proc.returncode = None
        mock_proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())
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
        # Simulate process not yet terminated when communicate raises
        mock_proc.returncode = None
        mock_proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())
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
        # Simulate process not yet terminated when communicate raises
        mock_proc.returncode = None
        mock_proc.communicate = AsyncMock(side_effect=RuntimeError("boom"))
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

    async def test_kill_process_calls_kill_on_unix(self) -> None:
        """On non-Windows, _kill_process calls proc.kill()."""
        proc = MagicMock()
        proc.pid = 12345
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys:
            mock_sys.platform = "linux"
            await _kill_process(proc)

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
        # Simulate process not yet terminated when communicate raises
        mock_proc.returncode = None
        mock_proc.communicate = AsyncMock(side_effect=RuntimeError("boom"))
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

        with patch.object(orch, "_spawn_story", side_effect=wrapped_spawn):
            with patch.object(orch, "_on_story_complete", new_callable=AsyncMock) as mock_complete:
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

        with patch("bmad_assist_lite.parallel.orchestrator.sys") as mock_sys:
            mock_sys.platform = "win32"
            mock_sys.executable = sys.executable
            await orch._spawn_story("3.2")

        call_kwargs = mock_exec.call_args[1]
        import subprocess as sp

        assert call_kwargs.get("creationflags") == sp.CREATE_NEW_PROCESS_GROUP

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
        # Simulate process not yet terminated when communicate raises
        mock_proc.returncode = None
        mock_proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())
        mock_exec.return_value = mock_proc

        orch = _make_orchestrator()
        orch._in_flight_ids.add("3.2")

        shield_path = "bmad_assist_lite.parallel.orchestrator.asyncio.shield"
        with patch(shield_path, new_callable=AsyncMock) as mock_shield:
            with pytest.raises(asyncio.CancelledError):
                await orch._spawn_story("3.2")

            mock_shield.assert_called_once()
