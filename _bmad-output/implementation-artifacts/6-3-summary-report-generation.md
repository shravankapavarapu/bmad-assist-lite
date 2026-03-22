# Story 6.3: Summary Report Generation

Status: in-progress

## Story

As a developer,
I want a summary report generated when a parallel run completes,
so that I can see concrete time savings, per-story results, and merge/QG outcomes at a glance.

## Acceptance Criteria

1. **Report content on completion** — Given all stories have completed (done or blocked), when the orchestrator finishes, then a summary report is appended to `parallel-run.log` containing:
   - Total stories: X completed, Y blocked
   - Per-story timing: start time, completion time, duration, merge time
   - Wall-clock time (parallel): actual elapsed time
   - Estimated sequential time: sum of all individual story durations
   - Time saved: sequential estimate minus wall-clock time (absolute and percentage)
   - Merge results: X clean, Y conflict-resolved, Z failed
   - Post-merge QG results: X passed, Y fixed, Z blocked

2. **Time savings display** — Given a run with 6 stories completing in 2h 47m with sequential estimate of 6h 10m, when the summary is generated, then it shows: "Time saved: 3h 23m (55% reduction)"

3. **Blocked story detail** — Given some stories were blocked, when the summary is generated, then blocked stories are listed with their failure reason (from `StoryState.error`)

4. **Stdout echo** — Given the summary is generated, when it is written, then it is also printed to stdout so the user sees it immediately (via `write_progress()` with output lock)

## Tasks / Subtasks

