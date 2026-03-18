---
stepsCompleted: ['step-01-init', 'step-02-discovery', 'step-02b-vision', 'step-02c-executive-summary', 'step-03-success', 'step-04-journeys', 'step-05-domain-skipped', 'step-06-innovation-skipped', 'step-07-project-type', 'step-08-scoping', 'step-09-functional', 'step-10-nonfunctional', 'step-11-polish', 'step-12-complete']
inputDocuments:
  - 'requirements-parallel-story-execution.md'
  - 'project-context.md'
  - 'CLAUDE.md'
workflowType: 'prd'
documentCounts:
  briefs: 0
  research: 0
  requirements: 1
  projectDocs: 1
  projectContext: 1
classification:
  projectType: 'cli_tool / developer_tool'
  domain: 'developer_tooling / ai_assisted_development'
  complexity: 'medium-high'
  projectContext: 'brownfield'
---

# Product Requirements Document - bmad-assist-lite-parallel-stories

**Author:** Shravan
**Date:** 2026-03-17

## Executive Summary

bmad-assist-lite is a Windows-native Python CLI tool that automates the BMAD methodology by coordinating Claude Code CLI and Gemini CLI through a 10-phase development loop: create story, validate, synthesize, implement, code-review, synthesize-review, quality-gate, fix-quality-gate, epic-quality-gate, and retrospective. The existing sequential pipeline processes stories one at a time — each story completes its full 7-phase cycle before the next begins.

