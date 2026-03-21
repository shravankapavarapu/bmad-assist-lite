# Story 5.2: Orphan Detection & Worktree Pruning

Status: in-progress

## Story

As a developer,
I want stale worktrees detected and cleaned on startup,
so that disk space isn't wasted and git state stays clean.

## Acceptance Criteria

1. **Stale reference pruning on startup**: Given the orchestrator starts, when initialization runs, then `git worktree prune` is executed to clean stale references and `git worktree list --porcelain` is used to enumerate existing worktrees.

2. **Orphaned worktree detection (no state record)**: Given a worktree exists on disk for a `parallel/*` branch but `parallel-state.yaml` has no record of the corresponding story, when orphan detection runs, then the worktree is identified as orphaned, cleaned up via `cleanup_worktree()`, and a warning is logged.

3. **Stale done-story worktree cleanup**: Given a worktree exists on disk and `parallel-state.yaml` shows the corresponding story as `done`, when orphan detection runs, then the worktree is cleaned up (it should have been removed after merge).

4. **Cleanup performance**: Given cleanup is invoked, when a worktree is removed, then removal completes within 10 seconds per worktree (NFR8).

5. **Integration with recovery flow**: The orphan detection and pruning function is called as part of the existing `recover_state()` startup flow, after state reconciliation, so that all cleanup happens in one pass.

## Tasks / Subtasks

