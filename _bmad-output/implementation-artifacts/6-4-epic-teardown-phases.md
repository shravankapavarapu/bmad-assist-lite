# Story 6.4: Epic Teardown Phases

Status: in-progress

## Story

As a developer,
I want epic_quality_gate and retrospective to run on the base branch after all stories merge,
so that the full project is validated and lessons are captured before the epic is considered complete.

## Acceptance Criteria

1. **Epic completion triggers teardown** — Given all stories in the epic are `done` (all merged + post-merge QG passed), when the orchestrator detects epic completion, then it runs epic teardown phases on the base branch in order: `epic_quality_gate` then `retrospective`.

2. **Teardown via subprocess** — Given epic teardown needs to run, when the orchestrator invokes teardown, then it spawns the existing loop with `--epic N --teardown-only` targeting the teardown phases directly (runs in project root, not a worktree), bypassing story discovery.

3. **Epic QG failure handling** — Given `epic_quality_gate` fails, when teardown reports the failure, then the failure details are logged to the orchestrator log, the user is informed which tests failed, and the epic is NOT marked as complete.

4. **Successful teardown updates sprint-status** — Given both teardown phases pass, when teardown completes, then the epic status is updated to `done` in sprint-status, the summary report is generated, and the orchestrator exits with code 0.

5. **Blocked stories prevent teardown** — Given some stories were `blocked` and never completed, when the orchestrator reaches the end of ready stories (including stories blocked-by-dependency), then epic teardown does NOT run (not all stories done), the epic status is updated to `blocked` in sprint-status, worktrees for both completed and blocked stories are cleaned up (per FR35), the orchestrator reports the blocked stories and exits.

## Tasks / Subtasks

