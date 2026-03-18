---
stepsCompleted: []
inputDocuments:
  - 'prd.md'
  - 'architecture.md'
---

# bmad-assist-lite-parallel-stories - Epic 4 Breakdown

## Epic 4: Merge & Integration Safety

**Epic ID:** Epic-4
**Created:** 2026-03-17
**Status:** Draft
**Priority:** High
**Points:** 13
**Stories:** 4

### Overview

Build the merger agent and post-merge validation. After this epic, completed stories merge safely back to the base branch. Merge conflicts are resolved by Claude CLI. Post-merge quality gate catches integration issues. fix_quality_gate attempts auto-repair. Sprint-status updates after successful merge.

### Business Goal

Ensure parallel story outputs integrate safely, maintaining code quality and tracking accuracy throughout the merge pipeline.

### Strategic Context

- Without safe merging, parallel execution produces conflicts and integration failures
- Uses Claude CLI subprocess (--print) for conflict resolution — not SDK
- Reuses existing quality gate and fix_quality_gate workflow patterns
- Sequential merge queue prevents merge-on-merge conflicts

### Dependencies

- Epic 3 (orchestrator, state persistence, worktree manager)

### Context7 Library Documentation

<!-- No external library documentation needed for this epic.
     All components use standard library (asyncio, subprocess, pathlib) and existing
     bmad-assist-lite internals. Claude CLI is invoked as a subprocess, not a library. -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|
| — | — | — | — |

### Context Requirements

<!-- Declares which sections from planning documents are needed for story creation.
     Controls what context the automated story-creation pipeline loads. -->

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Merge Queue; Post-Merge Validation; Parallel Module Layout; Enforcement Guidelines |
| `prd.md` | Functional Requirements; Non-Functional Requirements |
| `ux-design-specification.md` | (skip) |
| `project-context.md` | (full) |

### Recommended Story Order

1. 4-1-sequential-merge-queue-and-git-merge — Foundation: merge queue and git merge mechanics needed by all other stories
2. 4-2-claude-cli-merge-conflict-resolution — Builds on 4.1: adds conflict resolution to the merge path
3. 4-3-post-merge-quality-gate — Builds on 4.1: validates base branch after each merge
4. 4-4-post-merge-fix-quality-gate-and-sprint-status-update — Builds on 4.3: handles QG failures and updates tracking

---

### Story 4.1: Sequential Merge Queue & Git Merge

**Story ID:** 4-1-sequential-merge-queue-and-git-merge
**Component:** `src/bmad_assist_lite/parallel/merger.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** []

#### User Story

As a developer,
I want completed stories queued and merged one at a time into the base branch,
So that merges never conflict with each other and the base branch stays stable.

#### Description

Implement the sequential merge queue that accepts completed stories and merges them one at a time into the base branch. This is the foundation of the merge pipeline — all subsequent stories (conflict resolution, post-merge QG, sprint-status updates) build on this.

#### Current State

Completed stories in worktrees have no mechanism to merge back to the base branch. The orchestrator detects story completion but has no merge path.

#### Target State

An `asyncio.Queue`-based merge queue accepts completed stories. An async lock ensures only one merge executes at a time. `git merge parallel/{epic}-{story}` runs on the base branch. On success, the worktree branch is cleaned up. On conflict, the merge is aborted and the conflict file list is captured for downstream resolution.

#### Acceptance Criteria

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

#### Technical Notes

- `asyncio.Queue` for merge ordering
- One merge at a time enforced by `asyncio.Lock`
- Uses `_run_git()` wrapper from `parallel/git_ops.py` (Epic 1)
- `git merge` with `check=False` since conflicts are expected non-error path
- Branch cleanup (`git branch -d`) only after successful merge

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI.

---

### Story 4.2: Claude CLI Merge Conflict Resolution

**Story ID:** 4-2-claude-cli-merge-conflict-resolution
**Component:** `src/bmad_assist_lite/parallel/merger.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [Story 4.1]

#### User Story

As a developer,
I want merge conflicts resolved by Claude CLI with full story context,
So that integration issues are automatically handled when possible.

#### Description

When `git merge` produces conflicts, invoke Claude CLI (`claude --print -p`) with a prompt containing the story context and conflict markers. Apply the resolved content, verify no residual conflict markers remain, and complete the merge commit. If resolution fails for any reason, abort the merge and mark the story blocked.

#### Current State

Merge conflicts cause the merge to abort (Story 4.1). There is no automated resolution — conflicts would require manual intervention.

#### Target State

Claude CLI receives a structured prompt with story title/description, conflict file list, conflict markers from each file, and resolution instructions. Resolved content is applied, staged, and committed. Failures (timeout, auth error, residual markers) abort the merge and mark the story blocked.

#### Acceptance Criteria

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

#### Technical Notes

