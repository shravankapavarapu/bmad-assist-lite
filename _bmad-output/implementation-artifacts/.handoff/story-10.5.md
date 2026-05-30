## Dev Summary
**Status:** done
**Files changed:**
- README.md (modified)
- CLAUDE.md (modified)
- _bmad-output/implementation-artifacts/10-5-configuration-and-docs.md (modified)

**Tasks completed:** 11/11
**Decisions made:**
- Added Codex CLI install instructions as a sub-section under Install (matching the existing structure pattern) rather than a separate top-level section
- Placed CODEX_API_KEY auth docs near the existing Context7 API Key section in the Library Documentation area, renamed the existing heading to "Optional: Context7 API Key" for clarity
- Updated the local plugin example from CodexProvider to OpenCodeProvider since Codex is now a built-in provider
- Removed Codex from the "Additional providers" feature migration guide row and ProviderPlugin examples table since it is no longer a plugin candidate
- Updated provider credentials security note to mention CODEX_API_KEY and .env file security
- Updated project structure comment from "Claude SDK + Gemini CLI" to include Codex CLI
- Updated init command description to mention Codex as a multi-provider option
- Updated CLAUDE.md provider_name property example list to include "codex"

**Blockers:** none

---

## Review Findings (Cycle 1)
**Verdict:** NEEDS_FIXES

### PATCH: README init description says "Gemini/Codex multi" but init template only has Gemini + Claude

**File:** `README.md` line 61
**Evidence:** The README says `bmad-assist-lite init` creates config with "default: Claude master + Gemini/Codex multi". But the actual init command template in `src/bmad_assist_lite/cli.py` lines 441-480 only includes `gemini` and `claude` as multi-providers -- Codex is NOT in the default template. CLAUDE.md correctly says "Claude master + Gemini multi" (line 101).
**Fix:** Change README line 61 from `(default: Claude master + Gemini/Codex multi)` to `(default: Claude master + Gemini multi)`. Alternatively, update the init template in cli.py to include Codex -- but that is a code change outside this documentation-only story's scope.

### PATCH: "Codex CLI Authentication" subsection is misplaced under "Library Documentation (Context7)"

**File:** `README.md` line 361
**Evidence:** The `### Codex CLI Authentication` heading is a subsection of `## Library Documentation (Context7)` (line 327). Codex authentication has nothing to do with Context7 library documentation. This is a structural/organizational error that will confuse readers navigating by section headers.
**Fix:** Move the "Codex CLI Authentication" subsection out of the "Library Documentation (Context7)" section. Options: (a) promote it to a `##` top-level section between "Library Documentation" and "Sprint Status Tracking", or (b) place it under "Quick Start" near the prerequisites, or (c) add it as a subsection under "Configuration".

### DISMISSED: Model names accuracy

The documented model names (`codex-mini-latest`, `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`) match the `supports_model()` implementation which accepts any `gpt-` or `codex-` prefixed model string. The default model `codex-mini-latest` matches both `default_model` property and the fallback in `_do_invoke()`. All correct.

### DISMISSED: Install URLs

Install URLs (`https://chatgpt.com/codex/install.ps1` and `install.sh`) match the epic spec. GitHub link (`https://github.com/openai/codex`) is the correct repo.

### DISMISSED: CODEX_API_KEY documentation accuracy

The documentation correctly states that `CODEX_API_KEY` is passed via environment variable. The provider uses `os.environ.copy()` (line 358 of codex.py), so the key is automatically passed through to the subprocess when loaded by python-dotenv. The docs accurately describe the passthrough mechanism.

### DISMISSED: Formatting consistency

Codex entries in the provider tables, config YAML examples, and model value lists follow the same formatting patterns as existing Claude and Gemini entries. The CLAUDE.md provider_name example list now includes "codex". Config examples show Codex as a multi-provider option with consistent indentation and commenting style.

### DISMISSED: AC satisfaction

All 3 acceptance criteria are satisfied: (1) `provider: codex` with model values is in config examples, (2) platform-specific install commands are present for both Windows PowerShell and macOS/Linux, (3) CLAUDE.md documents CodexProvider with subprocess + NDJSON pattern. All 11 tasks marked complete.

---

## Fix Summary (Cycle 1)
**Fixes applied:** 2
**Files modified:**
- README.md

**Issues encountered:** none

---

## QA Results
**Verdict:** PASS

| # | AC (short) | Status | Evidence |
|---|------------|--------|----------|
| 1 | README shows `provider: codex` with model values | PASS | README lines 160, 218-226: `provider: codex` in both Configuration and Changing Models YAML examples. Line 234: full Codex row in Supported model values table listing `codex-mini-latest`, `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, default `codex-mini-latest`, source `providers/codex.py`. |
| 2 | Prerequisites have platform-specific install commands | PASS | README line 21: Codex CLI listed as prerequisite with GitHub link. Lines 38-49: dedicated "Install Codex CLI" subsection with Windows PowerShell (`irm .../install.ps1 \| iex`) and macOS/Linux (`curl -fsSL .../install.sh \| sh`) commands. |
| 3 | CLAUDE.md documents CodexProvider with subprocess pattern | PASS | CLAUDE.md line 30: providers subsection describes `codex.py` as "CodexProvider using subprocess + NDJSON stream parsing (following the GeminiProvider subprocess pattern), with structured output via `--output-schema` and `--output-last-message` file output for Evidence Score integration". Line 96: `provider_name` example includes `"codex"`. Lines 116-131: Changing Models section lists Codex models and config example. |

**Accuracy cross-check against codex.py source:**
- `provider_name = "codex"` (codex.py line 202) -- matches docs
- `default_model = "codex-mini-latest"` (line 208) -- matches docs
- `supports_model` accepts `gpt-`/`codex-` prefixes (lines 210-216) -- matches docs
- Subprocess + NDJSON pattern confirmed (line 385: `process_ndjson_stream`) -- matches docs
- `--output-schema` and `--output-last-message` flags confirmed (lines 334-339) -- matches docs

**Cycle 1 fix verification:**
- README init description: says "Claude master + Gemini multi" (not "Gemini/Codex multi") -- FIXED
- Codex CLI Authentication section: `## Codex CLI Authentication` (level 2, top-level) at line 373, no longer nested under Context7 -- FIXED

**Fixes applied:** None (all issues resolved in prior cycle)
**Gaps remaining:** None
