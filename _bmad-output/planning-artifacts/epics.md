---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories', 'step-04-final-validation']
status: 'complete'
completedAt: '2026-03-17'
epicCount: 6
storyCount: 25
frCoverage: '54/54'
inputDocuments:
  - 'prd.md'
  - 'architecture.md'
extension:
  name: 'cursor-provider-linux'
  epic: 11
  status: 'in-progress'
  date: '2026-06-12'
  stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories']
  inputDocuments:
    - 'requirements-cursor-provider.md'
    - 'architecture.md (Extension: Cursor Provider sections)'
---

# bmad-assist-lite-parallel-stories - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for bmad-assist-lite-parallel-stories, decomposing the requirements from the PRD and Architecture into implementable stories.

## Requirements Inventory

### Functional Requirements

- FR1: Orchestrator can parse story dependencies from epic markdown files using the existing `**Dependencies:** Story X.Y` format
- FR2: Orchestrator can build a directed acyclic graph (DAG) from parsed story dependencies
- FR3: Orchestrator can detect circular dependencies and report them to the user before execution begins
- FR4: Orchestrator can determine which stories are ready to execute (all dependencies satisfied, not in-flight, not blocked)
- FR5: Orchestrator can compute scheduling scores to prioritize stories that unblock the most downstream work
- FR6: Orchestrator can re-evaluate ready stories after each story completion and merge
- FR7: Orchestrator can create git worktrees from the current base branch for each parallel story
- FR8: Orchestrator can spawn the existing loop process in each worktree targeting a specific epic and story
- FR9: Orchestrator can run up to N stories concurrently where N is configurable (default 3, max 5)
- FR10: Orchestrator can stagger worktree starts by a configurable delay to avoid API rate limit spikes
- FR11: Orchestrator can monitor worktree loop processes and detect completion (success or failure) via exit codes
- FR12: Each worktree loop can execute the full 7-phase pipeline independently with its own state.yaml
- FR13: Worktree loops can operate without updating sprint-status.yaml (parallel mode bypass)
- FR14: Orchestrator can queue completed stories for sequential merge (one at a time)
- FR15: Merger agent can merge a worktree branch into the base branch using git merge
- FR16: Merger agent can invoke Claude CLI to resolve merge conflicts with story context
- FR17: Orchestrator can run the full project quality gate (lint, typecheck, build, test) on the base branch after each merge
- FR18: Orchestrator can invoke fix_quality_gate on the base branch when post-merge QG fails
- FR19: Orchestrator can re-run quality gate after fix_quality_gate and determine pass/fail
- FR20: Orchestrator can commit post-merge fixes with tagged messages for traceability
- FR21: Orchestrator can persist its state to parallel-state.yaml including story statuses, worktree references, and timestamps
- FR22: Orchestrator can read parallel-state.yaml on startup to determine what's done, in-flight, and blocked
- FR23: Orchestrator can resume in-flight stories by detecting existing worktrees and restarting loops with resume flag
- FR24: Orchestrator can detect orphaned worktrees (in-flight status but no worktree on disk) and reset them to backlog
- FR25: Orchestrator can prune stale git worktree references on startup
- FR26: Orchestrator can update sprint-status.yaml on the base branch when stories complete (after merge + QG)
- FR27: Orchestrator can transition story status through: backlog → in-flight → merging → done (or blocked)
- FR28: Orchestrator can update epic status when all stories in the epic are complete
- FR29: Orchestrator can run epic teardown phases (epic_quality_gate, retrospective) on the base branch after all stories merge
- FR30: Orchestrator can mark a story as blocked when its worktree loop fails quality gate after retry
- FR31: Orchestrator can mark a story as blocked when merge conflicts are unresolvable
- FR32: Orchestrator can mark a story as blocked when post-merge QG fails after fix_quality_gate attempt
- FR33: Orchestrator can prevent dependent stories from starting when their dependencies are blocked
- FR34: Orchestrator can continue executing non-dependent stories when one story is blocked
- FR35: Orchestrator can clean up worktrees for both completed and blocked stories
- FR36: User can start or resume parallel execution via `bmad-assist-lite parallel run`
- FR37: User can view current parallel execution state via `bmad-assist-lite parallel status`
- FR38: User can reset a blocked story to backlog via `bmad-assist-lite parallel unblock <story>`
- FR39: Orchestrator can refuse to run on main/master branch and inform the user to use a feature branch
- FR40: Orchestrator can handle Ctrl+C by stopping new story spawning and draining running stories
- FR41: Orchestrator can persist state before shutdown so the next run can resume
- FR42: Orchestrator can report blocked stories and their unmet dependencies on exit
- FR43: Orchestrator can write high-level events to an orchestrator log file (parallel-run.log)
- FR44: Orchestrator can stream prefixed live output from all worktrees to the console
- FR45: Orchestrator can generate a summary report on completion with per-story timing and time saved
- FR46: Orchestrator can log detailed post-merge QG failure information (which gates failed, specific errors)
- FR47: Status command can display story states, current phases (by peeking at worktree state files), and dependency status
- FR48: User can configure max concurrency via `parallel.max_concurrency` in bmad-assist-lite.yaml
- FR49: User can configure stagger delay via `parallel.stagger_delay` in bmad-assist-lite.yaml
- FR50: User can configure post-merge fix retries via `parallel.post_merge_fix_retries` in bmad-assist-lite.yaml
- FR51: User can configure custom worktree location via `parallel.worktree_base_dir` in bmad-assist-lite.yaml
- FR52: Existing loop can accept `--epic` and `--story` CLI flags to target a specific story
- FR53: Existing loop can exit after completing a single story when invoked with `--single-story` flag
- FR54: Existing sprint sync can be bypassed when `BMAD_PARALLEL_MODE` environment variable is set

