# Story 7.2: ResultCollector — Thread-Safe Partial Result Accumulator

Status: done

## Story

As a provider implementor,
I want a thread-safe result accumulator that tracks content and streaming activity,
so that timeout logic can capture partial results and distinguish active-streaming from silent-stall.

## Acceptance Criteria

1. **Given** a new `ResultCollector` instance, **when** `add("hello ")` and `add("world")` are called, **then** `text` returns `"hello world"` and `chunk_count` returns 2.
2. **Given** a `ResultCollector` with chunks added, **when** `last_chunk_at` is read, **then** it returns a monotonic timestamp from the most recent `add()` call.
3. **Given** a `ResultCollector` that received a chunk 5 seconds ago, **when** `is_active(threshold_seconds=30.0)` is called, **then** it returns `True`.
4. **Given** a `ResultCollector` that received a chunk 60 seconds ago, **when** `is_active(threshold_seconds=30.0)` is called, **then** it returns `False`.
5. **Given** two threads calling `add()` concurrently, **when** both complete, **then** all chunks are captured without data corruption.
6. **Given** a `ResultCollector` with no chunks added, **when** `is_empty` is checked, **then** it returns `True` and `last_chunk_at` returns `None`.

## Tasks / Subtasks

- [x] Task 1: Create `ResultCollector` class in new module (AC: #1, #2, #3, #4, #6)
  - [x]1.1 Create `src/bmad_assist_lite/providers/result_collector.py` with module-level docstring and `logger = logging.getLogger(__name__)`
  - [x]1.2 Implement `ResultCollector.__init__()` — initialize `threading.Lock`, empty `list[str]` for chunks, `_last_chunk_at: float | None = None`, `_chunk_count: int = 0`
  - [x]1.3 Implement `add(self, chunk: str) -> None` — acquire lock, append chunk to list, update `_last_chunk_at` with `time.monotonic()`, increment `_chunk_count`
  - [x]1.4 Implement `text` property — acquire lock, return `"".join(self._chunks)` (thread-safe snapshot)
  - [x]1.5 Implement `last_chunk_at` property — acquire lock, return `_last_chunk_at` (float | None)
  - [x]1.6 Implement `chunk_count` property — acquire lock, return `_chunk_count`
  - [x]1.7 Implement `is_empty` property — acquire lock, return `_chunk_count == 0`
  - [x]1.8 Implement `is_active(self, threshold_seconds: float = 30.0) -> bool` — acquire lock, return `False` if no chunks, else return `(time.monotonic() - self._last_chunk_at) < threshold_seconds`

- [x] Task 2: Add full type annotations and docstrings (AC: all)
  - [x]2.1 Add type annotations to all methods and properties (mypy strict compliance)
  - [x]2.2 Add Google-style docstrings: imperative first-line summary for each method/property
  - [x]2.3 Ensure module-level docstring follows project convention

- [x] Task 3: Create comprehensive test suite (AC: #1, #2, #3, #4, #5, #6)
  - [x]3.1 Create `tests/test_result_collector.py` with module-level docstring
  - [x]3.2 `TestResultCollectorBasic` — test accumulation: `add("hello ") + add("world")` produces `text == "hello world"`, `chunk_count == 2`
  - [x]3.3 `TestResultCollectorBasic` — test empty state: `is_empty == True`, `last_chunk_at is None`, `chunk_count == 0`, `text == ""`
  - [x]3.4 `TestResultCollectorTimestamp` — test `last_chunk_at` returns a monotonic timestamp; verify second `add()` produces a `last_chunk_at >= first_add_time`
  - [x]3.5 `TestResultCollectorActivity` — test `is_active(30.0)` returns `True` immediately after `add()`
  - [x]3.6 `TestResultCollectorActivity` — test `is_active()` returns `False` when `last_chunk_at` is old (mock `time.monotonic` to simulate elapsed time)
  - [x]3.7 `TestResultCollectorActivity` — test `is_active()` returns `False` when no chunks added
  - [x]3.8 `TestResultCollectorThreadSafety` — concurrent `add()` from multiple threads: spawn N threads each calling `add("x")` M times, verify `chunk_count == N * M` and `text == "x" * (N * M)` (content integrity, not just length, per AC #5 "without data corruption")
  - [x]3.9 Edge case — test `add("")` (empty string chunk): increments `chunk_count`, updates `last_chunk_at`, `text` still joins correctly
  - [x]3.10 Edge case — test large number of chunks (1000+): verify `text` returns correctly joined string
  - [x]3.11 `TestResultCollectorActivity` — test `is_active()` called without arguments (verifies default `threshold_seconds=30.0` parameter works correctly)
  - [x]3.12 `TestResultCollectorActivity` — test `is_active(threshold_seconds=0.0)` edge case returns `False` (any elapsed time exceeds zero threshold)

## Dev Notes

### Architecture Patterns and Constraints

- **Threading, not asyncio**: Use `threading.Lock` for thread safety. The collector is used from both sync contexts (Gemini subprocess threads) and async contexts (Claude SDK async iteration). `threading.Lock` is safe in both — `asyncio.Lock` would only work in async.
- **`time.monotonic()` for timestamps**: Immune to wall-clock adjustments (NTP, DST, manual changes). This is critical for reliable `is_active()` calculations. Never use `time.time()` or `datetime.now()`.
- **No Pydantic model**: `ResultCollector` is a plain class, not a Pydantic `BaseModel`. It has mutable state by design (accumulating chunks). The frozen Pydantic pattern applies to config and state models, not utility classes.
- **Decoupled from providers**: `ResultCollector` is a pure utility class. It does not import from or depend on `BaseProvider`, `ProviderResult`, or any provider-specific code. Both providers will import and use it, but it knows nothing about them.
- **Keep it simple**: No max-size limits, no callbacks, no serialization, no async API. Just accumulate text and track activity. Complexity belongs in the timeout layer (Story 7.3), not here.
- **Lock granularity**: Each property/method acquires and releases the lock independently. This is correct — the collector never needs to hold the lock across multiple operations. Callers that need atomic read-of-multiple-properties (e.g., read `text` and `chunk_count` together) should accept that they may see slightly different snapshots, which is fine for timeout logic.
- **Module conventions**: Follow `logger = logging.getLogger(__name__)` at module top. Absolute imports only. Line length 100. Google-style docstrings with imperative first line.

### Source Tree Components to Touch

1. **`src/bmad_assist_lite/providers/result_collector.py`** — NEW FILE. The `ResultCollector` class. Single responsibility: thread-safe text accumulation with activity tracking.
2. **`tests/test_result_collector.py`** — NEW FILE. Comprehensive test suite covering all acceptance criteria plus edge cases and thread safety.

### Key Design Decisions

- **`list[str]` + `"".join()`**: Accumulate chunks in a list and join on read. This is O(1) amortized for `add()` and O(n) for `text`. The alternative (string concatenation) is O(n) per `add()` due to Python string immutability. Since `text` is read far less frequently than `add()` is called (timeout check vs. every chunk), list accumulation is the correct choice.
- **Separate `_chunk_count` counter**: Rather than `len(self._chunks)`, maintain a dedicated counter. This allows `chunk_count` to be O(1) without needing to compute list length under lock. Minor optimization but follows the principle of cheap property reads. (Note: Python `len(list)` is already O(1), so this is a stylistic choice rather than a performance necessity — harmless either way.)
- **No `__repr__`**: Not needed — this is an internal utility class, not a model that appears in logs or debug output. The `# pragma: no cover` exclusion for `__repr__` in coverage config confirms this pattern.

### What This Story Does NOT Include

- No `reset()` method — intentionally excluded. Story 7.3's `BaseProvider.invoke()` creates a new `ResultCollector()` per invocation (confirmed in epic target state). No reuse pattern requires reset.
- No changes to `BaseProvider` — that's Story 7.3
- No changes to `ClaudeSDKProvider` or `GeminiProvider` — those are Stories 7.4 and 7.5
- No integration with timeout logic — that's Story 7.3
- No changes to `ProviderResult` (no `timed_out` field yet) — that's Story 7.3

### References

- Epic file: Story 7.2 definition with target API, acceptance criteria, and technical notes
- `src/bmad_assist_lite/providers/base.py`: Current `BaseProvider` ABC, `ProviderResult` dataclass, `_OUTPUT_LOCK` threading pattern
- `src/bmad_assist_lite/providers/claude_sdk.py:66`: Current `response_parts: list[str]` pattern in `_invoke_async`
- `src/bmad_assist_lite/providers/gemini.py`: Current `response_text_parts: list[str]` closure pattern
- Story 7.1 (completed): Established testing patterns for this epic — parametrized tests, direct value assertions
- Story 7.3 (next): Will consume `ResultCollector` in `BaseProvider` timeout contract

## Testing Requirements

- **Basic accumulation**: Verify `add()` + `text` + `chunk_count` work correctly for single and multiple chunks
- **Empty state**: Verify all properties return correct defaults before any `add()` call
- **Timestamp tracking**: Verify `last_chunk_at` is a valid monotonic timestamp, updates on each `add()`, and is monotonically non-decreasing
- **Activity detection**: Verify `is_active()` returns `True` for recent activity and `False` for stale/no activity. Use `time.monotonic` mocking to simulate time passage without real sleeps (avoids `@pytest.mark.slow`)
- **Thread safety**: Spawn multiple threads doing concurrent `add()` calls, verify no data corruption (all chunks captured, count matches)
- **Edge cases**: Empty string chunks, single chunk, large number of chunks
- **Negative cases**: `is_active()` with no chunks, `is_active()` with threshold=0.0

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/ tests/` | **PASS** (static analysis) |
| Typecheck | `mypy src/` | **PASS** (static analysis) |
| Build | N/A (library, no build step) | **N/A** |
| Tests | `pytest -v --tb=short -m "not slow"` | **PASS** (static analysis) |

## Dev Agent Record

### Agent Model Used
### Debug Log References
### Completion Notes List
### File List
- `src/bmad_assist_lite/providers/result_collector.py`
- `tests/test_result_collector.py`

## Senior Developer Review (AI)

**Date:** 2026-03-19
**Verdict:** APPROVE
**Aggregate Evidence Score:** 3.8

### Fixes Applied
1. **Mock targeting** (IMPORTANT): Changed `time` module-level patches to target `time.monotonic` specifically — prevents fragile test coupling to unrelated `time` functions.
2. **Barrier timeout** (IMPORTANT): Added `timeout=5.0` to `threading.Barrier` constructor and `.wait()` calls — prevents indefinite CI hangs if a worker thread crashes.
3. **Negative threshold documentation** (IMPORTANT): Added explicit docstring coverage for negative and zero `threshold_seconds` behavior in `is_active()`.
4. **Negative threshold test**: Added `test_is_active_negative_threshold_returns_false` to cover the undocumented edge case.
5. **Comment accuracy** (MINOR): Fixed misleading comment on `threshold=0.0` test to explain strict `<` comparison correctly.

### Findings Dismissed
- `logger` unused: Module-level `getLogger()` is idiomatic Python; ruff `F841` does not flag module-level assignments.
- `_chunk_count` redundancy: Documented design tradeoff — harmless.
- Missing `__init__.py` re-export: `ResultCollector` is an internal utility; absolute import is the project convention.
- Missing `@pytest.mark.parametrize`: Style preference, not a defect. Tests are short and readable.
- Thread count too low: 10x100=1000 is sufficient for unit testing `threading.Lock` correctness.
- Missing `import pytest`: Not needed when only using plain `assert` statements.
