"""Orchestrate parallel story execution via asyncio subprocess spawning.

Spawns loop subprocesses in git worktrees for each ready story, monitors
their completion, and manages the story lifecycle (in-flight, merging,
blocked). Concurrency is controlled by an asyncio semaphore and an
optional stagger delay between spawns.
"""

import asyncio
import contextlib
import logging
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.dependency_graph import DependencyGraph
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.output import OutputMultiplexer
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
    """Terminate a subprocess and wait for it to exit.

    Uses platform-appropriate termination: ``taskkill /F /T`` on Windows
    for process-tree kill, ``proc.kill()`` on Unix.

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
    ``asyncio.Semaphore``.
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

        # Status tracking sets
        self._done_ids: set[str] = set()
        self._in_flight_ids: set[str] = set()
        self._blocked_ids: set[str] = set()
        self._merging_ids: set[str] = set()

        # Persistent state — load existing or create fresh
        self._state_path = get_parallel_state_path(project_root)
        existing_state = load_state(self._state_path)
        if existing_state is not None:
            self._state: ParallelState = existing_state
            # Populate in-memory sets from persisted state
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
    # Subprocess spawning
    # ========================================================================

    async def _spawn_story(self, story_id: str) -> int:
        """Spawn a story loop subprocess in a dedicated worktree.

        Acquires a semaphore slot, creates the worktree, applies the
        stagger delay, then spawns the subprocess and waits for it.

        Args:
            story_id: The story ID to execute (e.g. ``"3.2"``).

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

                # Create worktree (sync function, bridge via to_thread)
                worktree_path: Path = await asyncio.to_thread(
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
                exec_args = [
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
                    )

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
                # lost from the asyncio buffer after process exit.
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

        if exit_code == 0:
            self._merging_ids.add(story_id)
            self._state = self._state.with_story_status(
                story_id,
                StoryStatus.MERGING,
                completed_at=_utc_now(),
            )
            save_state(self._state, self._state_path)
            await self._output_mux.write_orchestrator(
                f"Story {story_id} completed successfully, status -> merging"
            )
        else:
            self._blocked_ids.add(story_id)
            self._state = self._state.with_story_status(
                story_id,
                StoryStatus.BLOCKED,
                error=f"Exit code {exit_code}",
                completed_at=_utc_now(),
            )
            save_state(self._state, self._state_path)
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
    # Main orchestration loop
    # ========================================================================

    async def run(self) -> None:
        """Execute the main orchestration loop.

        Continuously evaluates ready stories from the dependency graph,
        spawns them as asyncio tasks, and waits for completions. Exits
        when all stories are done, merging, or blocked, and no tasks
        remain in-flight.

        """
        await self._output_mux.write_orchestrator(
            f"Starting orchestration for epic {self._epic_num} "
            f"({self._dependency_graph.story_count} stories)"
        )

        while True:
            # Re-evaluate ready stories: union _merging_ids with _done_ids
            # so dependents of successfully-completed stories can proceed
            ready = self._dependency_graph.get_ready_stories(
                self._done_ids | self._merging_ids,
                self._in_flight_ids,
                self._blocked_ids,
            )

            # Spawn ready stories
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
                remaining = all_ids - completed - self._blocked_ids
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

            # Wait for at least one task to complete
            # Snapshot via set() to prevent RuntimeError if dict mutated
            done_tasks, _pending = await asyncio.wait(
                set(self._running_tasks.values()),
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Process completed tasks
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

        # Log final summary
        await self._output_mux.write_orchestrator(
            f"Orchestration complete. "
            f"Done: {len(self._done_ids)}, "
            f"Merging: {len(self._merging_ids)}, "
            f"Blocked: {len(self._blocked_ids)}"
        )
