# Session Handoff — bmad-assist-lite (`feature/cursor-cli`)

> **Constraint:** NO commits until explicitly told.
> **Last updated:** 2026-06-19 — folded perf (was #3) + post-merge QG robustness (was #1) into
> `docs/performance-optimization-plan.md`. Handoff now tracks the uncommitted `cli_path` lever + the
> next-session performance discussion.

---

## Open Work — status board

| # | Item | Status |
|---|------|--------|
| 1 | 0.2.x SDK: bundled-CLI truncation + `cli_path` lever | 🟢 **applied + validated (UNCOMMITTED); truncation cure still unproven** |
| 2 | Performance + quality-gate robustness feature (future work) | 📋 **PLANNED — consolidated in `docs/performance-optimization-plan.md`. Discuss next session.** |

> 🗓️ **NEXT SESSION — discuss performance improvements first.** Before picking what to implement, review the
> **reports/artifacts from the previous run**: timing breakdown, `synthesis-diff-*.patch` (OPT-2 signal),
> `post-merge-qg-failures-*.md` (Part B evidence), and the quality-gate outcomes. Decide scope/sequencing
> from that data, then start (recommended first step: OPT-1 per-phase model routing — isolated, biggest win).

---

## 1. 🟢 0.2.x SDK — bundled-CLI truncation + `cli_path` lever (applied, uncommitted)

> Decision: **stay on the latest SDK** (do NOT pin to 0.1.x). Installed 0.2.102; latest is **0.2.103**.

### The reliability problem
On heavy phases the SDK's benign-success quirk can fire mid-turn and **truncate the output** (e.g. a
`dev_story` that ran 3m17s and produced only **147 chars**, so code_review rejected it and `fix_quality_gate`
did the actual implementation). Root: SDK 0.2.x runs a **bundled `claude.exe` (v2.1.178)**; 0.1.x used the
system CLI and didn't do this.

### `cli_path` lever — applied + validated, NOT committed
A distinct, newer system CLI exists, so `cli_path` is the right lever:
| | Version | Source |
|---|---|---|
| Bundled CLI (SDK 0.2.102 launches this) | **2.1.178** | `.venv/.../claude_agent_sdk/_bundled/claude.exe` |
| System CLI on PATH | **2.1.181** | `~/.local/bin/claude.exe` |

**Applied (uncommitted), all gates green (suite 1760 passed = 1752 + 8 new):**
- `providers/claude_sdk.py` — new `_resolve_cli_path()` (best-effort: `resolve_cli_path("claude")`, swallows
  `ProviderError`→`None` so SDK falls back to bundled) wired into `ClaudeAgentOptions(cli_path=...)`.
- `core/config.py` — added `claude` field to `CliPathsConfig`.
- `providers/base.py` — added `claude` entry to `_KNOWN_CLI_PATHS` (`~/.local/bin` + Win `%APPDATA%\npm`).
- Tests: +4 in `test_claude_sdk_timeout.py`, +4 in `test_cursor_resolution.py`. Docs: `CLAUDE.md` cli_paths example.

**Real-run verification (2026-06-18):** every Claude phase logged `Using system Claude CLI for SDK: ...2.1.181`;
no 147-char truncation; benign quirk never fired; no regression. ⚠️ **Inconclusive as a *cure*** — the quirk is
intermittent and didn't fire in either run, so this proves "works + no regression," NOT "definitively fixes
truncation." **Decision: keep `cli_path`.** Commit when ready.

### Fallbacks if truncation recurs
Unused 0.2.x levers confirmed in `types.py`: `fallback_model` (opus→sonnet — degraded-but-complete instead of
hard-blocked), `max_turns`, `setting_sources`, `strict_mcp_config`, `include_partial_messages`,
`resume`/`session_id`/`fork_session`. Bigger fallback: **replace `claude_sdk.py` with a subprocess+NDJSON
provider** (like `CodexProvider`/`CursorProvider` already are — paperclip-style
`claude --print - --output-format stream-json`, prompt over stdin). Reference:
[[reference_sdk_02x_performance_features]].

> Related cheap fix (also tracked as **OPT-8** in the perf report): `create_story` has no skip-if-exists, so
> every run re-rolls the flaky CLI on a heavy phase. Skipping when the `.md` exists & is non-empty cuts a full
> Opus phase + truncation exposure.

---

## 2. 📋 Performance + quality-gate robustness feature — FUTURE WORK

**Full consolidated plan → [`docs/performance-optimization-plan.md`](docs/performance-optimization-plan.md)**
(refreshed 2026-06-19; reconciled against verified code state). Summary of what lives there:

- **Part A — per-story wall-clock.** Root cause is structural (96–98% is real LLM work), not infra. Top levers:
  **OPT-1** per-phase model routing (Sonnet for create_story + validate_story_synthesis; keep Opus@max for
  dev/synthesis/fix — biggest win, isolated) and **OPT-4** parallel QG commands. Plus OPT-2 (drop validation
  phases — needs data), OPT-8 (skip create_story if `.md` exists). FIX-QG already ✅ shipped.
- **Part B — post-merge QG robustness.** Spurious blocks because the **base repo is never bootstrapped**
  (missing `node_modules` → gates fail in ~1s → "failed gates: unknown"). Fixes: bootstrap base/canary at run
  start + classify env-vs-real failures. Plus open conflict-resolution / merge-rollback design questions.
- **Part C — shared gate runner.** The architecture that unifies OPT-4 + Part B (one runner: parallel
  execution + `pass|real|env` classification, reused across per-story / post-merge / epic gates).

**Recommended sequencing:** OPT-1 (independent, first) → extract shared gate runner → base-repo/canary
bootstrap → conflict-resolution robustness → cheap follow-ons (OPT-8, OPT-2 data, OPT-6/7).

---

## Working-tree state on `feature/cursor-cli`

**Committed (local only, not pushed):**
- `7eb3cf6` fix(compiler): strip XML-illegal control chars from embedded content
- `c4572cd` fix(claude): tolerate benign "error result: success" SDK escalation

**Uncommitted (the `cli_path` lever — item #1; intentionally not committed yet):**
- `src/bmad_assist_lite/providers/claude_sdk.py` — `_resolve_cli_path()` + `cli_path=` wiring
- `src/bmad_assist_lite/core/config.py` — `claude` field on `CliPathsConfig`
- `src/bmad_assist_lite/providers/base.py` — `claude` entry in `_KNOWN_CLI_PATHS`
- `tests/test_claude_sdk_timeout.py` (+4), `tests/test_cursor_resolution.py` (+4)
- `CLAUDE.md` — cli_paths example now lists `claude`
- `SESSION-HANDOFF.md` (this file)
- Gates: `ruff check src/` clean, mypy clean on changed files, suite **1760 passed**.
