# Story 5.1: Crash Recovery & Resume In-Flight Stories

Status: in-progress

## Story

As a developer,
I want the orchestrator to resume in-flight stories after a crash,
so that no work is lost when the process is interrupted unexpectedly.

## Acceptance Criteria

1. **In-flight + worktree exists → resume**: Given `parallel-state.yaml` shows a story as `in_flight` with a worktree path, when the orchestrator starts and the worktree exists on disk, then the story remains `in_flight` with its existing worktree path preserved so the orchestrator can re-spawn the loop with `--resume` in the existing worktree.

2. **In-flight + worktree missing → reset to backlog**: Given `parallel-state.yaml` shows a story as `in_flight`, when the orchestrator starts and the worktree does NOT exist on disk, then the story status is reset to `backlog` in the state, stale fields (`worktree_path`, `started_at`, `error`) are cleared, and a warning is logged: `"Story {id} was in-flight but worktree missing -- reset to backlog"`.

3. **Done and blocked stories preserved**: Given the orchestrator restarts after a crash, stories marked `done` remain `done` and stories marked `blocked` remain `blocked` — recovery does not alter terminal statuses.

4. **Recovery performance**: Recovery completes within 30 seconds (NFR3) for typical project sizes.

5. **Temp file cleanup**: Orphaned `*.tmp` files adjacent to `parallel-state.yaml` are cleaned up during recovery (already handled by `load_state()` but recovery should also clean `.tmp` files in cache directories).

6. **State persisted after recovery**: After recovery reconciliation, the updated `parallel-state.yaml` is atomically saved so subsequent crashes don't re-run recovery on stale data.

7. **Merging stories reset to backlog**: Stories in `merging` status with no worktree on disk are reset to `backlog` (the merge was never completed). Stories in `merging` with a worktree still present are preserved as `merging` (merge can be reattempted).

## Tasks / Subtasks