### NonFunctional Requirements

- NFR1: Orchestrator state (parallel-state.yaml) must survive process crashes — state persisted after every status transition using atomic write pattern
- NFR2: No story work is lost on orchestrator crash — worktree branches and per-worktree state.yaml preserve all committed progress
- NFR3: Orchestrator restart after crash must reach consistent state within 30 seconds
- NFR4: Git operations (worktree create, merge, branch delete) must be atomic — partial failures must not leave the repository in a broken state
- NFR5: Concurrent worktree loops must not interfere with each other — complete filesystem and git branch isolation
- NFR6: Orchestrator overhead must be negligible compared to story execution time — target <1% of total wall-clock time
- NFR7: Worktree creation must complete within 30 seconds for typical project sizes
- NFR8: Worktree cleanup must complete within 10 seconds per worktree
- NFR9: Dependency graph computation must complete in <1 second for epics with up to 50 stories
- NFR10: `parallel status` command must respond in <2 seconds
- NFR11: All git operations must work on Windows (primary) and Unix; platform-specific code isolated in dedicated modules
- NFR12: Path handling must use pathlib.Path throughout — no hardcoded path separators
- NFR13: Process spawning must use platform-safe subprocess kwargs
- NFR14: File locking and atomic writes must work correctly on both NTFS and ext4/APFS
- NFR15: Orchestrator must not modify any existing loop code behavior — worktree loops produce identical results to direct sequential execution
- NFR16: Claude CLI invocation for merge conflict resolution must use the same authentication mechanism as the existing Claude SDK provider
- NFR17: All new code must pass the existing test suite plus new parallel-specific tests
- NFR18: All new code must pass mypy strict mode and ruff linting with the existing project configuration

### Additional Requirements

- Implementation follows 8-step sequence from architecture: git_ops → worktree_manager → orchestrator → loop spawning → merger → state → CLI → observability
- All new code in `src/bmad_assist_lite/parallel/` module (12 source files)
- 9 new test files required (one per major module)
- 6 enforcement guidelines: git subprocess pattern, async rules, state mutation, log prefixes, process cleanup, testing patterns
- 3 existing files modified (~18 lines): cli.py, loop/runner.py, core/sprint_sync.py
- No new dependencies — uses existing Python 3.11+ toolchain
- No starter template — brownfield enhancement to existing package
- asyncio event loop for orchestrator (not threading)
- Raw subprocess for git operations (not GitPython)
- Claude CLI subprocess with --print for merger agent (not SDK)
- Process-per-story via subprocess spawn (not thread-per-story)
- asyncio stream reader for output multiplexing

### UX Design Requirements

N/A — CLI tool, no UI design requirements

### FR Coverage Map

| FR | Epic | Description |
|----|------|-------------|
| FR1 | Epic 2 | Parse story dependencies from epic files |
| FR2 | Epic 2 | Build DAG from dependencies |
| FR3 | Epic 2 | Detect circular dependencies |
| FR4 | Epic 2 | Determine ready stories |
| FR5 | Epic 2 | Compute scheduling scores |
| FR6 | Epic 2 | Re-evaluate ready stories after completion |
| FR7 | Epic 3 | Create git worktrees from base branch |
| FR8 | Epic 3 | Spawn loop process in worktree |
| FR9 | Epic 3 | Run up to N stories concurrently |
| FR10 | Epic 3 | Stagger worktree starts |
| FR11 | Epic 3 | Monitor processes via exit codes |
| FR12 | Epic 3 | Worktree loop executes full 7-phase pipeline |
| FR13 | Epic 3 | Worktree loops bypass sprint sync |
| FR14 | Epic 4 | Queue completed stories for merge |
| FR15 | Epic 4 | Merge worktree branch into base |
| FR16 | Epic 4 | Claude CLI resolves merge conflicts |
| FR17 | Epic 4 | Post-merge quality gate on base branch |
| FR18 | Epic 4 | fix_quality_gate on base branch |
| FR19 | Epic 4 | Re-run QG after fix |
| FR20 | Epic 4 | Commit post-merge fixes with tagged messages |
| FR21 | Epic 3 | Persist state to parallel-state.yaml |
| FR22 | Epic 3 | Read parallel-state.yaml on startup |
| FR23 | Epic 5 | Resume in-flight stories |
| FR24 | Epic 5 | Detect orphaned worktrees |
| FR25 | Epic 5 | Prune stale worktree references |
| FR26 | Epic 4 | Update sprint-status after merge + QG |
| FR27 | Epic 3 | Transition story status (backlog → in-flight → done) |
| FR28 | Epic 5 | Update epic status when all stories complete |
| FR29 | Epic 6 | Run epic teardown phases |
| FR30 | Epic 5 | Mark blocked on worktree QG failure |
| FR31 | Epic 5 | Mark blocked on unresolvable merge conflicts |
| FR32 | Epic 5 | Mark blocked on post-merge QG failure |
| FR33 | Epic 5 | Prevent dependent stories when deps blocked |
| FR34 | Epic 5 | Continue non-dependent stories when one blocked |
| FR35 | Epic 5 | Clean up worktrees for completed and blocked |
| FR36 | Epic 3 | `parallel run` CLI command |
| FR37 | Epic 5 | `parallel status` CLI command |
| FR38 | Epic 5 | `parallel unblock` CLI command |
| FR39 | Epic 3 | Refuse to run on main/master |
| FR40 | Epic 3 | Handle Ctrl+C with drain |
| FR41 | Epic 3 | Persist state before shutdown |
| FR42 | Epic 3 | Report blocked stories on exit |
| FR43 | Epic 6 | Write orchestrator log file |
| FR44 | Epic 6 | Stream prefixed live output (enhanced) |
| FR45 | Epic 6 | Generate summary report with time saved |
| FR46 | Epic 6 | Log detailed post-merge QG failures |
| FR47 | Epic 6 | Status command shows phases and dependencies |
| FR48 | Epic 1 | Configure max_concurrency |
| FR49 | Epic 1 | Configure stagger_delay |
| FR50 | Epic 1 | Configure post_merge_fix_retries |
| FR51 | Epic 1 | Configure worktree_base_dir |
| FR52 | Epic 1 | --epic and --story CLI flags |
| FR53 | Epic 1 | --single-story exit behavior |
| FR54 | Epic 1 | BMAD_PARALLEL_MODE sprint sync bypass |