- [x] Task 1: Create `parallel/report.py` module with report data model (AC: #1, #2, #3)
  - [x] 1.1: Create `ReportData` frozen Pydantic model containing all fields needed for the summary: `total_stories`, `completed_count`, `blocked_count`, `wall_clock_seconds`, `sequential_estimate_seconds`, `per_story_timings` (list of per-story records), `merge_stats` (clean/conflict-resolved/failed counts), `qg_stats` (passed/fixed/blocked counts), `blocked_stories` (list of story_id + error reason)
  - [x] 1.2: Create `StoryTiming` frozen Pydantic model with `story_id`, `started_at`, `completed_at`, `duration_seconds`, `merge_duration_seconds` (`float | None`), `status` fields for per-story breakdown. `merge_duration_seconds` is populated from `MergeOutcome` timing data when available, `None` if the story never reached the merge phase
  - [x] 1.3: Create `MergeStats` frozen Pydantic model with `clean`, `conflict_resolved`, `failed` int fields
  - [x] 1.4: Create `QGStats` frozen Pydantic model with `passed`, `fixed`, `blocked` int fields

- [x] Task 2: Create report builder function (AC: #1, #2, #3)
  - [x] 2.1: Create `build_report(state: ParallelState, orchestrator_started_at: datetime, merge_outcomes: list[MergeOutcome]) -> ReportData` function that extracts all timing and status data from `ParallelState.stories` and merge result history. Populate `StoryTiming.merge_duration_seconds` by matching `MergeOutcome.story_id` → `MergeOutcome.duration_seconds` for each story
  - [x] 2.2: Calculate wall-clock time as `orchestrator_started_at` to `_utc_now()`
  - [x] 2.3: Calculate sequential estimate as sum of individual story durations (`completed_at - started_at` for each story with both timestamps). Stories missing timestamps are excluded from the sequential estimate with a note
  - [x] 2.4: Calculate time saved as `sequential_estimate - wall_clock` (absolute) and `(time_saved / sequential_estimate) * 100` (percentage). Handle edge cases: if sequential estimate is 0 or negative, report "N/A"
  - [x] 2.5: Populate `blocked_stories` from `StoryState` entries where `status == BLOCKED`, using the `error` field for the reason

- [x] Task 3: Create human-readable time formatting (AC: #2)
  - [x] 3.1: Create `_format_duration(seconds: float) -> str` helper that formats durations as human-readable strings: `"2h 47m"`, `"38m"`, `"1h 05m"`, `"1h 00m"`, `"45s"` (under 60s shows seconds, 60s to < 3600s shows minutes, >= 3600s shows hours+minutes — e.g. exactly 3600s → `"1h 00m"`)
  - [x] 3.2: Create `_format_percentage(value: float) -> str` helper that formats as `"55%"` (rounded to nearest integer)

- [x] Task 4: Create report rendering function (AC: #1, #2, #3, #4)
  - [x] 4.1: Create `render_report(report: ReportData) -> str` function that builds the full text report as a multi-line string
  - [x] 4.2: Include a header section with total stories and completion counts
  - [x] 4.3: Include per-story timing table: story ID, status, duration (formatted), merge time (formatted, or `"-"` if not applicable), start/completion times
  - [x] 4.4: Include timing comparison section: wall-clock time, sequential estimate, time saved (absolute + percentage)
  - [x] 4.5: Include merge statistics: clean/conflict-resolved/failed counts
  - [x] 4.6: Include QG statistics: passed/fixed/blocked counts
  - [x] 4.7: Include blocked stories section with story ID and failure reason (only when blocked stories exist)

- [x] Task 5: Create report output function (AC: #4)
  - [x] 5.1: Create `write_report(report_text: str, project_root: Path) -> None` function that appends the report to `parallel-run.log` via the `_logger` from `logging.py` module and echoes to stdout via `write_progress()` from `providers.base`
  - [x] 5.2: The log file write uses the existing `bmad_assist_lite.parallel` logger namespace (same as Story 6.1's logging infrastructure). Log the report at INFO level so it flows through the already-configured FileHandler
  - [x] 5.3: Use `write_progress()` (from `bmad_assist_lite.providers.base`) for stdout output — this respects the `_OUTPUT_LOCK` for thread-safe console writes

- [x] Task 6: Create `MergeOutcome` data class for merge tracking (AC: #1)
  - [x] 6.1: Create `MergeOutcome` frozen Pydantic model with fields: `story_id: str`, `merged: bool`, `had_conflicts: bool`, `conflicts_resolved: bool`, `qg_passed: bool`, `qg_fixed: bool`, `duration_seconds: float` (merge elapsed time from merge-start to merge-complete, used for per-story merge time in the report). Note: `qg_fixed` is `True` only when post-merge QG initially failed but was resolved via `post_merge_fix_retries`. If the retry-fix flow is not yet implemented, set `qg_fixed=False` always — the field is forward-compatible
  - [x] 6.2: The orchestrator will build `MergeOutcome` records in `_process_merge_queue()` after each merge result

- [x] Task 7: Integrate report generation into orchestrator (AC: #1, #4)
  - [x] 7.1: Add `_merge_outcomes: list[MergeOutcome]` field to `Orchestrator.__init__()` initialized to empty list
  - [x] 7.2: In `_process_merge_queue()`, after processing each merge result, append a `MergeOutcome` record capturing the merge/QG outcome
  - [x] 7.3: In `Orchestrator.run()` finally block, after `_print_exit_summary()` and before `log_run_complete()`, call `build_report()` → `render_report()` → `write_report()`. Wrap in `contextlib.suppress(Exception)` to prevent report failures from masking other errors
  - [x] 7.4: Store `_orchestrator_started_at: datetime` in `__init__()` using `_utc_now()` for wall-clock calculation

- [x] Task 8: Write tests for `parallel/report.py` (AC: #1, #2, #3, #4)
  - [x] 8.1: Test `_format_duration()` with edge cases: 0 seconds, 45 seconds, 90 seconds, 3599 seconds (59m), 3600 seconds (1h 00m), 167 minutes (2h 47m), 3661 seconds (1h 01m)
  - [x] 8.2: Test `_format_percentage()` with 0%, 55.4% → 55%, 100%, edge cases
  - [x] 8.3: Test `build_report()` with a `ParallelState` containing 3 done stories and 1 blocked story — verify correct counts, timing calculations, and blocked story extraction
  - [x] 8.4: Test `build_report()` handles stories missing timestamps gracefully (excluded from sequential estimate)
  - [x] 8.5: Test `build_report()` edge case: all stories blocked → sequential estimate is 0 → time saved shows "N/A"
  - [x] 8.6: Test `render_report()` produces output containing all required sections (header, timing, merge stats, QG stats, blocked stories)
  - [x] 8.7: Test `render_report()` with no blocked stories — blocked section is omitted
  - [x] 8.8: Test `render_report()` time saved display matches AC#2 format: "Time saved: Xh Ym (Z% reduction)"
  - [x] 8.9: Test `write_report()` calls both `_logger.info()` and `write_progress()` — mock both targets and verify
  - [x] 8.10: Test `MergeOutcome` model is frozen and correctly tracks merge/QG outcomes
  - [x] 8.11: Test `build_report()` with `MergeOutcome` list containing clean merges, conflict-resolved merges, and failed merges — verify `MergeStats` counts
  - [x] 8.12: Test `build_report()` with `MergeOutcome` entries tracking QG outcomes — verify `QGStats` counts
  - [x] 8.13: Test `build_report()` with empty `merge_outcomes` list (no merges completed) — verify `MergeStats` and `QGStats` are all zeros and report renders gracefully

- [x] Task 9: Update `parallel/__init__.py` exports (AC: all)
  - [x] 9.1: Add public API functions and models to `__init__.py` and `__all__`: `build_report`, `render_report`, `write_report`, `ReportData`, `MergeOutcome`

## Dev Notes

### Architecture Patterns and Constraints

- **Frozen Pydantic models**: All new models (`ReportData`, `StoryTiming`, `MergeStats`, `QGStats`, `MergeOutcome`) must use `model_config = ConfigDict(frozen=True)`. Mutations via `model_copy(update={...})` only.
- **Logging convention**: `logger = logging.getLogger(__name__)` at module top. The report module uses the standard `logging` module directly (NOT `import logging as _logging` — that alias is only needed inside `parallel/logging.py` which shadows stdlib). For writing to the parallel log file, use `logging.getLogger("bmad_assist_lite.parallel")` so messages flow through the FileHandler set up by Story 6.1.
- **Stdout output**: Use `write_progress()` from `bmad_assist_lite.providers.base` for user-facing console output. This respects `_OUTPUT_LOCK` for thread safety. Do NOT use `print()` or `typer.echo()` in the report module — it's a library module, not a CLI command. Note: `write_progress()` internally calls `logger.info()` on the `bmad_assist_lite.providers.base` logger — this does NOT cause duplicate entries in `parallel-run.log` because that FileHandler is scoped to the `bmad_assist_lite.parallel` namespace only.
- **Import rules**: `parallel/` modules may import from `core/` and `providers/` (`write_progress`). Must NOT import from `loop/`. The existing `output.py` already imports `write_progress` from `providers.base`, establishing precedent.
- **`_utc_now()` pattern**: Timestamps use `datetime.now(timezone.utc).replace(tzinfo=None)` — naive UTC. Define a local `_utc_now()` helper in `report.py` rather than importing the private function from `state.py` (importing `_`-prefixed functions across modules is fragile). The orchestrator already follows this local-copy pattern.
- **Path handling**: Always `pathlib.Path`, never `os.path`.
- **Type annotations**: Required on all functions (mypy strict). Use `X | None` not `Optional[X]`.
- **Line length**: 100 characters max (ruff enforced).
- **Section separators**: Use `# ============================================================================` between logical sections.
- **Exception hierarchy**: Use `ParallelError` subclasses from `parallel/exceptions.py`. Never bare `Exception`.
- **Atomic writes**: The report does NOT need atomic writes — it appends to the log file via Python's `logging` module (which handles file I/O internally) and writes to stdout. No `os.replace()` pattern needed.

### Actual Model Reference (from codebase)

**StoryState fields** (from `state.py`):
```python
class StoryState(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: StoryStatus          # BACKLOG, IN_FLIGHT, MERGING, DONE, BLOCKED
    worktree_path: Path | None
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None            # Failure reason for BLOCKED stories
```

**ParallelState fields** (from `state.py`):
```python
class ParallelState(BaseModel):
    model_config = ConfigDict(frozen=True)
    base_branch: str
    epic: int
    started_at: datetime         # Run start time
    stories: dict[str, StoryState]
```

**MergeResult fields** (from `merger.py`):
```python
class MergeResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    success: bool
    story_id: str
    conflict_files: list[str] = []
    error: str | None = None
    qg_result: PostMergeQGResult | None = None
```

**PostMergeQGResult fields** (from `merger.py`):
```python
class PostMergeQGResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    all_passed: bool
    story_id: str
    gate_results: list[GateResult] = []
    duration_ms: int = 0
```

**Key Data Flow**:
- `ParallelState.stories` → per-story timing (started_at, completed_at) and status (DONE/BLOCKED)
- `ParallelState.stories[sid].error` → blocked story failure reason
- `MergeOutcome` list (accumulated in orchestrator) → merge stats (clean/conflict/failed) and QG stats (passed/fixed/blocked)
- `Orchestrator._orchestrator_started_at` → wall-clock start time
- `_utc_now()` at report generation → wall-clock end time

### Story 6.1 Integration

Story 6.1 created `parallel/logging.py` with `setup_parallel_log()` / `teardown_parallel_log()` and a `FileHandler` on the `bmad_assist_lite.parallel` logger namespace. The report module leverages this existing infrastructure:
- Get a logger via `logging.getLogger("bmad_assist_lite.parallel")` and call `.info()` — messages automatically flow to the `parallel-run.log` FileHandler
- The FileHandler uses append mode, so the report is appended after all the event logs from the run
- The report should be generated BEFORE `teardown_parallel_log()` is called in the orchestrator's `finally` block, so the FileHandler is still active

### Orchestrator Integration Point

In `orchestrator.py`, the `run()` method's `finally` block currently:
1. Removes signal handlers
2. Prints exit summary (`_print_exit_summary()`)
3. Logs run-end footer (`log_run_complete()`)
4. Tears down log (`teardown_parallel_log()`)

The report generation should be inserted between steps 2 and 3:
1. `_remove_signal_handlers()`
2. `_print_exit_summary()` — existing brief console summary
3. **NEW**: `build_report()` → `render_report()` → `write_report()` — comprehensive report
4. `log_run_complete()` — run-end footer in log file
5. `teardown_parallel_log()` — close FileHandler

### Project Structure Notes

**New file:**
```
src/bmad_assist_lite/parallel/report.py
```

**Modified files:**
```
src/bmad_assist_lite/parallel/orchestrator.py  — add MergeOutcome tracking, report generation call
src/bmad_assist_lite/parallel/__init__.py      — add new exports
```

**New test file:**
```
tests/test_parallel_report.py
```

### Key Implementation Decisions

1. **MergeOutcome tracking**: Rather than re-deriving merge statistics from `ParallelState` (which only has final status, not merge attempt details), the orchestrator accumulates `MergeOutcome` records as merges complete. This captures whether conflicts were resolved, whether QG was fixed, etc. — data not preserved in the state model.

2. **Separate report module**: The architecture document specifies `logging.py` as the home for the summary report, but `logging.py` already shadows stdlib `logging` and has the `import logging as _logging` workaround. Creating `report.py` keeps the report logic separate and avoids further complicating the shadow-import module. The architecture module layout section includes `logging.py` for "summary report" but doesn't prohibit a separate `report.py`.

3. **Sequential estimate calculation**: Sum of individual story durations (completed_at - started_at). This is a rough estimate — it assumes stories would take the same time sequentially as they do in parallel. In practice, parallel stories may take longer due to resource contention, but this provides a useful approximation.

4. **Report formatting**: Plain text with simple alignment. No `rich` or `tabulate` dependencies (consistent with the status display in Story 6.2). Use `str.ljust()` for table columns.

5. **write_progress for stdout**: The epic file's technical notes specify using `write_progress()` for stdout output. This function acquires `_OUTPUT_LOCK` (threading.Lock) before writing, ensuring thread safety with any concurrent output.

6. **Edge case: no completed stories**: If all stories are blocked, sequential estimate is 0. The time-saved calculation should show "N/A" rather than a misleading number.

### References

- Architecture: Observability section — summary report generation tier
- Architecture: Parallel Module Layout — `logging.py` designated for summary report
- Architecture: Enforcement Guidelines — all 54 project-context rules apply
- PRD: FR45 — generate summary report with per-story timing and time saved
- PRD: FR46 — detailed post-merge QG failure logging (already in Story 6.1, report references these)
- PRD: NFR6 — orchestrator overhead <1% of wall-clock time (report generation is trivial)
- Story 6.1: Logging infrastructure (`setup_parallel_log`, FileHandler on `bmad_assist_lite.parallel` namespace)
- Epic file: Story 6.3 acceptance criteria and technical notes

## Testing Requirements

- **Duration formatting**: `_format_duration()` with various inputs — 0s, 45s, 90s, 3600s, 10020s (2h 47m), sub-minute, exact hour boundary
- **Percentage formatting**: `_format_percentage()` with 0%, fractional values, 100%
- **Report build with mixed statuses**: 3 done + 1 blocked stories → correct counts, sequential estimate excludes blocked
- **Report build with missing timestamps**: Stories with `started_at=None` or `completed_at=None` excluded from sequential estimate
- **Report build with all blocked**: Sequential estimate = 0, time saved = "N/A"
- **Report build with merge outcomes**: Clean, conflict-resolved, and failed merges → correct `MergeStats` counts
- **Report build with QG outcomes**: Passed, fixed, blocked QG results → correct `QGStats` counts
- **Report render contains all sections**: Header, per-story timing, wall-clock/sequential comparison, merge stats, QG stats
- **Report render with blocked stories**: Blocked section present with story IDs and failure reasons
- **Report render without blocked stories**: Blocked section omitted
- **Time saved format**: Matches "Time saved: Xh Ym (Z% reduction)" pattern from AC#2
- **write_report integration**: Verifies both log file write (via logger) and stdout write (via `write_progress`) are called
- **MergeOutcome model**: Frozen, correct field tracking
- **Edge case: zero wall-clock time**: Handles division-by-zero gracefully in percentage calculation
- **Edge case: single story**: Report generates correctly for 1-story runs

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/report.py tests/test_parallel_report.py` | **PENDING** (sandbox restricted) |
| Typecheck | `mypy src/bmad_assist_lite/parallel/report.py` | **PENDING** (sandbox restricted) |
| Build | `python -m py_compile src/bmad_assist_lite/parallel/report.py` | **PENDING** (sandbox restricted) |
| Tests | `pytest tests/test_parallel_report.py -v --tb=short` | **PENDING** (sandbox restricted) |

> **Note**: Quality gates could not be executed due to sandbox restrictions preventing Python/pytest execution. All code has been reviewed for ruff/mypy compliance. Manual execution by the developer is required.

## Dev Agent Record

### Agent Model Used
Claude Opus 4.6

### Debug Log References
- Sandbox restricted all Python command execution; code reviewed manually for lint/type compliance

### Completion Notes List
- Created `parallel/report.py` with 5 frozen Pydantic models (StoryTiming, MergeStats, QGStats, MergeOutcome, ReportData)
- Implemented `_format_duration()` with proper boundary handling (seconds/minutes/hours transitions)
- Implemented `_format_percentage()` with integer rounding
- Implemented `build_report()` to extract timing, merge, and QG data from ParallelState + MergeOutcome records
- Implemented `render_report()` with human-readable sections: header, per-story timing table, timing comparison, merge stats, QG stats, blocked stories
- Implemented `write_report()` using parallel logger namespace + write_progress() for dual output
- Integrated report generation into orchestrator: MergeOutcome recording in _process_merge_queue(), _orchestrator_started_at tracking, report generation in finally block wrapped in contextlib.suppress()
- Updated __init__.py with new exports (MergeOutcome, ReportData, build_report, render_report, write_report)
- Updated conftest.py to mock write_report in orchestrator tests
- Wrote 50+ comprehensive tests covering all data models, formatting helpers, builder, renderer, writer, and edge cases
- All code follows project conventions: frozen Pydantic models, 100-char line limit, section separators, type annotations, _utc_now() local copy pattern, proper docstrings

### File List
- `src/bmad_assist_lite/parallel/report.py` (NEW) — Report data models, builder, renderer, writer
- `src/bmad_assist_lite/parallel/orchestrator.py` (MODIFIED) — MergeOutcome tracking, report generation integration
- `src/bmad_assist_lite/parallel/__init__.py` (MODIFIED) — New exports
- `tests/conftest.py` (MODIFIED) — Added write_report mock for orchestrator tests
- `tests/test_parallel_report.py` (NEW) — Comprehensive test suite (50+ tests)

## Senior Developer Review (AI)

**Date:** 2026-03-22
**Verdict:** REJECT (pre-calculated score: 7.3) — fixes applied, requires test verification
**Reviewers:** 2 (Reviewer-1 scored 8.9/REJECT, Reviewer-2 scored 5.8/MAJOR REWORK)

### Critical Issues Fixed
1. **Merge timing never captured** — `duration_seconds=0.0` was hardcoded in orchestrator. Fixed by timing `process_merge_with_fix()` calls with `_utc_now()` before/after.
2. **QG skipped → erroneously counted as blocked** — When `qg_result is None` and merge succeeded, `qg_passed` was `False`. Fixed: `qg_passed = result.success and (result.qg_result is None or result.qg_result.all_passed)`.

### Important Issues Fixed
3. **Uncounted merge edge case** — `merged=True, had_conflicts=True, conflicts_resolved=False` fell through all conditions silently. Fixed with exhaustive if/elif/else classification.
4. **Log interleaving risk** — Per-line `parallel_logger.info()` calls replaced with single atomic `parallel_logger.info(report_text)`.

### Minor Issues Fixed
5. **`_utc_now()` inconsistency** — Changed `timezone.utc` to `UTC` for consistency with `orchestrator.py` and `state.py`.
6. **Unused `project_root` parameter** — Removed from `write_report()` signature and all call sites.
7. **`_orchestrator_started_at` timing** — Moved from `__init__()` to `run()` to avoid measuring init overhead.
8. **`_format_duration()` truncation** — Changed `int(seconds)` to `round(seconds)` for better precision.
9. **Silent error suppression** — Replaced `contextlib.suppress(Exception)` around report generation with `try/except` that logs to `logger.debug`.

### Findings Not Applied
- **`_merge_outcomes` not crash-resilient** (R1 Critical) — Valid concern for resumed runs, but out of scope for this story; requires state model changes.
- **Wall-clock uses run start vs state start** (R1 Important) — Matches Task 2.2 spec; shows current session time.
- **`blocked_stories` raw tuples** (R2 Minor) — Accepted tradeoff; changing requires test/render updates for marginal benefit.
- **Duration precision in minutes range** (R2 Minor) — Matches Task 3.1 spec design.

### Runtime Verification
- **Lint/Type Check:** Sandbox restricted — not executed
- **Build:** Sandbox restricted — not executed
- **Tests:** Sandbox restricted — not executed
- **Manual verification:** All modified files reviewed for syntax and logic correctness

### Action Required
Run `python -m pytest tests/test_parallel_report.py -v` and `python -m ruff check src/bmad_assist_lite/parallel/report.py src/bmad_assist_lite/parallel/orchestrator.py` to verify fixes. Set status to `done` if all pass.