- Claude CLI via `subprocess.run(["claude", "--print", "-p", prompt])`
- Same auth mechanism as existing Claude SDK provider (user's Claude CLI login)
- Timeout configurable via `post_merge_fix_retries` config pattern
- Conflict markers detected by searching for `<<<<<<<`, `=======`, `>>>>>>>` in resolved files
- Uses `get_subprocess_kwargs()` for platform safety

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI.

---

### Story 4.3: Post-Merge Quality Gate

**Story ID:** 4-3-post-merge-quality-gate
**Component:** `src/bmad_assist_lite/parallel/merger.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [Story 4.1]

#### User Story

As a developer,
I want a full project quality gate run on the base branch after each merge,
So that integration issues between parallel stories are caught immediately.

#### Description

After each successful merge to the base branch, run the full project quality gate (lint, typecheck, build, test). This catches integration issues that individual worktree quality gates cannot detect — e.g., Story A's new API breaking Story B's code that was merged earlier. Results are captured per-gate with pass/fail status.

#### Current State

Stories merge to the base branch (Story 4.1) but there is no integration validation. Quality gates only run within each worktree (isolated from other stories' changes).

#### Target State

After each merge, lint/typecheck/build/test commands execute on the base branch. Commands are sourced using the existing priority order (config `quality_gate` section, then auto-detected toolchain). Per-gate results are captured. All-pass transitions the story to `done`. Any failure triggers fix_quality_gate (Story 4.4).

#### Acceptance Criteria

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

#### Technical Notes

- Reuses existing quality gate command sourcing logic from `core/quality_gates.py`
- Runs on the base branch working directory (not a worktree)
- Captures stdout/stderr per gate for failure reporting
- Uses `command_runner.py` patterns for subprocess execution with timeout
- `command_timeout` from quality_gate config applies per command

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI.

---

### Story 4.4: Post-Merge Fix Quality Gate & Sprint Status Update

**Story ID:** 4-4-post-merge-fix-quality-gate-and-sprint-status-update
**Component:** `src/bmad_assist_lite/parallel/merger.py`, `core/sprint_status.py`
**Estimate:** Medium
**Points:** 4
**Priority:** High
**Dependencies:** [Story 4.3]

#### User Story

As a developer,
I want integration failures auto-fixed on the base branch and sprint-status updated after successful merge,
So that dependent stories get clean code and sprint tracking reflects reality.

#### Description

When the post-merge quality gate fails, invoke fix_quality_gate using the existing workflow template on the base branch. The master LLM receives the failure report and attempts to fix the issues. After fix, re-run quality gate. If pass, transition to done; if still fail, mark blocked. On any successful merge+QG pass, update sprint-status.yaml using the existing SprintStatus Pydantic model.

#### Current State

Post-merge quality gate failures (Story 4.3) have no automated repair path. Sprint-status.yaml is not updated by the parallel orchestrator (sprint sync is disabled in worktree loops via `BMAD_PARALLEL_MODE=1`).

#### Target State

fix_quality_gate runs on the base branch with the existing workflow template. Fix commits are tagged for traceability. Retry count is configurable via `post_merge_fix_retries` (default 1). Sprint-status.yaml is updated to `done` after successful merge+QG, using the existing `SprintStatus` model.

#### Acceptance Criteria

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

#### Technical Notes

- Reuses existing `fix-quality-gate` workflow template from `workflows/fix-quality-gate/`
- Commit messages tagged for traceability: `fix: post-merge integration fix for story {id}`
- Sprint-status update uses existing `SprintStatus` model from `core/sprint_status.py`
- `post_merge_fix_retries` sourced from `ParallelConfig` (default 1)
- fix_quality_gate runs master LLM on the base branch (single LLM, safe for command execution)

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
| `tests/test_merger.py` | 4.1, 4.2, 4.3, 4.4 | New file: merge queue, git merge, conflict resolution, post-merge QG, fix QG, sprint-status update |
| `tests/test_git_ops.py` | 4.1, 4.2 | Extended: merge-related git operations (merge, abort, branch delete, conflict detection) |
| `tests/test_parallel_state.py` | 4.1, 4.4 | Extended: `merging` status transitions, `done`/`blocked` after merge |
| `tests/test_sprint_status_manager.py` | 4.4 | New or extended: orchestrator-owned sprint-status writes after merge+QG |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 4.1 | None | — | — | CLI tool, no user-facing UI |
| 4.2 | None | — | — | CLI tool, no user-facing UI |
| 4.3 | None | — | — | CLI tool, no user-facing UI |
| 4.4 | None | — | — | CLI tool, no user-facing UI |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests updated and passing (`pytest -q --tb=line --no-header`)
- [ ] All new code passes `mypy` strict mode
- [ ] All new code passes `ruff check src/` and `ruff format src/`
- [ ] Merge queue processes completed stories sequentially
- [ ] Claude CLI conflict resolution invoked on merge conflicts
- [ ] Post-merge quality gate runs full project lint/typecheck/build/test on base branch
- [ ] fix_quality_gate auto-repairs integration failures with tagged commits
- [ ] Sprint-status.yaml updated after successful merge+QG
- [ ] Documentation sync story completed (Tier 1 core docs verified current)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Merge conflicts too complex for Claude CLI to resolve | Medium | High | Full story context in prompt. Post-merge QG catches incomplete resolution. Story marked blocked for manual fix. |
| Post-merge QG false positives from pre-existing failures | Low | Medium | Base branch should be clean before parallel run. Epic QG (Epic 6) provides defense-in-depth. |
| fix_quality_gate introduces regressions while fixing integration issues | Low | High | fix runs full project test suite, not just the merging story's tests. If fix fails, story blocked — no silent corruption. |
| Claude CLI unavailable or auth expired during merge | Low | Medium | Graceful fallback: abort merge, mark story blocked, user fixes manually. |
| Sequential merge queue becomes bottleneck with many stories | Low | Low | Merge + QG is fast relative to story execution (minutes vs. hours). Bottleneck only if many stories complete simultaneously. |

## Rollback Plan

All merge operations use git's native merge mechanism. If this epic introduces issues:

1. **Revert merge commits** on the base branch using `git revert` for any problematic merges
2. **Remove `parallel/merger.py`** additions — the orchestrator can still run stories in worktrees without the merge pipeline (worktree branches preserved)
3. **Sprint-status** can be manually corrected since it uses the existing `SprintStatus` model and YAML format
4. **Worktree branches** are preserved even if merge fails — no code is lost, only the automated merge path is removed
