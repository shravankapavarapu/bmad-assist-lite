# Story 11.5: Linux Deployment Documentation & Spike Checklist

Status: in-progress

## Story

As a developer setting up the dedicated Linux box,
I want a step-by-step deployment guide with validation gates and the spike checklist,
so that the migration is reproducible and verified before real epics run on it.

## Acceptance Criteria

1. [x] **Copy-pasteable setup instructions:** Given a fresh Linux box and the guide, when the steps are followed top to bottom, then every command is copy-pasteable and ordering is unambiguous — covering Cursor CLI install, PATH check, `CURSOR_API_KEY` placement in `.env`, repo clone, Python 3.11 venv creation, and `pip install -e ".[dev]"`.

2. [x] **Spike S5 gates the entire premise:** Given spike S5 fails (composer-2.5 not visible to the key), when the reader reaches the spike section, then the documented outcome states the feature premise is blocked and names the fallback (revisit role/model before building further).

3. [x] **Each spike S1–S4 fully documented:** Given each spike S1–S4, when documented, then each has: exact command(s), expected outcome, and which architectural decision its result feeds (D1 prompt delivery, D3 read-only mechanism, context budgets, version pinning).

4. [x] **Validation gate sequence documented:** The guide includes the validation gate sequence in order: full `pytest` green → spikes S5/S1–S4 → one complete story loop with `master: cursor` on a sample project → first real epic.

5. [x] **Config example provided:** A `bmad-assist-lite.yaml` example showing cursor as master provider alongside existing multi validators is included.

6. [x] **Troubleshooting section covers known issues:** Hang symptoms (kill behavior escalation per Story 11.1), cost-guard warnings in logs, deny-config leftovers are all documented.

## Tasks / Subtasks

