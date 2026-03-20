---
stepsCompleted: []
inputDocuments:
  - 'architecture.md'
  - 'prd.md'
---

# bmad-assist-lite-parallel-stories - Epic 7 Breakdown

## Epic 7: Graceful Provider Timeout & Partial Result Recovery

**Epic ID:** Epic-7
**Created:** 2026-03-19
**Status:** Ready for Development
**Priority:** High
**Points:** 16
**Stories:** 5

### Overview

Redesign the provider timeout architecture to eliminate false failures where the CLI completes work but the Python wrapper discards results due to `asyncio.wait_for` hard cancellation. Introduces a standardized `ResultCollector` pattern in `BaseProvider`, replaces hard cancellation with a proportional grace-period timeout (25% of phase timeout, minimum 60s) that distinguishes active-streaming from silent-stall, captures partial results on timeout, cleans up orphan processes, and fixes the missing `retrospective` phase default that causes 60% failure rate.

### Business Goal

Eliminate wasted LLM compute and developer time from false timeout failures. Production logs show 8 failures across 2 days where the CLI was actively working at the timeout boundary — every failure hit exactly at the configured limit (within 200ms). The retrospective phase fails 60% of the time due to a missing default. Code review synthesis fails ~11% of the time at the 600s ceiling despite successful runs routinely reaching 550-593s.

### Strategic Context

- **Immediate reliability**: 3 of 5 retrospective runs fail; 2 of 18 code_review_synthesis runs fail — all due to timeout, not LLM errors
- **Wasted compute**: On timeout, the Claude CLI subprocess continues running (orphaned), consuming API credits while the pipeline declares failure
- **Future-proofing**: New providers (HTTP API, gRPC, local models) all need the same timeout pattern — standardize now before adding more providers
- **Data-driven**: Every failure in logs hits exactly at the timeout boundary (300,081ms / 600,099ms / 600,126ms), confirming the CLI was still actively working

### Dependencies

- None — this epic modifies core provider infrastructure used by both sequential and parallel loops

### Context7 Library Documentation

<!-- No external library documentation needed. All changes use Python stdlib (asyncio, threading, time)
     and the existing claude_agent_sdk package (already in use, no new APIs needed). -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|
| *(none)* | — | — | — |

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Provider Subsystem; Key Architectural Patterns; Existing Patterns (from project-context.md); New Patterns for Parallel Module; Enforcement Guidelines |
| `prd.md` | Non-Functional Requirements |
| `project-context.md` | `(full)` |

### Recommended Story Order

1. 7-1-fix-phase-timeout-defaults — Bug fix for missing `retrospective` default + data-driven bumps. Zero risk, immediate value.
2. 7-2-result-collector — New `ResultCollector` class with thread-safe accumulation + activity tracking. Foundation for all subsequent stories.
3. 7-3-graceful-timeout-base-provider — Standardized timeout contract in `BaseProvider` using `ResultCollector`. Defines the interface future providers inherit.
4. 7-4-claude-sdk-graceful-timeout — Migrate Claude SDK provider to new pattern with partial capture + orphan cleanup.
5. 7-5-gemini-provider-alignment-and-epic-documentation-sync — Align Gemini provider to new `BaseProvider` contract + documentation sync.

---

### Story 7.1: Fix Phase Timeout Defaults

**Story ID:** 7-1-fix-phase-timeout-defaults
**Component:** `src/bmad_assist_lite/core/config.py`
**Estimate:** Small
**Points:** 1
**Priority:** High
**Dependencies:** []

#### User Story

As a developer running the BMAD loop,
I want every LLM phase to have a data-driven timeout default,
So that phases don't fail due to missing or too-tight timeout configuration.

#### Description

Add `retrospective` to `_PHASE_DEFAULTS` (currently missing — falls to 300s global default causing 60% failure rate). Bump `code_review_synthesis` and `fix_quality_gate` defaults based on production duration data. Add a regression test ensuring every LLM phase has an entry in `_PHASE_DEFAULTS`.