## Epic List

### Epic 1: Foundation & Configuration
Set up the parallel module structure, configuration model, git operations wrapper, and existing code integration. After this epic, the `parallel` CLI subcommand exists, config validates, `--epic`/`--story`/`--single-story` flags work, and sprint sync respects parallel mode.
**FRs covered:** FR48, FR49, FR50, FR51, FR52, FR53, FR54

### Epic 2: Dependency Resolution
Build the dependency graph engine. After this epic, given an epic file with story dependencies, the system builds a DAG, detects circular dependencies, computes scheduling scores prioritizing stories that unblock the most downstream work, and determines which stories are ready to run in parallel.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6

### Epic 3: Parallel Execution Core
Build the orchestrator that runs stories in parallel. After this epic, user runs `bmad-assist-lite parallel run` and stories execute concurrently in isolated git worktrees. The orchestrator creates worktrees, spawns loop subprocesses, monitors completion, multiplexes output with prefixes, tracks state, and handles Ctrl+C gracefully.
**FRs covered:** FR7, FR8, FR9, FR10, FR11, FR12, FR13, FR21, FR22, FR27, FR36, FR39, FR40, FR41, FR42
**Dependencies:** Epic 1, Epic 2

### Epic 4: Merge & Integration Safety
Build the merger agent and post-merge validation. After this epic, completed stories merge safely back to the base branch. Merge conflicts are resolved by Claude CLI. Post-merge quality gate catches integration issues. fix_quality_gate attempts auto-repair. Sprint-status updates after successful merge.
**FRs covered:** FR14, FR15, FR16, FR17, FR18, FR19, FR20, FR26
**Dependencies:** Epic 3

### Epic 5: Resilience & Recovery
Build crash recovery, blocked story handling, and management CLI. After this epic, the system survives crashes and resumes cleanly. Orphaned worktrees are detected and pruned. Blocked stories are tracked with dependency cascade prevention. User can `parallel status` and `parallel unblock` to manage execution.
**FRs covered:** FR23, FR24, FR25, FR28, FR30, FR31, FR32, FR33, FR34, FR35, FR37, FR38
**Dependencies:** Epic 3, Epic 4

### Epic 6: Observability & Epic Teardown
Build full logging, reporting, and epic completion. After this epic, the orchestrator writes a structured log file, generates a summary report with per-story timing and time saved, logs detailed post-merge QG failures, shows current phases in status display, and runs epic teardown (epic_quality_gate + retrospective) after all stories merge.
**FRs covered:** FR29, FR43, FR44, FR45, FR46, FR47
**Dependencies:** Epic 4, Epic 5

## Epic 1: Foundation & Configuration

Set up the parallel module structure, configuration model, git operations wrapper, and existing code integration. After this epic, the `parallel` CLI subcommand exists, config validates, `--epic`/`--story`/`--single-story` flags work, and sprint sync respects parallel mode.

### Story 1.1: Parallel Module Structure & Configuration Model

As a developer,
I want the parallel module created with a configuration model that validates parallel settings,
So that the foundation exists for all parallel execution components.

**Acceptance Criteria:**

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

### Story 1.2: Git Operations Wrapper

As a developer,
I want a platform-safe git subprocess wrapper,
So that all parallel components use consistent git error handling.

**Acceptance Criteria:**

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

### Story 1.3: Existing Code Integration

As a developer,
I want the existing loop to accept `--epic`, `--story`, and `--single-story` CLI flags, and sprint sync to respect parallel mode,
So that the orchestrator can invoke targeted single-story loop execution in worktrees.

**Acceptance Criteria:**

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

## Epic 2: Dependency Resolution

Build the dependency graph engine. Given an epic file with story dependencies, the system builds a DAG, detects circular dependencies, computes scheduling scores, and determines which stories are ready to run in parallel.

### Story 2.1: Epic Dependency Parsing & DAG Construction

As a developer,
I want the orchestrator to parse story dependencies from epic files and build a directed acyclic graph,
So that the system knows which stories depend on which.

**Acceptance Criteria:**

**Given** an epic file containing stories with `**Dependencies:** Story 3.2, Story 3.4`
**When** dependencies are parsed
**Then** an adjacency graph is built mapping each story to its dependencies
**And** stories without dependencies are identified as roots (in-degree 0)

**Given** an epic file with stories that have no `**Dependencies:**` field
**When** dependencies are parsed
**Then** those stories are treated as having no dependencies (independent)

**Given** the existing `bmad/parser.py` already extracts `EpicStory.dependencies`
**When** the dependency resolver receives parsed story data
**Then** it builds the DAG from the existing parser output (reuses, does not duplicate parsing)

**Given** an epic with up to 50 stories
**When** the DAG is constructed
**Then** construction completes in <1 second (NFR9)

### Story 2.2: Circular Dependency Detection & Scheduling Scores

As a developer,
I want circular dependencies detected before execution and stories prioritized by unblocking potential,
So that the orchestrator never deadlocks and maximizes parallelism throughput.

**Acceptance Criteria:**

**Given** an epic where Story A depends on B and Story B depends on A
**When** cycle detection runs (DFS with recursion stack)
**Then** the circular dependency is detected and reported to the user with the cycle path
**And** execution does not start

