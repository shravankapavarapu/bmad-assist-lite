# Story 2.3: Ready Story Discovery & Re-evaluation

Status: in-progress

## Story

As a developer,
I want to determine which stories are ready to execute and re-evaluate after each completion,
so that the orchestrator always knows what to schedule next for maximum parallel throughput.

## Acceptance Criteria

1. **Given** a DAG where Stories 3.1 and 3.2 have no dependencies, and Story 3.3 depends on 3.1, **when** `get_ready_stories()` is called with no stories completed, **then** it returns [3.1, 3.2] sorted by scheduling score (higher first), **and** 3.3 is not included (dependency not satisfied).

2. **Given** Story 3.1 is marked as `done` in the done set, **when** `get_ready_stories()` is called with the updated done set, **then** Story 3.3 is now included (dependency 3.1 satisfied).

3. **Given** Story 3.2 is `in-flight` (currently executing), **when** `get_ready_stories()` is called, **then** Story 3.2 is excluded (already running).

4. **Given** Story 3.1 is `blocked`, **when** `get_ready_stories()` is called, **then** Story 3.3 is excluded (dependency 3.1 not in `done` set).

5. **Given** `are_dependencies_satisfied()` is called in a loop for N stories, **when** a pre-computed `done_ids` set is passed, **then** each check is O(1) per dependency (set membership lookup, not O(n) re-scanning).

## Tasks / Subtasks

