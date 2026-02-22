# Performance Optimization Plan — bmad-assist-lite

## Timing Analysis (Real Run: Epic 8, 5 Stories, ~115+ min)

### Per-Story Breakdown

| Story | Total | Outcome | Biggest Sinks |
|-------|-------|---------|---------------|
| 8.1 | ~30 min | PASSED | code_review_synthesis (8m40s), dev_story (7m29s) |
| 8.2 | ~25 min | BLOCKED | dev_story (8m38s), code_review_synthesis (5m49s) |
| 8.3 | ~22 min | BLOCKED | dev_story (6m39s), code_review_synthesis (4m5s) |
| 8.4 | ~26 min | BLOCKED | code_review_synthesis (6m2s), dev_story (5m55s) |
| 8.5 | ~12+ min | Incomplete | create_story (4m52s), still running |

### Time by Phase Type (all stories combined)

| Phase | Total Time | % of Run | Model |
|-------|-----------|----------|-------|
| dev_story | ~29 min | 28% | opus |
| code_review_synthesis | ~25 min | 24% | opus |
| create_story | ~15 min | 14% | opus |
| validate_story_synthesis | ~13 min | 13% | opus |
| code_review | ~10 min | 10% | parallel (gemini-flash + sonnet) |
| quality_gate + fix | ~14 min | 13% | non-LLM + opus |
| validate_story | ~6 min | 6% | parallel (gemini-flash + sonnet) |

**Key insight: Opus calls = 79% of total time.**

### Quality Gate Failure Waste
- 3/5 stories failed QG even after fix attempt → each wasted ~3-5 min
- Total waste: ~12 min on stories that ended up blocked

---

## Approved Optimizations

### OPT-1: Per-Phase Model Selection (HIGH IMPACT, code change)
**Status: APPROVED**
**Decision:** Opus only for dev_story, code_review_synthesis, fix_quality_gate. Sonnet for everything else.

**Implementation approach:** Add `phase_overrides` to config:
```yaml
providers:
  master:
    provider: claude
    model: opus  # default
  phase_overrides:
    create_story: { model: sonnet }
    validate_story_synthesis: { model: sonnet }
    retrospective: { model: sonnet }
```

**Where to implement:**
- `core/config.py` — Add `PhaseOverrideConfig` model, add `phase_overrides` field to `ProvidersConfig`
- `loop/handlers/base.py` — `invoke_provider()` resolves model from phase_overrides before calling provider
- Phases that stay on opus: `dev_story`, `code_review_synthesis`, `fix_quality_gate`
- All other master phases: use override model (default sonnet)

**Estimated savings:** ~10-15 min per 5 stories (create_story 3min→1min, validate_story_synthesis 3min→1min each)

---

### OPT-2: Validate Story Impact Assessment (DEFERRED — needs data)
**Status: NEEDS DATA**
**Decision:** Keep validate_story + validate_story_synthesis for now. Need to compare story quality before/after to decide.

**How to get data:** Log or diff the story file before validate_story_synthesis and after. If the synthesis rarely changes the story, it's safe to skip. If it frequently catches real issues (especially ones code_review also catches), it's redundant.

**Action:** Add a simple diff log in `ValidateStorySynthesisHandler` that shows bytes changed in story file before/after synthesis. User reviews a few runs to decide.

---

### OPT-3: Multi-Provider Count (DEFERRED)
**Status: DEFERRED — user switching to newer Gemini model first**

---

### OPT-4: Parallel Quality Gate Commands (APPROVED, code change)
**Status: APPROVED**

**Where to implement:** `src/bmad_assist_lite/loop/handlers/quality_gate.py`

**Current flow:** Sequential loop over gate entries, each runs `run_command()`.
**New flow:** Use `concurrent.futures.ThreadPoolExecutor` to run all commands concurrently. Collect results. Report in original order.

