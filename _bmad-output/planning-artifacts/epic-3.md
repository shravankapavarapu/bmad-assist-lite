---
stepsCompleted: []
inputDocuments:
  - 'prd.md'
  - 'architecture.md'
  - 'epics.md'
---

# bmad-assist-lite-parallel-stories - Epic 3 Breakdown

## Epic 3: Parallel Execution Core

**Epic ID:** Epic-3
**Created:** 2026-03-17
**Status:** Draft
**Priority:** High
**Points:** 21
**Stories:** 6

### Overview

Build the orchestrator that runs stories in parallel. After this epic, user runs `bmad-assist-lite parallel run` and stories execute concurrently in isolated git worktrees. The orchestrator creates worktrees, spawns loop subprocesses, monitors completion, multiplexes output with prefixes, tracks state, and handles Ctrl+C gracefully.

### Business Goal

Deliver the core value proposition — parallel story execution that dramatically reduces wall-clock development time.

### Strategic Context

- Largest and most complex epic — 6 stories, highest risk
- Uses asyncio event loop (not threading) for orchestration
- Process-per-story via subprocess spawn (not thread-per-story)
- All new code in `src/bmad_assist_lite/parallel/`
- Windows-native process management throughout

### Dependencies

- Epic 1 (configuration, git_ops, CLI flags, `--single-story`)
- Epic 2 (dependency resolution, ready story discovery, scheduling scores)

### Context7 Library Documentation

<!-- No external libraries — uses Python stdlib asyncio and subprocess -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Parallel Module Layout; Worktree Manager; Orchestrator; State Persistence; Enforcement Guidelines; Configuration Schema |
| `prd.md` | Functional Requirements; Non-Functional Requirements |
| `project-context.md` | `(full)` |

### Recommended Story Order

1. 3-1-worktree-manager - Foundation: worktree creation/cleanup used by all orchestrator operations
2. 3-2-orchestrator-core-loop-and-subprocess-spawning - Core: the main asyncio loop that drives everything
3. 3-3-parallel-state-persistence - State: must persist before adding more features on top of the orchestrator
4. 3-4-parallel-run-cli-command-and-branch-guard - Entry point: CLI command that starts the orchestrator
5. 3-5-live-output-multiplexing - Output: enhances the orchestrator with prefixed console streaming
6. 3-6-graceful-shutdown-and-drain-mode - Safety: shutdown handling depends on orchestrator + state

---

### Story 3.1: Worktree Manager