- [x] Task 1: Create `docs/linux-deployment.md` with environment setup section (AC: #1)
  - [x] Document Cursor CLI installation: `curl https://cursor.com/install -fsS | bash` (lands in `~/.local/bin`)
  - [x] Document PATH verification: ensure `~/.local/bin` is on PATH, verify with `command -v cursor-agent || command -v agent` (prefer `command -v` over `which` for POSIX portability)
  - [x] Document `CURSOR_API_KEY` placement in package-root `.env` file (same convention as other provider API keys)
  - [x] Document repo clone, Python 3.11 venv creation (`python3.11 -m venv .venv`), activation, and dev install (`pip install -e ".[dev]"`)
  - [x] Note that `agent login` is NOT required — API key auth via environment is sufficient

- [x] Task 2: Write spike checklist section, S5 first (AC: #2, #3)
  - [x] **S5 (run first — gates everything):** `agent --list-models` — expected: `composer-2.5` appears in output on the Pro-plan key. If absent: feature premise is blocked, must revisit Cursor plan/key role before proceeding. Gates: entire Cursor provider viability
  - [x] **S1:** `echo "What is 2+2?" | agent -p --output-format stream-json --model composer-2.5` — expected: NDJSON stream with result event. Tests stdin prompt delivery semantics. Feeds decision D1 (prompt delivery mechanism for >32K prompts). **If failed:** document error output; if no NDJSON events, try `--output-format text` as fallback and note D1 implications
  - [x] **S2:** `agent -p --mode=ask --output-format stream-json --model composer-2.5 "Summarize this file"` — expected: determine if `--mode=ask` composes with `-p` for read-only invocation. Feeds decision D3 (potential simplification of read-only enforcement beyond deny-config). **If failed:** `--mode=ask` may not compose with `-p`; document incompatibility and confirm deny-config remains the required read-only mechanism
  - [x] **S3:** Probe Composer 2.5 context window (256K vs 1M conflicting claims) — document test approach (e.g., feed progressively larger prompts). Feeds context filtering budget decisions for `dev_story` prompts. **If inconclusive:** use conservative 256K budget for context filtering until confirmed
  - [x] **S4:** `agent --version` before and after a multi-hour run — document auto-update behavior observation. Feeds version pinning decision (accept auto-update unless drift observed). **If version drifts:** document observed version change and evaluate whether pinning is needed

- [x] Task 3: Write validation gate sequence section (AC: #4)
  - [x] Gate 1: `pytest` — full test suite must pass: `python -m pytest -v --tb=short`
  - [x] Gate 2: Spikes S5 → S1 → S2 → S3 → S4 (S5 is go/no-go for the rest)
  - [x] Gate 3: One complete story loop with `master: cursor` on a sample project — end-to-end verification that the provider works in the full dev loop. **Pass criteria:** story reaches "done" status, all expected artifacts are generated, no unhandled errors in logs, and provider does not fall back to a different model mid-run
  - [x] Gate 4: First real epic — production validation on actual project work

- [x] Task 4: Write config example section (AC: #5)
  - [x] Provide a `bmad-assist-lite.yaml` example with cursor as master provider (`master: { provider: cursor, model: composer-2.5 }`) alongside existing multi validators (claude, codex as code review validators)
  - [x] Note the `providers.cli_paths.cursor` override option for non-standard installation paths
  - [x] Include timeout recommendations for cursor-specific phases
  - [x] Note that the `effort` configuration key is not applicable to cursor/composer-2.5 (only applies to Claude Opus 4.7)

- [x] Task 5: Write troubleshooting section (AC: #6)
  - [x] **Hang symptoms:** Document that Story 11.1 implemented SIGTERM→SIGKILL escalation — hung `agent` processes are now force-killed after 5 seconds. Reference `SIGTERM_GRACE_SECONDS` in `_windows.py` (note: despite the filename, this module contains cross-platform process management logic including Unix signal handling)
  - [x] **Cost-guard warnings:** Document the `"Cursor model mismatch: requested %s, got %s"` warning format — indicates silent switch to `composer-2.5-fast` (6× cost). Action: check Cursor account/plan settings
  - [x] **Deny-config leftovers:** Document that orphaned `.cursor/cli.json` files (from crashed read-only invocations) are automatically cleaned on next run via the marker file in `.bmad-assist-lite/cache/cursor-deny-config.marker`. Manual cleanup: delete both the marker and the `.cursor/cli.json` if it was bmad-created
  - [x] **Binary not found:** Document the `ProviderError` message and resolution (set `providers.cli_paths.cursor` in config, or ensure `cursor-agent`/`agent` is on PATH)
  - [x] **Stream ended without result event:** Document this error as a known Cursor CLI quirk — retry is usually sufficient; check stderr for details
  - [x] **`--trust` flag note:** Document that `--trust` is automatically included in headless invocations by the provider (per D1); users should NOT add it manually when running CLI commands outside the provider harness, and should be aware of its security implications

- [x] Task 6: Update `CLAUDE.md` "Changing Models" section (AC: #5)
  - [x] Add cursor/composer-2.5 to the available models documentation if not already present (verify current state — Story 11.2 or 11.3 may have already done this)
  - [x] Also update `cli_paths` documentation in CLAUDE.md with the `cursor` entry (e.g., `providers.cli_paths.cursor`) if not already documented

## Dev Notes

- **This is a documentation-only story** — no production code changes, no test changes
- **Source content:** Requirements doc "Open Questions / Spikes" table (S1–S5) + architecture D1, D3, D13, D14 sections + prior story completion notes
- **Tone:** Keep operational (commands + checks), not architectural — link to `architecture.md` for the "why"
- **Prior stories completed:**
  - Story 11.1 (done): SIGKILL escalation — `SIGTERM_GRACE_SECONDS = 5`, `terminate_process()` now escalates on Unix
  - Story 11.2 (done): Config schema + CLI resolution + provider registry — `provider: cursor` accepted, `resolve_cli_path("cursor")` works, tries `cursor-agent` before `agent`
  - Story 11.3 (done): Full CursorProvider — `_do_invoke()`, NDJSON parsing, cost guard, mode split, `_cleanup()`, `parse_output()`, `supports_model()`
  - Story 11.4 (done): Deny-config lifecycle — `.cursor/cli.json` creation/cleanup for read-only mode, crash recovery sweep in `cleanup.py`
- **FR15** from requirements: "Linux deployment setup documented (CLI install, API key, project bootstrap)"
- **Decision D14:** "Deployment documentation at `docs/linux-deployment.md`: CLI install, API key placement, project bootstrap, spike checklist (S1–S5 from requirements)"
- **Decision D13:** "Validate, don't rewrite. Validation gate before first real epic: full pytest green → spikes S1/S2/S5 → one complete story loop"

### Project Structure Notes

```
docs/
└── linux-deployment.md           [NEW]   Step-by-step deployment guide with
                                          setup, spike checklist, validation
                                          gates, config example, troubleshooting

CLAUDE.md                         [TOUCH] Verify/update "Changing Models" section
                                          with cursor/composer-2.5 entry
```

No production code files modified. No test files modified.

### References

- **Epic file:** `_bmad-output/planning-artifacts/epic-11.md` — Story 11.5 section (acceptance criteria, technical notes)
- **Architecture:** `architecture.md` — Decisions D13 (validate, don't rewrite), D14 (deployment documentation), D1 (prompt delivery), D3 (read-only enforcement)
- **Requirements:** `requirements-cursor-provider.md` — FR15 (deployment docs), Open Questions/Spikes table (S1–S5), NFR1–NFR5
- **Prior stories:**
  - Story 11.1: SIGKILL escalation details — `SIGTERM_GRACE_SECONDS`, escalation behavior
  - Story 11.2: Config schema, CLI resolution order (`cursor-agent` before `agent`), `CliPathsConfig.cursor` field
  - Story 11.3: CursorProvider implementation — invocation flags, NDJSON events, cost guard warning format, error messages
  - Story 11.4: Deny-config lifecycle — `.cursor/cli.json` format, marker file location, crash recovery sweep
- **Research artifact:** `reference_cursor_cli_research.md` — CLI facts with citations (June 2026)

## Testing Requirements

- No automated tests — this is a documentation-only story
- **Manual validation:** Each command in the guide should be verified as copy-pasteable on a Linux system
- **Link verification:** Any cross-references to `architecture.md` or other docs should be checked for accuracy
- **Config example validation:** The YAML example should be syntactically valid and match the current `bmad-assist-lite.yaml` schema

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | N/A (documentation only) | **N/A** |
| Typecheck | N/A (documentation only) | **N/A** |
| Build | N/A (documentation only) | **N/A** |
| Tests | N/A (documentation only) | **N/A** |

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (via Claude Code)

### Debug Log References
No debug issues encountered. Documentation-only story with no code execution required.

### Completion Notes List
1. Created `docs/linux-deployment.md` (new file) — comprehensive deployment guide with 6 sections:
   - Section 1: Environment Setup (CLI install, PATH verification, API key, repo clone, venv, pip install)
   - Section 2: Spike Checklist (S5 first as gate, then S1-S4 with exact commands, expected outcomes, failure paths, and decision references)
   - Section 3: Validation Gate Sequence (4 gates in order: pytest → spikes → story loop → first epic, with pass criteria)
   - Section 4: Configuration (full bmad-assist-lite.yaml example with cursor master, multi validators, cli_paths, timeouts)
   - Section 5: Architecture References (links to architecture.md decisions D1, D3, D13, D14)
   - Section 6: Troubleshooting (6 items: hung processes, cost-guard warnings, deny-config leftovers, binary not found, stream without result, --trust flag)
2. Updated `CLAUDE.md` — added cursor/composer-2.5 to "Changing Models" section:
   - Added `cursor` to provider comment (`# or: gemini, codex, cursor`)
   - Added Cursor model values entry with `composer-*` prefix, default, and auth requirements
   - Updated effort values note to mention Cursor is ignored
   - Added `cursor` to `cli_paths` configuration example
3. All spike documentation includes failure paths per validation synthesis finding #2
4. Gate 3 includes explicit pass criteria per validation synthesis finding #1
5. `_windows.py` cross-platform clarification included per validation synthesis finding #4

### File List
- `docs/linux-deployment.md` [NEW] — Linux deployment guide with setup, spikes, gates, config, troubleshooting
- `CLAUDE.md` [MODIFIED] — Added cursor/composer-2.5 models and cli_paths.cursor documentation

## Change Log

| Date | Change |
|------|--------|
| 2026-06-13 | Created `docs/linux-deployment.md` with all 6 sections covering AC #1-#6 |
| 2026-06-13 | Updated `CLAUDE.md` "Changing Models" section with cursor provider documentation |
| 2026-06-13 | Updated `CLAUDE.md` Configuration section with `cli_paths.cursor` example |
| 2026-06-13 | Code review synthesis: applied 8 fixes (token math, known-paths, Python install, API key security, binary name note, S3→S1 dependency, deny-config cleanup clarity, CLAUDE.md overview). Status → in-progress per MAJOR REWORK verdict |

## Senior Developer Review (AI)

**Verdict:** MAJOR REWORK (Score: 4.8)
**Date:** 2026-06-13

### Applied Fixes
1. **S3 token math corrected** — `'x ' * 100000` = 200KB ≈ 50K tokens (was documented as 100K/400KB). Adjusted second test to `'x ' * 500000` for actual 250K token test.
2. **Known-paths list corrected** — removed erroneous `~/.npm-global/bin` from cursor resolution docs (actual: `~/.local/bin`, `/usr/local/bin` only).
3. **Python 3.11 install instructions added** — new subsection 1.5 with Ubuntu/Debian and RHEL/Fedora commands.
4. **API key security improved** — replaced `echo >>` with editor-based approach + security warning about bash history.
5. **Binary name inconsistency addressed** — added note at S5 to substitute `cursor-agent` for `agent` if applicable.
6. **S3→S1 dependency documented** — added prerequisite note with argv-based fallback if stdin fails.
7. **Deny-config cleanup clarified** — improved manual cleanup steps with explicit marker-first verification.
8. **CLAUDE.md overview updated** — added "+ Cursor CLI" to project overview line.

### Rejected Findings
- **R2-7 (`--force` flag docs):** FALSE POSITIVE — `--force` is an internal provider implementation detail, automatically handled; not relevant to deployment guide scope.
- **R2-8 (changes not committed):** Out of scope for code review — commit is a workflow step handled separately.

### Remaining Issues for Re-review
- Story file-to-git traceability (R1-5): sprint-status.yaml and story artifact not listed in File List — needs story file update during next implementation pass.
