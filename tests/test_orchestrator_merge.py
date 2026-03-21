"""Tests for merge processing and blocked story handling in the Orchestrator.

Covers:
- MergeQueue integration (enqueue, process, state transitions)
- _process_merge_queue() outcomes: done, blocked (conflict), blocked (QG failure)
- Blocked dependency cascade prevention via get_ready_stories()
- Exit summary with error details distinguishing block sources
- Stalemate detection with blocked stories from merge/QG failures
"""

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.merger import GateResult, MergeResult, PostMergeQGResult
from bmad_assist_lite.parallel.orchestrator import Orchestrator
from bmad_assist_lite.parallel.state import StoryStatus


# ============================================================================
# Module-level fixtures — mock state persistence for all orchestrator tests
# ============================================================================


@pytest.fixture(autouse=True)
def _mock_state_persistence():
    """Prevent orchestrator tests from hitting the real filesystem.

    Patches load_state (returns None -> fresh state) and save_state (no-op)
    so Orchestrator.__init__ works with fake project_root paths.
    """
    with patch(
        "bmad_assist_lite.parallel.orchestrator.load_state", return_value=None,
    ), patch(
        "bmad_assist_lite.parallel.orchestrator.save_state",
    ):
        yield


# ============================================================================
# Helper factories
# ============================================================================


def _make_config(
    max_concurrency: int = 3,
    stagger_delay: float = 0.0,
    worktree_base_dir: Path | None = None,
    post_merge_fix_retries: int = 1,
) -> ParallelConfig:
    """Create a ParallelConfig with test-friendly defaults."""
    return ParallelConfig(
        max_concurrency=max_concurrency,
        stagger_delay=stagger_delay,
        worktree_base_dir=worktree_base_dir,
        post_merge_fix_retries=post_merge_fix_retries,
    )


