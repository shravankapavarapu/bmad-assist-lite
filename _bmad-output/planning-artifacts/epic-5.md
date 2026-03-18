---
stepsCompleted: []
inputDocuments:
  - 'prd.md'
  - 'architecture.md'
  - 'epics.md'
---

# bmad-assist-lite-parallel-stories - Epic 5 Breakdown

## Epic 5: Resilience & Recovery

**Epic ID:** Epic-5
**Created:** 2026-03-17
**Status:** Draft
**Priority:** High
**Points:** 13
**Stories:** 5

### Overview

Build crash recovery, blocked story handling, and management CLI. After this epic, the system survives crashes and resumes cleanly. Orphaned worktrees are detected and pruned. Blocked stories are tracked with dependency cascade prevention. User can `parallel status` and `parallel unblock` to manage execution.

### Business Goal

Make parallel execution production-ready by handling all failure modes gracefully and giving users visibility and control.

### Strategic Context

- Essential for production use — without recovery, any crash loses all progress
- Builds on state persistence from Epic 3
- CLI commands provide operational control for monitoring and unblocking
- Blocked story cascade prevention keeps the pipeline moving where possible

### Dependencies

- Epic 3 (orchestrator core, state persistence, worktree manager)
- Epic 4 (merge results feed into blocked status)

### Context7 Library Documentation

<!-- No external libraries — uses existing project infrastructure -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Crash Recovery; Blocked Story Handling; State Persistence; Parallel Module Layout; Enforcement Guidelines |
| `prd.md` | Functional Requirements; Non-Functional Requirements |
| `project-context.md` | `(full)` |

### Recommended Story Order

1. 5-1-crash-recovery-and-resume-in-flight-stories - Foundation: recovery logic needed before other resilience features
2. 5-2-orphan-detection-and-worktree-pruning - Builds on 5.1's startup recovery flow
3. 5-3-blocked-story-handling-and-dependency-cascade - Depends on recovery and integrates with dependency graph
4. 5-4-status-cli-command - Read-only CLI command, depends on state model from 5.1
5. 5-5-unblock-cli-command - Depends on blocked handling from 5.3

---

### Story 5.1: Crash Recovery & Resume In-Flight Stories

**Story ID:** 5-1-crash-recovery-and-resume-in-flight-stories
**Component:** `src/bmad_assist_lite/parallel/recovery.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** []

#### User Story

As a developer,
I want the orchestrator to resume in-flight stories after a crash,
So that no work is lost when the process is interrupted unexpectedly.

#### Description

Create `recovery.py` with startup recovery logic. On orchestrator start, read `parallel-state.yaml` and reconcile it with actual worktree state on disk. In-flight stories with existing worktrees are resumed. In-flight stories with missing worktrees are reset to backlog. Done and blocked stories are preserved.

#### Current State

No crash recovery exists. If the orchestrator process dies, `parallel-state.yaml` may show stories as `in-flight` that are no longer running.

#### Target State

- `recover_state(state, project_root)` reconciles parallel-state with filesystem reality
- In-flight + worktree exists: spawn loop with `--resume` flag in existing worktree
- In-flight + worktree missing: reset to `backlog`, log warning
- Done/blocked: preserved as-is
- Recovery completes within 30 seconds (NFR3)

#### Acceptance Criteria

**Given** `parallel-state.yaml` shows story 3.2 as `in-flight` with worktree `parallel/3-2`
**When** the orchestrator starts and the worktree exists on disk
**Then** the orchestrator spawns the loop in the existing worktree with `--resume` flag
**And** the loop reads its own `state.yaml` and resumes from the last completed phase

**Given** `parallel-state.yaml` shows story 3.2 as `in-flight`
**When** the orchestrator starts and the worktree does NOT exist on disk (orphaned state)
**Then** the story status is reset to `backlog` in `parallel-state.yaml`
**And** a warning is logged: "Story 3.2 was in-flight but worktree missing -- reset to backlog"

**Given** the orchestrator restarts after a crash
**When** it reaches consistent state
**Then** the recovery completes within 30 seconds (NFR3)
**And** stories marked `done` remain `done`
**And** stories marked `blocked` remain `blocked`

#### Technical Notes

- Recovery runs at startup only, not during runtime
- Uses `worktree_manager.list_worktrees()` to enumerate actual worktrees on disk
- Cross-references against `ParallelState.stories` dict
- `--resume` flag reuses existing loop resume mechanism (reads `state.yaml` in worktree)
- NFR2: no story work is lost — worktree branches and per-worktree state.yaml preserve committed progress

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 5.2: Orphan Detection & Worktree Pruning

**Story ID:** 5-2-orphan-detection-and-worktree-pruning
**Component:** `src/bmad_assist_lite/parallel/recovery.py`
**Estimate:** Small
**Points:** 2
**Priority:** High
**Dependencies:** [Story 5.1]

#### User Story

As a developer,
I want stale worktrees detected and cleaned on startup,
So that disk space isn't wasted and git state stays clean.

#### Description

Extend the startup recovery flow to detect orphaned worktrees (exist on disk but not tracked in state, or tracked as `done` but not cleaned up). Prune stale git worktree references and clean up orphans.

#### Current State

Recovery from Story 5.1 handles state-vs-disk reconciliation for in-flight stories, but does not clean up orphaned worktrees from previous runs.

#### Target State

- `prune_worktrees(state, project_root)` runs `git worktree prune` then enumerates worktrees
- Orphan detection: worktree on disk with no matching state entry, or state shows `done`
- Orphaned worktrees cleaned up via `cleanup_worktree()`
- Warning logged for each orphan cleaned
- Cleanup completes within 10 seconds per worktree (NFR8)

#### Acceptance Criteria

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

#### Technical Notes

- `git worktree prune` first to clean git's internal state
- `git worktree list --porcelain` for machine-readable worktree enumeration
- Parse porcelain output: `worktree <path>\nHEAD <sha>\nbranch refs/heads/<name>\n\n`
- Filter for `parallel/` prefixed branches to identify parallel worktrees
- Uses `cleanup_worktree()` from `worktree_manager.py` for actual cleanup

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 5.3: Blocked Story Handling & Dependency Cascade

**Story ID:** 5-3-blocked-story-handling-and-dependency-cascade
**Component:** `src/bmad_assist_lite/parallel/orchestrator.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [Story 5.1]

