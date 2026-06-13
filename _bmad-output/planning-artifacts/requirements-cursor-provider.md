# Requirements: Cursor CLI Provider (Composer 2.5) + Linux Migration

**Date:** 2026-06-12
**Status:** Draft — pending review by Shravan
**Branch:** feature/parallel-story-execution

---

## Core Concept

Add a fourth provider (`cursor`) that wraps the **Cursor CLI** (`agent` binary) as a subprocess, exposing Cursor's **Composer 2.5** model as a **master-capable provider** — primarily targeting `dev_story` and `code_review_synthesis`. Deploy bmad-assist-lite to a **dedicated Linux box/VM**, since the Cursor CLI's native Windows build is unreliable (Node-based, reported console freezes). Windows support for the existing providers (claude/gemini/codex) is retained — this is a cross-platform hardening, not a port away from Windows.

---

## Motivation

- **Composer 2.5 quality/cost/speed:** SWE-Bench Multilingual 79.8% (Opus 4.7: 80.5%) at ~200 tok/s and **$0.50/M input, $2.50/M output** — a fraction of Opus cost for the most token-hungry phases. Today `dev_story`, `code_review_synthesis`, and `fix_quality_gate` are Opus-only (project decision, Feb 2026).
- **Cursor-exclusive model:** No public API, no third-party gateway. CLI subprocess is the only integration path.
- **Prior validation:** June 2026 research session verified headless mode, NDJSON output, model selection, and subprocess invocability (adversarially fact-checked). Full integration facts in memory: `reference_cursor_cli_research.md`.

---

## Key Design Decisions

### 1. CodexProvider Is the Implementation Template
`CursorProvider` follows the established subprocess + NDJSON streaming pattern from `providers/codex.py`: `Popen` → parse NDJSON events from stdout → feed `ResultCollector` → extract final text. Rule of Three: this is the third subprocess-NDJSON provider (Codex, now Cursor) — shared parsing helpers may be extracted **only if** duplication is real after implementation, not preemptively.

### 2. Master-Capable, Write-Mode First
The primary role is master provider for `dev_story` and `code_review_synthesis`:
- **Write mode:** `agent -p --force --trust --model composer-2.5 --output-format stream-json`. Without `--force`, print mode only *proposes* file changes — `--force` is mandatory for implementation phases.
- **Command execution:** allowed. `code_review_synthesis` runs build/test/lint as the single Master LLM — consistent with the existing multi-LLM safety constraint.
- **Multi/validator role (secondary):** if used in `providers.multi` for `code_review`, the invocation must be read-only: omit `--force` + project-level `.cursor/cli.json` with `{"permissions": {"deny": ["Write(**)", "Shell(**)"]}}` (deny rules survive `--force`).

### 3. Cost Guard: Model Verification Is Mandatory
Known unfixed Cursor bug (June 2026): runs silently switch to `composer-2.5-fast` (**$3/$15 per M — 6x cost**). The provider MUST parse the `model` field from the stream-json **system init event** and verify it matches the requested model. On mismatch: log loudly at WARNING and record in the result; this is the difference between a $2 and a $12 dev_story.

### 4. Error Handling: stderr + Missing Terminal Event
Cursor CLI **never emits errors as JSON**: failures are non-zero exit + stderr text, and the NDJSON stream may end **without** a terminal `{"type":"result"}` event. Additionally there are reports of exit code 1 after successful runs. Therefore:
- Success signal = terminal result event received (not exit code alone).
- Missing terminal event = failure; surface the **tail** of stderr in the error (matching the Codex provider convention).
- Hard timeouts always enforced — the CLI has a documented history of `-p` hangs. The existing BaseProvider grace-period machinery applies unchanged.

### 5. Auth and Configuration
- `CURSOR_API_KEY` from the package-root `.env` (existing convention — API keys live with the tool). Cursor Pro plan key available.
- Config addition: `provider: cursor` valid in `providers.master` and `providers.multi`; CLI path override via `providers.cli_paths.cursor`.
- `resolve_cli_path()` gains binary names `agent` / `cursor-agent` (Linux install location `~/.local/bin` is already in the known-paths list).

### 6. Prompt Delivery
Prompts exceed 32K chars routinely. On Linux the argv limit (~2MB) makes argument-passing viable as the default. Stdin piping (`agent -p` with prompt on stdin) is officially recognized to trigger print mode but its semantics are undocumented — resolve via spike S1 before choosing it.

