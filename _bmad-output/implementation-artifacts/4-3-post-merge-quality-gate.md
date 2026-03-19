# Story 4.3: Post-Merge Quality Gate

Status: in-progress

## Story

As a developer,
I want a full project quality gate run on the base branch after each merge,
so that integration issues between parallel stories are caught immediately.

## Acceptance Criteria

1. **Given** a story has been successfully merged to the base branch, **when** the post-merge quality gate runs, **then** lint, typecheck, build, and test commands execute on the base branch, **and** results are captured with pass/fail per gate.

2. **Given** all 4 gates pass, **when** the quality gate completes, **then** `PostMergeQGResult` is returned with `all_passed=True`, **and** the caller (orchestrator / Story 4.4) can transition the story status to `done` and proceed to the next merge in the queue. **Note:** This story does NOT write to `parallel-state.yaml` — state transitions are the orchestrator's responsibility (see module docstring).

3. **Given** one or more gates fail, **when** the quality gate completes, **then** the failure details are captured (which gates failed, specific error output), **and** a `PostMergeQGResult` with `all_passed=False` is returned so the caller (Story 4.4) can invoke fix_quality_gate.

4. **Given** the quality gate commands are sourced, **when** commands are determined, **then** they follow the existing priority order: config `quality_gate` section (from `Config.quality_gate`) -> auto-detected toolchain (via `detect_toolchain()`). **Note:** Story file table is NOT used for post-merge QG since this runs at project level, not per-story.

## Tasks / Subtasks

