---
stepsCompleted: []
inputDocuments:
  - 'architecture.md'
  - 'project-context.md'
---

# bmad-assist-lite-parallel-stories - Epic 9 Breakdown

## Epic 9: Worktree Bootstrap & Validation

**Epic ID:** Epic-9
**Created:** 2026-03-22
**Status:** Ready for Development
**Priority:** High
**Points:** 8
**Stories:** 3

### Overview

Adds a configurable bootstrap phase between worktree creation and subprocess spawn. Copies untracked files (`.env`, configs), runs setup commands (`pip install`, `npm ci`), and executes a validation smoke test to confirm the worktree can build before spending LLM tokens. Uses a canary pattern: the first story's worktree runs full bootstrap + validation; if it fails, the entire batch aborts immediately since all worktrees share the same base branch state.

### Business Goal

Prevent wasted LLM tokens and developer time by catching broken environments (missing `.env`, uninstalled dependencies, broken builds) before any LLM invocation. A 30-second bootstrap check replaces 10+ minutes of doomed LLM work per worktree.

### Strategic Context

- Parallel mode is useless if worktrees can't build — this is a prerequisite for real-world adoption
- Target projects (not bmad-assist-lite itself) need `.env` files, `node_modules/`, `.venv/` to run quality gates
- Git worktrees only contain tracked files — untracked configs and installed dependencies are absent by design
- Canary pattern avoids wasting time bootstrapping N identical broken environments

### Dependencies

- Epic 3 (Orchestrator Core) — bootstrap integrates into the orchestrator spawn flow
- Epic 5 (Crash Recovery) — bootstrap failures must not corrupt parallel state

### Context7 Library Documentation

<!-- No external libraries needed — this epic uses only stdlib (shutil, subprocess) and existing project patterns -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|
| — | — | — | — |

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Worktree Bootstrap; Worktree Loop Spawning; Implementation Patterns & Consistency Rules; Parallel Module Layout |
| `prd.md` | (skip) |
| `ux-design-specification.md` | (skip) |
| `project-context.md` | (full) |

### Recommended Story Order

1. 9-1-bootstrap-module-and-config - Foundation: config fields + bootstrap pipeline implementation
2. 9-2-canary-bootstrap-integration - Wires bootstrap into orchestrator with canary-first pattern
3. 9-3-epic-documentation-sync - Standard documentation sync

---

### Story 9.1: Bootstrap Module & Config

