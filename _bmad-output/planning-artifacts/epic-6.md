---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories', 'step-04-final-validation']
inputDocuments:
  - 'prd.md'
  - 'architecture.md'
  - 'epics.md'
---

# bmad-assist-lite-parallel-stories - Epic 6 Breakdown

## Epic 6: Observability & Epic Teardown

**Epic ID:** Epic-6
**Created:** 2026-03-17
**Status:** Draft
**Priority:** Medium
**Points:** 13
**Stories:** 4

### Overview

Build full logging, reporting, and epic completion. After this epic, the orchestrator writes a structured log file, generates a summary report with per-story timing and time saved, logs detailed post-merge QG failures, shows current phases in status display, and runs epic teardown (epic_quality_gate + retrospective) after all stories merge.

### Business Goal

Provide complete visibility into parallel runs and ensure epics are properly validated and documented before completion.

### Strategic Context

- Final epic — polish and completeness
- Observability is essential for debugging production issues
- Epic teardown reuses existing teardown phases from the sequential loop
- Summary report quantifies the value of parallel execution (time saved)

### Dependencies

- Epic 4 (merge results, post-merge QG)
- Epic 5 (status command enhanced, blocked story data)

**FRs covered:** FR29, FR43, FR44, FR45, FR46, FR47

### Context7 Library Documentation

<!-- No external library documentation needed for this epic.
     All functionality uses Python stdlib (logging module) and existing bmad-assist-lite internals.
     No third-party libraries are introduced, mocked, or integrated. -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|
| *(none)* | — | — | — |

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Observability; Epic Teardown; Parallel Module Layout; Enforcement Guidelines |
| `prd.md` | Functional Requirements; Non-Functional Requirements |
| `project-context.md` | `(full)` |

### Recommended Story Order

1. 6-1-orchestrator-log-file — Foundation logging infrastructure used by all other stories
2. 6-2-enhanced-status-display-with-phase-info — Enhances existing status command (depends on log infrastructure patterns)
3. 6-3-summary-report-generation — Requires log file infrastructure to append summary
4. 6-4-epic-teardown-phases — Final integration; requires summary report and logging for teardown output

---

### Story 6.1: Orchestrator Log File

**Story ID:** 6-1-orchestrator-log-file
**Component:** `src/bmad_assist_lite/parallel/logging.py`
**Estimate:** Small
**Points:** 2
**Priority:** Medium
**Dependencies:** []

#### User Story

As a developer,
I want high-level orchestrator events written to a log file,
So that I can review what happened during a parallel run after it completes.

#### Description

Create the orchestrator logging infrastructure using Python's `logging` module. A dedicated `FileHandler` writes orchestrator-level events to `parallel-run.log` in the project root. The log captures all significant orchestrator decisions and outcomes using structured prefixes and appropriate log levels.

#### Current State

No orchestrator log file exists. Console output via `write_progress()` is the only observability channel for parallel runs.

#### Target State

`parallel-run.log` is created (or appended) on each parallel run, containing timestamped orchestrator events with `[ORCHESTRATOR]` prefix, detailed merge QG failure information, and structured log levels (INFO/WARNING/ERROR).

#### Acceptance Criteria

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

#### Technical Notes

- Python `logging` module with `FileHandler` (append mode)
- Log to `parallel-run.log` in project root (via `get_paths()`)
- Follows existing `logger = logging.getLogger(__name__)` pattern at module top
- Log prefix convention from architecture: `[ORCHESTRATOR]`, `[MERGE|{story}]`, `[QG|post-merge|{story}]`
- Log levels: INFO for state transitions, WARNING for recoverable issues, ERROR for failures, DEBUG for subprocess commands
- FileHandler uses UTF-8 encoding for cross-platform consistency

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI.

---

### Story 6.2: Enhanced Status Display with Phase Info

**Story ID:** 6-2-enhanced-status-display-with-phase-info
**Component:** `src/bmad_assist_lite/parallel/cli.py`
**Estimate:** Small
**Points:** 3
**Priority:** Medium
**Dependencies:** [Story 6.1]

#### User Story

As a developer,
I want the status command to show which phase each in-flight story is currently in,
So that I can see detailed progress beyond just "in-flight."

#### Description

Enhance the `parallel status` CLI command (from Story 5.4) to peek at per-worktree `state.yaml` files and extract the current phase for in-flight stories. Also display dependency status with visual indicators. Gracefully handle unreadable state files.

#### Current State

Story 5.4's status command shows story statuses and durations but does not show the current phase for in-flight stories or dependency status indicators.

#### Target State

Status display includes current phase (e.g., `phase: CODE_REVIEW`) for in-flight stories by reading worktree state files, and shows dependency status with symbols: done, in-flight (pending), blocked.

#### Acceptance Criteria

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

#### Technical Notes

- Peek at worktree `state.yaml` is read-only — load via `yaml.safe_load()` with try/except for all failure modes
- Enhances Story 5.4's `parallel status` command implementation
- Dependency status symbols: checkmark (done), hourglass (in-flight), X (blocked)
- Response time must remain <2 seconds (NFR10) even with multiple worktree state reads
- Use `pathlib.Path` for all worktree path construction

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI.

---

### Story 6.3: Summary Report Generation

**Story ID:** 6-3-summary-report-generation
**Component:** `src/bmad_assist_lite/parallel/report.py`
**Estimate:** Medium
**Points:** 3
**Priority:** Medium
**Dependencies:** [Story 6.1]

#### User Story

As a developer,
I want a summary report generated when a parallel run completes,
So that I can see concrete time savings and per-story results.