#### User Story

As a developer,
I want blocked stories to prevent their dependents from starting while non-dependent stories continue,
So that failures are isolated and the pipeline keeps moving where possible.

#### Description

Add blocked story handling to the orchestrator. Stories can be blocked from multiple sources: worktree QG failure, unresolvable merge conflicts, or post-merge QG failure. Blocked stories feed into `dependency_graph.get_ready_stories()` to prevent dependents from starting. Non-dependent stories continue executing.

#### Current State

The orchestrator (from Epic 3) handles successful completions and basic failures, but has no blocked status concept or dependency cascade prevention.

#### Target State

- Stories marked `blocked` in `parallel-state.yaml` with error details
- Worktree cleaned up on block
- `get_ready_stories()` excludes stories whose dependencies are blocked (not `done`)
- Non-dependent stories continue executing normally
- Multiple block sources: worktree QG fail after retry, unresolvable merge conflicts, post-merge QG fail

#### Acceptance Criteria

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

#### Technical Notes

- `blocked` is a terminal state until `unblock` command resets it
- `blocked_ids` set passed to `dependency_graph.get_ready_stories()`
- Error details stored in `StoryState.error` field
- Worktree cleanup on block via `worktree_manager.cleanup_worktree()`
- Three block sources: (1) worktree subprocess exit code > 0 after retry, (2) `merger.py` conflict resolution failure, (3) post-merge QG failure after fix

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 5.4: Status CLI Command

**Story ID:** 5-4-status-cli-command
**Component:** `src/bmad_assist_lite/parallel/cli.py`
**Estimate:** Small
**Points:** 2
**Priority:** Medium
**Dependencies:** [Story 5.1]

#### User Story

As a developer,
I want to check the current state of a parallel run from another terminal,
So that I can monitor progress without watching the live console.

#### Description

Implement `bmad-assist-lite parallel status` command that reads `parallel-state.yaml` and displays a human-readable summary of the current parallel run state, including story statuses, durations, and dependency information.

#### Current State

No way to check parallel run progress without watching the live console output.

#### Target State

- `parallel status` reads `parallel-state.yaml` and displays formatted table
- Shows: story ID, status, duration (if in-flight or done), error (if blocked)
- Shows summary counts: done, in-flight, blocked, backlog
- Responds in <2 seconds (NFR10)
- Safe to run concurrently with active orchestrator (read-only)

