# Story 5.4: Status CLI Command

Status: in-progress

## Story

As a developer,
I want to check the current state of a parallel run from another terminal,
so that I can monitor progress without watching the live console.

## Acceptance Criteria

1. **Status command reads and displays state**: Given a parallel run is in progress, when `bmad-assist-lite parallel status` is invoked from another terminal, then it reads `parallel-state.yaml` and displays a human-readable table showing story statuses, current phases (by peeking at worktree state files for running stories), durations, dependency state (`blocked_by`), and diagnostic info (`block_reason`).

2. **No state file graceful exit**: Given no `parallel-state.yaml` exists, when `parallel status` is invoked, then it prints "No parallel run state found" and exits cleanly (exit code 0).

3. **All stories done summary**: Given all stories in the epic are `done`, when `parallel status` is invoked, then it shows all stories as done with a completion summary.

4. **Response time**: Given the command is invoked, when it runs, then it responds in <2 seconds (NFR10).

5. **Read-only safety**: The command performs no writes — safe to run concurrently with an active orchestrator.

## Tasks / Subtasks

- [x] Task 1: Implement `parallel_status` function in `parallel/cli.py` (AC: #1-#5)
  - [x] 1.1: Add `parallel_status()` function with Typer options: `--project` (Path, default `.`, exists=True, dir_okay=True) — same pattern as `parallel_run`
  - [x] 1.2: Resolve `project` path, derive state path via `get_parallel_state_path(project)`
  - [x] 1.3: Call `load_state(state_path)` — if `None` returned, print `"No parallel run state found"` and return (AC #2)
  - [x] 1.4: Handle `ParallelError` from `load_state()` (corrupt file) — print error message to stderr, exit code 1

- [x] Task 1b: Implement phase peeking helper (AC: #1 — FR47 phase display)
  - [x] 1b.1: Create `_peek_worktree_phase(worktree_path: Path | None) -> str | None` helper in `parallel/cli.py`
  - [x] 1b.2: If `worktree_path` is `None`, return `None`
  - [x] 1b.3: Read `{worktree_path}/.bmad-assist/state.yaml` — parse YAML to extract current phase
  - [x] 1b.4: If file is missing or corrupt (YAML parse error, key error), return `None` gracefully — do not raise
  - [x] 1b.5: Keep read-only; use a short timeout or size limit to avoid blocking on very large files

- [x] Task 2: Implement duration calculation helper (AC: #1, #3)
  - [x] 2.1: Create `_format_duration(started_at: datetime | None, completed_at: datetime | None) -> str` helper in `parallel/cli.py`
  - [x] 2.2: For running stories: calculate duration from `started_at` to current naive UTC (`_utc_now()` pattern: `datetime.now(UTC).replace(tzinfo=None)`)
  - [x] 2.3: For done/blocked/failed stories: calculate duration from `started_at` to `completed_at`
  - [x] 2.4: For ready stories (no `started_at`): return `"-"`
  - [x] 2.5: Format as `"Xh Ym Zs"` or `"Ym Zs"` (omit hours when zero, omit minutes when zero)

- [x] Task 3: Implement table formatting and display (AC: #1, #3)
  - [x] 3.1: Create `_format_status_table(state: ParallelState) -> str` that builds a simple aligned text table (no external library — use string formatting with `ljust`/`rjust`)
  - [x] 3.2: Table columns: Story ID, Status, Phase, Duration, Blocked By, Info
  - [x] 3.3: Header row with separator line (e.g., `"-----"`)
  - [x] 3.4: Status values displayed in lowercase to match `StoryStatus` enum values
  - [x] 3.5: For blocked stories, show `error` in the Info column (truncated to ~80 chars with `...` suffix if longer)
  - [x] 3.6: Blocked By column included (empty when `blocked_by` not in model)
  - [x] 3.7: Phase column: for `in_flight` stories, peek at worktree `state.yaml` to get current phase (read-only, gracefully handle missing/corrupt file)

- [x] Task 4: Implement summary counts (AC: #1, #3)
  - [x] 4.1: Count stories by status: done, in_flight, merging, blocked, backlog
  - [x] 4.2: Display counts below the table: e.g., `"Done: 3 | In-flight: 2 | Merging: 1 | Blocked: 1 | Backlog: 5"`
  - [x] 4.3: Include epic number and base branch in summary header
  - [x] 4.4: If all stories are `done`, add a completion message: `"All stories complete!"`
  - [x] 4.5: If any stories are `blocked`, include a warning: `"⚠ N story(ies) blocked — see Info column"`

- [x] Task 5: Register `parallel_status` in main CLI (AC: #1)
  - [x] 5.1: In `parallel/cli.py`, export `parallel_status` function
  - [x] 5.2: In `src/bmad_assist_lite/cli.py`, import `parallel_status` from `parallel.cli` and register: `parallel_app.command(name="status")(parallel_status)`

- [x] Task 6: Write tests (AC: #1-#5)
  - [x] 6.1: Test status display with mixed story statuses (backlog, in_flight, done, blocked, merging)
  - [x] 6.2: Test no state file → prints "No parallel run state found" and exits cleanly
  - [x] 6.3: Test all stories done → shows completion summary
  - [x] 6.4: Test corrupt state file → prints error to stderr, exit code 1
  - [x] 6.5: Test duration formatting: running (elapsed from now), done (start to complete), backlog (dash)
  - [x] 6.6: Test blocked story `error` displayed and truncated when long
  - [x] 6.7: Test summary counts are accurate
  - [x] 6.8: Test `_format_duration()` edge cases: `started_at=None`, hours/minutes/seconds formatting, zero-duration, negative clamping
  - [x] 6.9: Test CLI invocation via Typer test runner (`typer.testing.CliRunner`)
  - [x] 6.10: Test phase column: worktree `state.yaml` peeked for in_flight stories
  - [x] 6.11: Test phase peek graceful fallback when worktree `state.yaml` is missing or corrupt
  - [x] 6.12: Test read-only safety (no `save_state` calls)

## Dev Notes

### Architecture Patterns and Constraints

- **Frozen Pydantic models**: `ParallelState` and `StoryState` use `ConfigDict(frozen=True)`. The status command is read-only so no mutations needed, but always access fields via properties (never try direct assignment).
- **Atomic file writes**: `load_state()` already handles orphaned `.tmp` file cleanup on read. The status command just calls `load_state()` — no write operations needed.
- **`_utc_now()` convention**: Timestamps are naive UTC (`datetime.now(UTC).replace(tzinfo=None)`). Duration calculation for running stories must compare against naive UTC now.
- **Logging convention**: `logger = logging.getLogger(__name__)` at module top. Use `typer.echo()` for user-facing output (this is a CLI command, not library code). `typer.echo(..., err=True)` for error messages.
- **Exception hierarchy**: Catch `ParallelError` from `load_state()` for corrupt state files. Don't let it propagate — display user-friendly message and exit.
- **Path handling**: Always use `pathlib.Path`. Use `.resolve()` for the project path.
- **Import style**: Absolute imports only (`from bmad_assist_lite.parallel.state import ...`). Heavy imports inside function body to avoid circular imports (follow existing `parallel_run` pattern in `cli.py`).
- **No external table libraries**: Use simple string formatting with `str.ljust()` for aligned output. The project does not have `rich`, `tabulate`, or similar dependencies.
- **Read-only safety**: `load_state()` is safe to call concurrently with an active orchestrator because `save_state()` uses atomic `os.replace()`. The reader will always see either the old complete state or the new complete state, never a partial write.

### Source Tree Components to Touch

```
src/bmad_assist_lite/parallel/
  cli.py               # UPDATE — add parallel_status function, duration/table helpers
src/bmad_assist_lite/
  cli.py               # UPDATE — register parallel_status as "status" subcommand
tests/
  test_parallel_status.py  # NEW — status CLI tests
```

### Key Dependencies (Existing Modules)

- **`state.py`**: `ParallelState`, `StoryState`, `StoryStatus`, `load_state()`, `get_parallel_state_path()` — all read-only operations for loading and interpreting state
- **`exceptions.py`**: `ParallelError` — caught when state file is corrupt
- **`config.py`**: Not needed — status command doesn't need parallel config

### StoryStatus Enum Values

```python
class StoryStatus(Enum):
    READY = "ready"
    RUNNING = "running"
    MERGING = "merging"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"
```

### StoryState Field Reference (Canonical)

```python
class StoryState(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: StoryStatus          # The story's lifecycle status
    worktree_path: Path | None   # Path to git worktree (None when ready)
    branch: str | None           # Git branch name for this story
    pid: int | None              # Process ID of running agent (None when not running)
    started_at: datetime | None  # When story execution started
    completed_at: datetime | None # When story finished (done/blocked/failed)
    blocked_by: list[str]        # Upstream story IDs causing block (empty when unblocked)
    block_reason: str | None     # Human-readable block reason (e.g., "QG failed after 2 retries")
    last_phase: str | None       # Last known execution phase (e.g., "implement", "test")
```

**Note**: Duration is derived from `started_at` and `completed_at` timestamps — it is not stored as a field. The `last_phase` field tracks the last phase the agent was executing; for real-time phase info on running stories, peek at the worktree's `state.yaml`.

### ParallelState Field Reference (Canonical)

```python
class ParallelState(BaseModel):
    model_config = ConfigDict(frozen=True)
    epic_id: str
    base_branch: str
    started_at: datetime
    status: str  # "running" | "completed" | "failed" | "recovered"
    stories: dict[str, StoryState]
    merge_queue: list[str]
    completed_merges: list[str]
    failed_qa_stories: list[str]
```

### CLI Registration Pattern

The existing `parallel_run` is registered in `src/bmad_assist_lite/cli.py` as:
```python
parallel_app = typer.Typer(name="parallel", help="...", no_args_is_help=True)
app.add_typer(parallel_app, name="parallel")
from bmad_assist_lite.parallel.cli import parallel_run
parallel_app.command(name="run")(parallel_run)
```

The `parallel_status` command follows the same pattern:
```python
from bmad_assist_lite.parallel.cli import parallel_status
parallel_app.command(name="status")(parallel_status)
```

### Duration Calculation Logic

```python
from datetime import UTC, datetime

def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

# Running: started_at to now
# Done/Blocked/Failed: started_at to completed_at
# Ready: no duration (display "-")
```

### Project Structure Notes

- Tests go in `tests/test_parallel_status.py` (flat test directory, no `__init__.py`)
- Test functions: `test_*` prefix, grouped in classes (e.g., `class TestParallelStatus:`, `class TestFormatDuration:`, `class TestFormatStatusTable:`)
- Use `typer.testing.CliRunner` for CLI integration tests
- Mock `load_state()` and `get_parallel_state_path()` — don't require actual state files on disk
- Use `MINIMAL_CONFIG_DATA` autouse fixture (default) — no need to opt out
- No async needed — status command is fully synchronous

### References

- Architecture: State Persistence section — defines `ParallelState` model and read protocol
- Architecture: Parallel Module Layout — `cli.py` is the correct home for CLI commands
- Architecture: Enforcement Guidelines — all rules apply
- PRD: FR37 — user can view current parallel execution state via `parallel status`
- PRD: FR47 — status command can display story states, current phases, and dependency status
- PRD: NFR10 — status command must respond in <2 seconds
- Project Context: Typer CLI entry point pattern, frozen Pydantic models, logging convention

## Testing Requirements

- **Happy path: mixed statuses** — state has stories in various statuses (ready, running, done, blocked, failed), all displayed correctly with accurate durations
- **No state file** — `load_state()` returns `None`, prints "No parallel run state found", exits cleanly
- **All stories done** — shows all stories as done with completion summary message
- **Corrupt state file** — `load_state()` raises `ParallelError`, error printed to stderr, exit code 1
- **Duration formatting: running** — calculates elapsed time from `started_at` to now
- **Duration formatting: completed** — calculates time from `started_at` to `completed_at`
- **Duration formatting: ready** — displays dash (`-`) for stories not yet started
- **Duration formatting: edge cases** — `started_at=None` for running (defensive), zero-duration, multi-hour durations
- **Blocked story display** — `block_reason` shown in Info column, `blocked_by` shown in Blocked By column, truncated when too long
- **Failed story display** — `block_reason` shown in Info column, `failed` status displayed
- **Phase display: last_phase** — `last_phase` value shown in Phase column for non-running stories
- **Phase display: worktree peek** — for running stories, read worktree `state.yaml` to get current phase; fall back to `last_phase` on error
- **Summary counts** — correct counts per status category including `failed`
- **CLI integration** — `CliRunner.invoke()` with `parallel status` produces expected output
- **Concurrent read safety** — no file writes occur during status command (verified via mock assertions)

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/parallel/cli.py src/bmad_assist_lite/cli.py tests/test_parallel_status.py` | **PENDING** (sandbox blocked execution) |
| Typecheck | `mypy src/bmad_assist_lite/parallel/cli.py` | **PENDING** (sandbox blocked execution) |
| Build | `python -m py_compile src/bmad_assist_lite/parallel/cli.py` | **PENDING** (sandbox blocked execution) |
| Tests | `pytest tests/test_parallel_status.py -v --tb=short` | **PENDING** (sandbox blocked execution) |

**Note**: All quality gate commands require user approval in sandbox. Manual execution needed.

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (via Claude Code)

### Debug Log References
No errors encountered during implementation.

### Completion Notes List
- Adapted implementation to actual codebase models (StoryStatus uses BACKLOG/IN_FLIGHT, not READY/RUNNING; StoryState uses `error` field not `block_reason`; ParallelState uses `epic: int` not `epic_id: str`)
- Story file's "canonical" model references (from validation synthesis) did not match actual code — implemented against actual `state.py` models
- `blocked_by` and `last_phase` fields do not exist in actual `StoryState` model; Blocked By column is included but empty; Phase column shows worktree peek for in_flight stories only
- No `FAILED` status in actual `StoryStatus` enum; blocked stories with `error` serve the same diagnostic purpose
- Sandbox environment blocked all Python execution (pytest, ruff, mypy) — quality gates need manual verification
- Used `TYPE_CHECKING` guard for `ParallelState` import to avoid circular imports while maintaining mypy strict compatibility
- Added 1MB size limit on worktree state.yaml reads to prevent blocking (Task 1b.5)
- Duration calculation clamps negative values to 0 for clock skew safety

### File List
- `src/bmad_assist_lite/parallel/cli.py` — MODIFIED: Added `parallel_status`, `_utc_now`, `_peek_worktree_phase`, `_format_duration`, `_format_status_table`, `_format_summary` functions; added `yaml`, `datetime`, `TYPE_CHECKING` imports
- `src/bmad_assist_lite/cli.py` — MODIFIED: Added `parallel_status` import and registration as `parallel status` subcommand
- `tests/test_parallel_status.py` — NEW: 36 test cases across 4 test classes (TestFormatDuration, TestPeekWorktreePhase, TestFormatStatusTable, TestFormatSummary, TestParallelStatus)

## Senior Developer Review (AI)

**Date**: 2026-03-21
**Verdict**: REJECT (Pre-calculated Evidence Score: 10.8)
**Status set to**: in-progress

### Fixes Applied During Review
1. **Vacuous truth bug fixed** (`_format_summary`): `all()` on empty `state.stories` dict now guarded with `state.stories and all(...)` to prevent misleading "All stories complete!" on zero-story state.
2. **Duration format corrected** (`_format_duration`): Minutes are now omitted when zero for multi-hour durations (e.g., `"3h 5s"` instead of `"3h 0m 5s"`), per Task 2.5 spec.
3. **Test added**: `test_empty_stories_no_all_done_message` covers the vacuous truth edge case.
4. **Test updated**: `test_hours_minutes_seconds` updated for new format; `test_hours_with_minutes` added for non-zero minutes case.

### False Positives Identified (Reviewer-1 CRITICALs)
Reviewer-1 flagged 5 CRITICAL issues claiming the code uses wrong field names (`IN_FLIGHT`/`BACKLOG`/`error` instead of `RUNNING`/`READY`/`block_reason`). These are **all false positives**. The actual `StoryState` model in `state.py` uses `BACKLOG`, `IN_FLIGHT`, and `error` — the dev agent correctly implemented against the real code. The validation synthesis "corrected" canonical models to match architecture spec, but those fields/enums do not exist in the codebase.

### Remaining Issues (Architecture Gaps — Out of Scope for Status Command)
- **`Blocked By` column permanently empty**: `blocked_by` field does not exist on `StoryState`. Column present but always empty. Requires `state.py` model change (out of scope).
- **Phase display partial**: Only shows phase for `IN_FLIGHT` stories via worktree peek. `last_phase` field does not exist on `StoryState`. Requires model change.
- **Story file canonical models misleading**: Dev Notes section lists fields that don't exist in actual code. The Completion Notes acknowledge this but the main reference sections remain incorrect.
- **No `FAILED` status in actual enum**: `StoryStatus` has no `FAILED` variant; `BLOCKED` with `error` serves that purpose.

### Runtime Verification
- **Lint/Type Check**: Sandbox blocked execution — requires manual verification
- **Build**: Sandbox blocked execution — requires manual verification
- **Tests**: Sandbox blocked execution — requires manual verification (`python -m pytest tests/test_parallel_status.py -v --tb=short`)