#### Current State

`_PHASE_DEFAULTS` in `config.py:104-114` is missing `retrospective`. Current values:
```python
_PHASE_DEFAULTS: dict[str, int] = {
    "create_story": 900,
    "validate_story": 600,
    "validate_story_synthesis": 600,
    "dev_story": 1200,
    "code_review": 900,
    "code_review_synthesis": 600,
    "quality_gate": 300,
    "fix_quality_gate": 600,
    "epic_quality_gate": 600,
}
```

Production failure data (March 17-18):
- `retrospective`: 3/5 runs FAIL at 300s (successes at 273s, 290s)
- `code_review_synthesis`: 2/18 runs FAIL at 600s (P90 of successes: ~555s)
- `fix_quality_gate`: 1/14 runs FAIL at 600s (outlier success at 454s)

#### Target State

```python
_PHASE_DEFAULTS: dict[str, int] = {
    "create_story": 900,
    "validate_story": 600,
    "validate_story_synthesis": 600,
    "dev_story": 1200,
    "code_review": 900,
    "code_review_synthesis": 900,   # was 600, P90=555s needs headroom
    "quality_gate": 300,
    "fix_quality_gate": 900,        # was 600, outlier at 454s + large prompt risk
    "epic_quality_gate": 600,
    "retrospective": 600,           # was MISSING (fell to 300s default)
}
```

Plus a regression test that fails if any phase in the story loop or epic teardown is missing from `_PHASE_DEFAULTS`.

#### Acceptance Criteria

**Given** the BMAD loop configuration is loaded with default settings
**When** `get_timeout("retrospective")` is called
**Then** it returns 600 (not the global default 300)

**Given** the BMAD loop configuration is loaded with default settings
**When** `get_timeout("code_review_synthesis")` is called
**Then** it returns 900

**Given** a new LLM phase is added to the story loop or epic teardown in config
**When** the regression test runs
**Then** it fails if the phase is not in `_PHASE_DEFAULTS`

#### Technical Notes

- The phase list is defined in `LoopConfig` (config.py) as `story` and `epic_teardown` lists
- The regression test should parametrize over all phases from both lists, excluding non-LLM phases (`quality_gate`, `epic_quality_gate`)
- Keep `quality_gate` at 300s and `epic_quality_gate` at 600s — these are non-LLM phases with different timeout characteristics

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Configuration-only change, no user-facing behavior affected

---

### Story 7.2: ResultCollector — Thread-Safe Partial Result Accumulator

**Story ID:** 7-2-result-collector
**Component:** `src/bmad_assist_lite/providers/result_collector.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** []

#### User Story

As a provider implementor,
I want a thread-safe result accumulator that tracks content and streaming activity,
So that timeout logic can capture partial results and distinguish active-streaming from silent-stall.

#### Description

Create a `ResultCollector` class that providers feed incrementally as LLM response chunks arrive. The collector tracks accumulated text and the timestamp of the last chunk, enabling the timeout layer to make intelligent decisions: if the provider was actively streaming at the timeout boundary, grant a grace period; if silent for a long time, fail immediately.

#### Current State

No shared result accumulation pattern exists. Each provider manages response collection independently:
- **Claude SDK** (`claude_sdk.py:66`): `response_parts: list[str]` local variable in `_invoke_async`. Lost on `CancelledError`.
- **Gemini** (`gemini.py`): `response_text_parts: list[str]` captured in closure, available for `partial_result` on timeout.

No activity tracking exists in either provider.

#### Target State

```python
# src/bmad_assist_lite/providers/result_collector.py

class ResultCollector:
    """Thread-safe accumulator for incremental LLM response chunks.

    Tracks content and last-activity timestamp for timeout intelligence.
    """

    def __init__(self) -> None: ...

    def add(self, chunk: str) -> None:
        """Append a chunk (thread-safe). Updates last_chunk_at."""
        ...

    @property
    def text(self) -> str:
        """Return accumulated text (thread-safe snapshot)."""
        ...

    @property
    def last_chunk_at(self) -> float | None:
        """Monotonic timestamp of last add() call, or None if no chunks."""
        ...

    @property
    def chunk_count(self) -> int:
        """Number of chunks added."""
        ...

    @property
    def is_empty(self) -> bool:
        """True if no chunks have been added."""
        ...

    def is_active(self, threshold_seconds: float = 30.0) -> bool:
        """True if a chunk was added within threshold_seconds of now."""
        ...