**Considerations:**
- Commands are truly independent (lint, typecheck, build, test don't interfere)
- Output ordering must be preserved for the failure report
- `command_timeout` applies per-command, not total

**Estimated savings:** ~20-30s per QG run × 8 runs = ~3-4 min total

---

### OPT-5: Smarter Context7 Doc Slimming (APPROVED, code change)
**Status: APPROVED — needs design**

**Problem:** Docs fetched at epic level with generic query ("API usage examples"), cached forever, injected in full for every story. Up to 40,000 tokens of irrelevant docs.

**Current architecture:**
- `resolver.py` calls `_fetch_library_docs()` at startup per epic
- `query` param to Context7 API controls relevance ranking of returned sections
- Epic table path already supports `query_focus` per library
- Cache is flat files at `lib-docs/{name}.md`, never invalidated

**Proposed approach: Story-specific query refinement (move fetch to compile time)**

Instead of fetching all docs at startup, defer the Context7 fetch to compile time when we know the story context:

1. At startup (`resolve_epic_docs`): only resolve library IDs + save to `epic-libs.yaml` (skip fetch)
2. At compile time (`inject_library_docs` in `DevStoryCompiler`/`CodeReviewSynthesisCompiler`):
   - Read story file → extract task descriptions + acceptance criteria
   - Build story-specific query: "How to {task1}, {task2} with {library}"
   - Check cache for `lib-docs/{name}-story-{epic}.{story}.md`
   - If miss: call `_fetch_library_docs(query=story_specific_query, tokens=max_tokens_per_lib)`
   - Inject the story-specific version

**Alternative simpler approach:** Keep startup fetch but reduce tokens:
```yaml
context_docs:
  max_tokens_per_lib: 2000  # was 5000
  max_libs: 4               # was 8
```
And encourage epic table usage with targeted `query_focus` strings.

**Decision:** Start with the simpler approach (reduce tokens + encourage epic tables). Build story-specific query refinement as a follow-up if needed.

---

### OPT-6: Cache Toolchain Detection (APPROVED, code change)
**Status: APPROVED**

**Where:** `src/bmad_assist_lite/core/toolchain.py` — `detect_toolchain()` re-reads package.json etc. every call.
**Fix:** Add `@lru_cache` or module-level cache with project_path key.

**Where:** `shutil.which("gemini")` in `providers/gemini.py` — resolves PATH every invocation.
**Fix:** Cache the result.

**Estimated savings:** ~10-30s total across a full run.

---

### OPT-7: Skip Build in Per-Story Quality Gate (APPROVED, config change)
**Status: APPROVED**

**Change:** Configure quality gate to skip build (save for epic_quality_gate):
```yaml
quality_gate:
  lint: "pnpm run lint"
  typecheck: "pnpm run typecheck"
  test: "pnpm run test:unit"
  command_timeout: 60
```

Build runs in epic_quality_gate anyway.

**Estimated savings:** ~10-15s per QG run.

---

### OPT-8: Batch Story Creation (APPROVED, code change)
**Status: APPROVED — new CLI command**

**Proposed command:** `bmad-assist-lite create-stories --epic N`

**Implementation:**
1. New CLI command in `cli.py` reusing story-queue discovery logic
2. Loops over all backlog stories, calls `CreateStoryHandler.execute()` for each
3. Updates sprint-status after each via `trigger_sync()`

**Change to `CreateStoryHandler`:**
- Add file-existence check: if `{story_key}.md` exists AND sprint-status is `ready-for-dev` or beyond → return `PhaseResult.ok({"skipped": True})` without LLM call
- This makes the main `run` loop idempotent when batch creation has already run

**Benefit:** Stories can be created in batch (possibly with sonnet for speed), then the main loop only runs dev_story through quality_gate per story.

**Story file naming note:** Quality gate handler resolves `story-{epic}.{story}.md` but create-story instructions tell LLM to save as `{story_key}.md`. Must verify these align or fix the path resolution.

---

## Combined Impact Estimates

| Strategy | Changes | Time Saved | New Est. Time |
|----------|---------|------------|---------------|
| Config only (OPT-5 simple, OPT-7) | YAML changes | ~5-8 min | ~107 min |
| + Per-phase model (OPT-1) | Code change | +10-15 min | ~90-95 min |
| + Parallel QG (OPT-4) + cache (OPT-6) | Code change | +4-5 min | ~85-90 min |
| + Batch create (OPT-8) with skip | Code change | +10-15 min | ~70-80 min |
| + Skip validation (OPT-2, if data supports) | Config | +19 min | ~55-65 min |

**Best case: ~55-65 min (50-55% improvement)**
**Conservative (no validation skip): ~70-80 min (30-35% improvement)**

---

## Implementation Priority Order

1. **OPT-1** — Per-phase model selection (biggest single impact, moderate complexity)
2. **OPT-4** — Parallel quality gate commands (straightforward ThreadPoolExecutor)
3. **FIX-QG** — Fix quality gate instructions problem (see fix-quality-gate-analysis.md)
4. **OPT-8** — Batch story creation with skip-if-exists
5. **OPT-5** — Context7 doc token reduction (config first, story-specific query later)
6. **OPT-6** — Cache toolchain detection (simple lru_cache)
7. **OPT-7** — Skip build in per-story QG (config change)
8. **OPT-2** — Validation impact assessment (add diff logging, review data, decide)

---

## Debugging Added (IMPLEMENTED)

### Synthesis Phase Debugging

Both `validate_story_synthesis` and `code_review_synthesis` now log detailed diagnostics.

#### validate_story_synthesis — Story Diff Tracking

On every run, the handler now:
1. **Captures story file before** LLM invocation
2. **Logs prompt composition breakdown** to console and log:
   - `Prompt breakdown: base=~726 + evidence=~200 + validations=~8000 = ~8926 tokens`
3. **Logs per-validator response sizes**: chars and estimated tokens for each validator output
4. **Logs LLM output size**: chars and estimated tokens
5. **Computes unified diff** of story file before vs after synthesis
6. **Prints diff summary** to console: `Story diff: +12 -3 lines (5200 -> 5560 bytes, delta=+360)`
   or `Story diff: NO CHANGES made by synthesis`
7. **Saves full diff** to `.bmad-assist-lite/cache/synthesis-diff-validate-{story_id}.patch`
8. **Saves LLM response** to `.bmad-assist-lite/cache/synthesis-response-validate-{story_id}.md`

**How to use the data:**
- After a run, check `synthesis-diff-validate-*.patch` files in cache
- If most patches show `(no changes)`, the validation synthesis is not adding value
- If patches show meaningful changes, compare with code_review findings to check for redundancy

#### code_review_synthesis — Code Change Tracking

On every run, the handler now:
1. **Captures git diff --stat before** LLM invocation
2. **Logs prompt composition breakdown**: base + evidence + review tokens
3. **Logs per-reviewer response sizes**
4. **Logs LLM output size**
5. **Captures git diff --stat after** synthesis to show actual code changes
6. **Prints change summary** to console: file-level git diff stat or `NO CODE CHANGES`
7. **Saves full git diff** to `.bmad-assist-lite/cache/synthesis-diff-review-{story_id}.patch`
8. **Saves LLM response** to `.bmad-assist-lite/cache/synthesis-response-review-{story_id}.md`

**How to use the data:**
- After a run, check `synthesis-diff-review-*.patch` files for what code the synthesis actually changed
- Check `synthesis-response-review-*.md` for the full LLM response (what it tried to do)
- Compare code changes with quality gate results to see if synthesis fixes helped or were wasted

### Cache Files Generated Per Story

After a run with `-vv`, each story will produce these debug files in `.bmad-assist-lite/cache/`:

| File | Phase | Content |
|------|-------|---------|
| `synthesis-diff-validate-{id}.patch` | validate_story_synthesis | Unified diff of story file changes |
| `synthesis-response-validate-{id}.md` | validate_story_synthesis | Full LLM response text |
| `synthesis-diff-review-{id}.patch` | code_review_synthesis | Git diff of source code changes |
| `synthesis-response-review-{id}.md` | code_review_synthesis | Full LLM response text |
| `validations.json` | validate_story | Raw validator outputs + evidence scores |
| `reviews.json` | code_review | Raw reviewer outputs + evidence scores |
| `qa-failures-{id}.md` | quality_gate | Failure report (if QG failed) |
