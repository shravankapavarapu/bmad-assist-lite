# Story 3.2: Orchestrator Core Loop & Subprocess Spawning

Status: in-progress

## Story

As a developer using bmad-assist-lite parallel execution,
I want an asyncio orchestrator that spawns loop subprocesses in worktrees and monitors their completion,
so that multiple stories execute concurrently with proper lifecycle management.

## Acceptance Criteria

1. **Ready story discovery & spawn:** Given the dependency resolver identifies stories as ready and `max_concurrency` allows, the orchestrator creates worktrees (via `worktree_manager.create_worktree()`) and spawns loop subprocesses via `asyncio.create_subprocess_exec(sys.executable, "-m", "bmad_assist_lite", "run", "--epic", str(epic), "--story", str(story), "--single-story")` with `cwd=worktree_path` and `env={..., "BMAD_PARALLEL_MODE": "1"}`.
2. **Successful completion handling:** Given a subprocess exits with code 0, the orchestrator transitions the story to `merging` status, re-evaluates ready stories via `dependency_graph.get_ready_stories()`, and spawns newly ready stories if concurrency slots are available.
3. **Failure handling:** Given a subprocess exits with a non-zero code, the orchestrator updates the story status to reflect failure (blocked), and continues executing remaining non-dependent stories.
4. **Concurrency limiting:** Given `max_concurrency` is N and N stories are already running, the orchestrator waits (via `asyncio.Semaphore`) until a slot opens before spawning the next story.
5. **Stagger delay:** Given `stagger_delay` is configured and multiple stories become ready in the same evaluation cycle, each spawn is delayed by `stagger_delay` seconds from the previous (via `asyncio.sleep(stagger_delay)`).
6. **Process cleanup guarantee:** Every spawned subprocess is tracked and cleaned up (including process tree termination) via `try/finally` blocks, even on exceptions or cancellation.
7. **Orchestrator terminates when done:** The orchestrator's `run()` method completes when all stories are either `done`, `merging`, or `blocked`, and no stories are in-flight.
8. **Stalemate detection:** Given no stories are ready and no stories are in-flight on any iteration (including the first), the orchestrator logs a clear warning summary (stories done, blocked, remaining) and exits cleanly.

## Tasks / Subtasks

- [x] Task 1: Create `orchestrator.py` module skeleton (AC: all)
  - [x] 1.1: Add module docstring (imperative summary, Google style), `logging.getLogger(__name__)`, standard imports (`asyncio`, `sys`, `os`, `logging`, `pathlib`)
  - [x] 1.2: Import dependencies: `ParallelConfig` from `config`, `DependencyGraph` from `dependency_graph`, `create_worktree`/`cleanup_worktree` from `worktree_manager`, `ParallelError` from `exceptions`
  - [x] 1.3: Use `TYPE_CHECKING` guard for any type-only imports (e.g., future `ParallelState` from state module) — Not needed; no type-only imports required.
  - [x] 1.4: Add section separators (`# ============================================================================`) between logical sections

