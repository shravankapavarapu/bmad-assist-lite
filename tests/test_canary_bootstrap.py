"""Tests for canary bootstrap integration in orchestrator (Story 9.2).

Covers canary validation, canary failure abort, non-canary bootstrap,
non-canary failure blocking, zero overhead when unconfigured, logging,
stagger delay conditioning, and resume mode.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bmad_assist_lite.parallel.bootstrap import BootstrapResult
from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.orchestrator import Orchestrator
from bmad_assist_lite.parallel.state import StoryStatus


# ============================================================================
# Module-level fixtures — mock state persistence for all orchestrator tests
# ============================================================================


@pytest.fixture(autouse=True)
def _mock_state_persistence():
    """Prevent orchestrator tests from hitting the real filesystem."""
    with patch(
        "bmad_assist_lite.parallel.orchestrator.load_state", return_value=None,
    ), patch(
        "bmad_assist_lite.parallel.orchestrator.save_state",
    ), patch(
        "bmad_assist_lite.parallel.orchestrator.setup_parallel_log",
    ), patch(
        "bmad_assist_lite.parallel.orchestrator.teardown_parallel_log",
    ):
        yield


# ============================================================================
# Helper factories
# ============================================================================


def _make_config(
    max_concurrency: int = 3,
    stagger_delay: float = 0.0,
    worktree_base_dir: Path | None = None,
    setup_commands: list[str] | None = None,
    validation_command: str | None = None,
    copy_to_worktree: list[str] | None = None,
) -> ParallelConfig:
    """Create a ParallelConfig with test-friendly defaults."""
    kwargs: dict = {
        "max_concurrency": max_concurrency,
        "stagger_delay": stagger_delay,
        "worktree_base_dir": worktree_base_dir,
    }
    if setup_commands is not None:
        kwargs["setup_commands"] = setup_commands
    if validation_command is not None:
        kwargs["validation_command"] = validation_command
    if copy_to_worktree is not None:
        kwargs["copy_to_worktree"] = copy_to_worktree
    return ParallelConfig(**kwargs)


def _make_graph(
    ready_sequence: list[list[str]] | None = None,
    all_ids: list[str] | None = None,
    story_count: int = 0,
) -> MagicMock:
    """Create a mock DependencyGraph with configurable ready story sequences."""
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


def _mock_output_mux() -> MagicMock:
    """Create a mock OutputMultiplexer for orchestrator tests."""
    mux = MagicMock()
    mock_task = MagicMock()
    mock_task.done = MagicMock(return_value=True)
    mux.start_reader = MagicMock(return_value=mock_task)
    mux.stop_reader = AsyncMock()
    mux.stop_all = AsyncMock()
    mux.write_orchestrator = AsyncMock()
    mux.await_reader = AsyncMock(return_value=True)
    mux._reader_tasks = {}
    return mux


def _make_orchestrator(
    graph: MagicMock | None = None,
    config: ParallelConfig | None = None,
    project_root: Path | None = None,
    epic_num: int = 3,
) -> Orchestrator:
    """Create an Orchestrator with test-friendly defaults."""
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


# ============================================================================
# TestHasBootstrapConfig (Task 1 — AC #5)
# ============================================================================


class TestHasBootstrapConfig:
    """Test _has_bootstrap_config() helper method."""

    def test_returns_false_for_default_config(self) -> None:
        """Default ParallelConfig has no bootstrap config."""
        config = _make_config()
        orch = _make_orchestrator(config=config)
        assert orch._has_bootstrap_config() is False

    def test_returns_true_with_setup_commands(self) -> None:
        """Returns True when setup_commands is non-empty."""
        config = _make_config(setup_commands=["pip install -e ."])
        orch = _make_orchestrator(config=config)
        assert orch._has_bootstrap_config() is True

    def test_returns_true_with_validation_command(self) -> None:
        """Returns True when validation_command is set."""
        config = _make_config(validation_command="pytest -q -x")
        orch = _make_orchestrator(config=config)
        assert orch._has_bootstrap_config() is True

    def test_returns_true_with_copy_to_worktree(self) -> None:
        """Returns True when copy_to_worktree is non-empty."""
        config = _make_config(copy_to_worktree=[".env"])
        orch = _make_orchestrator(config=config)
        assert orch._has_bootstrap_config() is True

    def test_returns_true_with_all_set(self) -> None:
        """Returns True when all bootstrap fields are set."""
        config = _make_config(
            copy_to_worktree=[".env"],
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        orch = _make_orchestrator(config=config)
        assert orch._has_bootstrap_config() is True


# ============================================================================
# TestCanaryState (Task 2)
# ============================================================================


class TestCanaryState:
    """Test canary tracking state initialization."""

    def test_canary_passed_initialized_false(self) -> None:
        """_canary_passed is initialized to False."""
        orch = _make_orchestrator()
        assert orch._canary_passed is False

    def test_canary_story_id_initialized_none(self) -> None:
        """_canary_story_id is initialized to None."""
        orch = _make_orchestrator()
        assert orch._canary_story_id is None


# ============================================================================
# TestCanaryBootstrapSuccess (Task 3, AC #1, #3, #6)
# ============================================================================


class TestCanaryBootstrapSuccess:
    """Test canary bootstrap runs full validation on first story."""

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_canary_runs_validate_true(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """First story runs bootstrap_worktree with validate=True."""
        config = _make_config(
            setup_commands=["pip install -e ."],
            validation_command="pytest -q -x",
        )
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph, config=config)

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(success=True, output="all good")

        # to_thread calls: create_worktree returns path, bootstrap returns result
        mock_to_thread.side_effect = [wt_path, bootstrap_result]

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        with patch.object(
            orch, "_on_story_complete", new_callable=AsyncMock
        ) as mock_complete:
            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            await orch.run()

        # Verify bootstrap_worktree was called via to_thread with validate=True
        # to_thread calls in canary block: [0]=create_worktree, [1]=bootstrap
        assert mock_to_thread.call_count >= 2
        bootstrap_call = mock_to_thread.call_args_list[1]
        # First arg is the patched bootstrap_worktree mock
        assert bootstrap_call[0][0] is mock_bootstrap
        # validate=True should be passed as kwarg
        assert bootstrap_call[1].get("validate") is True

        assert orch._canary_passed is True
        assert orch._canary_story_id == "3.1"

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_canary_success_logging(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Canary success logs [BOOTSTRAP] message at INFO level."""
        config = _make_config(
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph, config=config)

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(success=True, output="ok")
        mock_to_thread.side_effect = [wt_path, bootstrap_result]

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        with patch.object(
            orch, "_on_story_complete", new_callable=AsyncMock
        ) as mock_complete:
            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            await orch.run()

        # Check log messages include the bootstrap canary success message
        messages = [
            c[0][0]
            for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        assert any(
            "[BOOTSTRAP]" in m and "bootstrap passed" in m and "3.1" in m
            for m in messages
        )


# ============================================================================
# TestCanaryBootstrapFailure (Task 3, Task 4, AC #2, #7)
# ============================================================================


class TestCanaryBootstrapFailure:
    """Test canary failure aborts entire run."""

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    async def test_canary_failure_aborts_run(
        self,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Canary bootstrap failure raises ParallelError, no other worktrees."""
        config = _make_config(
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        graph = _make_graph(
            ready_sequence=[["3.1", "3.2"], []],
            all_ids=["3.1", "3.2"],
        )
        orch = _make_orchestrator(graph=graph, config=config)

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(
            success=False,
            failed_phase="validation",
            error_message="tests failed",
            output="FAILED test_x.py",
        )
        mock_to_thread.side_effect = [wt_path, bootstrap_result, None]

        with pytest.raises(ParallelError):
            await orch.run()

        # Canary should not have passed
        assert orch._canary_passed is False
        # No stories should be in-flight after failure
        assert len(orch._in_flight_ids) == 0

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    async def test_canary_failure_cleans_up_worktree(
        self,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Canary failure calls cleanup_worktree for the canary."""
        config = _make_config(
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph, config=config)

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(
            success=False,
            failed_phase="setup",
            error_message="pip failed",
            output="error output",
        )
        # to_thread: create_worktree, bootstrap, cleanup_worktree
        mock_to_thread.side_effect = [wt_path, bootstrap_result, None]

        with pytest.raises(ParallelError):
            await orch.run()

        # Verify cleanup_worktree was called
        cleanup_call = mock_to_thread.call_args_list[2]
        assert cleanup_call[0][0].__name__ == "cleanup_worktree"

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    async def test_canary_failure_logging(
        self,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Canary failure logs [BOOTSTRAP] FAILED message."""
        config = _make_config(
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph, config=config)

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(
            success=False,
            failed_phase="validation",
            error_message="tests failed",
            output="FAILED test_x.py",
        )
        mock_to_thread.side_effect = [wt_path, bootstrap_result, None]

        with pytest.raises(ParallelError):
            await orch.run()

        messages = [
            c[0][0]
            for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        assert any(
            "[BOOTSTRAP]" in m and "FAILED" in m and "3.1" in m
            for m in messages
        )


# ============================================================================
# TestNonCanaryBootstrap (Task 3b, AC #3, #4)
# ============================================================================


class TestNonCanaryBootstrap:
    """Test non-canary stories run bootstrap with validate=False."""

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_non_canary_uses_validate_false(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Non-canary stories call bootstrap_worktree(validate=False)."""
        config = _make_config(
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        orch = _make_orchestrator(config=config)
        # Simulate canary already passed
        orch._canary_passed = True
        orch._canary_story_id = "3.0"

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(success=True, output="ok")
        mock_to_thread.side_effect = [wt_path, bootstrap_result]

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        orch._in_flight_ids.add("3.2")
        await orch._spawn_story("3.2")

        # to_thread calls: [0]=create_worktree, [1]=bootstrap_worktree
        assert mock_to_thread.call_count >= 2
        bootstrap_call = mock_to_thread.call_args_list[1]
        # The first arg is the patched bootstrap_worktree mock
        assert bootstrap_call[0][0] is mock_bootstrap
        # Check validate=False was passed via kwargs
        assert bootstrap_call[1].get("validate") is False

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    async def test_non_canary_setup_failure_blocks_story(
        self,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Non-canary bootstrap failure marks story as BLOCKED."""
        config = _make_config(
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        orch = _make_orchestrator(config=config)
        orch._canary_passed = True
        orch._canary_story_id = "3.0"

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(
            success=False,
            failed_phase="setup",
            error_message="pip failed",
            output="error",
        )
        # to_thread: create_worktree, bootstrap, cleanup_worktree
        mock_to_thread.side_effect = [wt_path, bootstrap_result, None]

        orch._in_flight_ids.add("3.2")
        result = await orch._spawn_story("3.2")

        # Should return sentinel exit code for blocked
        assert result == -2
        assert "3.2" in orch._blocked_ids

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    async def test_non_canary_failure_cleans_up_worktree(
        self,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Non-canary bootstrap failure calls cleanup_worktree."""
        config = _make_config(
            setup_commands=["pip install"],
        )
        orch = _make_orchestrator(config=config)
        orch._canary_passed = True

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(
            success=False,
            failed_phase="setup",
            error_message="npm failed",
            output="err",
        )
        mock_to_thread.side_effect = [wt_path, bootstrap_result, None]

        orch._in_flight_ids.add("3.2")
        await orch._spawn_story("3.2")

        # to_thread calls: [0]=create_worktree, [1]=bootstrap, [2]=cleanup
        assert mock_to_thread.call_count == 3
        cleanup_call = mock_to_thread.call_args_list[2]
        # cleanup_worktree is not patched, so it's the real function
        assert cleanup_call[0][0].__name__ == "cleanup_worktree"

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    async def test_non_canary_failure_does_not_abort_run(
        self,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Non-canary failure does NOT raise — other stories continue."""
        config = _make_config(
            setup_commands=["pip install"],
        )
        orch = _make_orchestrator(config=config)
        orch._canary_passed = True

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(
            success=False,
            failed_phase="setup",
            error_message="failed",
            output="err",
        )
        mock_to_thread.side_effect = [wt_path, bootstrap_result, None]

        orch._in_flight_ids.add("3.2")
        # Should not raise
        result = await orch._spawn_story("3.2")
        assert result == -2

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    async def test_non_canary_failure_preserves_descriptive_error(
        self,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Non-canary bootstrap failure preserves descriptive block reason.

        When _spawn_story returns -2 and _on_story_complete is called,
        the already-blocked story should not have its error overwritten
        with a generic "Exit code -2".
        """
        config = _make_config(
            setup_commands=["pip install"],
        )
        orch = _make_orchestrator(config=config)
        orch._canary_passed = True

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(
            success=False,
            failed_phase="setup",
            error_message="pip failed",
            output="err",
        )
        mock_to_thread.side_effect = [wt_path, bootstrap_result, None]

        orch._in_flight_ids.add("3.2")
        result = await orch._spawn_story("3.2")
        assert result == -2

        # Simulate what the main loop does after _spawn_story returns
        await orch._on_story_complete("3.2", -2)

        # Verify the descriptive error is preserved, not overwritten
        story_state = orch._state.stories.get("3.2")
        assert story_state is not None
        assert "Bootstrap setup failed" in (story_state.error or "")


# ============================================================================
# TestZeroOverhead (Task 1, AC #5)
# ============================================================================


class TestZeroOverhead:
    """Test no bootstrap calls when unconfigured."""

    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_no_bootstrap_when_unconfigured(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
    ) -> None:
        """With default config, bootstrap_worktree is never called."""
        config = _make_config()  # All defaults — no bootstrap
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph, config=config)

        wt_path = Path("/fake/worktree")
        mock_to_thread.return_value = wt_path

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        with patch.object(
            orch, "_on_story_complete", new_callable=AsyncMock
        ) as mock_complete:
            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            await orch.run()

        # All to_thread calls should be for create_worktree only
        # (no bootstrap_worktree calls since config has no bootstrap)
        for call in mock_to_thread.call_args_list:
            func = call[0][0]
            # The real create_worktree function has __name__; ensure
            # no bootstrap-related call was made
            if hasattr(func, '__name__') and isinstance(func.__name__, str):
                assert func.__name__ != "bootstrap_worktree"


# ============================================================================
# TestStaggerDelay (Task 5, AC #1)
# ============================================================================


class TestStaggerDelayCanary:
    """Test stagger delay is not applied to canary story."""

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_canary_skips_stagger_delay(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Canary story should not have stagger delay applied."""
        config = _make_config(
            stagger_delay=10.0,
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        graph = _make_graph(
            ready_sequence=[["3.1"], []],
            all_ids=["3.1"],
        )
        orch = _make_orchestrator(graph=graph, config=config)

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(success=True, output="ok")
        mock_to_thread.side_effect = [wt_path, bootstrap_result]

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        sleep_called = False

        async def track_sleep(delay: float) -> None:
            nonlocal sleep_called
            sleep_called = True

        with patch.object(
            orch, "_on_story_complete", new_callable=AsyncMock,
        ) as mock_complete:
            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                orch._in_flight_ids.discard(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            with patch(
                "bmad_assist_lite.parallel.orchestrator.asyncio.sleep",
                side_effect=track_sleep,
            ):
                await orch.run()

        # Canary story gets spawned via _spawn_story() after bootstrap
        # passes in run(). The stagger delay should NOT be applied because
        # the story_id matches _canary_story_id.
        assert orch._canary_passed is True
        assert sleep_called is False, (
            "Stagger delay should be skipped for canary story"
        )

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_non_canary_gets_stagger_delay(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Non-canary stories should respect stagger_delay."""
        config = _make_config(
            stagger_delay=0.01,
            setup_commands=["pip install"],
        )
        orch = _make_orchestrator(config=config)
        orch._canary_passed = True
        orch._canary_story_id = "3.0"

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(success=True, output="ok")
        mock_to_thread.side_effect = [wt_path, bootstrap_result]

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        orch._in_flight_ids.add("3.2")

        sleep_called = False

        async def track_sleep(delay: float) -> None:
            nonlocal sleep_called
            sleep_called = True

        with patch(
            "bmad_assist_lite.parallel.orchestrator.asyncio.sleep",
            side_effect=track_sleep,
        ):
            await orch._spawn_story("3.2")

        assert sleep_called is True


# ============================================================================
# TestResumeSkipsCanary (Task 4b, AC #8)
# ============================================================================


class TestResumeSkipsCanary:
    """Test resume mode skips canary bootstrap entirely."""

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_resume_sets_canary_passed_true(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """On resume, _canary_passed is set to True immediately in run()."""
        config = _make_config(
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1"],
        )

        with patch(
            "bmad_assist_lite.parallel.orchestrator.load_state",
        ) as mock_load:
            # Simulate existing state with in-flight story
            from bmad_assist_lite.parallel.state import (
                ParallelState,
                StoryState,
            )
            existing_state = ParallelState(
                base_branch="main",
                epic=3,
                started_at=__import__("datetime").datetime.now(),
                stories={
                    "3.1": StoryState(
                        status=StoryStatus.IN_FLIGHT,
                        worktree_path=Path("/fake/wt"),
                    ),
                },
            )
            mock_load.return_value = existing_state

            with patch(
                "bmad_assist_lite.parallel.orchestrator.save_state",
            ), patch(
                "bmad_assist_lite.parallel.orchestrator.recover_state",
                return_value=existing_state,
            ):
                orch = Orchestrator(
                    dependency_graph=graph,
                    config=config,
                    project_root=Path("/fake/project"),
                    epic_num=3,
                )
                orch._output_mux = _mock_output_mux()

        # Orchestrator has stale in-flight story from recovered state
        assert orch._in_flight_ids == {"3.1"}
        assert orch._canary_passed is False  # Not set yet — happens in run()

        # Mock subprocess for the resume re-spawn
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        with patch.object(
            orch, "_on_story_complete", new_callable=AsyncMock,
        ) as mock_complete:
            async def complete_side_effect(sid: str, code: int) -> None:
                orch._merging_ids.add(sid)
                orch._in_flight_ids.discard(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            await orch.run()

        # After run(), resume detection should have set _canary_passed=True
        assert orch._canary_passed is True
        # bootstrap_worktree should NOT have been called (resume skips canary)
        mock_bootstrap.assert_not_called()

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_resume_spawn_skips_bootstrap(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """On resume=True, _spawn_story skips bootstrap."""
        config = _make_config(
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        orch = _make_orchestrator(config=config)
        orch._canary_passed = True

        wt_path = Path("/fake/worktree")
        orch._story_worktrees["3.2"] = wt_path
        orch._in_flight_ids.add("3.2")

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        await orch._spawn_story("3.2", resume=True)

        # bootstrap_worktree should NOT be called via to_thread on resume
        # to_thread may not be called at all for resume path
        for call in mock_to_thread.call_args_list:
            func = call[0][0]
            if hasattr(func, '__name__') and isinstance(func.__name__, str):
                assert func.__name__ != "bootstrap_worktree"


# ============================================================================
# TestAllStoriesFailBootstrap (Edge Cases)
# ============================================================================


class TestEdgeCases:
    """Test edge cases for bootstrap integration."""

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    async def test_all_non_canary_fail_bootstrap(
        self,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """All non-canary stories fail bootstrap — all marked BLOCKED."""
        config = _make_config(setup_commands=["pip install"])
        orch = _make_orchestrator(config=config)
        orch._canary_passed = True

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(
            success=False,
            failed_phase="setup",
            error_message="failed",
            output="err",
        )
        mock_to_thread.side_effect = [wt_path, bootstrap_result, None]

        orch._in_flight_ids.add("3.2")
        result = await orch._spawn_story("3.2")

        assert result == -2
        assert "3.2" in orch._blocked_ids

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_non_canary_success_proceeds_to_subprocess(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Non-canary bootstrap success leads to subprocess spawn."""
        config = _make_config(setup_commands=["pip install"])
        orch = _make_orchestrator(config=config)
        orch._canary_passed = True

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(success=True, output="ok")
        mock_to_thread.side_effect = [wt_path, bootstrap_result]

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_exec.return_value = mock_proc

        orch._in_flight_ids.add("3.2")
        result = await orch._spawn_story("3.2")

        assert result == 0
        mock_exec.assert_called_once()

    @patch("bmad_assist_lite.parallel.orchestrator.bootstrap_worktree")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread")
    @patch("bmad_assist_lite.parallel.orchestrator.asyncio.create_subprocess_exec")
    async def test_drain_during_canary_bootstrap(
        self,
        mock_exec: AsyncMock,
        mock_to_thread: AsyncMock,
        mock_bootstrap: MagicMock,
    ) -> None:
        """Drain mode activated during canary bootstrap is handled gracefully.

        If drain mode is set while the blocking bootstrap_worktree call
        runs, the canary completes its bootstrap. If it passes, the
        canary story subprocess spawns but the main loop then detects
        drain mode and exits without spawning additional stories.
        """
        config = _make_config(
            setup_commands=["pip install"],
            validation_command="pytest",
        )
        graph = _make_graph(
            ready_sequence=[["3.1", "3.2"], []],
            all_ids=["3.1", "3.2"],
        )
        orch = _make_orchestrator(graph=graph, config=config)

        wt_path = Path("/fake/worktree")
        bootstrap_result = BootstrapResult(success=True, output="ok")

        async def to_thread_with_drain(*args, **kwargs):
            """Simulate drain being set during bootstrap."""
            if len(mock_to_thread.call_args_list) == 1:
                # This is the create_worktree call — return path
                return wt_path
            # This is the bootstrap call — set drain and return
            orch._draining = True
            return bootstrap_result

        mock_to_thread.side_effect = to_thread_with_drain

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.stdout = MagicMock()
        mock_proc.wait = AsyncMock(return_value=-1)
        mock_exec.return_value = mock_proc

        with patch.object(
            orch, "_on_story_complete", new_callable=AsyncMock,
        ) as mock_complete:
            async def complete_side_effect(
                sid: str, code: int,
            ) -> None:
                orch._in_flight_ids.discard(sid)
                task = orch._running_tasks.pop(sid, None)
                if task:
                    orch._task_to_story.pop(task, None)

            mock_complete.side_effect = complete_side_effect
            await orch.run()

        # Canary should have passed and completed
        assert orch._canary_passed is True
        # Drain should be set
        assert orch._draining is True
        # Story 3.2 should NOT have been spawned (drain active)
        assert "3.2" not in orch._in_flight_ids