```

#### Acceptance Criteria

**Given** a new `ResultCollector` instance
**When** `add("hello ")` and `add("world")` are called
**Then** `text` returns `"hello world"` and `chunk_count` returns 2

**Given** a `ResultCollector` with chunks added
**When** `last_chunk_at` is read
**Then** it returns a monotonic timestamp from the most recent `add()` call

**Given** a `ResultCollector` that received a chunk 5 seconds ago
**When** `is_active(threshold_seconds=30.0)` is called
**Then** it returns `True`

**Given** a `ResultCollector` that received a chunk 60 seconds ago
**When** `is_active(threshold_seconds=30.0)` is called
**Then** it returns `False`

**Given** two threads calling `add()` concurrently
**When** both complete
**Then** all chunks are captured without data corruption

**Given** a `ResultCollector` with no chunks added
**When** `is_empty` is checked
**Then** it returns `True` and `last_chunk_at` returns `None`

#### Technical Notes

- Use `threading.Lock` for thread safety (not `asyncio.Lock` — collector is used from both sync and async contexts)
- Use `time.monotonic()` for `last_chunk_at` (immune to wall-clock adjustments)
- Keep it simple: no max-size limits, no callbacks, no serialization. Just accumulate and track.
- This class is deliberately decoupled from providers — it's a utility that both providers will use

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Internal utility class, no user-facing behavior

---

### Story 7.3: Graceful Timeout Contract in BaseProvider

**Story ID:** 7-3-graceful-timeout-base-provider
**Component:** `src/bmad_assist_lite/providers/base.py`
**Estimate:** Medium
**Points:** 4
**Priority:** High
**Dependencies:** [7-2-result-collector]

#### User Story

As a provider implementor (current or future),
I want `BaseProvider` to define a standardized timeout contract with grace period and partial result capture,
So that all providers inherit consistent timeout behavior without re-implementing it.

#### Description

Refactor `BaseProvider` to provide a concrete `invoke()` method that wraps the provider-specific `_do_invoke()` with standardized timeout logic. On timeout, it checks the `ResultCollector` for activity (active-streaming vs. silent-stall), optionally extends a grace period, captures partial results, and calls `_cleanup()` for provider-specific resource teardown. Individual providers implement `_do_invoke()` and `_cleanup()` instead of `invoke()`.

#### Current State

`BaseProvider.invoke()` is abstract — each provider implements its own timeout logic independently:
- **Claude SDK**: `asyncio.wait_for()` → hard cancel → discard results → orphan process
- **Gemini**: `process.wait(timeout)` → `kill_process()` → capture partial → raise with partial

`ProviderResult` has no field to indicate timeout status. `BaseProvider` has a no-op `cancel()` method that is never called on timeout.

#### Target State

```python
# Updated BaseProvider contract

MIN_GRACE_PERIOD_SECONDS: int = 60
ACTIVE_STREAM_THRESHOLD: float = 30.0
MIN_USEFUL_RESPONSE_CHARS: int = 200

@dataclass(frozen=True)
class ProviderResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    model: str | None
    command: tuple[str, ...]
    provider_session_id: str | None = None
    timed_out: bool = False           # NEW: indicates result was captured after timeout