**Given** a valid DAG with no cycles
**When** scheduling scores are computed
**Then** each story receives a score using: `(1000 * unblock_potential) + (100 * depth_score) + (10 * priority)`
**And** stories that unblock more downstream work score higher
**And** root stories (no dependencies) score higher on depth

**Given** a linear chain: A → B → C (C depends on B, B depends on A)
**When** scheduling scores are computed
**Then** A scores highest (unblocks 2 downstream), B scores middle (unblocks 1), C scores lowest

### Story 2.3: Ready Story Discovery & Re-evaluation

As a developer,
I want to determine which stories are ready to execute and re-evaluate after each completion,
So that the orchestrator always knows what to schedule next.

**Acceptance Criteria:**

**Given** a DAG where Stories 3.1 and 3.2 have no dependencies, Story 3.3 depends on 3.1
**When** `get_ready_stories()` is called with no stories completed
**Then** it returns [3.1, 3.2] sorted by scheduling score (higher first)
**And** 3.3 is not included (dependency not satisfied)

**Given** Story 3.1 is marked as `done` in the passing set
**When** `get_ready_stories()` is called with updated passing set
**Then** Story 3.3 is now included (dependency 3.1 satisfied)

**Given** Story 3.2 is `in-flight` (currently executing)
**When** `get_ready_stories()` is called
**Then** Story 3.2 is excluded (already running)

**Given** Story 3.1 is `blocked`
**When** `get_ready_stories()` is called
**Then** Story 3.3 is excluded (dependency 3.1 not `done`)

**Given** `are_dependencies_satisfied()` is called in a loop for N stories
**When** a pre-computed `passing_ids` set is passed
**Then** each check is O(1) per dependency (not O(n) re-scanning all stories)

## Epic 3: Parallel Execution Core

Build the orchestrator that runs stories in parallel. After this epic, user runs `bmad-assist-lite parallel run` and stories execute concurrently in isolated git worktrees with live output, state tracking, and graceful shutdown.

### Story 3.1: Worktree Manager

As a developer,
I want git worktrees created and cleaned up for parallel stories,
So that each story executes in complete filesystem isolation.

**Acceptance Criteria:**

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

### Story 3.2: Orchestrator Core Loop & Subprocess Spawning

As a developer,
I want an asyncio orchestrator that spawns loop subprocesses in worktrees and monitors their completion,
So that multiple stories execute concurrently with proper lifecycle management.

**Acceptance Criteria:**

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
**Then** the orchestrator waits (via `asyncio.Event`) until a slot opens before spawning

**Given** `stagger_delay` is configured to 10 seconds
**When** multiple worktrees are started in the same evaluation cycle
**Then** each start is delayed by `stagger_delay` seconds from the previous

### Story 3.3: Parallel State Persistence

As a developer,
I want orchestrator state persisted to parallel-state.yaml after every state change,
So that the system can recover from crashes.

**Acceptance Criteria:**

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

### Story 3.4: Parallel Run CLI Command & Branch Guard

As a developer,
I want a `bmad-assist-lite parallel run` command that starts the orchestrator with branch safety,
So that users have a clean entry point for parallel execution.

**Acceptance Criteria:**

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

### Story 3.5: Live Output Multiplexing

As a developer,
I want live output from all worktree subprocesses prefixed and streamed to the console,
So that I can monitor parallel story progress in real time.

**Acceptance Criteria:**

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

### Story 3.6: Graceful Shutdown & Drain Mode

As a developer,
I want the orchestrator to handle Ctrl+C by draining running stories and persisting state,
So that no work is lost on interruption and the next run can resume.

**Acceptance Criteria:**

**Given** the orchestrator is running with 2 stories in-flight
**When** the user presses Ctrl+C
**Then** the orchestrator stops spawning new stories immediately
**And** prints "Shutting down — waiting for running stories to finish..."
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

## Epic 4: Merge & Integration Safety

Build the merger agent and post-merge validation. After this epic, completed stories merge safely back to the base branch with conflict resolution, quality gate protection, and sprint-status updates.

### Story 4.1: Sequential Merge Queue & Git Merge

As a developer,
I want completed stories queued and merged one at a time into the base branch,
So that merges never conflict with each other and the base branch stays stable.

**Acceptance Criteria:**

**Given** story 3.1 completes in its worktree (exit code 0)
**When** the orchestrator processes the completion
**Then** the story is queued for merge with status transition to `merging`

**Given** the merge queue contains story 3.1
**When** the merge is executed
**Then** `git merge parallel/3-1` runs on the base branch
**And** if the merge succeeds (no conflicts), the merge commit exists on the base branch

**Given** stories 3.1 and 3.2 complete near-simultaneously
**When** both are queued for merge
**Then** only one merge executes at a time (sequential queue)
**And** the second merge waits until the first completes (including post-merge QG)

**Given** a merge is attempted
**When** git merge produces a fast-forward or clean merge
**Then** the worktree branch is deleted via `git branch -d parallel/3-1`

**Given** a merge fails due to conflicts
**When** `git merge` returns non-zero with conflict markers
**Then** the merge is aborted (`git merge --abort`) before attempting conflict resolution
**And** the conflict file list is captured from `git diff --name-only --diff-filter=U`

### Story 4.2: Claude CLI Merge Conflict Resolution

As a developer,
I want merge conflicts resolved by Claude CLI with full story context,
So that integration issues are automatically handled when possible.

**Acceptance Criteria:**

**Given** a merge produced conflicts in 2 files
**When** the merger agent is invoked
**Then** Claude CLI is called via `subprocess.run(["claude", "--print", "-p", prompt])` with a prompt containing the story title/description, conflict file list, conflict markers, and resolution instructions