**Story ID:** 3-1-worktree-manager
**Component:** `src/bmad_assist_lite/parallel/worktree_manager.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** []

#### User Story

As a developer,
I want git worktrees created and cleaned up for parallel stories,
So that each story executes in complete filesystem isolation.

#### Description

Create `worktree_manager.py` with functions to create git worktrees (with new branches) for each parallel story and clean them up after completion or failure. Handles path construction, branch naming, and platform-safe cleanup.

#### Current State

No worktree management exists. Stories execute in the main project directory.

#### Target State

- `create_worktree(story_id, base_dir)` creates worktree at `{base_dir}/parallel-{story_id}/` with branch `parallel/{story_id}`
- `cleanup_worktree(story_id, base_dir)` removes worktree and deletes branch
- `list_worktrees()` enumerates existing worktrees via `git worktree list --porcelain`
- All paths use `pathlib.Path` (NFR12)

#### Acceptance Criteria

**Given** the user is on branch `epic/3`
**When** `create_worktree(story_id="3.1")` is called
**Then** a new git worktree is created at `{worktree_base_dir}/parallel-3-1/` (or adjacent to project if null)
**And** a new branch `parallel/3-1` is created from the current HEAD
**And** the worktree path is returned

**Given** a worktree exists for story 3.1
**When** `cleanup_worktree(story_id="3.1")` is called
**Then** the worktree is removed via `git worktree remove`
**And** the branch `parallel/3-1` is deleted via `git branch -d`
**And** the worktree directory no longer exists on disk

**Given** worktree creation is invoked
**When** it completes
**Then** it finishes within 30 seconds for a typical project (NFR7)

**Given** the operation runs on Windows
**When** worktree paths are constructed
**Then** all paths use `pathlib.Path` and resolve correctly on NTFS

#### Technical Notes

- Uses `git_ops._run_git()` from Story 1.2 for all git commands
- Worktree naming: `parallel-{story_id_with_dashes}/` (dots replaced with dashes)
- Branch naming: `parallel/{story_id_with_dashes}`
- Default base_dir: parent of project root (adjacent placement)
- `git worktree add -b <branch> <path>` for creation
- `git worktree remove <path>` then `git branch -d <branch>` for cleanup

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 3.2: Orchestrator Core Loop & Subprocess Spawning

**Story ID:** 3-2-orchestrator-core-loop-and-subprocess-spawning
**Component:** `src/bmad_assist_lite/parallel/orchestrator.py`
**Estimate:** Large
**Points:** 5
**Priority:** High
**Dependencies:** [Story 3.1]

#### User Story

As a developer,
I want an asyncio orchestrator that spawns loop subprocesses in worktrees and monitors their completion,
So that multiple stories execute concurrently with proper lifecycle management.

#### Description

Create `orchestrator.py` with the main `Orchestrator` class that runs the parallel execution loop. Uses asyncio to manage concurrent subprocess spawning, monitors completion via exit codes, enforces concurrency limits, applies stagger delays, and re-evaluates ready stories after each completion.

#### Current State

No parallel orchestrator exists. Stories can only run sequentially via the existing loop.

#### Target State

- `Orchestrator` class with `async run()` method
- Spawns subprocesses via `asyncio.create_subprocess_exec`
- Each subprocess: `sys.executable -m bmad_assist_lite run --epic N --story M --single-story`
- `cwd=worktree_path`, `env={..., "BMAD_PARALLEL_MODE": "1"}`
- Concurrency limited by `asyncio.Semaphore(max_concurrency)`
- Stagger delay between spawns in same evaluation cycle
- Re-evaluates ready stories after each completion via `dependency_graph.get_ready_stories()`

#### Acceptance Criteria

**Given** the dependency resolver identifies stories 3.1 and 3.2 as ready and max_concurrency is 3
**When** the orchestrator loop runs
**Then** worktrees are created for 3.1 and 3.2
**And** loop subprocesses are spawned via `asyncio.create_subprocess_exec(sys.executable, "-m", "bmad_assist_lite", "run", "--epic", "3", "--story", "1", "--single-story")`
**And** each subprocess runs with `cwd=worktree_path` and `env={..., "BMAD_PARALLEL_MODE": "1"}`

**Given** a subprocess exits with code 0
**When** the orchestrator detects completion
**Then** the story is transitioned to `merging` status
**And** the orchestrator re-evaluates ready stories (using dependency resolver)

**Given** a subprocess exits with non-zero code
**When** the orchestrator detects completion
**Then** the story status is updated to reflect failure
**And** the orchestrator continues with remaining stories

**Given** max_concurrency is 3 and 3 stories are already running
**When** another story becomes ready
**Then** the orchestrator waits (via `asyncio.Semaphore`) until a slot opens before spawning

**Given** `stagger_delay` is configured to 10 seconds
**When** multiple worktrees are started in the same evaluation cycle
**Then** each start is delayed by `stagger_delay` seconds from the previous

#### Technical Notes

- asyncio event loop — not threading (architecture decision)
- `asyncio.create_subprocess_exec` for non-blocking subprocess management
- `asyncio.Semaphore(max_concurrency)` for slot limiting
- `asyncio.sleep(stagger_delay)` between spawns
- Track running tasks in `dict[str, asyncio.Task]`
- `asyncio.wait(tasks, return_when=FIRST_COMPLETED)` for completion detection
- NFR6: orchestrator overhead <1% of total wall-clock time

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 3.3: Parallel State Persistence

**Story ID:** 3-3-parallel-state-persistence
**Component:** `src/bmad_assist_lite/parallel/state.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [Story 3.2]