def _make_graph(
    ready_sequence: list[list[str]] | None = None,
    all_ids: list[str] | None = None,
    story_count: int = 0,
    dependencies: dict[str, list[str]] | None = None,
) -> MagicMock:
    """Create a mock DependencyGraph with configurable ready story sequences.

    Args:
        ready_sequence: List of lists; each call to get_ready_stories returns
            the next list. After exhaustion, returns empty lists.
        all_ids: List of all story IDs in the graph.
        story_count: Number of stories in the graph.
        dependencies: Dict mapping story_id -> list of dependency IDs.

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

    # Configure dependencies_of for exit summary tests
    _deps = dependencies or {}
    graph.dependencies_of = MagicMock(
        side_effect=lambda sid: _deps.get(sid, [])
    )

    # Configure are_dependencies_satisfied for blocked-by-dependency counting.
    # Returns True only when all deps of sid are in the done_ids set.
    def _are_deps_satisfied(sid: str, done_ids: set[str]) -> bool:
        deps = _deps.get(sid, [])
        return all(d in done_ids for d in deps)

    graph.are_dependencies_satisfied = MagicMock(side_effect=_are_deps_satisfied)

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
        graph = _make_graph(all_ids=["3.1", "3.2", "3.3", "3.4"])
    orch = Orchestrator(
        dependency_graph=graph,
        config=config or _make_config(),
        project_root=project_root or Path("/fake/project"),
        epic_num=epic_num,
    )
    orch._output_mux = _mock_output_mux()
    return orch


def _make_merge_result(
    story_id: str = "3.1",
    *,
    success: bool = True,
    conflict_files: list[str] | None = None,
    error: str | None = None,
    qg_all_passed: bool | None = None,
) -> MergeResult:
    """Create a MergeResult for testing."""
    qg_result = None
    if qg_all_passed is not None:
        qg_result = PostMergeQGResult(
            all_passed=qg_all_passed,
            story_id=story_id,
            gate_results=[],
            duration_ms=100,
        )
    return MergeResult(
        success=success,
        story_id=story_id,
        conflict_files=conflict_files or [],
        error=error,
        qg_result=qg_result,
    )


# ============================================================================
# TestProcessMergeQueue — Task 2
# ============================================================================


class TestProcessMergeQueue:
    """Test _process_merge_queue() method and state transitions."""

    async def test_merge_success_with_passing_qg_transitions_to_done(self) -> None:
        """Successful merge + passing QG transitions story from merging to done."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")

        merge_result = _make_merge_result("3.1", success=True, qg_all_passed=True)

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"):
            await orch._process_merge_queue()

        assert "3.1" in orch._done_ids
        assert "3.1" not in orch._merging_ids
        assert "3.1" not in orch._blocked_ids

    async def test_merge_conflict_transitions_to_blocked(self) -> None:
        """Merge conflict (MergeResult.success=False) transitions to blocked."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")
        orch._story_worktrees["3.1"] = Path("/fake/worktree/3.1")

        merge_result = _make_merge_result(
            "3.1",
            success=False,
            conflict_files=["src/foo.py", "src/bar.py"],
            error="Merge conflict in 2 file(s)",
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"), \
             patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread", new_callable=AsyncMock):
            await orch._process_merge_queue()

        assert "3.1" in orch._blocked_ids
        assert "3.1" not in orch._merging_ids
        assert "3.1" not in orch._done_ids

    async def test_merge_conflict_records_error(self) -> None:
        """Merge conflict records the error description in state."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")
        orch._story_worktrees["3.1"] = Path("/fake/worktree/3.1")

        merge_result = _make_merge_result(
            "3.1",
            success=False,
            error="Merge conflict in 2 file(s)",
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"), \
             patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread", new_callable=AsyncMock):
            await orch._process_merge_queue()

        # Verify the state has the error
        story_state = orch._state.stories["3.1"]
        assert story_state.status == StoryStatus.BLOCKED
        assert "Merge conflict" in (story_state.error or "")

    async def test_merge_conflict_cleans_up_worktree(self) -> None:
        """Merge conflict cleans up the worktree."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")
        orch._story_worktrees["3.1"] = Path("/fake/worktree/3.1")

        merge_result = _make_merge_result(
            "3.1",
            success=False,
            error="Merge conflict in 2 file(s)",
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"), \
             patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread", new_callable=AsyncMock) as mock_tt:
            await orch._process_merge_queue()

        # Verify cleanup_worktree was called
        mock_tt.assert_called_once()
        call_args = mock_tt.call_args[0]
        assert call_args[0].__name__ == "cleanup_worktree"
        assert call_args[1] == "3.1"
        # Worktree mapping should be removed
        assert "3.1" not in orch._story_worktrees

    async def test_post_merge_qg_failure_transitions_to_blocked(self) -> None:
        """Successful merge but failing post-merge QG transitions to blocked."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")

        merge_result = _make_merge_result(
            "3.1", success=True, qg_all_passed=False,
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"):
            await orch._process_merge_queue()

        assert "3.1" in orch._blocked_ids
        assert "3.1" not in orch._merging_ids
        assert "3.1" not in orch._done_ids

    async def test_post_merge_qg_failure_records_error(self) -> None:
        """Post-merge QG failure records QG error in state."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")

        merge_result = _make_merge_result(
            "3.1", success=True, qg_all_passed=False,
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"):
            await orch._process_merge_queue()

        story_state = orch._state.stories["3.1"]
        assert story_state.status == StoryStatus.BLOCKED
        assert "quality gate" in (story_state.error or "").lower()

    async def test_post_merge_qg_failure_records_specific_gate_names(self) -> None:
        """Post-merge QG failure error includes names of failed gates."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")

        qg_result = PostMergeQGResult(
            all_passed=False,
            story_id="3.1",
            gate_results=[
                GateResult(
                    name="Lint", command="ruff check .", passed=True,
                    exit_code=0, stdout="", stderr="", duration_ms=50,
                ),
                GateResult(
                    name="Typecheck", command="mypy src", passed=False,
                    exit_code=1, stdout="error", stderr="", duration_ms=100,
                ),
                GateResult(
                    name="Tests", command="pytest", passed=False,
                    exit_code=1, stdout="FAILED", stderr="", duration_ms=200,
                ),
            ],
            duration_ms=350,
        )
        merge_result = MergeResult(
            success=True,
            story_id="3.1",
            conflict_files=[],
            qg_result=qg_result,
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"):
            await orch._process_merge_queue()

        story_state = orch._state.stories["3.1"]
        assert story_state.status == StoryStatus.BLOCKED
        assert "Typecheck" in (story_state.error or "")
        assert "Tests" in (story_state.error or "")
        assert "Lint" not in (story_state.error or "")  # Lint passed

    async def test_empty_merge_queue_no_state_changes(self) -> None:
        """Empty merge queue (process_merge_with_fix returns None) has no effect."""
        orch = _make_orchestrator()

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(return_value=None)
        orch._merge_queue = mock_mq

        original_done = set(orch._done_ids)
        original_blocked = set(orch._blocked_ids)
        original_merging = set(orch._merging_ids)

        await orch._process_merge_queue()

        assert orch._done_ids == original_done
        assert orch._blocked_ids == original_blocked
        assert orch._merging_ids == original_merging

    async def test_merge_success_logs_via_output_mux(self) -> None:
        """Successful merge writes a log message via write_orchestrator."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")

        merge_result = _make_merge_result("3.1", success=True, qg_all_passed=True)

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"):
            await orch._process_merge_queue()

        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        assert any("3.1" in m and "done" in m.lower() for m in messages)

    async def test_merge_conflict_logs_via_output_mux(self) -> None:
        """Merge conflict writes a log message via write_orchestrator."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")
        orch._story_worktrees["3.1"] = Path("/fake/worktree/3.1")

        merge_result = _make_merge_result(
            "3.1", success=False, error="Merge conflict in 2 file(s)",
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"), \
             patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread", new_callable=AsyncMock):
            await orch._process_merge_queue()

        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        assert any("3.1" in m and "blocked" in m.lower() for m in messages)

    async def test_merge_success_no_qg_transitions_to_done(self) -> None:
        """Merge success with no QG configured (qg_result is None) transitions to done."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")

        merge_result = _make_merge_result("3.1", success=True)
        # qg_all_passed not set -> qg_result is None

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"):
            await orch._process_merge_queue()

        assert "3.1" in orch._done_ids
        assert "3.1" not in orch._merging_ids
        assert "3.1" not in orch._blocked_ids
        # Verify log message mentions "no QG"
        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        assert any("no QG" in m for m in messages)

    async def test_processes_multiple_merges_in_sequence(self) -> None:
        """Multiple merges are processed in sequence until queue is empty."""
        orch = _make_orchestrator()
        orch._merging_ids.update({"3.1", "3.2"})

        result1 = _make_merge_result("3.1", success=True, qg_all_passed=True)
        result2 = _make_merge_result("3.2", success=True, qg_all_passed=True)

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[result1, result2, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"):
            await orch._process_merge_queue()

        assert "3.1" in orch._done_ids
        assert "3.2" in orch._done_ids


# ============================================================================
# TestMergeQueueIntegration — Task 1
# ============================================================================


class TestMergeQueueIntegration:
    """Test MergeQueue integration with orchestrator __init__ and _on_story_complete."""

    async def test_on_story_complete_enqueues_on_success(self) -> None:
        """Successful story (exit_code=0) is enqueued into MergeQueue."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.1"] = task
        orch._task_to_story[task] = "3.1"

        mock_mq = AsyncMock()
        mock_mq.enqueue = AsyncMock()
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"):
            await orch._on_story_complete("3.1", exit_code=0)

        mock_mq.enqueue.assert_called_once_with("3.1")

    async def test_on_story_complete_does_not_enqueue_on_failure(self) -> None:
        """Failed story (exit_code!=0) is NOT enqueued into MergeQueue."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.1"] = task
        orch._task_to_story[task] = "3.1"
        orch._story_worktrees["3.1"] = Path("/fake/worktree")

        mock_mq = AsyncMock()
        mock_mq.enqueue = AsyncMock()
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"), \
             patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread", new_callable=AsyncMock):
            await orch._on_story_complete("3.1", exit_code=1)

        mock_mq.enqueue.assert_not_called()

    async def test_merge_queue_enqueue_process_cycle(self) -> None:
        """Stories flow from merging -> enqueue -> merge -> done state transition."""
        orch = _make_orchestrator()
        orch._merging_ids.add("3.1")

        merge_result = _make_merge_result("3.1", success=True, qg_all_passed=True)

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[merge_result, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"):
            await orch._process_merge_queue()

        assert "3.1" in orch._done_ids
        assert "3.1" not in orch._merging_ids
        # Verify state was updated
        assert orch._state.stories["3.1"].status == StoryStatus.DONE


# ============================================================================
# TestBlockedDependencyCascade — Task 3
# ============================================================================


class TestBlockedDependencyCascade:
    """Test that blocked stories prevent dependent scheduling via get_ready_stories().

    Uses the real DependencyGraph to verify the implicit cascade behavior.
    """

    def test_blocked_dependency_prevents_scheduling(self) -> None:
        """Story with blocked dependency is NOT returned as ready."""
        from bmad_assist_lite.bmad.parser import EpicStory
        from bmad_assist_lite.parallel.dependency_graph import DependencyGraph

        stories = [
            EpicStory(number="3.1", title="Story 3.1"),
            EpicStory(number="3.2", title="Story 3.2"),
            EpicStory(
                number="3.3", title="Story 3.3",
                dependencies=["Story 3.1"],
            ),
            EpicStory(
                number="3.4", title="Story 3.4",
                dependencies=["Story 3.2"],
            ),
        ]
        graph = DependencyGraph(stories)

        # 3.1 is blocked, 3.2 is done
        ready = graph.get_ready_stories({"3.2"}, set(), {"3.1"})
        # 3.3 depends on 3.1 (blocked, not in done_ids) -> NOT ready
        assert "3.3" not in ready
        # 3.4 depends on 3.2 (done) -> IS ready
        assert "3.4" in ready

    def test_non_dependent_stories_continue(self) -> None:
        """Stories with no dependency on blocked stories are returned as ready."""
        from bmad_assist_lite.bmad.parser import EpicStory
        from bmad_assist_lite.parallel.dependency_graph import DependencyGraph

        stories = [
            EpicStory(number="3.1", title="Story 3.1"),
            EpicStory(number="3.2", title="Story 3.2"),
            EpicStory(number="3.3", title="Story 3.3"),
            EpicStory(
                number="3.4", title="Story 3.4",
                dependencies=["Story 3.1"],
            ),
        ]
        graph = DependencyGraph(stories)

        # 3.1 is blocked
        ready = graph.get_ready_stories(set(), set(), {"3.1"})
        # 3.2 and 3.3 are independent, ready
        assert "3.2" in ready
        assert "3.3" in ready
        # 3.4 depends on 3.1 which is blocked -> NOT ready
        assert "3.4" not in ready
        # 3.1 itself is blocked -> NOT ready
        assert "3.1" not in ready

    def test_transitive_blocked_cascade(self) -> None:
        """Transitive dependents of blocked stories are also not ready.

        If A is blocked, B depends on A, and C depends on B, then neither
        B nor C can be ready.
        """
        from bmad_assist_lite.bmad.parser import EpicStory
        from bmad_assist_lite.parallel.dependency_graph import DependencyGraph

        stories = [
            EpicStory(number="3.1", title="Story 3.1"),
            EpicStory(
                number="3.2", title="Story 3.2",
                dependencies=["Story 3.1"],
            ),
            EpicStory(
                number="3.3", title="Story 3.3",
                dependencies=["Story 3.2"],
            ),
        ]
        graph = DependencyGraph(stories)

        ready = graph.get_ready_stories(set(), set(), {"3.1"})
        # 3.1 is blocked, 3.2 depends on 3.1, 3.3 depends on 3.2
        # Neither 3.2 nor 3.3 can be ready
        assert ready == []

    def test_blocked_story_with_multiple_dependencies(self) -> None:
        """Story with one blocked and one done dependency is NOT ready."""
        from bmad_assist_lite.bmad.parser import EpicStory
        from bmad_assist_lite.parallel.dependency_graph import DependencyGraph

        stories = [
            EpicStory(number="3.1", title="Story 3.1"),
            EpicStory(number="3.2", title="Story 3.2"),
            EpicStory(
                number="3.3", title="Story 3.3",
                dependencies=["Story 3.1", "Story 3.2"],
            ),
        ]
        graph = DependencyGraph(stories)

        # 3.1 blocked, 3.2 done
        ready = graph.get_ready_stories({"3.2"}, set(), {"3.1"})
        # 3.3 needs both 3.1 and 3.2 in done_ids -> NOT ready
        assert "3.3" not in ready


# ============================================================================
# TestExitSummaryBlockSources — Task 4
# ============================================================================


class TestExitSummaryBlockSources:
    """Test exit summary distinguishes between block sources."""

    async def test_exit_summary_shows_error_for_blocked_stories(self) -> None:
        """Blocked stories show their error field in the exit summary."""
        graph = _make_graph(
            all_ids=["3.1", "3.2", "3.3"],
            dependencies={"3.1": [], "3.2": [], "3.3": ["3.1"]},
        )
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids = {"3.1"}
        # Set up state with error
        orch._state = orch._state.with_story_status(
            "3.1", StoryStatus.BLOCKED, error="Exit code 1",
        )

        await orch._print_exit_summary()

        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        blocked_msgs = [m for m in messages if "3.1" in m and "Blocked" in m]
        assert len(blocked_msgs) >= 1
        assert any("Exit code 1" in m for m in blocked_msgs)

    async def test_exit_summary_shows_merge_conflict_error(self) -> None:
        """Blocked story from merge conflict shows conflict error."""
        graph = _make_graph(
            all_ids=["3.1", "3.2"],
            dependencies={"3.1": [], "3.2": []},
        )
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids = {"3.1"}
        orch._state = orch._state.with_story_status(
            "3.1", StoryStatus.BLOCKED,
            error="Merge conflict in 3 file(s)",
        )

        await orch._print_exit_summary()

        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        blocked_msgs = [m for m in messages if "3.1" in m and "Blocked" in m]
        assert any("Merge conflict" in m for m in blocked_msgs)

    async def test_exit_summary_shows_qg_failure_error(self) -> None:
        """Blocked story from post-merge QG failure shows QG error."""
        graph = _make_graph(
            all_ids=["3.1"],
            dependencies={"3.1": []},
        )
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids = {"3.1"}
        orch._state = orch._state.with_story_status(
            "3.1", StoryStatus.BLOCKED,
            error="Post-merge quality gate failed",
        )

        await orch._print_exit_summary()

        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        blocked_msgs = [m for m in messages if "3.1" in m and "Blocked" in m]
        assert any("quality gate" in m.lower() for m in blocked_msgs)

    async def test_exit_summary_blocked_by_dependency_count(self) -> None:
        """Exit summary includes count of stories blocked by dependency."""
        graph = _make_graph(
            all_ids=["3.1", "3.2", "3.3", "3.4"],
            dependencies={
                "3.1": [],
                "3.2": ["3.1"],
                "3.3": ["3.1"],
                "3.4": [],
            },
        )
        # Configure dependents_of for cascade count
        graph.dependents_of = MagicMock(
            side_effect=lambda sid: {"3.1": ["3.2", "3.3"], "3.2": [], "3.3": [], "3.4": []}.get(sid, [])
        )
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids = {"3.1"}
        orch._state = orch._state.with_story_status(
            "3.1", StoryStatus.BLOCKED, error="Exit code 1",
        )

        await orch._print_exit_summary()

        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        # Should mention blocked-by-dependency count
        assert any("blocked-by-dependency" in m.lower() for m in messages)

    async def test_exit_summary_transitive_blocked_by_dependency_count(self) -> None:
        """Transitive dependents of blocked stories are counted as blocked-by-dep.

        If A is blocked, B depends on A, and C depends on B, both B and C
        should be counted as blocked-by-dependency (not just B).
        """
        from bmad_assist_lite.bmad.parser import EpicStory
        from bmad_assist_lite.parallel.dependency_graph import DependencyGraph

        stories = [
            EpicStory(number="3.1", title="Story 3.1"),
            EpicStory(
                number="3.2", title="Story 3.2",
                dependencies=["Story 3.1"],
            ),
            EpicStory(
                number="3.3", title="Story 3.3",
                dependencies=["Story 3.2"],
            ),
            EpicStory(number="3.4", title="Story 3.4"),
        ]
        graph = DependencyGraph(stories)
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids = {"3.1"}
        orch._state = orch._state.with_story_status(
            "3.1", StoryStatus.BLOCKED, error="Exit code 1",
        )

        await orch._print_exit_summary()

        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        # Both 3.2 (direct) and 3.3 (transitive) should be counted
        dep_msgs = [m for m in messages if "blocked-by-dependency" in m.lower()]
        assert len(dep_msgs) == 1
        assert "2" in dep_msgs[0]  # count should be 2 (3.2 and 3.3)

    async def test_exit_summary_multiple_block_sources(self) -> None:
        """Multiple blocked stories from different sources in exit summary."""
        graph = _make_graph(
            all_ids=["3.1", "3.2", "3.3"],
            dependencies={"3.1": [], "3.2": [], "3.3": []},
        )
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids = {"3.1", "3.2"}
        orch._state = orch._state.with_story_status(
            "3.1", StoryStatus.BLOCKED, error="Exit code 1",
        )
        orch._state = orch._state.with_story_status(
            "3.2", StoryStatus.BLOCKED,
            error="Merge conflict in 2 file(s)",
        )

        await orch._print_exit_summary()

        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        blocked_msgs = [m for m in messages if "Blocked" in m and ":" in m]
        # Should have at least 2 blocked story entries
        assert len(blocked_msgs) >= 2


# ============================================================================
# TestWorktreeQGFailure — Task 5.1
# ============================================================================


class TestWorktreeQGFailure:
    """Test worktree QG failure (exit_code != 0) marks story as blocked."""

    async def test_exit_code_nonzero_marks_blocked(self) -> None:
        """Story with exit code > 0 transitions to blocked."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.1"] = task
        orch._task_to_story[task] = "3.1"
        orch._in_flight_ids.add("3.1")
        orch._story_worktrees["3.1"] = Path("/fake/worktree/3.1")

        # No merge queue enqueue for failures
        mock_mq = AsyncMock()
        mock_mq.enqueue = AsyncMock()
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"), \
             patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread", new_callable=AsyncMock):
            await orch._on_story_complete("3.1", exit_code=1)

        assert "3.1" in orch._blocked_ids
        assert "3.1" not in orch._in_flight_ids
        mock_mq.enqueue.assert_not_called()

    async def test_exit_code_nonzero_records_error(self) -> None:
        """Error message records the exit code."""
        orch = _make_orchestrator()
        task = MagicMock()
        orch._running_tasks["3.1"] = task
        orch._task_to_story[task] = "3.1"
        orch._story_worktrees["3.1"] = Path("/fake/worktree/3.1")

        mock_mq = AsyncMock()
        mock_mq.enqueue = AsyncMock()
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"), \
             patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread", new_callable=AsyncMock):
            await orch._on_story_complete("3.1", exit_code=2)

        story_state = orch._state.stories["3.1"]
        assert "Exit code 2" in (story_state.error or "")


