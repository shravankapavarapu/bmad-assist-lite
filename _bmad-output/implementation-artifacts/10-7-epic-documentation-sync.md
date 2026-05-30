# Story 10.7: Epic Documentation Sync

Status: done

## Story

As a developer (human or AI),
I want project documentation to reflect everything built in Epic 10 (Codex CLI Provider),
so that future implementation decisions are based on accurate information about the Codex provider, its structured output, evidence score integration, and registry changes.

## Acceptance Criteria

1. **Given** all implementation stories in Epic 10 are complete, **When** the documentation sync story executes, **Then** every applicable item in the Doc Audit Checklist is addressed.
2. **Given** `CLAUDE.md` describes the providers subsystem, **When** the audit identifies it, **Then** the Core Subsystems > providers section is updated with `CodexProvider` (subprocess + NDJSON, `--output-schema` structured JSON, `--output-last-message` file output).
3. **Given** `CLAUDE.md` lists supported providers, **When** the audit identifies it, **Then** the provider list includes `codex` alongside `claude` and `gemini` in all relevant sections (Changing Models, Configuration, Key Patterns).
4. **Given** `CLAUDE.md` has Configuration examples, **When** the audit identifies it, **Then** config examples show `provider: codex` as a valid option with model values (`codex-mini-latest`, `gpt-5.3-codex`, `gpt-5.4-mini`).
5. **Given** Epic 10 added a new test file (`tests/test_codex_provider.py`), **When** the audit verifies the test conventions section, **Then** the section remains accurate (no new markers or fixtures were introduced beyond existing conventions).
6. **Given** `_bmad-output/project-context.md` contains critical implementation rules, **When** the audit identifies stale content, **Then** any new code conventions or module references introduced by Epic 10 are added.
7. **Given** `architecture.md` and `prd.md` are planning artifacts, **When** the doc sync executes, **Then** neither file is modified.

## Tasks / Subtasks

