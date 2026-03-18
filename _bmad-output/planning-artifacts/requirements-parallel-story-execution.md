# Requirements: Parallel Story Execution for bmad-assist-lite

**Date:** 2026-03-17
**Status:** Reviewed by full BMAD team (Architect, Developer, SM, PM, QA, Analyst)
**Branch:** feature/parallel-story-execution

---

## Core Concept

Run multiple independent stories in parallel using **git worktrees** for isolation, with a **thin orchestration layer** above the existing loop. The existing 7-phase loop runs unchanged inside each worktree — one story per worktree.

---

## Architecture Diagram

```
Parallel Orchestrator (new code)
  │
  ├── Dependency Resolver: parse epic → build DAG → determine ready stories
  │
  ├── Worktree Manager: create/cleanup git worktrees from base branch
  │
  ├── [Worktree A] existing loop → Story 3.1 (all 7 phases, own state.yaml)
  ├── [Worktree B] existing loop → Story 3.2 (all 7 phases, own state.yaml)
  ├── [Worktree C] existing loop → Story 3.4 (all 7 phases, own state.yaml)
  │
  ├── Merge Queue (sequential, one at a time)
  │     ├── Merger Agent: Claude CLI merges worktree branch → base branch
  │     ├── Post-Merge Quality Gate: full project lint, typecheck, build, test
  │     └── Fix Quality Gate (if needed): LLM fixes integration issues, re-run QG
  │
  ├── Sprint Status Manager: orchestrator-owned, updates on base branch
  │
  ├── Observability: orchestrator log + per-story logs + live console + summary report
  │
  └── Epic Teardown: runs on base branch after all stories merged
```

---

## Key Design Decisions

### 1. No Changes to Existing Loop
Each worktree runs the full existing 7-phase pipeline as-is:
`create_story → validate_story → validate_story_synthesis → dev_story → code_review → code_review_synthesis → quality_gate`

The loop handles everything within the worktree: story creation, LLM invocations, quality gates, auto-commits, crash recovery.

### 2. Base Branch Strategy
- The orchestrator works on **whatever branch the user is on** (e.g., `epic/3`, `release/v2`)
- **Guard against main/master** — refuse to run parallel mode on main; require a PR to merge into main
- Worktree branches are created from the current base branch
- User can create separate branches for epics or combine multiple epics into one release branch

### 3. Story Creation in Worktrees
Each worktree creates its own story file during the create_story phase. No pre-creation step needed. The story file gets merged back to the base branch with the rest of the changes.

**Merge conflict risk in `_bmad-output/` is minimal:** Story files have unique names per story (e.g., `story-3-1.md`, `story-3-2.md`). `code-reviews/` and `story-validations/` are already gitignored. `sprint-status.yaml` is owned by the orchestrator and excluded from worktree merges. Real merge conflicts will be in project source code — handled by the merger agent.

### 4. Merge Strategy
- Merges happen **sequentially** (one at a time) through a merge queue
- A **merger agent** (Claude CLI with merge instructions) handles the merge + conflict resolution
- On merge failure (conflicts unresolvable), the story is marked **blocked** and dependents won't start until resolved
- Post-merge fix commits are tagged with clear messages (e.g., `fix: post-merge integration fix for story 3.3`) for traceability

### 5. Post-Merge Validation (Integration Safety)
After each merge to the base branch:

```
Merge Story → base branch (merger agent)
  ↓
Run Quality Gate on base branch (full project: lint, typecheck, build, test)
  ↓
  ├── PASS → Story done. Dependents unblocked. Next merge.
  │
  └── FAIL → Run fix_quality_gate (master LLM fixes integration issues on base branch)
              ↓
              Run Quality Gate again
              ├── PASS → Commit fix. Story done. Dependents unblocked.
              └── FAIL → Mark story blocked. Dependents can't start.
```

**Rationale:** Stories develop in isolation — each worktree's quality gate passes independently. But after merging, Story A's changes could break Story B's code. Without post-merge validation, integration failures cascade into blocking the entire dependency chain, defeating the purpose of parallel execution.

