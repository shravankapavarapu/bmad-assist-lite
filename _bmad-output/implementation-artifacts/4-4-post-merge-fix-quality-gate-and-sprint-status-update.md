# Story 4.4: Post-Merge Fix Quality Gate & Sprint Status Update

Status: in-progress

## Story

As a developer,
I want integration failures auto-fixed on the base branch and sprint-status updated after successful merge,
so that dependent stories get clean code and sprint tracking reflects reality.

## Acceptance Criteria

1. **Given** the post-merge QG failed for a story, **when** `run_post_merge_fix()` is invoked on the base branch, **then** the master LLM receives the failure report (from `.bmad-assist-lite/cache/post-merge-qg-failures-{story_id}.md`) and the `fix-quality-gate` workflow template is used to attempt the fix.

2. **Given** `fix_quality_gate` makes changes, **when** the fix is committed, **then** the commit message is tagged: `fix: post-merge integration fix for story {story_id}`.

3. **Given** `fix_quality_gate` completes, **when** the quality gate is re-run, **then** if all gates pass the story transitions to `done`; if gates still fail the story transitions to `blocked` with detailed error info stored in `StoryState.error`.

4. **Given** `post_merge_fix_retries` is configured (default 1), **when** fix attempts are made, **then** at most `post_merge_fix_retries` fix attempts are made before marking the story as `blocked`.

5. **Given** a story successfully transitions to `done` (merge + QG pass, with or without fix), **when** sprint-status is updated, **then** the story is written as `done` in `sprint-status.yaml` on the base branch using the existing `SprintStatus` model and `save_sprint_status()` function.

## Tasks / Subtasks

