# Story 9.1: Bootstrap Module & Config

Status: in-progress

## Story

As a developer using parallel mode on a project that requires `.env` files and dependency installation,
I want worktrees to be automatically bootstrapped with the necessary files and commands,
so that the LLM loop runs in a functional environment without manual setup per worktree.

## Acceptance Criteria

1. **Copy files to worktree** — Given `copy_to_worktree: [".env", "local.settings.json"]` is configured, when `copy_files_to_worktree()` is called with a valid worktree path, then each listed file is copied from project root to the same relative path in the worktree.

2. **Missing file with strict=False** — Given `copy_to_worktree: [".env"]` and `.env` does not exist in project root, when `copy_files_to_worktree()` is called with `strict=False`, then a warning is logged and bootstrap continues (no error).

3. **Missing file with strict=True** — Given `copy_to_worktree: [".env"]` and `.env` does not exist in project root, when `copy_files_to_worktree()` is called with `strict=True`, then `BootstrapResult.success` is `False` with `failed_phase="copy"` and a descriptive error message.

4. **Setup commands run sequentially** — Given `setup_commands: ["pip install -e .", "npm ci"]` is configured, when `run_setup_commands()` is called, then commands run sequentially in the worktree cwd; if one fails (non-zero exit), remaining commands are skipped and the result reports the failure.

5. **Validation command** — Given `validation_command: "pytest -q -x"` is configured, when `run_validation_command()` is called, then the command runs in worktree cwd; non-zero exit returns `BootstrapResult.success=False` with `failed_phase="validation"`.

6. **Timeout enforcement** — Given a setup command or validation command hangs beyond `bootstrap_timeout`, when the timeout fires, then the process tree is killed and `BootstrapResult` reports a timeout failure with any output captured before the timeout.

7. **No-op when unconfigured** — Given no bootstrap fields are configured (all defaults), when `bootstrap_worktree()` is called, then it returns `BootstrapResult(success=True)` immediately with no subprocess spawning.

8. **Directory copy** — Given `copy_to_worktree: ["config/"]` is configured and `config/` exists as a directory in project root, when `copy_files_to_worktree()` is called, then the entire directory tree is copied recursively to the same relative path in the worktree using `shutil.copytree(dirs_exist_ok=True)`. Directory detection must use `pathlib.Path.is_dir()` on the resolved source path (not rely solely on trailing `/`).

## Tasks / Subtasks