#### Acceptance Criteria

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

#### Technical Notes

- Typer subcommand under `parallel` group
- Read-only: safe to run concurrently with orchestrator
- Uses `ParallelState` Pydantic model from `state.py` for parsing
- Duration calculated from `started_at` timestamp vs now (in-flight) or `completed_at` (done)
- Table formatting: simple aligned text output (no external table library)

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 5.5: Unblock CLI Command

**Story ID:** 5-5-unblock-cli-command
**Component:** `src/bmad_assist_lite/parallel/cli.py`
**Estimate:** Small
**Points:** 3
**Priority:** Medium
**Dependencies:** [Story 5.3]

#### User Story

As a developer,
I want to reset a blocked story to backlog so the orchestrator picks it up on the next run,
So that I can retry after manually fixing the underlying issue.

#### Description

Implement `bmad-assist-lite parallel unblock <story_id>` command that resets a blocked story to backlog status. Validates the story exists and is actually blocked before modifying state.

#### Current State

Blocked stories are permanent until the user manually edits `parallel-state.yaml`.

#### Target State

- `parallel unblock <story_id>` resets blocked story to `backlog`
- Validates story exists in state
- Validates story is actually `blocked` (not done, in-flight, etc.)
- Atomic write to `parallel-state.yaml`
- Confirmation message printed

#### Acceptance Criteria

**Given** `parallel-state.yaml` shows story 3.2 as `blocked`
**When** `bmad-assist-lite parallel unblock 3.2` is invoked
**Then** story 3.2 status changes to `backlog` in `parallel-state.yaml`
**And** confirmation is printed: "Story 3.2 unblocked -- will be picked up on next parallel run"

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

#### Technical Notes

- Typer subcommand under `parallel` group with `story_id` argument
- Load state via `load_state()`, validate, update via `model_copy()`, save via `save_state()`
- Atomic write to prevent corruption
- Unblocked story gets fresh worktree on next run (previous worktree was cleaned up on block)
- Error details cleared when unblocked

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
| `tests/test_recovery.py` | 5.1, 5.2 | New: crash recovery, orphan detection, worktree pruning |
| `tests/test_blocked_handling.py` | 5.3 | New: blocked status, dependency cascade, multiple block sources |
| `tests/test_parallel_cli.py` | 5.4, 5.5 | Extended: status command, unblock command, error cases |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 5.1 | None | — | — | Startup recovery logic |
| 5.2 | None | — | — | Git worktree cleanup |
| 5.3 | None | — | — | Orchestrator internals |
| 5.4 | None | — | — | CLI command |
| 5.5 | None | — | — | CLI command |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests written and passing (`pytest -q --tb=short --no-header`)
- [ ] All code passes mypy strict mode (`mypy src/`)
- [ ] All code passes ruff linting (`ruff check src/`)
- [ ] All code passes ruff formatting (`ruff format --check src/`)
- [ ] Existing test suite still passes (NFR17)
- [ ] Crash recovery resumes in-flight stories with existing worktrees
- [ ] Orphaned worktrees detected and cleaned on startup
- [ ] Blocked stories prevent dependent stories from starting
- [ ] Non-dependent stories continue when one is blocked
- [ ] `parallel status` shows current state in <2 seconds
- [ ] `parallel unblock` resets blocked stories to backlog
- [ ] Recovery reaches consistent state within 30 seconds (NFR3)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Recovery logic misidentifies worktree state | Medium | High | Cross-reference git worktree list with filesystem and state file |
| Orphan cleanup removes active worktree | Low | High | Only clean worktrees not in `in-flight` state; verify no running process |
| Blocked cascade prevents all progress | Medium | Medium | Non-dependent stories continue; `unblock` command provides escape hatch |
| State file corruption during crash | Low | High | Atomic writes; corrupt file detection with fallback to fresh state |
| `--resume` flag behavior differs in worktree | Low | Medium | Test resume in worktree context; same state.yaml format used |

## Rollback Plan

All changes are in new files (`parallel/recovery.py`) and extensions to existing parallel module files. Rollback by:
1. Remove `recovery.py` and test files
2. Remove blocked handling logic from `orchestrator.py`
3. Remove `status` and `unblock` commands from `parallel/cli.py`
4. Orchestrator would still function but without crash recovery or blocked handling