**Story ID:** 9-1-bootstrap-module-and-config
**Component:** `src/bmad_assist_lite/parallel/bootstrap.py`, `src/bmad_assist_lite/parallel/config.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** []

#### User Story

As a developer using parallel mode on a project that requires `.env` files and dependency installation,
I want worktrees to be automatically bootstrapped with the necessary files and commands,
So that the LLM loop runs in a functional environment.

#### Description

Add bootstrap configuration fields to `ParallelConfig` and create a new `bootstrap.py` module that implements the three-phase bootstrap pipeline: copy untracked files, run setup commands, and run a validation command. Each phase is independent and optional (unconfigured = skipped).

#### Current State

`ParallelConfig` in `config.py` has 5 fields: `max_concurrency`, `stagger_delay`, `post_merge_fix_retries`, `conflict_resolution_timeout`, `worktree_base_dir`. No bootstrap, file copy, or validation support exists. Worktrees are created with `git worktree add` and immediately used.

#### Target State

`ParallelConfig` gains 5 new fields:
```python
copy_to_worktree: list[str] = []       # Files/dirs to copy from project root
copy_strict: bool = False               # True = error on missing file, False = warn
setup_commands: list[str] = []          # Commands to run in worktree (e.g., "pip install -e .")
validation_command: str | None = None   # Smoke test command (e.g., "pytest -q -x")
bootstrap_timeout: int = 120            # Per-command timeout in seconds
```

New `bootstrap.py` module with:
- `copy_files_to_worktree(project_root, worktree_path, files, strict)` — copies listed files/dirs using `shutil.copy2`/`shutil.copytree`
- `run_setup_commands(worktree_path, commands, timeout)` — runs each command sequentially via `subprocess.run()` in worktree cwd
- `run_validation_command(worktree_path, command, timeout)` — runs single validation command
- `bootstrap_worktree(project_root, worktree_path, config, validate)` — orchestrates all three phases; `validate=False` skips validation command (for non-canary worktrees)

Returns a `BootstrapResult` frozen Pydantic model with `success: bool`, `failed_phase: str | None`, `error_message: str | None`, `output: str` (captured stdout/stderr for diagnostics).

#### Acceptance Criteria

**Given** `copy_to_worktree: [".env", "local.settings.json"]` is configured
**When** `copy_files_to_worktree()` is called with a valid worktree path
**Then** each listed file is copied from project root to the same relative path in the worktree

**Given** `copy_to_worktree: [".env"]` and `.env` does not exist in project root
**When** `copy_files_to_worktree()` is called with `strict=False`
**Then** a warning is logged and bootstrap continues (no error)

**Given** `copy_to_worktree: [".env"]` and `.env` does not exist in project root
**When** `copy_files_to_worktree()` is called with `strict=True`
**Then** `BootstrapResult.success` is `False` with `failed_phase="copy"` and descriptive error

**Given** `setup_commands: ["pip install -e .", "npm ci"]` is configured
**When** `run_setup_commands()` is called
**Then** commands run sequentially in the worktree cwd; if one fails (non-zero exit), remaining commands are skipped and result reports the failure

**Given** `validation_command: "pytest -q -x"` is configured
**When** `run_validation_command()` is called
**Then** the command runs in worktree cwd; non-zero exit returns `BootstrapResult.success=False`

**Given** a setup command hangs beyond `bootstrap_timeout`
**When** the timeout fires
**Then** the process is killed and `BootstrapResult` reports timeout failure

**Given** no bootstrap fields are configured (all defaults)
**When** `bootstrap_worktree()` is called
**Then** it returns `BootstrapResult(success=True)` immediately (no-op)

#### Technical Notes

- Use `subprocess.run()` with `cwd=str(worktree_path)`, `capture_output=True`, `text=True`, `timeout=bootstrap_timeout`
- Use `get_subprocess_kwargs()` from `providers/_windows.py` for platform safety
- For directory entries in `copy_to_worktree` (trailing `/`), use `shutil.copytree(dirs_exist_ok=True)`
- For files, use `shutil.copy2()` preserving metadata
- Create parent directories in worktree with `Path.mkdir(parents=True, exist_ok=True)` before copy
- Log prefix: `[BOOTSTRAP]` for all log messages
- All subprocess calls must use `timeout` parameter — no hanging processes
- `BootstrapResult` follows frozen Pydantic model pattern
- Update `parallel/__init__.py` exports

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Internal module, no user-visible behavior change. CLI integration is in Story 9.2.

---

### Story 9.2: Canary Bootstrap Integration

**Story ID:** 9-2-canary-bootstrap-integration
**Component:** `src/bmad_assist_lite/parallel/orchestrator.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [9-1-bootstrap-module-and-config]

#### User Story

As a developer running parallel mode,
I want the first worktree to act as a canary that validates the bootstrap recipe before other worktrees are created,
So that a broken base branch is detected in 30 seconds instead of wasting LLM tokens across multiple doomed worktrees.

#### Description

Integrate `bootstrap_worktree()` into the orchestrator's `_spawn_and_monitor()` flow with a canary-first pattern. The first story in the batch runs full bootstrap (copy + setup + validation). If it fails, the entire parallel run aborts — no further worktrees are created. If it passes, remaining worktrees run copy + setup but skip the validation command (since the identical base branch already proved buildable).

#### Current State