- [x] Task 1: Audit changes introduced by Epic 10 (AC: #1)
  - [x] 1.1: Run `git diff main...HEAD --name-only` to identify all files changed in this epic
  - [x] 1.2: Review Story 10.1 (codex provider core): new `providers/codex.py` module, `CodexProvider` class with `_do_invoke()`, `_cleanup()`, `parse_output()`, `supports_model()`, NDJSON parsing, subprocess management
  - [x] 1.3: Review Story 10.2 (provider registry): `providers/__init__.py` updated with `codex` registration, lazy import, `__all__` update
  - [x] 1.4: Review Story 10.3 (structured output): `--output-schema` flag, `--output-last-message` file output, `workflows/schemas/codex-review-schema.json`, temp file management
  - [x] 1.5: Review Story 10.4 (evidence score integration): `parse_output()` JSON-to-text conversion, P0-P3 priority mapping, clean pass formatting
  - [x] 1.6: Review Story 10.5 (configuration & docs): README updates, CLAUDE.md provider docs, config examples, auth instructions
  - [x] 1.7: Review Story 10.6 (E2E testing): `tests/test_codex_provider.py` test structure, error handling patterns
  - [x] 1.8: Cross-reference changed paths against the Doc Audit Checklist from epic

- [x] Task 2: Update `CLAUDE.md` — Core Subsystems > providers section (AC: #2)
  - [x] 2.1: Already done by Story 10.5 — CodexProvider fully described in providers subsystem (subprocess + NDJSON streaming, structured output, Evidence Score integration)
  - [x] 2.2: Already done by Story 10.5 — P0-P3 priority mapping documented implicitly via "Evidence Score integration" mention

- [x] Task 3: Update `CLAUDE.md` — Provider list and Changing Models section (AC: #3, #4)
  - [x] 3.1: Already done by Story 10.5 — codex in Changing Models section with all model values
  - [x] 3.2: Already done by Story 10.5 — CODEX_API_KEY env var documented in README auth section
  - [x] 3.3: Already done by Story 10.5 — config YAML example shows codex as multi-provider

- [x] Task 4: Update `CLAUDE.md` — Configuration section (AC: #4)
  - [x] 4.1: Verified — Configuration YAML example includes codex as valid provider (line 167-168)
  - [x] 4.2: No new config fields introduced by Epic 10 beyond provider/model

- [x] Task 5: Verify test conventions section (AC: #5)
  - [x] 5.1: Checked — `tests/test_codex_provider.py` uses standard patterns (pytest.raises, MagicMock, patch). No new markers or fixtures.
  - [x] 5.2: Existing docs remain accurate — no updates needed

- [x] Task 6: Evaluate `_bmad-output/project-context.md` for updates (AC: #6)
  - [x] 6.1: No new module reference needed — `providers/codex.py` follows existing provider pattern, subsystem dir already listed
  - [x] 6.2: No new codebase-wide conventions — NDJSON parsing, structured output, JSON-to-text conversion are all Codex-internal patterns
  - [x] 6.3: No new test patterns — `test_codex_provider.py` uses same conventions as existing provider tests
  - [x] 6.4: Fixed pre-existing inaccuracy: `__all__` rule now includes `providers/__init__.py` (was missing). Updated `Last Updated` date
  - [x] 6.5: N/A — correction was made in 6.4

- [x] Task 7: Verify no planning artifacts modified (AC: #7)
  - [x] 7.1: Confirmed — `architecture.md` and `prd.md` were NOT updated by this story
  - [x] 7.2: Verified — no contradictions between `CLAUDE.md` and `project-context.md`
  - [x] 7.3: Verified — only `project-context.md` modified with `__all__` rule correction and date update

## File List

- `_bmad-output/project-context.md` (modified) — Fixed `__all__` rule to include `providers/__init__.py`, updated Last Updated date
- `_bmad-output/implementation-artifacts/10-7-epic-documentation-sync.md` (modified) — This story file, checkboxes marked done

## Dev Notes

### Architecture Patterns and Constraints

- **Do NOT update `architecture.md`, `prd.md`, or `ux-design-specification.md`** — These are planning artifacts owned by the planning phase, per the epic file's Technical Notes section. If they need changes, flag them for a course correction.
- **Documentation-only story** — No production code changes. No test changes. Only markdown file updates.
- **Atomic file writes not needed** — These are documentation files, not state/config files. Standard file writes are appropriate.
- **Line length 100** — Still applies to any code blocks within documentation, though markdown prose is not strictly length-enforced.
- **Follow the Story 9.3 pattern** — This story follows the same doc-sync pattern established in Story 9.3. Use it as a structural reference.

### What Epic 10 Changed (Summary for Doc Updates)

**Story 10.1 — Codex Provider Core:**
- New module: `src/bmad_assist_lite/providers/codex.py`
  - `CodexProvider` — subclass of `BaseProvider`
  - `provider_name` → `"codex"`
  - `default_model` → `"codex-mini-latest"`
  - `supports_model()` — accepts `codex-mini-latest`, `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, or any `gpt-`/`codex-` prefixed model
  - `_do_invoke()` — subprocess via `codex exec --json`, NDJSON streaming, `item.completed` event parsing
  - `_cleanup()` — process termination, reader thread joining (same pattern as GeminiProvider)
  - Windows: `stdin=subprocess.DEVNULL` to avoid hang bug, `get_subprocess_kwargs()` for `CREATE_NO_WINDOW`
  - Auth: `CODEX_API_KEY` env var from `.env` via python-dotenv

**Story 10.2 — Provider Registry:**
- Modified: `src/bmad_assist_lite/providers/__init__.py`
  - Added `"codex": CodexProvider` to `_init_default_providers()`
  - Added `"CodexProvider": ".codex"` to `_lazy_imports`
  - Added `CodexProvider` to `__all__`

**Story 10.3 — Structured Output:**
- New file: `src/bmad_assist_lite/workflows/schemas/codex-review-schema.json`
- Modified: `src/bmad_assist_lite/providers/codex.py`
  - `--output-schema <schema_path>` flag in command construction
  - `--output-last-message <temp_output_path>` for file-based output
  - Temp file cleanup in `_cleanup()`
  - Graceful fallback to stdout text if output file doesn't exist

**Story 10.4 — Evidence Score Integration:**
- Modified: `src/bmad_assist_lite/providers/codex.py`
  - `parse_output()` converts structured JSON to Evidence Score text format
  - Priority mapping: P0→CRITICAL(+3), P1→IMPORTANT(+1), P2→MINOR(+0.3), P3→MINOR(+0.3)
  - Plain text fallback for non-JSON output
  - Zero changes to `evidence_score.py`

**Story 10.5 — Configuration & Docs:**
- Modified: `README.md`, `CLAUDE.md` — Codex provider documentation, config examples, auth instructions

**Story 10.6 — E2E Testing & Hardening:**
- New file: `tests/test_codex_provider.py`
  - Test classes: `TestInvocation`, `TestCleanup`, `TestTimeout`, `TestParseOutput`
  - Mocked subprocess for all unit tests
  - Error handling: CLI not found, auth expired, rate limits, malformed NDJSON

### Source Tree Components to Touch

1. **`CLAUDE.md`** — Update: providers subsystem (add CodexProvider), Changing Models section (add codex), Configuration example (add codex option)
2. **`_bmad-output/project-context.md`** — Evaluate: new module reference, code conventions, test patterns

### Project Structure Notes

```
CLAUDE.md                                    <- Update: providers, config, model list
_bmad-output/
  project-context.md                         <- Evaluate: new rules if applicable
  planning-artifacts/
    architecture.md                          <- DO NOT UPDATE
    prd.md                                   <- DO NOT UPDATE
  implementation-artifacts/
    10-1-codex-provider-core.md              <- Reference only
    10-2-provider-registry.md                <- Reference only
    10-3-structured-output.md                <- Reference only
    10-4-evidence-score-integration.md       <- Reference only
    10-5-configuration-and-docs.md           <- Reference only
    10-6-e2e-testing-and-hardening.md        <- Reference only
    10-7-epic-documentation-sync.md          <- This file
    sprint-status.yaml                       <- Updated by workflow
```

### References

- Epic file: Story 10.7 definition with Doc Audit Checklist
- Story 9.3: `9-3-epic-documentation-sync.md` — Prior doc-sync story (pattern reference)
- `CLAUDE.md` — Current state of project documentation
- `_bmad-output/project-context.md` — Current state of AI agent rules
- Architecture: "Provider Implementor Reference" section — BaseProvider extension pattern

## Testing Requirements

- No automated tests — this is a documentation-only story
- Manual verification: read updated docs to confirm they accurately reflect the implemented behavior from Stories 10.1-10.6
- Self-validation: run `git diff` on modified docs to confirm only intended sections changed
- Verify no contradictions introduced between `CLAUDE.md` and `project-context.md`
- Verify `architecture.md` and `prd.md` were NOT modified

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/ tests/` | **N/A** (documentation-only, no code changes) |
| Typecheck | `mypy src/` | **N/A** (documentation-only, no code changes) |
| Build | N/A (library, no build step) | **N/A** |
| Tests | `pytest -v --tb=short -m "not slow"` | **N/A** (documentation-only, no code changes) |

> Note: This is a documentation-only story — no production code or test files are modified. Quality gates are not applicable but should be run as regression checks before epic close.
