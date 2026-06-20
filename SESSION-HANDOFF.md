# Session Handoff — bmad-assist-lite (`feature/cursor-cli`)

> **Constraint:** NO commits until explicitly told.
> **Last updated:** 2026-06-19 — `cli_path` lever now COMMITTED (`2bd20a5`, local-only, not pushed);
> handoff + perf plan committed separately (`d5a194a`). Handoff now tracks the committed lever + the
> next-session performance discussion.

---

## Open Work — status board

| # | Item | Status |
|---|------|--------|
| 1 | 0.2.x SDK: bundled-CLI truncation + `cli_path` lever | 🟢 **committed (`2bd20a5`, local-only); truncation cure still unproven** |
| 2 | Performance + quality-gate robustness feature (future work) | 📋 **PLANNED — consolidated in `docs/performance-optimization-plan.md`. Discuss next session.** |

> 🗓️ **NEXT SESSION — discuss performance improvements first.** Before picking what to implement, review the
> **reports/artifacts from the previous run**: timing breakdown, `synthesis-diff-*.patch` (validation-phase signal),
> `post-merge-qg-failures-*.md` (Part B evidence), and the quality-gate outcomes. Decide scope/sequencing
> from that data, then start (recommended first step: per-phase model routing — isolated, biggest win).

---

## 1. 🟢 0.2.x SDK — bundled-CLI truncation + `cli_path` lever (committed `2bd20a5`, local-only)

> Decision: **stay on the latest SDK** (do NOT pin to 0.1.x). Installed 0.2.102; latest is **0.2.103**.

### The reliability problem
On heavy phases the SDK's benign-success quirk can fire mid-turn and **truncate the output** (e.g. a
`dev_story` that ran 3m17s and produced only **147 chars**, so code_review rejected it and `fix_quality_gate`
did the actual implementation). Root: SDK 0.2.x runs a **bundled `claude.exe` (v2.1.178)**; 0.1.x used the
system CLI and didn't do this.

### `cli_path` lever — applied + validated, COMMITTED (`2bd20a5`)
A distinct, newer system CLI exists, so `cli_path` is the right lever:
| | Version | Source |
|---|---|---|
| Bundled CLI (SDK 0.2.102 launches this) | **2.1.178** | `.venv/.../claude_agent_sdk/_bundled/claude.exe` |
| System CLI on PATH | **2.1.181** | `~/.local/bin/claude.exe` |

**Committed in `2bd20a5` (gates re-run green immediately before commit: `ruff check src/`, `ruff format --check`, and mypy clean on changed files; 84 targeted tests pass):**
- `providers/claude_sdk.py` — new `_resolve_cli_path()` (best-effort: `resolve_cli_path("claude")`, swallows
  `ProviderError`→`None` so SDK falls back to bundled) wired into `ClaudeAgentOptions(cli_path=...)`.
- `core/config.py` — added `claude` field to `CliPathsConfig`.
- `providers/base.py` — added `claude` entry to `_KNOWN_CLI_PATHS` (`~/.local/bin` + Win `%APPDATA%\npm`).
- Tests: +4 in `test_claude_sdk_timeout.py`, +4 in `test_cursor_resolution.py`. Docs: `CLAUDE.md` cli_paths example.

**Real-run verification (2026-06-18):** every Claude phase logged `Using system Claude CLI for SDK: ...2.1.181`;
no 147-char truncation; benign quirk never fired; no regression. ⚠️ **Inconclusive as a *cure*** — the quirk is
intermittent and didn't fire in either run, so this proves "works + no regression," NOT "definitively fixes
truncation." **Decision: keep `cli_path`; committed in `2bd20a5` (local-only, not pushed).**

### Fallbacks if truncation recurs
Unused 0.2.x levers confirmed in `types.py`: `fallback_model` (opus→sonnet — degraded-but-complete instead of
hard-blocked), `max_turns`, `setting_sources`, `strict_mcp_config`, `include_partial_messages`,
`resume`/`session_id`/`fork_session`. Bigger fallback: **replace `claude_sdk.py` with a subprocess+NDJSON
provider** (like `CodexProvider`/`CursorProvider` already are — paperclip-style
`claude --print - --output-format stream-json`, prompt over stdin). Reference:
[[reference_sdk_02x_performance_features]].

> Related cheap fix (also tracked as **create_story skip-if-exists** in the perf report): `create_story` has no skip-if-exists, so
> every run re-rolls the flaky CLI on a heavy phase. Skipping when the `.md` exists & is non-empty cuts a full
> Opus phase + truncation exposure.

---

## 2. 📋 Performance + quality-gate robustness feature — FUTURE WORK

**Full consolidated plan → [`docs/performance-optimization-plan.md`](docs/performance-optimization-plan.md)**
(refreshed 2026-06-19; reconciled against verified code state). Summary of what lives there:

- **Part A — per-story wall-clock.** Root cause is structural (96–98% is real LLM work), not infra. Top levers:
  **Per-phase model routing** (Sonnet for create_story + validate_story_synthesis; keep Opus@max for
  dev/synthesis/fix — biggest win, isolated) and **parallel QG commands**. Plus drop validation
  phases (needs data), create_story skip-if-exists. FIX-QG already ✅ shipped.
- **Part B — post-merge QG robustness.** Spurious blocks because the **base repo is never bootstrapped**
  (missing `node_modules` → gates fail in ~1s → "failed gates: unknown"). Fixes: bootstrap base/canary at run
  start + classify env-vs-real failures. Plus open conflict-resolution / merge-rollback design questions.
- **Part C — shared gate runner.** The architecture that unifies parallel QG commands + Part B (one runner: parallel
  execution + `pass|real|env` classification, reused across per-story / post-merge / epic gates).

**Recommended sequencing:** per-phase model routing (independent, first) → extract shared gate runner → base-repo/canary
bootstrap → conflict-resolution robustness → cheap follow-ons (create_story skip-if-exists, validation-phase data, cache-toolchain + skip-build-in-QG).

---

## Working-tree state on `feature/cursor-cli`

**Committed (local only, not pushed) — newest first:**
- `d5a194a` docs: refresh perf+QG plan and add session handoff
- `2bd20a5` fix(claude): use system CLI via `cli_path` to avoid bundled-CLI truncation (item #1)
- `c4572cd` fix(claude): tolerate benign "error result: success" SDK escalation
- `7eb3cf6` fix(compiler): strip XML-illegal control chars from embedded content

`2bd20a5` contents: `providers/claude_sdk.py` (`_resolve_cli_path()` + `cli_path=` wiring),
`core/config.py` (`claude` field on `CliPathsConfig`), `providers/base.py` (`claude` in
`_KNOWN_CLI_PATHS`), `tests/test_claude_sdk_timeout.py` (+4), `tests/test_cursor_resolution.py` (+4),
`CLAUDE.md`. This handoff status update lands as a further commit on top.

Working tree clean. Branch is ahead of upstream; nothing pushed.