- [x] Task 1: Implement `run_post_merge_fix()` function in `merger.py` (AC: #1, #2, #3, #4)
  - [x] 1.1 Create `run_post_merge_fix(story_id: str, project_root: Path, config: Config | None = None, attempt: int = 1) -> PostMergeQGResult` function in `merger.py`
  - [x] 1.2 Read the failure report from `.bmad-assist-lite/cache/post-merge-qg-failures-{story_id}.md`
  - [x] 1.3 Resolve the `fix-quality-gate` workflow instructions path using `importlib.resources.files("bmad_assist_lite.workflows") / "fix-quality-gate" / "instructions.xml"` and read its content. Invoke Claude CLI (`claude --print`) with the failure report and the workflow instructions as context. Use the same `subprocess.Popen` + `get_subprocess_kwargs()` + `kill_process()` pattern established in `resolve_conflicts()` for process management
  - [x] 1.4 Build the fix prompt by combining: the `fix-quality-gate` workflow instructions, retry context if attempt > 1 (following the pattern from `FixQualityGateHandler.execute()`), and the failure report content wrapped in `<qa-failure-report>` tags
  - [x] 1.5 After Claude CLI completes, check for changes via `_run_git(["diff", "--stat"], ...)`. If no changes detected (empty output), log a warning and treat as a failed fix attempt (skip commit, return `all_passed=False`). Otherwise, commit changes with message: `fix: post-merge integration fix for story {story_id}` using `_run_git(["add", "-A"], ...)` then `_run_git(["commit", "-m", msg], ...)`
  - [x] 1.6 Re-run quality gate via `run_post_merge_qg()` to verify the fix
  - [x] 1.7 If fix attempt fails (Claude CLI error, commit fails, etc.), return the latest `PostMergeQGResult` with `all_passed=False` to signal the caller to retry or block
  - [x] 1.8 Log all operations using `[FIX-QG|post-merge|{story_id}]` tag prefix

- [x] Task 2: Implement `process_merge_with_fix()` orchestration method in `merger.py` (AC: #1, #3, #4)
  - [x] 2.1 Create `async process_merge_with_fix(self) -> MergeResult | None` method on `MergeQueue` that wraps the full merge+QG+fix pipeline
  - [x] 2.2 Call existing `process_next()` to perform merge + post-merge QG
  - [x] 2.3 If `MergeResult.qg_result.all_passed` is `True`, return the result immediately (success path)
  - [x] 2.4 If `MergeResult.qg_result.all_passed` is `False`, enter fix loop: call `run_post_merge_fix()` via `asyncio.to_thread()` (since it is sync) up to `post_merge_fix_retries` times (sourced from `ParallelConfig`). If `post_merge_fix_retries` is 0, skip the fix loop entirely and return immediately (story should transition to blocked)
  - [x] 2.5 Accept `parallel_config: ParallelConfig | None = None` in `MergeQueue.__init__()` for accessing `post_merge_fix_retries`
  - [x] 2.6 After each fix attempt, check if `PostMergeQGResult.all_passed` — if `True`, break and return success; if `False` and retries remain, re-invoke fix
  - [x] 2.7 After all retries exhausted, return the final `MergeResult` with the last `qg_result` (still `all_passed=False`) so the orchestrator can transition to blocked
  - [x] 2.8 If `MergeResult.success` is `False` (merge itself failed), skip fix entirely and return
  - [x] 2.9 On success (QG passes, with or without fix), call `update_sprint_status_done(story_id, project_root)` before returning the result. This call is non-fatal — catch and log any exceptions as warnings (matching Task 3.7 pattern)

- [x] Task 3: Implement `update_sprint_status_done()` helper function (AC: #5)
  - [x] 3.1 Create `update_sprint_status_done(story_id: str, project_root: Path) -> None` function in `merger.py`
  - [x] 3.2 Use `get_sprint_status_path(project_root)` from `core/sprint_status` to resolve the path
  - [x] 3.3 Call `load_sprint_status(path)` to load the current sprint status
  - [x] 3.4 Call `sprint_status.set_story_status(story_id, "done")` on the loaded model
  - [x] 3.5 Call `save_sprint_status(sprint_status, path)` to persist atomically
  - [x] 3.6 Log with `[SPRINT|{story_id}]` tag prefix
  - [x] 3.7 Wrap in try/except: sprint-status update failures are non-fatal (log as warning, do not raise) — matching the project rule that sprint sync is one-way and non-fatal

- [x] Task 4: Export new public functions from `parallel/__init__.py` (AC: all)
  - [x] 4.1 Add `run_post_merge_fix` to imports and `__all__` in `parallel/__init__.py`
  - [x] 4.2 Add `update_sprint_status_done` to imports and `__all__` in `parallel/__init__.py`

- [x] Task 5: Write unit tests in `tests/test_merger_post_merge_fix.py` (AC: #1-#5)
  - [x] 5.1 Test `run_post_merge_fix()` reads the failure report from the correct cache path
  - [x] 5.2 Test `run_post_merge_fix()` invokes Claude CLI with the failure report in the prompt
  - [x] 5.3 Test `run_post_merge_fix()` commits with the correct tagged message format
  - [x] 5.4 Test `run_post_merge_fix()` re-runs QG after fix and returns the new result
  - [x] 5.5 Test `run_post_merge_fix()` handles missing failure report gracefully (logs warning, still attempts fix)
  - [x] 5.6 Test `run_post_merge_fix()` handles Claude CLI timeout (returns `all_passed=False`)
  - [x] 5.7 Test `run_post_merge_fix()` handles Claude CLI failure (non-zero exit)
  - [x] 5.8 Test `process_merge_with_fix()` returns immediately on QG pass (no fix needed)
  - [x] 5.9 Test `process_merge_with_fix()` invokes fix when QG fails, then re-runs QG
  - [x] 5.10 Test `process_merge_with_fix()` respects `post_merge_fix_retries` limit (default 1)
  - [x] 5.11 Test `process_merge_with_fix()` returns blocked-ready result when all retries exhausted
  - [x] 5.12 Test `process_merge_with_fix()` skips fix when merge itself fails
  - [x] 5.13 Test `update_sprint_status_done()` loads, updates, and saves sprint-status correctly
  - [x] 5.14 Test `update_sprint_status_done()` handles missing sprint-status file gracefully
  - [x] 5.15 Test `update_sprint_status_done()` catches and logs errors without raising (non-fatal)
  - [x] 5.16 Test retry context is included in fix prompt when attempt > 1 (following `FixQualityGateHandler` pattern)
  - [x] 5.17 Test `process_merge_with_fix()` with `post_merge_fix_retries=0` skips fix loop entirely and returns blocked-ready result
  - [x] 5.18 Test `run_post_merge_fix()` handles Claude CLI producing no changes (empty diff) — treated as failed attempt, no commit created

## Dev Notes

### Architecture Patterns & Constraints

- **Pydantic models**: Parallel module models use `model_config = ConfigDict(frozen=True)` with mutations via `model_copy(update={...})`. **Exception**: `SprintStatus` (in `core/sprint_status.py`) uses `ConfigDict(extra="allow")` and mutates in-place via `set_story_status()` — do NOT use `model_copy()` with it.
- **Claude CLI invocation**: Use `subprocess.Popen` with `get_subprocess_kwargs()` from `providers/_windows.py` for platform safety. Use `kill_process()` for timeout cleanup. Pattern established in `resolve_conflicts()` (lines 325-351 of `merger.py`).
- **Atomic file writes**: Sprint-status uses temp file + `os.replace()` pattern via `save_sprint_status()`. The function handles this internally.
- **Sprint sync is non-fatal**: Sprint-status update failures MUST be logged as warnings, never raised as exceptions. This is a critical project rule.
- **Logging tags**: Use `[FIX-QG|post-merge|{story_id}]` for fix operations, `[SPRINT|{story_id}]` for sprint status updates.
- **Process management**: Follow Architecture Enforcement Guideline #5 — include process cleanup in `finally` blocks, never leave orphaned subprocesses.
- **Async wrapping**: The orchestrator is async; `run_post_merge_fix()` is sync. Wrap calls with `asyncio.to_thread()` from the `MergeQueue` async methods.
- **No direct state writes**: `merger.py` does NOT write to `parallel-state.yaml`. State transitions (done, blocked) remain the orchestrator's responsibility. The merger returns results; the orchestrator acts on them.

### Integration Points

- **`run_post_merge_qg()`** (merger.py lines 755-834): Already exists from Story 4.3. Re-used after each fix attempt to verify the fix.
- **`_write_post_merge_failure_report()`** (merger.py lines 714-752): Already writes failure reports. The fix function reads these reports.
- **`MergeQueue.process_next()`** (merger.py lines 876-924): Already runs merge + QG. `process_merge_with_fix()` wraps this to add the fix retry loop.
- **`fix-quality-gate` workflow** (`workflows/fix-quality-gate/instructions.xml`): Existing 5-step workflow template. The fix function sends its instructions as context to Claude CLI.
- **`SprintStatus` model** (core/sprint_status.py): `set_story_status()` handles both simple and rich entry formats. `_find_key()` resolves dot notation (4.4) and dash notation (4-4) to the actual key in sprint-status.yaml.
- **`ParallelConfig.post_merge_fix_retries`** (parallel/config.py line 31-35): Already exists, default 1, ge=0.

### Commit Message Format

Fix commits MUST use this exact format:
```
fix: post-merge integration fix for story {story_id}
```
Where `{story_id}` is the dot-notation story ID (e.g., `3.3`).

### Retry Context Pattern

When attempt > 1, prepend retry context to the prompt following the pattern from `FixQualityGateHandler.execute()` (fix_quality_gate.py lines 51-61):
```
<retry-context>
This is fix attempt #{attempt}. A previous fix attempt did not fully resolve the
quality gate failures. The failure report below shows the CURRENT errors after
the previous fix. Do not repeat the same approach -- analyze what the previous
attempt likely tried and choose a different strategy. Read the failing files
carefully before making changes.
</retry-context>
```

### Project Structure Notes

Files to create/modify:
- **Modify**: `src/bmad_assist_lite/parallel/merger.py` — add `run_post_merge_fix()`, `update_sprint_status_done()`, extend `MergeQueue`
- **Modify**: `src/bmad_assist_lite/parallel/__init__.py` — export new functions
- **Create**: `tests/test_merger_post_merge_fix.py` — unit tests

### References

- `src/bmad_assist_lite/parallel/merger.py` — merge queue and post-merge QG (Story 4.1, 4.2, 4.3)
- `src/bmad_assist_lite/parallel/config.py` — `ParallelConfig.post_merge_fix_retries`
- `src/bmad_assist_lite/core/sprint_status.py` — `SprintStatus`, `load_sprint_status()`, `save_sprint_status()`, `get_sprint_status_path()`
- `src/bmad_assist_lite/loop/handlers/fix_quality_gate.py` — retry context pattern
- `src/bmad_assist_lite/workflows/fix-quality-gate/instructions.xml` — fix workflow template
- `src/bmad_assist_lite/providers/_windows.py` — `get_subprocess_kwargs()`, `kill_process()`
- `src/bmad_assist_lite/parallel/git_ops.py` — `_run_git()` wrapper
- `src/bmad_assist_lite/parallel/orchestrator.py` — consumer of merge results, state transitions

## Testing Requirements

- **Fix invocation**: Verify Claude CLI is invoked with the correct prompt (failure report + workflow instructions + retry context)
- **Commit tagging**: Verify commit messages follow the exact `fix: post-merge integration fix for story {id}` format
- **Retry logic**: Verify retry count respects `post_merge_fix_retries` from config (0 = no fix attempts, 1 = one attempt, etc.)
- **QG re-run**: Verify quality gate is re-run after each fix attempt
- **Sprint-status update**: Verify `set_story_status()` is called with the correct story ID and "done" status
- **Non-fatal sprint sync**: Verify sprint-status errors are caught and logged, not raised
- **Edge cases**: Missing failure report, Claude CLI not found (FileNotFoundError), empty QG commands, process timeout, `retries=0` (no fix attempts), Claude CLI produces no changes (empty diff)
- **Mock boundaries**: Mock `subprocess.Popen` for Claude CLI, mock `run_command` for QG, mock `_run_git` for git operations, mock `load_sprint_status`/`save_sprint_status` for sprint-status
- **Process cleanup**: Verify `kill_process()` is called on timeout, no orphaned processes

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/ tests/` | **PENDING** |
| Typecheck | `mypy src/` | **PENDING** |
| Tests | `pytest tests/ -v --tb=short -m "not slow"` | **PENDING** |

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (claude-opus-4-20250514)

### Debug Log References
N/A - No debug issues encountered.

### Completion Notes List
- Implemented `run_post_merge_fix()` with Claude CLI invocation, failure report reading, workflow instruction loading via `importlib.resources`, retry context for attempt > 1, empty diff detection, and tagged commit messages
- Implemented `process_merge_with_fix()` as async orchestration method on `MergeQueue` with fix retry loop, `asyncio.to_thread()` wrapping, `post_merge_fix_retries` from `ParallelConfig`, and sprint-status update on success
- Implemented `update_sprint_status_done()` with non-fatal error handling, using existing `SprintStatus` model and atomic persistence functions
- Extended `MergeQueue.__init__()` to accept `parallel_config: ParallelConfig | None`
- Exported `run_post_merge_fix` and `update_sprint_status_done` from `parallel/__init__.py`
- Wrote 22 unit tests covering all acceptance criteria, edge cases (empty diff, retries=0, timeout, missing report, CLI not found), and non-fatal sprint-status behavior
- Note: Quality gates (lint, typecheck, tests) require manual execution due to sandbox restrictions. Run: `ruff check src/`, `mypy src/`, `pytest tests/test_merger_post_merge_fix.py -v --tb=short`

### File List
- **Modified**: `src/bmad_assist_lite/parallel/merger.py` - Added `run_post_merge_fix()`, `update_sprint_status_done()`, `MergeQueue.process_merge_with_fix()`, updated `MergeQueue.__init__()` signature
- **Modified**: `src/bmad_assist_lite/parallel/__init__.py` - Added exports for `run_post_merge_fix` and `update_sprint_status_done`
- **Created**: `tests/test_merger_post_merge_fix.py` - 22 unit tests for all new functionality
- **Modified**: `_bmad-output/implementation-artifacts/4-4-post-merge-fix-quality-gate-and-sprint-status-update.md` - Story status and task checkboxes updated

## Senior Developer Review (AI)

**Date:** 2026-03-19
**Evidence Score:** 5.8 (Pre-calculated)
**Verdict:** MAJOR REWORK
**Status:** in-progress (fixes applied, runtime verification pending)

### Fixes Applied

1. **`git diff --stat` replaced with `git status --porcelain`** (IMPORTANT) — `git diff --stat` missed untracked/new files created by Claude CLI. `git status --porcelain` detects all working tree changes including new files.

2. **Claude CLI timeout made configurable** (IMPORTANT) — Added `timeout: int = 300` parameter to `run_post_merge_fix()` signature, replacing the hardcoded 300s. Consistent with `resolve_conflicts()` pattern.

3. **Git commit failure now returns result instead of raising** (MINOR) — Wrapped `_run_git(["add", ...])` and `_run_git(["commit", ...])` in `try/except ParallelError` to return `PostMergeQGResult(all_passed=False)` per Task 1.7, allowing the fix loop to continue retrying.

4. **Added test for workflow instructions loading** — New test verifies `importlib.resources` loads content into the prompt before `<qa-failure-report>` tags.

5. **Added test for git commit failure** — New test verifies `ParallelError` from commit returns `all_passed=False` instead of propagating.

### Findings Rejected

- **Finding 1** (update_sprint_status_done not try/except wrapped at caller): FALSE POSITIVE. The function's own `except Exception` block guarantees non-fatal behavior. Redundant wrapping adds noise.
- **Finding 5** (retry context template deviation): FALSE POSITIVE. Template matches story spec; formatting differences irrelevant for LLM prompts.
- **Finding 6** (test mock fragility): FALSE POSITIVE. Reviewer self-corrected; lazy import + `@patch` on source module works correctly.
- **Finding 7** (fix loop outside lock): Design trade-off. Running fix inside the lock would block the queue for 300s+ per retry. The orchestrator's sequential call pattern is the correct architectural control.

### Runtime Verification

Sandbox restrictions prevented automated execution. Manual verification required:
- `ruff check src/ tests/`
- `mypy src/`
- `pytest tests/test_merger_post_merge_fix.py -v --tb=short`
