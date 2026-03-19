# Story 3.6: Graceful Shutdown & Drain Mode

Status: in-progress

## Story

As a developer using bmad-assist-lite parallel execution,
I want the orchestrator to handle Ctrl+C by draining running stories and persisting state,
so that no work is lost on interruption and the next run can resume from where it left off.

## Acceptance Criteria

1. **First Ctrl+C enters drain mode** — Given the orchestrator is running with 2 stories in-flight, when the user presses Ctrl+C, then the orchestrator stops spawning new stories immediately, prints "Shutting down -- waiting for running stories to finish...", and waits for running subprocesses to complete (drain mode).

2. **Drain completion saves state and prints summary** — Given drain mode is active and all subprocesses have exited, when drain completes, then `parallel-state.yaml` is saved with current state (in-flight stories remain in-flight for resume), the orchestrator prints a summary of what's done, in-flight, and remaining, and exits cleanly.

3. **Second Ctrl+C force-terminates** — Given the orchestrator is interrupted during drain (second Ctrl+C), when the second signal is received, then running subprocesses are terminated via `_kill_process()` (process tree kill), state is saved immediately, and the orchestrator exits.

4. **Exit summary lists blocked stories** — Given the orchestrator exits with blocked stories, when the exit summary is printed, then blocked stories and their unmet dependencies are listed.

## Tasks / Subtasks

