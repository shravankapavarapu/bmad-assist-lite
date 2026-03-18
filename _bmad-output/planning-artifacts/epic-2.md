---
stepsCompleted: []
inputDocuments:
  - 'prd.md'
  - 'architecture.md'
  - 'epics.md'
---

# bmad-assist-lite-parallel-stories - Epic 2 Breakdown

## Epic 2: Dependency Resolution

**Epic ID:** Epic-2
**Created:** 2026-03-17
**Status:** Draft
**Priority:** High
**Points:** 8
**Stories:** 3

### Overview

Build the dependency graph engine. Given an epic file with story dependencies, the system builds a DAG, detects circular dependencies, computes scheduling scores prioritizing stories that unblock the most downstream work, and determines which stories are ready to run in parallel.

### Business Goal

Enable intelligent parallel scheduling by understanding story interdependencies, ensuring no deadlocks and maximum throughput.

### Strategic Context

- Critical path for parallelism — without dependency resolution, stories can only run sequentially
- Pure algorithmic work (graph theory) with no I/O or external dependencies
- Reuses existing `bmad/parser.py` `EpicStory.dependencies` data
- Performance target: DAG construction <1 second for 50 stories (NFR9)

### Dependencies

- Epic 1 (for `ParallelError` exception hierarchy and module structure)

### Context7 Library Documentation

<!-- No external libraries needed — pure Python graph algorithms using stdlib data structures -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Dependency Resolution; Parallel Module Layout; Enforcement Guidelines |
| `prd.md` | Functional Requirements |
| `project-context.md` | `(full)` |

### Recommended Story Order

1. 2-1-epic-dependency-parsing-and-dag-construction - Foundation: DAG must exist before cycle detection or scheduling
2. 2-2-circular-dependency-detection-and-scheduling-scores - Builds on DAG from 2.1; scoring needed before ready discovery
3. 2-3-ready-story-discovery-and-re-evaluation - Final consumer of DAG + scores; provides the API the orchestrator calls

---

### Story 2.1: Epic Dependency Parsing & DAG Construction

**Story ID:** 2-1-epic-dependency-parsing-and-dag-construction
**Component:** `src/bmad_assist_lite/parallel/dependency_graph.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** []

#### User Story

As a developer,
I want the orchestrator to parse story dependencies from epic files and build a directed acyclic graph,
So that the system knows which stories depend on which.

#### Description

Create `dependency_graph.py` with a `DependencyGraph` class that accepts parsed story data from the existing `bmad/parser.py` and builds an adjacency-list DAG. Stories without dependencies are identified as roots (in-degree 0).

#### Current State

The existing `bmad/parser.py` extracts `EpicStory.dependencies` from epic markdown files, but no graph structure is built from this data.

#### Target State

- `DependencyGraph` class constructed from list of `EpicStory` objects
- Internal adjacency list representation: `dict[str, list[str]]` (story_id -> list of dependency story_ids)
- `roots` property returns stories with in-degree 0
- Reverse adjacency list tracks dependents (who depends on me)

#### Acceptance Criteria

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

#### Technical Notes

- Reuse `EpicStory.dependencies` from existing parser — do not re-parse markdown
- Adjacency list: `dict[str, list[str]]` for dependencies (edges point from story to its deps)
- Reverse adjacency list: `dict[str, list[str]]` for dependents (who depends on me)
- No external graph libraries — pure Python stdlib
- Story IDs normalized to match sprint-status format (e.g., `2-1-epic-dependency-parsing-and-dag-construction`)

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 2.2: Circular Dependency Detection & Scheduling Scores

**Story ID:** 2-2-circular-dependency-detection-and-scheduling-scores
**Component:** `src/bmad_assist_lite/parallel/dependency_graph.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [Story 2.1]

#### User Story

As a developer,
I want circular dependencies detected before execution and stories prioritized by unblocking potential,
So that the orchestrator never deadlocks and maximizes parallelism throughput.

#### Description

Add cycle detection (DFS with recursion stack) and scheduling score computation to `DependencyGraph`. Cycle detection runs at construction time and raises `ParallelError` with the cycle path. Scheduling scores use a formula that prioritizes stories that unblock the most downstream work.

#### Current State

`DependencyGraph` (from Story 2.1) stores the DAG but does not validate it or compute scheduling priorities.

#### Target State

- `detect_cycles()` method finds all cycles via DFS with recursion stack
- Raises `ParallelError` with cycle path on detection (e.g., "Circular dependency: A -> B -> A")
- `compute_scores()` method assigns scheduling scores to all stories
- Score formula: `(1000 * unblock_potential) + (100 * depth_score) + (10 * priority)`
- `unblock_potential` = count of transitive downstream dependents