The quality gate runs the **full project test suite** (not just the story's tests) to catch cross-story integration issues. This reuses existing quality gate and fix_quality_gate components.

### 6. State Management (Two-Layer Separation)

**Two separate state files, two separate owners:**

| File | Owner | Location | Purpose |
|------|-------|----------|---------|
| `parallel-state.yaml` | Orchestrator | Base branch project root | Story-level tracking: which stories are in-flight, completed, merged, blocked. Dependency graph status. Merge queue. |
| `state.yaml` | Loop (per worktree) | Each worktree's `.bmad-assist-lite/` | Phase-level tracking: current phase, crash recovery within that story's 7-phase pipeline |

Neither layer needs to understand the other's internals. The orchestrator monitors worktree loop processes via exit codes, not by reading `state.yaml`. For optional status display, the orchestrator can peek at worktree state files (read-only, non-critical).

**`parallel-state.yaml` structure:**
```yaml
base_branch: epic/3
epic: 3
started_at: 2026-03-17T10:30:00
stories:
  "3.1":
    status: done           # backlog | in-flight | merging | done | blocked
    worktree: parallel/3-1
    started_at: 2026-03-17T10:30:00
    merged_at: 2026-03-17T11:45:00
  "3.2":
    status: in-flight
    worktree: parallel/3-2
    started_at: 2026-03-17T10:35:00
  "3.3":
    status: backlog
    depends_on: ["3.1"]
merge_queue: []
```

### 7. Sprint Status Ownership
- **Orchestrator owns sprint-status.yaml** on the base branch
- Sprint sync is **disabled** in worktree loops (via `BMAD_PARALLEL_MODE=1` env var)
- Orchestrator updates sprint-status when stories complete (after successful merge + QG)
- Reuses existing `SprintStatus` Pydantic model and YAML I/O from `core/sprint_status.py`

### 8. Dependency Resolution
- Dependencies parsed from epic files (`**Dependencies:** Story X.Y` format — already supported by parser)
- **Kahn's algorithm** (topological sort) with priority-aware heap determines execution order
- **Scheduling scores** prioritize stories that unblock the most downstream work:
  `Score = (1000 * unblock_potential) + (100 * depth_score) + (10 * priority)`
- Duration/story-point estimates are **not** factored into scheduling — threads self-balance naturally as shorter stories complete and pick up the next ready story
- Worktrees created **on-demand** as dependencies are satisfied (not all upfront)
- After a story merges, the orchestrator re-evaluates which stories are now unblocked
- New worktrees branch from the **updated** base branch (includes all previously merged stories)

### 9. Max Concurrency
- Configurable via `parallel.max_concurrency` in config YAML
- **Default: 3** stories in parallel
- Stagger worktree starts (configurable `parallel.stagger_delay`, default 5-10 seconds) to avoid API rate limit spikes

### 10. Epic Teardown
- `epic_quality_gate` and `retrospective` phases run on the **base branch** after all stories are merged
- This is the final integration check across all stories in the epic (defense-in-depth alongside post-merge QG)

### 11. Worktree Lifecycle
- **Created** when a story becomes ready (dependencies satisfied, capacity available)
- **Cleaned up** after successful merge + post-merge QG pass
- **Cleaned up** on story failure/blocked (changes already merged or user fixes manually)
- **Orphaned worktrees** detected and cleaned on orchestrator restart (`git worktree prune`)

### 12. Tool vs. Project Separation
bmad-assist-lite is installed in one directory (the tool). Worktrees are created in the **project directory** where `bmad-assist-lite init` and `bmad-assist-lite run` are invoked (e.g., `webdozo-v1/`). The tool directory is never worktree'd.

Worktree build artifact cold-start cost (e.g., `npm install`, `pip install` per worktree) is documented but not optimized in v1. Shared cache/symlink strategies are deferred to a future optimization pass. The tool supports multiple languages and platforms — any future caching must accommodate:
- Node.js projects (`node_modules/`, `.next/`, `pnpm` stores)
- Python projects (`venv/`, `__pycache__/`)
- Other language ecosystems (Java, Go, Rust, etc.)
- Cross-platform paths (Windows and Unix)

---

## Orchestrator Run Model

### Stateless Between Runs
Each orchestrator invocation is effectively a fresh run:
1. Read `parallel-state.yaml` — see what's done, blocked, backlog
2. Read epic file — get full dependency graph
3. Compute ready stories: backlog stories whose dependencies are all `done`
4. Blocked stories stay blocked unless user manually unblocked them
5. Create worktrees for ready stories, execute

### Restart / Crash Recovery
- On restart, orchestrator reads `parallel-state.yaml`
- Stories marked `in-flight` with existing worktrees: resume loop with restart flag (loop reads its own `state.yaml`, resumes from last phase)
- Stories marked `in-flight` without worktrees (orphaned): reset to `backlog`
- `git worktree prune` cleans stale worktree references

### Blocked Story Recovery
User workflow for fixing a blocked story:
1. Orchestrator run ends (some stories done, story 3.2 blocked)
2. User manually fixes the issue on the base branch
3. User runs `bmad-assist-lite parallel unblock 3.2`
4. User runs `bmad-assist-lite parallel run`
5. Orchestrator creates fresh worktree for 3.2, runs full 7-phase pipeline from scratch

---

## Minimal Changes to Existing Code

| Change | File | Scope | Lines |
|--------|------|-------|-------|
| Add `--epic` and `--story` CLI flags | `cli.py` | Story assignment to worktree loop | ~10 |
| Add `--single-story` exit behavior | `loop/runner.py` | Loop exits after assigned story completes | ~5 |
| Check `BMAD_PARALLEL_MODE` env var to skip sprint sync | `core/sprint_sync.py` | Prevent worktree loops from updating sprint-status | ~3 |

**Total: ~18 lines of changes to existing code.** Everything else is new code in the orchestrator layer.

---

## New Components to Build

### 1. Parallel Orchestrator (`parallel/orchestrator.py`)
Main coordination loop:
- Read epic file, build dependency graph
- Identify ready stories (dependencies satisfied)
- Create worktrees, spawn loops, monitor completion
- Queue merges, trigger post-merge validation
- Re-evaluate after each merge, spawn next batch
- Handle graceful shutdown (drain mode)
- Persist state to `parallel-state.yaml` for crash recovery

### 2. Dependency Resolver (`parallel/dependency_resolver.py`)
- Kahn's algorithm with priority-aware heap for topological sort
- Scheduling scores (unblocking potential + depth + priority)
- `are_dependencies_satisfied()` with pre-computed passing set
- Cycle detection (DFS + recursion stack)
- Safety limits (max dependencies per story, max depth)
- `get_ready_stories()` — stories with all deps satisfied, not in-flight, not blocked

### 3. Worktree Manager (`parallel/worktree_manager.py`)
- `create_worktree(story_id)` — branch from base, create worktree directory
- `cleanup_worktree(story_id)` — remove worktree and branch
- `prune_orphaned()` — detect and clean stale worktrees on restart
- Branch naming convention: `parallel/{epic}-{story}` (e.g., `parallel/3-1`)

### 4. Merger Agent (`parallel/merger.py`)
- Attempt `git merge` of worktree branch into base branch
- If conflicts: invoke Claude CLI with conflict markers + story context for resolution
- Post-merge quality gate: run full project QG on base branch
- Post-merge fix commits tagged with clear messages for traceability
- If QG fails: run fix_quality_gate (master LLM), re-run QG
- If still fails: mark story blocked, report to orchestrator
- Log detailed QG results to orchestrator log (which gates failed, specific errors)

### 5. Sprint Status Manager (`parallel/sprint_status_manager.py`)
- Orchestrator-owned sprint-status updates on base branch
- Reuses existing `SprintStatus` Pydantic model and YAML I/O from `core/sprint_status.py`
- Updates story status: `backlog → in-progress → done` (or `blocked`)
- Updates epic status when all stories complete

### 6. Parallel Config (`core/config.py` extension)
```yaml
parallel:
  max_concurrency: 3          # Max parallel stories (1-5)
  stagger_delay: 10           # Seconds between worktree starts
  post_merge_fix_retries: 1   # fix_quality_gate attempts after merge
  worktree_base_dir: null     # Custom worktree location (default: adjacent to project)
```

### 7. CLI Commands
```bash
bmad-assist-lite parallel run             # Start/resume parallel orchestrator
bmad-assist-lite parallel status          # Show parallel-state: done/blocked/backlog/in-flight
bmad-assist-lite parallel unblock <story> # Set a blocked story back to backlog for retry
```

### 8. Orchestrator State (`parallel/state.py`)
Persist orchestrator state to `parallel-state.yaml`:
- Which stories are in-flight (worktree exists)
- Which stories completed and merged
- Which stories are blocked
- Dependency graph status
- Merge queue state
- Timestamps for timing/reporting

---

## Observability

### Tiered Logging Architecture

1. **Orchestrator log** (`parallel-run.log`) — High-level events: story assignments, worktree creation, merge events, QG results, dependency unlocks, timing. Primary post-run review file.

2. **Per-story logs** (existing run logs, one per worktree) — Detail: phase execution, LLM output, quality gate results. Already exist — each worktree loop writes its own.

3. **Live console output** — Unified view with prefixed lines for real-time monitoring:
   ```
   [3.1|DEV_STORY]   Running quality gate: npm run lint...
   [3.2|CODE_REVIEW]  Validator A: Evidence Score -2.3 (EXCELLENT)
   [ORCHESTRATOR]     Story 3.1 complete, queuing merge...
   [MERGE|3.1]        Merging parallel/3-1 → epic/3... OK
   [QG|post-merge]    lint ✓  typecheck ✓  build ✓  test ✓
   [ORCHESTRATOR]     Story 3.3 unblocked, creating worktree...
   ```

4. **Summary report** on completion:
   - Total stories completed / blocked
   - Per-story timing (start → merge)
   - Wall-clock time (parallel) vs. estimated sequential time (sum of individual durations)
   - **Time saved** as a concrete number
   - Merge success/failure per story
   - Post-merge QG pass/fail per story

### Post-Merge QG Logging
Quality gate failures in the orchestrator log include specific details:
```
[QG|post-merge|3.3] FAIL: 2 of 4 gates failed
  typecheck: FAIL (3 errors in src/auth/middleware.ts)
  test: FAIL (2 failing: auth.spec.ts:45, auth.spec.ts:78)
  lint: PASS
  build: PASS
[FIX|post-merge|3.3] Attempting fix_quality_gate...
[QG|post-merge|3.3] PASS (all gates green after fix)
```

---

## Execution Flow (Happy Path)

```
1. User: bmad-assist-lite parallel run (on branch epic/3)
2. Orchestrator reads epic file, parses stories + dependencies
3. Builds dependency graph: Stories 3.1, 3.2 have no deps. Story 3.3 depends on 3.1.
4. Writes initial parallel-state.yaml
5. Creates worktrees for 3.1 and 3.2 (capacity=3, but only 2 ready)
6. Spawns existing loop in each worktree (--epic 3 --story 1 --single-story)
7. Story 3.1 completes first (all 7 phases pass in worktree)
8. Orchestrator queues merge for 3.1
9. Merger agent merges 3.1 branch → base branch
10. Post-merge QG runs on base branch → PASS
11. parallel-state.yaml updated: Story 3.1 = done
12. Sprint-status updated: Story 3.1 = done
13. Re-evaluate: Story 3.3 (depends on 3.1) is now unblocked
14. Create worktree for 3.3 (branches from updated base with 3.1's changes)
15. Story 3.2 completes, merge + post-merge QG → PASS
16. Story 3.3 completes, merge + post-merge QG → PASS
17. All stories done → run epic teardown on base branch
18. Summary report generated
19. User creates PR from epic/3 → main
```

---

## Failure Scenarios

### Story fails quality gate in worktree (existing behavior)
- fix_quality_gate attempts fix → re-run QG
- If still fails: story marked blocked in parallel-state.yaml, worktree cleaned up
- Dependent stories won't start
- User fixes manually, runs `parallel unblock`, then `parallel run`

### Merge conflicts unresolvable by merger agent
- Story marked blocked in parallel-state.yaml
- Worktree cleaned up
- Dependent stories won't start
- User resolves manually on base branch, runs `parallel unblock`, then `parallel run`

### Post-merge QG fails
- fix_quality_gate runs on base branch (one attempt)
- If fix succeeds: commit with clear message, QG re-runs, story done
- If fix fails: story marked blocked, dependents can't start
- User fixes manually, runs `parallel unblock`, then `parallel run`

### Orchestrator interrupted (Ctrl+C)
- Stop spawning new stories
- Wait for running stories to finish (drain mode)
- Save orchestrator state to parallel-state.yaml
- On next `parallel run`: read state, prune orphaned worktrees, resume in-flight stories, continue

### All remaining stories blocked by dependencies
- Orchestrator reports blocked stories and their unmet dependencies
- Exits with clear error message for manual intervention

---

## Decisions Log (Team Review)

| Topic | Decision | Rationale |
|-------|----------|-----------|
| Duration-based scheduling | Skip | Story point estimates are noisy. Threads self-balance — short stories finish and pick up next ready story. Unblocking potential is the right scheduling signal. |
| Sprint status intermediate states | Not needed | Orchestrator tracks story-level status. Phase-level detail lives in each worktree's state.yaml. Orchestrator can optionally peek for status display. |
| `_bmad-output/` merge conflicts | Non-issue | Story files have unique names. code-reviews/story-validations gitignored. sprint-status owned by orchestrator. |
| Build artifact cold-start | Document, defer optimization | Each worktree needs its own node_modules/venv. Shared cache strategies deferred to future optimization. |
| Post-merge fix traceability | Commit with tagged messages | e.g., `fix: post-merge integration fix for story 3.3` |
| Epic QG vs post-merge QG | Keep both | Post-merge QG catches per-story integration issues. Epic QG is defense-in-depth for the full epic. Track overlap to decide if one can be removed later. |
| Orchestrator run model | Stateless between runs | Each `parallel run` reads parallel-state.yaml, computes ready stories, goes. No persistent daemon. |
| Blocked story recovery | Manual unblock via CLI | User fixes issue, runs `parallel unblock <story>`, then `parallel run`. Story restarts full 7-phase pipeline from scratch. |

---

## Research References

- `_bmad-output/planning-artifacts/research-bmad-assist-parallel-capabilities.md` — Parent project (bmad-assist) analysis: sequential story execution, existing dependency parsing, multi-LLM parallelism within phases
- `_bmad-output/planning-artifacts/research-autoforge-parallel-execution.md` — AutoForge analysis: Kahn's algorithm, scheduling scores, subprocess-per-feature orchestrator, SQLite concurrency patterns