- [x] Task 1: Implement `are_dependencies_satisfied()` method on `DependencyGraph` (AC: #5)
  - [x] 1.1: Add method signature: `are_dependencies_satisfied(self, story_id: str, done_ids: set[str]) -> bool`
  - [x] 1.2: Check that every dependency in `self._forward[story_id]` is present in `done_ids` — O(1) per dependency via set membership
  - [x] 1.3: Raise `KeyError` if `story_id` is not in the graph (consistent with `dependencies_of()` and `dependents_of()` pattern)
  - [x] 1.4: Add Google-style docstring with Args, Returns, and Raises sections

- [x] Task 2: Implement `get_ready_stories()` method on `DependencyGraph` (AC: #1, #2, #3, #4)
  - [x] 2.1: Add method signature: `get_ready_stories(self, done_ids: set[str], in_flight_ids: set[str], blocked_ids: set[str]) -> list[str]`
  - [x] 2.2: A story is "ready" if ALL of: (a) all its deps are in `done_ids`, (b) story itself is NOT in `done_ids | in_flight_ids | blocked_ids`
  - [x] 2.3: Sort the ready list by pre-computed scheduling score (descending) — use `self._scores` populated by Story 2.2's `_compute_scores()`
  - [x] 2.4: Use `_story_sort_key` as a secondary tiebreaker when scores are equal for deterministic ordering
  - [x] 2.5: Add Google-style docstring explaining the readiness criteria, parameters, and return value
  - [x] 2.6: Add debug-level log message listing the ready stories and their scores (use `[DependencyGraph]` prefix)

- [x] Task 3: Write comprehensive tests in `tests/test_dependency_graph.py` (AC: #1–#5)
  - [x] 3.1: Test basic readiness — stories with no deps and nothing done/in-flight/blocked are ready
  - [x] 3.2: Test score-based ordering — higher-scored stories appear first in the returned list
  - [x] 3.3: Test dependency satisfaction — story becomes ready only when ALL deps are in `done_ids`
  - [x] 3.4: Test re-evaluation — calling `get_ready_stories()` with updated `done_ids` after a completion returns newly-unblocked stories
  - [x] 3.5: Test in-flight exclusion — stories in `in_flight_ids` are excluded even if deps are satisfied
  - [x] 3.6: Test blocked exclusion — stories in `blocked_ids` are excluded
  - [x] 3.7: Test blocked dependency cascade — if a dependency is in `blocked_ids` (not `done_ids`), dependents are NOT ready
  - [x] 3.8: Test all stories done — returns empty list when all stories are in `done_ids`
  - [x] 3.9: Test empty graph — returns empty list
  - [x] 3.10: Test single story with no deps — returns that story when not done/in-flight/blocked
  - [x] 3.11: Test diamond pattern re-evaluation — leaf node becomes ready only when both parents are done
  - [x] 3.12: Test `are_dependencies_satisfied()` returns True when all deps in `done_ids`
  - [x] 3.13: Test `are_dependencies_satisfied()` returns False when any dep missing from `done_ids`
  - [x] 3.14: Test `are_dependencies_satisfied()` returns True for a story with no dependencies (empty forward list)
  - [x] 3.15: Test `are_dependencies_satisfied()` raises `KeyError` for unknown story_id
  - [x] 3.16: Test tiebreaker ordering — stories with equal scores sorted by natural numeric order
  - [x] 3.17: Extend existing 50-story performance test to include `get_ready_stories()` call and verify completion in <1 second

- [x] Task 4: Update `parallel/__init__.py` exports if needed (AC: N/A)
  - [x] 4.1: Verify `DependencyGraph` is already exported (it is — done in Story 2.1). No new standalone functions, so no additional exports needed.

## Dev Notes

### Architecture Patterns and Constraints

- **Extend existing `DependencyGraph` class** — Stories 2.1 and 2.2 created and extended `DependencyGraph` in `src/bmad_assist_lite/parallel/dependency_graph.py` (currently ~443 lines). This story adds `get_ready_stories()` and `are_dependencies_satisfied()` methods to that same class. Do NOT create a new class or module.
- **O(1) dependency checks** — Use set membership (`dep_id in done_ids`) for each dependency. The `_forward[story_id]` list gives the dependencies; iterate it and check each against the `done_ids` set. Total cost: O(k) per story where k is the number of direct deps (typically 0-3), with each check O(1).
- **Score-based sorting** — `self._scores` dict is computed during `__init__` by Story 2.2's `_compute_scores()`. Use it directly. Sort descending by score. For equal scores, use `_story_sort_key()` as a secondary key for deterministic natural numeric ordering.
- **Stateless query design** — `get_ready_stories()` is a pure query. It takes status sets as parameters and returns a list. No internal state mutation. The orchestrator calls this after every story completion or block event with updated sets. Re-evaluation is simply calling the method again — no incremental state needed.
- **Exception hierarchy** — Use `KeyError` for unknown story IDs (consistent with `dependencies_of()` and `dependents_of()` from Story 2.1).
- **Logging convention** — `logger = logging.getLogger(__name__)` already at module top. Use `[DependencyGraph]` prefix in log messages (consistent with Stories 2.1/2.2). Use `logger.debug()` for ready story lists.
- **Type annotations** — All functions need full type hints including return types (mypy strict mode).
- **Union syntax** — Use `X | None`, not `Optional[X]`.
- **Import style** — Absolute imports only.
- **Line length** — 100 chars max (ruff).
- **Docstrings** — Google style. First line imperative summary.

### Existing `DependencyGraph` API (from Stories 2.1 + 2.2)

The class currently provides:
- `__init__(self, stories: list[EpicStory])` — Builds DAG, runs cycle detection, computes scores
- `roots` property → `list[str]` — Story IDs with zero deps, sorted numerically
- `dependencies_of(story_id)` → `list[str]` — Forward edges (what this story depends on)
- `dependents_of(story_id)` → `list[str]` — Reverse edges (who depends on this story)
- `all_story_ids` property → `list[str]` — All IDs, sorted numerically
- `story_count` property → `int`
- `scores` property → `dict[str, int]` — Scheduling scores for all stories
- `score_of(story_id)` → `int` — Score for one story
- `detect_cycles()` → `None` — Raises `ParallelError` on cycles
- `_forward: dict[str, list[str]]` — Forward adjacency (story → what it depends on)
- `_reverse: dict[str, list[str]]` — Reverse adjacency (story → who depends on it)
- `_dep_count: dict[str, int]` — Number of dependencies per story
- `_scores: dict[str, int]` — Scheduling scores
- `_priorities: dict[str, int]` — Parsed priority per story
- `_story_sort_key()` — Module-level function for natural numeric sort

### How the Orchestrator Will Call This

The orchestrator (Epic 3) will maintain three sets:
- `done_ids: set[str]` — Stories that completed and merged successfully
- `in_flight_ids: set[str]` — Stories currently running in worktrees
- `blocked_ids: set[str]` — Stories that failed and cannot proceed

After each story completes (or blocks), the orchestrator calls:
```python
ready = graph.get_ready_stories(done_ids, in_flight_ids, blocked_ids)
for story_id in ready[:max_concurrency - len(in_flight_ids)]:
    spawn_story(story_id)
```

This is the final API that Epic 3's `orchestrator.py` will depend on.

### Project Structure Notes

```
src/bmad_assist_lite/parallel/
  __init__.py              # EXISTS: already exports DependencyGraph (no changes needed)
  dependency_graph.py      # MODIFY: add get_ready_stories(), are_dependencies_satisfied()
  config.py                # EXISTS (Epic 1) — no changes
  exceptions.py            # EXISTS (Epic 1) — no changes
  git_ops.py               # EXISTS (Epic 1) — no changes

tests/
  test_dependency_graph.py  # MODIFY: add TestReadyStories and TestAreDependenciesSatisfied classes
```

### References

- `src/bmad_assist_lite/parallel/dependency_graph.py` — Existing `DependencyGraph` class (~443 lines, Stories 2.1 + 2.2)
- `src/bmad_assist_lite/parallel/exceptions.py` — `ParallelError` base exception
- `tests/test_dependency_graph.py` — Existing test cases from Stories 2.1 + 2.2 (extend with new test classes)
- Architecture doc: "Dependency Resolution" section, "FR1-FR6 → `dependency_resolver.py`"
- PRD: FR4 (determine ready stories), FR5 (scheduling scores for prioritization), FR6 (re-evaluate after completion)

## Testing Requirements

- **Basic readiness**: Stories with no dependencies and not in any exclusion set are returned as ready.
- **Score-based ordering**: Ready stories are sorted by descending scheduling score. Higher-scored stories first.
- **Dependency satisfaction**: A story is NOT ready until ALL of its dependencies are in the `done_ids` set. One missing dep = not ready.
- **Re-evaluation after completion**: When a newly-completed story is added to `done_ids`, dependents that now have all deps satisfied become ready.
- **In-flight exclusion**: Stories currently running (in `in_flight_ids`) are excluded even if their deps are satisfied.
- **Blocked exclusion**: Stories in `blocked_ids` are excluded. Additionally, stories whose deps include a blocked story are NOT ready (blocked ≠ done).
- **Diamond pattern**: A story depending on two parents becomes ready only when BOTH parents are in `done_ids`.
- **All done**: Returns empty list when every story is in `done_ids`.
- **Empty graph**: Returns empty list for an empty `DependencyGraph`.
- **Single story**: A story with no deps is ready (when not excluded).
- **`are_dependencies_satisfied` — True**: All deps in `done_ids` → True.
- **`are_dependencies_satisfied` — False**: At least one dep not in `done_ids` → False.
- **`are_dependencies_satisfied` — No deps**: Story with no dependencies → True (vacuously satisfied).
- **`are_dependencies_satisfied` — Unknown story**: Raises `KeyError`.
- **Tiebreaker**: Stories with equal scores sorted by natural numeric order (e.g., 3.1 before 3.2).
- **Performance**: `get_ready_stories()` call on 50-story graph completes in <1 second.

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/dependency_graph.py tests/test_dependency_graph.py` | **NEEDS-MANUAL-RUN** |
| Typecheck | `mypy src/bmad_assist_lite/parallel/dependency_graph.py --strict` | **NEEDS-MANUAL-RUN** |
| Build | `python -m py_compile src/bmad_assist_lite/parallel/dependency_graph.py` | **NEEDS-MANUAL-RUN** |
| Tests | `pytest tests/test_dependency_graph.py -v` | **NEEDS-MANUAL-RUN** |

> **Note**: Quality gates could not be run automatically due to sandbox security restrictions blocking all Python executables. Please run the above commands manually to verify.

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (claude-opus-4-20250514)

### Debug Log References
Sandbox security policy blocked all Python/pytest/ruff/mypy executables, preventing automated quality gate validation. Implementation was verified through code review and structural analysis.

### Completion Notes List
- Implemented `are_dependencies_satisfied()` method on `DependencyGraph` with O(1) per-dependency set membership checks
- Implemented `get_ready_stories()` method as a pure stateless query, returning ready stories sorted by descending scheduling score with natural numeric tiebreaking
- Added `TestAreDependenciesSatisfied` test class with 6 tests covering: all deps satisfied, missing dep, no deps (vacuously true), unknown story KeyError, single dep satisfied/not satisfied
- Added `TestReadyStories` test class with 17 tests covering: basic readiness, score-based ordering, dependency satisfaction, re-evaluation after completion, in-flight exclusion, blocked exclusion, blocked dependency cascade, all done, empty graph, single story, diamond pattern, tiebreaker ordering, numeric ID tiebreaker, done exclusion, debug logging, and 50-story performance
- Verified `__init__.py` already exports `DependencyGraph` — no changes needed
- All methods follow existing patterns: Google-style docstrings, `[DependencyGraph]` log prefix, KeyError for unknown story IDs, full type annotations

### File List
- `src/bmad_assist_lite/parallel/dependency_graph.py` — MODIFIED: Added `are_dependencies_satisfied()` and `get_ready_stories()` methods (~80 lines added)
- `tests/test_dependency_graph.py` — MODIFIED: Added `TestAreDependenciesSatisfied` (6 tests) and `TestReadyStories` (17 tests) classes (~230 lines added)
- `_bmad-output/implementation-artifacts/2-3-ready-story-discovery-and-re-evaluation.md` — MODIFIED: Updated status, task checkboxes, quality gates, dev agent record

## Senior Developer Review (AI)

**Review Date:** 2026-03-18
**Aggregate Score:** 6.8 / REJECT
**Reviewers:** 2 (Reviewer-1: 7.3/REJECT, Reviewer-2: 6.2/REJECT)

### Fixes Applied
1. **DRY Violation (IMPORTANT):** `get_ready_stories()` now calls `self.are_dependencies_satisfied()` instead of duplicating the inline logic
2. **Strict Score Lookup (IMPORTANT):** Replaced `self._scores.get(sid, 0)` with `self._scores[sid]` in sort key and debug log — fail-fast on invariant violation
3. **Eager Log Evaluation (IMPORTANT):** Wrapped `logger.debug()` dict comprehension in `if logger.isEnabledFor(logging.DEBUG)` guard
4. **Set Union Removed (MINOR):** Replaced `excluded = done_ids | in_flight_ids | blocked_ids` with inline `or` membership checks — avoids per-call allocation
5. **Tautological Test Fixed (IMPORTANT):** Rewrote `test_blocked_dependency_cascade` with a two-dep scenario where one dep is done and the other blocked, proving the blocked cascade is the distinguishing factor
6. **Weak Test Strengthened (IMPORTANT):** `test_score_based_ordering` now asserts full output: `len(ready) == 2`, `ready[0]`, `ready[1]`, and `3.3 not in ready`
7. **Task 3.17 Compliance (IMPORTANT):** Merged `get_ready_stories()` perf test into existing `test_50_stories_under_one_second` (as the task specified "extend"), removed duplicate standalone test

### Rejected Findings
- **No input validation for bogus IDs in status sets (R2):** FALSE POSITIVE. The method is a pure query iterating `self._forward` keys; IDs not in the graph in caller sets have no effect. Adding validation would impose O(n) overhead on every query for no functional benefit, contradicting the stateless query design.
- **Sprint-status / Story 2.2 promotion (R2):** Out of scope for code review — administrative process issue, not a code defect.

### Runtime Verification
Sandbox restrictions blocked execution of ruff, mypy, and pytest. Quality gates require manual execution.

### Verdict
All code-level findings have been resolved. Status set to `in-progress` pending manual quality gate verification.

## Change Log
- 2026-03-18: Implemented Story 2.3 — added `are_dependencies_satisfied()` and `get_ready_stories()` methods to `DependencyGraph`, with 23 comprehensive tests
- 2026-03-18: Code review synthesis — applied 7 fixes from dual-reviewer findings (DRY, strict lookups, lazy logging, test improvements); 2 findings rejected as false positives
