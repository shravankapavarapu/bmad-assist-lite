# Story 10.5: Configuration & Documentation

## Story Metadata

| Field | Value |
|-------|-------|
| **Story ID** | 10-5-configuration-and-docs |
| **Title** | Configuration & Documentation |
| **Epic** | Epic 10: Codex CLI Provider (Replace Gemini) |
| **Status** | done |
| **Points** | 1 |
| **Priority** | Medium |
| **Estimate** | Small |
| **Dependencies** | 10-2-provider-registry |
| **Component** | `README.md`, `CLAUDE.md`, config examples |

---

## User Story

As a new user of bmad-assist-lite,
I want clear documentation on how to set up Codex CLI as a reviewer,
So that I can configure it without reading the source code.

## Description

Update all documentation to include Codex as a supported provider: README prerequisites, installation instructions, config examples, model table, and comparison table. Update CLAUDE.md architecture docs. This is a documentation-only story with no code changes.

### Current State

README and CLAUDE.md reference only Claude and Gemini providers. Config examples show Gemini as the multi-LLM reviewer. The plugin example section mentions Codex as a hypothetical local plugin example, but there are no first-class Codex docs.

### Target State

- README: Add Codex to prerequisites, install instructions (Windows PowerShell + macOS/Linux), supported providers table, config examples, auth instructions for `CODEX_API_KEY`
- CLAUDE.md: Add CodexProvider to architecture docs (Core Subsystems > providers), provider list, implementor reference, Changing Models section
- Config example shows Codex as a multi-provider option alongside Gemini

---

## Acceptance Criteria

**Given** a user reads the README
**When** they look at the config example
**Then** they see `provider: codex` as a documented option with model values

**Given** a user needs to install Codex CLI
**When** they read the prerequisites section
**Then** they find platform-specific install commands (Windows PowerShell, macOS/Linux curl)

**Given** a developer reads CLAUDE.md
**When** they look at the provider architecture section
**Then** `CodexProvider` is documented with its subprocess pattern and structured output approach

---

## Tasks

### README Updates

- [x] **Task 1: Update README prerequisites section**
  Add Codex CLI as a third prerequisite alongside Claude Code CLI and Gemini CLI. Include a link to Codex CLI and note that it requires authentication.

- [x] **Task 2: Add Codex CLI install instructions**
  Add platform-specific install commands in the Install section or a new subsection:
  - Windows PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"`
  - macOS/Linux: `curl -fsSL https://chatgpt.com/codex/install.sh | sh`

- [x] **Task 3: Add Codex to supported providers table**
  Update the "Supported model values" table in the Changing Models section to include a Codex row:
  - Provider: `codex`
  - Valid Models: `codex-mini-latest`, `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, or any `gpt-`/`codex-` prefixed model
  - Default: `codex-mini-latest`
  - Source File: `src/bmad_assist_lite/providers/codex.py`

- [x] **Task 4: Add config example with `provider: codex`**
  Update the config YAML examples in the Configuration and Changing Models sections to show Codex as a multi-provider option. Example:
  ```yaml
  multi:
    - provider: codex
      model: gpt-5.3-codex
    - provider: claude
      model: sonnet
  ```

- [x] **Task 5: Document CODEX_API_KEY authentication**
  Add auth instructions explaining:
  - Set `CODEX_API_KEY` env var in `.env` file (recommended for automation)
  - Pay-as-you-go API auth avoids ChatGPT rate limits
  - Example `.env` entry: `CODEX_API_KEY=your-api-key-here`

- [x] **Task 6: Update provider count in comparison table**
  Update the "How It Differs from bmad-assist" table row for Providers from `2 (Claude SDK, Gemini)` to `3 (Claude SDK, Gemini, Codex)`.

- [x] **Task 7: Update README overview text**
  Update the opening description to mention Codex alongside Claude and Gemini (e.g., "Coordinates Claude Code CLI, Gemini CLI, and Codex CLI").

### CLAUDE.md Updates

- [x] **Task 8: Update CLAUDE.md Project Overview**
  Update the opening paragraph to mention Codex CLI alongside Claude Code CLI and Gemini CLI.

- [x] **Task 9: Update CLAUDE.md Core Subsystems > providers**
  Add CodexProvider to the providers bullet point. Describe its subprocess + NDJSON pattern, structured output via `--output-schema`, and `--output-last-message` file output. Mention it follows the GeminiProvider pattern (subprocess with reader threads).

- [x] **Task 10: Update CLAUDE.md Changing Models section**
  Add Codex to the YAML example and the "Valid model values" list:
  - `codex` (`providers/codex.py`): `codex-mini-latest`, `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, or any `gpt-`/`codex-` prefixed model. Default: `codex-mini-latest`

- [x] **Task 11: Update CLAUDE.md Configuration section**
  Update the config YAML example to show Codex as a multi-provider option.

---

## File List

| File | Action | Description |
|------|--------|-------------|
| `README.md` | MODIFY | Add Codex to prerequisites, install, config, auth, provider table, comparison table |
| `CLAUDE.md` | MODIFY | Add CodexProvider to architecture, provider list, models, config example |

---

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-05-30 | Story created | BMAD |

---

## Dev Notes

### Technical Notes

- Windows install: `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"`
- macOS/Linux install: `curl -fsSL https://chatgpt.com/codex/install.sh | sh`
- Auth: `CODEX_API_KEY` env var in `.env` file (recommended for automation -- no browser login, no ChatGPT rate limits)
- Document the known Windows stdin bug workaround (handled internally by the provider via `stdin=subprocess.DEVNULL`)
- The plugin example section in README currently shows a hypothetical `CodexProvider` as a local plugin -- this should be updated since Codex is now a built-in provider

### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Documentation only, no code changes

<!-- QUALITY-GATE: BLOCKING -->

### Testing Requirements

#### Unit Tests (Mandatory)

No unit tests required -- this is a documentation-only story with no code changes.

#### Quality Gates

| Gate | Command | Expected |
|------|---------|----------|
| Lint | `ruff check src/` | Pass (no src changes) |
| Type Check | `mypy src/` | Pass (no src changes) |
| Tests | `pytest -q --tb=short --no-header` | Pass (no test changes) |
