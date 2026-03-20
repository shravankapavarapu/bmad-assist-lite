# Story 7.1: Fix Phase Timeout Defaults

Status: in-progress

## Story

As a developer running the BMAD loop,
I want every LLM phase to have a data-driven timeout default,
so that phases don't fail due to missing or too-tight timeout configuration.

## Acceptance Criteria

1. **Given** the BMAD loop configuration is loaded with default settings, **when** `get_timeout("retrospective")` is called, **then** it returns 600 (not the global default 300).
2. **Given** the BMAD loop configuration is loaded with default settings, **when** `get_timeout("code_review_synthesis")` is called, **then** it returns 900.
3. **Given** the BMAD loop configuration is loaded with default settings, **when** `get_timeout("fix_quality_gate")` is called, **then** it returns 900.
4. **Given** a new LLM phase is added to the story loop or epic teardown in config, **when** the regression test runs, **then** it fails if the phase is not in `_PHASE_DEFAULTS`.

## Tasks / Subtasks

- [x] Task 1: Add `retrospective` to `_PHASE_DEFAULTS` and bump timeout values (AC: #1, #2, #3)
  - [x] 1.1 Add `"retrospective": 600` to `_PHASE_DEFAULTS` dict in `TimeoutsConfig` (config.py line ~114)
  - [x] 1.2 Change `"code_review_synthesis"` from `600` to `900` (config.py line ~110)
  - [x] 1.3 Change `"fix_quality_gate"` from `600` to `900` (config.py line ~112)
- [x] Task 2: Update existing test that asserts retrospective falls through to default (AC: #1)
  - [x] 2.1 Update `test_get_phase_timeout_with_timeouts` in `test_config.py` — the assertion at line 151 (`assert get_phase_timeout(cfg, "retrospective") == 200`) must change. With `retrospective` now in `_PHASE_DEFAULTS` at 600, and the test's explicit `timeouts.default` of 200, the phase-specific default (600) wins over `timeouts.default`. Update assertion to `== 600`.
- [x] Task 3: Add regression test ensuring all loop phases have `_PHASE_DEFAULTS` entries (AC: #4)
  - [x] 3.1 Create a new test class `TestPhaseDefaultsCoverage` in `test_config.py`
  - [x] 3.2 Parametrize over all phases from `LoopConfig().story + LoopConfig().epic_teardown`
  - [x] 3.3 Include `fix_quality_gate` (detour phase reachable from `quality_gate`)
  - [x] 3.4 Assert each phase has an entry in `TimeoutsConfig._PHASE_DEFAULTS`
  - [x] 3.5 **Alternative (more robust)**: Implemented using `Phase` enum from `src/bmad_assist_lite/core/state.py` for 100% coverage of all phases including future detour phases.
- [x] Task 4: Add direct unit tests for the new/changed default values (AC: #1, #2, #3)
  - [x] 4.1 Test `TimeoutsConfig().get_timeout("retrospective")` returns 600 with no explicit config — uses `TimeoutsConfig()` directly (not `get_phase_timeout()`) to bypass the autouse `reset_config_singleton` fixture which loads `MINIMAL_CONFIG_DATA` without a `timeouts` section
  - [x] 4.2 Test `TimeoutsConfig().get_timeout("code_review_synthesis")` returns 900 with no explicit config
  - [x] 4.3 Test `TimeoutsConfig().get_timeout("fix_quality_gate")` returns 900 with no explicit config
  - [x] 4.4 Verify explicit per-phase config still overrides the new defaults

## Dev Notes

### Architecture Patterns and Constraints

- **Frozen Pydantic models**: `TimeoutsConfig` uses `model_config = ConfigDict(frozen=True)`. The `_PHASE_DEFAULTS` is a class-level private dict (not a Pydantic field), so it's mutable at the class level but should only be edited in source. This is the correct pattern — it's not a model field, just a lookup table.
- **`get_timeout()` priority chain**: Explicit per-phase field → `_PHASE_DEFAULTS` → `self.default` (global default, 300s). The fix only changes `_PHASE_DEFAULTS`, so user-configured explicit overrides remain untouched.
- **`get_phase_timeout()` function**: The module-level helper at config.py line 367 delegates to `TimeoutsConfig.get_timeout()` when `config.timeouts` is not None. When `config.timeouts` is None, it falls back to `config.timeout` (global scalar). The `_PHASE_DEFAULTS` only apply when a `timeouts` section exists in config. **Scope note**: This fallback behavior is intentional — users without a `timeouts` block get the single global `config.timeout`. Changing `get_phase_timeout()` to apply `_PHASE_DEFAULTS` even without a `timeouts` section is out of scope for this story (would change the config contract) but could be considered for a follow-up if the minimal-config path causes failures.
- **Non-LLM phases note**: The epic story says to "keep `quality_gate` at 300s and `epic_quality_gate` at 600s — these are non-LLM phases." They already exist in `_PHASE_DEFAULTS` at those values and should not change.

### Source Tree Components to Touch

1. **`src/bmad_assist_lite/core/config.py`** — Lines 104-114: `_PHASE_DEFAULTS` dict. Three changes: add `retrospective: 600`, bump `code_review_synthesis: 600→900`, bump `fix_quality_gate: 600→900`.
2. **`tests/test_config.py`** — Update existing test assertion (line 151), add new regression test class and new value-verification tests.

### Critical Detail — Existing Test Must Change

The test at `test_config.py:151` currently asserts:
```python
assert get_phase_timeout(cfg, "retrospective") == 200
```
This test loads config with `"timeouts": {"default": 200, ...}`. After the fix, `retrospective` will be in `_PHASE_DEFAULTS` with value 600. Since `_PHASE_DEFAULTS` takes priority over `self.default` in `get_timeout()`, the assertion must change to `== 600`. This is **correct behavior** — phase-specific defaults should override the global default. If a user explicitly sets `retrospective: 200` in their timeouts config, that still wins (it's a Pydantic field check via `getattr`).

### References

- Epic file: Story 7.1 definition with production failure data
- `src/bmad_assist_lite/core/config.py`: `TimeoutsConfig` class (lines 86-125), `get_phase_timeout()` (lines 367-371)
- `src/bmad_assist_lite/core/state.py`: `Phase` enum (lines 28-53), full list of 10 phases
- `tests/test_config.py`: Existing `TestGetPhaseTimeout` class (lines 135-164), `TestLoopConfigDefaults` (lines 230-247)
- `tests/conftest.py`: Autouse fixtures and `MINIMAL_CONFIG_DATA`

## Testing Requirements

- **Regression test**: Parametrized test that iterates all phases from `LoopConfig().story`, `LoopConfig().epic_teardown`, and the special `fix_quality_gate` detour phase — asserts each is a key in `TimeoutsConfig._PHASE_DEFAULTS`. This test catches any future phase additions that lack a default.
- **Value tests**: Direct assertions on the three changed/added values (retrospective=600, code_review_synthesis=900, fix_quality_gate=900) using `TimeoutsConfig().get_timeout()` with no explicit per-phase config.
- **Override test**: Verify that explicitly setting `retrospective: 120` in config still returns 120 (explicit config > phase default > global default).
- **Existing test update**: The assertion that `retrospective` falls through to `timeouts.default` must be updated to reflect that `retrospective` now has a phase-specific default.
- **Edge case**: Verify that phases NOT in `_PHASE_DEFAULTS` (if any hypothetical one existed) still fall back to `self.default`.

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/ tests/` | **PENDING** |
| Typecheck | `mypy src/` | **PENDING** |
| Build | N/A (library, no build step) | **PENDING** |
| Tests | `pytest -v --tb=short -m "not slow"` | **PENDING** |

## Dev Agent Record

### Agent Model Used
Claude (code-review-synthesis phase)

### Debug Log References
N/A — single-pass implementation, no debug cycles required.

### Completion Notes List
- Added `ClassVar` annotation to `_PHASE_DEFAULTS` for Pydantic v2 hygiene (code-review-synthesis fix)
- Regression test uses `Phase` enum (Task 3.5 approach) for full coverage
- Reverted unrelated 4-4 status change from sprint-status.yaml changeset

### File List
- `src/bmad_assist_lite/core/config.py` — `_PHASE_DEFAULTS` dict: added `retrospective: 600`, bumped `code_review_synthesis` and `fix_quality_gate` to 900, added `ClassVar` annotation
- `tests/test_config.py` — Updated retrospective assertion, added `TestPhaseDefaultsCoverage` and `TestPhaseDefaultValues` classes
