---
stepsCompleted: []
inputDocuments:
  - 'architecture.md'
  - 'project-context.md'
---

# bmad-assist-lite-parallel-stories - Epic 10 Breakdown

## Epic 10: Codex CLI Provider (Replace Gemini)

**Epic ID:** Epic-10
**Created:** 2026-05-29
**Status:** Ready for Development
**Priority:** High
**Points:** 14
**Stories:** 7

### Overview

Add OpenAI Codex CLI as a first-class code review provider, replacing Gemini CLI which has persistent Windows pipe race conditions (`[Errno 22] Invalid argument`, `[Errno 32] Broken pipe`) causing ~50% validator failure rate on parallel multi-LLM reviews. Codex CLI provides native Windows support, structured JSON output via `--output-schema`, and codebase file access — making it architecturally superior to both Gemini (plain text, pipe issues) and CodeRabbit (no Windows support, 7-30 min reviews).

### Business Goal

Achieve reliable, fast multi-LLM code reviews on Windows. Currently Gemini fails ~50% of the time in parallel validation/review phases, meaning only the Claude validator's findings are used. A second working reviewer provides genuine model diversity and catches different classes of issues.

### Strategic Context

- Gemini CLI has unfixable Windows pipe race conditions in the subprocess/reader-thread cleanup path
- CodeRabbit CLI was evaluated and rejected: no native Windows support, 7-30 minute review latency
- Codex CLI validated on the user's machine: v0.135.0, Windows native, ChatGPT free auth, `codex exec --json` produces clean NDJSON
- Codex CLI's `--output-schema` enables deterministic structured review output — maps directly to Evidence Score system
- Cost: ~$0.08/review with gpt-5.3-codex, ~$0.03/review with gpt-5.4-mini
- Auth: `CODEX_API_KEY` env var in `.env` file (pay-as-you-go API, no ChatGPT rate limits)

### Dependencies

- None — this is an additive provider, no existing code needs modification beyond the registry

### Research Artifacts

- `_bmad-output/reports/codex-cli-research.md` — Full research report with pricing, comparison, known bugs
- `_bmad-output/reports/codex-provider-implementation-plan.md` — Detailed implementation plan

### Context7 Library Documentation

<!-- No external libraries needed — Codex CLI is invoked via subprocess, same pattern as Gemini -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|
| — | — | — | — |

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Provider Implementor Reference; Key Patterns; Core Subsystems > providers |
| `project-context.md` | (full) |
| `prd.md` | (skip) |

### Recommended Story Order

1. 10-1-codex-provider-core — Core provider: `_do_invoke()`, `_cleanup()`, subprocess management
2. 10-2-provider-registry — Register in provider registry, config validation
3. 10-3-structured-output — `--output-schema` JSON output + `--output-last-message` file output
4. 10-4-evidence-score-integration — Wire structured findings into Evidence Score system
5. 10-5-configuration-and-docs — Config examples, README, CLAUDE.md documentation
6. 10-6-e2e-testing-and-hardening — End-to-end testing, Windows hardening, performance validation
7. 10-7-epic-documentation-sync — Standard documentation sync

---

### Story 10.1: Codex Provider Core

