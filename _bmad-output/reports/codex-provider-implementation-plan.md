# Codex CLI Provider — Implementation Plan

**Date:** 2026-05-29
**Epic:** Add CodexProvider to replace Gemini as multi-LLM reviewer
**Estimated Stories:** 6

---

## Story 1: CodexProvider Core — `_do_invoke()` + `_cleanup()`

**Goal:** Minimal working provider that can invoke `codex exec` and return results.

### Tasks

1. **Create `src/bmad_assist_lite/providers/codex.py`**
   - Subclass `BaseProvider`
   - `provider_name` → `"codex"`
   - `default_model` → `"gpt-5.3-codex"`
   - `supports_model()` — accept `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, or any `gpt-` prefixed model

2. **Implement `_do_invoke()`**
   - Build command: `["codex", "exec", "--model", model, prompt]`
   - Use `subprocess.Popen` with `stdout=PIPE, stderr=PIPE`
   - **Windows**: `stdin=subprocess.DEVNULL` (avoid hang bug #20919)
   - **Windows**: use `get_subprocess_kwargs()` from `_windows.py`
   - Stream stderr for progress (feed `collector.add()` for grace period tracking)
   - Read stdout for final response text
   - Handle `TimeoutExpired` → raise `TimeoutError` for base class

3. **Implement `_cleanup()`**
   - Track `_current_process` (same pattern as Gemini)
   - Kill via `kill_process()` if still running
   - Join reader threads with timeout

4. **Implement `parse_output()`**
   - Return `result.stdout.strip()`

### Acceptance Criteria
- `CodexProvider().invoke(prompt, model="gpt-5.3-codex", timeout=300)` returns `ProviderResult`
- Process cleanup works on Windows (no orphans)
- Timeout handling triggers base class grace period logic

### Tests
- `tests/test_codex_provider.py` — mock subprocess, verify command construction, cleanup, timeout

---

## Story 2: Register CodexProvider in Provider Registry

**Goal:** Make `codex` a first-class provider alongside `claude` and `gemini`.

### Tasks

1. **Update `providers/__init__.py`**
   - Add lazy import: `"CodexProvider": ".codex"`
   - Register in `_init_default_providers()`: `"codex": CodexProvider`
   - Add to `__all__`

2. **Update `core/config.py`**
   - Add `"codex"` to valid provider names in Pydantic validation (if hardcoded)

3. **Verify `get_provider("codex")` works**

### Acceptance Criteria
- `get_provider("codex")` returns `CodexProvider` instance
- Config `provider: codex` is accepted in `bmad-assist-lite.yaml`
- `list_providers()` includes `"codex"`

### Tests
- Provider registry tests: registration, lookup, listing

---

## Story 3: Structured Output via `--output-schema`

**Goal:** Use Codex CLI's `--output-schema` for deterministic review output that maps to Evidence Score.

### Tasks

1. **Create review JSON Schema file**
   - `src/bmad_assist_lite/workflows/schemas/codex-review-schema.json`
   - Schema fields:
     ```json
     {
       "type": "object",
       "properties": {
         "findings": {
           "type": "array",
           "items": {
             "type": "object",
             "properties": {
               "title": {"type": "string"},
               "body": {"type": "string"},
               "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
               "confidence_score": {"type": "number"},
               "code_location": {
                 "type": "object",
                 "properties": {
                   "file_path": {"type": "string"},
                   "line_range": {"type": "array", "items": {"type": "integer"}}
                 }
               }
             },
             "required": ["title", "body", "priority"]
           }
         },
         "overall_verdict": {"type": "string", "enum": ["PASS", "NEEDS_WORK", "REJECT"]},
         "summary": {"type": "string"}
       },
       "required": ["findings", "overall_verdict", "summary"]
     }
     ```

2. **Update `_do_invoke()` to use schema**
   - Copy schema to temp dir (Codex needs file path)
   - Add `--output-schema <schema_path>` to command
   - Add `--output-last-message <temp_output_path>` to command
   - After process completes, read output file as the result

3. **Update `parse_output()` to parse structured JSON**
   - Parse JSON from output file
   - Map `priority` → Evidence Score severity
   - Format as review text for synthesis phase (or return structured data)

### Acceptance Criteria
- Review output is valid JSON matching the schema
- P0-P3 priorities correctly map to Evidence Score severities
- Falls back gracefully if `--output-schema` is not supported (older Codex versions)

### Tests
- Schema validation tests
- JSON parsing with various finding combinations
- Priority-to-severity mapping

---

## Story 4: Evidence Score Integration

**Goal:** Wire Codex's structured findings into the Evidence Score system.

### Tasks

1. **Create Codex-specific evidence parser**
   - `src/bmad_assist_lite/validation/codex_parser.py`
   - Parse JSON findings → `Finding` objects with severity
   - Priority mapping: P0→CRITICAL(+3), P1→IMPORTANT(+1), P2→MINOR(+0.3), P3→MINOR(+0.3)

2. **Update validate_story handler**
   - When validator is `codex`, use structured parser instead of text-based parser
   - Or: format structured JSON as text that the existing parser can handle

3. **Update code_review handler**
   - Same approach as validate_story

### Design Decision
**Option A:** Create a Codex-specific parser that returns `Finding` objects directly from JSON.
**Option B:** Format Codex JSON into the same text format the existing evidence parser expects.

**Recommendation:** Option B (simpler, no parallel code paths). The Codex provider's `parse_output()` formats structured JSON into the standard evidence text format. The existing parser handles it. Zero changes to handlers.

### Acceptance Criteria
- Evidence scores calculated correctly from Codex review output
- Scores match expected values for known finding sets

### Tests
- Score calculation with P0/P1/P2/P3 findings
- Empty findings → clean pass
- Mixed severity combinations

---

## Story 5: Configuration & Documentation

**Goal:** Make Codex configurable and document usage.

### Tasks

1. **Update config examples**
   - `bmad-assist-lite.yaml` example with codex as multi provider
   - Add `OPENAI_API_KEY` to `.env` loading (if not already)

2. **Update CLAUDE.md**
   - Add CodexProvider to architecture docs
   - Add `codex` to provider list

3. **Update README.md**
   - Add Codex to prerequisites
   - Add Codex to supported providers table
   - Update model values table
   - Add Codex install instructions

4. **Authentication**
   - Verify Codex CLI reads `OPENAI_API_KEY` from environment
   - Document setup: `codex auth login --api-key $OPENAI_API_KEY`

### Acceptance Criteria
- README documents Codex as a supported provider
- Config example shows codex multi-provider setup
- Auth instructions work on Windows and Linux

---

## Story 6: End-to-End Testing & Hardening

**Goal:** Validate Codex works in the real loop, handle edge cases.

### Tasks

1. **Manual E2E test**
   - Run `bmad-assist-lite run --epic 1 --story 1` with codex as multi reviewer
   - Verify validate_story works with codex
   - Verify code_review works with codex
   - Check Evidence Score calculation is correct

2. **Error handling**
   - Codex CLI not installed → `ProviderError` with clear message
   - `OPENAI_API_KEY` not set → meaningful error
   - Rate limit exceeded → retry with backoff (match Gemini's retry pattern)
   - Network failure → `ProviderError`

3. **Performance validation**
   - Verify review completes in < 5 minutes
   - Compare review quality: Codex findings vs Claude findings
   - Check cost per review matches estimates

4. **Windows-specific testing**
   - Verify `stdin=subprocess.DEVNULL` prevents hang
   - Verify process cleanup on timeout
   - Verify no orphan processes

### Acceptance Criteria
- Full loop completes with codex as multi reviewer
- No pipe errors, no orphan processes
- Review quality is comparable to Gemini
- Cost per review confirmed < $0.10

---

## Implementation Order

```
Story 1 (core provider)
  └→ Story 2 (registry)
       └→ Story 3 (structured output)
            └→ Story 4 (evidence score)
                 └→ Story 5 (docs)
                      └→ Story 6 (E2E testing)
```

Stories 1-4 are sequential (each builds on previous).
Story 5 can start after Story 2.
Story 6 requires all others.

## Estimated Effort

| Story | Complexity | Est. Time |
|-------|-----------|-----------|
| 1. Core provider | Medium | 2-3 hours |
| 2. Registry | Low | 30 min |
| 3. Structured output | Medium | 2 hours |
| 4. Evidence Score | Low-Medium | 1-2 hours |
| 5. Documentation | Low | 1 hour |
| 6. E2E testing | Medium | 2-3 hours |
| **Total** | | **~9-12 hours** |

## Config Example

```yaml
# bmad-assist-lite.yaml
providers:
  master:
    provider: claude
    model: opus
  multi:
    - provider: codex
      model: gpt-5.3-codex
    - provider: claude
      model: sonnet
```