#### Acceptance Criteria

**Given** an epic where Story A depends on B and Story B depends on A
**When** cycle detection runs (DFS with recursion stack)
**Then** the circular dependency is detected and reported to the user with the cycle path
**And** execution does not start

**Given** a valid DAG with no cycles
**When** scheduling scores are computed
**Then** each story receives a score using: `(1000 * unblock_potential) + (100 * depth_score) + (10 * priority)`
**And** stories that unblock more downstream work score higher
**And** root stories (no dependencies) score higher on depth

**Given** a linear chain: A -> B -> C (C depends on B, B depends on A)
**When** scheduling scores are computed
**Then** A scores highest (unblocks 2 downstream), B scores middle (unblocks 1), C scores lowest

#### Technical Notes

- DFS-based cycle detection with three-color marking (white/gray/black) or recursion stack set
- `unblock_potential`: count all transitive dependents via reverse adjacency BFS/DFS
- `depth_score`: inverse of topological depth (roots score highest)
- `priority`: extracted from story metadata if available, default 5
- Cycle detection runs automatically during `DependencyGraph.__init__` or via explicit `validate()` call

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool, no user-facing UI

---

### Story 2.3: Ready Story Discovery & Re-evaluation

**Story ID:** 2-3-ready-story-discovery-and-re-evaluation
**Component:** `src/bmad_assist_lite/parallel/dependency_graph.py`
**Estimate:** Small
**Points:** 2
**Priority:** High
**Dependencies:** [Story 2.2]

#### User Story

As a developer,
I want to determine which stories are ready to execute and re-evaluate after each completion,
So that the orchestrator always knows what to schedule next.

#### Description

Add `get_ready_stories()` method to `DependencyGraph` that returns stories whose dependencies are all satisfied, sorted by scheduling score. Excludes in-flight, blocked, and done stories. Uses O(1) set lookups for dependency satisfaction checks.

#### Current State

`DependencyGraph` has DAG structure and scheduling scores but no API for the orchestrator to query which stories should run next.

#### Target State

- `get_ready_stories(done_ids, in_flight_ids, blocked_ids)` returns `list[str]` sorted by score (descending)
- `are_dependencies_satisfied(story_id, done_ids)` returns `bool` with O(1) per-dependency lookup
- Re-evaluation is simply calling `get_ready_stories()` again with updated sets after each completion

#### Acceptance Criteria

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

#### Technical Notes

- `get_ready_stories(done_ids: set[str], in_flight_ids: set[str], blocked_ids: set[str]) -> list[str]`
- A story is "ready" if: all deps in `done_ids`, story not in `done_ids | in_flight_ids | blocked_ids`
- Sort by pre-computed scheduling score (descending)
- O(1) set membership for dependency checks — no graph traversal at query time
- The orchestrator calls this after every story completion or block event

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
| `tests/test_dependency_graph.py` | 2.1, 2.2, 2.3 | New: DAG construction, cycle detection, scoring, ready discovery |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 2.1 | None | — | — | Pure algorithm, no UI |
| 2.2 | None | — | — | Pure algorithm, no UI |
| 2.3 | None | — | — | Pure algorithm, no UI |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests written and passing (`pytest -q --tb=short --no-header`)
- [ ] All code passes mypy strict mode (`mypy src/`)
- [ ] All code passes ruff linting (`ruff check src/`)
- [ ] All code passes ruff formatting (`ruff format --check src/`)
- [ ] Existing test suite still passes (NFR17)
- [ ] DAG construction works from existing `EpicStory` parser output
- [ ] Circular dependencies detected with clear error messages
- [ ] Scheduling scores correctly prioritize high-unblock stories
- [ ] `get_ready_stories()` returns correct results for all status combinations
- [ ] Performance: DAG construction <1 second for 50 stories

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| EpicStory.dependencies format changes upstream | Low | High | Pin to existing parser API; add integration test |
| Cycle detection misses complex cycles (>2 nodes) | Low | High | Test with 3-node, 4-node, and self-referencing cycles |
| Scheduling scores produce ties frequently | Medium | Low | Tie-breaking by story ID ensures deterministic ordering |
| Story ID normalization mismatch with sprint-status | Medium | Medium | Centralize ID normalization in one function; test against real sprint-status |
| Performance degrades with large DAGs | Low | Low | NFR9 target is 50 stories; test with boundary case |

## Rollback Plan

All changes are in a new file (`parallel/dependency_graph.py`) with no modifications to existing code. Rollback by removing the file and its test file.