**Story ID:** 10-1-codex-provider-core
**Component:** `src/bmad_assist_lite/providers/codex.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** []

#### User Story

As a developer using bmad-assist-lite,
I want a Codex CLI provider that can invoke `codex exec` as a subprocess and collect results,
So that Codex CLI can be used as a code review provider alongside Claude.

#### Description

Create `CodexProvider` subclassing `BaseProvider` with `_do_invoke()`, `_cleanup()`, `parse_output()`, and `supports_model()`. The provider invokes `codex exec` via subprocess with `--json` for NDJSON streaming, reads `item.completed` events from stdout for the agent's response text, and feeds the `ResultCollector` for grace period tracking.

#### Current State

Two providers exist: `ClaudeSDKProvider` (uses claude-agent-sdk async generator) and `GeminiProvider` (subprocess with JSON stream parsing via reader threads). No Codex provider exists.

#### Target State

New `codex.py` module implementing:
- `provider_name` → `"codex"`
- `default_model` → `"codex-mini-latest"` (cheapest model available via ChatGPT auth)
- `supports_model()` — accept `codex-mini-latest`, `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, or any `gpt-` / `codex-` prefixed model
- `_do_invoke()`:
  - Build command: `["codex", "exec", "--json", "--model", model, prompt]`
  - **Auth**: Pass `CODEX_API_KEY` from environment into subprocess env (loaded from `.env` by python-dotenv)
  - **Windows**: `stdin=subprocess.DEVNULL` (avoid hang bug — Codex issue #20919)
  - **Windows**: use `get_subprocess_kwargs()` from `_windows.py` for `CREATE_NO_WINDOW`
  - Spawn `subprocess.Popen` with `stdout=PIPE, stderr=PIPE`
  - Reader threads parse NDJSON from stdout: extract `item.completed` events where `type == "agent_message"`, feed `collector.add(text)` for each
  - `process.wait(timeout=remaining)` with `TimeoutExpired` → raise `TimeoutError`
- `_cleanup()`:
  - Track `_current_process`, `_stdout_thread`, `_stderr_thread` (same pattern as Gemini)
  - Kill via `kill_process()` if still running
  - Join reader threads with timeout

#### Acceptance Criteria

**Given** Codex CLI is installed and `CODEX_API_KEY` is set in the environment
**When** `CodexProvider().invoke(prompt, model="codex-mini-latest", timeout=300, cwd=project_path)` is called
**Then** it returns a `ProviderResult` with the agent's response text in `stdout`

**Given** the codex process exceeds the timeout
**When** `TimeoutExpired` is raised by `process.wait()`
**Then** `TimeoutError` is raised for base class grace period handling

**Given** `_cleanup()` is called while the process is still running
**When** `process.poll()` returns `None`
**Then** `kill_process()` terminates the subprocess and reader threads are joined

**Given** `codex` is not installed (not in PATH)
**When** `_do_invoke()` tries to spawn the subprocess
**Then** `ProviderError("Codex CLI not found. Is 'codex' in PATH?")` is raised

**Given** the subprocess is invoked on Windows
**When** the `Popen` call is constructed
**Then** `stdin=subprocess.DEVNULL` is used (not `PIPE`) to prevent the non-TTY stdin hang bug

#### Technical Notes

- Follow `GeminiProvider` pattern closely — it's the closest architectural match (subprocess + NDJSON)
- Key difference from Gemini: Codex NDJSON events use `type: "item.completed"` with nested `item.type: "agent_message"` for response text, while Gemini uses `type: "message"` with `role: "assistant"`
- Codex also emits `item.type: "command_execution"` events — log these at INFO level for tool use visibility (same pattern as Gemini's tool_use logging)
- The `--json` flag may be silently ignored when MCP tools are active (Codex issue #15451) — for code review, MCP tools shouldn't be needed
- `shutil.which("codex")` can be used for the "not installed" check before spawning
- No retry logic needed in this story — Codex doesn't have Gemini's transient empty-response failures

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Internal provider module, no user-visible behavior change

---

### Story 10.2: Provider Registry Integration

**Story ID:** 10-2-provider-registry
**Component:** `src/bmad_assist_lite/providers/__init__.py`
**Estimate:** Small
**Points:** 1
**Priority:** High
**Dependencies:** [10-1-codex-provider-core]

#### User Story

As a developer configuring bmad-assist-lite,
I want to specify `provider: codex` in my config file,
So that Codex CLI is used for multi-LLM validation and code review.

#### Description

Register `CodexProvider` in the provider registry alongside Claude and Gemini. Add lazy import, update `_init_default_providers()`, and verify config validation accepts `"codex"` as a provider name.

#### Current State

`providers/__init__.py` registers two providers: `"claude": ClaudeSDKProvider`, `"gemini": GeminiProvider`. The lazy import dict maps `"ClaudeSDKProvider"` and `"GeminiProvider"` to their modules.

#### Target State

Registry includes `"codex": CodexProvider`. Config `provider: codex` is accepted in both `master` and `multi` provider configs.

#### Acceptance Criteria

**Given** the provider registry is initialized
**When** `get_provider("codex")` is called
**Then** a `CodexProvider` instance is returned

**Given** `list_providers()` is called
**When** the registry is initialized
**Then** the result includes `"codex"` alongside `"claude"` and `"gemini"`

**Given** `bmad-assist-lite.yaml` contains `provider: codex` in the multi config
**When** config is loaded via `load_config_with_project()`
**Then** validation succeeds (no `ConfigError`)

#### Technical Notes

- Add `"CodexProvider": ".codex"` to `_lazy_imports` dict
- Add `"codex": CodexProvider` to `_init_default_providers()`
- Add `CodexProvider` to `__all__`
- Add `if TYPE_CHECKING: from .codex import CodexProvider as CodexProvider`
- If provider names are validated against a hardcoded list in `config.py`, add `"codex"` there

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Registry change, no user-visible behavior

---

### Story 10.3: Structured Output via --output-schema

**Story ID:** 10-3-structured-output
**Component:** `src/bmad_assist_lite/providers/codex.py`, `src/bmad_assist_lite/workflows/schemas/`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [10-1-codex-provider-core]

#### User Story

As a developer using Codex for code reviews,
I want review findings returned as structured JSON matching a defined schema,
So that findings can be deterministically parsed into Evidence Score calculations without fragile text parsing.

#### Description

Create a JSON Schema file for code review output, pass it to Codex CLI via `--output-schema`, and use `--output-last-message` to write the final result to a temp file. Update `_do_invoke()` to use these flags and `parse_output()` to read the structured JSON.

#### Current State

No review schema exists. Gemini returns plain text that is parsed by the evidence score text parser (regex-based, fragile).

#### Target State

New schema file at `src/bmad_assist_lite/workflows/schemas/codex-review-schema.json`:

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
          "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
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

Updated `_do_invoke()` adds:
- `--output-schema <schema_path>` (path to bundled schema file)
- `--output-last-message <temp_output_path>` (unique temp file per invocation)

Updated `parse_output()`:
- Reads temp output file as JSON
- Falls back to stdout text if file doesn't exist (graceful degradation for older Codex versions)

#### Acceptance Criteria

**Given** a review prompt is sent to Codex with `--output-schema`
**When** Codex completes the review
**Then** the output file contains valid JSON matching the review schema

**Given** the schema file is bundled as package data
**When** `_do_invoke()` constructs the command
**Then** it resolves the schema path via `importlib.resources` or `__file__` relative path

**Given** Codex is an older version that doesn't support `--output-schema`
**When** the flag is unrecognized
**Then** the provider falls back to reading stdout text (same as Gemini behavior)

**Given** the temp output file is created
**When** the invocation completes (success or failure)
**Then** the temp file is cleaned up in `_cleanup()`

#### Technical Notes

- Schema file must be included in `pyproject.toml` as package data
- Temp file path: `project_root / ".bmad-assist-lite" / "cache" / f"codex-review-{uuid4().hex[:8]}.json"`
- Known bug: `--output-schema` can incorrectly apply to intermediate messages (Codex issue #19816) — `--output-last-message` is the authoritative source
- Known bug: JSON schema field names may drift between Codex CLI versions (#4776) — add version-tolerant parsing

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Internal structured output, no user-visible behavior change

---

### Story 10.4: Evidence Score Integration

**Story ID:** 10-4-evidence-score-integration
**Component:** `src/bmad_assist_lite/providers/codex.py`
**Estimate:** Small-Medium
**Points:** 2
**Priority:** High
**Dependencies:** [10-3-structured-output]

#### User Story

As a developer using bmad-assist-lite with Codex reviews,
I want Codex findings to produce correct Evidence Scores,
So that the synthesis phase receives properly scored review data.

#### Description

Map Codex's structured JSON findings (P0-P3 priorities) into the text format expected by the existing evidence score parser. This avoids creating a parallel parsing path — the Codex provider's `parse_output()` formats structured JSON into the standard evidence text format, and the existing parser handles it unchanged.

#### Current State

Evidence score parser (`validation/evidence_score.py`) parses text output looking for severity markers (CRITICAL, IMPORTANT, MINOR, CLEAN PASS) in table or bullet format. It works with unstructured text from Claude and Gemini reviews.

#### Target State

`CodexProvider.parse_output()` converts the structured JSON into formatted text that the existing parser recognizes:

```
## Evidence Score Summary

| Severity | Description | Source | Score |
|----------|-------------|--------|-------|
| 🔴 CRITICAL | {finding.title}: {finding.body} | {finding.code_location.file_path} | +3 |
| 🟠 IMPORTANT | {finding.title}: {finding.body} | {finding.code_location.file_path} | +1 |
| 🟡 MINOR | {finding.title}: {finding.body} | {finding.code_location.file_path} | +0.3 |
| 🟢 CLEAN PASS | N categories clean | — | -{N*0.5} |
```

Priority mapping:
- P0 → CRITICAL (+3)
- P1 → IMPORTANT (+1)
- P2 → MINOR (+0.3)
- P3 → MINOR (+0.3)

If overall_verdict is "PASS" and findings is empty, format as clean passes.

#### Acceptance Criteria

**Given** Codex returns JSON with 1 P0, 2 P1, and 1 P2 finding
**When** `parse_output()` converts to text and the evidence parser scores it
**Then** the score is: +3 + 1 + 1 + 0.3 = +5.3 (MAJOR REWORK)

**Given** Codex returns JSON with no findings and verdict "PASS"
**When** `parse_output()` converts to text and the evidence parser scores it
**Then** the score reflects clean passes (negative score)

**Given** Codex returns plain text instead of JSON (schema fallback)
**When** `parse_output()` receives the text
**Then** it passes through unchanged for the existing text parser to handle

#### Technical Notes

- Zero changes to `evidence_score.py` or any handler code — all transformation happens in `CodexProvider.parse_output()`
- The formatted text must match the exact patterns the parser looks for: `CRITICAL`, `IMPORTANT`, `MINOR`, `CLEAN PASS` keywords in the right format
- Include the Evidence Score Summary header so the parser's section detection works
- Test with the actual evidence score parser to verify roundtrip: JSON → text → parsed score matches expected

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Internal score mapping, no user-visible behavior change

---

### Story 10.5: Configuration & Documentation

**Story ID:** 10-5-configuration-and-docs
**Component:** `README.md`, `CLAUDE.md`, config examples
**Estimate:** Small
**Points:** 1
**Priority:** Medium
**Dependencies:** [10-2-provider-registry]

#### User Story

As a new user of bmad-assist-lite,
I want clear documentation on how to set up Codex CLI as a reviewer,
So that I can configure it without reading the source code.

#### Description

Update all documentation to include Codex as a supported provider: README prerequisites, installation instructions, config examples, model table, and comparison table. Update CLAUDE.md architecture docs.

#### Current State

README and CLAUDE.md reference only Claude and Gemini providers. Config examples show Gemini as the multi-LLM reviewer.

#### Target State

- README: Add Codex to prerequisites, install instructions, supported providers table, config examples
- CLAUDE.md: Add CodexProvider to architecture docs, provider list, implementor reference
- Config example shows Codex as multi-provider option
- Auth instructions for both ChatGPT auth and API key auth

#### Acceptance Criteria

**Given** a user reads the README
**When** they look at the config example
**Then** they see `provider: codex` as a documented option with model values

**Given** a user needs to install Codex CLI
**When** they read the prerequisites section
**Then** they find platform-specific install commands (Windows PowerShell, macOS/Linux curl)

**Given** a developer reads CLAUDE.md
**When** they look at the provider architecture section
**Then** `CodexProvider` is documented with its subprocess pattern and structured output approach

#### Technical Notes

- Windows install: `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"`
- macOS/Linux install: `curl -fsSL https://chatgpt.com/codex/install.sh | sh`
- Auth: `CODEX_API_KEY` env var in `.env` file (recommended for automation — no browser login, no ChatGPT rate limits)
- Document the known Windows stdin bug workaround (handled internally by the provider)

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Documentation only, no code changes

---

### Story 10.6: End-to-End Testing & Hardening

**Story ID:** 10-6-e2e-testing-and-hardening
**Component:** `tests/test_codex_provider.py`, manual E2E
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** [10-1-codex-provider-core, 10-2-provider-registry, 10-3-structured-output, 10-4-evidence-score-integration]

#### User Story

As a developer maintaining bmad-assist-lite,
I want comprehensive tests for the Codex provider,
So that regressions are caught before they reach users.

#### Description

Write unit tests with mocked subprocess for all provider behaviors. Perform manual E2E testing with a real Codex CLI invocation. Handle edge cases: CLI not installed, auth expired, rate limits, network failure, malformed output.

#### Current State

`test_claude_sdk_timeout.py` (53 tests) and `test_gemini_timeout.py` (41 tests) provide the testing patterns for providers.

#### Target State

New `tests/test_codex_provider.py` with:

**Unit tests (mocked subprocess):**
- Command construction: model flag, `--json`, `--output-schema`, `--output-last-message`, `stdin=DEVNULL`
- NDJSON parsing: extract `agent_message` text from `item.completed` events
- Cleanup: process termination, thread joining
- Timeout: `TimeoutExpired` → `TimeoutError` for base class
- Error: CLI not found → `ProviderError`
- Error: malformed NDJSON → graceful handling
- `parse_output()`: JSON → Evidence Score text formatting
- `parse_output()`: plain text fallback
- `supports_model()`: accepted and rejected model names

**Error handling:**
- Rate limit exceeded: detect from stderr/exit code, log warning
- Auth expired: detect from stderr, raise `ProviderError` with actionable message
- Network failure: detect from stderr, raise `ProviderError`
- Empty response: handle gracefully (same as Gemini pattern)

**Manual E2E (not automated):**
- Run `bmad-assist-lite run --epic 1 --story 1` with codex as multi reviewer
- Verify validate_story works with codex
- Verify code_review works with codex
- Check Evidence Score calculation is correct
- Verify review completes in < 5 minutes
- Verify no orphan processes on Windows

#### Acceptance Criteria

**Given** `pytest tests/test_codex_provider.py` is run
**When** all unit tests execute
**Then** all tests pass with no warnings

**Given** a manual E2E test with a real Codex CLI
**When** the full loop runs with `provider: codex` in multi config
**Then** the review phase completes successfully with parseable Evidence Score

**Given** `codex` is not in PATH
**When** the provider is invoked
**Then** `ProviderError` is raised with message "Codex CLI not found. Is 'codex' in PATH?"

#### Technical Notes

- Follow `test_gemini_timeout.py` structure: `TestInvocation`, `TestCleanup`, `TestTimeout`, `TestParseOutput` classes
- Mock `subprocess.Popen` to return pre-recorded NDJSON streams
- Use `conftest.py` autouse fixtures for singleton resets
- For manual E2E: document results in `_bmad-output/reports/codex-e2e-results.md`

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** CLI tool — no UI, no user-visible behavior beyond console output

---

### Story 10.7: Epic Documentation Sync

**Story ID:** 10-7-epic-documentation-sync
**Component:** `CLAUDE.md`, `project-context.md`
**Estimate:** Small
**Points:** 1
**Priority:** High
**Dependencies:** [All prior stories in this epic]

#### User Story

As a developer (human or AI),
I want project documentation to reflect everything built in Epic 10,
So that future implementation decisions are based on accurate information.

#### Description

Final story in every epic. Audit all changes introduced by the epic and update project documentation accordingly.

#### Current State

Documentation reflects the project state before Epic 10 began.

#### Target State

All documentation accurately reflects the project state after Epic 10 completion.

#### Acceptance Criteria

**Given** all implementation stories in Epic 10 are complete
**When** the documentation sync story executes
**Then** every applicable item in the Doc Audit Checklist is addressed

**Given** a Tier 1 doc has a stale section
**When** the audit identifies it
**Then** the section is updated with accurate information from the implemented code

#### Technical Notes

**Audit Method:** Run `git diff main...HEAD --name-only` to identify all files changed in this epic. Cross-reference changed paths against the checklist below.

**Do NOT update:** `architecture.md`, `prd.md`. These are planning artifacts owned by the planning phase.

#### Doc Audit Checklist

##### Tier 1: Core Docs (Always Evaluate)

**`CLAUDE.md`:**
- [ ] New provider added? → Update "Core Subsystems > providers" section
- [ ] Provider registry changed? → Update provider list
- [ ] New config fields? → Update Configuration section
- [ ] New test file? → Verify test conventions section still accurate

**`project-context.md`:**
- [ ] New module added? → Update relevant rules section
- [ ] New code conventions established? → Update rules section
- [ ] New test patterns introduced? → Update "Testing Rules" section

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Documentation-only changes, no user-facing behavior affected

---

## Test Impact Summary

### Unit / Integration Tests

| Test File | Stories Affected | Changes |
|-----------|------------------|---------|
| `tests/test_codex_provider.py` | 10-1, 10-3, 10-4, 10-6 | New test file: command construction, NDJSON parsing, cleanup, timeout, parse_output, supports_model |
| `tests/test_codex_provider.py` | 10-4 | Evidence Score text formatting roundtrip tests |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 10.1 | None | — | — | Internal provider, no UI |
| 10.2 | None | — | — | Registry change, no UI |
| 10.3 | None | — | — | Internal structured output, no UI |
| 10.4 | None | — | — | Internal score mapping, no UI |
| 10.5 | None | — | — | Documentation only |
| 10.6 | None | — | — | Tests only |
| 10.7 | None | — | — | Documentation only |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests updated and passing (`pytest -q --tb=short --no-header`)
- [ ] `mypy src/` passes with no errors
- [ ] `ruff check src/` passes with no errors
- [ ] Manual E2E: Codex review completes in < 5 minutes on a real project
- [ ] Manual E2E: Evidence Scores are calculated correctly from Codex output
- [ ] Manual E2E: No orphan processes on Windows after review completes
- [ ] Documentation sync story completed (CLAUDE.md, project-context.md verified current)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Codex CLI `--output-schema` breaks in future versions | Medium | Medium | Graceful fallback to plain text parsing; pin CLI version in docs |
| Windows stdin hang bug (#20919) not fully resolved by `DEVNULL` | Low | High | Tested on user's machine — confirmed working; add regression test |
| OpenAI API rate limits hit during multi-story runs | Low | Low | `CODEX_API_KEY` uses pay-as-you-go API — generous limits at Tier 1+ |
| Codex CLI is still under rapid development (v0.135.0) | Medium | Medium | Version-tolerant parsing; test against multiple CLI versions |
| `--json` silently ignored with MCP tools (#15451) | Low | Low | Code review doesn't use MCP tools; document the limitation |

## Rollback Plan

Codex is an additive provider — rollback is trivial:
1. Change config `provider: codex` back to `provider: gemini` in `bmad-assist-lite.yaml`
2. No code removal needed — the Codex provider simply won't be invoked
3. Gemini provider remains fully functional (with its known pipe issues on Windows)