#### User Story

As a developer,
I want orchestrator state persisted to parallel-state.yaml after every state change,
So that the system can recover from crashes.

#### Description

Create `state.py` with a frozen `ParallelState` Pydantic model that tracks all orchestrator state. State is persisted atomically after every transition using the temp + `os.replace()` pattern. The orchestrator reads existing state on startup for resume support.

#### Current State

No parallel state persistence exists. If the orchestrator crashes, all progress is lost.

#### Target State

- `ParallelState` frozen Pydantic model with: `base_branch`, `epic`, `started_at`, `stories: dict[str, StoryState]`
- `StoryState` tracks: `status`, `worktree_path`, `started_at`, `completed_at`, `error`
- `save_state(state, path)` writes atomically (temp + `os.replace()`)
- `load_state(path)` reads existing state
- State transitions via `model_copy(update={...})` (never direct mutation)

#### Acceptance Criteria

**Given** the orchestrator starts a fresh run
**When** initial state is created
**Then** `parallel-state.yaml` is written with `base_branch`, `epic`, `started_at`, and all stories with status `backlog`

**Given** a story transitions to `in-flight`
**When** state is updated
**Then** `parallel-state.yaml` is atomically written (temp + `os.replace()`) with the updated story status, worktree path, and `started_at` timestamp

**Given** `ParallelState` is a frozen Pydantic model
**When** a state transition occurs
**Then** `model_copy(update={...})` is used (never direct mutation)
**And** the new state is saved via atomic write

**Given** the orchestrator starts and `parallel-state.yaml` exists
**When** state is loaded on startup
**Then** the existing state is read and used to determine what's done, in-flight, and blocked

#### Technical Notes

- Frozen Pydantic model (`model_config = ConfigDict(frozen=True)`)
- `model_copy(update={...})` for immutable state transitions
- Atomic write: write to `.tmp` file, then `os.replace()` (follows existing pattern in `core/`)
- State file location: `.bmad-assist-lite/parallel-state.yaml`
- NFR1: state must survive process crashes
- NFR4: atomic writes prevent corruption on partial failure

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 3.4: Parallel Run CLI Command & Branch Guard

**Story ID:** 3-4-parallel-run-cli-command-and-branch-guard
**Component:** `src/bmad_assist_lite/parallel/cli.py`
**Estimate:** Small
**Points:** 2
**Priority:** High
**Dependencies:** [Story 3.2]

#### User Story

As a developer,
I want a `bmad-assist-lite parallel run` command that starts the orchestrator with branch safety,
So that users have a clean entry point for parallel execution.

#### Description

Implement the `parallel run` CLI command that starts the orchestrator. Includes branch guard to refuse execution on main/master, settings summary on startup, and integration with the Typer subcommand group registered in Epic 1.

#### Current State

The `parallel` Typer subcommand group exists (from Story 1.3) but has no commands registered.

#### Target State

- `parallel run` command starts the orchestrator
- Branch guard prevents execution on main/master
- Startup prints settings summary (max_concurrency, base branch, epic, story count, ready count)
- Default config values used if parallel config is missing

#### Acceptance Criteria

**Given** the user is on branch `epic/3`
**When** `bmad-assist-lite parallel run` is invoked
**Then** the orchestrator starts, reads the epic file, builds the dependency graph, and begins parallel execution

**Given** the user is on branch `main` or `master`
**When** `bmad-assist-lite parallel run` is invoked
**Then** the command refuses to run with a clear message: "Parallel mode cannot run on main/master. Create a feature branch first."
**And** exits with non-zero code

**Given** the orchestrator is invoked
**When** it starts
**Then** it prints settings summary (max_concurrency, base branch, epic, story count, ready count)

**Given** the parallel config is missing or invalid
**When** the command is invoked
**Then** default config values are used (max_concurrency=3, stagger_delay=10)

#### Technical Notes