**Given** Claude CLI returns resolved content
**When** the resolution is applied
**Then** conflict markers are removed from all affected files
**And** `git add` stages the resolved files
**And** `git commit` creates a merge commit

**Given** Claude CLI fails (timeout, auth error, non-zero exit)
**When** the resolution fails
**Then** the merge is aborted (`git merge --abort`)
**And** the story is marked as `blocked` with error details

**Given** Claude CLI returns content but conflicts remain
**When** residual conflict markers are detected
**Then** the merge is aborted
**And** the story is marked as `blocked`

### Story 4.3: Post-Merge Quality Gate

As a developer,
I want a full project quality gate run on the base branch after each merge,
So that integration issues between parallel stories are caught immediately.

**Acceptance Criteria:**

**Given** a story has been successfully merged to the base branch
**When** the post-merge quality gate runs
**Then** lint, typecheck, build, and test commands execute on the base branch
**And** results are captured with pass/fail per gate

**Given** all 4 gates pass
**When** the quality gate completes
**Then** the story status transitions to `done`
**And** the orchestrator proceeds to the next merge in the queue

**Given** one or more gates fail
**When** the quality gate completes
**Then** the failure details are captured (which gates failed, specific error output)
**And** fix_quality_gate is invoked

**Given** the quality gate commands are sourced
**When** commands are determined
**Then** they follow the existing priority order: config `quality_gate` section → auto-detected toolchain

### Story 4.4: Post-Merge Fix Quality Gate & Sprint Status Update

As a developer,
I want integration failures auto-fixed on the base branch and sprint-status updated after successful merge,
So that dependent stories get clean code and sprint tracking reflects reality.

**Acceptance Criteria:**

**Given** post-merge QG failed for story 3.3
**When** fix_quality_gate is invoked on the base branch
**Then** the master LLM receives the failure report and attempts to fix the issues
**And** the fix runs with the existing `fix-quality-gate` workflow template

**Given** fix_quality_gate makes changes
**When** the fix is committed
**Then** the commit message is tagged: `fix: post-merge integration fix for story 3.3`

**Given** fix_quality_gate completes
**When** quality gate is re-run
**Then** if all gates pass: story transitions to `done`
**And** if gates still fail: story transitions to `blocked` with detailed error info

**Given** `post_merge_fix_retries` is configured to 1 (default)
**When** fix_quality_gate is attempted
**Then** at most 1 fix attempt is made before marking blocked

**Given** a story successfully transitions to `done` (merge + QG pass)
**When** sprint-status is updated
**Then** `sprint_status_manager` writes the story as `done` in `sprint-status.yaml` on the base branch
**And** the update reuses the existing `SprintStatus` Pydantic model

## Epic 5: Resilience & Recovery

Build crash recovery, blocked story handling, and management CLI. After this epic, the system survives crashes, manages blocked stories with dependency cascade prevention, and provides CLI tools for inspection and control.

### Story 5.1: Crash Recovery & Resume In-Flight Stories

As a developer,
I want the orchestrator to resume in-flight stories after a crash,
So that no work is lost when the process is interrupted unexpectedly.

**Acceptance Criteria:**

**Given** `parallel-state.yaml` shows story 3.2 as `in-flight` with worktree `parallel/3-2`
**When** the orchestrator starts and the worktree exists on disk
**Then** the orchestrator spawns the loop in the existing worktree with `--resume` flag
**And** the loop reads its own `state.yaml` and resumes from the last completed phase

**Given** `parallel-state.yaml` shows story 3.2 as `in-flight`
**When** the orchestrator starts and the worktree does NOT exist on disk (orphaned state)
**Then** the story status is reset to `backlog` in `parallel-state.yaml`
**And** a warning is logged: "Story 3.2 was in-flight but worktree missing — reset to backlog"

**Given** the orchestrator restarts after a crash
**When** it reaches consistent state
**Then** the recovery completes within 30 seconds (NFR3)
**And** stories marked `done` remain `done`
**And** stories marked `blocked` remain `blocked`

### Story 5.2: Orphan Detection & Worktree Pruning

As a developer,
I want stale worktrees detected and cleaned on startup,
So that disk space isn't wasted and git state stays clean.

**Acceptance Criteria:**

**Given** the orchestrator starts
**When** initialization runs
**Then** `git worktree prune` is executed to clean stale references
**And** `git worktree list --porcelain` is used to enumerate existing worktrees

**Given** a worktree exists on disk for `parallel/3-4` but `parallel-state.yaml` has no record of story 3.4
**When** orphan detection runs
**Then** the worktree is identified as orphaned
**And** it is cleaned up via `cleanup_worktree()`
**And** a warning is logged

**Given** a worktree exists and `parallel-state.yaml` shows the story as `done`
**When** orphan detection runs
**Then** the worktree is cleaned up (should have been removed after merge)

**Given** cleanup is invoked
**When** a worktree is removed
**Then** removal completes within 10 seconds (NFR8)

### Story 5.3: Blocked Story Handling & Dependency Cascade

As a developer,
I want blocked stories to prevent their dependents from starting while non-dependent stories continue,
So that failures are isolated and the pipeline keeps moving where possible.

**Acceptance Criteria:**

**Given** story 3.1 fails quality gate in its worktree after retry
**When** the orchestrator processes the failure
**Then** story 3.1 is marked `blocked` in `parallel-state.yaml`
**And** the worktree is cleaned up

**Given** story 3.3 merges but post-merge QG fails after fix_quality_gate
**When** the orchestrator processes the failure
**Then** story 3.3 is marked `blocked`

**Given** story 3.1 is `blocked` and story 3.3 depends on 3.1
**When** `get_ready_stories()` evaluates 3.3
**Then** story 3.3 is NOT returned as ready