# ============================================================================
# TestStalemateWithMergeFailures — Task 5.10
# ============================================================================


class TestStalemateWithMergeFailures:
    """Test stalemate detection with blocked stories from merge/QG failures."""

    async def test_stalemate_when_all_remaining_depend_on_blocked(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """All remaining stories depend on blocked stories -> stalemate."""
        # Stories: 3.1 (blocked from merge), 3.2 depends on 3.1, 3.3 depends on 3.1
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1", "3.2", "3.3"],
        )
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids.add("3.1")
        orch._state = orch._state.with_story_status(
            "3.1", StoryStatus.BLOCKED,
            error="Merge conflict in 2 file(s)",
        )

        # Set up merge queue that returns None (empty)
        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(return_value=None)
        orch._merge_queue = mock_mq

        with caplog.at_level(logging.WARNING):
            await orch.run()

        assert any(
            "Stalemate" in r.message
            for r in caplog.records
        )

    async def test_stalemate_with_mixed_block_sources(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Stalemate with one QG-blocked and one merge-blocked story."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1", "3.2", "3.3", "3.4"],
        )
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids.update({"3.1", "3.2"})
        orch._state = orch._state.with_story_status(
            "3.1", StoryStatus.BLOCKED, error="Exit code 1",
        )
        orch._state = orch._state.with_story_status(
            "3.2", StoryStatus.BLOCKED,
            error="Post-merge quality gate failed",
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(return_value=None)
        orch._merge_queue = mock_mq

        with caplog.at_level(logging.WARNING):
            await orch.run()

        # 3.3 and 3.4 are remaining
        assert any(
            "Stalemate" in r.message and "Remaining: 2" in r.message
            for r in caplog.records
        )


# ============================================================================
# TestMultipleBlockSources — Task 5.8
# ============================================================================


class TestMultipleBlockSources:
    """Test multiple block sources in the same run."""

    async def test_qg_failure_and_merge_conflict_both_handled(self) -> None:
        """One story blocked from QG failure, another from merge conflict."""
        orch = _make_orchestrator()
        orch._merging_ids.update({"3.1", "3.2"})
        orch._story_worktrees["3.2"] = Path("/fake/worktree/3.2")

        result1 = _make_merge_result(
            "3.1", success=True, qg_all_passed=False,
        )
        result2 = _make_merge_result(
            "3.2", success=False,
            error="Merge conflict in 1 file(s)",
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(
            side_effect=[result1, result2, None]
        )
        orch._merge_queue = mock_mq

        with patch("bmad_assist_lite.parallel.orchestrator.save_state"), \
             patch("bmad_assist_lite.parallel.orchestrator.asyncio.to_thread", new_callable=AsyncMock):
            await orch._process_merge_queue()

        assert "3.1" in orch._blocked_ids
        assert "3.2" in orch._blocked_ids
        assert "3.1" not in orch._done_ids
        assert "3.2" not in orch._done_ids

        # Verify different error messages
        s1 = orch._state.stories["3.1"]
        s2 = orch._state.stories["3.2"]
        assert "quality gate" in (s1.error or "").lower()
        assert "merge conflict" in (s2.error or "").lower()

    async def test_all_stories_blocked_exits_cleanly(self) -> None:
        """All stories blocked -> orchestrator exits cleanly with summary."""
        graph = _make_graph(
            ready_sequence=[[]],
            all_ids=["3.1", "3.2", "3.3"],
        )
        orch = _make_orchestrator(graph=graph)
        orch._blocked_ids.update({"3.1", "3.2", "3.3"})
        orch._state = orch._state.with_story_status(
            "3.1", StoryStatus.BLOCKED, error="Exit code 1",
        )
        orch._state = orch._state.with_story_status(
            "3.2", StoryStatus.BLOCKED, error="Merge conflict in 1 file(s)",
        )
        orch._state = orch._state.with_story_status(
            "3.3", StoryStatus.BLOCKED, error="Post-merge quality gate failed",
        )

        mock_mq = AsyncMock()
        mock_mq.process_merge_with_fix = AsyncMock(return_value=None)
        orch._merge_queue = mock_mq

        # Should exit cleanly without error
        await orch.run()

        messages = [
            c[0][0] for c in orch._output_mux.write_orchestrator.call_args_list
        ]
        assert any("Exit summary" in m for m in messages)
        assert any("Blocked: 3" in m for m in messages)