- Typer command registered under the `parallel` subcommand group from Story 1.3
- Branch detection via `git_ops.get_current_branch()` + `git_ops.is_protected_branch()`
- `asyncio.run(orchestrator.run())` to start the async orchestrator from sync CLI
- Exit code 1 on branch guard failure

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 3.5: Live Output Multiplexing

**Story ID:** 3-5-live-output-multiplexing
**Component:** `src/bmad_assist_lite/parallel/output.py`
**Estimate:** Medium
**Points:** 3
**Priority:** Medium
**Dependencies:** [Story 3.2]

#### User Story

As a developer,
I want live output from all worktree subprocesses prefixed and streamed to the console,
So that I can monitor parallel story progress in real time.

#### Description

Create `output.py` with an async output multiplexer that reads stdout from all subprocess streams, prefixes each line with the story ID, and writes to the console. Uses asyncio stream readers for non-blocking I/O and the existing `write_progress()` function with output lock for thread safety.

#### Current State

No output multiplexing exists. Subprocess output would be lost or interleaved without prefixes.

#### Target State

- `OutputMultiplexer` class manages async stream readers per subprocess
- Lines prefixed with `[{story_id}]` for story output, `[ORCHESTRATOR]` for orchestrator messages
- Non-blocking I/O via `asyncio` stream readers (`proc.stdout`)
- Thread-safe console writes via existing `write_progress()` with output lock
- Clean EOF handling when subprocesses exit

#### Acceptance Criteria

**Given** two worktree subprocesses are running (story 3.1 and 3.2)
**When** each produces stdout output
**Then** lines are prefixed with `[3.1]` and `[3.2]` respectively
**And** output from both stories interleaves on the console without corruption

**Given** the orchestrator makes a decision (story complete, creating worktree, etc.)
**When** it logs to console
**Then** the line is prefixed with `[ORCHESTRATOR]`

**Given** output is read via `asyncio` stream reader (`proc.stdout`)
**When** lines arrive from subprocess
**Then** they are decoded, prefixed, and written via `write_progress()` (existing output lock for thread safety)

**Given** a subprocess exits
**When** the stream reader detects EOF
**Then** the reader task completes cleanly without errors

#### Technical Notes

- `proc.stdout.readline()` in async loop per subprocess
- `asyncio.Task` per stream reader, managed by the orchestrator
- Prefix format: `[{story_id}]` padded for alignment
- Reuse existing `write_progress()` from loop output utilities
- Handle `b""` (EOF) cleanly to break the read loop
- Stderr merged into stdout via `subprocess` `stderr=STDOUT`

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 3.6: Graceful Shutdown & Drain Mode

**Story ID:** 3-6-graceful-shutdown-and-drain-mode
**Component:** `src/bmad_assist_lite/parallel/orchestrator.py`
**Estimate:** Medium
**Points:** 5
**Priority:** High
**Dependencies:** [Story 3.2, Story 3.3]

#### User Story

As a developer,
I want the orchestrator to handle Ctrl+C by draining running stories and persisting state,
So that no work is lost on interruption and the next run can resume.

#### Description

Add graceful shutdown handling to the orchestrator. First Ctrl+C sets drain mode (stop spawning, wait for running stories). Second Ctrl+C force-terminates all subprocesses. State is persisted before exit in both cases.

#### Current State

No shutdown handling in the orchestrator. Ctrl+C would kill the process and all subprocesses, potentially losing state.

#### Target State

- First Ctrl+C: set `_draining = True`, stop new spawns, wait for running subprocesses
- Second Ctrl+C: force-terminate all subprocesses via `terminate_process()`, save state, exit
- State saved before exit in both cases
- Exit summary printed: done count, in-flight count, blocked stories with unmet deps
- Windows-safe signal handling

#### Acceptance Criteria

**Given** the orchestrator is running with 2 stories in-flight
**When** the user presses Ctrl+C
**Then** the orchestrator stops spawning new stories immediately
**And** prints "Shutting down -- waiting for running stories to finish..."
**And** waits for running subprocesses to complete (drain mode)