This enhancement introduces **parallel story execution**: an orchestration layer that runs multiple independent stories concurrently using git worktrees for isolation. The existing loop runs unchanged inside each worktree — one story per worktree, full 7-phase pipeline. A dependency resolver (Kahn's topological sort) determines which stories can safely run in parallel based on declared dependencies in epic files. A merger agent (Claude CLI) merges completed stories back to the base branch with post-merge quality gates to catch integration issues. The target user is the developer running bmad-assist-lite on their own projects, seeking to reduce wall-clock time for multi-story epics from sequential (N × T) to parallel (bounded by dependency depth and concurrency limit).

### What Makes This Special

The core architectural insight: the proven sequential loop is the unit of work — don't modify it for parallelism, replicate it in isolated worktrees and coordinate above. This yields zero changes to the existing 7-phase pipeline (create, validate, synthesize, dev, code-review, synthesis, quality-gate) while gaining concurrent execution. Each worktree gets its own branch, its own state file, its own quality gates — complete isolation. The orchestrator layer handles dependency resolution, worktree lifecycle, sequential merge queue, and post-merge integration validation. When a merge introduces integration failures, the orchestrator runs fix_quality_gate on the base branch before allowing dependent stories to proceed, preventing cascade blocking.

## Project Classification

- **Type:** CLI Tool / Developer Tool — Python (Typer) CLI with multi-LLM orchestration
- **Domain:** AI-Assisted Software Development — Coordinates Claude and Gemini for automated story implementation
- **Complexity:** Medium-High — Dependency graph algorithms, git worktree isolation, multi-process coordination, cross-platform (Windows + Unix), LLM-powered merge conflict resolution
- **Context:** Brownfield — Enhancing existing ~60 source file Python CLI. Existing loop unchanged; new parallel orchestration layer added above it

## Success Criteria

### User Success

- **Time reduction is tangible:** A 5-story epic with default concurrency (3) completes in roughly the wall-clock time of the longest dependency chain, not 5× sequential. The summary report quantifies time saved.
- **Hands-off execution:** User runs `bmad-assist-lite parallel run`, walks away. Stories execute, merge, and unblock dependents automatically. Manual intervention only on blocked stories.
- **Clear recovery path:** When a story blocks (QG failure, merge conflict), the user gets actionable output — which story, what failed, specific errors. `parallel unblock` + `parallel run` resumes cleanly.
- **Full visibility:** Live prefixed console output shows all parallel stories progressing. Orchestrator log provides post-run review. Per-story logs available for deep debugging.

### Business Success

- **Adoption on real epics:** Used on production projects (e.g., webdozo-v1) within the first week of completion. If it's not trusted enough to run on real work, it hasn't succeeded.
- **Net positive ROI per epic:** Wall-clock time saved exceeds the additional complexity of reviewing parallel merge results. The summary report proves this with concrete numbers.

### Technical Success

- **Zero regressions to existing sequential loop:** All existing tests pass. The loop runs identically in a worktree as it does today. The ~18 lines of existing code changes introduce no side effects.
- **Dependency resolution is correct:** Kahn's algorithm produces valid topological ordering. Circular dependencies detected and reported. No story starts before its dependencies are `done`.
- **Merge integrity:** Post-merge quality gate catches integration issues. fix_quality_gate resolves them at least some of the time. Failed fixes correctly mark stories as blocked rather than silently passing.
- **Crash resilience:** Orchestrator interrupted mid-run resumes cleanly — in-flight worktrees resume from their phase, completed stories stay done, orphaned worktrees are pruned.
- **Cross-platform:** Works on Windows (primary) and Unix. Git worktree operations, process management, and path handling are platform-safe.

### Measurable Outcomes

- Parallel run of a 5+ story epic completes without manual intervention (happy path)
- Summary report shows >30% wall-clock time reduction vs. sequential estimate
- Post-merge QG catches at least one integration issue that would have been missed without it (proves the safety net works)
- `parallel status` accurately reflects the state of all stories at any point during execution

## User Journeys

### Journey 1: Shravan Launches a Parallel Epic Run (Happy Path)

Shravan has just finished planning Epic 4 for webdozo-v1 — 6 stories, well-defined acceptance criteria, dependencies declared in the epic file. Stories 4.1 and 4.2 have no dependencies. Story 4.3 depends on 4.1. Stories 4.4 and 4.5 depend on 4.2. Story 4.6 depends on 4.3 and 4.5.

He checks out a fresh branch: `git checkout -b epic/4`. He runs `bmad-assist-lite parallel run`. The orchestrator reads the epic file, builds the dependency graph, and reports: "3 stories ready (4.1, 4.2), 4 stories blocked by dependencies. Max concurrency: 3. Creating worktrees..."

Two worktrees spin up. The console shows prefixed output — `[4.1|CREATE_STORY]` and `[4.2|DEV_STORY]` interleaving as both stories progress through their phases. Shravan glances at it occasionally but mostly works on something else.

Story 4.2 finishes first. `[ORCHESTRATOR] Story 4.2 complete, queuing merge...` The merger agent merges cleanly. Post-merge QG runs — all green. `[ORCHESTRATOR] Story 4.2 merged. Stories 4.4, 4.5 unblocked.` Two new worktrees appear for 4.4 and 4.5.

Story 4.1 finishes next, merges, unlocks 4.3. Now three stories run in parallel (4.3, 4.4, 4.5). They finish in sequence, each merging and passing post-merge QG. Finally 4.6 starts (depends on 4.3 + 4.5, both done), completes, merges.

`[ORCHESTRATOR] All stories complete. Running epic teardown...` Epic QG passes. Retrospective runs. The summary report shows: 6 stories, 2h 47m wall-clock, estimated sequential 6h 10m — **3h 23m saved (55% reduction)**. Shravan creates a PR from `epic/4` to `main`.

### Journey 2: Shravan Handles a Blocked Story (Recovery Path)

Mid-run on Epic 5, Story 5.3 passes all 7 phases in its worktree but the post-merge QG fails — 5.3's new auth middleware conflicts with 5.1's session handling that was merged earlier. The orchestrator runs fix_quality_gate. The LLM patches the type mismatch, QG re-runs, but tests still fail on an edge case.

`[QG|post-merge|5.3] FAIL: 1 of 4 gates failed`
`  test: FAIL (1 failing: auth.integration.spec.ts:112)`
`[ORCHESTRATOR] Story 5.3 marked blocked. Stories 5.5, 5.6 (depend on 5.3) will not start.`

Stories 5.4 (no dependency on 5.3) continues running normally. The orchestrator completes everything it can, then exits: "3 stories done, 1 blocked, 2 waiting on blocked dependency."

Shravan checks the orchestrator log, sees the specific test failure, opens the base branch, and fixes the edge case manually — a 5-minute fix. He runs `bmad-assist-lite parallel unblock 5.3`, then `bmad-assist-lite parallel run`. The orchestrator sees 5.3 as backlog again, creates a fresh worktree, and runs the full pipeline. This time everything passes. Stories 5.5 and 5.6 unlock and proceed.

### Journey 3: Shravan Monitors and Reviews a Run (Operations Path)

Shravan started a parallel run before a meeting. He opens a new terminal and runs `bmad-assist-lite parallel status`:

```
Epic 3 — Parallel Run (branch: epic/3)
  Story 3.1: done       (merged 10:42, duration: 38m)
  Story 3.2: in-flight  (phase: CODE_REVIEW, running 22m)
  Story 3.3: in-flight  (phase: DEV_STORY, running 15m)
  Story 3.4: backlog    (depends on: 3.1 ✓, 3.2 ⏳)
  Story 3.5: backlog    (depends on: 3.3 ⏳)

Active: 2/3 slots | Merge queue: empty
```

He sees 3.2 is in code review, 3.3 is in dev. 3.4 is waiting for 3.2 to finish. He checks the live console in the other terminal to see real-time output, then goes back to his meeting.

After the run completes, he opens `parallel-run.log` to review the orchestrator's decisions — which stories ran when, how long each merge took, whether any post-merge QG needed fixes. The summary report at the bottom gives him the total time saved.

### Journey Requirements Summary

| Journey | Capabilities Revealed |
|---------|----------------------|
| **Happy Path** | Dependency graph building, worktree creation, parallel loop execution, sequential merge queue, post-merge QG, dependency unlock, epic teardown, summary report |
| **Recovery** | Post-merge QG failure detection, fix_quality_gate on base branch, blocked story marking, dependency cascade blocking, manual unblock CLI, fresh worktree restart |
| **Operations** | `parallel status` CLI command, orchestrator log, live prefixed console output, summary report generation |

## Product Scope & Phased Development

### MVP Strategy

**Approach:** Problem-solving MVP — deliver the complete parallel execution capability in one release. The feature is only useful if all components work together (orchestrator without merger agent is useless; merger agent without post-merge QG is unsafe). No partial delivery.

**Resource:** Single developer (Shravan) using bmad-assist-lite itself for implementation.

### MVP Feature Set (Phase 1)

All three user journeys supported from day one. 10 components, no feature deferral:

1. **Parallel Orchestrator** — Dependency graph, scheduling scores, worktree lifecycle, process monitoring, drain mode, crash recovery via `parallel-state.yaml`
2. **Dependency Resolver** — Kahn's algorithm, cycle detection, scheduling scores (unblock potential + depth + priority), ready story discovery
3. **Worktree Manager** — Create/cleanup/prune git worktrees, branch naming convention, orphan detection
4. **Merger Agent** — Claude CLI merge + conflict resolution, sequential merge queue, post-merge QG, fix_quality_gate with tagged commits
5. **Sprint Status Manager** — Orchestrator-owned updates on base branch, reuses existing Pydantic models
6. **Orchestrator State** — `parallel-state.yaml` persistence, two-layer state separation (orchestrator + per-worktree loop)
7. **CLI Commands** — `parallel run`, `parallel status`, `parallel unblock <story>`
8. **Parallel Config** — `max_concurrency`, `stagger_delay`, `post_merge_fix_retries`, `worktree_base_dir`
9. **Observability** — Orchestrator log, per-story logs, live prefixed console, summary report with time-saved calculation, detailed post-merge QG failure logging
10. **Minimal existing code changes** — `--epic`/`--story`/`--single-story` CLI flags, `BMAD_PARALLEL_MODE` env var for sprint sync bypass (~18 lines)

### Growth Features (Phase 2)

- Shared cache / symlink optimization for worktree cold-start (`node_modules`, `venv`, build artifacts)
- Web dashboard for real-time visualization of parallel story progress and dependency graph
- Automatic unblock detection when user fixes a blocked story on the base branch
- Multi-epic parallel execution with cross-epic dependency tracking

### Vision (Phase 3)

- Distributed execution across machines / cloud instances for unlimited concurrency
- Smart concurrency tuning based on API rate limits, system resources, and historical completion times
- Merge conflict learning from past resolution patterns

### Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Merge conflicts too complex for Claude CLI | Medium | High | Merger agent gets full story context + conflict markers. Post-merge QG catches failures. User can manually fix blocked stories. |
| Git worktree operations fail on Windows | Low | High | Test worktree create/cleanup/prune on Windows early. Use `subprocess` with platform-safe kwargs (existing `_windows.py` patterns). |
| API rate limits with 3 concurrent stories | Medium | Medium | Stagger worktree starts (`stagger_delay`). Each story's phases are sequential, so peak concurrent LLM calls is 3. Existing retry logic handles transient rate limits. |
| Post-merge QG fix breaks other stories' code | Low | High | fix_quality_gate runs full test suite, not just the merging story's tests. If fix fails, story is blocked — no silent corruption. Epic QG is defense-in-depth. |
| Orchestrator crash loses progress | Medium | Medium | `parallel-state.yaml` persisted after every state change. Per-worktree `state.yaml` enables phase-level recovery. `git worktree prune` cleans orphans on restart. |

## CLI Tool Specific Requirements

### Command Structure

**New `parallel` subcommand group** extending the existing Typer CLI:

```
bmad-assist-lite parallel run             # Start/resume parallel orchestrator
bmad-assist-lite parallel status          # Show current parallel execution state
bmad-assist-lite parallel unblock <story> # Reset blocked story to backlog
```

All commands are non-interactive. `parallel run` is long-running (minutes to hours), streaming output to stdout. `parallel status` and `parallel unblock` are instant commands that read/write `parallel-state.yaml` and exit.

**Existing commands unchanged:** `run`, `init`, `compile`, `reset-lock`, `fetch-docs` continue to work as-is. The `run` command remains the sequential single-story mode.

### Output Formats

- **Live console:** Prefixed lines (`[story|phase]`, `[ORCHESTRATOR]`, `[MERGE|story]`, `[QG|post-merge]`) to stdout
- **Orchestrator log:** `parallel-run.log` — high-level events for post-run review
- **Per-story logs:** Existing run log format, one per worktree — detailed phase execution
- **Summary report:** Appended to orchestrator log on completion — timing, time saved, per-story results
- **Status command:** Human-readable table to stdout showing story states, phases, dependencies

### Configuration Schema

Extends existing `bmad-assist-lite.yaml` with new `parallel:` section:

```yaml
parallel:
  max_concurrency: 3          # Max parallel stories (1-5)
  stagger_delay: 10           # Seconds between worktree starts
  post_merge_fix_retries: 1   # fix_quality_gate attempts after merge
  worktree_base_dir: null     # Custom worktree location (default: adjacent to project)
```

No CLI flag overrides — all parallel configuration via YAML only. Validated by Pydantic model at config load time, consistent with existing config patterns.

### Installation & Compatibility

- **Package:** Existing `pip install -e .` — no new system dependencies
- **Python:** 3.11+ (same as existing)
- **Git:** 2.5+ required for worktree support (widely available)
- **Platform:** Windows (primary) + Unix. Platform-safe git, process, and path handling

### Implementation Considerations

- **Typer subcommand group:** `parallel` registered as a sub-app on the existing Typer `app`
- **Config validation:** `ParallelConfig` Pydantic model with `ConfigDict(frozen=True)`
- **Error handling:** New `ParallelError` subclass of existing `BmadAssistError` hierarchy
- **Logging:** `logging.getLogger(__name__)` pattern. Console output via `write_progress()` for thread safety

## Functional Requirements

### Dependency Resolution

- FR1: Orchestrator can parse story dependencies from epic markdown files using the existing `**Dependencies:** Story X.Y` format
- FR2: Orchestrator can build a directed acyclic graph (DAG) from parsed story dependencies
- FR3: Orchestrator can detect circular dependencies and report them to the user before execution begins
- FR4: Orchestrator can determine which stories are ready to execute (all dependencies satisfied, not in-flight, not blocked)
- FR5: Orchestrator can compute scheduling scores to prioritize stories that unblock the most downstream work
- FR6: Orchestrator can re-evaluate ready stories after each story completion and merge

### Parallel Execution

- FR7: Orchestrator can create git worktrees from the current base branch for each parallel story
- FR8: Orchestrator can spawn the existing loop process in each worktree targeting a specific epic and story
- FR9: Orchestrator can run up to N stories concurrently where N is configurable (default 3, max 5)
- FR10: Orchestrator can stagger worktree starts by a configurable delay to avoid API rate limit spikes
- FR11: Orchestrator can monitor worktree loop processes and detect completion (success or failure) via exit codes
- FR12: Each worktree loop can execute the full 7-phase pipeline independently with its own state.yaml
- FR13: Worktree loops can operate without updating sprint-status.yaml (parallel mode bypass)

### Merge & Integration

- FR14: Orchestrator can queue completed stories for sequential merge (one at a time)
- FR15: Merger agent can merge a worktree branch into the base branch using git merge
- FR16: Merger agent can invoke Claude CLI to resolve merge conflicts with story context
- FR17: Orchestrator can run the full project quality gate (lint, typecheck, build, test) on the base branch after each merge
- FR18: Orchestrator can invoke fix_quality_gate on the base branch when post-merge QG fails
- FR19: Orchestrator can re-run quality gate after fix_quality_gate and determine pass/fail
- FR20: Orchestrator can commit post-merge fixes with tagged messages for traceability

### State Management

- FR21: Orchestrator can persist its state to parallel-state.yaml including story statuses, worktree references, and timestamps
- FR22: Orchestrator can read parallel-state.yaml on startup to determine what's done, in-flight, and blocked
- FR23: Orchestrator can resume in-flight stories by detecting existing worktrees and restarting loops with resume flag
- FR24: Orchestrator can detect orphaned worktrees (in-flight status but no worktree on disk) and reset them to backlog
- FR25: Orchestrator can prune stale git worktree references on startup

### Sprint Status & Story Lifecycle

- FR26: Orchestrator can update sprint-status.yaml on the base branch when stories complete (after merge + QG)
- FR27: Orchestrator can transition story status through: backlog → in-flight → merging → done (or blocked)
- FR28: Orchestrator can update epic status when all stories in the epic are complete
- FR29: Orchestrator can run epic teardown phases (epic_quality_gate, retrospective) on the base branch after all stories merge

### Failure Handling

- FR30: Orchestrator can mark a story as blocked when its worktree loop fails quality gate after retry
- FR31: Orchestrator can mark a story as blocked when merge conflicts are unresolvable
- FR32: Orchestrator can mark a story as blocked when post-merge QG fails after fix_quality_gate attempt
- FR33: Orchestrator can prevent dependent stories from starting when their dependencies are blocked
- FR34: Orchestrator can continue executing non-dependent stories when one story is blocked
- FR35: Orchestrator can clean up worktrees for both completed and blocked stories

### CLI Commands

- FR36: User can start or resume parallel execution via `bmad-assist-lite parallel run`
- FR37: User can view current parallel execution state via `bmad-assist-lite parallel status`
- FR38: User can reset a blocked story to backlog via `bmad-assist-lite parallel unblock <story>`
- FR39: Orchestrator can refuse to run on main/master branch and inform the user to use a feature branch

### Graceful Shutdown

- FR40: Orchestrator can handle Ctrl+C by stopping new story spawning and draining running stories
- FR41: Orchestrator can persist state before shutdown so the next run can resume
- FR42: Orchestrator can report blocked stories and their unmet dependencies on exit

### Observability

- FR43: Orchestrator can write high-level events to an orchestrator log file (parallel-run.log)
- FR44: Orchestrator can stream prefixed live output from all worktrees to the console
- FR45: Orchestrator can generate a summary report on completion with per-story timing and time saved
- FR46: Orchestrator can log detailed post-merge QG failure information (which gates failed, specific errors)
- FR47: Status command can display story states, current phases (by peeking at worktree state files), and dependency status

### Configuration

- FR48: User can configure max concurrency via `parallel.max_concurrency` in bmad-assist-lite.yaml
- FR49: User can configure stagger delay via `parallel.stagger_delay` in bmad-assist-lite.yaml
- FR50: User can configure post-merge fix retries via `parallel.post_merge_fix_retries` in bmad-assist-lite.yaml
- FR51: User can configure custom worktree location via `parallel.worktree_base_dir` in bmad-assist-lite.yaml

### Existing Code Integration

- FR52: Existing loop can accept `--epic` and `--story` CLI flags to target a specific story
- FR53: Existing loop can exit after completing a single story when invoked with `--single-story` flag
- FR54: Existing sprint sync can be bypassed when `BMAD_PARALLEL_MODE` environment variable is set

## Non-Functional Requirements

### Reliability & Data Integrity

- NFR1: Orchestrator state (parallel-state.yaml) must survive process crashes — state persisted after every status transition using atomic write pattern (temp file + `os.replace()`)
- NFR2: No story work is lost on orchestrator crash — worktree branches and per-worktree state.yaml preserve all committed progress
- NFR3: Orchestrator restart after crash must reach consistent state within 30 seconds (read state, prune orphans, resume)
- NFR4: Git operations (worktree create, merge, branch delete) must be atomic — partial failures must not leave the repository in a broken state
- NFR5: Concurrent worktree loops must not interfere with each other — complete filesystem and git branch isolation

### Performance & Overhead

- NFR6: Orchestrator overhead (scheduling, state management, process monitoring) must be negligible compared to story execution time — target <1% of total wall-clock time
- NFR7: Worktree creation must complete within 30 seconds for typical project sizes (including git checkout)
- NFR8: Worktree cleanup must complete within 10 seconds per worktree
- NFR9: Dependency graph computation (Kahn's algorithm + scheduling scores) must complete in <1 second for epics with up to 50 stories
- NFR10: `parallel status` command must respond in <2 seconds

### Platform Compatibility

- NFR11: All git operations must work on Windows (primary) and Unix; platform-specific code isolated in dedicated modules (following existing `_windows.py` pattern)
- NFR12: Path handling must use `pathlib.Path` throughout — no hardcoded path separators
- NFR13: Process spawning must use platform-safe subprocess kwargs (existing `get_subprocess_kwargs()` pattern)
- NFR14: File locking and atomic writes must work correctly on both NTFS (Windows) and ext4/APFS (Unix)

### Integration Contracts

- NFR15: Orchestrator must not modify any existing loop code behavior — worktree loops produce identical results to direct sequential execution
- NFR16: Claude CLI invocation for merge conflict resolution must use the same authentication mechanism as the existing Claude SDK provider
- NFR17: All new code must pass the existing test suite (`pytest -q --tb=line --no-header`) plus new parallel-specific tests
- NFR18: All new code must pass `mypy` strict mode and `ruff` linting with the existing project configuration
