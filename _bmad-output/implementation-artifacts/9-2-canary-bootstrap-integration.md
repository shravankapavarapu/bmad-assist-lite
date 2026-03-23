# Story 9.2: Canary Bootstrap Integration

Status: in-progress

## Story

As a developer running parallel mode,
I want the first worktree to act as a canary that validates the bootstrap recipe before other worktrees are created,
so that a broken base branch is detected in 30 seconds instead of wasting LLM tokens across multiple doomed worktrees.

## Acceptance Criteria

1. **Canary runs full bootstrap** — Given bootstrap config is set with `setup_commands` and `validation_command`, when the orchestrator starts a parallel run, then the first story's worktree runs full bootstrap including validation (`bootstrap_worktree(validate=True)`) before subprocess spawn.

2. **Canary failure aborts run** — Given the canary worktree's bootstrap fails (validation command returns non-zero), when the orchestrator detects the failure, then no other worktrees are created, the canary worktree is cleaned up, the run aborts with a clear error message including the failed command's output, and exit code is non-zero.

3. **Non-canary skips validation** — Given the canary worktree's bootstrap succeeds, when the orchestrator proceeds to spawn remaining stories, then remaining worktrees run `bootstrap_worktree(validate=False)` — copy + setup only, no validation command.

4. **Non-canary setup failure blocks story** — Given a non-canary worktree's setup command fails, when the orchestrator detects the failure, then that story is marked as blocked with `block_reason="Bootstrap setup failed: {error}"`, its worktree is cleaned up, and other stories continue normally.

5. **Zero overhead when unconfigured** — Given no bootstrap config is set (all defaults), when the orchestrator runs, then behavior is identical to current (no bootstrap phase, immediate subprocess spawn).

6. **Canary success logging** — Given the canary bootstrap succeeds, when the orchestrator log is inspected, then it contains `[BOOTSTRAP] Canary story {id} bootstrap passed — proceeding with batch` at INFO level.

7. **Canary failure logging** — Given the canary bootstrap fails, when the orchestrator log is inspected, then it contains `[BOOTSTRAP] Canary story {id} bootstrap FAILED — aborting parallel run` at ERROR level, followed by the captured command output.

8. **Resume skips canary** — Given a parallel run is resumed with `--resume`, when the orchestrator spawns stories, then canary bootstrap logic is skipped entirely (worktrees already exist and were previously bootstrapped), and `_canary_passed` is set to `True` at startup.

## Tasks / Subtasks