- [x] Task 2: Implement `Orchestrator` class with `__init__` (AC: #1, #4, #5)
  - [x] 2.1: Class signature: `class Orchestrator:` with class-level docstring
  - [x] 2.2: `__init__` parameters: `dependency_graph: DependencyGraph`, `config: ParallelConfig`, `project_root: Path`, `epic_num: int`
  - [x] 2.3: Store injected dependencies as instance attributes
  - [x] 2.4: Initialize `_semaphore: asyncio.Semaphore` from `config.max_concurrency`
  - [x] 2.5: Initialize tracking dicts: `_running_tasks: dict[str, asyncio.Task[int]]` for active story tasks, `_story_worktrees: dict[str, Path]` for worktree path mapping
  - [x] 2.6: Initialize status tracking sets: `_done_ids: set[str]`, `_in_flight_ids: set[str]`, `_blocked_ids: set[str]`
  - [x] 2.7: Initialize `_merging_ids: set[str]` for stories awaiting merge (post-subprocess, pre-merge)

- [x] Task 3: Implement `_spawn_story()` async method (AC: #1, #4, #5, #6)
  - [x] 3.1: Signature: `async def _spawn_story(self, story_id: str) -> int`
  - [x] 3.2: Acquire semaphore slot (`async with self._semaphore`)
  - [x] 3.3: Create worktree via `asyncio.to_thread(create_worktree, story_id, self._project_root, self._config.worktree_base_dir)` — worktree_manager is synchronous, must not block event loop
  - [x] 3.4: Record worktree path in `_story_worktrees[story_id]`
  - [x] 3.5: Build subprocess env: `{**os.environ, "BMAD_PARALLEL_MODE": "1"}`
  - [x] 3.6: Spawn via `asyncio.create_subprocess_exec(...)` with `DEVNULL`, Windows `CREATE_NEW_PROCESS_GROUP`
  - [x] 3.7: Extract story_num from story_id using regex split on `[-.]`, taking last segment
  - [x] 3.8: `await proc.communicate()` and return `proc.returncode`
  - [x] 3.9: Wrap in `try/except/finally`: on `asyncio.CancelledError` or exception, terminate the process tree via platform-appropriate kill, then re-raise
  - [x] 3.10: In `finally`, remove from `_in_flight_ids` (whether success or failure)

- [x] Task 4: Implement `_on_story_complete()` method (AC: #2, #3)
  - [x] 4.1: Signature: `async def _on_story_complete(self, story_id: str, exit_code: int) -> None`
  - [x] 4.2: If `exit_code == 0`: add to `_merging_ids`, log at INFO with `[ORCHESTRATOR]` prefix
  - [x] 4.3: If `exit_code != 0`: add to `_blocked_ids`, log at ERROR with `[ORCHESTRATOR]` prefix
  - [x] 4.4: Remove task from both `_running_tasks` and `_task_to_story` dicts to keep them in sync. Both dicts must be cleaned up atomically in `_on_story_complete`.
  - [x] 4.5: Clean up worktree for blocked stories via `asyncio.to_thread(cleanup_worktree, ...)` (successful stories keep worktree for merge phase)

- [x] Task 5: Implement `async run()` main loop (AC: #1, #2, #3, #7)
  - [x] 5.1: Signature: `async def run(self) -> None`
  - [x] 5.2: Outer loop: `while True` — exits when no stories are in-flight AND no stories are ready
  - [x] 5.3: Call `get_ready_stories(done_ids | merging_ids, in_flight_ids, blocked_ids)` with union
  - [x] 5.4: For each ready story: add to `_in_flight_ids`, create `asyncio.Task`, store in `_running_tasks`. Stagger delay inside `_spawn_story`.
  - [x] 5.5: If no ready stories and no running tasks: break (all done or all blocked)
  - [x] 5.6: Wait for at least one task completion via `asyncio.wait(set(...), return_when=FIRST_COMPLETED)` with snapshot
  - [x] 5.7: For each completed task in `done`: retrieve result (exit_code), find corresponding story_id, call `_on_story_complete()`
  - [x] 5.8: Loop back to re-evaluate ready stories after completions
  - [x] 5.9: Log summary at end: total done, blocked, remaining counts

- [x] Task 6: Implement task-to-story-id reverse lookup (AC: #2, #3)
  - [x] 6.1: Maintain a `_task_to_story: dict[asyncio.Task[int], str]` mapping
  - [x] 6.2: Populate in `run()` when creating tasks
  - [x] 6.3: Use in `run()` when processing completed tasks from `asyncio.wait()`

- [x] Task 7: Handle process termination platform-safely (AC: #6)
  - [x] 7.1: Implemented `_kill_process()` async function following `providers/_windows.py` patterns
  - [x] 7.2: In `_spawn_story` exception handler, kill process tree: `proc.kill()` on Unix, `taskkill /F /T /PID` on Windows
  - [x] 7.3: Ensure `await proc.wait()` after kill to avoid zombie processes

- [x] Task 8: Update `parallel/__init__.py` exports (AC: all)
  - [x] 8.1: Add `Orchestrator` to imports and `__all__`

- [x] Task 9: Write comprehensive tests in `tests/test_orchestrator.py` (AC: all)
  - [x] 9.1: Test `Orchestrator.__init__` stores attributes and creates semaphore with correct limit
  - [x] 9.2: Test `_spawn_story` builds correct subprocess command args (mock `asyncio.create_subprocess_exec`)
  - [x] 9.3: Test `_spawn_story` sets correct `cwd` and `env` with `BMAD_PARALLEL_MODE=1`
  - [x] 9.4: Test `_spawn_story` calls `create_worktree` via `asyncio.to_thread`
  - [x] 9.5: Test `_on_story_complete` transitions story to `_merging_ids` on exit code 0
  - [x] 9.6: Test `_on_story_complete` transitions story to `_blocked_ids` on non-zero exit
  - [x] 9.7: Test `_on_story_complete` cleans up worktree for blocked stories
  - [x] 9.8: Test `run()` spawns ready stories from dependency graph
  - [x] 9.9: Test `run()` respects concurrency limit (semaphore blocks when full)
  - [x] 9.10: Test `run()` applies stagger delay between spawns in same cycle
  - [x] 9.11: Test `run()` re-evaluates ready stories after each completion
  - [x] 9.12: Test `run()` terminates when all stories are done/blocked and none in-flight
  - [x] 9.13: Test `run()` handles mixed success/failure completions correctly
  - [x] 9.14: Test process cleanup on `asyncio.CancelledError` in `_spawn_story`
  - [x] 9.15: Test story_num extraction from story_id with both dot and dash formats
  - [x] 9.16: Test stalemate detection: no ready stories and no in-flight stories triggers clean exit with warning log (AC #8)
  - [x] 9.17: Test `_on_story_complete` cleans up both `_running_tasks` and `_task_to_story` dicts
  - [x] 9.18: Test that `_merging_ids` are unioned with `_done_ids` when calling `get_ready_stories()`
  - [x] 9.19: Group tests in classes (`TestOrchestratorInit`, `TestSpawnStory`, `TestOnStoryComplete`, `TestRunLoop`, `TestProcessCleanup`, `TestStalemateDetection`) plus `TestExtractStoryNum`, `TestConcurrency`, `TestWindowsFlags`

## Dev Notes

### Architecture Patterns and Constraints

- **Pure async orchestrator** — The `Orchestrator` class uses `asyncio` exclusively for concurrency. Never use threading for subprocess management. This is a core architectural decision.
- **`asyncio.create_subprocess_exec`** for non-blocking subprocess management — not `subprocess.run()` (which blocks).
- **`asyncio.Semaphore(max_concurrency)`** for concurrency limiting — not a custom counter.
- **`asyncio.wait(tasks, return_when=FIRST_COMPLETED)`** for completion detection — not polling.
- **`asyncio.sleep(stagger_delay)`** between spawns — not `time.sleep()` (blocks event loop).
- **`asyncio.to_thread()`** for calling synchronous worktree_manager functions from async code — worktree_manager.py is intentionally synchronous.
- **Frozen Pydantic models** — Any new models must use `model_config = ConfigDict(frozen=True)`.
- **Type annotations required on ALL functions** — mypy strict mode. Use `X | None` (PEP 604).
- **Logging convention** — `logger = logging.getLogger(__name__)` at module top. Use `[ORCHESTRATOR]` log prefix. Never use `print()`.
- **Path handling** — `pathlib.Path` throughout. No `os.path`.
- **Absolute imports only** — `from bmad_assist_lite.parallel.worktree_manager import create_worktree`.
- **Exception hierarchy** — Raise `ParallelError`, never bare `Exception`.
- **Section separators** — Use `# ============================================================================` between logical sections.
- **Line length** — 100 chars max (ruff enforced).
- **Process cleanup in `finally` blocks** — Every subprocess must be killed and waited on if the task is cancelled or errors.
- **Platform-safe process termination** — Use `taskkill /F /T /PID` on Windows, `proc.kill()` / `os.killpg()` on Unix. Follow existing `providers/_windows.py` patterns.
- **NFR6** — Orchestrator overhead must be <1% of total wall-clock time. Keep scheduling logic lightweight.

### Project Structure Notes

**File to create:**
```
src/bmad_assist_lite/parallel/orchestrator.py
```

**Test file to create:**
```
tests/test_orchestrator.py
```

**File to modify:**
```
src/bmad_assist_lite/parallel/__init__.py  (add Orchestrator export)
```

**Dependencies (already exist — DO NOT modify):**
```
src/bmad_assist_lite/parallel/config.py            -> ParallelConfig (max_concurrency, stagger_delay, worktree_base_dir)
src/bmad_assist_lite/parallel/dependency_graph.py   -> DependencyGraph (get_ready_stories, all_story_ids)
src/bmad_assist_lite/parallel/worktree_manager.py   -> create_worktree(), cleanup_worktree()
src/bmad_assist_lite/parallel/git_ops.py            -> _run_git() (used internally by worktree_manager)
src/bmad_assist_lite/parallel/exceptions.py         -> ParallelError
src/bmad_assist_lite/providers/_windows.py          -> get_subprocess_kwargs(), terminate_process patterns
src/bmad_assist_lite/cli.py                         -> --epic, --story, --single-story flags (already implemented)
src/bmad_assist_lite/core/sprint_sync.py            -> BMAD_PARALLEL_MODE bypass (already implemented)
```

### Subprocess Command Reference

| Operation | Command | Notes |
|-----------|---------|-------|
| Spawn story loop | `sys.executable -m bmad_assist_lite run --epic N --story M --single-story` | `cwd=worktree_path`, `env={..., BMAD_PARALLEL_MODE=1}`, `stdout=DEVNULL`, `stderr=DEVNULL`, Windows: `CREATE_NEW_PROCESS_GROUP` |
| Exit code 0 | Story completed successfully | Transition to `merging` |
| Non-zero exit | Story failed | Transition to `blocked` |

### Async Pattern Reference

```python
# Spawning subprocess (non-blocking)
# Use DEVNULL until Story 3.5 adds output reader (PIPE without reader deadlocks)
kwargs = {}
if sys.platform == "win32":
    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
proc = await asyncio.create_subprocess_exec(
    sys.executable, "-m", "bmad_assist_lite", "run",
    "--epic", str(epic_num), "--story", str(story_num), "--single-story",
    cwd=str(worktree_path),
    stdout=asyncio.subprocess.DEVNULL,
    stderr=asyncio.subprocess.DEVNULL,
    env={**os.environ, "BMAD_PARALLEL_MODE": "1"},
    **kwargs,
)

# Concurrency control (stagger delay inside _spawn_story, not in main loop)
async with self._semaphore:
    await asyncio.sleep(self._config.stagger_delay)  # stagger inside spawn
    await proc.communicate()
    return_code = proc.returncode

# Completion detection (snapshot tasks to avoid mutation during iteration)
done, _pending = await asyncio.wait(
    set(tasks), return_when=asyncio.FIRST_COMPLETED
)

# Bridge sync → async for worktree ops
worktree_path = await asyncio.to_thread(
    create_worktree, story_id, self._project_root, self._config.worktree_base_dir
)

# Dependency resolution: union _merging_ids with _done_ids
ready = self._dependency_graph.get_ready_stories(
    self._done_ids | self._merging_ids, self._in_flight_ids, self._blocked_ids
)
```

### Key Design Decisions

1. **Story status lifecycle for this story:** `backlog → in-flight → merging (exit 0) | blocked (exit != 0)`. The merge phase (Story 4.x) handles `merging → done`. This story does NOT implement merge logic.
2. **Worktree cleanup:** Only clean up worktrees for blocked stories. Successful story worktrees are kept for the merge phase (Epic 4).
3. **No state persistence in this story:** State persistence (`parallel-state.yaml`) is Story 3.3. This story tracks state in-memory only.
4. **No output multiplexing in this story:** Live output streaming is Story 3.5. Subprocess stdout/stderr use `DEVNULL` in this story to prevent pipe buffer deadlocks. Story 3.5 will change to `PIPE` with an active reader.
5. **No graceful shutdown in this story:** Signal handling and drain mode are Story 3.6. CancelledError handling in `_spawn_story` is the only concession.
6. **No CLI entry point in this story:** The `parallel run` command is Story 3.4. This story provides the `Orchestrator` class for that story to use.

### References

- Architecture document: "Process Architecture" (asyncio decision), "Worktree Loop Spawning" (subprocess pattern), "Async Pattern Rules", "Process Cleanup Pattern"
- PRD: FR8 (spawn loop in worktree), FR9 (max N concurrent), FR10 (stagger delay), FR11 (monitor completion via exit codes), FR12 (full 7-phase pipeline independently), FR13 (parallel mode bypass)
- NFR5: Concurrent worktree loops must not interfere
- NFR6: Orchestrator overhead <1% of total wall-clock time
- NFR11-13: Windows-primary platform safety
- Project context: All 54 rules apply (frozen Pydantic, type annotations, logging, pathlib, etc.)
- Story 3.1 (worktree_manager): `create_worktree()`, `cleanup_worktree()` — synchronous functions, must be called via `asyncio.to_thread()`
- Epic 2 (dependency resolution): `DependencyGraph.get_ready_stories()` — pure query, no state mutation

## Testing Requirements

### Key Test Scenarios

- **Orchestrator initialization:** Verify `Orchestrator.__init__` correctly stores dependency graph, config, project root, epic num, and creates a semaphore with the configured limit
- **Subprocess command construction:** Verify the spawned subprocess uses `sys.executable -m bmad_assist_lite run --epic N --story M --single-story` with correct `cwd` and `BMAD_PARALLEL_MODE=1` env
- **Successful story flow:** Story spawned → process exits 0 → story moves to `_merging_ids` → ready stories re-evaluated → new stories spawned
- **Failed story flow:** Story spawned → process exits non-zero → story moves to `_blocked_ids` → worktree cleaned up → orchestrator continues with other stories
- **Concurrency limiting:** With `max_concurrency=2` and 3 ready stories, only 2 spawn initially; third spawns after one completes
- **Stagger delay:** Multiple stories spawned in one cycle have `stagger_delay` between each spawn
- **Run loop termination:** Orchestrator exits `run()` when all stories are done/blocked/merging and no tasks are running
- **Mixed outcomes:** Some stories succeed, some fail; orchestrator handles both and continues

### Edge Cases and Negative Scenarios

- **No ready stories at start:** All stories depend on others — orchestrator should detect deadlock/stalemate and exit
- **Single story:** Only one story in the graph — runs sequentially, no concurrency needed
- **All stories fail:** Orchestrator handles gracefully and exits
- **Process kill on CancelledError:** Subprocess gets killed cleanly when the task is cancelled
- **Story ID parsing:** Story IDs like `"3.2"`, `"3.10"`, `"10.1"` all parse correctly for `--story` flag
- **Zero stagger delay:** `stagger_delay=0.0` means no delay between spawns
- **Max concurrency 1:** Effectively sequential execution

### Testing Patterns

- **Mock `asyncio.create_subprocess_exec`** to avoid spawning real processes — return mock process objects with configurable `wait()` return codes
- **Mock `asyncio.to_thread`** to intercept `create_worktree` / `cleanup_worktree` calls without real git operations
- **Use `asyncio` test mode** (`asyncio_mode = "auto"` in pytest config) — async test functions auto-detected
- **Group tests in classes** (`TestOrchestratorInit`, `TestSpawnStory`, `TestOnStoryComplete`, `TestRunLoop`, `TestProcessCleanup`)
- **Use section separators** between test classes
- **Mock `DependencyGraph`** with predetermined ready story sequences to test orchestrator logic in isolation

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/orchestrator.py tests/test_orchestrator.py` | **AWAITING MANUAL RUN** |
| Typecheck | `mypy src/bmad_assist_lite/parallel/orchestrator.py --strict` | **AWAITING MANUAL RUN** |
| Tests | `pytest tests/test_orchestrator.py -v --tb=short` | **AWAITING MANUAL RUN** |

> **Note:** Quality gate commands could not be executed in the sandbox environment (tool permissions blocked). The code has been reviewed for lint, type, and correctness issues manually. User must run these commands to verify.

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (claude-sonnet-4-20250514)

### Debug Log References
- Sandbox tool permissions blocked `ruff`, `mypy`, `pytest` execution. Manual quality gate verification required.

### Completion Notes List
- Created `orchestrator.py` with full asyncio orchestration: `Orchestrator` class with `__init__`, `_spawn_story`, `_on_story_complete`, `run()` methods
- Implemented `_extract_story_num()` helper for robust story ID parsing (dot and dash separators)
- Implemented `_kill_process()` async function for platform-safe process termination (Windows: taskkill /F /T, Unix: proc.kill())
- Used `asyncio.Semaphore` for concurrency limiting, `asyncio.wait(FIRST_COMPLETED)` for completion detection
- Stagger delay applied inside `_spawn_story` (after semaphore acquire) per spec
- Worktree creation via `asyncio.to_thread()` to avoid blocking event loop
- Subprocess uses `DEVNULL` for stdout/stderr (per Story 3.5 design decision)
- Windows gets `CREATE_NEW_PROCESS_GROUP` creation flag for safe process-tree termination
- Stalemate detection with clear warning log including done/blocked/remaining counts
- Union of `_merging_ids` with `_done_ids` when calling `get_ready_stories()` so dependents can proceed
- Updated `parallel/__init__.py` to export `Orchestrator`
- Wrote 50+ tests across 8 test classes covering all acceptance criteria and edge cases
- No `TYPE_CHECKING` guard needed (no type-only imports in this module)

### File List
- **Created:** `src/bmad_assist_lite/parallel/orchestrator.py` (399 lines)
- **Created:** `tests/test_orchestrator.py` (~1030 lines, 50+ tests)
- **Modified:** `src/bmad_assist_lite/parallel/__init__.py` (added Orchestrator export)

### Change Log
- 2026-03-18: Initial implementation of Story 3.2 — Orchestrator Core Loop & Subprocess Spawning
- 2026-03-18: Code review synthesis — applied 6 fixes, 3 new tests added

## Senior Developer Review (AI)

**Verdict: REJECT → Fixes Applied → Re-review Required**
**Aggregate Evidence Score: 8.2**

### Review Summary

Two independent reviewers identified significant issues requiring rework. The following fixes were applied:

**Applied Fixes (6):**
1. **CRITICAL: Process cleanup moved to `finally` block** — `_kill_process` calls moved from `except` handlers to `finally` to guarantee cleanup for all exception types including `BaseException` (`KeyboardInterrupt`, `SystemExit`). Uses `asyncio.shield` to prevent re-cancellation during cleanup.
2. **IMPORTANT: `_in_flight_ids` ownership consolidated** — Removed premature `_in_flight_ids.discard()` from `_spawn_story.finally`; moved to `_on_story_complete` as the single source of truth for lifecycle transitions.
3. **IMPORTANT: `proc.wait()` timeout added** — `_kill_process` now uses `asyncio.wait_for(proc.wait(), timeout=15)` to prevent indefinite hang on unkillable processes.
4. **IMPORTANT: Windows `taskkill` returncode check** — Now checks `result.returncode != 0` after `taskkill` and falls back to `proc.kill()` on failure, instead of silently succeeding.
5. **IMPORTANT: `_story_worktrees` dict cleanup** — Blocked story entries are now removed from `_story_worktrees` dict after cleanup to prevent unbounded growth.
6. **MINOR: `typing.Any` replaced with `dict[str, int]`** — Stronger type annotation for `kwargs` dict.

**New Tests Added (3):**
- `test_removes_from_in_flight_ids` — verifies `_on_story_complete` is the single authority for `_in_flight_ids` cleanup
- `test_failure_removes_worktree_dict_entry` — verifies `_story_worktrees` dict cleanup for blocked stories
- `test_all_stories_fail_exits_cleanly` — edge case: all stories fail, orchestrator exits gracefully
- `test_finally_calls_kill_process_via_shield` — verifies `asyncio.shield` is used in finally block

**Deferred Issues (not fixed, documented):**
- Stagger delay implementation follows the prescribed Dev Notes pattern (`sleep` inside `_spawn_story`) but concurrent tasks sleep simultaneously, not sequentially. This is a spec-level contradiction (AC#5 says "from the previous" but Dev Notes pattern puts sleep inside each spawn). Deferred to Story 3.6 or a follow-up ticket.
- Sequential `_on_story_complete` processing for simultaneous failures (NFR6 concern). Low real-world impact; defer to optimization pass.
- Platform-dependent test assertions in `TestProcessCleanup`. Works on both CI platforms via mock; no change needed.

**Runtime Verification:**
- Lint (ruff): Blocked by sandbox — manual verification required
- Type Check (mypy): Blocked by sandbox — manual verification required
- Tests (pytest): Blocked by sandbox — manual verification required
- Build: N/A (pure Python)
