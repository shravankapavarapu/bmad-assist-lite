---
stepsCompleted: []
inputDocuments:
  - 'prd.md'
  - 'architecture.md'
  - 'epics.md'
---

# bmad-assist-lite-parallel-stories - Epic 1 Breakdown

## Epic 1: Foundation & Configuration

**Epic ID:** Epic-1
**Created:** 2026-03-17
**Status:** Draft
**Priority:** High
**Points:** 8
**Stories:** 3

### Overview

Set up the parallel module structure, configuration model, git operations wrapper, and existing code integration. After this epic, the `parallel` CLI subcommand exists, config validates, `--epic`/`--story`/`--single-story` flags work, and sprint sync respects parallel mode.

### Business Goal

Establish the foundational infrastructure for parallel story execution so all subsequent epics have a stable base to build upon.

### Strategic Context

- First epic — all other epics depend on this foundation
- Extends the existing config system with parallel-specific settings
- Modifies 3 existing files with minimal (~18 lines) changes
- No new external dependencies — pure Python 3.11+ stdlib + Pydantic

### Dependencies

- None (first epic)

### Context7 Library Documentation

<!-- No external libraries needed for this epic — uses existing project dependencies (Pydantic, Typer, subprocess) -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Implementation Sequence; Parallel Module Layout; Enforcement Guidelines; Configuration Schema |
| `prd.md` | Functional Requirements; Configuration |
| `project-context.md` | `(full)` |

### Recommended Story Order

1. 1-1-parallel-module-structure-and-configuration-model - Foundation: module structure and config must exist first
2. 1-2-git-operations-wrapper - Git ops used by all subsequent parallel components
3. 1-3-existing-code-integration - Integrates with existing codebase, depends on module structure from 1.1

---

### Story 1.1: Parallel Module Structure & Configuration Model

**Story ID:** 1-1-parallel-module-structure-and-configuration-model
**Component:** `src/bmad_assist_lite/parallel/`
**Estimate:** Small
**Points:** 2
**Priority:** High
**Dependencies:** []

#### User Story

As a developer,
I want the parallel module created with a configuration model that validates parallel settings,
So that the foundation exists for all parallel execution components.

#### Description

Create the `src/bmad_assist_lite/parallel/` package with `__init__.py`, `config.py`, and `exceptions.py`. Add a `ParallelConfig` Pydantic model that validates parallel-specific settings and integrate it into the existing 2-tier config system.

#### Current State

No `parallel/` module exists. The config system in `core/config.py` has no awareness of parallel settings.

#### Target State

- `src/bmad_assist_lite/parallel/__init__.py` exists (package init)
- `src/bmad_assist_lite/parallel/config.py` contains `ParallelConfig` Pydantic model
- `src/bmad_assist_lite/parallel/exceptions.py` contains `ParallelError(BmadAssistError)`
- `core/config.py` includes optional `parallel: ParallelConfig` field in the root config model
- Config loads cleanly with or without `parallel:` section in YAML

#### Acceptance Criteria

**Given** the `bmad-assist-lite.yaml` contains a `parallel:` section
**When** config is loaded via `get_config()`
**Then** `ParallelConfig` model validates `max_concurrency` (1-5, default 3), `stagger_delay` (default 10), `post_merge_fix_retries` (default 1), `worktree_base_dir` (nullable)
**And** invalid values raise `ConfigError` with descriptive messages

**Given** the `parallel:` section is absent from config
**When** config is loaded
**Then** default `ParallelConfig` values are used (no error)

**Given** the parallel module exists
**When** imported
**Then** `src/bmad_assist_lite/parallel/__init__.py`, `config.py`, `exceptions.py` exist
**And** `ParallelError` inherits from `BmadAssistError`
**And** all code passes mypy strict + ruff

#### Technical Notes

- Extend existing `core/config.py` with `ParallelConfig` Pydantic model
- Follow existing config patterns (2-tier YAML, singleton via `get_config()`)
- `ParallelError` in `parallel/exceptions.py` inherits from `BmadAssistError`
- `max_concurrency` constrained to 1-5 via Pydantic `Field(ge=1, le=5)`

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 1.2: Git Operations Wrapper

**Story ID:** 1-2-git-operations-wrapper
**Component:** `src/bmad_assist_lite/parallel/git_ops.py`
**Estimate:** Small
**Points:** 3
**Priority:** High
**Dependencies:** [Story 1.1]

#### User Story

As a developer,
I want a platform-safe git subprocess wrapper,
So that all parallel components use consistent git error handling.

#### Description

Create `git_ops.py` with a `_run_git()` function that wraps `subprocess.run()` for all git operations. Provides consistent error handling, Windows-safe process creation flags, and configurable check behavior for commands that may legitimately fail.

#### Current State

No centralized git subprocess wrapper exists for the parallel module. Individual git commands would need to duplicate subprocess configuration.

#### Target State

- `src/bmad_assist_lite/parallel/git_ops.py` contains `_run_git(args, cwd, check)` function
- All git subprocess calls use `get_subprocess_kwargs()` for platform safety
- `check=True` raises `ParallelError` on non-zero exit
- `check=False` returns `CompletedProcess` for commands that may fail (merge with conflicts)
- Helper functions: `get_current_branch()`, `is_protected_branch()`

#### Acceptance Criteria

**Given** a valid git repository path
**When** `_run_git(["status"], cwd=repo_path)` is called
**Then** the command executes via `subprocess.run()` with `get_subprocess_kwargs()`
**And** stdout and stderr are captured as text