#### Description

Generate a structured summary report when the orchestrator finishes (all stories done or blocked). The report includes per-story timing, wall-clock vs. sequential time comparison, merge statistics, and post-merge QG results. The report is appended to `parallel-run.log` and also printed to stdout.

#### Current State

No summary report is generated. The orchestrator exits without quantifying the results of the parallel run.

#### Target State

A comprehensive summary report is generated on completion, showing time saved, per-story timing, merge results, and QG outcomes. Written to both the log file and stdout.

#### Acceptance Criteria

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

#### Technical Notes

- Timing data sourced from `ParallelState` timestamps (`started_at`, `completed_at` per story)
- Sequential estimate = sum of individual story durations (started_at to completed_at)
- Wall-clock time = orchestrator start to orchestrator finish
- Report uses `write_progress()` for stdout output (existing output lock)
- Report appended to `parallel-run.log` via the logging infrastructure from Story 6.1
- Human-readable time formatting (e.g., "2h 47m", "38m", "55% reduction")

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI.

---

### Story 6.4: Epic Teardown Phases

**Story ID:** 6-4-epic-teardown-phases
**Component:** `src/bmad_assist_lite/parallel/orchestrator.py`
**Estimate:** Medium
**Points:** 5
**Priority:** High
**Dependencies:** [Story 6.3]

#### User Story

As a developer,
I want epic_quality_gate and retrospective to run on the base branch after all stories merge,
So that the full project is validated and lessons are captured before the epic is considered complete.

#### Description

After all stories in an epic reach `done` status, the orchestrator triggers epic teardown on the base branch. This reuses the existing sequential loop's `epic_quality_gate` and `retrospective` phases by spawning a loop subprocess targeting teardown. If any stories are blocked, teardown does not run and the orchestrator reports the incomplete state.

#### Current State

The parallel orchestrator completes after all stories are merged but does not run epic-level quality validation or retrospective.

#### Target State

Epic teardown (epic_quality_gate + retrospective) runs automatically on the base branch after all stories merge successfully. Epic status is updated in sprint-status. Summary report is generated after teardown.

#### Acceptance Criteria

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

#### Technical Notes

- Reuses existing epic teardown phases from sequential loop (`epic_quality_gate`, `retrospective`)
- Spawns loop subprocess on base branch: `sys.executable -m bmad_assist_lite run --epic N` targeting teardown phases
- `epic_quality_gate` runs full project test suite (lint, typecheck, build, test)
- Sprint-status update for epic `done` status via `sprint_status_manager`
- Teardown subprocess runs in the project root (not a worktree)
- Teardown output logged to `parallel-run.log` via orchestrator logging
- Summary report (Story 6.3) generated after teardown completes

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI.

---

## Test Impact Summary

### Unit / Integration Tests

| Test File | Stories Affected | Changes |
|-----------|------------------|---------|
| `tests/test_parallel_logging.py` | 6-1 | New: FileHandler setup, log prefix formatting, log header generation, QG failure detail logging |
| `tests/test_parallel_cli.py` | 6-2 | Update: Add tests for phase peek from worktree state.yaml, graceful fallback on unreadable state, dependency status symbols |
| `tests/test_parallel_report.py` | 6-3 | New: Summary report generation, time-saved calculation, per-story timing, blocked story listing, stdout output |
| `tests/test_orchestrator.py` | 6-4 | Update: Add tests for epic teardown trigger, teardown subprocess spawning, epic_quality_gate failure handling, epic done status update, blocked story skip |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 6.1 | None | — | — | CLI tool, no user-facing UI |
| 6.2 | None | — | — | CLI tool, no user-facing UI |
| 6.3 | None | — | — | CLI tool, no user-facing UI |
| 6.4 | None | — | — | CLI tool, no user-facing UI |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests updated and passing (`pytest -q --tb=line --no-header`)
- [ ] All new code passes `mypy src/` strict mode
- [ ] All new code passes `ruff check src/ && ruff format src/`
- [ ] `parallel-run.log` correctly captures orchestrator events with structured prefixes
- [ ] Status command displays current phase for in-flight stories
- [ ] Summary report shows accurate time-saved calculations
- [ ] Epic teardown runs `epic_quality_gate` + `retrospective` on base branch
- [ ] Sprint-status updated to `done` after successful epic teardown
- [ ] Documentation sync story completed (Tier 1 core docs verified current)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Worktree state.yaml locked during phase peek | Low | Low | Graceful fallback — show status without phase info. Read-only access with try/except. |
| Log file grows large on long runs | Low | Low | Append mode is fine for typical epic sizes (4-8 stories). Log rotation can be added post-MVP if needed. |
| Sequential time estimate inaccurate for blocked stories | Medium | Low | Blocked stories have no completion time — exclude from sequential estimate and note in report. |
| Epic teardown fails due to accumulated integration issues | Medium | Medium | Post-merge QG on each story provides defense-in-depth. Epic QG is the final safety net — failure is reported with details for manual fix. |

## Rollback Plan

All Epic 6 changes are additive — no existing behavior is modified. To revert:

1. Remove `parallel/logging.py` and `parallel/report.py` (new files)
2. Revert changes to `parallel/cli.py` (status enhancement)
3. Revert changes to `parallel/orchestrator.py` (epic teardown integration)
4. Remove new test files (`test_parallel_logging.py`, `test_parallel_report.py`)
5. Revert test changes in `test_parallel_cli.py` and `test_orchestrator.py`

The parallel orchestrator will function without observability — it will still run stories, merge, and track state. It just won't write logs, generate reports, show phases in status, or run epic teardown.