**Given** drain mode is active and all subprocesses have exited
**When** drain completes
**Then** `parallel-state.yaml` is saved with current state (in-flight stories remain in-flight for resume)
**And** the orchestrator prints a summary of what's done, in-flight, and remaining
**And** exits cleanly

**Given** the orchestrator is interrupted during drain (second Ctrl+C)
**When** the second signal is received
**Then** running subprocesses are terminated via `terminate_process()` (process tree kill)
**And** state is saved immediately
**And** the orchestrator exits

**Given** the orchestrator exits with blocked stories
**When** the exit summary is printed
**Then** blocked stories and their unmet dependencies are listed

#### Technical Notes

- Windows: use existing `terminate_process()` from `providers/_windows.py` (uses `taskkill`)
- Unix: `killpg` for process group termination
- `asyncio` signal handling: `loop.add_signal_handler(SIGINT, handler)` on Unix; on Windows, use `signal.signal(SIGINT, handler)`
- `_draining` flag checked in the main loop before spawning new stories
- `_force_exit` flag on second signal triggers immediate terminate + save
- NFR2: no story work is lost — worktree branches preserve committed progress

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
| `tests/test_worktree_manager.py` | 3.1 | New: create/cleanup worktree, path construction, branch naming |
| `tests/test_orchestrator.py` | 3.2, 3.6 | New: subprocess spawning, concurrency limits, stagger delay, shutdown |
| `tests/test_parallel_state.py` | 3.3 | New: state persistence, atomic writes, load/save, immutable transitions |
| `tests/test_parallel_cli.py` | 3.4 | New: branch guard, settings summary, default config |
| `tests/test_output_multiplexer.py` | 3.5 | New: prefix formatting, EOF handling, interleaved output |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 3.1 | None | — | — | Git worktree operations |
| 3.2 | None | — | — | Async orchestrator internals |
| 3.3 | None | — | — | State persistence |
| 3.4 | None | — | — | CLI command |
| 3.5 | None | — | — | Console output |
| 3.6 | None | — | — | Signal handling |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests written and passing (`pytest -q --tb=short --no-header`)
- [ ] All code passes mypy strict mode (`mypy src/`)
- [ ] All code passes ruff linting (`ruff check src/`)
- [ ] All code passes ruff formatting (`ruff format --check src/`)
- [ ] Existing test suite still passes (NFR17)
- [ ] `bmad-assist-lite parallel run` executes stories in parallel worktrees
- [ ] Concurrency limited to max_concurrency setting
- [ ] Stagger delay applied between spawns
- [ ] State persisted after every transition (survives crash)
- [ ] Live output prefixed per story
- [ ] Ctrl+C gracefully drains running stories
- [ ] Second Ctrl+C force-terminates
- [ ] Branch guard prevents execution on main/master

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| asyncio complexity causes subtle concurrency bugs | Medium | High | Extensive unit tests with mock subprocesses; avoid shared mutable state |
| Windows signal handling differs from Unix | High | Medium | Platform-specific signal registration; test on Windows CI |
| Subprocess spawning fails due to Python path issues | Medium | Medium | Use `sys.executable` for subprocess; test with `--single-story` |
| Worktree creation fails on NTFS with long paths | Low | Medium | Keep worktree paths short; use `pathlib.Path.resolve()` |
| State corruption during concurrent writes | Low | High | Atomic write pattern (temp + os.replace); single writer (orchestrator) |
| Stagger delay causes slow startup with many stories | Low | Low | Configurable delay; default 10s is reasonable for API rate limiting |

## Rollback Plan

All changes are in new files within `src/bmad_assist_lite/parallel/`. Rollback by:
1. Remove new files: `worktree_manager.py`, `orchestrator.py`, `state.py`, `cli.py`, `output.py`
2. Remove the `parallel run` command registration (but keep the subcommand group from Epic 1)
3. Remove corresponding test files