class BaseProvider(ABC):
    def invoke(self, prompt, *, timeout, **kwargs) -> ProviderResult:
        """Concrete method: wraps _do_invoke with timeout + grace + cleanup."""
        collector = ResultCollector()
        try:
            return self._do_invoke(prompt, collector=collector, timeout=timeout, **kwargs)
        except TimeoutError:
            return self._handle_timeout(collector, timeout, ...)
        finally:
            self._cleanup()

    def _handle_timeout(self, collector, timeout, ...) -> ProviderResult:
        """Grace period logic: active → extend, silent → fail, partial → return."""
        if collector.is_active(ACTIVE_STREAM_THRESHOLD):
            # Proportional grace: 25% of phase timeout, minimum 60s
            grace_seconds = max(MIN_GRACE_PERIOD_SECONDS, int(timeout * 0.25))
            result = self._wait_for_grace(collector, grace_seconds)
            if result:
                return result

        partial = collector.text
        if partial and len(partial) >= MIN_USEFUL_RESPONSE_CHARS:
            logger.warning("Timeout after %ds, returning partial (%d chars)", timeout, len(partial))
            return ProviderResult(stdout=partial, timed_out=True, ...)

        raise ProviderTimeoutError(f"Timeout after {timeout}s", partial_result=...)

    @abstractmethod
    def _do_invoke(self, prompt, *, collector, timeout, **kwargs) -> ProviderResult:
        """Provider-specific invocation. Must call collector.add() as chunks arrive."""
        ...

    @abstractmethod
    def _cleanup(self) -> None:
        """Provider-specific resource cleanup (kill process, close connection, etc.)."""
        ...
