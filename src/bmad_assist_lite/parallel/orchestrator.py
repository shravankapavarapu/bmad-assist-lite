"""Orchestrate parallel story execution via asyncio subprocess spawning.

Spawns loop subprocesses in git worktrees for each ready story, monitors
their completion, and manages the story lifecycle (in-flight, merging,
blocked). Concurrency is controlled by an asyncio semaphore and an
optional stagger delay between spawns. Supports graceful shutdown (drain
mode) and force-exit via signal handling.
"""

import asyncio
import contextlib
import logging
import os
import re
import signal
import subprocess
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.dependency_graph import DependencyGraph
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.logging import (
    log_dependency_unlocked,
    log_merge_queued,
    log_merge_result,
    log_qg_result,
    log_run_complete,
    log_run_header,
    log_story_blocked,
    log_story_completed,
    log_story_started,
    log_teardown_result,
    setup_parallel_log,
    teardown_parallel_log,
)
from bmad_assist_lite.parallel.merger import MergeQueue
from bmad_assist_lite.parallel.output import OutputMultiplexer
from bmad_assist_lite.parallel.recovery import recover_state
from bmad_assist_lite.parallel.report import (
    MergeOutcome,
    build_report,
    render_report,
    write_report,
)
from bmad_assist_lite.parallel.state import (
    ParallelState,
    StoryStatus,
    create_initial_state,
    get_parallel_state_path,
    load_state,
    save_state,
)
from bmad_assist_lite.parallel.worktree_manager import cleanup_worktree, create_worktree

logger = logging.getLogger(__name__)