### 7. Linux Migration: Validate, Don't Rewrite
The Linux audit (June 2026) found Unix branches already exist and look correct across all 32 platform-sensitive locations (`_windows.py`, `locking.py`, `signals.py`, `command_runner.py`, orchestrator, bootstrap). The migration is a **validation exercise plus one code fix**:
- **Code fix:** `terminate_process()` Unix branch sends SIGTERM only; the documented SIGTERM→SIGKILL escalation is not implemented. A hung `agent` process would survive. Implement the escalation.
- **Validation gate:** full `pytest` pass + one complete story loop on the Linux target before trusting it for real epics.
- **Config hygiene:** any Windows-syntax commands in project YAML (`quality_gate.*`, `parallel.setup_commands`) need POSIX equivalents (current defaults are already portable).
- Tests that mock Windows-only behavior get `sys.platform` skip markers as needed.

### 8. Deployment Target
Dedicated Linux box/VM. Setup must be documented: Cursor CLI install (`curl https://cursor.com/install -fsS | bash`), `agent login` not required (API key auth), `CURSOR_API_KEY` in `.env`, repo clone + `.venv` + `pip install -e ".[dev]"`. Note: CLI auto-updates by default with no documented opt-out; version pinning only via direct tarball download (accept auto-update unless spike S4 finds problems).

---

## Functional Requirements

| ID | Requirement |
|----|-------------|
| FR1 | `CursorProvider` extends `BaseProvider` implementing `provider_name`, `_do_invoke()`, `_cleanup()`, `parse_output()`, `supports_model()` |
| FR2 | `provider_name` returns `"cursor"`; `supports_model()` accepts `composer-*` model strings (and `auto`) |
| FR3 | Invocation: `agent -p --output-format stream-json --model <model>` with workspace = project root |
| FR4 | Master phases run write-mode (`--force --trust`); multi/code_review runs read-only (no `--force` + permissions deny config) |
| FR5 | Streaming assistant-event text fed to `ResultCollector.add()` as it arrives (activity tracking for grace period) |
| FR6 | Final response text extracted from the terminal `{"type":"result"}` event's `result` field; `session_id` captured |
| FR7 | Missing terminal event or non-zero exit without result event → `ProviderError` carrying the tail of stderr |
| FR8 | Model verification: system-init event `model` field compared to requested model; mismatch logged at WARNING and recorded |
| FR9 | `resolve_cli_path()` resolves `agent`/`cursor-agent` with standard 3-tier order (config override → PATH → known locations) |
| FR10 | Config schema accepts `provider: cursor` in `master` and `multi`; `providers.cli_paths.cursor` override supported |
| FR11 | `parse_output()` returns response text compatible with Evidence Score parsing when used as validator |
| FR12 | Timeout + grace-period behavior inherited unchanged from `BaseProvider.invoke()` |
| FR13 | Prompt delivery works for prompts >32K chars (argv on Linux; stdin if spike S1 confirms) |
| FR14 | `terminate_process()` Unix branch escalates SIGTERM → SIGKILL after grace (per its docstring contract) |
| FR15 | Linux deployment setup documented (CLI install, API key, project bootstrap) |

## Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR1 | Zero regression to existing providers and loop behavior on Windows |
| NFR2 | Existing toolchain compliance: strict mypy, ruff, pytest conventions (mocked subprocess tests; no live CLI calls in CI) |
| NFR3 | Cost safety: a run can never silently proceed on `composer-2.5-fast` without a logged warning |
| NFR4 | Reliability: every Cursor invocation bounded by a hard timeout; no orphaned `agent` processes after timeout/kill on either platform |
| NFR5 | Provider failures are non-fatal to multi-validator aggregation (consistent with existing multi-LLM handling) |

---

## Open Questions / Spikes (resolve on the Linux box before or during implementation)

| ID | Question | Why it matters |
|----|----------|----------------|
| S1 | Stdin prompt semantics: does `cat prompt.md \| agent -p` use stdin as the prompt? | Chooses prompt delivery mechanism for >32K prompts |
| S2 | Does `--mode=ask` compose with `-p` for read-only review? | Simpler read-only mechanism than deny-config if it works |
| S3 | Composer 2.5 actual context window (256K vs 1M claims conflict) | Sets context filtering budgets for dev_story prompts |
| S4 | Auto-update behavior in practice; `agent --version` stability across a multi-hour epic run | Version pinning decision |
| S5 | `agent --list-models` output on the Pro-plan API key — confirm `composer-2.5` is selectable headless | Validates the whole premise before building |

---

## References

- Memory: `reference_cursor_cli_research.md` (full CLI facts with citations, June 2026)
- Prior research session: `bc680461` (June 2026) — Cursor CLI evaluated as code-review provider; headless mode verified
- Linux readiness audit: this session (June 12, 2026) — 32 locations classified, one code fix identified
- Sibling pattern: `src/bmad_assist_lite/providers/codex.py` + `project_codex_provider_plan.md` memory