**Given** story 3.1 is `blocked` and story 3.4 has no dependency on 3.1
**When** `get_ready_stories()` evaluates 3.4
**Then** story 3.4 IS returned as ready
**And** the orchestrator spawns 3.4 normally

**Given** a merge fails with unresolvable conflicts
**When** the merger agent gives up
**Then** the story is marked `blocked`
**And** the worktree is cleaned up

### Story 5.4: Status CLI Command

As a developer,
I want to check the current state of a parallel run from another terminal,
So that I can monitor progress without watching the live console.

**Acceptance Criteria:**

**Given** a parallel run is in progress
**When** `bmad-assist-lite parallel status` is invoked from another terminal
**Then** it reads `parallel-state.yaml` and displays a human-readable table showing story statuses, durations, and dependency state

**Given** no `parallel-state.yaml` exists
**When** `parallel status` is invoked
**Then** it prints "No parallel run state found" and exits

**Given** all stories in the epic are `done`
**When** `parallel status` is invoked
**Then** it shows all stories as done with completion summary

**Given** the command is invoked
**When** it runs
**Then** it responds in <2 seconds (NFR10)

### Story 5.5: Unblock CLI Command

As a developer,
I want to reset a blocked story to backlog so the orchestrator picks it up on the next run,
So that I can retry after manually fixing the underlying issue.

**Acceptance Criteria:**

**Given** `parallel-state.yaml` shows story 3.2 as `blocked`
**When** `bmad-assist-lite parallel unblock 3.2` is invoked
**Then** story 3.2 status changes to `backlog` in `parallel-state.yaml`
**And** confirmation is printed: "Story 3.2 unblocked — will be picked up on next parallel run"

**Given** `parallel-state.yaml` shows story 3.2 as `done`
**When** `bmad-assist-lite parallel unblock 3.2` is invoked
**Then** an error is printed: "Story 3.2 is not blocked (status: done)"
**And** `parallel-state.yaml` is not modified

**Given** story ID "3.99" does not exist in `parallel-state.yaml`
**When** `bmad-assist-lite parallel unblock 3.99` is invoked
**Then** an error is printed: "Story 3.99 not found in parallel state"

**Given** a story is unblocked and `parallel run` is invoked
**When** the orchestrator evaluates ready stories
**Then** the unblocked story is treated as `backlog` and scheduled normally (fresh worktree, full 7-phase pipeline)

## Epic 6: Observability & Epic Teardown

Build full logging, reporting, and epic completion. After this epic, the system provides complete visibility into parallel runs and performs epic-level quality validation.

### Story 6.1: Orchestrator Log File

As a developer,
I want high-level orchestrator events written to a log file,
So that I can review what happened during a parallel run after it completes.

**Acceptance Criteria:**

**Given** the orchestrator starts a parallel run
**When** the run begins
**Then** `parallel-run.log` is created (or appended) in the project root
**And** the log header includes: timestamp, base branch, epic, max_concurrency, story count

**Given** the orchestrator makes decisions during the run
**When** events occur (story started, story completed, merge queued, merge result, QG result, story blocked, dependency unlocked)
**Then** each event is written to the log with timestamp and `[ORCHESTRATOR]` prefix
**And** the log uses INFO level for normal events, WARNING for recoverable issues, ERROR for failures

**Given** the orchestrator encounters a merge QG failure
**When** the failure details are logged
**Then** the specific gates that failed and their error output are included

**Given** logging overhead
**When** measured across a full run
**Then** log I/O contributes <1% of total wall-clock time (NFR6)

### Story 6.2: Enhanced Status Display with Phase Info

As a developer,
I want the status command to show which phase each in-flight story is currently in,
So that I can see detailed progress beyond just "in-flight."

**Acceptance Criteria:**

**Given** story 3.2 is in-flight with a worktree at `parallel-3-2/`
**When** `bmad-assist-lite parallel status` is invoked
**Then** it peeks at `parallel-3-2/.bmad-assist-lite/state.yaml` to read `current_phase`
**And** displays: `Story 3.2: in-flight  (phase: CODE_REVIEW, running 22m)`

**Given** a worktree's `state.yaml` is unreadable (locked, corrupted, missing)
**When** the status command attempts to peek
**Then** it falls back gracefully to `Story 3.2: in-flight  (running 22m)` without the phase
**And** no error is raised

**Given** the status display shows dependencies
**When** a dependency is `done`
**Then** it shows: `depends on: 3.1 ✓`
**When** a dependency is `in-flight`
**Then** it shows: `depends on: 3.2 ⏳`
**When** a dependency is `blocked`
**Then** it shows: `depends on: 3.1 ✗ (blocked)`

### Story 6.3: Summary Report Generation

As a developer,
I want a summary report generated when a parallel run completes,
So that I can see concrete time savings and per-story results.

**Acceptance Criteria:**

**Given** all stories have completed (done or blocked)
**When** the orchestrator finishes
**Then** a summary report is appended to `parallel-run.log` containing:
  - Total stories: X completed, Y blocked
  - Per-story timing: start time, completion time, duration, merge time
  - Wall-clock time (parallel): actual elapsed time
  - Estimated sequential time: sum of all individual story durations
  - Time saved: sequential estimate minus wall-clock time (absolute and percentage)
  - Merge results: X clean, Y conflict-resolved, Z failed
  - Post-merge QG results: X passed, Y fixed, Z blocked

**Given** a run with 6 stories completing in 2h 47m with sequential estimate of 6h 10m
**When** the summary is generated
**Then** it shows: "Time saved: 3h 23m (55% reduction)"

**Given** some stories were blocked
**When** the summary is generated
**Then** blocked stories are listed with their failure reason

**Given** the summary is generated
**When** it is written
**Then** it is also printed to stdout so the user sees it immediately

### Story 6.4: Epic Teardown Phases