- [x] Task 1: Create `PostMergeQGResult` frozen Pydantic model (AC: #1, #3)
  - [x] 1.1 Add `PostMergeQGResult` to `merger.py` with fields: `all_passed: bool`, `story_id: str`, `gate_results: list[GateResult]`, `duration_ms: int`
  - [x] 1.2 Create `GateResult` frozen Pydantic model with fields: `name: str`, `command: str`, `passed: bool`, `exit_code: int`, `stdout: str`, `stderr: str`, `duration_ms: int`
  - [x] 1.3 Ensure both models use `model_config = ConfigDict(frozen=True)`
  - [x] 1.4 Export `PostMergeQGResult` and `GateResult` from `parallel/__init__.py`

- [x] Task 2: Implement `_resolve_qg_commands()` helper function (AC: #4)
  - [x] 2.1 Create function `_resolve_qg_commands(project_root: Path, config: Config | None = None) -> list[QualityGateEntry]` in `merger.py`
  - [x] 2.2 Priority 1: Check `config.quality_gate` — build entries from `lint`, `typecheck`, `build`, and test fields. **For test command:** use `test` (full suite), NOT `test_unit`, since post-merge QG runs at the project level for integration validation. If `test` is not set, fall back to `test_unit`. This intentionally differs from the per-story QG handler which prefers `test_unit` over `test`.
  - [x] 2.3 Priority 2: Call `detect_toolchain(project_root)` — build entries from detected commands
  - [x] 2.4 Return empty list if no commands found (caller handles this as all-pass)
  - [x] 2.5 Import `QualityGateEntry` from `core/quality_gates`, `Config` from `core/config`, `detect_toolchain` from `core/toolchain`

- [x] Task 3: Implement `run_post_merge_qg()` function (AC: #1, #2, #3)
  - [x] 3.1 Create function `run_post_merge_qg(story_id: str, project_root: Path, config: Config | None = None, command_timeout: int = 120) -> PostMergeQGResult` in `merger.py`
  - [x] 3.2 Call `_resolve_qg_commands()` to get the command list
  - [x] 3.3 If no commands found, return `PostMergeQGResult(all_passed=True, ...)` with empty `gate_results` (pass-by-default, matching existing QG handler behavior)
  - [x] 3.4 Resolve `command_timeout` from `config.quality_gate.command_timeout` if config is provided, falling back to the parameter default (120s)
  - [x] 3.5 Iterate over each command entry and call `run_command(entry.command, project_root, timeout=command_timeout)` from `core/command_runner`
  - [x] 3.6 Build `GateResult` from each `CommandResult` (map `success` -> `passed`, capture stdout/stderr/exit_code/duration_ms)
  - [x] 3.7 Log each gate result using `[QG|post-merge|{story_id}]` prefix with pass/fail icon
  - [x] 3.8 Return `PostMergeQGResult` with `all_passed` set to `True` only if every gate passed. Calculate `duration_ms` as the sum of individual `GateResult.duration_ms` values (total wall time across all gates)
  - [x] 3.9 Import `run_command` from `core/command_runner` — do NOT use `_run_git()` for QG commands (they are shell commands, not git commands)

- [x] Task 4: Integrate post-merge QG into `MergeQueue.process_next()` (AC: #2, #3)
  - [x] 4.1 After successful `merge_story()` in `process_next()`, call `run_post_merge_qg()` via `asyncio.to_thread()` (since `run_command` is synchronous)
  - [x] 4.2 Return a tuple or composite result so the orchestrator knows both merge outcome and QG outcome — add `qg_result: PostMergeQGResult | None` field to `MergeResult` (default `None`)
  - [x] 4.3 On merge failure, skip QG and return `MergeResult` with `qg_result=None`
  - [x] 4.4 On merge success + QG all pass, return `MergeResult(success=True, qg_result=<pass>)`
  - [x] 4.5 On merge success + QG failure, return `MergeResult(success=True, qg_result=<fail>)` — the orchestrator (Story 4.4) will handle the fix_quality_gate invocation
  - [x] 4.6 Accept `config: Config | None = None` parameter in `MergeQueue.__init__()` for passing through to `run_post_merge_qg()`

- [x] Task 5: Write failure report for post-merge QG failures (AC: #3)
  - [x] 5.1 Create `_write_post_merge_failure_report(story_id: str, project_root: Path, qg_result: PostMergeQGResult) -> Path` helper
  - [x] 5.2 Ensure cache directory exists via `cache_dir.mkdir(parents=True, exist_ok=True)` (matching existing `_write_failure_report` pattern in `quality_gate.py` line 107), then write report to `.bmad-assist-lite/cache/post-merge-qg-failures-{story_id}.md` with per-gate details (command, exit code, stdout/stderr)
  - [x] 5.3 Use `clean_test_output()` from `core/command_runner` for test output sanitization
  - [x] 5.4 Call from `run_post_merge_qg()` when any gate fails, before returning the result
  - [x] 5.5 Log the report path at INFO level with `[QG|post-merge|{story_id}]` prefix

- [x] Task 6: Write unit tests in `tests/test_merger_post_merge_qg.py` (AC: #1-#4)
  - [x] 6.1 Test `PostMergeQGResult` and `GateResult` model creation (frozen, field types, defaults)
  - [x] 6.2 Test `_resolve_qg_commands()` with config quality_gate section — verify entries built from config fields
  - [x] 6.3 Test `_resolve_qg_commands()` with no config — verify fallback to `detect_toolchain()`
  - [x] 6.4 Test `_resolve_qg_commands()` with no config and no toolchain detected — verify empty list
  - [x] 6.5 Test `run_post_merge_qg()` all gates pass — mock `run_command` to return success, verify `all_passed=True`
  - [x] 6.6 Test `run_post_merge_qg()` some gates fail — mock `run_command` to return failures, verify `all_passed=False` with correct gate details
  - [x] 6.7 Test `run_post_merge_qg()` no commands found — verify pass-by-default behavior
  - [x] 6.8 Test `run_post_merge_qg()` uses `command_timeout` from config when available
  - [x] 6.9 Test `MergeQueue.process_next()` runs post-merge QG after successful merge (mock `merge_story` + `run_post_merge_qg`)
  - [x] 6.10 Test `MergeQueue.process_next()` skips QG on merge failure
  - [x] 6.11 Test failure report is written when gates fail (verify file contents and path)
  - [x] 6.12 Test log output uses `[QG|post-merge|{story_id}]` prefix convention

## Dev Notes

### Architecture Patterns & Constraints

- **Frozen Pydantic models**: `PostMergeQGResult` and `GateResult` MUST use `model_config = ConfigDict(frozen=True)` — all models in this project are frozen
- **Reuse existing command_runner.py**: Use `run_command()` from `core/command_runner.py` for executing QG shell commands. Do NOT use `_run_git()` — quality gate commands are shell commands (lint, typecheck, build, test), not git operations
- **Reuse existing quality_gates.py**: Use `QualityGateEntry` dataclass from `core/quality_gates.py` for command representation
- **Reuse existing toolchain.py**: Use `detect_toolchain()` from `core/toolchain.py` for auto-detection fallback
- **Command sourcing priority (post-merge)**: Config `quality_gate` section -> auto-detected toolchain. The story file Quality Gates table is NOT used for post-merge QG because this runs at the project level, not for a specific story file
- **Log prefix**: Use `[QG|post-merge|{story_id}]` for all post-merge QG log messages per architecture doc convention table
- **Async bridging**: `run_command()` is synchronous — bridge via `asyncio.to_thread()` when calling from `MergeQueue.process_next()` (async context)
- **Exception hierarchy**: Raise `ParallelError` for fatal failures. Normal gate failures are NOT exceptions — they are captured in the return model
- **Type annotations**: All functions require full type hints including return types (mypy strict)
- **Union syntax**: Use `X | None`, not `Optional[X]`
- **Atomic file writes are NOT needed here**: The failure report is informational, not state — direct `Path.write_text()` is acceptable (matches existing `_write_failure_report` in `quality_gate.py`)
- **command_timeout**: Source from `config.quality_gate.command_timeout` when config is available, default to 120s otherwise

### Project Structure Notes

**Files to create:**
- `tests/test_merger_post_merge_qg.py` — New test file for post-merge QG tests

**Files to modify:**
- `src/bmad_assist_lite/parallel/merger.py` — Add `PostMergeQGResult`, `GateResult`, `_resolve_qg_commands()`, `run_post_merge_qg()`, `_write_post_merge_failure_report()`. Update `MergeResult` to include `qg_result` field. Update `MergeQueue.process_next()` to call QG after merge
- `src/bmad_assist_lite/parallel/__init__.py` — Export `PostMergeQGResult`, `GateResult`, `run_post_merge_qg`

**Files to reference (read-only patterns):**
- `src/bmad_assist_lite/loop/handlers/quality_gate.py` — Pattern for `_get_commands()` (config -> toolchain fallback) and `_write_failure_report()` (report format)
- `src/bmad_assist_lite/core/command_runner.py` — `run_command()`, `CommandResult`, `clean_test_output()`
- `src/bmad_assist_lite/core/quality_gates.py` — `QualityGateEntry` dataclass
- `src/bmad_assist_lite/core/toolchain.py` — `detect_toolchain()`, `ToolchainCommands`
- `src/bmad_assist_lite/core/config.py` — `Config`, `QualityGateConfig` (for `command_timeout`, `lint`, `typecheck`, `build`, `test`, `test_unit`)

**Existing test patterns (from `tests/test_merger.py`):**
- Mock `_run_git` at `bmad_assist_lite.parallel.merger._run_git`
- Use `subprocess.CompletedProcess` for mock returns
- Async tests use plain `async def test_*()` (asyncio_mode = "auto")
- Group tests in classes: `class TestPostMergeQGResult:`, `class TestResolveQGCommands:`, etc.

### References

- Architecture doc: "Post-merge QG" prefix convention — `[QG|post-merge|{story}]`
- Architecture doc: FR14-FR20 mapping to `merger.py`
- PRD: FR17 (post-merge QG), FR18 (fix_quality_gate on failure), FR46 (detailed failure logging)
- Project context: Atomic file writes, frozen Pydantic, `_run_git()` for git only, `run_command()` for shell

## Testing Requirements

- **All gates pass**: Verify `PostMergeQGResult.all_passed=True` and `MergeResult.qg_result` reflects success
- **Some gates fail**: Verify per-gate failure details captured (name, command, exit_code, stdout, stderr), `all_passed=False`, and failure report written
- **No commands found**: Verify pass-by-default behavior (empty gate_results, all_passed=True)
- **Config priority**: Verify config `quality_gate` section takes priority over auto-detected toolchain
- **Timeout handling**: Verify `command_timeout` sourced from config when available, fallback to 120s
- **Integration with MergeQueue**: Verify `process_next()` calls QG after successful merge and skips QG on merge failure
- **Edge case — command not found**: Verify `run_command()` returns exit_code 127, captured as FAIL gate result
- **Edge case — command timeout**: Verify `run_command()` returns exit_code 124, captured as FAIL gate result
- **Failure report content**: Verify report file is created at correct path with per-gate details

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/` | **PENDING** |
| Typecheck | `mypy src/` | **PENDING** |
| Build | `echo "no build step"` | **PENDING** |
| Tests | `pytest -q --tb=short --no-header` | **PENDING** |

## Senior Developer Review (AI)

**Verdict:** MAJOR REWORK (Aggregate Score: 4.0)
**Date:** 2026-03-19

### Applied Fixes
1. **Exception safety for failure report writes** — Wrapped `_write_post_merge_failure_report()` call in `run_post_merge_qg()` with `try/except OSError` so a disk/permission error writing the informational report does not crash the pipeline and lose the computed QG result.
2. **Exception safety for QG in process_next()** — Wrapped the `run_post_merge_qg()` call in `MergeQueue.process_next()` with `try/except Exception` so that any unexpected infrastructure error from the QG subsystem does not swallow the successful merge result. On failure, the merge result is returned with `qg_result=None`.
3. **Added 2 new tests** — `test_failure_report_write_error_is_nonfatal` and `test_qg_exception_preserves_merge_result` covering the new error-handling paths.

### Acknowledged but Not Fixed (Low Priority / By Design)
- DRY violation in `_resolve_qg_commands()` config vs toolchain paths — consistent with existing `quality_gate.py` pattern; refactoring deferred.
- `command_timeout` parameter always overridden by config — documented behavior per story spec.
- Fragile coupling of 120s default between function param and `QualityGateConfig` — low risk.
- Story claims "35 tests" but file has 37 — minor doc inaccuracy in Dev Agent Record.

### Rejected Findings (False Positives)
- `_resolve_qg_commands` dropping `test_unit` when `test` is set — intentional per AC#4 and story spec.
- `MergeResult.success` docstring inadequacy — the `qg_result` field docstring adequately explains the state nuance.
- `process_next()` not passing `resolve` to `merge_story()` — out of scope for Story 4.3.
- `__all__` in `__init__.py` — pre-existing pattern, not introduced by this story.
- Missing test for `run_command()` raising exceptions — `run_command()` already handles `FileNotFoundError` and `TimeoutExpired` internally.

### Runtime Verification
- Lint: **BLOCKED** (sandbox restrictions)
- Typecheck: **BLOCKED** (sandbox restrictions)
- Build: **BLOCKED** (sandbox restrictions)
- Tests: **BLOCKED** (sandbox restrictions)

*Note: All verification commands were blocked by the sandbox execution environment. Code changes have been verified by manual inspection for correctness.*

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (claude-opus-4-20250514)

### Debug Log References
- Sandbox execution environment blocked all Python/pytest/ruff/mypy commands requiring manual quality gate validation

### Completion Notes List
- Implemented `GateResult` and `PostMergeQGResult` frozen Pydantic models in `merger.py`
- Added `qg_result: PostMergeQGResult | None` field to existing `MergeResult` model
- Implemented `_resolve_qg_commands()` with config->toolchain priority; post-merge QG prefers `test` over `test_unit` (differs from per-story QG which prefers `test_unit`)
- Implemented `run_post_merge_qg()` — runs all gates, captures per-gate results, calculates total duration
- Implemented `_write_post_merge_failure_report()` — writes Markdown failure reports to `.bmad-assist-lite/cache/`
- Integrated post-merge QG into `MergeQueue.process_next()` via `asyncio.to_thread()` after successful merge
- Added `config: Config | None = None` parameter to `MergeQueue.__init__()` for config passthrough
- Used `model_copy(update={...})` pattern for frozen model updates (not mutation)
- All log messages use `[QG|post-merge|{story_id}]` prefix per architecture convention
- Updated 3 existing tests in `test_merger.py` to also mock `run_post_merge_qg` (since `process_next()` now calls it after successful merge)
- Created 35 new tests covering: model validation, command resolution, QG execution, failure reporting, MergeQueue integration, edge cases (command not found, timeout), log prefix verification
- Exported `GateResult`, `PostMergeQGResult`, `run_post_merge_qg` from `parallel/__init__.py`

### File List
**Created:**
- `tests/test_merger_post_merge_qg.py` — 35 unit tests for post-merge QG functionality

**Modified:**
- `src/bmad_assist_lite/parallel/merger.py` — Added `GateResult`, `PostMergeQGResult` models; `qg_result` field on `MergeResult`; `_resolve_qg_commands()`, `_write_post_merge_failure_report()`, `run_post_merge_qg()` functions; updated `MergeQueue.__init__()` to accept `config`; updated `process_next()` to run post-merge QG
- `src/bmad_assist_lite/parallel/__init__.py` — Added exports for `GateResult`, `PostMergeQGResult`, `run_post_merge_qg`
- `tests/test_merger.py` — Updated 3 existing tests to mock `run_post_merge_qg` for compatibility with new `process_next()` behavior