```

#### Acceptance Criteria

**Given** a provider's `_do_invoke()` completes within the timeout
**When** `invoke()` returns
**Then** the result has `timed_out=False` and contains full response text

**Given** a provider's `_do_invoke()` exceeds timeout while actively streaming (last chunk < 30s ago)
**When** the timeout fires
**Then** a proportional grace period is granted: `max(60, timeout * 0.25)` seconds (e.g., 150s for a 600s timeout, 300s for a 1200s `dev_story`)

**Given** a provider's `_do_invoke()` exceeds timeout while silent (last chunk > 30s ago)
**When** the timeout fires
**Then** no grace period is granted — failure is immediate

**Given** a timeout occurs and the collector has >= 200 chars of accumulated text
**When** the timeout handler runs
**Then** it returns a `ProviderResult` with `timed_out=True` and the partial text

**Given** a timeout occurs and the collector has < 200 chars
**When** the timeout handler runs
**Then** it raises `ProviderTimeoutError`

**Given** any timeout or error occurs
**When** `invoke()` exits
**Then** `_cleanup()` is called (verified via mock)

**Given** a future provider inherits from `BaseProvider`
**When** it implements only `_do_invoke()` and `_cleanup()`
**Then** it automatically gets grace period, partial capture, and activity detection

#### Technical Notes

- `_handle_timeout` and `_wait_for_grace` are concrete methods on `BaseProvider` — not abstract
- `_wait_for_grace` needs to be provider-agnostic: it polls `collector.is_active()` in a short loop for up to `grace_seconds`. If a new chunk arrives during grace, the wait resets. If the collector stops receiving, it times out.
- **Proportional grace period**: `grace_seconds = max(MIN_GRACE_PERIOD_SECONDS, int(timeout * 0.25))`. This scales naturally with phase duration — `dev_story` at 1200s gets 300s (5 min) grace, `validate_story` at 600s gets 150s, short phases get the 60s floor. The grace period only activates if the stream was active at the timeout boundary.
- The `timed_out` field on `ProviderResult` is a backward-compatible addition (default `False`)
- Handler-level policy (how to treat `timed_out=True` results) is NOT part of this story — that's downstream. This story just provides the mechanism.
- The existing `cancel()` method on `BaseProvider` should be replaced by `_cleanup()` to avoid confusion
- Constants (`MIN_GRACE_PERIOD_SECONDS`, `ACTIVE_STREAM_THRESHOLD`, `MIN_USEFUL_RESPONSE_CHARS`) should be module-level, not config-driven — keep it simple. The 0.25 multiplier is also a module-level constant: `GRACE_PERIOD_RATIO: float = 0.25`

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Internal provider infrastructure, no user-facing behavior

---

### Story 7.4: Claude SDK Provider — Graceful Timeout Migration

**Story ID:** 7-4-claude-sdk-graceful-timeout
**Component:** `src/bmad_assist_lite/providers/claude_sdk.py`
**Estimate:** Large
**Points:** 5
**Priority:** High
**Dependencies:** [7-3-graceful-timeout-base-provider]

#### User Story

As the BMAD loop operator,
I want the Claude SDK provider to capture partial results and clean up orphan processes on timeout,
So that completed or near-complete CLI work is not discarded, and orphan `claude.exe` processes don't waste compute.

#### Description

Migrate `ClaudeSDKProvider` from direct `asyncio.wait_for()` hard cancellation to the new `BaseProvider._do_invoke()` / `_cleanup()` contract. The provider feeds the shared `ResultCollector` as messages stream from the Claude Agent SDK. On timeout, the base class handles grace period and partial capture. `_cleanup()` attempts to terminate the underlying `claude.exe` subprocess.

#### Current State

`claude_sdk.py` invoke flow:
1. `invoke()` calls `run_async_in_thread(asyncio.wait_for(_invoke_async(...), timeout=...))`
2. `_invoke_async()` iterates `async for message in query(...)` and accumulates `response_parts`
3. On timeout: `asyncio.wait_for` raises `TimeoutError` → `CancelledError` injected into iterator
4. `response_parts` (local variable) is lost
5. `claude.exe` subprocess (owned by `claude_agent_sdk`) continues running — no cleanup
6. `ProviderTimeoutError` raised with no partial result

Log evidence: `claude_agent_sdk._internal.query: Read task cancelled` appears on every timeout — the SDK detects cancellation but doesn't kill the subprocess.

#### Target State

1. `_do_invoke()` replaces `invoke()` — feeds `collector.add(block.text)` as `TextBlock` messages arrive
2. `_cleanup()` attempts to find and terminate orphan `claude.exe` processes spawned by this invocation
3. On timeout, `BaseProvider._handle_timeout()` checks collector activity and captures partial text
4. Proportional grace period (`max(60s, 25% of timeout)`) allows a streaming response to finish if it was actively producing output — e.g., `dev_story` with 1200s timeout gets up to 300s (5 min) of grace

For orphan cleanup, since the `claude_agent_sdk` doesn't expose the subprocess PID directly:
- Track the PID by scanning for new `claude.exe` processes before/after spawning the query
- Or use the SDK's internal query object if it exposes a cancellation/cleanup method
- Fallback: log a warning about potential orphan if PID cannot be determined

#### Acceptance Criteria

**Given** the Claude SDK completes a response within timeout
**When** `invoke()` returns
**Then** behavior is identical to current — full response, `timed_out=False`

**Given** the Claude SDK is actively streaming text and the timeout fires
**When** the grace period begins
**Then** the provider waits up to 60s for completion, checking collector activity

**Given** the Claude SDK times out after grace period with 500+ chars accumulated
**When** the timeout handler runs
**Then** a `ProviderResult` is returned with `timed_out=True` and the partial text

**Given** the Claude SDK times out
**When** `_cleanup()` runs
**Then** the orphan `claude.exe` process is terminated (or a warning is logged if PID cannot be found)

**Given** the Claude SDK times out with no response text
**When** the timeout handler runs
**Then** `ProviderTimeoutError` is raised (same as current behavior)

**Given** the `ResultCollector` is being fed from the async `query()` stream
**When** chunks arrive from different `AssistantMessage` objects
**Then** all `TextBlock.text` values are captured in the collector

#### Technical Notes

- The `claude_agent_sdk.query()` function returns an async generator. Each `AssistantMessage` can contain multiple `TextBlock` items. Feed each `block.text` to `collector.add()`.
- The async nature means `_do_invoke()` needs to bridge async→sync for the collector. Since `ResultCollector` uses `threading.Lock` (not asyncio), it's safe to call from async context.
- For `_cleanup()`: investigate `claude_agent_sdk._internal.query.Query` for a `cancel()` or `close()` method. If none exists, use PID-based cleanup via `_windows.py:terminate_process()`.
- Keep the existing `run_async_in_thread` + `asyncio.wait_for` pattern for the hard timeout boundary. The change is: (a) feed collector during iteration, (b) on `TimeoutError`, let base class handle grace + partial instead of immediately raising.
- Test with mock SDK `query()` that yields messages with configurable delays.

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Internal provider change, no user-facing behavior

---

### Story 7.5: Gemini Provider Alignment & Epic Documentation Sync

**Story ID:** 7-5-gemini-alignment-and-documentation-sync
**Component:** `src/bmad_assist_lite/providers/gemini.py`, `CLAUDE.md`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [7-3-graceful-timeout-base-provider]

#### User Story

As a developer (human or AI),
I want the Gemini provider aligned to the new `BaseProvider` contract and project documentation updated,
So that both providers share consistent timeout behavior and future implementation decisions are based on accurate information.

#### Description

Two parts:

**Part A — Gemini Provider Alignment:** Migrate `GeminiProvider` from its current inline timeout handling to the `BaseProvider._do_invoke()` / `_cleanup()` contract. The Gemini provider already handles timeout better than Claude SDK (captures partial, kills process), but it should use `ResultCollector` for consistency and to get the grace period / activity detection behavior.

**Part B — Documentation Sync:** Audit all changes from Epic 7 and update `CLAUDE.md` to reflect the new provider timeout architecture, `ResultCollector` pattern, and `BaseProvider` contract.

#### Current State

**Gemini provider** (`gemini.py:233-252`):
- Uses `process.wait(timeout=effective_timeout)` for subprocess timeout
- On `TimeoutExpired`: calls `kill_process(process)`, builds `partial_result` from `response_text_parts`, raises `ProviderTimeoutError` with partial
- No activity tracking (no distinction between active streaming and silent stall)
- No grace period — immediate kill on timeout

**Documentation**: `CLAUDE.md` describes providers but doesn't mention timeout architecture, `ResultCollector`, or grace period pattern.

#### Target State

**Gemini provider**:
- `_do_invoke()` replaces inline timeout logic. Feeds `collector.add()` from JSON stream processing.
- `_cleanup()` calls `kill_process(process)` (already implemented, just needs to be extracted to the hook method)
- Inherits grace period and activity detection from `BaseProvider`
- Existing retry logic for transient Gemini errors (`exit_code != 0` + empty stderr) remains unchanged

**Documentation** (`CLAUDE.md`):
- Update "Core Subsystems > providers/" section to describe `ResultCollector`, `BaseProvider` timeout contract, grace period behavior
- Update "Key Patterns" to include the graceful timeout pattern
- Add provider implementor checklist: `_do_invoke()` + `_cleanup()` + `collector.add()` requirements

#### Acceptance Criteria

**Given** the Gemini CLI completes a response within timeout
**When** `invoke()` returns
**Then** behavior is identical to current — full response, `timed_out=False`

**Given** the Gemini CLI is actively streaming JSON and the timeout fires
**When** the grace period begins
**Then** the provider waits up to `max(60, timeout * 0.25)` seconds, checking collector activity

**Given** the Gemini CLI times out with accumulated partial text
**When** the timeout handler runs
**Then** `ProviderResult` is returned with `timed_out=True` and partial content

**Given** the Gemini CLI times out
**When** `_cleanup()` runs
**Then** `kill_process()` terminates the subprocess (same as current behavior, just via the new hook)

**Given** all Epic 7 stories are complete
**When** the documentation sync runs
**Then** `CLAUDE.md` accurately describes the new provider timeout architecture

**Given** a future provider implementor reads `CLAUDE.md`
**When** they implement a new provider
**Then** they find clear instructions for `_do_invoke()`, `_cleanup()`, and `ResultCollector` usage

#### Technical Notes

- The Gemini provider's JSON stream processing (`process_json_stream` inner function) currently feeds `response_text_parts`. Change it to also feed `collector.add()` for each text chunk extracted from JSON messages.
- The existing retry loop for transient Gemini errors (503, empty stderr) should remain in `_do_invoke()` — it's provider-specific behavior that the base class doesn't need to know about.
- For documentation: follow the patterns established in existing `CLAUDE.md` sections. Don't over-document — focus on what a provider implementor needs to know.
- Do NOT update `architecture.md` or `prd.md` — those are planning artifacts. If they need changes, flag for course correction.

#### Doc Audit Checklist

##### Tier 1: Core Docs (Always Evaluate)

**`CLAUDE.md`:**
- [ ] New module added (`providers/result_collector.py`)? → Update project structure
- [ ] New architectural pattern (graceful timeout, ResultCollector)? → Update Core Subsystems and Key Patterns
- [ ] Provider interface changed (`BaseProvider` contract)? → Update Architecture section
- [ ] New constants (`GRACE_PERIOD_SECONDS`, etc.)? → Document in provider section

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Internal provider change + documentation, no user-facing behavior

---

## Test Impact Summary

### Unit / Integration Tests

| Test File | Stories Affected | Changes |
|-----------|------------------|---------|
| `tests/test_config.py` | 7.1 | Add parametrized test: every LLM phase has `_PHASE_DEFAULTS` entry |
| `tests/test_result_collector.py` (new) | 7.2 | Thread safety, activity tracking, accumulation, edge cases |
| `tests/test_provider_timeout.py` (new) | 7.3, 7.4, 7.5 | BaseProvider timeout contract, grace period, partial capture, cleanup hook |
| `tests/test_claude_sdk_timeout.py` (new) | 7.4 | Mock SDK query stream, timeout + partial capture, orphan cleanup |
| `tests/test_gemini_timeout.py` (new) | 7.5 | Mock subprocess, timeout + partial capture via new contract |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 7.1 | None | — | — | Config-only |
| 7.2 | None | — | — | Internal utility |
| 7.3 | None | — | — | Provider infrastructure |
| 7.4 | None | — | — | Provider infrastructure |
| 7.5 | None | — | — | Provider + docs |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests updated and passing (`pytest -q --tb=short --no-header`)
- [ ] `ruff check src/ && mypy src/` passes
- [ ] No regression in existing test suite
- [ ] `retrospective` phase no longer fails at 300s default
- [ ] `code_review_synthesis` has 900s default
- [ ] `ResultCollector` is used by both Claude SDK and Gemini providers
- [ ] Both providers implement `_do_invoke()` and `_cleanup()` contract
- [ ] Partial results captured on timeout (verified by test)
- [ ] Grace period distinguishes active-streaming from silent-stall (verified by test)
- [ ] Orphan `claude.exe` cleanup attempted on timeout
- [ ] `CLAUDE.md` updated with new provider timeout architecture
- [ ] Manual QA: run a full story loop and verify timeout behavior in logs

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Claude Agent SDK doesn't expose subprocess PID for orphan cleanup | Medium | Low | Fallback to PID scanning or warning log. Orphan cleanup is best-effort. |
| `BaseProvider.invoke()` becoming concrete breaks existing provider tests | Low | Medium | Both providers are internal — update tests alongside implementation |
| Grace period extends total phase duration beyond expectations | Low | Low | Grace period is proportional (25% of timeout, min 60s) and only activates when stream is active. Max effective ceiling: `dev_story` 1200s + 300s = 1500s. Logged clearly so operators see it. |
| Thread safety issues in `ResultCollector` under high concurrency | Low | Medium | Stress test with concurrent writers in test suite |

## Rollback Plan

Each story is independently revertable:
- **Story 7.1**: Revert `_PHASE_DEFAULTS` changes in `config.py` (one dict change)
- **Stories 7.2-7.5**: Revert to pre-epic `BaseProvider` abstract `invoke()`. Delete `result_collector.py`. Restore original `claude_sdk.py` and `gemini.py` `invoke()` methods. All changes are in the `providers/` and `core/` directories.