As a developer,
I want epic_quality_gate and retrospective to run on the base branch after all stories merge,
So that the full project is validated and lessons are captured before the epic is considered complete.

**Acceptance Criteria:**

**Given** all stories in the epic are `done` (all merged + post-merge QG passed)
**When** the orchestrator detects epic completion
**Then** it runs epic teardown phases on the base branch in order: `epic_quality_gate` then `retrospective`

**Given** epic teardown needs to run
**When** the orchestrator invokes teardown
**Then** it spawns the existing loop with `--epic N` targeting the teardown phases

**Given** `epic_quality_gate` fails
**When** teardown reports the failure
**Then** the failure details are logged to the orchestrator log
**And** the user is informed which tests failed
**And** the epic is NOT marked as complete

**Given** both teardown phases pass
**When** teardown completes
**Then** the epic status is updated to `done` in sprint-status
**And** the summary report is generated
**And** the orchestrator exits with code 0

**Given** some stories were `blocked` and never completed
**When** the orchestrator reaches the end of ready stories
**Then** epic teardown does NOT run (not all stories done)
**And** the orchestrator reports the blocked stories and exits

---

# Extension: Cursor Provider (Composer 2.5) + Linux Migration — Epic 11

_Appended 2026-06-12. Requirements inventory for Epic 11. Sources: `requirements-cursor-provider.md`, `architecture.md` (extension sections, decisions D1–D14). Dedicated epic file: `epic-11.md`. FR/NFR numbering below is scoped to this extension and independent of the parallel-stories inventory above._

## Requirements Inventory (Cursor Extension)

### Functional Requirements

- FR1: `CursorProvider` extends `BaseProvider` implementing `provider_name`, `_do_invoke()`, `_cleanup()`, `parse_output()`, `supports_model()`
- FR2: `provider_name` returns `"cursor"`; `supports_model()` accepts `composer-*` model strings
- FR3: Invocation: `agent -p --output-format stream-json --model <model>` with workspace = project root
- FR4: Master phases run write-mode (`--force --trust`); multi/code_review runs read-only (no `--force` + permissions deny config)
- FR5: Streaming assistant-event text fed to `ResultCollector.add()` as it arrives (activity tracking for grace period)
- FR6: Final response text extracted from the terminal `{"type":"result"}` event's `result` field; `session_id` captured
- FR7: Missing terminal event or non-zero exit without result event → `ProviderError` carrying the tail of stderr
- FR8: Model verification: system-init event `model` field compared to requested model; mismatch logged at WARNING and recorded
- FR9: `resolve_cli_path()` resolves `agent`/`cursor-agent` with standard 3-tier order (config override → PATH → known locations)
- FR10: Config schema accepts `provider: cursor` in `master` and `multi`; `providers.cli_paths.cursor` override supported
- FR11: `parse_output()` returns response text compatible with Evidence Score parsing when used as validator
- FR12: Timeout + grace-period behavior inherited unchanged from `BaseProvider.invoke()`
- FR13: Prompt delivery works for prompts >32K chars (argv on Linux; stdin if spike S1 confirms)
- FR14: `terminate_process()` Unix branch escalates SIGTERM → SIGKILL after grace (per its docstring contract)
- FR15: Linux deployment setup documented (CLI install, API key, project bootstrap)

### NonFunctional Requirements

- NFR1: Zero regression to existing providers and loop behavior on Windows
- NFR2: Existing toolchain compliance: strict mypy, ruff, pytest conventions (mocked subprocess tests; no live CLI calls in CI)
- NFR3: Cost safety: a run can never silently proceed on `composer-2.5-fast` without a logged warning
- NFR4: Reliability: every Cursor invocation bounded by a hard timeout; no orphaned `agent` processes after timeout/kill on either platform
- NFR5: Provider failures are non-fatal to multi-validator aggregation (consistent with existing multi-LLM handling)

### Additional Requirements (from Architecture Extension)

- No starter template — brownfield extension; first story is the D12 platform fix, not project initialization
- Deny-config crash-recovery sweep hooks into `loop/cleanup.py` (D3) — marker-file ownership protocol, never touch user-authored `.cursor/cli.json`
- Actual model recorded via existing `ProviderResult.model` field — no schema changes required (D6, verified)
- CLI version logged once per process via cached `agent --version` (D11)
- Binary resolution order: `cursor-agent` before `agent` within each tier (D10)
- Tolerant NDJSON parsing: malformed lines and unknown event types never crash the provider (patterns section)
- Spikes S1–S5 are deployment gates documented in `docs/linux-deployment.md`, not code stories
- Strict 7-file touch budget enforcing the provider boundary (one new module + five touched files + docs)
- Test fixtures must cover three failure shapes: missing result event, malformed lines, model-mismatch init event
- Epic Definition of Done uses this project's gates (`ruff check src/`, `mypy src/`, `pytest`) — not web-project placeholders

### UX Design Requirements

None — headless CLI tool, no UI surface.

## Epic List (Extension)

### Epic 11: Cursor Provider — Composer 2.5 as Master LLM on Linux

A developer running bmad-assist-lite can configure `provider: cursor` with `model: composer-2.5` as master (or multi validator) and execute full dev loops on a Linux box — dev_story and code_review_synthesis running on Composer 2.5 at roughly 1/10th of Opus token cost, with hard cost guards against the `composer-2.5-fast` upstream bug, read-only enforcement for parallel review phases, and no orphaned CLI processes. Windows behavior for existing providers is untouched.

**FRs covered:** FR1–FR15 (all — single unified epic per product owner decision)
**NFRs addressed:** NFR1–NFR5 (woven into story acceptance criteria, not separate stories)

### FR Coverage Map (Extension)