**Given** a git command that fails (non-zero exit)
**When** `_run_git(args, cwd, check=True)` is called
**Then** `ParallelError` is raised with the stderr message

**Given** a git command that may legitimately fail (e.g., merge with conflicts)
**When** `_run_git(args, cwd, check=False)` is called
**Then** the `CompletedProcess` is returned without raising

**Given** the wrapper is used on Windows
**When** any git command runs
**Then** `creationflags=CREATE_NO_WINDOW` is applied (via `get_subprocess_kwargs()`)

#### Technical Notes

- Use raw `subprocess.run()` — not GitPython (no new dependencies)
- Reuse existing `get_subprocess_kwargs()` from `providers/_windows.py`
- All paths via `pathlib.Path` (NFR12)
- `_run_git` is module-private; public API is higher-level functions

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 1.3: Existing Code Integration

**Story ID:** 1-3-existing-code-integration
**Component:** `src/bmad_assist_lite/cli.py`, `loop/runner.py`, `core/sprint_sync.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [Story 1.1]

#### User Story

As a developer,
I want the existing loop to accept `--epic`, `--story`, and `--single-story` CLI flags, and sprint sync to respect parallel mode,
So that the orchestrator can invoke targeted single-story loop execution in worktrees.

#### Description

Modify 3 existing files (~18 lines total) to support parallel mode: add CLI flags for targeted story execution, add early exit for single-story mode, and bypass sprint-status sync when `BMAD_PARALLEL_MODE` environment variable is set. Register the `parallel` Typer subcommand group.

#### Current State

- `cli.py`: `run` command processes all backlog stories sequentially
- `loop/runner.py`: loop continues to next story after completion
- `core/sprint_sync.py`: always writes to sprint-status.yaml after each phase
- No `parallel` subcommand group exists

#### Target State

- `cli.py`: `run` command accepts `--epic`, `--story`, `--single-story` options; `parallel` subcommand group registered
- `loop/runner.py`: exits after single story when `--single-story` is set
- `core/sprint_sync.py`: skips sync when `BMAD_PARALLEL_MODE=1` is set in environment

#### Acceptance Criteria

**Given** the CLI is invoked with `--epic 3 --story 1 --single-story`
**When** the loop runs
**Then** it executes only story 3.1 and exits after that story completes (success or failure)
**And** it does not search for the next backlog story

**Given** the environment variable `BMAD_PARALLEL_MODE=1` is set
**When** sprint sync runs after a phase
**Then** sprint sync is skipped (no write to sprint-status.yaml)
**And** a debug log message indicates sync was bypassed

**Given** the CLI is invoked without `--single-story`
**When** the loop runs
**Then** existing behavior is unchanged (processes all backlog stories)

**Given** the `parallel` subcommand group is registered
**When** `bmad-assist-lite parallel --help` is run
**Then** it shows the parallel subcommand help (even if commands are not yet implemented)

#### Technical Notes

- `cli.py`: Add `--epic`, `--story`, `--single-story` as Typer Options to `run` command
- `cli.py`: Register `parallel` as Typer subcommand group (empty for now, populated in Epic 3)
- `loop/runner.py`: Check `single_story` flag after story completion, break loop if set
- `core/sprint_sync.py`: Check `os.environ.get("BMAD_PARALLEL_MODE")` at start of sync function
- NFR15: Existing behavior must be unchanged when new flags are not used

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

## Test Impact Summary

### Unit / Integration Tests

| Test File | Stories Affected | Changes |
|-----------|------------------|---------|
| `tests/test_parallel_config.py` | 1.1 | New: validate ParallelConfig defaults, bounds, error messages |
| `tests/test_git_ops.py` | 1.2 | New: test _run_git with check=True/False, Windows kwargs, error handling |
| `tests/test_existing_integration.py` | 1.3 | New: test --single-story exit, BMAD_PARALLEL_MODE bypass, CLI flags |
| `tests/test_config.py` | 1.1 | Modified: ensure existing config tests still pass with parallel field |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 1.1 | None | — | — | Config model, no UI |
| 1.2 | None | — | — | Git subprocess wrapper |
| 1.3 | None | — | — | CLI flags, backend-only |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests written and passing (`pytest -q --tb=short --no-header`)
- [ ] All code passes mypy strict mode (`mypy src/`)
- [ ] All code passes ruff linting (`ruff check src/`)
- [ ] All code passes ruff formatting (`ruff format --check src/`)
- [ ] Existing test suite still passes (NFR17)
- [ ] `parallel` subcommand group registered and `--help` works
- [ ] `--epic`/`--story`/`--single-story` flags functional
- [ ] `BMAD_PARALLEL_MODE` env var bypasses sprint sync
- [ ] ParallelConfig validates with correct defaults and bounds

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Config changes break existing tests | Low | High | Autouse config fixture resets config; ParallelConfig has defaults |
| CLI flag changes break existing `run` behavior | Low | High | NFR15: all flags optional with no-op defaults; existing tests validate |
| `get_subprocess_kwargs()` import path changes | Low | Medium | Direct import test in test_git_ops.py |
| Windows-specific subprocess flags not tested in CI | Medium | Medium | Use `get_subprocess_kwargs()` which is already battle-tested |
| Typer subcommand group conflicts with existing commands | Low | Low | `parallel` is a distinct namespace |

## Rollback Plan

All changes are additive (new module + optional config field + optional CLI flags). Rollback by:
1. Remove `src/bmad_assist_lite/parallel/` directory
2. Revert the ~18 lines changed in `cli.py`, `loop/runner.py`, `core/sprint_sync.py`
3. Remove `parallel` field from config model in `core/config.py`