- [ ] Task 1: Add bootstrap config fields to `ParallelConfig` (AC: #1-#7)
  - [ ] 1.1: Add 5 new fields to `ParallelConfig` in `config.py`: `copy_to_worktree: list[str]`, `copy_strict: bool`, `setup_commands: list[str]`, `validation_command: str | None`, `bootstrap_timeout: int`
  - [ ] 1.2: Use `Field(default=120, ge=1)` constraint for `bootstrap_timeout` (consistent with existing `ParallelConfig` patterns using `Field()` for simple bounds)

- [ ] Task 2: Create `BootstrapResult` model (AC: #1-#7)
  - [ ] 2.1: Define frozen Pydantic model in `bootstrap.py` with `success: bool`, `failed_phase: Literal["copy", "setup", "validation"] | None`, `error_message: str | None`, `output: str`

- [ ] Task 3: Implement `copy_files_to_worktree()` (AC: #1, #2, #3, #8)
  - [ ] 3.1: Iterate `files` list, resolve each entry against `project_root`, and use `pathlib.Path.is_dir()` on the resolved source path to determine if entry is a directory or file (trailing `/` may be used as a hint but must not be the sole detection mechanism)
  - [ ] 3.2: Verify source exists before creating destination directories; create parent directories in worktree with `Path.mkdir(parents=True, exist_ok=True)` just-in-time after source verification succeeds (avoids empty directories when `strict=False` and source is missing)
  - [ ] 3.3: Use `shutil.copytree(dirs_exist_ok=True)` for directories, `shutil.copy2()` for files
  - [ ] 3.4: Handle missing source: warn and continue when `strict=False`, return failure `BootstrapResult` when `strict=True`
  - [ ] 3.5: Return `BootstrapResult` with collected output

- [ ] Task 4: Implement `run_setup_commands()` (AC: #4, #6)
  - [ ] 4.1: Run each command via `subprocess.Popen()` with `cwd=str(worktree_path)`, `stdout=PIPE`, `stderr=PIPE`, `text=True`, `encoding="utf-8"`, `shell=True`, plus `**get_subprocess_kwargs()`. Use `process.communicate(timeout=timeout)` and handle `subprocess.TimeoutExpired` manually to ensure proper process tree cleanup on Windows (via `taskkill /T /F /PID` from `providers/_windows.py` utilities) and Unix (`os.killpg`). This prevents orphaned child processes when `shell=True` is used.
  - [ ] 4.2: On non-zero exit, skip remaining commands and return failure result with captured stderr/stdout
  - [ ] 4.3: On `subprocess.TimeoutExpired`, extract partial output from `e.stdout`/`e.stderr` (captured before timeout), kill entire process tree, and return failure result with timeout error message plus any captured partial output appended to `BootstrapResult.output`

- [ ] Task 5: Implement `run_validation_command()` (AC: #5, #6)
  - [ ] 5.1: Run single command via `subprocess.Popen()` with same kwargs pattern and process tree cleanup as setup commands (Task 4.1)
  - [ ] 5.2: Return success/failure result based on exit code

- [ ] Task 6: Implement `bootstrap_worktree()` orchestrator function (AC: #7)
  - [ ] 6.1: Accept `project_root`, `worktree_path`, `config` (ParallelConfig), and `validate: bool` parameters
  - [ ] 6.2: Short-circuit with `BootstrapResult(success=True)` when no bootstrap fields configured
  - [ ] 6.3: Execute phases in order: copy -> setup -> validation (if `validate=True`)
  - [ ] 6.4: Return immediately on any phase failure with the phase's result

- [ ] Task 7: Update `parallel/__init__.py` exports (AC: #1-#7)
  - [ ] 7.1: Add `BootstrapResult` and `bootstrap_worktree` to imports and `__all__`. Sub-functions (`copy_files_to_worktree`, `run_setup_commands`, `run_validation_command`) are module-internal and should NOT be exported — they are only accessed via `bootstrap_worktree()`

- [ ] Task 8: Write tests for all components (AC: #1-#8)
  - [ ] 8.1: Test `ParallelConfig` new field defaults and validation
  - [ ] 8.2: Test `copy_files_to_worktree()` — happy path, missing file strict/non-strict, directory copy with trailing `/`, directory copy without trailing `/` (both must work via `Path.is_dir()`), partial success return value in non-strict mode
  - [ ] 8.3: Test `run_setup_commands()` — success, first-command-fail skips rest, timeout
  - [ ] 8.4: Test `run_validation_command()` — pass and fail
  - [ ] 8.5: Test `bootstrap_worktree()` — no-op, full pipeline, validate=False skips validation, phase failure short-circuits

## Dev Notes

### Architecture Patterns & Constraints

- **Frozen Pydantic models**: `BootstrapResult` must use `model_config = ConfigDict(frozen=True)`. `ParallelConfig` already has this.
- **Subprocess pattern**: Use `subprocess.Popen()` with `cwd=str(worktree_path)`, `stdout=PIPE`, `stderr=PIPE`, `text=True`, `encoding="utf-8"`, `shell=True`, plus `**get_subprocess_kwargs()` from `bmad_assist_lite.providers._windows`. Use `process.communicate(timeout=timeout)` for timeout enforcement. On `TimeoutExpired`, kill the entire process tree (not just the shell) using platform-appropriate cleanup (`taskkill /T /F /PID` on Windows, `os.killpg` on Unix). Extract partial `stdout`/`stderr` from the exception before cleanup. This prevents orphaned child processes when `shell=True` is used.
- **Logging**: `logger = logging.getLogger(__name__)` at module top. All log messages use `[BOOTSTRAP]` prefix per architecture doc.
- **Path handling**: `pathlib.Path` only, never `os.path`. Use `.resolve()` for absolute paths where needed.
- **Exception hierarchy**: Use `ParallelError` for bootstrap failures that should propagate. However, `BootstrapResult` is the primary error communication mechanism (not exceptions).
- **Type annotations**: Full type hints on all functions including return types (mypy strict mode).
- **Union syntax**: Use `str | None`, not `Optional[str]`.
- **Imports**: Absolute imports only (`from bmad_assist_lite.parallel.config import ParallelConfig`).
- **Line length**: 100 characters max (ruff).

### Project Structure Notes

Files to create:
- `src/bmad_assist_lite/parallel/bootstrap.py` — new module with `BootstrapResult`, `copy_files_to_worktree()`, `run_setup_commands()`, `run_validation_command()`, `bootstrap_worktree()`

Files to modify:
- `src/bmad_assist_lite/parallel/config.py` — add 5 new fields to `ParallelConfig`
- `src/bmad_assist_lite/parallel/__init__.py` — add exports for `BootstrapResult`, `bootstrap_worktree`

Test file to create:
- `tests/test_bootstrap.py` — comprehensive tests

### Key Implementation Details

- **Directory detection**: Resolve each entry against `project_root` and use `pathlib.Path.is_dir()` on the resolved source path for robust detection. A trailing `/` may serve as a user hint but must not be the sole mechanism. Directories → `shutil.copytree(dirs_exist_ok=True)`. Files → `shutil.copy2()`.
- **`get_subprocess_kwargs()`**: Returns `{"creationflags": CREATE_NO_WINDOW}` on Windows, `{"start_new_session": True}` on Unix. Import from `bmad_assist_lite.providers._windows`.
- **No-op detection**: `bootstrap_worktree()` should check if all three phases would be no-ops (empty `copy_to_worktree`, empty `setup_commands`, `validation_command is None`) and return success immediately.
- **`validate` parameter**: When `False`, skip the validation command phase entirely (used for non-canary worktrees in Story 9.2).
- **Output accumulation**: Each phase appends its stdout/stderr to the `output` field of `BootstrapResult` so the canary diagnostic output is complete.
- **Partial success in non-strict copy**: When `strict=False` and some files are missing, `copy_files_to_worktree()` returns `BootstrapResult(success=True)` with warnings in `output`. Only `strict=True` returns `success=False` on missing files.
- **Subprocess `shell` parameter**: For setup/validation commands that may contain pipes or shell features (e.g., `pip install -e .`), use `shell=True` on the string command via `Popen`. This is consistent with how the user specifies commands as strings, not arg lists. Note: `shell=True` spawns a child shell, so timeout cleanup must kill the entire process tree (see Subprocess pattern above).

### References

- Architecture: "Worktree Bootstrap" section — three-phase pipeline, canary pattern, `BootstrapResult` model, configuration fields
- Architecture: "Implementation Patterns & Consistency Rules" — frozen models, subprocess kwargs, exception hierarchy
- Architecture: "Worktree Loop Spawning" — subprocess `cwd` pattern, `get_subprocess_kwargs()`
- Project Context: Testing rules (autouse fixtures, MINIMAL_CONFIG_DATA, markers)
- Existing code: `git_ops.py` — reference for `get_subprocess_kwargs()` import pattern
- Existing code: `config.py` — reference for `ParallelConfig` field patterns with `Field()`

## Testing Requirements

- **Config field defaults**: Verify all 5 new fields have correct defaults (`[]`, `False`, `[]`, `None`, `120`)
- **Config field validation**: Verify `bootstrap_timeout` enforces minimum value
- **File copy — happy path**: Create temp files, call `copy_files_to_worktree()`, verify files exist at destination
- **File copy — directory with trailing `/`**: Create temp directory tree, configure as `"config/"`, verify recursive copy with `dirs_exist_ok=True`
- **File copy — directory without trailing `/`**: Create temp directory tree, configure as `"config"`, verify `Path.is_dir()` detects it and copies recursively
- **File copy — missing file, strict=False**: Verify warning logged, success result returned
- **File copy — missing file, strict=True**: Verify failure result with `failed_phase="copy"`
- **Setup commands — success**: Mock `subprocess.Popen` returning 0, verify all commands executed in order
- **Setup commands — mid-sequence failure**: Mock second command returning non-zero, verify third command never called
- **Setup commands — timeout**: Mock `subprocess.TimeoutExpired`, verify timeout error in result with partial output preserved and process tree killed
- **Validation command — pass**: Mock returning 0 exit, verify success result
- **Validation command — fail**: Mock returning non-zero exit, verify failure with `failed_phase="validation"`
- **Validation command — timeout**: Mock `subprocess.TimeoutExpired`, verify timeout error in result with partial output preserved
- **`bootstrap_worktree()` — no-op**: Call with default config, verify immediate success with no subprocess calls
- **`bootstrap_worktree()` — full pipeline**: Verify copy -> setup -> validation execution order
- **`bootstrap_worktree()` — validate=False**: Verify validation phase skipped
- **`bootstrap_worktree()` — early exit on copy failure**: Verify setup/validation not called after copy fails

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/bootstrap.py src/bmad_assist_lite/parallel/config.py` | **PENDING** |
| Typecheck | `mypy src/bmad_assist_lite/parallel/bootstrap.py src/bmad_assist_lite/parallel/config.py` | **PENDING** |
| Tests | `pytest tests/test_bootstrap.py -v` | **PENDING** |

## Senior Developer Review (AI)

**Date:** 2026-03-23
**Verdict:** REJECT (Score: 7.2) — CRITICAL fixes applied, runtime verification pending
**Reviewers:** 2 independent adversarial reviewers

### Applied Fixes (8 total)

1. **CRITICAL — TimeoutExpired output capture**: `Popen.communicate(timeout=X)` does not populate `TimeoutExpired.stdout/stderr`. Changed to kill-then-drain pattern: `_kill_process_tree(process)` followed by `process.communicate(timeout=5)` to capture remaining output. Tests updated to match.
2. **CRITICAL — Path traversal security**: Added `.resolve()` + containment validation for both source (within `project_root`) and destination (within `worktree_path`). Rejects empty, `.`, and `../` entries. Handles strict/non-strict modes.
3. **IMPORTANT — shutil exception handling**: Wrapped `shutil.copy2()` and `shutil.copytree()` in `try/except OSError`, returning `BootstrapResult(success=False)` in strict mode, warning in non-strict. Aligns with Dev Notes: "BootstrapResult is the primary error communication mechanism."
4. **IMPORTANT — Process tree kill fallback**: `_kill_process_tree()` fallback now uses `os.killpg(pgid, SIGKILL)` on Unix and `terminate_process()` on Windows instead of `process.kill()` which only kills the shell.
5. **IMPORTANT — Parent dir creation for copytree**: Added `destination.parent.mkdir(parents=True, exist_ok=True)` before `shutil.copytree()` for nested directory entries.
6. **MINOR — assert → proper guard**: Replaced `assert config.validation_command is not None` with `if` condition in `bootstrap_worktree()`.
7. **MINOR — Windows test path portability**: Changed test assertions from hardcoded `cwd="/my/worktree"` to `cwd=str(Path("/my/worktree"))`.
8. **MINOR — _kill_process_tree pid None**: Added early return when `process.pid is None`.

### New Tests Added

- `TestCopyFilesPathSecurity`: 4 tests for path traversal protection (strict/non-strict, empty, dot)
- `TestCopyFilesOSError`: 3 tests for shutil exception handling (strict/non-strict file, copytree)
- `TestCopyFilesNestedDirectory`: 1 test for nested directory parent creation

### Rejected Findings (False Positives)

- **R1-MINOR docstring style**: "Worktree bootstrap module" not imperative — D401 is suppressed in ruff config. Changed anyway for consistency.
- **R2 `FileNotFoundError` for missing executables**: With `shell=True`, missing commands produce non-zero exit codes, not Python exceptions. Existing handling covers this.
- **R2 `shell=True` security**: Commands from project YAML config, not external input. Acceptable trust boundary.

### Runtime Verification

- **Lint**: Pending (sandbox blocked execution)
- **Type Check**: Pending (sandbox blocked execution)
- **Tests**: Pending (sandbox blocked execution)

**Action Required**: Run `pytest tests/test_bootstrap.py -v`, `ruff check src/bmad_assist_lite/parallel/bootstrap.py`, and `mypy src/bmad_assist_lite/parallel/bootstrap.py` to verify all fixes pass.

## Dev Agent Record

### Agent Model Used
### Debug Log References
### Completion Notes List
### File List
