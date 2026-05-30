# Plan: Agentic Worktree Execution Pattern

## Context

The parallel orchestrator spawns `bmad-assist-lite run --single-story` in each worktree, which runs 7+ phases sequentially (create_story → validate → synthesize → dev → review → synthesis → quality_gate → [fix loop]). Each phase is a separate LLM invocation with fresh context compilation. The key inefficiency: when `fix_quality_gate` runs, it starts a completely fresh Claude session that has no memory of what `dev_story` built or why. Similarly, `code_review_synthesis` must re-discover all changes via git diff rather than knowing the implementation intent.

The goal is to apply Domain 1 agentic orchestration patterns from the Claude Certified Architect certification to reduce LLM invocations and maintain context continuity where it matters, while preserving the multi-LLM validation that is core to BMAD methodology.

## Architecture Decision: What Changes

**The orchestrator (`parallel/orchestrator.py`) does NOT change.** Its subprocess model is battle-tested (signal handling, drain mode, crash recovery, merge queue). The change is **what the subprocess runs inside the worktree**.

### Phase Grouping: Three Zones

**Zone 1 — Story Specification (unchanged, 3 sessions)**
- `create_story` → `validate_story` (multi-LLM) → `validate_story_synthesis`
- These are distinct cognitive tasks. Multi-LLM validation requires separate sessions. No gain from combining.

**Zone 2 — Implementation + Fix Loop (NEW: `agentic_dev`, 1-3 sessions instead of 4-7+)**
- Replaces: `dev_story` + `code_review` + `code_review_synthesis` + `quality_gate` + `fix_quality_gate`
- **Session C1**: Claude implements the story (same as current `dev_story`, already agentic with tool access). At completion, harness extracts an implementation summary.
- **Multi-LLM code review**: Existing `CodeReviewHandler` runs N validators in parallel (unchanged).
- **Session C2**: Claude receives implementation summary + review reports + QG commands. Applies review fixes, runs quality gates, fixes failures — all in one session. Replaces what was previously `code_review_synthesis` + `quality_gate` + `fix_quality_gate` (3+ separate invocations).

**Zone 3 — Epic Teardown (unchanged)**
- `epic_quality_gate` → `retrospective`

### New Phase Flow per Story

```
Current (7+ invocations):
  create_story → validate (multi) → synthesize → dev_story → code_review (multi) →
  code_review_synthesis → quality_gate → [fix_quality_gate → quality_gate]...

Agentic (5 invocations, fewer with less fix loops):
  create_story → validate (multi) → synthesize → agentic_dev
                                                    ├── Session C1: implement (1 invocation)
                                                    ├── Multi-LLM review (N invocations, unchanged)
                                                    └── Session C2: review-fix + QG loop (1 invocation)
```

## Key Design: Implementation Summary Protocol

After Session C1 (implementation), the handler instructs Claude to write a structured summary to `.bmad-assist-lite/cache/impl-summary-{story_id}.md`:

```markdown
## Implementation Summary
### Files Created/Modified
- src/foo/bar.py: New module implementing X
- tests/test_bar.py: Unit tests

### Design Decisions
- Used factory pattern because...
- Chose library X over Y because...

### Quality Gate Expectations
- Lint: should pass
- Tests: 12 new tests added
```

This summary (~1-2K tokens) is injected into Session C2's prompt, giving the fix session full context of implementation intent — far richer than the current approach where `fix_quality_gate` only gets the failure report.

## Key Design: Quality Gate Integration

**Harness runs QG commands, feeds results to Claude** (not Claude running them directly). The `AgenticDevHandler` calls existing `QualityGateHandler` deterministically, then feeds failure reports to Claude for fixing. This preserves:
- Deterministic QG infrastructure
- Retry count tracking
- Skip decision logic
- State persistence between attempts

Claude CAN still run tests during Session C1 as a self-check (it already does — dev_story instructions include QG steps). The harness QG is the authoritative verification.

## Backward Compatibility

Fully opt-in via existing `loop.story` config:

```yaml
# Default (unchanged):
loop:
  story: [create_story, validate_story, validate_story_synthesis,
          dev_story, code_review, code_review_synthesis, quality_gate]

# Agentic mode (opt-in):
loop:
  story: [create_story, validate_story, validate_story_synthesis,
          agentic_dev]
```

No existing handlers are modified or removed. Both modes coexist.

## Files to Create

1. **`src/bmad_assist_lite/loop/handlers/agentic_dev.py`** — New unified handler
   - `AgenticDevHandler(BaseHandler)` with `phase_name = "agentic_dev"`
   - `execute()` orchestrates: C1 implementation → multi-LLM review → C2 fix+QG loop
   - Reuses existing `CodeReviewHandler`, `QualityGateHandler` internally
   - Manages implementation summary extraction and caching
   - Internal crash recovery via checkpoint files in cache/

2. **`src/bmad_assist_lite/workflows/agentic-dev/`** — Workflow templates
   - `workflow.yaml` — file discovery patterns (reuses dev-story patterns)
   - `instructions-c1.xml` — Implementation instructions (based on dev-story)
   - `instructions-c2.xml` — Review-fix-QG instructions (combines code-review-synthesis + fix-quality-gate)
   - `impl-summary-template.md` — Template for implementation summary output

3. **`src/bmad_assist_lite/compiler/workflows/agentic_dev.py`** — Compiler for the combined workflow

4. **`tests/test_agentic_dev.py`** — Unit tests

## Files to Modify

5. **`src/bmad_assist_lite/core/state.py`** — Add `AGENTIC_DEV = "agentic_dev"` to Phase enum
6. **`src/bmad_assist_lite/loop/dispatch.py`** — Register `AgenticDevHandler` in `init_handlers()`
7. **`src/bmad_assist_lite/loop/handlers/__init__.py`** — Export new handler
8. **`src/bmad_assist_lite/core/config.py`** — Add `agentic_dev` timeout field to TimeoutConfig

## Files NOT Changed

- `parallel/orchestrator.py` — Subprocess spawning unchanged
- `loop/runner.py` — Phase list iteration already handles any phase name
- `loop/transitions.py` — advance_story already works with configurable phase lists
- `providers/` — Provider abstraction unchanged
- All existing handlers — Preserved for non-agentic mode

## Implementation Steps

### Step 1: Phase registration (stub)
- Add `AGENTIC_DEV` to Phase enum
- Add timeout field
- Register stub handler in dispatch that just delegates to existing handlers sequentially
- Verify: `loop.story: [create_story, validate_story, validate_story_synthesis, agentic_dev]` works end-to-end

### Step 2: Implementation summary
- Add instructions to dev-story C1 prompt for writing impl summary to cache
- Extract and validate the summary file after C1 completes
- Test: verify summary file is created and contains expected structure

### Step 3: Combined review-fix-QG loop
- Build C2 prompt that includes: impl summary + Evidence Score context + review reports + QG failure report
- Implement the QG retry loop within the handler
- Wire up crash recovery checkpoints (which sub-phase was reached)

### Step 4: Workflow templates
- Create agentic-dev workflow directory with XML instructions
- Create compiler that handles C1 vs C2 prompt compilation

### Step 5: Testing
- Unit tests for handler with mock providers
- Test crash recovery (resume mid-handler)
- Test backward compatibility (default config still uses old phases)

## Verification

1. Run `pytest` — all existing tests pass (no regressions)
2. Run `mypy src/` — type checks pass
3. Run `ruff check src/` — lint passes
4. Manual test: configure `loop.story` with `agentic_dev`, run against a test epic
5. Manual test: default config still works with existing phase list
6. Compare: story completion time with agentic_dev vs default mode
