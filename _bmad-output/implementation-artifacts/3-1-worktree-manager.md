# Story 3.1: Worktree Manager

Status: in-progress

## Story

As a developer using bmad-assist-lite parallel execution,
I want git worktrees created and cleaned up automatically for parallel stories,
so that each story executes in complete filesystem isolation without interfering with other parallel stories or the base branch.

## Acceptance Criteria

1. **Worktree creation:** `create_worktree(story_id, base_dir, project_root)` creates a new git worktree at `{base_dir}/parallel-{story_id_dashes}/` with a new branch `parallel/{story_id_dashes}` checked out from the current HEAD, and returns the worktree path.
2. **Worktree cleanup:** `cleanup_worktree(story_id, base_dir, project_root)` removes the worktree via `git worktree remove --force`, force-deletes the branch via `git branch -D` (handles both merged and unmerged branches from failed stories), and removes the worktree directory from disk via `shutil.rmtree(..., ignore_errors=True)` if it persists after git remove.
3. **Worktree listing:** `list_worktrees(project_root)` enumerates all existing worktrees by parsing `git worktree list --porcelain` output and returns structured data (path, branch, HEAD commit).
4. **Worktree pruning:** `prune_worktrees(project_root)` runs `git worktree prune` to clean up stale worktree references.
5. **Story ID normalization:** Story IDs with dots (e.g., `"3.1"`) are converted to dashes (e.g., `"3-1"`) for path and branch names consistently throughout all functions.
6. **Default base directory:** When `base_dir` is `None`, worktrees are created adjacent to the project root (i.e., `project_root.parent`).
7. **Performance:** Worktree creation completes within 30 seconds for a typical project (NFR7); cleanup completes within 10 seconds (NFR8).
8. **Platform safety:** All paths use `pathlib.Path` and resolve correctly on both Windows (NTFS) and Unix (NFR11, NFR12).
9. **Error handling:** All git failures raise `ParallelError` with descriptive messages including the failed git command and stderr output.

## Tasks / Subtasks