- [ ] Task 1: Add `_has_bootstrap_config()` helper to orchestrator (AC: #5)
  - [ ] 1.1: Implement a private method `_has_bootstrap_config(self) -> bool` that checks whether any bootstrap fields in `ParallelConfig` are non-default (`copy_to_worktree` non-empty, `setup_commands` non-empty, or `validation_command` is not None)
  - [ ] 1.2: Short-circuit all bootstrap logic in `_spawn_story()` when this returns `False`

- [ ] Task 2: Add canary tracking state to `Orchestrator.__init__()` (AC: #1, #2, #3)
  - [ ] 2.1: Add `self._canary_passed: bool = False` instance attribute — tracks whether the canary has already passed for this run
  - [ ] 2.2: Add `self._canary_story_id: str | None = None` instance attribute — tracks which story was selected as canary (for logging)

- [ ] Task 3: Run canary bootstrap synchronously in `run()` before batch spawn (AC: #1, #2)
  - [ ] 3.1: In the `run()` method, after dependency resolution selects the first story but **before** the staggered concurrent spawn loop, check `_has_bootstrap_config()`; if `False`, skip to batch spawn (zero overhead)
  - [ ] 3.2: If bootstrap is configured, create the canary worktree and run `bootstrap_worktree(validate=True)` via `asyncio.to_thread()` — this must complete (pass or fail) before any other stories are spawned, avoiding race conditions
  - [ ] 3.3: On canary success: set `self._canary_passed = True`, `self._canary_story_id = story_id`, log `[BOOTSTRAP] Canary story {id} bootstrap passed — proceeding with batch` at INFO level via `_output_mux.write_orchestrator()`, then spawn the canary's subprocess and proceed to batch spawn loop for remaining stories
  - [ ] 3.4: On canary failure: log `[BOOTSTRAP] Canary story {id} bootstrap FAILED — aborting parallel run` at ERROR level with full diagnostic output via `_output_mux.write_orchestrator()`, clean up worktree via `cleanup_worktree()`, raise `ParallelError` to abort — no other worktrees are created

- [ ] Task 3b: Integrate non-canary bootstrap into `_spawn_story()` (AC: #3, #4, #5)
  - [ ] 3b.1: After `create_worktree()` succeeds and before subprocess spawn, check `_has_bootstrap_config()`; if `False`, proceed directly to subprocess spawn (no overhead)
  - [ ] 3b.2: For non-canary stories (when `self._canary_passed is True`): run `bootstrap_worktree(validate=False)` via `asyncio.to_thread()`
  - [ ] 3b.3: On non-canary setup failure: log warning, clean up worktree, mark story as BLOCKED with `block_reason="Bootstrap setup failed: {error}"`, return exit code indicating block (do NOT abort entire run)
  - [ ] 3b.4: On non-canary setup success: proceed to subprocess spawn as normal

- [ ] Task 4: Handle canary abort in orchestrator `run()` (AC: #2)
  - [ ] 4.1: Wrap the canary bootstrap call (Task 3.2) in a `try/except ParallelError` block inside `run()`. Since the canary runs **before** any other stories are spawned, there are no in-flight stories to cancel — simply clean up the canary worktree, persist state, and return non-zero exit code
  - [ ] 4.2: Write abort message to both orchestrator log and console output via `_output_mux.write_orchestrator()`

- [ ] Task 4b: Skip canary on resume (AC: #8)
  - [ ] 4b.1: In `run()`, if the run is a resume (`--resume`), set `self._canary_passed = True` immediately and skip the canary bootstrap block — existing worktrees were already bootstrapped in the original run
  - [ ] 4b.2: Non-canary bootstrap in `_spawn_story()` is also skipped when `resume=True` (worktree already exists)

- [ ] Task 5: Ensure stagger delay does NOT apply to canary (AC: #1)
  - [ ] 5.1: Move or condition the stagger delay so it only applies after the canary has passed — the canary story should not be delayed

- [ ] Task 6: Write tests for canary bootstrap integration (AC: #1-#7)
  - [ ] 6.1: Test canary runs full bootstrap with `validate=True` on first story
  - [ ] 6.2: Test canary failure aborts entire run — no subsequent worktrees created
  - [ ] 6.3: Test canary failure cleans up worktree
  - [ ] 6.4: Test canary success sets `_canary_passed = True` and remaining stories get `validate=False`
  - [ ] 6.5: Test non-canary setup failure marks story as BLOCKED, continues other stories
  - [ ] 6.6: Test zero overhead when no bootstrap config — no `bootstrap_worktree()` calls
  - [ ] 6.7: Test correct log messages for canary success and failure
  - [ ] 6.8: Test stagger delay not applied to canary story
  - [ ] 6.9: Test resume mode skips canary bootstrap entirely

## Dev Notes

### Architecture Patterns & Constraints

- **Frozen Pydantic models**: `ParallelState` uses `model_copy(update={...})` for mutations. `ParallelConfig` is frozen. Do not mutate directly.
- **Async bridge**: `bootstrap_worktree()` is synchronous (uses `subprocess.Popen`). Must be called via `asyncio.to_thread()` to avoid blocking the event loop. This is consistent with `create_worktree()` in orchestrator.
- **Worktree cleanup**: Use `cleanup_worktree()` from `worktree_manager.py` (already imported in orchestrator) to remove failed worktrees. This handles both git worktree removal and directory cleanup.
- **Output multiplexing**: All orchestrator messages must go through `self._output_mux.write_orchestrator()` (async). Never use `print()` or direct logger for user-visible messages.
- **Logging prefix**: All bootstrap-related log messages use `[BOOTSTRAP]` prefix per architecture doc.
- **State persistence**: Every status change persists to disk immediately via `save_state()`. Blocking a story updates `self._state` and calls `save_state()`.
- **Exception hierarchy**: Use `ParallelError` for bootstrap failures that need to propagate up the call stack (e.g., canary failure aborting the run).
- **Singleton safety**: The orchestrator itself is not a singleton. Bootstrap functions from `bootstrap.py` are stateless — safe to call in any context.
- **Type annotations**: Full type hints on all functions (mypy strict). Use `X | None` syntax.
- **Line length**: 100 characters max.
- **Imports**: Absolute imports only. Add `from bmad_assist_lite.parallel.bootstrap import bootstrap_worktree, BootstrapResult` to `orchestrator.py` (these do NOT currently exist in the file — must be added).

### Project Structure Notes

Files to modify:
- `src/bmad_assist_lite/parallel/orchestrator.py` — Primary target. Add `_has_bootstrap_config()`, canary state attrs, bootstrap calls in `_spawn_story()`, abort handling in `run()`

Test file to create:
- `tests/test_canary_bootstrap.py` — Integration tests for canary pattern in orchestrator

Existing files to reference (read-only):
- `src/bmad_assist_lite/parallel/bootstrap.py` — `bootstrap_worktree()`, `BootstrapResult` (implemented in Story 9.1)
- `src/bmad_assist_lite/parallel/config.py` — `ParallelConfig` with bootstrap fields (`copy_to_worktree`, `copy_strict`, `setup_commands`, `validation_command`, `bootstrap_timeout`)
- `src/bmad_assist_lite/parallel/worktree_manager.py` — `create_worktree()`, `cleanup_worktree()`
- `src/bmad_assist_lite/parallel/state.py` — `ParallelState`, `StoryStatus`, `save_state()`
- `src/bmad_assist_lite/parallel/logging.py` — `log_story_blocked()`

### Key Implementation Details

- **Canary selection**: The canary is whichever story the dependency resolver selects first — no special selection logic needed. Simply check `self._canary_passed` to determine if the current story is the canary.
- **`_has_bootstrap_config()` pattern**: Check `config.copy_to_worktree`, `config.setup_commands`, and `config.validation_command`. If all are empty/None, return `False`. This ensures zero overhead for unconfigured users.
- **Canary abort mechanism**: On canary failure, raise `ParallelError` from the canary block in `run()`. Since the canary runs before batch spawn, there are no in-flight stories to cancel — the error propagates cleanly to the top-level handler.
- **Stagger delay conditioning**: The current stagger delay is inside the semaphore in `_spawn_story()` (lines 419-422). For the canary, skip the delay. One approach: `if self._canary_passed and self._config.stagger_delay > 0: await asyncio.sleep(...)`.
- **Non-canary blocking**: Reuse the existing blocking pattern from `_on_story_complete()` — update `self._blocked_ids`, call `self._state.with_story_status(story_id, StoryStatus.BLOCKED, ...)`, persist, log, and clean up worktree.
- **Exit code from `_spawn_story()`**: Currently returns the subprocess exit code (int). Canary failure is now handled in `run()` via `ParallelError` (not in `_spawn_story()`). For non-canary bootstrap failure in `_spawn_story()`, return exit code `-2` as a sentinel that `_on_story_complete()` interprets as blocked.
- **Resume path**: On resume (`--resume`), skip canary logic since worktrees already exist and were previously bootstrapped. The canary pattern only applies to fresh spawns.
- **Copy-only config edge case**: If only `copy_to_worktree` is set (no `setup_commands`, no `validation_command`), the canary still runs `bootstrap_worktree(validate=True)` but `bootstrap_worktree()` will skip validation when `validation_command` is None. The canary adds no validation benefit in this case but still validates the file copy step, which is acceptable.

### References

- Architecture: "Worktree Bootstrap" — canary pattern specification, error handling rules
- Architecture: "Worktree Loop Spawning" — subprocess spawn pattern, `asyncio.to_thread()` usage
- Architecture: "Implementation Patterns & Consistency Rules" — async patterns, frozen models
- Story 9.1: `bootstrap.py` — `bootstrap_worktree()` API, `BootstrapResult` model, `_has_bootstrap_config()` concept
- Project Context: Testing rules (autouse fixtures, singleton resets, async test mode)
- Existing code: `orchestrator.py` `_spawn_story()` (lines 391-577) — current worktree creation and subprocess flow
- Existing code: `orchestrator.py` `_on_story_complete()` (lines 583-648) — story blocking pattern

## Testing Requirements

- **Canary runs full validation**: Mock `bootstrap_worktree`, verify first story calls with `validate=True`
- **Canary success enables non-canary spawns**: After canary passes, verify subsequent stories call `bootstrap_worktree(validate=False)`
- **Canary failure aborts run**: Mock bootstrap returning `BootstrapResult(success=False)`, verify no further stories spawned, worktree cleaned up, non-zero exit
- **Canary failure logging**: Capture log output, verify `[BOOTSTRAP] Canary story {id} bootstrap FAILED` at ERROR level with diagnostic output
- **Canary success logging**: Verify `[BOOTSTRAP] Canary story {id} bootstrap passed` at INFO level
- **Non-canary setup failure blocks story**: Mock non-canary bootstrap failing, verify story marked BLOCKED with descriptive reason, other stories unaffected
- **Non-canary setup failure worktree cleanup**: Verify `cleanup_worktree()` called for failed non-canary
- **Zero overhead unconfigured**: With default `ParallelConfig`, verify `bootstrap_worktree` never called
- **Stagger delay not applied to canary**: Verify first story spawns without delay, subsequent stories respect `stagger_delay`
- **Resume skips canary**: When spawning with `resume=True`, verify bootstrap is skipped
- **Edge case — all stories fail bootstrap**: After canary passes, all non-canary setups fail — verify all marked BLOCKED, clean exit
- **Edge case — drain during bootstrap**: If drain mode activated while bootstrap is running, verify graceful handling

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/orchestrator.py` | **PENDING** |
| Typecheck | `mypy src/bmad_assist_lite/parallel/orchestrator.py` | **PENDING** |
| Tests | `pytest tests/test_canary_bootstrap.py -v` | **PENDING** |

## Senior Developer Review (AI)

**Verdict**: REJECT (Evidence Score: 6.6)
**Date**: 2026-03-23

### Fixes Applied
1. **Double-blocking bug fixed** (IMPORTANT): Added `elif story_id in self._blocked_ids: pass` guard in `_on_story_complete()` to prevent overwriting descriptive `"Bootstrap setup failed: {msg}"` error with generic `"Exit code -2"` when non-canary bootstrap fails.
2. **Canary cleanup failure suppression** (IMPORTANT): Wrapped `cleanup_worktree` call on canary failure path (line ~1421) in `contextlib.suppress(Exception)` to prevent cleanup errors from masking the `ParallelError` bootstrap failure message. Now matches the pattern used in the generic exception handler.
3. **Added drain-during-bootstrap test** (IMPORTANT): Added `test_drain_during_canary_bootstrap` covering the explicitly required edge case of drain mode activation during bootstrap.
4. **Added error preservation test**: Added `test_non_canary_failure_preserves_descriptive_error` to verify the double-blocking fix preserves descriptive block reasons.

### Remaining Issues (Not Fixed)
- Story file tasks all marked `[ ]` despite implementation existing — Dev Agent Record empty
- Quality gates (lint, typecheck, tests) not yet executed against final code — sandbox blocked execution
- Test mocking approach (patching `asyncio.to_thread` globally) is valid but less precise than patching just the bootstrap function

### Rejected Findings
- **Finding 7** (exit code test gap): FALSE POSITIVE. `run()` returns `None` and uses `ParallelError` for abort signaling. Testing `ParallelError` raise IS the correct test — CLI layer exit code conversion is a separate concern.
- **Finding 2** (duplicate state transition): Reviewer self-withdrew after closer analysis confirmed correct behavior.

## Dev Agent Record

### Agent Model Used
### Debug Log References
### Completion Notes List
### File List