- [ ] Task 1: Create `recovery.py` module skeleton (AC: #1-#7)
  - [ ] 1.1: Create `src/bmad_assist_lite/parallel/recovery.py` with module docstring, imports, and `logger = logging.getLogger(__name__)`
  - [ ] 1.2: Define the public function signature: `recover_state(state: ParallelState, project_root: Path, worktree_base_dir: Path | None = None) -> ParallelState`
  - [ ] 1.3: Add full type annotations on all functions (mypy strict)

- [ ] Task 2: Implement worktree existence check (AC: #1, #2)
  - [ ] 2.1: Call `list_worktrees(project_root)` to get all actual worktrees on disk
  - [ ] 2.2: Build a `set[Path]` of resolved worktree paths from the `WorktreeInfo` list for O(1) lookup
  - [ ] 2.3: Also build a `set[str]` of worktree branch names for cross-referencing

- [ ] Task 3: Implement story reconciliation loop (AC: #1, #2, #3, #7)
  - [ ] 3.1: Iterate over all stories in `state.stories`
  - [ ] 3.2: For `in_flight` stories: check if `story.worktree_path` exists in the on-disk worktree set — if yes, preserve as `in_flight`; if no, reset to `backlog` via `state.with_story_status()` and log warning
  - [ ] 3.3: For `merging` stories: check worktree existence — if missing, reset to `backlog` and log warning; if present, preserve as `merging`
  - [ ] 3.4: For `done` and `blocked` stories: pass through unchanged
  - [ ] 3.5: For `backlog` stories: pass through unchanged

- [ ] Task 4: Implement temp file cleanup (AC: #5)
  - [ ] 4.1: Create helper `_cleanup_temp_files(project_root: Path) -> None` that **recursively** scans the `.bmad-assist-lite/` directory (including `cache/` subdirectory) for `*.tmp` files using `Path.rglob("*.tmp")` and removes them
  - [ ] 4.2: Log each temp file removed at `warning` level
  - [ ] 4.3: Wrap unlink in try/except OSError — log and continue on failure (non-fatal)

- [ ] Task 5: Persist recovered state (AC: #6)
  - [ ] 5.1: After reconciliation loop completes, derive `state_path` via `get_parallel_state_path(project_root)` and call `save_state(recovered_state, state_path)` using the atomic write pattern
  - [ ] 5.2: Log a summary of recovery actions: how many stories were reset, how many preserved, how many temp files cleaned

- [ ] Task 6: Integrate recovery into orchestrator startup (AC: #1-#7)
  - [ ] 6.1: In `Orchestrator.__init__()`, after loading existing state, call `recover_state()` to reconcile before populating in-memory sets
  - [ ] 6.2: Update the in-memory tracking sets (`_done_ids`, `_in_flight_ids`, `_blocked_ids`, `_merging_ids`, `_story_worktrees`) from the recovered state instead of the raw loaded state
  - [ ] 6.3: Add a `resume: bool = False` parameter to `_spawn_story()`. When `resume=True`: skip `create_worktree()` (use the existing `worktree_path` from state), append `--resume` to the CLI args, and log at `info` level: `"Re-spawning story {id} with --resume in existing worktree"`. In `run()`, after recovery, detect `in_flight` stories in `_in_flight_ids` that have no entry in `_running_tasks` and call `_spawn_story(story_id, resume=True)` for each

- [ ] Task 7: Update `__init__.py` exports (AC: #1)
  - [ ] 7.1: Add `recover_state` to `parallel/__init__.py` imports and `__all__`

- [ ] Task 8: Write tests for recovery module (AC: #1-#7)
  - [ ] 8.1: Test in-flight story with existing worktree → preserved as in_flight
  - [ ] 8.2: Test in-flight story with missing worktree → reset to backlog
  - [ ] 8.3: Test done stories → preserved unchanged
  - [ ] 8.4: Test blocked stories → preserved unchanged
  - [ ] 8.5: Test merging story with missing worktree → reset to backlog
  - [ ] 8.6: Test merging story with existing worktree → preserved as merging
  - [ ] 8.7: Test temp file cleanup removes `*.tmp` files
  - [ ] 8.8: Test temp file cleanup handles OSError gracefully
  - [ ] 8.9: Test state is saved after recovery (verify `save_state` called)
  - [ ] 8.10: Test recovery with empty stories dict (no-op edge case)
  - [ ] 8.11: Test recovery with all stories already `done` (no changes)
  - [ ] 8.12: Test `_spawn_story(story_id, resume=True)` skips worktree creation and appends `--resume` to CLI args
  - [ ] 8.13: Test `run()` detects in_flight stories without running tasks and calls `_spawn_story(resume=True)`

## Dev Notes

### Architecture Patterns and Constraints

- **Frozen Pydantic models**: `ParallelState` and `StoryState` use `ConfigDict(frozen=True)`. All mutations MUST use `model_copy(update={...})` via the `with_story_status()` helper. Never assign attributes directly.
- **Atomic file writes**: State persistence uses temp file + `os.replace()` pattern (already implemented in `save_state()`). Recovery must use the same `save_state()` function.
- **`_utc_now()` convention**: Timestamps are naive UTC (`datetime.now(UTC).replace(tzinfo=None)`). Import from `state.py` or redefine locally following the same pattern.
- **Logging convention**: `logger = logging.getLogger(__name__)`. Use `[ORCHESTRATOR]` prefix in log messages for grep-ability. Use `logger.warning()` for recovery actions, `logger.info()` for summary.
- **`_run_git()` wrapper**: All git operations MUST use the `_run_git()` wrapper from `git_ops.py`, never raw `subprocess.run()`.
- **Exception hierarchy**: Use `ParallelError` for recovery-specific errors. Recovery is non-fatal to the orchestrator — catch and log errors, don't propagate.
- **Path handling**: Always use `pathlib.Path`. Use `.resolve()` for comparison to avoid symlink/relative path mismatches.
- **Import style**: Absolute imports only (`from bmad_assist_lite.parallel.state import ...`). No relative imports.

### Source Tree Components to Touch

```
src/bmad_assist_lite/parallel/
  recovery.py          # NEW — main recovery logic
  __init__.py          # UPDATE — add recover_state export
  orchestrator.py      # UPDATE — integrate recovery into __init__
tests/
  test_recovery.py     # NEW — recovery unit tests
```

### Key Dependencies (Existing Modules)

- **`state.py`**: `ParallelState`, `StoryState`, `StoryStatus`, `load_state()`, `save_state()`, `get_parallel_state_path()` — all state operations
- **`worktree_manager.py`**: `list_worktrees()` returns `list[WorktreeInfo]` — used to detect which worktrees exist on disk; `WorktreeInfo` has `path: Path`, `branch: str | None`, `commit: str`
- **`exceptions.py`**: `ParallelError` — base exception for the parallel module
- **`config.py`**: `ParallelConfig` — needed for `worktree_base_dir` if recovery needs to compute expected worktree paths

### StoryStatus Enum Values

```python
class StoryStatus(Enum):
    BACKLOG = "backlog"
    IN_FLIGHT = "in_flight"
    MERGING = "merging"
    DONE = "done"
    BLOCKED = "blocked"
```

### Recovery Integration Point in Orchestrator

The `Orchestrator.__init__()` currently loads state at line ~204 and populates in-memory sets. Recovery should be called between loading state and populating sets:

```python
# Current flow (lines 204-237 of orchestrator.py):
existing_state = load_state(self._state_path)
if existing_state is not None:
    self._state = existing_state
    # Populate in-memory sets from persisted state...

# Target flow:
existing_state = load_state(self._state_path)
if existing_state is not None:
    self._state = recover_state(existing_state, project_root, config.worktree_base_dir)
    # Populate in-memory sets from RECOVERED state...
```

### Important Design Decision: Re-spawning Resumed Stories

Stories preserved as `in_flight` after recovery have a `worktree_path` but no running subprocess. The orchestrator's `run()` method needs to handle this — when it sees `in_flight` stories in its tracking sets, it should NOT use `get_ready_stories()` to find them (they're already in-flight). Instead, the orchestrator should detect `in_flight` stories without a corresponding `_running_tasks` entry and re-spawn them with the `--resume` flag in their existing worktree.

**Chosen approach**: Add a `resume: bool = False` parameter to `_spawn_story()`. When `resume=True`, skip `create_worktree()` and append `--resume` to CLI args. This is simpler than a separate method since the subprocess spawning and monitoring logic is identical.

### StoryState Field Reference (Canonical)

The `StoryState` model has exactly these fields — do NOT reference fields that don't exist:
```python
status: StoryStatus       # The story's lifecycle status
worktree_path: Path | None  # Path to git worktree (None when backlog)
started_at: datetime | None # When story execution started
completed_at: datetime | None # When story finished (done/blocked)
error: str | None          # Error message if blocked
```
**Note**: There is no `phase`, `log_path`, `pid`, or `branch` field on `StoryState`. The `with_story_status()` helper automatically clears `error`, `completed_at`, `worktree_path`, and `started_at` when transitioning to `BACKLOG` — manual field clearing is not needed when using this helper.

### Project Structure Notes

- Tests go in `tests/test_recovery.py` (flat test directory, no `__init__.py`)
- Test functions: `test_*` prefix, grouped in classes (`class TestRecoverState:`)
- Use `@pytest.mark.no_auto_config` only if testing config loading itself
- Mock `list_worktrees()` in tests — don't require actual git repos
- Use `MINIMAL_CONFIG_DATA` autouse fixture (default) — no need to opt out

### References

- Architecture: Crash Recovery section — defines the 5-step recovery strategy
- Architecture: State Persistence section — defines `ParallelState` model and write protocol
- Architecture: Enforcement Guidelines — all rules apply to recovery module
- PRD: FR21-FR25 — state persistence and resume requirements
- PRD: NFR1-NFR5 — reliability and data integrity requirements
- PRD: NFR3 — recovery must complete within 30 seconds

## Testing Requirements

- **Happy path: in-flight + worktree exists** — story preserved as `in_flight`, worktree path unchanged
- **Happy path: in-flight + worktree missing** — story reset to `backlog`, stale fields cleared, warning logged
- **Terminal states preserved** — `done` and `blocked` stories untouched by recovery
- **Merging + worktree missing** — reset to `backlog` (merge was incomplete)
- **Merging + worktree exists** — preserved as `merging`
- **Temp file cleanup** — `*.tmp` files in `.bmad-assist-lite/` removed during recovery
- **Temp file cleanup error handling** — `OSError` on unlink is caught and logged, not propagated
- **State persistence after recovery** — `save_state()` called with reconciled state
- **Edge case: all stories done** — recovery is a no-op, state unchanged
- **Edge case: empty stories dict** — recovery returns state as-is
- **Edge case: in-flight story with `worktree_path=None`** — treated as missing worktree, reset to backlog
- **Integration: orchestrator startup** — verify orchestrator uses recovered state for in-memory sets
- **Integration: resume re-spawn** — verify `_spawn_story(resume=True)` skips `create_worktree()` and adds `--resume` to CLI args
- **Integration: run() detects stale in-flight** — verify `run()` detects in_flight stories with no `_running_tasks` entry and re-spawns them with `resume=True`

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/recovery.py tests/test_recovery.py` | **PENDING** |
| Typecheck | `mypy src/bmad_assist_lite/parallel/recovery.py` | **PENDING** |
| Build | `python -m py_compile src/bmad_assist_lite/parallel/recovery.py` | **PENDING** |
| Tests | `pytest tests/test_recovery.py -v --tb=short` | **PENDING** |

## Senior Developer Review (AI)

**Date:** 2026-03-21
**Pre-Calculated Evidence Score:** 7.1 | **Verdict:** REJECT (in-progress)

### Applied Fixes
1. **Task 2.3 — Branch name cross-reference set**: Built `on_disk_branches: set[str]` from `WorktreeInfo.branch` values for O(1) lookup (consensus finding from both reviewers).
2. **Task 5.2 — Recovery summary temp file count**: Changed `_cleanup_temp_files()` to return `int` count; included count in recovery summary log message.
3. **`rglob` OSError handling**: Wrapped `bmad_dir.rglob("*.tmp")` in `try/except OSError` to prevent crash on permission-denied subdirectories.
4. **DRY violation**: Imported `STATE_DIR` from `state.py` instead of redefining locally.
5. **Test gap**: Added `mock_save.assert_not_called()` assertion in `test_list_worktrees_error_returns_state_unchanged`.
6. **New tests**: Added `test_returns_removed_count`, `test_returns_zero_when_no_bmad_dir`, `test_rglob_oserror_handled_gracefully`.

### Rejected/Out-of-Scope Findings
- **R2-F1 (CRITICAL)**: Task checkboxes and Dev Agent Record empty — process tracking issue, not code defect.
- **R2-F5**: Resume-failed stories leaving orphaned worktrees — edge case; worktree cleanup is Story 5.2 scope.
- **R2-F6**: Architecture violation (orphaned worktree detection) — explicitly Story 5.2 scope per epic-5.md.
- **R1-F4**: Broad `except Exception` on `list_worktrees()` — correct per design ("recovery is non-fatal"); `Exception` already excludes `SystemExit`/`KeyboardInterrupt`.
- **R2-F10**: Missing `pytest.mark.asyncio` — false positive; `asyncio_mode = "auto"` in pyproject.toml.

### Remaining Issues
- Task checkboxes in story artifact need manual updating (process, not code).
- Quality gates (lint/typecheck/build/tests) need to be run to confirm all changes pass.

## Dev Agent Record

### Agent Model Used
### Debug Log References
### Completion Notes List
### File List