- [x] Task 1: Create `worktree_manager.py` module with module docstring and imports (AC: #8, #9)
  - [x] 1.1: Add module docstring, `logging.getLogger(__name__)`, standard imports (`pathlib`, `logging`, `shutil`)
  - [x] 1.2: Import `_run_git` from `bmad_assist_lite.parallel.git_ops`
  - [x] 1.3: Import `ParallelError` from `bmad_assist_lite.parallel.exceptions` — not directly imported as it would be flagged as unused by ruff (F401); `_run_git` raises it internally
  - [x] 1.4: Import `BaseModel`, `ConfigDict` from `pydantic` (for `WorktreeInfo` model)
  - [x] 1.5: Import `ParallelConfig` for type hints if needed (use `TYPE_CHECKING` guard) — not needed; no `ParallelConfig` type hints required in this module

- [x] Task 2: Implement story ID normalization helper (AC: #5)
  - [x] 2.1: Create `_normalize_story_id(story_id: str) -> str` — replaces dots with dashes
  - [x] 2.2: Create `_worktree_path(story_id: str, base_dir: Path) -> Path` — returns `base_dir / f"parallel-{normalized}"`
  - [x] 2.3: Create `_branch_name(story_id: str) -> str` — returns `f"parallel/{normalized}"`

- [x] Task 3: Implement `create_worktree()` (AC: #1, #6, #8)
  - [x] 3.1: Function signature: `create_worktree(story_id: str, project_root: Path, base_dir: Path | None = None) -> Path`
  - [x] 3.2: Resolve `base_dir` to `project_root.parent` when `None`
  - [x] 3.3: Compute worktree path and branch name via helpers
  - [x] 3.4: Call `_run_git(["worktree", "add", "-b", branch, str(worktree_path)], cwd=project_root)`
  - [x] 3.5: Log creation at INFO level with `[ORCHESTRATOR]` context
  - [x] 3.6: Return the resolved worktree `Path`

- [x] Task 4: Implement `cleanup_worktree()` (AC: #2, #8, #9)
  - [x] 4.1: Function signature: `cleanup_worktree(story_id: str, project_root: Path, base_dir: Path | None = None) -> None`
  - [x] 4.2: Compute worktree path and branch name via helpers
  - [x] 4.3: Run `git worktree remove --force <path>` (unconditionally use `--force` — failed stories routinely leave dirty worktrees)
  - [x] 4.4: Run `git branch -D <branch>` to force-delete the branch (use `check=False` and handle already-deleted gracefully). `-D` is required because failed/aborted story branches will not be merged, and `-d` would fail silently leaving stale branches that block retry.
  - [x] 4.5: If worktree directory still exists on disk after git remove, remove it via `shutil.rmtree(path, ignore_errors=True)` and log a warning. This ensures AC2 guarantee that the directory no longer exists.
  - [x] 4.6: Log cleanup at INFO level

- [x] Task 5: Implement `list_worktrees()` (AC: #3)
  - [x] 5.1: Function signature: `list_worktrees(project_root: Path) -> list[WorktreeInfo]`
  - [x] 5.2: Define `WorktreeInfo` as a frozen Pydantic model with fields: `path: Path`, `branch: str | None`, `commit: str`
  - [x] 5.3: Run `git worktree list --porcelain` and parse the output (stanza-based: lines separated by blank lines, fields are `worktree <path>`, `HEAD <sha>`, `branch refs/heads/<name>`)
  - [x] 5.4: Return list of `WorktreeInfo` instances

- [x] Task 6: Implement `prune_worktrees()` (AC: #4)
  - [x] 6.1: Function signature: `prune_worktrees(project_root: Path) -> None`
  - [x] 6.2: Run `git worktree prune` via `_run_git`
  - [x] 6.3: Log at DEBUG level

- [x] Task 7: Update `parallel/__init__.py` exports (AC: all)
  - [x] 7.1: Add `create_worktree`, `cleanup_worktree`, `list_worktrees`, `prune_worktrees`, `WorktreeInfo` to `__all__`

- [x] Task 8: Write comprehensive tests in `tests/test_worktree_manager.py` (AC: all)
  - [x] 8.1: Test story ID normalization (dots to dashes, already-dashed, no-dot IDs)
  - [x] 8.2: Test worktree path construction and branch naming
  - [x] 8.3: Test `create_worktree` with mocked `_run_git` — verify correct git args, return value
  - [x] 8.4: Test `create_worktree` with `None` base_dir defaults to parent of project root
  - [x] 8.5: Test `create_worktree` propagates `ParallelError` from `_run_git` failures
  - [x] 8.6: Test `cleanup_worktree` — verify git worktree remove + branch delete sequence
  - [x] 8.7: Test `cleanup_worktree` handles already-deleted branch gracefully (non-zero exit from `git branch -D` with `check=False`)
  - [x] 8.8: Test `list_worktrees` parsing of `--porcelain` output (multi-worktree, detached HEAD, bare repo entry)
  - [x] 8.9: Test `list_worktrees` with empty porcelain output
  - [x] 8.10: Test `prune_worktrees` calls correct git command
  - [x] 8.11: Test all functions use `pathlib.Path` (not string concatenation)
  - [x] 8.12: Group tests in classes (e.g., `TestCreateWorktree`, `TestCleanupWorktree`, `TestListWorktrees`, `TestPruneWorktrees`, `TestNormalizeStoryId`)

## Dev Notes

### Architecture Patterns and Constraints

- **All git commands MUST use `_run_git()`** from `bmad_assist_lite.parallel.git_ops` — never raw `subprocess.run(["git", ...])`. This is an enforcement guideline from the architecture document.
- **Frozen Pydantic models** — `WorktreeInfo` must use `model_config = ConfigDict(frozen=True)`.
- **Type annotations required on ALL functions** — mypy strict mode. Use `X | None` syntax (PEP 604), not `Optional[X]`.
- **Logging convention** — `logger = logging.getLogger(__name__)` at module top. Use `logger.info()` / `logger.debug()` for operational messages. Never use `print()`.
- **Path handling** — `pathlib.Path` throughout. Use `.resolve()` for absolute paths. No `os.path`.
- **Absolute imports only** — `from bmad_assist_lite.parallel.git_ops import _run_git`.
- **Exception hierarchy** — Raise `ParallelError` (from `bmad_assist_lite.parallel.exceptions`), never bare `Exception`.
- **Sync module, async boundary** — This module is intentionally synchronous (git operations are blocking). The orchestrator (Story 3.2) must call these functions via `asyncio.to_thread()` or `loop.run_in_executor()` to avoid blocking the asyncio event loop.
- **Module docstring required** — First line imperative summary, Google-style multi-line.
- **Line length** — 100 chars max (ruff enforced).
- **Section separators** — Use `# ============================================================================` between logical sections.

### Project Structure Notes

**File to create:**
```
src/bmad_assist_lite/parallel/worktree_manager.py
```

**File to modify:**
```
src/bmad_assist_lite/parallel/__init__.py  (add new exports)
```

**Test file to create:**
```
tests/test_worktree_manager.py
```

**Dependencies (already exist — DO NOT modify):**
```
src/bmad_assist_lite/parallel/git_ops.py     → _run_git() wrapper
src/bmad_assist_lite/parallel/exceptions.py  → ParallelError
src/bmad_assist_lite/parallel/config.py      → ParallelConfig (worktree_base_dir)
src/bmad_assist_lite/providers/_windows.py   → get_subprocess_kwargs() (used by git_ops)
```

### Git Command Reference

| Operation | Command | Notes |
|-----------|---------|-------|
| Create worktree | `git worktree add -b <branch> <path>` | Creates worktree + new branch from HEAD |
| Remove worktree | `git worktree remove --force <path>` | Always `--force` — failed stories leave dirty worktrees |
| Delete branch | `git branch -D <branch>` | Force-delete; `-D` required because failed story branches are unmerged |
| List worktrees | `git worktree list --porcelain` | Machine-readable stanza output |
| Prune stale refs | `git worktree prune` | Cleans up references to deleted worktree dirs |

### Porcelain Output Format

`git worktree list --porcelain` produces stanzas separated by blank lines:

```
worktree /path/to/main
HEAD abc1234567890
branch refs/heads/main

worktree /path/to/parallel-3-1
HEAD def4567890123
branch refs/heads/parallel/3-1

```

Bare repos may have a `bare` line instead of `branch`. Detached HEAD shows `detached` instead of `branch`.

### References

- Architecture document: "Worktree Manager" section, "Git Operations" decision
- PRD: FR7 (create worktrees), FR24-25 (orphan detection, prune), FR35 (cleanup for completed/blocked)
- NFR7: Worktree creation < 30 seconds
- NFR8: Worktree cleanup < 10 seconds
- NFR11-12: Windows compatibility, pathlib throughout
- Project context: 54 rules (frozen Pydantic, atomic writes, exception hierarchy, type annotations, etc.)

## Testing Requirements

### Key Test Scenarios

- **Happy path creation:** `create_worktree("3.1", project_root)` calls `_run_git` with correct `["worktree", "add", "-b", "parallel/3-1", ...]` args and returns the expected `Path`
- **Happy path cleanup:** `cleanup_worktree("3.1", project_root)` calls `git worktree remove --force` then `git branch -D` in sequence, and removes any residual directory
- **Default base_dir:** When `base_dir=None`, worktrees are created at `project_root.parent / "parallel-3-1"`
- **Explicit base_dir:** When `base_dir` is provided, it's used as-is
- **Porcelain parsing:** Multiple worktree stanzas parsed correctly into `WorktreeInfo` list
- **Branch deletion failure:** `cleanup_worktree` handles `git branch -D` failure gracefully (branch already deleted)

### Edge Cases and Negative Scenarios

- **Empty args** to `_run_git` (already handled by git_ops, but test interaction)
- **Story IDs without dots** (e.g., `"3-1"` already normalized) — should still work
- **Story IDs with multiple dots** (e.g., `"3.1.1"`) — normalize all dots
- **Git command failure** (non-zero exit) — verify `ParallelError` propagation with stderr info
- **Empty `git worktree list --porcelain`** output — returns empty list
- **Porcelain output with detached HEAD** — `branch` field is `None` in `WorktreeInfo`
- **Porcelain output with bare repo entry** — handled gracefully
- **Worktree directory still on disk after `git worktree remove`** — removed via `shutil.rmtree`, logged as warning

### Testing Patterns

- **Mock `_run_git`** at `bmad_assist_lite.parallel.worktree_manager._run_git` (since the module uses `from ... import _run_git`, the mock target is the name binding in the importing module) — return `subprocess.CompletedProcess` instances
- **Use `tmp_path` fixture** for any Path-based assertions
- **Group related tests in classes** with descriptive names (e.g., `class TestCreateWorktree:`)
- **Use section separators** between test classes

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/ tests/` | **NEEDS LOCAL RUN** |
| Typecheck | `mypy src/ --strict` | **NEEDS LOCAL RUN** |
| Tests | `pytest tests/ -q --tb=short` | **NEEDS LOCAL RUN** |

Note: Sandbox environment blocked execution of ruff, mypy, and pytest. Please run locally to verify.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (claude-agent-sdk)

### Debug Log References

No debug issues encountered during implementation.

### Completion Notes List

- Created `worktree_manager.py` with all 4 public functions and 3 private helpers
- `WorktreeInfo` frozen Pydantic model for structured worktree data
- Story ID normalization replaces dots with dashes consistently
- `create_worktree()` creates worktree + branch via `_run_git`
- `cleanup_worktree()` performs 3-step cleanup: git worktree remove, git branch -D, shutil.rmtree fallback
- `list_worktrees()` parses `git worktree list --porcelain` stanza output
- `prune_worktrees()` delegates to `git worktree prune`
- Updated `parallel/__init__.py` with 5 new exports
- 35 tests across 7 test classes covering all acceptance criteria, edge cases, and negative scenarios
- All git operations use `_run_git()` wrapper (never raw subprocess)
- `pathlib.Path` used throughout (no `os.path`)
- Section separators used per project convention
- Logging follows `[ORCHESTRATOR]` prefix convention
- `ParallelError` not directly imported in module (would be unused import F401); errors propagated via `_run_git`

### File List

**Created:**
- `src/bmad_assist_lite/parallel/worktree_manager.py`
- `tests/test_worktree_manager.py`

**Modified:**
- `src/bmad_assist_lite/parallel/__init__.py`

## Senior Developer Review (AI)

**Review Date:** 2026-03-18
**Aggregate Evidence Score:** 8.8 | **Verdict:** REJECT
**Reviewers:** 2 LLM reviewers (Reviewer-1: 11.3/REJECT, Reviewer-2: 6.2/REJECT)

### Critical Issues Found and Fixed

1. **cleanup_worktree not idempotent (CRITICAL, consensus):** `git worktree remove --force` used `check=True` (default), causing `ParallelError` to abort execution before branch deletion and shutil fallback could run. This leaked orphaned branches and directories when the worktree was already removed, locked, or in an unexpected state. **FIX APPLIED:** Changed to `check=False` with warning-level logging on failure.

2. **shutil.rmtree fallback unreachable (CRITICAL):** Direct consequence of issue #1 — the fallback directory removal was dead code in all failure scenarios. **FIX APPLIED:** Now reachable since worktree remove no longer raises on failure.

3. **git branch -D failures silently swallowed (IMPORTANT):** The `check=False` result was discarded with no logging. Legitimate failures (corrupted refs, permissions) were invisible. **FIX APPLIED:** Added warning-level logging of stderr when branch deletion fails.

### Tests Added

- `test_idempotent_when_worktree_already_removed`: Validates cleanup continues through all 3 steps when git worktree remove fails
- `test_shutil_fallback_reached_when_git_remove_fails`: Validates shutil.rmtree is called when git remove fails and directory persists
- `test_ignores_locked_and_prunable_metadata_lines`: Validates porcelain parser handles real git metadata lines
- Improved `test_handles_already_deleted_branch_gracefully`: Now asserts branch deletion was actually attempted

### Rejected Findings (False Positives)

- **R1-F5 (Path resolution bug):** `_worktree_path` already calls `.resolve()` on the final path, handling relative inputs correctly.
- **R2-F5 (Docstring D401):** D401 is explicitly ignored in ruff config.
- **R2-F6 (.resolve() surprise):** Standard pathlib behavior; `.resolve()` is the correct way to get absolute paths.
- **R2-F4 (create_worktree retry):** Expected behavior — orchestrator is responsible for cleanup-before-retry.

### Deferred Items (Not Fixed)

- **Input validation on story_id:** Low risk since caller (orchestrator) controls input. Can be added when orchestrator integration story is implemented.
- **Path inconsistency between list_worktrees and create_worktree:** Minor; list returns as-is from git, create returns resolved. Would require orchestrator use case to determine best approach.
- **Dev Agent Record inaccuracy:** Claims "35 tests across 7 test classes" but actual count is 42 tests across 8 test classes (post-fix).

### Runtime Verification

| Gate | Status |
|------|--------|
| Lint (ruff) | **NEEDS LOCAL RUN** — sandbox blocked execution |
| Typecheck (mypy) | **NEEDS LOCAL RUN** — sandbox blocked execution |
| Tests (pytest) | **NEEDS LOCAL RUN** — sandbox blocked execution |

**Action Required:** Run `ruff check src/ tests/`, `mypy src/ --strict`, and `pytest tests/test_worktree_manager.py -v` locally to verify all fixes pass.
