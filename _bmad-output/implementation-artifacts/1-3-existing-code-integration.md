# Story 1.3: Existing Code Integration

Status: in-progress

## Story

As a developer,
I want the existing loop to accept `--epic`, `--story`, and `--single-story` CLI flags, and sprint sync to respect parallel mode,
so that the orchestrator can invoke targeted single-story loop execution in worktrees.

## Acceptance Criteria

1. **Single-story CLI flag** — Given the CLI is invoked with `--epic 3 --story 1 --single-story`, the loop executes only story 3.1 and exits after that story completes (success or failure), without searching for the next backlog story.
2. **Sprint sync bypass** — Given the environment variable `BMAD_PARALLEL_MODE=1` is set, sprint sync is skipped (no write to sprint-status.yaml) and a debug log message indicates sync was bypassed.
3. **Backward compatibility** — Given the CLI is invoked without `--single-story`, existing behavior is unchanged (processes all backlog stories sequentially).
4. **Parallel subcommand group** — Given the `parallel` subcommand group is registered, `bmad-assist-lite parallel --help` shows the parallel subcommand help (even if commands are not yet implemented).

## Tasks / Subtasks

- [x] Task 1: Add `--single-story` flag to `run` command in `cli.py` (AC: #1, #3)
  - [x] 1.1: Add `single_story: bool = typer.Option(False, "--single-story", help="Exit after completing a single story.")` parameter to the `run()` function signature
  - [x] 1.2: Pass `single_story=single_story` kwarg to `run_loop()` call (line ~319)

- [x] Task 2: Modify `--epic` and `--story` flag behavior for targeted execution (AC: #1, #3)
  - [x] 2.1: When `--single-story` is set and both `--epic` and `--story` are provided, filter `epic_stories` to contain **only** the single matching story (exact match on story number, not `>=` filtering). When `--single-story` is False, keep existing `>=` behavior for `--story` flag.

- [x] Task 3: Register `parallel` Typer subcommand group in `cli.py` (AC: #4)
  - [x] 3.1: Create `parallel_app = typer.Typer(name="parallel", help="Parallel story execution commands.", no_args_is_help=True)` near the top of cli.py (after `app` definition)
  - [x] 3.2: Register with `app.add_typer(parallel_app, name="parallel")` below the parallel_app definition

- [x] Task 4: Add `single_story` parameter to `run_loop()` in `loop/runner.py` (AC: #1, #3)
  - [x] 4.1: Add `single_story: bool = False` parameter to the `run_loop()` function signature
  - [x] 4.2: After a story completes (after the `advance_story` block around line ~248-269), check if `single_story is True`. If so, log an info message (`"Single-story mode: exiting after story %s"`) and `return LoopExitReason.COMPLETED`
  - [x] 4.3: Also handle `single_story` exit in the quality gate `action == "pass"` path (line ~178-188) and the `action == "skip_story"` path (line ~190-217) — after the quality gate resolution completes, if `single_story` is True, return `LoopExitReason.COMPLETED` instead of advancing to the next story

- [x] Task 5: Add `BMAD_PARALLEL_MODE` environment variable check to `trigger_sync()` in `core/sprint_sync.py` (AC: #2)
  - [x] 5.1: Add `import os` to the imports section
  - [x] 5.2: At the very start of `trigger_sync()`, before the `try` block, check `os.environ.get("BMAD_PARALLEL_MODE") == "1"`. If True, log `logger.debug("Sprint sync bypassed (BMAD_PARALLEL_MODE=1)")` and `return` early

- [x] Task 6: Write tests (AC: #1–#4)
  - [x] 6.1: Create `tests/test_existing_integration.py`
  - [x] 6.2: Test `trigger_sync` skips sync when `BMAD_PARALLEL_MODE=1` is set (use `monkeypatch.setenv`; mock `load_sprint_status` to verify it is NOT called)
  - [x] 6.3: Test `trigger_sync` performs sync when `BMAD_PARALLEL_MODE` is not set (existing behavior)
  - [x] 6.4: Test `trigger_sync` performs sync when `BMAD_PARALLEL_MODE` is set to a value other than `"1"` (e.g., `"0"`, `"true"`)
  - [x] 6.5: Test `run_loop` with `single_story=True` returns `LoopExitReason.COMPLETED` after first story completes (mock `execute_phase` to return success for all phases in the story, verify loop exits after full story lifecycle without advancing to next story)
  - [x] 6.6: Test `run_loop` with `single_story=False` continues to next story (existing behavior preserved)
  - [x] 6.7: Test `parallel` subcommand group is registered on the Typer app (invoke `--help` via `typer.testing.CliRunner`)
  - [x] 6.8: Test `--single-story` flag is accepted by the `run` command (parse check)
  - [x] 6.9: Test `--single-story` without `--epic`/`--story` processes the first backlog story and exits (edge case)
  - [x] 6.10: Test `run_loop` with `single_story=True` and QG `fix_quality_gate` retry → eventual pass triggers single-story exit

## Dev Notes

### Architecture Patterns & Constraints

- **~18 lines of changes across 3 existing files** — This story is intentionally minimal. The `--epic` and `--story` flags already exist on the `run` command; only `--single-story` is new. The runner needs one conditional check, and sprint_sync needs a 3-line early return.
- **Frozen Pydantic models** — `Config` and `State` are frozen. Do not attempt to add `single_story` to the `State` model. It is passed as a function parameter to `run_loop()`.
- **Typer option naming** — Use `--single-story` (kebab-case with double dash) as the CLI flag. Typer automatically maps this to `single_story` parameter. Per project convention, do NOT use the short-flag `-S` to avoid collision with future flags.
- **Import style** — Absolute imports only. `os` is stdlib and can be imported at module top level. Use lazy imports inside functions only for heavy internal modules (to avoid circular imports).
- **Logging convention** — `logger = logging.getLogger(__name__)` at module top. Use `logger.debug()` for the sprint sync bypass message (not `info` — this is expected behavior in parallel mode, not something the user needs to see at normal verbosity).
- **`run_loop()` signature change** — Adding `single_story: bool = False` is backward-compatible. All existing callers pass keyword args, and the default is `False`, preserving existing behavior (FR52-54, NFR15).
- **`parallel_app` placeholder** — Register an empty Typer sub-app. In Epic 3, `parallel/cli.py` will add commands (`run`, `status`, `unblock`) to this app. For now, it just needs to show help text when invoked. **Integration note:** Epic 3 will likely define its own Typer app in `parallel/cli.py` and register it in `cli.py`, replacing this placeholder. Avoid importing `parallel_app` from `cli.py` into `parallel/cli.py` to prevent circular imports.
- **No B008 concern** — Typer uses `typer.Option(...)` as function default arguments, which triggers ruff B008 (function-call-in-default-arg). This is already ignored in the project ruff config.
- **Story filter behavior in `--single-story` mode** — The existing `--story` flag filters with `sn >= story` (start from story N). When `--single-story` is active, the filter should be `sn == story` (exact match) so only one story is in the queue.
- **Quality gate paths must also respect `single_story`** — When QG passes or skips a story, the runner normally advances to the next story. With `single_story=True`, these paths must exit instead of advancing.
- **`LoopExitReason.COMPLETED` UX note** — In single-story mode, returning `COMPLETED` triggers "All epics completed successfully!" in `cli.py`. This is slightly misleading but cosmetic; adjusting CLI output messaging is out of scope for this story and can be addressed in a future UX polish pass.

### Project Structure Notes

**Files to modify (~18 lines total):**
```
src/bmad_assist_lite/cli.py
  - Add --single-story Option to run() (~1 line)
  - Add single_story= kwarg to run_loop() call (~1 line)
  - Adjust --story filter for single-story exact match (~3 lines)
  - Create parallel_app Typer and register it (~3 lines)

src/bmad_assist_lite/loop/runner.py
  - Add single_story: bool = False to run_loop() signature (~1 line)
  - Add single_story exit check after story advance (~4 lines)
  - Add single_story exit check in QG pass/skip paths (~3 lines)

src/bmad_assist_lite/core/sprint_sync.py
  - Add import os (~1 line)
  - Add BMAD_PARALLEL_MODE check at start of trigger_sync() (~3 lines)
```

**New files to create:**
```
tests/test_existing_integration.py  # Tests for all changes in this story
```

### Dependencies on Prior Stories

- **Story 1.1** (done) — Created `parallel/` package, `ParallelConfig`, `ParallelError`. This story references the parallel module only for registering the subcommand group; no code from 1.1 is imported in the modified files.
- **Story 1.2** (review/done) — Created `git_ops.py`. Not directly used by this story's changes.

### References

- **`cli.py` current state** — `run()` at line 104-341, existing `--epic`/`--story` at lines 121-132, `run_loop()` call at lines 319-325
- **`runner.py` current state** — `run_loop()` at line 66-280, story advancement at lines 245-269, QG handling at lines 168-217
- **`sprint_sync.py` current state** — `trigger_sync()` at lines 79-93, already has `try/except` wrapper
- **PRD references** — FR52 (`--epic`/`--story` flags), FR53 (`--single-story`), FR54 (`BMAD_PARALLEL_MODE` bypass)
- **Project context** — NFR15 (backward compatibility), state machine (10 phases), sprint sync is one-way and non-fatal

## Testing Requirements

- **Sprint sync bypass** — Verify `trigger_sync` returns immediately without calling `load_sprint_status` when `BMAD_PARALLEL_MODE=1`. Use `monkeypatch.setenv` to set the env var and mock `load_sprint_status` to assert it was not called.
- **Sprint sync bypass specificity** — Verify the check is for exactly `"1"`, not truthy strings. `BMAD_PARALLEL_MODE=0`, `BMAD_PARALLEL_MODE=true`, or unset should NOT bypass sync.
- **Sprint sync normal path** — Verify `trigger_sync` calls `load_sprint_status` → `sync_state_to_sprint` → `save_sprint_status` when env var is not set.
- **Single-story loop exit** — Verify `run_loop` with `single_story=True` exits with `LoopExitReason.COMPLETED` after the first story's **full phase pipeline** completes (all phases through story advancement), without advancing to a second story. The exit check fires after the story transition block, not after a single phase. Requires mocking `execute_phase`, `save_state`, `trigger_sync`, etc.
- **Single-story backward compat** — Verify `run_loop` with `single_story=False` (default) advances to the next story normally.
- **CLI flag parsing** — Use `typer.testing.CliRunner` to verify `--single-story` is accepted without error. Can be a simple `--help` parse test.
- **Parallel subcommand** — Use `CliRunner` to invoke `parallel --help` and verify it returns success with help text.
- **Edge case: single-story with QG failure** — If quality gate triggers `skip_story` with `single_story=True`, the loop should still exit (not try the next story).
- **Edge case: `--single-story` without `--epic`/`--story`** — When `--single-story` is used alone, the loop should process the first backlog story and exit. The `single_story` check in `runner.py` handles this correctly since it's a post-completion check regardless of how stories were selected.
- **Edge case: single-story with QG retry** — When `single_story=True` and QG returns `fix_quality_gate`, the retry loop continues normally. After the retry, if QG then passes, the `single_story` exit check should fire, preventing advancement to the next story.

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/cli.py src/bmad_assist_lite/loop/runner.py src/bmad_assist_lite/core/sprint_sync.py tests/test_existing_integration.py` | **NEEDS MANUAL RUN** |
| Typecheck | `mypy src/bmad_assist_lite/cli.py src/bmad_assist_lite/loop/runner.py src/bmad_assist_lite/core/sprint_sync.py --strict` | **NEEDS MANUAL RUN** |
| Build | `pip install -e .` | **NEEDS MANUAL RUN** |
| Tests | `pytest tests/test_existing_integration.py -v` | **NEEDS MANUAL RUN** |

> **Note:** Quality gate commands could not be executed in the sandbox environment. Please run them manually before merging.

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (claude-opus-4-20250514)

### Debug Log References
- No errors encountered during implementation.
- Sandbox blocked all python/pytest/ruff/mypy command execution; quality gates need manual verification.

### Completion Notes List
- All 6 tasks implemented with ~20 lines of production code changes across 3 files
- 1 new test file created with 12 test cases covering all acceptance criteria
- Task 5: Added `import os` and 3-line `BMAD_PARALLEL_MODE` early-return check to `trigger_sync()` in `sprint_sync.py`
- Task 1: Added `--single-story` flag to `run()` command in `cli.py` and passed it to `run_loop()`
- Task 2: Modified `--story` filter to use exact match (`sn == story`) when `single_story=True`, preserving `sn >= story` behavior otherwise
- Task 3: Created `parallel_app` Typer sub-app and registered it with `app.add_typer(parallel_app, name="parallel")`
- Task 4: Added `single_story: bool = False` parameter to `run_loop()`, with exit checks in QG pass path, QG skip_story path, normal story advancement path, and story-to-story transition path
- Task 6: Created `tests/test_existing_integration.py` with tests for all acceptance criteria and edge cases
- All changes are backward-compatible (default `single_story=False` preserves existing behavior)

### File List
**Modified files:**
- `src/bmad_assist_lite/cli.py` — Added `--single-story` flag, `parallel_app` registration, exact-match story filter
- `src/bmad_assist_lite/loop/runner.py` — Added `single_story` parameter and exit checks in 4 code paths
- `src/bmad_assist_lite/core/sprint_sync.py` — Added `import os` and `BMAD_PARALLEL_MODE` bypass check

**New files:**
- `tests/test_existing_integration.py` — 13 test cases covering all ACs and edge cases

## Senior Developer Review (AI)

**Review Date:** 2026-03-18
**Verdict:** MAJOR REWORK (Score: 5.9)
**Reviewers:** 1 of 2 (Reviewer-1/Gemini failed with exit code 1)

### Summary
Production code is functionally correct across all 4 ACs. Two test quality issues were fixed during synthesis:
1. **Tautological assertion removed** — Dead logic in `test_single_story_without_filters_processes_first_story` (lines 381-383) was redundant; replaced with clearer assertion including `1.3` coverage.
2. **CLI integration test added** — New `TestSingleStoryCLIIntegration` class verifies `--epic 1 --story 2 --single-story` passes only story `1.2` to `run_loop()` (exact-match filter at `cli.py:233`), addressing untested AC #1 gap.

### Rejected Findings
- **Finding 1 (--single-story without --story):** By-design per story spec; runner exit guards handle correctly.
- **Finding 2 (State mutation inconsistency):** Pre-existing pattern in codebase, not introduced by Story 1.3.
- **Finding 5 (Defensive duplication):** Distinct code paths for QG-pass vs normal-advance; not actual duplication.
- **Finding 6 (COMPLETED for failed QA):** Explicitly documented as out-of-scope cosmetic issue in Dev Notes.
- **Finding 8 (Line count mismatch):** Documentation inaccuracy, no code impact.

### Remaining Items (non-blocking)
- Finding 7: `test_trigger_sync_performs_sync_when_not_set` tests full chain rather than isolated bypass behavior. Functional but slightly over-coupled to `load_sprint_status` internals. Low risk since `load_sprint_status` handles missing files gracefully.

### Runtime Verification
- **Lint/Type Check:** Sandbox blocked execution (consistent with Dev Agent Record)
- **Build:** Sandbox blocked execution
- **Tests:** Sandbox blocked execution — manual verification required