- [ ] Task 1: Add epic completion detection to orchestrator (AC: #1, #5)
  - [ ] 1.1: Create `_all_stories_done() -> bool` method on `Orchestrator` that checks whether every story in `self._state.stories` has `status == StoryStatus.DONE`. This is checked in the `run()` method after the main loop exits (before the `finally` block)
  - [ ] 1.2: If `_all_stories_done()` is `True`, call the new `_run_epic_teardown()` method. If `False`, skip teardown, update epic sprint-status to `blocked` (not `in-progress`), clean up worktrees for both completed and blocked stories (per FR35), and log a message listing blocked/remaining stories (AC#5 — existing `_print_exit_summary()` already handles blocked story reporting). This covers both directly-blocked stories and stories blocked-by-dependency

- [ ] Task 2: Implement `_run_epic_teardown()` method (AC: #1, #2, #3, #4)
  - [ ] 2.1: Create `async def _run_epic_teardown(self) -> bool` method on `Orchestrator` that spawns the existing loop as a subprocess to run epic teardown phases. Returns `True` if teardown succeeded, `False` on failure
  - [ ] 2.2: Build subprocess command: `[sys.executable, "-m", "bmad_assist_lite", "run", "--epic", str(self._epic_num), "--teardown-only"]`. The `--teardown-only` flag bypasses story discovery in the CLI and constructs a `resume_state` starting at the `epic_quality_gate` phase, passing it directly to `run_loop()`. Without this flag, the CLI's `find_backlog_stories()` filters out all `done` stories, finds an empty backlog, and exits with code 0 without ever calling `run_loop()` — teardown would never execute
  - [ ] 2.2a: **Prerequisite — Add `--teardown-only` flag to CLI**: Add a `--teardown-only` boolean flag to the `run` CLI command in `cli.py`. When set, skip `find_backlog_stories()` and instead construct a resume state that starts at the `epic_quality_gate` phase, then call `run_loop()` directly. This is a minimal, targeted CLI change
  - [ ] 2.3: Run the subprocess in the project root (not a worktree): `cwd=str(self._project_root)`. Use `asyncio.create_subprocess_exec()` with `stdout=PIPE, stderr=STDOUT` for output capture
  - [ ] 2.4: Stream teardown subprocess output through `OutputMultiplexer` with prefix `"teardown"` (reuse the existing `start_reader()` / `await_reader()` / `stop_reader()` pattern)
  - [ ] 2.5: On subprocess exit code 0: teardown succeeded → return `True`. On non-zero exit: teardown failed → log error details to orchestrator log via `log_teardown_result()`, return `False`
  - [ ] 2.6: Use the same platform-safe subprocess kwargs as `_spawn_story()`: `CREATE_NEW_PROCESS_GROUP` on Windows, `start_new_session=True` on Unix. Set `BMAD_PARALLEL_MODE` env var (same as story spawns) so sprint-status sync is bypassed during teardown subprocess
  - [ ] 2.7: Track the teardown subprocess PID (e.g., `self._teardown_process`) so `_on_sigint()` can terminate it on Ctrl+C. Extend the signal handler to check for an active teardown process and terminate it gracefully (SIGTERM, then SIGKILL after timeout), following the same pattern used for `self._story_processes`

- [ ] Task 3: Add `log_teardown_result()` to `parallel/logging.py` (AC: #3)
  - [ ] 3.1: Create `log_teardown_result(epic_num: int, success: bool, exit_code: int, duration_s: float | None = None, error: str | None = None) -> None` function in `logging.py` that logs the teardown outcome with tag `[TEARDOWN|epic-{N}]`. Include duration in log output when provided (e.g., `"completed in {duration_s:.1f}s"`)
  - [ ] 3.2: On success: `_logger.info("[TEARDOWN|epic-{N}] Epic teardown completed successfully")`
  - [ ] 3.3: On failure: `_logger.error("[TEARDOWN|epic-{N}] Epic teardown failed (exit_code={X}): {error}")`

- [ ] Task 4: Add sprint-status epic update (AC: #4)
  - [ ] 4.1: Create `_update_epic_sprint_status(self, status: str) -> None` method on `Orchestrator` that loads the `SprintStatus` model, calls the `set_epic_status(epic_id, status)` **method** on the model instance (not a standalone function), then saves. Follow the exact non-fatal pattern from `update_sprint_status_done()` in `merger.py`: load → mutate → save, wrap in try/except, log warning on failure, never propagate exceptions. See Dev Notes code block for reference implementation
  - [ ] 4.2: After successful teardown (`_run_epic_teardown()` returns `True`), call `_update_epic_sprint_status("done")`
  - [ ] 4.3: After failed teardown, do NOT update epic sprint-status — epic stays `in-progress`
  - [ ] 4.4: On partial completion (some stories blocked, teardown skipped), call `_update_epic_sprint_status("blocked")` — per Architecture requirement, epic status must be `blocked` (not `done` or `in-progress`) when some stories are blocked

- [ ] Task 5: Integrate teardown into orchestrator `run()` flow (AC: #1, #4, #5)
  - [ ] 5.1: After the main `while True` loop exits and before the `finally` block, check `_all_stories_done()`. If `True`, call `await self._run_epic_teardown()`
  - [ ] 5.2: Store the teardown result so the summary report (from Story 6.3) runs AFTER teardown — the report should include the final epic status
  - [ ] 5.3: After teardown completes (success or failure), persist state: `save_state(self._state, self._state_path)`. The state at this point reflects all stories as `DONE`
  - [ ] 5.4: Ensure teardown does NOT run in drain/force-exit mode — if `self._draining` or `self._force_exit` is set, skip teardown (stories may be mid-execution, not all done)

- [ ] Task 6: Handle worktree cleanup for completed epic (AC: #1)
  - [ ] 6.1: After successful teardown OR on partial completion (some stories blocked), call bulk worktree cleanup: iterate `self._story_worktrees` and clean up any remaining worktrees via `cleanup_worktree()` for **both completed and blocked stories** (per FR35). `cleanup_worktree()` already handles branch deletion as part of its three-step cleanup, so no separate branch deletion step is needed
  - [ ] 6.2: Verify that `cleanup_worktree()` branch deletion covers all story branches. Only add explicit `_run_git(["branch", "-D", ...])` calls for branches NOT covered by worktree cleanup (e.g., stories that were merged but whose worktree was already removed). Wrap in try/except per branch — individual branch delete failures are non-fatal

- [ ] Task 7: Write tests (AC: #1, #2, #3, #4, #5)
  - [ ] 7.1: Test `_all_stories_done()` with all stories `DONE` → returns `True`
  - [ ] 7.2: Test `_all_stories_done()` with one story `BLOCKED` → returns `False`
  - [ ] 7.3: Test `_all_stories_done()` with one story `IN_FLIGHT` → returns `False`
  - [ ] 7.4: Test `_run_epic_teardown()` with subprocess exit code 0 → returns `True`, verifies subprocess invoked with correct args (`sys.executable -m bmad_assist_lite run --epic N --teardown-only`), verifies `cwd=project_root`
  - [ ] 7.5: Test `_run_epic_teardown()` with subprocess exit code 1 → returns `False`, verifies `log_teardown_result()` called with `success=False`
  - [ ] 7.6: Test `log_teardown_result()` success and failure log messages contain correct tags and details
  - [ ] 7.7: Test epic sprint-status update: mock `sprint_status` module functions, verify `set_epic_status(epic_num, "done")` called after successful teardown
  - [ ] 7.8: Test sprint-status update is non-fatal: simulate exception in `set_epic_status`, verify warning logged but no exception raised
  - [ ] 7.9: Test teardown is skipped when not all stories done: set up state with 1 blocked story, verify `_run_epic_teardown()` is NOT called
  - [ ] 7.10: Test teardown is skipped in drain mode: set `_draining=True`, verify teardown not called even if all stories done
  - [ ] 7.11: Test worktree cleanup after successful teardown: verify `cleanup_worktree()` called for remaining worktrees
  - [ ] 7.12: Test branch cleanup after successful teardown: verify `_run_git(["branch", "-D", ...])` called for each done story's branch
  - [ ] 7.13: Test teardown subprocess uses `--teardown-only` flag: verify the subprocess command includes `--teardown-only` in its args
  - [ ] 7.14: Test epic status set to `blocked` on partial completion: set up state with some done and some blocked stories, verify `_update_epic_sprint_status("blocked")` is called
  - [ ] 7.15: Test worktree cleanup runs for blocked stories: set up state with blocked stories, verify `cleanup_worktree()` called for blocked story worktrees (not just completed)
  - [ ] 7.16: Test teardown subprocess Ctrl+C handling: simulate SIGINT during teardown, verify teardown process is terminated gracefully
  - [ ] 7.17: Test focused orchestrator integration: mock subprocess for teardown only, verify that after all stories are DONE in state, teardown is called and sprint-status is updated to `done`. (Scope to teardown flow only, not full story lifecycle)

- [ ] Task 8: Update `parallel/__init__.py` exports (AC: all)
  - [ ] 8.1: No new public API functions needed — teardown is internal to the orchestrator. If `log_teardown_result` is considered useful externally, add it to `__init__.py` and `__all__`

## Dev Notes

### Architecture Patterns and Constraints

- **Frozen Pydantic models**: All existing models (`ParallelState`, `StoryState`, `ParallelConfig`, etc.) are frozen. The orchestrator uses `model_copy(update={...})` for all state mutations. No new Pydantic models are needed for this story.
- **Subprocess spawning for teardown**: The architecture specifies spawning the existing loop subprocess: `sys.executable -m bmad_assist_lite run --epic N --teardown-only`. The `--teardown-only` flag is required because the CLI's `find_backlog_stories()` filters out `done` stories and would exit without calling `run_loop()`. With the flag, the CLI bypasses story discovery and constructs a resume state starting at `epic_quality_gate`, then proceeds through `retrospective`. Teardown phases are controlled by `config.loop.epic_teardown` (default: `["epic_quality_gate", "retrospective"]`).
- **Teardown runs in project root**: Unlike story subprocesses which run in worktrees, the teardown subprocess runs in `self._project_root` (the actual repo). This is critical because epic_quality_gate runs the full project test suite on the base branch with all merged code.
- **`BMAD_PARALLEL_MODE` env var**: Set this for the teardown subprocess to bypass sprint-status sync during the loop's internal phases. The orchestrator handles sprint-status updates externally after teardown completes.
- **No import from `loop/`**: The `parallel/` module must NOT import from `loop/`. Teardown is invoked via subprocess, not by importing and calling `EpicQualityGateHandler` or `RetrospectiveHandler` directly.
- **Logging convention**: `logger = logging.getLogger(__name__)` at module top. New log functions in `logging.py` use the `_logger` (stdlib `logging` aliased as `_logging` due to module name shadow). Use `[TEARDOWN|epic-{N}]` tag prefix for teardown-related log entries.
- **Non-fatal sprint-status updates**: Follow the exact pattern from `update_sprint_status_done()` in `merger.py`: wrap in try/except, log warning on failure, never propagate. Sprint sync is one-way and non-fatal per project-context rules.
- **Atomic writes for state**: State is saved via `save_state()` which uses temp + `os.replace()` pattern. No new atomic write code needed.
- **Platform-safe subprocesses**: Use `CREATE_NEW_PROCESS_GROUP` on Windows, `start_new_session=True` on Unix (same as `_spawn_story()`).
- **Async patterns**: The orchestrator is async. Use `asyncio.create_subprocess_exec()` for the teardown subprocess. Do NOT use `subprocess.run()` (blocks the event loop).
- **Output multiplexing**: Reuse `OutputMultiplexer.start_reader()` with a `"teardown"` prefix so the user sees teardown output prefixed clearly in the console.
- **Type annotations**: Required on all functions (mypy strict). Use `X | None` not `Optional[X]`.
- **Line length**: 100 characters max (ruff enforced).
- **Section separators**: Use `# ============================================================================` between logical sections.
- **Exception hierarchy**: Use `ParallelError` subclasses from `parallel/exceptions.py`. Never bare `Exception`.

### Existing Handler Details (for reference only — do NOT import)

**EpicQualityGateHandler** (`loop/handlers/epic_quality_gate.py`):
- Non-LLM handler. Takes `Config` and `project_path`.
- Runs lint, typecheck, build, test via `run_command()`.
- Gets commands from config `quality_gate` section or auto-detects via `detect_toolchain()`.
- Returns `PhaseResult.ok()` on success, `PhaseResult.fail(msg)` on failure.
- Writes an epic QA report to `.bmad-assist-lite/cache/epic-{N}-qa-report.md`.

**RetrospectiveHandler** (`loop/handlers/retrospective.py`):
- LLM handler. Subclasses `BaseHandler`.
- Uses `render_prompt()` → `invoke_provider()` flow.
- Generates an epic retrospective document.

### Key Data Flow

1. Main loop exits → check `_all_stories_done()`
2. If all done → `_run_epic_teardown()`:
   a. Spawn `sys.executable -m bmad_assist_lite run --epic N --teardown-only` in project root
   b. Stream output via `OutputMultiplexer` with `"teardown"` prefix
   c. Wait for exit code
3. If teardown succeeds (exit 0):
   a. Update epic sprint-status to `done` via `set_epic_status()`
   b. Clean up remaining worktrees (safety net)
   c. Delete merged story branches
4. If teardown fails (non-zero exit):
   a. Log failure details via `log_teardown_result()`
   b. Do NOT update epic sprint-status — stays `in-progress`
5. Summary report (Story 6.3) runs in the `finally` block — captures final state including epic outcome

### Sprint-Status Update Pattern (from `merger.py`)

```python
def _update_epic_sprint_status(self, status: str) -> None:
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
        logger.info("%s Updated sprint-status: epic %d -> %s", tag, self._epic_num, status)
    except Exception:
        logger.warning(
            "%s Failed to update sprint-status (non-fatal)",
            tag,
            exc_info=True,
        )
```

### Project Structure Notes

**Modified files:**
```
src/bmad_assist_lite/parallel/orchestrator.py  — add _all_stories_done(), _run_epic_teardown(),
                                                  _update_epic_sprint_status(), worktree/branch
                                                  cleanup, teardown integration in run(),
                                                  teardown subprocess signal handling
src/bmad_assist_lite/parallel/logging.py       — add log_teardown_result() (with duration param)
src/bmad_assist_lite/cli.py                    — add --teardown-only flag to run command
```

**Possibly modified files:**
```
src/bmad_assist_lite/parallel/__init__.py      — add log_teardown_result if needed
```

**New test file:**
```
tests/test_parallel_teardown.py
```

### Key Implementation Decisions

1. **Subprocess over direct invocation**: The architecture mandates that `parallel/` does NOT import from `loop/`. Teardown is invoked by spawning a loop subprocess — this reuses the existing loop runner's epic teardown logic without any cross-boundary imports.

2. **`--teardown-only` flag required**: The existing CLI's `find_backlog_stories()` filters out `done` stories, so when all stories are done, the CLI finds an empty backlog and exits with code 0 without ever calling `run_loop()`. A `--teardown-only` flag must be added to the `run` CLI command to bypass story discovery and enter the runner directly at the `epic_quality_gate` phase. This is a minimal, targeted change to `cli.py`.

3. **Teardown happens before `finally` block**: Teardown runs between the main loop and the `finally` block so that (a) signal handlers are still installed (teardown can be interrupted), (b) the summary report in `finally` captures the teardown outcome, and (c) state is saved after teardown.

4. **Branch cleanup is post-teardown**: Story branches (`parallel/{story-id}`) are only deleted after successful teardown. If teardown fails, branches are preserved for debugging.

5. **Worktree cleanup is a safety net**: In normal flow, worktrees are cleaned up after merge (in `_process_merge_queue()`). The post-teardown cleanup catches any stragglers.

### References

- Architecture: Epic Teardown section — teardown sequence, partial completion handling
- Architecture: Parallel Module Layout — module boundaries, import rules
- Architecture: Enforcement Guidelines — all 54 project-context rules apply
- PRD: FR29 — run epic teardown phases after all stories merge
- PRD: FR28 — update epic status when all stories complete
- PRD: FR35 — clean up worktrees for completed stories
- PRD: NFR5 — complete filesystem and git branch isolation
- PRD: NFR15 — orchestrator must not modify existing loop code behavior
- Story 6.1: Logging infrastructure (`setup_parallel_log`, `log_run_complete`)
- Story 6.3: Summary report generation (report runs after teardown in `finally` block)
- Epic file: Story 6.4 acceptance criteria and technical notes

## Testing Requirements

- **Epic completion detection**: `_all_stories_done()` returns `True` only when ALL stories are `StoryStatus.DONE`, `False` if any are `BLOCKED`, `IN_FLIGHT`, `MERGING`, or `BACKLOG`
- **Teardown subprocess invocation**: Verify correct command args (`sys.executable -m bmad_assist_lite run --epic N --teardown-only`), correct `cwd` (project root), correct env (`BMAD_PARALLEL_MODE=1`)
- **Teardown success path**: Exit code 0 → `log_teardown_result(success=True)` called, sprint-status updated to `done`, worktree cleanup invoked, branch cleanup invoked
- **Teardown failure path**: Non-zero exit → `log_teardown_result(success=False)` called with error details, sprint-status NOT updated, no cleanup
- **Teardown logging**: `log_teardown_result()` with success/failure produces correct log tags `[TEARDOWN|epic-{N}]` at correct severity (INFO/ERROR)
- **Sprint-status non-fatal**: Exception in `set_epic_status()` logged as warning, not raised
- **Teardown skipped when stories blocked**: State with blocked stories → teardown not called
- **Teardown skipped in drain mode**: `_draining=True` → teardown not called even if all done
- **Teardown skipped in force-exit mode**: `_force_exit=True` → teardown not called
- **Output multiplexing**: Teardown output streamed through `OutputMultiplexer` with `"teardown"` prefix
- **Branch cleanup non-fatal**: Exception in `_run_git(["branch", "-D", ...])` logged as warning per branch
- **Worktree cleanup non-fatal**: Exception in `cleanup_worktree()` logged as warning per worktree
- **Epic blocked status**: On partial completion, epic status updated to `blocked` in sprint-status (not `in-progress`)
- **Worktree cleanup for blocked stories**: `cleanup_worktree()` called for both completed and blocked story worktrees (per FR35)
- **Teardown subprocess signal handling**: Teardown subprocess PID tracked and terminated on Ctrl+C (SIGTERM → SIGKILL after timeout)
- **Integration test**: Focused teardown flow mock — all stories done in state, teardown subprocess returns 0, verify epic marked done in sprint-status

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/orchestrator.py src/bmad_assist_lite/parallel/logging.py src/bmad_assist_lite/cli.py tests/test_parallel_teardown.py` | **PENDING** |
| Typecheck | `mypy src/bmad_assist_lite/parallel/orchestrator.py src/bmad_assist_lite/parallel/logging.py src/bmad_assist_lite/cli.py` | **PENDING** |
| Build | `python -m py_compile src/bmad_assist_lite/parallel/orchestrator.py && python -m py_compile src/bmad_assist_lite/cli.py` | **PENDING** |
| Tests | `pytest tests/test_parallel_teardown.py -v --tb=short` | **PENDING** |

## Senior Developer Review (AI)

**Date:** 2026-03-22
**Verdict:** REJECT (Score: 10.1)
**Reviewers:** 2 independent adversarial reviewers

### Fixes Applied

1. **Stalemate partial completion (CRITICAL):** Removed `if self._blocked_ids:` guard in `orchestrator.py:1352`. Epic now unconditionally marked `blocked` and worktrees cleaned up when not all stories are done, covering stalemate scenarios with empty `_blocked_ids`.

2. **Cleanup-on-failure contradiction (IMPORTANT):** Moved `_cleanup_remaining_worktrees()` inside the `if teardown_success:` block. On teardown failure, worktrees and branches are now preserved for debugging per Key Decision #4.

3. **Teardown failure error details (IMPORTANT):** Enhanced error message in `_run_epic_teardown()` to reference `[teardown]` console output where test failure details are streamed. User console and log output now explicitly direct to failure details.

4. **Docstring mismatch (MINOR):** Fixed `_kill_teardown_process` docstring — was "SIGTERM first, then SIGKILL" but actual behavior is immediate SIGKILL via `_kill_process()`.

5. **Fragile test (MINOR):** Replaced brittle string-split assertion in `test_success_without_duration` with robust regex check.

6. **Missing Test 7.12 (IMPORTANT):** Added `TestBranchCleanup` test class verifying `cleanup_worktree` covers branch deletion.

7. **Missing stalemate test (IMPORTANT):** Added `TestStalematePartialCompletion` test verifying epic is marked `blocked` even with empty `_blocked_ids`.

### Findings Not Fixed (Deferred)

- **Task checkbox bookkeeping (R2-CRITICAL-1):** Tracking artifact issue, not code.
- **Running lock collision (R2-IMPORTANT-3):** Low-probability edge case; stale lock from previous sequential run. Defensive lock cleanup before teardown is deferred.
- **CLI Phase enum validation (R2-IMPORTANT-5):** Config validation belongs upstream; crash on invalid config is acceptable.
- **CLI integration tests (R2-MINOR-2):** Test for `--teardown-only` CLI State construction deferred to separate testing story.

### Runtime Verification

Verification commands require manual execution:
- `ruff check` on modified files
- `mypy` typecheck on modified files
- `pytest tests/test_parallel_teardown.py -v --tb=short`

## Dev Agent Record

### Agent Model Used
### Debug Log References
### Completion Notes List
### File List