In `orchestrator.py`, `_spawn_and_monitor()` (lines 422-438) creates a worktree via `create_worktree()` and immediately spawns the subprocess. No bootstrap step exists between creation and spawn. The stagger delay happens between story spawns but doesn't gate on bootstrap success.

#### Target State

New flow in orchestrator:
```
1. Select first ready story
2. Create worktree
3. Run bootstrap_worktree(validate=True)  ← CANARY
4. If bootstrap fails:
   a. Log error with full diagnostic output
   b. Clean up the canary worktree
   c. Abort entire parallel run with clear error message
   d. Exit with non-zero return code
5. If bootstrap succeeds:
   a. Spawn subprocess for canary story
   b. For each remaining story (respecting stagger_delay):
      i.  Create worktree
      ii. Run bootstrap_worktree(validate=False)  ← skip validation
      iii. If setup fails: mark story as blocked, clean up worktree, continue
      iv. If setup succeeds: spawn subprocess
```

The canary bootstrap runs synchronously (via `asyncio.to_thread()`) before any subprocess is spawned. This is a blocking gate — nothing proceeds until the canary passes.

#### Acceptance Criteria

**Given** bootstrap config is set with `setup_commands` and `validation_command`
**When** the orchestrator starts a parallel run
**Then** the first story's worktree runs full bootstrap including validation before subprocess spawn

**Given** the canary worktree's bootstrap fails (validation command returns non-zero)
**When** the orchestrator detects the failure
**Then** no other worktrees are created, the canary worktree is cleaned up, the run aborts with a clear error message including the failed command's output, and exit code is non-zero

**Given** the canary worktree's bootstrap succeeds
**When** the orchestrator proceeds to spawn remaining stories
**Then** remaining worktrees run `bootstrap_worktree(validate=False)` — copy + setup only, no validation command

**Given** a non-canary worktree's setup command fails
**When** the orchestrator detects the failure
**Then** that story is marked as blocked with `block_reason="Bootstrap setup failed: {error}"`, its worktree is cleaned up, and other stories continue normally

**Given** no bootstrap config is set (all defaults)
**When** the orchestrator runs
**Then** behavior is identical to current (no bootstrap phase, immediate subprocess spawn)

**Given** the canary bootstrap succeeds
**When** the orchestrator log is inspected
**Then** it contains `[BOOTSTRAP] Canary story {id} bootstrap passed — proceeding with batch` at INFO level

**Given** the canary bootstrap fails
**When** the orchestrator log is inspected
**Then** it contains `[BOOTSTRAP] Canary story {id} bootstrap FAILED — aborting parallel run` at ERROR level, followed by the captured command output

#### Technical Notes

- Bootstrap runs via `asyncio.to_thread(bootstrap_worktree, ...)` to avoid blocking the event loop
- The canary story is whichever story the dependency resolver selects first — no special selection logic
- On canary failure, call `cleanup_worktree()` before aborting to leave git state clean
- The `_has_bootstrap_config()` helper should check if any bootstrap fields are non-default — if all are empty/None, skip bootstrap entirely (zero overhead for unconfigured users)
- Update `_spawn_and_monitor()` signature to accept a `is_canary: bool` parameter
- Stagger delay should apply AFTER canary bootstrap succeeds, not before — don't delay the canary
- On canary abort, write to both orchestrator log and console output via `_output_mux.write_orchestrator()`

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool internal behavior change; no UI elements affected

---

### Story 9.3: Epic Documentation Sync

**Story ID:** 9-3-epic-documentation-sync
**Component:** `docs/`, `CLAUDE.md`, `docs/project-context.md`
**Estimate:** Small
**Points:** 2
**Priority:** High
**Dependencies:** [All prior stories in this epic]

#### User Story

As a developer (human or AI),
I want project documentation to reflect everything built in Epic 9,
So that future implementation decisions are based on accurate information.

#### Description

Final story in every epic. Audit all changes introduced by the epic and update project documentation accordingly. Uses a two-tier system: Tier 1 (core docs) is always evaluated; Tier 2 (reusable library) is conditional based on what the epic introduced.