| FR | Epic | Story area |
|----|------|-----------|
| FR14 | Epic 11 | Platform fix — SIGTERM→SIGKILL escalation (first story, independent) |
| FR9, FR10 | Epic 11 | Integration plumbing — binary resolution + config schema |
| FR1–FR3, FR5–FR7, FR12, FR13 | Epic 11 | Provider core — class, invocation, NDJSON parsing, errors, timeouts |
| FR4 | Epic 11 | Mode selection + read-only enforcement (deny-config lifecycle + cleanup sweep) |
| FR8 | Epic 11 | Cost guard + version logging |
| FR11 | Epic 11 | Validator compatibility (Evidence Score path) |
| FR15 | Epic 11 | Linux deployment docs + spike checklist |

All 15 FRs mapped. Story breakdown follows the architecture's implementation sequence (D12 → provider core → mode/guards → docs), closing with the mandatory Epic Documentation Sync story per `_epic-template.md`. Full story specifications (Current/Target State, Technical Notes, dependencies, test impact): `epic-11.md`.

## Epic 11: Cursor Provider — Composer 2.5 as Master LLM on Linux

Add the Cursor CLI as a fourth provider so Composer 2.5 can run master phases on a Linux box at ~1/10th Opus cost, with cost guards, read-only enforcement for parallel review, and one platform fix (SIGKILL escalation). Zero Windows regression.

### Story 11.1: SIGTERM→SIGKILL Escalation in Unix Process Termination

As a developer running bmad-assist-lite on Linux,
I want hung provider processes force-killed after a grace period,
So that a stuck `agent` CLI can never orphan a dev run.

**Acceptance Criteria:**

**Given** a Unix process that ignores SIGTERM
**When** `terminate_process(pid)` is called
**Then** after at most `SIGTERM_GRACE_SECONDS` (5s) `os.killpg(pgid, SIGKILL)` is sent and `True` is returned

**Given** the process exits within the grace period after SIGTERM
**When** escalation logic polls liveness
**Then** SIGKILL is never sent

**Given** the platform is Windows
**When** `terminate_process(pid)` is called
**Then** the `taskkill` path behaves identically to before (NFR1)

### Story 11.2: Cursor CLI Resolution & Config Schema

As a developer,
I want `provider: cursor` accepted in configuration and the Cursor CLI binary resolvable,
So that the provider can be configured before and independently of its implementation.

**Acceptance Criteria:**

**Given** both `cursor-agent` and `agent` exist on PATH with no config override
**When** `resolve_cli_path("cursor")` is called
**Then** `cursor-agent` is preferred over `agent` in every resolution tier

**Given** a config with `master: {provider: cursor, model: composer-2.5}`
**When** the config is loaded
**Then** validation passes; unknown provider names are still rejected

### Story 11.3: CursorProvider Core — Invocation, Streaming, Errors

As a developer using bmad-assist-lite,
I want a CursorProvider that invokes `agent -p --output-format stream-json` and returns Composer 2.5's response,
So that Cursor models can run master phases in dev loops.

**Acceptance Criteria:**

**Given** a mocked stream (init → assistant → tool → result events)
**When** `invoke()` is called
**Then** `ProviderResult` carries the result-event text, `session_id`, and the init-event model

**Given** the init event reports `composer-2.5-fast` when `composer-2.5` was requested
**When** the stream is parsed
**Then** a WARNING naming both models is logged and the actual model is recorded (NFR3)

**Given** malformed JSON lines or unknown event types in the stream
**When** parsing runs
**Then** no exception propagates (DEBUG log, skip)

**Given** the stream ends with no result event and non-zero exit
**When** `_do_invoke()` finalizes
**Then** `ProviderError` is raised carrying the tail of stderr; a non-zero exit AFTER a result event is logged and ignored

**Given** `allowed_tools=None` (master phase)
**When** the command is built
**Then** `--force --trust` are present; with a restricted list, `--force` is absent

**Given** `supports_model()` is called
**Then** `composer-*` models return True; `auto` and other vendors' models return False

### Story 11.4: Read-Only Mode & Deny-Config Lifecycle

As a developer running parallel multi-LLM code review,
I want Cursor validator invocations physically unable to write files or execute shell commands,
So that the multi-LLM safety constraint holds even against a misbehaving model.

**Acceptance Criteria:**

**Given** a read-only invocation in a cwd with no `.cursor/cli.json`
**When** the subprocess is prepared
**Then** a deny-config (`Write(**)`, `Shell(**)`) is created atomically with an ownership marker, and `_cleanup()` removes both

**Given** a user-authored `.cursor/cli.json` already exists
**When** a read-only invocation runs
**Then** the file is never touched; fallback layers (no `--force` + prompt restriction warning) still apply

**Given** a crash left deny file + marker behind
**When** the next run's resume cleanup executes
**Then** both are removed so write-mode master runs are unaffected

### Story 11.5: Linux Deployment Documentation & Spike Checklist

As a developer setting up the dedicated Linux box,
I want a step-by-step deployment guide with validation gates and the spike checklist,
So that the migration is reproducible and verified before real epics run.

**Acceptance Criteria:**

**Given** a fresh Linux box and `docs/linux-deployment.md`
**When** the steps are followed
**Then** every command is copy-pasteable: CLI install, `CURSOR_API_KEY` in `.env`, venv bootstrap

**Given** the spike checklist
**When** documented
**Then** S5 (`agent --list-models`) is ordered first as the premise gate, and S1–S4 each carry exact commands, expected outcomes, and the decision each result feeds

### Story 11.6: Epic Documentation Sync

As a developer (human or AI),
I want project documentation to reflect everything built in Epic 11,
So that future implementation decisions are based on accurate information.

**Acceptance Criteria:**

**Given** all implementation stories are complete
**When** the documentation sync executes
**Then** CLAUDE.md (provider list, Changing Models, config examples, Key Patterns) and project-context.md (new conventions) are updated; planning artifacts are not touched