- [x] Task 1: Add `prune_and_clean_orphaned_worktrees()` function to `recovery.py` (AC: #1, #2, #3, #5)
  - [x] 1.1: Define function signature: `prune_and_clean_orphaned_worktrees(state: ParallelState, project_root: Path, worktrees: list[WorktreeInfo], base_dir: Path | None = None) -> int` — accepts pre-fetched worktree list (from `recover_state()`), returns number of orphans cleaned
  - [x] 1.2: Filter `worktrees` to those with `parallel/` prefixed branches (only parallel worktrees are candidates for orphan detection)
  - [x] 1.3: Add full type annotations on all functions (mypy strict)

- [x] Task 2: Implement orphan identification logic (AC: #2, #3)
  - [x] 2.1: Extract story ID from each parallel worktree branch name (reverse of `_branch_name()`: strip `parallel/` prefix, replace `-` with `.`). Validate the extracted string matches the `X.Y` numeric format (e.g., via `_STORY_NUM_RE` pattern) before proceeding — skip and log a warning for any branch that doesn't match.
  - [x] 2.2: For each parallel worktree, check if a matching story ID exists in `state.stories`
  - [x] 2.3: Identify as orphaned if: (a) no state record exists for the story ID, OR (b) state shows status `done`
  - [x] 2.4: Derive the `story_id` needed by `cleanup_worktree()` from the branch name

- [x] Task 3: Implement orphan cleanup (AC: #2, #3, #4)
  - [x] 3.1: For each identified orphan, call `cleanup_worktree(story_id, project_root, base_dir)` for actual removal
  - [x] 3.2: Wrap each `cleanup_worktree()` call in try/except to handle failures gracefully (log warning, continue to next orphan)
  - [x] 3.3: Log a warning for each orphan cleaned: `"[ORCHESTRATOR] Cleaned orphaned worktree for story {id} (reason: {no_state_record|done})"`
  - [x] 3.4: Return count of successfully cleaned orphans

- [x] Task 4: Integrate into `recover_state()` (AC: #1, #5)
  - [x] 4.1: Add `prune_worktrees(project_root)` call BEFORE the existing `list_worktrees(project_root)` call in `recover_state()`, wrapped in try/except (log warning on failure, continue — AC #1). This ensures stale git references are cleaned before any worktree enumeration.
  - [x] 4.2: Reuse the already-fetched worktree list from `recover_state()` step 2 — pass it to `prune_and_clean_orphaned_worktrees(state, project_root, worktrees, base_dir)` after the story reconciliation loop and before the final state save. Do NOT call `list_worktrees()` a second time.
  - [x] 4.3: Include orphan cleanup count in the recovery summary log message
  - [x] 4.4: Accept and pass through `base_dir` parameter (already present as `worktree_base_dir` on `recover_state()`)

- [x] Task 5: Update `__init__.py` exports (AC: #1)
  - [x] 5.1: Add `prune_and_clean_orphaned_worktrees` to `parallel/__init__.py` imports and `__all__` if needed for external access (may remain internal if only called from `recover_state()`) — **Decision: kept internal, only called from `recover_state()`**

- [x] Task 6: Write tests for orphan detection and pruning (AC: #1-#5)
  - [x] 6.1: Test `prune_worktrees()` is called before `list_worktrees()` (order matters)
  - [x] 6.2: Test worktree with `parallel/` branch and no state record → identified as orphan → `cleanup_worktree()` called
  - [x] 6.3: Test worktree with `parallel/` branch and state shows `done` → identified as orphan → `cleanup_worktree()` called
  - [x] 6.4: Test worktree with `parallel/` branch and state shows `in_flight` → NOT identified as orphan → `cleanup_worktree()` NOT called
  - [x] 6.5: Test worktree with `parallel/` branch and state shows `merging` → NOT identified as orphan → `cleanup_worktree()` NOT called
  - [x] 6.6: Test worktree with `parallel/` branch and state shows `blocked` → NOT identified as orphan → `cleanup_worktree()` NOT called
  - [x] 6.7: Test worktree with non-`parallel/` branch (e.g., `main`, `feature/foo`) → skipped entirely
  - [x] 6.8: Test `cleanup_worktree()` failure is caught and logged, does not abort remaining orphan cleanup
  - [x] 6.9: Test `prune_worktrees()` failure in `recover_state()` is caught and logged, `list_worktrees()` and orphan detection still proceed
  - [x] 6.10: Test `list_worktrees()` failure in `recover_state()` is caught and logged, orphan detection is skipped (no worktree list available), function returns unchanged state
  - [x] 6.11: Test integration: `recover_state()` now includes orphan count in summary log
  - [x] 6.12: Test empty worktree list → no orphans, returns 0
  - [x] 6.13: Test all worktrees are orphans → all cleaned, correct count returned
  - [x] 6.14: Test branch name to story ID reverse mapping (e.g., `parallel/3-4` → `3.4`)

## Dev Notes

### Architecture Patterns and Constraints

- **Frozen Pydantic models**: `ParallelState` and `StoryState` use `ConfigDict(frozen=True)`. All mutations MUST use `model_copy(update={...})` via the `with_story_status()` helper. Never assign attributes directly.
- **Atomic file writes**: State persistence uses temp file + `os.replace()` pattern (already implemented in `save_state()`). Recovery already calls `save_state()` — orphan cleanup integrates before that save.
- **`_run_git()` wrapper**: All git operations MUST use the `_run_git()` wrapper from `git_ops.py`, never raw `subprocess.run()`. The `prune_worktrees()` and `list_worktrees()` functions in `worktree_manager.py` already use this wrapper.
- **Logging convention**: `logger = logging.getLogger(__name__)`. Use `[ORCHESTRATOR]` prefix in log messages for grep-ability. Use `logger.warning()` for cleanup actions.
- **Exception handling**: Recovery is non-fatal to the orchestrator. Wrap all git/cleanup operations in try/except, log errors, and continue. Never let orphan cleanup failures crash the orchestrator.
- **Path handling**: Always use `pathlib.Path`. Use `.resolve()` for comparison to avoid symlink/relative path mismatches.
- **Import style**: Absolute imports only (`from bmad_assist_lite.parallel.worktree_manager import ...`). No relative imports.

### Source Tree Components to Touch

```
src/bmad_assist_lite/parallel/
  recovery.py          # UPDATE — add prune_and_clean_orphaned_worktrees(), integrate into recover_state()
  __init__.py          # UPDATE — add export if function is public (evaluate during implementation)
tests/
  test_recovery.py     # UPDATE — add orphan detection/pruning tests
```

### Key Dependencies (Existing Modules)

- **`recovery.py`** (Story 5.1): `recover_state()` — the function being extended. Already handles state reconciliation and temp file cleanup.
- **`worktree_manager.py`**: `prune_worktrees()` — runs `git worktree prune`; `list_worktrees()` — returns `list[WorktreeInfo]` with `path`, `branch`, `commit` fields; `cleanup_worktree(story_id, project_root, base_dir)` — three-step cleanup (git worktree remove → branch delete → shutil.rmtree fallback).
- **`state.py`**: `ParallelState`, `StoryState`, `StoryStatus` — state model. `save_state()` — atomic persistence.
- **`exceptions.py`**: `ParallelError` — base exception for the parallel module.

### StoryStatus Enum Values

```python
class StoryStatus(Enum):
    BACKLOG = "backlog"
    IN_FLIGHT = "in_flight"
    MERGING = "merging"
    DONE = "done"
    BLOCKED = "blocked"
```

### Branch Name ↔ Story ID Mapping

The `worktree_manager.py` uses these conventions:
- **Branch name**: `parallel/{normalized}` where normalized replaces `.` with `-` (e.g., story `3.4` → branch `parallel/3-4`)
- **Worktree path**: `{base_dir}/parallel-{normalized}` (e.g., `../parallel-3-4`)
- **Reverse mapping** (needed for orphan detection): strip `parallel/` prefix, replace `-` with `.` → story ID. Example: `parallel/3-4` → `3.4`

**Important**: The reverse mapping assumes story IDs use a single dot separator (e.g., `3.4`, `5.2`). The `_STORY_NUM_RE` pattern in `orchestrator.py` splits on both `.` and `-`, confirming this convention.

### StoryState Field Reference (Canonical)

```python
class StoryState(BaseModel):
    status: StoryStatus       # The story's lifecycle status
    worktree_path: Path | None  # Path to git worktree (None when backlog)
    started_at: datetime | None # When story execution started
    completed_at: datetime | None # When story finished (done/blocked)
    error: str | None          # Error message if blocked
```

### Existing `recover_state()` Flow (Story 5.1)

The current `recover_state()` in `recovery.py` executes these steps in order:
1. `_cleanup_temp_files(project_root)` — remove orphaned `*.tmp` files
2. `list_worktrees(project_root)` — get on-disk worktree list
3. Story reconciliation loop — check in_flight/merging stories against on-disk worktrees
4. `save_state(recovered_state, state_path)` — persist reconciled state
5. Log recovery summary

**Integration points** (two changes to `recover_state()`):
1. **Before step 2**: Add `prune_worktrees(project_root)` wrapped in try/except BEFORE the existing `list_worktrees()` call. This ensures stale git references are cleaned before any worktree enumeration.
2. **Between steps 3 and 4**: Call `prune_and_clean_orphaned_worktrees(state, project_root, worktrees, base_dir)` passing the worktree list already fetched in step 2. No second `list_worktrees()` call.

### Design Decision: Reuse Worktree List (AUTHORITATIVE)

The existing `recover_state()` already calls `list_worktrees()`. To avoid redundancy and ensure correct ordering:
- `prune_worktrees()` is called in `recover_state()` BEFORE the existing `list_worktrees()` call (Task 4.1), wrapped in try/except
- The already-fetched worktree list is passed to `prune_and_clean_orphaned_worktrees()` (Task 4.2) — the function does NOT call `list_worktrees()` internally
- Canonical signature: `prune_and_clean_orphaned_worktrees(state, project_root, worktrees, base_dir)` where `worktrees` is the pre-fetched list
- If `list_worktrees()` fails in `recover_state()`, the existing error handling returns unchanged state (orphan detection is skipped along with reconciliation)

### `cleanup_worktree()` Signature

```python
def cleanup_worktree(
    story_id: str,          # e.g., "3.4" — uses _normalize_story_id internally
    project_root: Path,
    base_dir: Path | None = None,  # defaults to project_root.parent
) -> None
```

This function is **idempotent** and handles partial cleanup gracefully (each step uses `check=False` on git commands). Safe to call even if the worktree is already partially removed.

### Project Structure Notes

- Tests go in `tests/test_recovery.py` (extend existing test file from Story 5.1)
- Test functions: `test_*` prefix, grouped in classes (e.g., `class TestPruneAndCleanOrphanedWorktrees:`)
- Mock `prune_worktrees()`, `list_worktrees()`, and `cleanup_worktree()` — don't require actual git repos
- Use `MINIMAL_CONFIG_DATA` autouse fixture (default) — no need to opt out

### References

- Architecture: Crash Recovery section — step 3 defines orphan cross-reference logic
- Architecture: State Persistence section — defines `ParallelState` model
- Architecture: Parallel Module Layout — `recovery.py` is the correct home for this logic
- Architecture: Enforcement Guidelines — `_run_git()` wrapper mandatory, async patterns in orchestrator
- PRD: FR24 — detect orphaned worktrees (in-flight status but no worktree on disk) and reset to backlog
- PRD: FR25 — prune stale git worktree references on startup
- PRD: NFR8 — worktree cleanup must complete within 10 seconds per worktree

## Testing Requirements

- **Happy path: orphaned worktree with no state record** — worktree with `parallel/` branch has no matching story in state → cleaned up, warning logged
- **Happy path: stale done-story worktree** — worktree exists for a story marked `done` in state → cleaned up, warning logged
- **Non-orphan preservation** — worktrees for `in_flight`, `merging`, `backlog`, and `blocked` stories are NOT cleaned up
- **Non-parallel worktree skipped** — worktrees on branches without `parallel/` prefix are ignored entirely
- **Prune before list ordering** — `prune_worktrees()` is called before `list_worktrees()` to ensure stale references are cleaned first
- **Cleanup failure resilience** — if `cleanup_worktree()` raises an exception for one orphan, remaining orphans are still processed
- **Git prune failure resilience** — if `prune_worktrees()` fails, orphan detection still proceeds using `list_worktrees()`
- **Git list failure resilience** — if `list_worktrees()` fails after prune, function returns 0 and logs error
- **Branch-to-story-ID reverse mapping** — verify `parallel/3-4` correctly maps to story ID `3.4`
- **Edge case: no worktrees on disk** — returns 0, no cleanup attempted
- **Edge case: all parallel worktrees are orphans** — all cleaned, count matches total
- **Edge case: empty state (no stories)** — all parallel worktrees are orphans by definition
- **Integration: recover_state() includes orphan count** — verify the summary log message includes orphan cleanup count

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/recovery.py tests/test_recovery.py` | **NEEDS MANUAL RUN** |
| Typecheck | `mypy src/bmad_assist_lite/parallel/recovery.py` | **NEEDS MANUAL RUN** |
| Build | `python -m py_compile src/bmad_assist_lite/parallel/recovery.py` | **NEEDS MANUAL RUN** |
| Tests | `pytest tests/test_recovery.py -v --tb=short` | **NEEDS MANUAL RUN** |

> **Note:** Sandbox environment blocked Python execution. Quality gates must be run manually before marking as passed.

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (claude-sonnet-4-20250514)

### Debug Log References
No debug issues encountered during implementation.

### Completion Notes List
- Implemented `prune_and_clean_orphaned_worktrees()` in `recovery.py` with full type annotations
- Added `_STORY_ID_RE` regex pattern for validating reverse-mapped story IDs from branch names
- Integrated `prune_worktrees()` call before `list_worktrees()` in `recover_state()` with try/except
- Integrated orphan detection after reconciliation loop, before `save_state()`, passing reconciled state
- Recovery summary log now includes orphan cleanup count
- `worktree_base_dir` parameter properly passed through from `recover_state()` to orphan cleanup
- `prune_and_clean_orphaned_worktrees` kept internal (not exported in `__init__.py`) as it's only called from `recover_state()`
- All existing Story 5.1 tests updated to mock `prune_worktrees` and `cleanup_worktree` (newly imported in `recovery.py`)
- 20 new test functions added covering all 14 task 6 subtests plus additional edge cases
- Tests verify: orphan detection for no-state-record and done statuses, non-orphan preservation for all active statuses, non-parallel branch skipping, cleanup failure resilience, prune/list ordering, git prune failure resilience, list failure resilience, branch-to-story-ID mapping, empty/all-orphan edge cases, reconciled state usage, base_dir pass-through, non-standard branch name validation

### File List
- `src/bmad_assist_lite/parallel/recovery.py` — MODIFIED (added `prune_and_clean_orphaned_worktrees()`, `_STORY_ID_RE`, integrated into `recover_state()`)
- `tests/test_recovery.py` — MODIFIED (updated existing tests for new mocks, added 5 new test classes with 20 test functions)

## Senior Developer Review (AI)

**Date:** 2026-03-21
**Synthesis Verdict:** MAJOR REWORK (Score: 5.2)
**Reviewers:** 2 (Reviewer-1: 6.3/REJECT, Reviewer-2: 4.2/MAJOR REWORK)

### Applied Fixes
1. **Observability on early return** (IMPORTANT): Added `temp_files_cleaned` count to the `list_worktrees()` failure log message so cleanup actions are not silently lost.
2. **Removed dead code** (MINOR): Removed unused `on_disk_branches` set comprehension with `noqa: F841` suppression from `recover_state()`.
3. **Grammar in log messages** (MINOR): Summary log now uses singular/plural correctly (e.g., "1 story" vs "2 stories", "1 worktree" vs "0 worktrees").
4. **Test: success-path log assertion** (MINOR): Added assertion in `test_cleanup_failure_continues_to_next_orphan` to verify the success log for story 3.2.
5. **Removed duplicate test class** (MINOR): Consolidated `TestRecoverStateListWorktreesErrorOrphan` into existing `TestRecoverStateListWorktreesError` (same behavior tested).

### Unresolved CRITICAL Finding
- **NFR8 Timeout (AC #4)**: No 10-second timeout enforcement exists on `cleanup_worktree()` calls. The `_run_git()` wrapper in `git_ops.py` uses `subprocess.run()` without a `timeout` parameter. This is a cross-story infrastructure gap — `_run_git()` and `cleanup_worktree()` are defined in `worktree_manager.py`/`git_ops.py` (earlier stories). Story 5.2's code correctly calls `cleanup_worktree()` but cannot enforce the timeout without modifying shared infrastructure. **Recommend: add `timeout` parameter to `_run_git()` and use it in `cleanup_worktree()`.**

### Rejected Findings
- **Path discrepancy** (R1): `cleanup_worktree()` recalculating path from story_id is by design — it's the canonical cleanup function using the same normalization. Custom/moved worktrees are out of scope.
- **Multi-segment story ID regex** (R1+R2): `_STORY_ID_RE` strictly matching `X.Y` is intentional per Task 2.1. Current codebase only uses two-segment IDs. Forward-compatibility concern accepted as tech debt.
- **Missing `_` prefix** (R2): Not worth renaming — would require updating all test imports for no functional benefit.
- **Cross-epic worktree scenario** (R2): Correct behavior (cleaned as orphan) but extremely unlikely edge case. Not a bug.

### Runtime Verification
Sandbox blocked Python execution. **Quality gates must be run manually:**
```
ruff check src/bmad_assist_lite/parallel/recovery.py tests/test_recovery.py
mypy src/bmad_assist_lite/parallel/recovery.py
pytest tests/test_recovery.py -v --tb=short
```