#### Current State

Documentation reflects the project state before Epic 9 began.

#### Target State

All documentation accurately reflects the project state after Epic 9 completion.

#### Acceptance Criteria

**Given** all implementation stories in Epic 9 are complete
**When** the documentation sync story executes
**Then** every applicable item in the Doc Audit Checklist is addressed

**Given** a Tier 1 doc has a stale section
**When** the audit identifies it
**Then** the section is updated with accurate information from the implemented code

**Given** the epic introduced a new reusable pattern, feature, or architectural decision
**When** the audit identifies it
**Then** the corresponding `docs/reusable/` file is created or updated

#### Technical Notes

**Audit Method:** Run `git diff main...HEAD --name-only` (or diff against the epic's base branch) to identify all files changed in this epic. Cross-reference changed paths against the checklist below.

**Do NOT update:** `architecture.md`, `prd.md`, `ux-design-specification.md`. These are planning artifacts owned by the planning phase. If they need changes, flag them for a course correction.

#### Doc Audit Checklist

##### Tier 1: Core Docs (Always Evaluate)

**`CLAUDE.md`:**
- [ ] New config fields added? Update Configuration section with `copy_to_worktree`, `setup_commands`, `validation_command`, `copy_strict`, `bootstrap_timeout` under `parallel:`
- [ ] New module created? Update Architecture / Package Layout with `bootstrap.py`
- [ ] New patterns introduced? Document canary bootstrap pattern

**`docs/project-context.md`:**
- [ ] New code conventions established? Update relevant rules section
- [ ] New test patterns introduced? Update "Testing Rules" section

##### Tier 2: Reusable Library (Conditional)

- [ ] New reusable code pattern? The canary bootstrap pattern may warrant a `docs/reusable/patterns/canary-bootstrap.md`
- [ ] Any `docs/reusable/BACKLOG.md` items implicitly completed? Mark them done

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Documentation-only changes, no user-facing behavior affected

---

## Test Impact Summary

### Unit / Integration Tests

| Test File | Stories Affected | Changes |
|-----------|------------------|---------|
| `tests/test_parallel_bootstrap.py` | 9.1 | NEW: Tests for copy_files_to_worktree, run_setup_commands, run_validation_command, bootstrap_worktree |
| `tests/test_parallel_config.py` | 9.1 | MODIFIED: Add tests for new config fields, defaults, validation |
| `tests/test_orchestrator.py` | 9.2 | MODIFIED: Add tests for canary bootstrap flow, abort on failure, skip validation for non-canary |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 9.1 | None | — | — | Internal module |
| 9.2 | None | — | — | CLI tool internal |
| 9.3 | None | — | — | Documentation only |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests updated and passing (`pytest -q --tb=line --no-header`)
- [ ] `ruff check src/ && mypy src/` passes
- [ ] Bootstrap with `copy_to_worktree` + `setup_commands` + `validation_command` works end-to-end
- [ ] Canary failure correctly aborts entire parallel run
- [ ] Unconfigured bootstrap has zero overhead (no-op path verified)
- [ ] Documentation sync story completed (Tier 1 core docs verified current)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Setup commands have side effects on host system | Low | Medium | Commands run in worktree cwd with isolated env; documented as user's responsibility |
| Bootstrap timeout too short for large `npm ci` | Medium | Low | Default 120s is configurable; documented in config comments |
| Canary pattern masks per-worktree issues (e.g., port conflicts) | Low | Low | These are runtime issues, not bootstrap issues; quality gate handles them |
| File copy of large directories (e.g., `data/`) slows bootstrap | Low | Medium | Document that `copy_to_worktree` is for config files, not data; user controls the list |

## Rollback Plan

Revert the 3 commits (one per story). The `parallel/` module has no external consumers of bootstrap — removing it restores the previous immediate-spawn behavior. Config fields with defaults mean existing `bmad-assist-lite.yaml` files are unaffected.