- [ ] Task 1: Add shutdown flags and signal handler infrastructure to `Orchestrator` (AC: #1, #3)
  - [ ] 1.1: Add `_draining: bool = False` and `_force_exit: bool = False` instance attributes to `Orchestrator.__init__()`
  - [ ] 1.2: Create `_install_signal_handlers()` method that sets up SIGINT **and SIGTERM** handling — on Unix use `loop.add_signal_handler(signal.SIGINT, handler)` and `loop.add_signal_handler(signal.SIGTERM, handler)`, on Windows use `signal.signal(signal.SIGINT, handler)` (asyncio signal handlers not supported on Windows; SIGTERM is not raised on Windows so only SIGINT is needed there). SIGTERM is essential for CI/CD runners, container managers, and system services that use it for graceful shutdown.
  - [ ] 1.3: Create `_remove_signal_handlers()` method to restore default signal disposition on exit — on Unix use `loop.remove_signal_handler(signal.SIGINT)` and `loop.remove_signal_handler(signal.SIGTERM)`, on Windows use `signal.signal(signal.SIGINT, signal.SIG_DFL)`
  - [ ] 1.4: The signal handler callback must implement two-tier logic: first call sets `_draining = True`, second call sets `_force_exit = True`

- [ ] Task 2: Implement the `_on_sigint()` handler method (AC: #1, #3)
  - [ ] 2.1: On first signal: set `_draining = True`, write "[ORCHESTRATOR] Shutting down -- waiting for running stories to finish..." via `_output_mux.write_orchestrator()` (schedule as asyncio task since handler may run outside coroutine context on Windows)
  - [ ] 2.2: On second signal: set `_force_exit = True`, write "[ORCHESTRATOR] Force shutdown -- terminating all subprocesses..." via output mux

- [ ] Task 3: Integrate drain mode into the main orchestration loop (AC: #1, #2)
  - [ ] 3.0: **[CRITICAL]** Update `_spawn_story()` to isolate subprocesses from the parent's console process group — use `start_new_session=True` on Unix or `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` on Windows in the `asyncio.create_subprocess_exec()` call. Without this, `Ctrl+C` sends SIGINT to all child subprocesses simultaneously (OS-level process group signal propagation), completely defeating drain mode since running stories would abort immediately.
  - [ ] 3.1: In `run()`, call `_install_signal_handlers()` at the start within a try/finally that calls `_remove_signal_handlers()` and `_print_exit_summary()`
  - [ ] 3.2: In the main `while True` loop, check `_draining` before the spawn section — if draining, skip spawning new stories entirely
  - [ ] 3.3: When `_draining` is True and `_running_tasks` is empty (all drained), break out of the main loop
  - [ ] 3.4: Save state before exiting when draining — stories that complete during the current drain cycle have their status updated normally via `_on_story_complete()`. Stories that were already in-flight from a *prior crashed run* (loaded from persisted state but not spawned in this session) retain their in-flight status for resume on the next orchestrator run.

- [ ] Task 4: Implement force-exit logic (AC: #3)
  - [ ] 4.1: Modify the existing `asyncio.wait(tasks, return_when=FIRST_COMPLETED)` call to include a timeout: `asyncio.wait(tasks, timeout=1.0, return_when=FIRST_COMPLETED)`. Handle the case where `done_tasks` is empty (timeout expired, no completions) by continuing the loop. After each wait returns (whether by completion or timeout), check `_force_exit` flag.
  - [ ] 4.2: When `_force_exit` is True, cancel all running asyncio tasks — the `_spawn_story()` finally block already handles process termination via `_kill_process()`
  - [ ] 4.3: After cancelling tasks, `await asyncio.gather(*tasks, return_exceptions=True)` to let all finally blocks run for cleanup
  - [ ] 4.4: Call `await _output_mux.stop_all()` during force-exit cleanup to cancel active reader tasks and clear tracking state
  - [ ] 4.5: Save state immediately after force-termination before exiting

- [ ] Task 5: Implement `_print_exit_summary()` method (AC: #2, #4)
  - [ ] 5.1: Signature: `async def _print_exit_summary(self) -> None`
  - [ ] 5.2: Print count of done, merging, in-flight, and blocked stories via `_output_mux.write_orchestrator()`
  - [ ] 5.3: For blocked stories, list each blocked story ID along with its unmet dependencies (query `_dependency_graph` for each blocked story's deps, filter to those not in `_done_ids | _merging_ids`)
  - [ ] 5.4: Print count of remaining (backlog) stories
  - [ ] 5.5: If exiting via force-exit, include a warning that stories interrupted mid-git-operation may leave orphaned `.git/index.lock` files in worktrees, which will block those stories on the next run (advise user to check and remove stale locks if needed)

- [ ] Task 6: Update `cli.py` to handle shutdown cleanly (AC: #1, #2, #3)
  - [ ] 6.1: In `parallel_run()`, address the `except (KeyboardInterrupt, asyncio.CancelledError)` block — since the orchestrator now installs a custom SIGINT handler via `signal.signal()`, Python's default SIGINT→KeyboardInterrupt translation is overridden, making this block unreachable for Ctrl+C. Additionally, `asyncio.run()` installs its own SIGINT handler which the orchestrator's `_install_signal_handlers()` overrides. Update the block to handle the case where `run()` returns normally after drain/force-exit (no exception raised), and remove or comment the `KeyboardInterrupt` catch with a note explaining why it's no longer raised.
  - [ ] 6.2: Ensure the running lock is released on all exit paths (the `with running_lock(project)` context manager already handles this)

- [ ] Task 7: Write tests for signal handling and drain mode (AC: #1, #2, #3, #4)
  - [ ] 7.1: Test `_draining` flag prevents new story spawning in the main loop
  - [ ] 7.2: Test drain mode waits for running tasks to complete before exiting
  - [ ] 7.3: Test `_force_exit` flag cancels all running tasks
  - [ ] 7.4: Test force-exit calls `_kill_process()` for active subprocesses (via the finally block in `_spawn_story()`)
  - [ ] 7.5: Test state is saved before exit in drain mode
  - [ ] 7.6: Test state is saved before exit in force-exit mode
  - [ ] 7.7: Test exit summary includes done, in-flight, blocked counts
  - [ ] 7.8: Test exit summary lists blocked stories with unmet dependencies
  - [ ] 7.9: Test `_on_sigint()` first call sets `_draining`, second call sets `_force_exit`
  - [ ] 7.10: Test signal handlers are installed and removed correctly (both SIGINT and SIGTERM on Unix, SIGINT only on Windows)
  - [ ] 7.11: Test the orchestrator loop breaks cleanly when `_draining` is True and no tasks remain
  - [ ] 7.12: Test that subprocess isolation is applied — verify `start_new_session=True` (Unix) or `CREATE_NEW_PROCESS_GROUP` (Windows) is passed to `create_subprocess_exec()`
  - [ ] 7.13: Test SIGTERM triggers the same drain/force-exit behavior as SIGINT (Unix only)
  - [ ] 7.14: Test force-exit summary includes git lock warning
  - [ ] 7.15: Test `asyncio.wait()` timeout behavior — loop continues when timeout expires with no completions
  - [ ] 7.16: Group tests in classes: `TestSignalHandlerSetup`, `TestDrainMode`, `TestForceExit`, `TestExitSummary`

## Dev Notes

### Architecture Patterns and Constraints

- **Pure async — no threading** — The orchestrator uses `asyncio` exclusively. Signal handling must integrate with the asyncio event loop. Never use `threading.Event` or thread-based signal handling.
- **Platform-divergent signal handling** — On Unix, use `loop.add_signal_handler(signal.SIGINT, callback)` and `loop.add_signal_handler(signal.SIGTERM, callback)` which is the asyncio-native approach. On Windows, asyncio does not support `add_signal_handler` for SIGINT, so use `signal.signal(signal.SIGINT, callback)` instead (SIGTERM is not delivered on Windows). The handler runs in the main thread on Windows.
- **Subprocess process group isolation** — **CRITICAL**: By default, child subprocesses inherit the parent's console process group. When `Ctrl+C` is pressed, the OS sends SIGINT to *all* processes in the group simultaneously. This completely defeats drain mode. `_spawn_story()` must pass `start_new_session=True` (Unix) or `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` (Windows) to `asyncio.create_subprocess_exec()` to shield child processes from console interrupts.
- **`_kill_process()` already exists** — The `_kill_process()` async function in `orchestrator.py` handles platform-safe process termination (taskkill on Windows, proc.kill() on Unix) with a 15s wait timeout. Reuse it via the existing `_spawn_story()` finally block — cancelling the asyncio task triggers `CancelledError` which enters the finally block.
- **`terminate_process()` in `_windows.py`** — Available as a sync fallback if needed, but `_kill_process()` in orchestrator.py is the preferred async version for the orchestrator context.
- **Frozen Pydantic models** — `ParallelState` and `StoryState` use `ConfigDict(frozen=True)`. All state transitions use `model_copy(update={...})`. Never mutate directly.
- **Atomic state saves** — `save_state()` uses temp file + `os.replace()` pattern. Safe to call in shutdown paths.
- **`_utc_now()` convention** — Use the module-level `_utc_now()` in orchestrator.py for naive UTC timestamps.
- **Output via `_output_mux.write_orchestrator()`** — All user-visible console output from the orchestrator must go through the `OutputMultiplexer`. Never use `print()` or direct `logger.info()` for user-facing messages.
- **Type annotations on all functions** — mypy strict mode. Use `X | None` (PEP 604), not `Optional[X]`.
- **Logging convention** — `logger = logging.getLogger(__name__)` at module top. Use `write_progress()` / `_output_mux.write_orchestrator()` for user-visible output.
- **Exception hierarchy** — Use `ParallelError` from `bmad_assist_lite.parallel.exceptions`.
- **Section separators** — Use `# ============================================================================` between logical sections.
- **Line length** — 100 chars max (ruff enforced).
- **NFR2** — No story work is lost — worktree branches and per-worktree `state.yaml` preserve all committed progress regardless of orchestrator crash.

### Project Structure Notes

**File to modify:**
```
src/bmad_assist_lite/parallel/orchestrator.py   (add signal handling, drain mode, force-exit, exit summary)
src/bmad_assist_lite/parallel/cli.py            (verify shutdown compatibility)
```

**Test file to create/modify:**
```
tests/test_orchestrator.py   (add shutdown/drain/force-exit test classes)
```

**Dependencies (already exist — DO NOT modify):**
```
src/bmad_assist_lite/parallel/state.py           → save_state(), ParallelState (atomic persistence)
src/bmad_assist_lite/parallel/output.py          → OutputMultiplexer.write_orchestrator(), stop_all()
src/bmad_assist_lite/parallel/config.py          → ParallelConfig (frozen config model)
src/bmad_assist_lite/parallel/dependency_graph.py → DependencyGraph (for blocked story dep queries)
src/bmad_assist_lite/parallel/exceptions.py      → ParallelError
src/bmad_assist_lite/providers/_windows.py       → terminate_process() (sync fallback, not primary)
```

### Key Existing Code to Leverage

**`Orchestrator._spawn_story()` finally block** — Already contains process cleanup:
```python
finally:
    # Drain reader, stop reader, kill process if still running
    if proc is not None and proc.returncode is None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.shield(_kill_process(proc))
```
When an asyncio task is cancelled, `CancelledError` propagates into `_spawn_story()`, which enters this finally block and kills the subprocess. This is the mechanism for force-exit — cancel the task, let finally handle cleanup.

**`OutputMultiplexer.stop_all()`** — Cancels all active reader tasks and clears tracking state. Call this during force-exit cleanup.

**`asyncio.wait()` with timeout** — The main loop currently uses `asyncio.wait(tasks, return_when=FIRST_COMPLETED)` with no timeout (blocks indefinitely). This **must** be changed to `asyncio.wait(tasks, timeout=1.0, return_when=FIRST_COMPLETED)` so the loop can check `_force_exit` periodically. When timeout expires with no completions, `done_tasks` will be empty — handle this by continuing the loop iteration.

**`asyncio.run()` and SIGINT handler interaction** — `asyncio.run()` installs its own SIGINT handler. The orchestrator's `_install_signal_handlers()` overrides this. After the orchestrator handles shutdown internally (drain/force-exit), `run()` returns normally — `KeyboardInterrupt` is never raised. The CLI's `except KeyboardInterrupt` block becomes dead code and should be updated accordingly.

### Signal Handling Design

```
First Ctrl+C:                     Second Ctrl+C:
┌─────────────────┐               ┌──────────────────────┐
│ _draining = True│               │ _force_exit = True    │
│ Skip spawning   │──(waiting)──→ │ Cancel all tasks      │
│ Wait for drain  │               │ Tasks' finally blocks │
│                 │               │   → _kill_process()   │
│                 │               │ save_state()          │
│                 │               │ Exit                  │
└────────┬────────┘               └──────────────────────┘
         │ (all tasks done)
         ▼
    save_state()
    print_summary()
    Exit
```

### Windows Signal Handling Specifics

On Windows, `asyncio.get_event_loop().add_signal_handler()` raises `NotImplementedError` for `SIGINT`. The fallback is:
```python
signal.signal(signal.SIGINT, self._signal_handler_sync)
```
The sync handler runs in the main thread. It can set flags (`_draining`, `_force_exit`) safely since Python's GIL ensures atomic flag writes. To trigger the event loop to react, use `loop.call_soon_threadsafe()` from within the signal handler to schedule an async callback.

### References

- Architecture document: "Enforcement Guidelines" (use async patterns, never threading)
- PRD: FR40 (handle Ctrl+C by stopping new story spawning and draining running stories)
- PRD: FR41 (persist state before shutdown so next run can resume)
- PRD: FR42 (report blocked stories and their unmet dependencies on exit)
- NFR1: Orchestrator state must survive process crashes — state persisted after every status transition
- NFR2: No story work is lost — worktree branches preserve committed progress
- NFR5: Concurrent worktree loops must not interfere with each other
- NFR11: All git operations must work on Windows (primary) and Unix
- Project context: All 54 rules apply (type annotations, frozen models, logging, pathlib, etc.)
- Story 3.2: `Orchestrator` class with `run()` main loop, `_spawn_story()`, `_on_story_complete()`
- Story 3.3: `save_state()` with atomic writes, `ParallelState` frozen model
- Story 3.5: `OutputMultiplexer` with `write_orchestrator()`, `stop_all()`

## Testing Requirements

### Key Test Scenarios

- **Drain mode prevents spawning** — Verify that when `_draining` is True, the main loop does not call `_spawn_story()` for any new stories, even if ready stories are available
- **Drain waits for completion** — Verify that the orchestrator waits for all in-flight tasks to finish before exiting when in drain mode
- **Force-exit terminates subprocesses** — Verify that setting `_force_exit` cancels all running asyncio tasks and that the `_spawn_story()` finally block fires `_kill_process()` for each active process
- **State persistence on shutdown** — Verify that `save_state()` is called before exit in both drain and force-exit paths
- **Exit summary content** — Verify the summary includes correct counts for done, merging, in-flight, blocked, and remaining stories
- **Blocked story dependency listing** — Verify the exit summary lists each blocked story along with its specific unmet dependencies
- **Signal handler two-tier behavior** — Verify first invocation sets `_draining`, second sets `_force_exit`

### Edge Cases and Negative Scenarios

- **No stories in-flight at Ctrl+C** — Orchestrator should save state and exit immediately with summary
- **All stories already done at Ctrl+C** — Graceful exit with "all done" summary
- **Force-exit with subprocess that won't die** — `_kill_process()` has a 15s timeout; test that the orchestrator doesn't hang indefinitely
- **Signal during state save** — Atomic write pattern ensures no corruption
- **Multiple rapid Ctrl+C** — Third+ signals should not cause errors (handler is idempotent after `_force_exit = True`)
- **Windows vs Unix signal handling paths** — Both platform paths should be testable via mocking `sys.platform`
- **Stagger delay interrupted by drain** — If a task is in stagger sleep when drain starts, it should not spawn a new subprocess (check `_draining` after sleep)
- **Subprocess receives parent SIGINT** — Verify subprocess isolation prevents child processes from receiving the parent's console interrupt (process group isolation)
- **SIGTERM in CI/CD** — On Unix, verify SIGTERM triggers the same drain→force-exit flow as SIGINT
- **Git lock left by force-killed subprocess** — Verify exit summary warns about potential stale `.git/index.lock` files after force-exit

### Testing Patterns

- **Mock signal module** — Mock `signal.signal()` and `loop.add_signal_handler()` to verify handler installation without actually sending signals
- **Simulate signal by calling handler directly** — Test `_on_sigint()` by calling it directly rather than sending OS signals
- **Use `asyncio` test mode** — `asyncio_mode = "auto"` in pytest config
- **Mock `_spawn_story` for orchestrator loop tests** — Isolate loop behavior from actual subprocess spawning
- **Mock `save_state`** — Verify it's called at the right points during shutdown
- **Group tests in classes** with section separators: `TestSignalHandlerSetup`, `TestDrainMode`, `TestForceExit`, `TestExitSummary`

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/orchestrator.py src/bmad_assist_lite/parallel/cli.py` | **PENDING** |
| Typecheck | `mypy src/bmad_assist_lite/parallel/orchestrator.py src/bmad_assist_lite/parallel/cli.py --strict` | **PENDING** |
| Tests | `pytest tests/test_orchestrator.py -v --tb=short` | **PENDING** |

## Senior Developer Review (AI)

**Date:** 2026-03-18
**Aggregate Evidence Score:** 6.6
**Verdict:** REJECT (requires re-verification after fixes)

### Fixes Applied

1. **CRITICAL: Unix Process Tree Kill** — `_kill_process()` now uses `os.killpg(os.getpgid(pid), signal.SIGKILL)` on Unix instead of `proc.kill()`. With `start_new_session=True`, child subprocesses are process group leaders; `proc.kill()` only killed the leader, leaving child processes (git, pytest) orphaned.

2. **CRITICAL: Tautological Test Fixed** — `test_kill_process_calls_kill_on_unix` renamed to `test_kill_process_calls_killpg_on_unix` and now verifies `os.killpg()` is called with the correct process group ID. Added `test_kill_process_falls_back_to_proc_kill_on_unix` for fallback path.

3. **IMPORTANT: Stored Event Loop Reference** — `_on_sigint()` now uses `self._loop` (captured during `_install_signal_handlers()` via `asyncio.get_running_loop()`) instead of deprecated `asyncio.get_event_loop()`. Safe for Windows signal handler context.

4. **IMPORTANT: Deferred Coroutine Creation** — `_on_sigint()` now wraps `write_orchestrator()` calls in a lambda inside `call_soon_threadsafe()`, deferring coroutine creation to the event loop thread instead of eagerly creating coroutines in signal handler context.

5. **IMPORTANT: Force-Exit Reader Delay** — `_spawn_story()` finally block now skips the 5-second `await_reader()` drain when `_force_exit` is True, preventing a 5s delay per story during force-exit.

6. **IMPORTANT: Process Done Before Force-Exit** — `done_tasks` from `asyncio.wait()` are now processed before checking `_force_exit`, ensuring stories that completed successfully aren't discarded as IN_FLIGHT.

7. **IMPORTANT: Test 7.4 Gap Closed** — Added `test_force_exit_triggers_kill_process_via_finally` that exercises the real `_spawn_story()` finally block during task cancellation, verifying `_kill_process()` is actually called.

8. **MINOR: Stalemate Detection** — Fixed `remaining` calculation to subtract `_in_flight_ids`, preventing prior-run zombie stories from inflating the remaining count.

9. **MINOR: Strengthened Drain Test** — `test_draining_prevents_new_story_spawning` now tracks spawned story IDs and asserts `"3.3" not in spawned_ids` instead of the weak `spawn_count <= 2`.

10. **MINOR: Closed Loop Safety** — Added `_on_sigint()` guard for closed/None loop reference with new tests.

### Findings Not Applied (Rejected or Deferred)

- **Redundant `save_state()` on force-exit path** — Kept both calls (in `_handle_force_exit()` and post-loop) as defense-in-depth; the redundancy is harmless and provides safety if `_handle_force_exit()` raises.
- **KeyboardInterrupt catch in cli.py** — Kept with explanatory comment as safety net for edge cases (signal during startup before handlers installed). Acceptable approach.
- **Stale `_running_tasks` after force-exit** — Resource leak exists only briefly before process exit; not worth fixing.
- **V2 Finding 4 (prior-run stalemate)** — Pre-existing from story 3.2; fixed stalemate `remaining` calculation but full recovery of zombie in-flight stories is out of scope for 3.6.

### Runtime Verification

| Gate | Status |
|------|--------|
| Lint | **BLOCKED** (sandbox restrictions prevented execution) |
| Typecheck | **BLOCKED** (sandbox restrictions prevented execution) |
| Tests | **BLOCKED** (sandbox restrictions prevented execution) |

**Action Required:** Run `python -m pytest tests/test_orchestrator.py -v --tb=short` and `python -m ruff check src/bmad_assist_lite/parallel/orchestrator.py tests/test_orchestrator.py` to verify all fixes pass before merging.

## Dev Agent Record

### Agent Model Used
### Debug Log References
### Completion Notes List
### File List