# Pattern to extract the story number from a story_id like "3.2", "3-2", "10.1"
_STORY_NUM_RE = re.compile(r"[-.]")


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info (naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


# ============================================================================
# Story number extraction
# ============================================================================


def _extract_story_num(story_id: str) -> str:
    """Extract the story number (last segment) from a story ID.

    Splits on ``"."`` or ``"-"`` and returns the last segment.

    Args:
        story_id: A story identifier, e.g. ``"3.2"``, ``"3-2"``, ``"3.10"``.

    Returns:
        The last numeric segment (e.g. ``"2"``, ``"2"``, ``"10"``).

    Raises:
        ParallelError: If the story_id has no separator or is empty.

    """
    parts = _STORY_NUM_RE.split(story_id)
    if len(parts) < 2:  # noqa: PLR2004
        msg = f"Cannot extract story number from story_id: {story_id!r}"
        raise ParallelError(msg)
    return parts[-1]


# ============================================================================
# Platform-safe process termination
# ============================================================================


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess and its entire process tree.

    Uses platform-appropriate termination: ``taskkill /F /T`` on Windows
    for process-tree kill, ``os.killpg()`` on Unix (since subprocesses
    are spawned with ``start_new_session=True``, making them process
    group leaders).

    Args:
        proc: The asyncio subprocess to terminate.

    """
    pid = proc.pid
    if pid is None:
        return

    try:
        if sys.platform == "win32":
            # Use taskkill for process-tree termination on Windows
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    # taskkill failed (e.g. access denied) — fallback to basic kill
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
            except Exception:
                # Fallback to basic kill on any exception (FileNotFoundError, etc.)
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        else:
            # Use os.killpg() for process-group kill since subprocesses
            # are spawned with start_new_session=True (making them
            # process group leaders). proc.kill() only kills the leader,
            # leaving child processes (git, pytest) orphaned.
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                # Fallback to basic kill if process group kill fails
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
    except Exception as exc:
        logger.warning("[ORCHESTRATOR] Failed to kill process PID %d: %s", pid, exc)

    # Always wait to avoid zombies — use timeout to prevent indefinite hang
    try:
        await asyncio.wait_for(proc.wait(), timeout=15)
    except TimeoutError:
        logger.warning(
            "[ORCHESTRATOR] Process PID %d did not exit within 15s after kill", pid,
        )
    except Exception:
        pass


# ============================================================================
# Orchestrator
# ============================================================================


class Orchestrator:
    """Asyncio orchestrator for parallel story execution.

    Spawns loop subprocesses in git worktrees, monitors their completion
    via ``asyncio.wait(FIRST_COMPLETED)``, and manages concurrency via
    ``asyncio.Semaphore``. Supports graceful shutdown via drain mode
    (first Ctrl+C) and force-exit (second Ctrl+C).
    """

    def __init__(
        self,
        dependency_graph: DependencyGraph,
        config: ParallelConfig,
        project_root: Path,
        epic_num: int,
        *,
        base_branch: str = "main",
    ) -> None:
        """Initialize the orchestrator with injected dependencies.

        Args:
            dependency_graph: Pre-built DAG of story dependencies.
            config: Parallel execution configuration.
            project_root: Path to the main git repository.
            epic_num: The epic number being executed.
            base_branch: The git branch stories are based on.

        """
        self._dependency_graph = dependency_graph
        self._config = config
        self._project_root = project_root
        self._epic_num = epic_num

        # Concurrency control
        self._semaphore = asyncio.Semaphore(config.max_concurrency)

        # Active task tracking
        self._running_tasks: dict[str, asyncio.Task[int]] = {}
        self._task_to_story: dict[asyncio.Task[int], str] = {}
        self._story_worktrees: dict[str, Path] = {}

        # Output multiplexer for live prefixed output
        self._output_mux = OutputMultiplexer()

        # Merge queue — sequential merge with post-merge QG fix retries.
        # process_merge_with_fix() is async and handles asyncio.to_thread()
        # wrapping internally for sync git/QG operations.
        self._merge_queue: MergeQueue = MergeQueue(
            project_root, parallel_config=config,
        )

        # Status tracking sets
        self._done_ids: set[str] = set()
        self._in_flight_ids: set[str] = set()
        self._blocked_ids: set[str] = set()
        self._merging_ids: set[str] = set()

        # Report data accumulation (Story 6.3)
        self._merge_outcomes: list[MergeOutcome] = []
        # Set in run() to capture actual orchestration start, not __init__
        self._orchestrator_started_at: datetime | None = None

        # Shutdown flags (Task 1.1)
        self._draining: bool = False
        self._force_exit: bool = False
        # Event loop reference — set during _install_signal_handlers()
        # when the loop is guaranteed running. Used by _on_sigint() to
        # avoid deprecated asyncio.get_event_loop() in signal context.
        self._loop: asyncio.AbstractEventLoop | None = None

        # Teardown process tracking — set during _run_epic_teardown()
        # so _on_sigint() can terminate it on Ctrl+C
        self._teardown_process: asyncio.subprocess.Process | None = None

        # Persistent state — load existing or create fresh
        self._state_path = get_parallel_state_path(project_root)
        existing_state = load_state(self._state_path)
        if existing_state is not None:
            # Run crash recovery to reconcile state against on-disk worktrees
            self._state: ParallelState = recover_state(
                existing_state, project_root, config.worktree_base_dir,
            )
            # Populate in-memory sets from RECOVERED state
            for story_id, story_state in self._state.stories.items():
                if story_state.status == StoryStatus.DONE:
                    self._done_ids.add(story_id)
                elif story_state.status == StoryStatus.IN_FLIGHT:
                    self._in_flight_ids.add(story_id)
                elif story_state.status == StoryStatus.BLOCKED:
                    self._blocked_ids.add(story_id)
                elif story_state.status == StoryStatus.MERGING:
                    self._merging_ids.add(story_id)
                if story_state.worktree_path is not None:
                    self._story_worktrees[story_id] = story_state.worktree_path
            logger.info(
                "[ORCHESTRATOR] Resumed from persisted state: "
                "done=%d, in_flight=%d, blocked=%d, merging=%d",
                len(self._done_ids),
                len(self._in_flight_ids),
                len(self._blocked_ids),
                len(self._merging_ids),
            )
        else:
            self._state = create_initial_state(
                base_branch=base_branch,
                epic=epic_num,
                story_ids=list(dependency_graph.all_story_ids),
            )
            save_state(self._state, self._state_path)
            logger.info(
                "[ORCHESTRATOR] Created fresh parallel state with %d stories",
                len(self._state.stories),
            )

    # ========================================================================
    # Signal handling (Task 1, Task 2)
    # ========================================================================

    def _on_sigint(self) -> None:
        """Handle SIGINT/SIGTERM with two-tier shutdown logic.

        First call enters drain mode (stop spawning, wait for running).
        Second call triggers force-exit (cancel all tasks immediately).
        Third+ calls are idempotent (no-op after force_exit is set).
        """
        if self._force_exit:
            # Already force-exiting, ignore further signals
            return

        loop = self._loop
        if loop is None or loop.is_closed():
            # No loop available — just set flags, skip messages
            if self._draining:
                self._force_exit = True
            else:
                self._draining = True
            return

        if self._draining:
            # Second signal: force-exit
            self._force_exit = True
            # Use lambda to defer coroutine creation to the event loop
            # thread, avoiding eager coroutine creation in signal context
            loop.call_soon_threadsafe(
                lambda: loop.create_task(
                    self._output_mux.write_orchestrator(
                        "Force shutdown -- terminating all subprocesses..."
                    )
                ),
            )
            # Terminate teardown subprocess if active
            if self._teardown_process is not None:
                td_proc = self._teardown_process
                loop.call_soon_threadsafe(
                    lambda: loop.create_task(self._kill_teardown_process(td_proc)),
                )
        else:
            # First signal: drain mode
            self._draining = True
            loop.call_soon_threadsafe(
                lambda: loop.create_task(
                    self._output_mux.write_orchestrator(
                        "Shutting down -- waiting for running stories to finish..."
                    )
                ),
            )
            # Terminate teardown subprocess if active (single signal
            # during teardown means stop immediately)
            if self._teardown_process is not None:
                td_proc = self._teardown_process
                loop.call_soon_threadsafe(
                    lambda: loop.create_task(self._kill_teardown_process(td_proc)),
                )

    def _install_signal_handlers(self) -> None:
        """Install SIGINT and SIGTERM handlers for graceful shutdown.

        On Unix, uses ``loop.add_signal_handler()`` for both SIGINT and
        SIGTERM (asyncio-native). On Windows, uses ``signal.signal()``
        for SIGINT only (asyncio signal handlers not supported on Windows;
        SIGTERM is not raised on Windows).

        Captures the running event loop reference for use by
        ``_on_sigint()`` to avoid deprecated ``asyncio.get_event_loop()``.
        """
        self._loop = asyncio.get_running_loop()
        if sys.platform == "win32":
            signal.signal(signal.SIGINT, self._signal_handler_sync)
        else:
            self._loop.add_signal_handler(signal.SIGINT, self._on_sigint)
            self._loop.add_signal_handler(signal.SIGTERM, self._on_sigint)

    def _remove_signal_handlers(self) -> None:
        """Restore default signal disposition on exit.

        On Unix, removes asyncio signal handlers for SIGINT and SIGTERM.
        On Windows, restores SIGINT to default behavior.
        """
        if sys.platform == "win32":
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        elif self._loop is not None and not self._loop.is_closed():
            with contextlib.suppress(Exception):
                self._loop.remove_signal_handler(signal.SIGINT)
            with contextlib.suppress(Exception):
                self._loop.remove_signal_handler(signal.SIGTERM)

    def _signal_handler_sync(
        self, signum: int, frame: types.FrameType | None,
    ) -> None:
        """Sync signal handler for Windows SIGINT.

        Delegates to ``_on_sigint()``. The sync handler runs in the main
        thread; Python's GIL ensures atomic flag writes are safe.

        Args:
            signum: The signal number received.
            frame: The current stack frame (unused).

        """
        self._on_sigint()

    # ========================================================================
    # Subprocess spawning
    # ========================================================================

    async def _spawn_story(
        self, story_id: str, *, resume: bool = False,
    ) -> int:
        """Spawn a story loop subprocess in a dedicated worktree.

        Acquires a semaphore slot, creates the worktree (or reuses an
        existing one when ``resume=True``), applies the stagger delay,
        then spawns the subprocess and waits for it.

        Subprocesses are isolated from the parent's console process group
        via ``start_new_session=True`` (Unix) or
        ``CREATE_NEW_PROCESS_GROUP`` (Windows) to prevent Ctrl+C from
        propagating to child processes and defeating drain mode.

        Args:
            story_id: The story ID to execute (e.g. ``"3.2"``).
            resume: If True, skip worktree creation, use the existing
                ``worktree_path`` from state, and append ``--resume``
                to the CLI args.

        Returns:
            The subprocess exit code.

        """
        proc: asyncio.subprocess.Process | None = None

        async with self._semaphore:
            try:
                # Apply stagger delay inside semaphore to avoid blocking
                # the main loop dispatcher during delay
                if self._config.stagger_delay > 0:
                    await asyncio.sleep(self._config.stagger_delay)

                # Check drain flag after stagger sleep to avoid spawning
                # a new subprocess if shutdown was requested during delay
                if self._draining:
                    return -1

                if resume:
                    # Re-spawn in existing worktree — skip create_worktree
                    worktree_path = self._story_worktrees.get(story_id)
                    if worktree_path is None:
                        # Fallback: look up from state
                        story_state = self._state.stories.get(story_id)
                        if story_state is not None and story_state.worktree_path is not None:
                            worktree_path = story_state.worktree_path
                            self._story_worktrees[story_id] = worktree_path
                        else:
                            logger.error(
                                "[ORCHESTRATOR] Cannot resume story %s: "
                                "no worktree path found",
                                story_id,
                            )
                            return -1
                    logger.info(
                        "[ORCHESTRATOR] Re-spawning story %s with --resume "
                        "in existing worktree %s",
                        story_id,
                        worktree_path,
                    )
                else:
                    # Create worktree (sync function, bridge via to_thread)
                    worktree_path = await asyncio.to_thread(
                        create_worktree,
                        story_id,
                        self._project_root,
                        self._config.worktree_base_dir,
                    )
                    self._story_worktrees[story_id] = worktree_path

                    # Persist worktree_path to state for crash recovery (AC #2)
                    self._state = self._state.with_story_status(
                        story_id,
                        StoryStatus.IN_FLIGHT,
                        worktree_path=worktree_path,
                    )
                    save_state(self._state, self._state_path)

                # Extract story number for CLI flag
                story_num = _extract_story_num(story_id)

                # Build environment
                env = {**os.environ, "BMAD_PARALLEL_MODE": "1"}

                # Spawn subprocess
                await self._output_mux.write_orchestrator(
                    f"Spawning story {story_id} in worktree {worktree_path}"
                )
                exec_args: list[str] = [
                    sys.executable,
                    "-m",
                    "bmad_assist_lite",
                    "run",
                    "--epic",
                    str(self._epic_num),
                    "--story",
                    str(story_num),
                    "--single-story",
                ]

                # Append --resume flag for crash recovery re-spawn
                if resume:
                    exec_args.append("--resume")
                # Task 3.0: Subprocess process group isolation
                if sys.platform == "win32":
                    proc = await asyncio.create_subprocess_exec(
                        *exec_args,
                        cwd=str(worktree_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        env=env,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        *exec_args,
                        cwd=str(worktree_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        env=env,
                        start_new_session=True,
                    )

                # Log story started after successful spawn
                log_story_started(story_id, worktree_path)

                # Start output reader for live prefixed output
                assert proc.stdout is not None  # guaranteed by PIPE
                self._output_mux.start_reader(
                    story_id, proc.stdout
                )

                # Wait for process exit — reader task drains stdout
                await proc.wait()
                return_code = proc.returncode
                if return_code is None:  # pragma: no cover
                    return_code = -1

                await self._output_mux.write_orchestrator(
                    f"Story {story_id} exited with code {return_code}"
                )
                return return_code

            except asyncio.CancelledError:
                logger.warning(
                    "[ORCHESTRATOR] Story %s task cancelled, killing subprocess",
                    story_id,
                )
                raise

            except Exception as exc:
                logger.error(
                    "[ORCHESTRATOR] Story %s spawn/execution failed: %s",
                    story_id,
                    exc,
                )
                raise

            finally:
                # Drain reader to EOF before cleanup — ensures no output is
                # lost from the asyncio buffer after process exit. Skip the
                # drain during force-exit to avoid a 5s delay per story.
                if not self._force_exit:
                    try:
                        await self._output_mux.await_reader(story_id, timeout=5.0)
                    except Exception:
                        logger.debug(
                            "[ORCHESTRATOR] Reader drain interrupted for story %s",
                            story_id,
                        )
                # Fallback: force-stop if reader didn't complete
                try:
                    await self._output_mux.stop_reader(story_id)
                except Exception:
                    logger.debug(
                        "[ORCHESTRATOR] Reader stop failed for story %s",
                        story_id,
                    )

                # Process cleanup in finally guarantees termination for ALL
                # exception types including BaseException (KeyboardInterrupt,
                # SystemExit). Use asyncio.shield to prevent CancelledError
                # from aborting cleanup midway. Skip if process already exited
                # (returncode is set by wait on success).
                if proc is not None and proc.returncode is None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await asyncio.shield(_kill_process(proc))

    # ========================================================================
    # Completion handling
    # ========================================================================

    async def _on_story_complete(self, story_id: str, exit_code: int) -> None:
        """Handle a completed story subprocess.

        Transitions the story to ``_merging_ids`` on success or
        ``_blocked_ids`` on failure, and cleans up all tracking state.
        This is the single authority for story lifecycle transitions.

        Args:
            story_id: The story that completed.
            exit_code: The subprocess exit code.

        """
        # Clean up all tracking state atomically — single source of truth
        self._in_flight_ids.discard(story_id)
        task = self._running_tasks.pop(story_id, None)
        if task is not None:
            self._task_to_story.pop(task, None)

        # Log story completion to parallel-run.log
        log_story_completed(story_id, exit_code)

        if exit_code == 0:
            self._merging_ids.add(story_id)
            self._state = self._state.with_story_status(
                story_id,
                StoryStatus.MERGING,
            )
            save_state(self._state, self._state_path)
            await self._output_mux.write_orchestrator(
                f"Story {story_id} completed successfully, status -> merging"
            )
            # Log merge queued and enqueue for merge processing
            log_merge_queued(story_id)
            await self._merge_queue.enqueue(story_id)
        else:
            self._blocked_ids.add(story_id)
            self._state = self._state.with_story_status(
                story_id,
                StoryStatus.BLOCKED,
                error=f"Exit code {exit_code}",
                completed_at=_utc_now(),
            )
            save_state(self._state, self._state_path)
            log_story_blocked(story_id, f"Exit code {exit_code}")
            await self._output_mux.write_orchestrator(
                f"Story {story_id} failed with exit code {exit_code}, status -> blocked"
            )
            # Clean up worktree for blocked stories (successful keep for merge)
            if story_id in self._story_worktrees:
                try:
                    await asyncio.to_thread(
                        cleanup_worktree,
                        story_id,
                        self._project_root,
                        self._config.worktree_base_dir,
                    )
                except Exception as exc:
                    logger.warning(
                        "[ORCHESTRATOR] Worktree cleanup failed for %s: %s",
                        story_id,
                        exc,
                    )
                finally:
                    # Remove worktree path mapping for blocked stories
                    del self._story_worktrees[story_id]

    # ========================================================================
    # Dependency unlock logging
    # ========================================================================

    def _log_unlocked_dependents(self, completed_story_id: str) -> None:
        """Log stories whose dependencies are now satisfied.

        Called after a story moves to DONE. Checks all dependents of the
        completed story and logs those that are now fully unblocked.

        Args:
            completed_story_id: The story that just completed.

        """
        completed_set = self._done_ids | self._merging_ids
        try:
            dependents = self._dependency_graph.dependents_of(completed_story_id)
        except (KeyError, AttributeError):
            return
        for dep_id in dependents:
            if dep_id in self._done_ids or dep_id in self._blocked_ids:
                continue
            try:
                if self._dependency_graph.are_dependencies_satisfied(
                    dep_id, completed_set,
                ):
                    log_dependency_unlocked(dep_id, completed_story_id)
            except (KeyError, AttributeError):
                continue

    # ========================================================================
    # Merge queue processing
    # ========================================================================

    async def _process_merge_queue(self) -> None:
        """Drain the merge queue one story at a time.

        Calls ``process_merge_with_fix()`` in a loop until it returns ``None``
        (empty queue). For each result, transitions the story state:

        - **merge + QG success**: ``merging`` -> ``done``
        - **merge conflict**: ``merging`` -> ``blocked`` (worktree cleaned)
        - **merge success + QG failure**: ``merging`` -> ``blocked``

        Note: ``process_merge_with_fix()`` is async and handles
        ``asyncio.to_thread()`` wrapping internally for sync git/QG
        operations — the orchestrator does NOT add its own thread-bridging.
        Sprint-status update (FR26) is handled inside ``process_merge_with_fix()``
        via ``update_sprint_status_done()`` — NOT duplicated here.
        """
        while True:
            merge_start = _utc_now()
            result = await self._merge_queue.process_merge_with_fix()
            if result is None:
                break
            merge_elapsed = (
                _utc_now() - merge_start
            ).total_seconds()

            story_id = result.story_id

            # Log merge result to parallel-run.log
            log_merge_result(story_id, result.success, result.error)

            # Record MergeOutcome for summary report (Story 6.3)
            had_conflicts = bool(result.conflict_files)
            conflicts_resolved = had_conflicts and result.success
            # QG is considered "passed" when it ran and all gates passed,
            # OR when no QG was configured (qg_result is None) and the
            # merge itself succeeded — skipping QG is not a failure.
            qg_passed = result.success and (
                result.qg_result is None or result.qg_result.all_passed
            )
            # qg_fixed is forward-compatible — set False until retry-fix
            # flow tracks initial QG failure + subsequent fix success
            self._merge_outcomes.append(
                MergeOutcome(
                    story_id=story_id,
                    merged=result.success,
                    had_conflicts=had_conflicts,
                    conflicts_resolved=conflicts_resolved,
                    qg_passed=qg_passed,
                    qg_fixed=False,
                    duration_seconds=merge_elapsed,
                )
            )

            if result.success and result.qg_result is not None and result.qg_result.all_passed:
                # Merge + QG success -> done
                log_qg_result(
                    story_id, True, list(result.qg_result.gate_results),
                )
                self._done_ids.add(story_id)
                self._merging_ids.discard(story_id)
                self._state = self._state.with_story_status(
                    story_id,
                    StoryStatus.DONE,
                    completed_at=_utc_now(),
                )
                save_state(self._state, self._state_path)
                # Clean up worktree mapping (worktree already removed by merge_story)
                self._story_worktrees.pop(story_id, None)
                self._log_unlocked_dependents(story_id)
                await self._output_mux.write_orchestrator(
                    f"Story {story_id} merged and QG passed, status -> done"
                )

            elif result.success and result.qg_result is not None and not result.qg_result.all_passed:
                # Merge success but QG failure after fix retries -> blocked
                log_qg_result(
                    story_id,
                    False,
                    list(result.qg_result.gate_results),
                )
                self._blocked_ids.add(story_id)
                self._merging_ids.discard(story_id)
                # Build descriptive error from failed gate results
                failed_gates = [
                    g.name for g in result.qg_result.gate_results if not g.passed
                ]
                if failed_gates:
                    qg_error = (
                        f"Post-merge quality gate failed: {', '.join(failed_gates)}"
                    )
                else:
                    qg_error = "Post-merge quality gate failed"
                self._state = self._state.with_story_status(
                    story_id,
                    StoryStatus.BLOCKED,
                    error=qg_error,
                    completed_at=_utc_now(),
                )
                save_state(self._state, self._state_path)
                # Worktree was already cleaned up by merge_story's _cleanup_after_merge
                self._story_worktrees.pop(story_id, None)
                log_story_blocked(story_id, qg_error)
                await self._output_mux.write_orchestrator(
                    f"Story {story_id} merged but post-merge QG failed, status -> blocked"
                )

            elif result.success and result.qg_result is None:
                # Merge success, no QG was run (no commands found) -> done
                self._done_ids.add(story_id)
                self._merging_ids.discard(story_id)
                self._state = self._state.with_story_status(
                    story_id,
                    StoryStatus.DONE,
                    completed_at=_utc_now(),
                )
                save_state(self._state, self._state_path)
                self._story_worktrees.pop(story_id, None)
                self._log_unlocked_dependents(story_id)
                await self._output_mux.write_orchestrator(
                    f"Story {story_id} merged (no QG configured), status -> done"
                )

            else:
                # Merge failure (conflict) -> blocked
                self._blocked_ids.add(story_id)
                self._merging_ids.discard(story_id)
                error_msg = result.error or "Merge failed"
                self._state = self._state.with_story_status(
                    story_id,
                    StoryStatus.BLOCKED,
                    error=error_msg,
                    completed_at=_utc_now(),
                )
                save_state(self._state, self._state_path)
                log_story_blocked(story_id, error_msg)

                # Clean up worktree for merge conflict
                if story_id in self._story_worktrees:
                    try:
                        await asyncio.to_thread(
                            cleanup_worktree,
                            story_id,
                            self._project_root,
                            self._config.worktree_base_dir,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[ORCHESTRATOR] Worktree cleanup failed for %s: %s",
                            story_id,
                            exc,
                        )
                    finally:
                        del self._story_worktrees[story_id]

                await self._output_mux.write_orchestrator(
                    f"Story {story_id} merge failed ({error_msg}), status -> blocked"
                )

    # ========================================================================
    # Exit summary (Task 5)
    # ========================================================================

    async def _print_exit_summary(self) -> None:
        """Print a summary of story statuses on exit.

        Lists counts of done, merging, in-flight, blocked, and remaining
        stories. For blocked stories, shows the ``error`` field from
        ``StoryState`` to distinguish between execution failure, merge
        conflict, and post-merge QG failure. Includes a count of stories
        blocked-by-dependency (dependents of blocked stories). On force-exit,
        includes a warning about potential stale git lock files.
        """
        all_ids = set(self._dependency_graph.all_story_ids)
        completed = self._done_ids | self._merging_ids
        remaining = all_ids - completed - self._blocked_ids - self._in_flight_ids

        await self._output_mux.write_orchestrator(
            f"Exit summary: "
            f"Done: {len(self._done_ids)}, "
            f"Merging: {len(self._merging_ids)}, "
            f"In-flight: {len(self._in_flight_ids)}, "
            f"Blocked: {len(self._blocked_ids)}, "
            f"Remaining: {len(remaining)}"
        )

        # List blocked stories with their error details and unmet dependencies
        if self._blocked_ids:
            for story_id in sorted(self._blocked_ids):
                # Retrieve the error from persisted state
                story_state = self._state.stories.get(story_id)
                error_detail = (
                    story_state.error if story_state is not None else None
                )
                try:
                    deps = self._dependency_graph.dependencies_of(story_id)
                    unmet = [
                        d for d in deps
                        if d not in self._done_ids and d not in self._merging_ids
                    ]
                    if error_detail:
                        if unmet:
                            await self._output_mux.write_orchestrator(
                                f"  Blocked: {story_id} — {error_detail} "
                                f"(unmet deps: {', '.join(sorted(unmet))})"
                            )
                        else:
                            await self._output_mux.write_orchestrator(
                                f"  Blocked: {story_id} — {error_detail}"
                            )
                    elif unmet:
                        await self._output_mux.write_orchestrator(
                            f"  Blocked: {story_id} "
                            f"(unmet deps: {', '.join(sorted(unmet))})"
                        )
                    else:
                        await self._output_mux.write_orchestrator(
                            f"  Blocked: {story_id} (failed execution)"
                        )
                except KeyError:
                    if error_detail:
                        await self._output_mux.write_orchestrator(
                            f"  Blocked: {story_id} — {error_detail}"
                        )
                    else:
                        await self._output_mux.write_orchestrator(
                            f"  Blocked: {story_id}"
                        )

            # Count stories blocked-by-dependency: stories that cannot be
            # scheduled because they (directly or transitively) depend on a
            # blocked story. A story is blocked-by-dependency if its
            # dependencies are NOT all satisfied (i.e., not all in done/merging).
            blocked_by_dep_count = 0
            for sid in all_ids - self._blocked_ids - completed - self._in_flight_ids:
                try:
                    if not self._dependency_graph.are_dependencies_satisfied(
                        sid, completed,
                    ):
                        blocked_by_dep_count += 1
                except KeyError:
                    pass
            if blocked_by_dep_count > 0:
                await self._output_mux.write_orchestrator(
                    f"  Stories blocked-by-dependency: {blocked_by_dep_count}"
                )

        # Force-exit warning about potential stale git locks
        if self._force_exit:
            await self._output_mux.write_orchestrator(
                "WARNING: Stories interrupted mid-git-operation may leave "
                "orphaned .git/index.lock files in worktrees. Check and "
                "remove stale locks before the next run."
            )

    # ========================================================================
    # Epic completion detection (Story 6.4 — Task 1)
    # ========================================================================

    def _all_stories_done(self) -> bool:
        """Check whether every story has reached ``StoryStatus.DONE``.

        Returns:
            ``True`` only when ALL stories in the parallel state have
            ``status == StoryStatus.DONE``. Returns ``False`` if any story
            is ``BLOCKED``, ``IN_FLIGHT``, ``MERGING``, or ``BACKLOG``.

        """
        if not self._state.stories:
            return False
        return all(
            story.status == StoryStatus.DONE
            for story in self._state.stories.values()
        )

    # ========================================================================
    # Epic teardown subprocess (Story 6.4 — Task 2)
    # ========================================================================

    async def _kill_teardown_process(
        self, proc: asyncio.subprocess.Process,
    ) -> None:
        """Terminate the teardown subprocess.

        Delegates to ``_kill_process()`` which uses platform-appropriate
        termination: ``taskkill /F /T`` on Windows, ``os.killpg()``
        with ``SIGKILL`` on Unix (immediate kill of the process group).

        Args:
            proc: The teardown subprocess to terminate.

        """
        await _kill_process(proc)
        self._teardown_process = None

    async def _run_epic_teardown(self) -> bool:
        """Spawn the existing loop as a subprocess for epic teardown.

        Builds the command
        ``[sys.executable, "-m", "bmad_assist_lite", "run",
        "--epic", str(N), "--teardown-only"]``
        and runs it in the project root (not a worktree).

        Returns:
            ``True`` if teardown succeeded (exit code 0), ``False``
            on any failure.

        """
        teardown_start = _utc_now()
        await self._output_mux.write_orchestrator(
            f"Starting epic teardown for epic {self._epic_num}..."
        )

        exec_args: list[str] = [
            sys.executable,
            "-m",
            "bmad_assist_lite",
            "run",
            "--epic",
            str(self._epic_num),
            "--teardown-only",
        ]

        env = {**os.environ, "BMAD_PARALLEL_MODE": "1"}

        try:
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_exec(
                    *exec_args,
                    cwd=str(self._project_root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *exec_args,
                    cwd=str(self._project_root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                    start_new_session=True,
                )

            # Track process for signal handler cleanup
            self._teardown_process = proc

            # Stream output via OutputMultiplexer with "teardown" prefix
            assert proc.stdout is not None  # guaranteed by PIPE
            self._output_mux.start_reader("teardown", proc.stdout)

            # Wait for process exit
            await proc.wait()
            return_code = proc.returncode
            if return_code is None:  # pragma: no cover
                return_code = -1

        except asyncio.CancelledError:
            logger.warning(
                "[ORCHESTRATOR] Teardown task cancelled, killing subprocess",
            )
            if self._teardown_process is not None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        _kill_process(self._teardown_process),
                    )
            self._teardown_process = None
            return False

        except Exception as exc:
            logger.error(
                "[ORCHESTRATOR] Teardown subprocess failed to start: %s",
                exc,
            )
            self._teardown_process = None
            return False

        finally:
            # Drain and stop reader
            if not self._force_exit:
                with contextlib.suppress(Exception):
                    await self._output_mux.await_reader(
                        "teardown", timeout=5.0,
                    )
            with contextlib.suppress(Exception):
                await self._output_mux.stop_reader("teardown")
            self._teardown_process = None

        # Calculate duration
        duration_s = (_utc_now() - teardown_start).total_seconds()

        if return_code == 0:
            log_teardown_result(
                self._epic_num,
                success=True,
                exit_code=return_code,
                duration_s=duration_s,
            )
            await self._output_mux.write_orchestrator(
                f"Epic teardown completed successfully in {duration_s:.1f}s"
            )
            return True
        else:
            error_msg = (
                f"Teardown subprocess exited with code {return_code}. "
                "See [teardown] output above for failure details."
            )
            log_teardown_result(
                self._epic_num,
                success=False,
                exit_code=return_code,
                duration_s=duration_s,
                error=error_msg,
            )
            await self._output_mux.write_orchestrator(
                f"Epic teardown FAILED (exit_code={return_code}). "
                "Check [teardown] output above for which tests failed."
            )
            return False

    # ========================================================================
    # Sprint-status epic update (Story 6.4 — Task 4)
    # ========================================================================

    def _update_epic_sprint_status(self, status: str) -> None:
        """Update the epic status in ``sprint-status.yaml``.

        Follows the non-fatal pattern from ``update_sprint_status_done()``
        in ``merger.py``: load → mutate → save, wrap in try/except, log
        warning on failure, never propagate exceptions.

        Args:
            status: The new status string (e.g. ``"done"``, ``"blocked"``).

        """
        tag = f"[SPRINT|epic-{self._epic_num}]"
        try:
            from bmad_assist_lite.core.sprint_status import (
                get_sprint_status_path,
                load_sprint_status,
                save_sprint_status,
            )

            path = get_sprint_status_path(self._project_root)
            sprint_status = load_sprint_status(path)
            sprint_status.set_epic_status(self._epic_num, status)
            save_sprint_status(sprint_status, path)
            logger.info(
                "%s Updated sprint-status: epic %d -> %s",
                tag,
                self._epic_num,
                status,
            )
        except Exception:
            logger.warning(
                "%s Failed to update sprint-status (non-fatal)",
                tag,
                exc_info=True,
            )

    # ========================================================================
    # Worktree cleanup for epic completion (Story 6.4 — Task 6)
    # ========================================================================

    async def _cleanup_remaining_worktrees(self) -> None:
        """Clean up any remaining worktrees as a safety net.

        Iterates ``self._story_worktrees`` and calls
        ``cleanup_worktree()`` for each remaining entry.
        ``cleanup_worktree()`` already handles branch deletion as part
        of its three-step cleanup. Individual failures are non-fatal.
        """
        if not self._story_worktrees:
            return

        worktrees_to_clean = list(self._story_worktrees.items())
        for story_id, _wt_path in worktrees_to_clean:
            try:
                await asyncio.to_thread(
                    cleanup_worktree,
                    story_id,
                    self._project_root,
                    self._config.worktree_base_dir,
                )
                logger.info(
                    "[ORCHESTRATOR] Cleaned up worktree for story %s",
                    story_id,
                )
            except Exception as exc:
                logger.warning(
                    "[ORCHESTRATOR] Worktree cleanup failed for %s: %s (non-fatal)",
                    story_id,
                    exc,
                )
            finally:
                self._story_worktrees.pop(story_id, None)

    # ========================================================================
    # Main orchestration loop (Task 3, Task 4)
    # ========================================================================

    async def run(self) -> None:
        """Execute the main orchestration loop.

        Continuously evaluates ready stories from the dependency graph,
        spawns them as asyncio tasks, and waits for completions. Exits
        when all stories are done, merging, or blocked, and no tasks
        remain in-flight.

        Installs signal handlers for graceful shutdown (drain mode on
        first Ctrl+C, force-exit on second Ctrl+C). Signal handlers are
        removed on exit.

        """
        self._orchestrator_started_at = _utc_now()
        setup_parallel_log(self._project_root)
        try:
            log_run_header(
                base_branch=self._state.base_branch,
                epic=self._epic_num,
                max_concurrency=self._config.max_concurrency,
                story_count=self._dependency_graph.story_count,
            )
            self._install_signal_handlers()
            await self._output_mux.write_orchestrator(
                f"Starting orchestration for epic {self._epic_num} "
                f"({self._dependency_graph.story_count} stories)"
            )

            # Re-spawn in-flight stories that survived crash recovery but
            # have no running task (Task 6.3). These were preserved as
            # in_flight by recover_state() because their worktrees exist.
            stale_in_flight = self._in_flight_ids - set(self._running_tasks.keys())
            for story_id in sorted(stale_in_flight):
                task = asyncio.create_task(
                    self._spawn_story(story_id, resume=True),
                    name=f"story-{story_id}",
                )
                self._running_tasks[story_id] = task
                self._task_to_story[task] = story_id

            while True:
                # Check if draining and no tasks remain — break to exit
                if self._draining and not self._running_tasks:
                    break

                # Re-evaluate ready stories: union _merging_ids with _done_ids
                # so dependents of successfully-completed stories can proceed
                ready = self._dependency_graph.get_ready_stories(
                    self._done_ids | self._merging_ids,
                    self._in_flight_ids,
                    self._blocked_ids,
                )

                # Spawn ready stories only if NOT draining (Task 3.2)
                if not self._draining:
                    for story_id in ready:
                        self._in_flight_ids.add(story_id)
                        self._state = self._state.with_story_status(
                            story_id,
                            StoryStatus.IN_FLIGHT,
                            started_at=_utc_now(),
                        )
                        save_state(self._state, self._state_path)
                        task = asyncio.create_task(
                            self._spawn_story(story_id),
                            name=f"story-{story_id}",
                        )
                        self._running_tasks[story_id] = task
                        self._task_to_story[task] = story_id

                # Check termination: no running tasks and no new ready stories
                if not self._running_tasks:
                    # Stalemate detection: log warning with status summary
                    all_ids = set(self._dependency_graph.all_story_ids)
                    completed = self._done_ids | self._merging_ids
                    remaining = (
                        all_ids - completed - self._blocked_ids - self._in_flight_ids
                    )
                    if remaining:
                        logger.warning(
                            "[ORCHESTRATOR] Stalemate detected: no stories ready and "
                            "none in-flight. Done: %d, Merging: %d, Blocked: %d, "
                            "Remaining: %d (%s)",
                            len(self._done_ids),
                            len(self._merging_ids),
                            len(self._blocked_ids),
                            len(remaining),
                            sorted(remaining),
                        )
                    break

                # Wait for at least one task to complete, with timeout
                # so the loop can check _force_exit periodically (Task 4.1)
                # Snapshot via set() to prevent RuntimeError if dict mutated
                done_tasks, _pending = await asyncio.wait(
                    set(self._running_tasks.values()),
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Timeout expired with no completions — continue loop
                # (must check before processing done_tasks)
                if not done_tasks and not self._force_exit:
                    continue

                # Process completed tasks before checking force-exit,
                # so stories that finished are not discarded as IN_FLIGHT
                for completed_task in done_tasks:
                    completed_story_id = self._task_to_story.get(completed_task)
                    if completed_story_id is None:
                        continue

                    try:
                        exit_code = completed_task.result()
                    except asyncio.CancelledError:
                        exit_code = -1
                    except Exception:
                        logger.exception(
                            "[ORCHESTRATOR] Unexpected error in story %s task",
                            completed_story_id,
                        )
                        exit_code = -1

                    await self._on_story_complete(completed_story_id, exit_code)

                # Process merge queue after handling completions.
                # process_merge_with_fix() is async and handles
                # asyncio.to_thread() internally — awaiting it does NOT
                # block the event loop.
                await self._process_merge_queue()

                # Check force-exit after processing completions (Task 4.2)
                if self._force_exit:
                    await self._handle_force_exit()
                    break

            # Save state before exiting (Task 3.4)
            save_state(self._state, self._state_path)

            # ================================================================
            # Epic teardown (Story 6.4 — Task 5)
            # ================================================================
            # Teardown runs ONLY when:
            #   - All stories are DONE
            #   - NOT in drain mode (stories may be mid-execution)
            #   - NOT in force-exit mode
            teardown_success: bool | None = None
            if (
                not self._draining
                and not self._force_exit
                and self._all_stories_done()
            ):
                teardown_success = await self._run_epic_teardown()
                if teardown_success:
                    self._update_epic_sprint_status("done")
                    # Clean up remaining worktrees (safety net) only
                    # on success. On failure, preserve worktrees and
                    # branches for debugging (per Key Decision #4).
                    await self._cleanup_remaining_worktrees()
                # On teardown failure, do NOT update epic sprint-status
                # — epic stays "in-progress". Worktrees preserved for
                # debugging.

                # Persist final state after teardown
                save_state(self._state, self._state_path)

            elif not self._draining and not self._force_exit:
                # Not all stories done — some are blocked, stalled by
                # dependency cycles, or otherwise incomplete.
                # Update epic sprint-status to "blocked" and clean up
                # all worktrees (both completed and blocked — per FR35).
                # Note: _blocked_ids may be empty in stalemate scenarios
                # (stories stuck due to unresolvable dependencies), so we
                # unconditionally update status and clean up.
                self._update_epic_sprint_status("blocked")
                await self._cleanup_remaining_worktrees()

        finally:
            self._remove_signal_handlers()
            # Print exit summary in all cases (normal, drain, force-exit)
            with contextlib.suppress(Exception):
                await self._print_exit_summary()
            # Generate comprehensive summary report (Story 6.3)
            try:
                started = self._orchestrator_started_at or _utc_now()
                report_data = build_report(
                    self._state,
                    started,
                    self._merge_outcomes,
                )
                report_text = render_report(report_data)
                write_report(report_text)
            except Exception:
                logger.debug(
                    "[ORCHESTRATOR] Report generation failed",
                    exc_info=True,
                )
            # Log run-end footer and tear down the parallel log FileHandler
            with contextlib.suppress(Exception):
                all_ids = set(self._dependency_graph.all_story_ids)
                # Distinguish blocked-by-dependency from directly-failed stories.
                # A story is "failed" if it's in _blocked_ids (execution/merge/QG
                # failure). Stories blocked-by-dependency are in remaining (never
                # scheduled because a dependency failed).
                completed_set = self._done_ids | self._merging_ids
                remaining = (
                    all_ids - completed_set - self._blocked_ids - self._in_flight_ids
                )
                log_run_complete(
                    total_stories=len(all_ids),
                    completed=len(self._done_ids),
                    blocked=len(remaining),
                    failed=len(self._blocked_ids),
                )
            teardown_parallel_log()

    # ========================================================================
    # Force-exit handling (Task 4)
    # ========================================================================

    async def _handle_force_exit(self) -> None:
        """Cancel all running tasks and perform cleanup on force-exit.

        Cancels all running asyncio tasks, waits for their finally blocks
        to run (which call ``_kill_process()``), stops all output readers,
        and saves state immediately.
        """
        # Cancel all running asyncio tasks (Task 4.2)
        tasks_to_cancel = list(self._running_tasks.values())
        for task in tasks_to_cancel:
            task.cancel()

        # Wait for all finally blocks to run for cleanup (Task 4.3)
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        # Stop all output readers (Task 4.4)
        await self._output_mux.stop_all()

        # Save state immediately after force-termination (Task 4.5)
        save_state(self._state, self._state_path)
