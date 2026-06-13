---
stepsCompleted: []
inputDocuments:
  - 'architecture.md'
  - 'project-context.md'
  - 'requirements-cursor-provider.md'
---

# bmad-assist-lite-parallel-stories - Epic 11 Breakdown

## Epic 11: Cursor Provider — Composer 2.5 as Master LLM on Linux

**Epic ID:** Epic-11
**Created:** 2026-06-12
**Status:** Ready for Development
**Priority:** High
**Points:** 13
**Stories:** 6

### Overview

Add the Cursor CLI (`agent` binary) as a fourth provider so Cursor's Composer 2.5 model can serve as **master LLM** for the token-heavy phases (`dev_story`, `code_review_synthesis`) and optionally as a multi validator. Composer 2.5 is Cursor-exclusive (no public API) — the CLI subprocess is the only integration path. The CLI is unreliable on native Windows, so this epic also hardens the codebase for deployment on a dedicated Linux box: one real code fix (SIGTERM→SIGKILL escalation) plus deployment documentation with validation gates.

### Business Goal

Run dev loops at roughly 1/10th of Opus token cost without giving up implementation quality: Composer 2.5 scores 79.8% SWE-Bench Multilingual (Opus 4.7: 80.5%) at ~200 tok/s, priced $0.50/M input + $2.50/M output. Today `dev_story`, `code_review_synthesis`, and `fix_quality_gate` are Opus-only — the most expensive part of every epic run.

### Strategic Context

- Cursor CLI verified headless-capable (June 2026 research): `-p` print mode, `--output-format stream-json` NDJSON, `--model` selection, `CURSOR_API_KEY` auth
- Known upstream issues that shape the design: errors never emitted as JSON (stderr only), stream may end without terminal event, history of `-p` hangs, exit-code-1-after-success reports, and a **silent switch to `composer-2.5-fast` (6× cost) — staff-acknowledged, unfixed**
- Architecture extension (architecture.md, D1–D14) is READY FOR IMPLEMENTATION; three load-bearing assumptions verified against source during validation
- Windows remains fully supported for claude/gemini/codex; this epic must cause zero Windows regression (NFR1)
- Auth: Cursor Pro plan + API key available; key flows from package-root `.env`

### Dependencies

- None — additive provider extension; prior epics fully complete (epics run sequentially)

### Research Artifacts

- `_bmad-output/planning-artifacts/requirements-cursor-provider.md` — 15 FRs, 5 NFRs, spikes S1–S5
- `architecture.md` — Extension sections: decisions D1–D14, patterns, structure, validation
- Session memory: `reference_cursor_cli_research.md` — full CLI facts with citations (June 2026)

### Context7 Library Documentation

<!-- No external libraries needed — Cursor CLI is invoked via subprocess using stdlib only,
     same pattern as the Codex provider. Zero new Python dependencies. -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|
| — | — | — | — |

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Core Architectural Decisions (Cursor Provider Extension); Implementation Patterns & Consistency Rules (Cursor Provider Extension); Project Structure & Boundaries (Cursor Provider Extension) |
| `requirements-cursor-provider.md` | (full) |
| `project-context.md` | (full) |
| `prd.md` | (skip) |

### Recommended Story Order

1. 11-1-sigkill-escalation — independent platform fix, testable on Windows today, required by NFR4
2. 11-2-cursor-resolution-and-config — plumbing with no provider dependency; enables 11.3 to test end-to-end
3. 11-3-cursor-provider-core — the provider itself; depends on resolution + config
4. 11-4-readonly-mode-deny-config — layered on the working provider
5. 11-5-linux-deployment-docs — documents what 11.3/11.4 built; spike checklist for the box
6. 11-6-epic-documentation-sync — standard closing story

---

<!-- DEPENDENCY FORMAT: "Story N.M" or bare "N.M", comma-separated. [] = none.
     Stories 11.1 and 11.2 are independent and may run in parallel. -->

### Story 11.1: SIGTERM→SIGKILL Escalation in Unix Process Termination

**Story ID:** 11-1-sigkill-escalation
**Component:** `src/bmad_assist_lite/providers/_windows.py`
**Estimate:** Small
**Points:** 1
**Priority:** High
**Dependencies:** []

#### User Story

As a developer running bmad-assist-lite on Linux,
I want hung provider processes force-killed after a grace period,
So that a stuck `agent` CLI (or any provider subprocess) can never orphan a dev run.

#### Description

Implement the SIGTERM→SIGKILL escalation that `terminate_process()`'s docstring already promises but the code does not deliver (FR14, decision D12). The Cursor CLI has a documented history of headless hangs; SIGTERM alone is not sufficient on the platform we are migrating to.

#### Current State

`terminate_process()` (`providers/_windows.py:38–67`) Unix branch sends `os.killpg(pgid, signal.SIGTERM)` and returns `True` immediately. The docstring claims "killpg(pgid, SIGTERM) then SIGKILL after 5s" — the escalation does not exist. A process ignoring SIGTERM survives indefinitely.

#### Target State

```python
SIGTERM_GRACE_SECONDS = 5  # module-level constant

# Unix branch of terminate_process():
# 1. killpg(pgid, SIGTERM)
# 2. poll process liveness (is_pid_alive) in short intervals up to SIGTERM_GRACE_SECONDS
# 3. if still alive: killpg(pgid, SIGKILL)
# 4. ProcessLookupError during escalation = process died = success
```

Windows `taskkill` branch is untouched.

#### Acceptance Criteria

**Given** a Unix process group whose leader exits within the grace period after SIGTERM
**When** `terminate_process(pid)` is called
**Then** it returns `True` and SIGKILL is never sent

**Given** a Unix process that ignores SIGTERM
**When** `terminate_process(pid)` is called
**Then** after at most `SIGTERM_GRACE_SECONDS` (5s) `os.killpg(pgid, SIGKILL)` is sent
**And** the function returns `True`

**Given** the PID does not exist (already dead before signaling)
**When** `terminate_process(pid)` is called
**Then** it returns `False` (unchanged current behavior)

**Given** the process dies between SIGTERM and the SIGKILL check (`ProcessLookupError` mid-escalation)
**When** escalation logic runs
**Then** the death is treated as success and `True` is returned

**Given** the platform is Windows
**When** `terminate_process(pid)` is called
**Then** the `taskkill /F /T /PID` path behaves byte-identically to before this story (NFR1)

#### Technical Notes

- Constant `SIGTERM_GRACE_SECONDS = 5` lives in `_windows.py` next to its user (patterns section)
- Synchronous block ≤5s is acceptable — the Windows taskkill path already blocks up to 10s
- Tests mock `os.getpgid`/`os.killpg`/`os.kill` and patch `IS_WINDOWS = False`, so they run on any platform including the Windows dev machine; new test module `tests/test_windows.py`
- Poll with short sleeps (e.g. 0.1s) against `is_pid_alive()`; do not busy-wait

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Backend-only platform fix, no user-visible behavior

---

### Story 11.2: Cursor CLI Resolution & Config Schema

**Story ID:** 11-2-cursor-resolution-and-config
**Component:** `src/bmad_assist_lite/providers/base.py`, `src/bmad_assist_lite/core/config.py`
**Estimate:** Small
**Points:** 2
**Priority:** High
**Dependencies:** []

#### User Story

As a developer,
I want `provider: cursor` accepted in configuration and the Cursor CLI binary resolvable on disk,
So that the provider can be configured and located before (and independently of) its implementation.

#### Description

Integration plumbing (FR9, FR10; decisions D9, D10): teach `resolve_cli_path()` the Cursor binary names and extend the config models to accept `cursor` as a provider name in both master and multi roles.

#### Current State

`resolve_cli_path()` (`providers/base.py`) resolves claude/gemini/codex binaries via config override → `shutil.which()` → known platform install paths. Config models (`core/config.py`: `MasterProviderConfig`, `MultiProviderConfig`) do not accept `cursor` as a provider value; `providers.cli_paths` has no `cursor` key.

#### Target State

- `CURSOR_BINARY_NAMES = ("cursor-agent", "agent")` — tried in that order within every tier (`agent` is dangerously generic as a PATH name; `cursor-agent` is unambiguous)
- `resolve_cli_path("cursor")` follows the standard 3-tier order: `providers.cli_paths.cursor` → PATH → known locations (Linux `~/.local/bin` already present; Windows `%LOCALAPPDATA%\cursor-agent` added for completeness)
- Config accepts `provider: cursor` in `providers.master` and `providers.multi` entries

#### Acceptance Criteria

**Given** `providers.cli_paths.cursor` is set in config
**When** `resolve_cli_path("cursor")` is called
**Then** the configured path is returned without consulting PATH

**Given** no config override and both `cursor-agent` and `agent` exist on PATH
**When** `resolve_cli_path("cursor")` is called
**Then** `cursor-agent` is preferred over `agent`

**Given** no config override and no PATH hit, on Linux
**When** `resolve_cli_path("cursor")` is called
**Then** `~/.local/bin/cursor-agent` then `~/.local/bin/agent` are checked among known locations

**Given** a config with `master: {provider: cursor, model: composer-2.5}`
**When** the config is loaded and validated
**Then** validation passes and the singleton exposes the cursor master config

**Given** a config with an unknown provider name (e.g. `provider: cursorx`)
**When** the config is loaded
**Then** validation fails exactly as it does today (no loosening of validation)

#### Technical Notes

- Follow the exact mechanism used when codex was added (same files, same shape) — see `git log` for the codex config/resolution commits as reference
- On Windows the `.cmd`/`.exe`/bare suffix probing already exists; cursor names flow through it unchanged
- No provider class exists yet — provider lookup happens at runtime via the registry, so config acceptance is safely decoupled (registration lands in Story 11.3)

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Configuration plumbing only

---

### Story 11.3: CursorProvider Core — Invocation, Streaming, Errors

**Story ID:** 11-3-cursor-provider-core
**Component:** `src/bmad_assist_lite/providers/cursor.py`
**Estimate:** Large
**Points:** 5
**Priority:** High
**Dependencies:** Story 11.2

#### User Story

As a developer using bmad-assist-lite,
I want a CursorProvider that invokes `agent -p --output-format stream-json` as a subprocess and returns Composer 2.5's response,
So that Cursor models can run master phases (dev_story, code_review_synthesis) in dev loops.

#### Description

The core provider (FR1–FR3, FR5–FR8, FR11–FR13; decisions D1, D4–D9, D11): `CursorProvider(BaseProvider)` with subprocess + NDJSON streaming following the CodexProvider pattern, tolerant event dispatch, terminal-event-based success detection, the composer-2.5-fast cost guard, and registration as a built-in.

#### Current State

Three providers exist: `ClaudeSDKProvider` (SDK async), `GeminiProvider` (subprocess JSON streaming), `CodexProvider` (subprocess NDJSON + structured output). No Cursor provider. `providers/__init__.py` lazy-import map and built-ins dict know nothing of cursor.

#### Target State

New `cursor.py` module implementing:

- `provider_name` → `"cursor"`; `default_model` → `"composer-2.5"` (`DEFAULT_CURSOR_MODEL` constant)
- `supports_model()` — accept `composer-` prefixed models ONLY (no `auto`, no other vendors' models — D9)
- `_build_command(model, write_mode)` — the single place argv is constructed (patterns section):
  `[binary, "-p", "--output-format", "stream-json", "--model", model]` + `["--force", "--trust"]` when `write_mode`
  - Write mode predicate: `allowed_tools is None` (D2) — stated exactly once
  - Prompt passed as final argv element (FR13; Linux argv limit ~2MB; stdin delivery deferred to spike S1)
- `_do_invoke()`:
  - Resolve binary via `resolve_cli_path("cursor")` (Story 11.2)
  - `subprocess.Popen` with `stdout=PIPE, stderr=PIPE, stdin=DEVNULL` + `get_subprocess_kwargs()`; environment inherited (carries `CURSOR_API_KEY`)
  - Reader threads via `start_stream_reader_threads()`; NDJSON dispatch in ONE function by event `type`:
    - system init → capture `model` field; mismatch vs requested → `logger.warning("Cursor model mismatch: requested %s, got %s", ...)`; actual model recorded into `ProviderResult.model` (FR8, D6)
    - assistant message → text from `message.content[].text` → `collector.add(text)` (FR5)
    - tool call started/completed → `collector.add("")` (activity mark only — D4; prevents false grace-period denial during long tool runs)
    - result → final text from `result` field, `session_id` captured, completion flag set (FR6, D5)
  - Tolerant parsing: UTF-8 `errors="replace"`; malformed lines logged DEBUG and skipped; unknown event types ignored (patterns)
  - `process.wait(timeout=remaining)`; `TimeoutExpired` → raise `TimeoutError` for base-class grace machinery (FR12)
  - Success = result event received. Non-zero exit AFTER result event → log and ignore (known upstream quirk). No result event → `ProviderError` with the tail of stderr, same truncation convention as `codex.py` (FR7, D7)
  - `agent --version` run lazily once per process, cached in module-level var, logged INFO (D11)
- `parse_output(result)` — return response text; must be Evidence-Score-parseable when used as validator (FR11)
- `_cleanup()` — track `_current_process` + reader threads; kill via `terminate_process()`; join threads with timeout
- Registration: `providers/__init__.py` lazy-import map + built-ins dict gain `"cursor"`

#### Acceptance Criteria

**Given** a mocked `agent` subprocess emitting a valid stream (init → assistant → tool → result events)
**When** `CursorProvider().invoke(prompt, model="composer-2.5", timeout=300, cwd=path)` is called
**Then** it returns a `ProviderResult` with the result-event text in `stdout`, `session_id` in `provider_session_id`, and the init-event model in `model`

**Given** the init event reports `composer-2.5-fast` while `composer-2.5` was requested
**When** the stream is parsed
**Then** a WARNING naming both models is logged and `ProviderResult.model` records `composer-2.5-fast` (NFR3)

**Given** the stream contains malformed JSON lines and unknown event types
**When** parsing runs
**Then** no exception propagates; malformed lines are logged at DEBUG and skipped

**Given** the process exits non-zero AFTER a result event was received
**When** `_do_invoke()` finalizes
**Then** the invocation is treated as success and the exit code is logged

**Given** the stream ends with NO result event and the process exits non-zero
**When** `_do_invoke()` finalizes
**Then** `ProviderError` is raised carrying the tail of stderr

**Given** the subprocess exceeds the timeout
**When** `process.wait()` raises `TimeoutExpired`
**Then** `TimeoutError` is raised for base-class grace handling, and tool events received during streaming count as collector activity

**Given** `supports_model()` is called
**When** the model is `composer-2.5`, `composer-2.5-fast`, or `composer-1`
**Then** it returns `True`
**When** the model is `auto`, `gpt-5.3-codex`, or `claude-opus`
**Then** it returns `False`

**Given** `allowed_tools=None` (master phase)
**When** `_build_command()` constructs argv
**Then** `--force --trust` are present
**Given** `allowed_tools` is a restricted list
**Then** `--force` is absent

**Given** the provider registry initializes
**When** built-ins register
**Then** `"cursor"` maps to `CursorProvider` and config `provider: cursor` resolves to it

#### Technical Notes

- **Template:** `providers/codex.py` — same subprocess/reader-thread/cleanup skeleton; reuse `COMMON_TOOL_NAMES`, `get_subprocess_kwargs()`, `start_stream_reader_threads()`, `write_progress()` with `color_index`
- **Event shapes** (from research, June 2026): terminal event `{"type":"result","subtype":"success","is_error":false,"result":"<text>","session_id":"<uuid>", ...}`; init event carries `apiKeySource`, `cwd`, `model`, `permissionMode`; assistant text in `message.content[].text`
- `stdin=DEVNULL` — piped stdin triggers print-mode inference and has unknown prompt semantics (spike S1); avoid the ambiguity entirely
- Constants: `DEFAULT_CURSOR_MODEL`, `CURSOR_BINARY_NAMES` imported from where Story 11.2 placed them (or defined here per structure section)
- Tests in `tests/test_cursor_provider.py` mirroring `tests/test_codex_provider.py` class grouping; NDJSON fixtures as multi-line strings covering the three failure shapes (missing result event, malformed lines, model mismatch); NO live CLI invocation (NFR2)
- Read-only deny-config behavior is Story 11.4 — this story only implements the flag-level mode split in `_build_command()`

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Backend provider, no UI surface

---

### Story 11.4: Read-Only Mode & Deny-Config Lifecycle

**Story ID:** 11-4-readonly-mode-deny-config
**Component:** `src/bmad_assist_lite/providers/cursor.py`, `src/bmad_assist_lite/loop/cleanup.py`
**Estimate:** Medium
**Points:** 3
**Priority:** High
**Dependencies:** Story 11.3

#### User Story

As a developer running parallel multi-LLM code review,
I want Cursor validator invocations physically unable to write files or execute shell commands,
So that the multi-LLM safety constraint (read-only during parallel phases) holds even against a misbehaving model.

#### Description

The layered read-only enforcement (FR4; decision D3): omitting `--force` (Story 11.3) makes writes proposal-only, but shell execution without `--force` is unconfirmed upstream. This story adds the project-level deny-config with marker-gated lifecycle, the crash-recovery sweep, and the Codex-parity prompt restriction warning.

#### Current State

After Story 11.3, read-only invocations merely omit `--force`. No deny-config exists; `loop/cleanup.py` knows nothing about Cursor artifacts. A crash during a read-only invocation could leak state.

#### Target State

For read-only invocations (`allowed_tools` is a restricted list):

1. Create `<cwd>/.cursor/cli.json` with `{"permissions": {"deny": ["Write(**)", "Shell(**)"]}}` — **only if absent**, written atomically (temp + `os.replace`), content from a frozen module constant
2. Record the created path in `.bmad-assist-lite/cache/cursor-deny-config.marker` (ownership marker)
3. `_cleanup()` removes the deny file only when the marker confirms we created it, then removes the marker
4. Pre-existing user `.cursor/cli.json` → never touched, DEBUG log, fall back to remaining layers
5. Prompt restriction warning appended using the shared `COMMON_TOOL_NAMES` construction from `codex.py` (import, don't copy)
6. `loop/cleanup.py` resume sweep: if the marker exists, remove the referenced deny file and the marker (crash recovery)

#### Acceptance Criteria

**Given** a read-only invocation in a cwd with no `.cursor/cli.json`
**When** `_do_invoke()` prepares the subprocess
**Then** the deny-config is created atomically with Write/Shell deny rules and the marker records its path

**Given** the invocation completes (success, timeout, or exception)
**When** `_cleanup()` runs
**Then** the deny file and marker are both removed

**Given** a user-authored `.cursor/cli.json` already exists
**When** a read-only invocation runs
**Then** the file is not modified or deleted, a DEBUG message is logged, and the prompt restriction warning is still applied

**Given** the orchestrator crashed mid-invocation leaving deny file + marker behind
**When** the next run's resume cleanup executes
**Then** the orphaned deny file and marker are removed (and a write-mode master run is therefore unaffected)

**Given** a write-mode invocation (`allowed_tools=None`)
**When** `_do_invoke()` prepares the subprocess
**Then** no deny-config is created and any existing user file is untouched

**Given** multiple concurrent read-only validators in the same cwd
**When** they race on deny-config creation
**Then** the atomic create-if-absent semantics leave a single valid file and no validator fails

#### Technical Notes

- Marker lives in `.bmad-assist-lite/cache/` (NOT inside `.cursor/` — the CLI may rewrite its own directory); content = absolute path of the created deny file
- Deny rules survive `--force` upstream, so a leaked deny file would silently cripple subsequent master runs — this is why the sweep matters (cross-component dependency noted in D-impact analysis)
- Sweep follows the existing `*.tmp` cleanup pattern in `loop/cleanup.py`; add cases to `tests/test_cleanup.py`
- `.cursor/` directory may need creating (`mkdir(parents=True, exist_ok=True)`)

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Backend safety mechanism

---

### Story 11.5: Linux Deployment Documentation & Spike Checklist

**Story ID:** 11-5-linux-deployment-docs
**Component:** `docs/linux-deployment.md`
**Estimate:** Small
**Points:** 1
**Priority:** Medium
**Dependencies:** Story 11.3, Story 11.4

#### User Story

As a developer setting up the dedicated Linux box,
I want a step-by-step deployment guide with validation gates and the spike checklist,
So that the migration is reproducible and verified before real epics run on it.

#### Description

FR15 / decision D14: write `docs/linux-deployment.md` covering environment setup, the S1–S5 spike checklist with expected outcomes, and the validation gate sequence that must pass before the box is trusted.

#### Current State

No Linux deployment documentation exists. The Linux audit results and spike definitions live in planning artifacts only.

#### Target State

`docs/linux-deployment.md` with:

1. **Setup:** Cursor CLI install (`curl https://cursor.com/install -fsS | bash`, lands in `~/.local/bin`), PATH check, `CURSOR_API_KEY` placement in package-root `.env`, repo clone + `python3.11 -m venv .venv` + `pip install -e ".[dev]"`
2. **Spike checklist (run S5 first):**
   - S5: `agent --list-models` shows `composer-2.5` on the Pro key — gates the entire premise
   - S1: stdin prompt semantics (`cat prompt.md | agent -p`) — chooses future prompt delivery
   - S2: `--mode=ask` composition with `-p` — potential read-only simplification
   - S3: context window probe (256K vs 1M conflicting claims)
   - S4: auto-update behavior across a long run (`agent --version` drift)
3. **Validation gates (in order):** full `pytest` green → spikes → one complete story loop with `master: cursor` on a sample project → first real epic
4. **Config example:** `bmad-assist-lite.yaml` with cursor master + existing multi validators
5. **Troubleshooting:** hang symptoms (kill behavior now escalates per Story 11.1), cost-guard warnings in logs, deny-config leftovers

#### Acceptance Criteria

**Given** a fresh Linux box and this guide
**When** the steps are followed top to bottom
**Then** every command is copy-pasteable and ordering is unambiguous

**Given** spike S5 fails (composer-2.5 not visible to the key)
**When** the reader reaches the spike section
**Then** the documented outcome states the feature premise is blocked and names the fallback (revisit role/model before building further)

**Given** each spike S1–S4
**When** documented
**Then** each has: exact command(s), expected outcome, and which decision its result feeds (D1 prompt delivery, D3 read-only mechanism, context budgets, version pinning)

#### Technical Notes

- Source content: requirements doc "Open Questions / Spikes" table + architecture "Environment Initialization (Linux Box)" section
- Keep the doc operational (commands + checks), not architectural — link to architecture.md for the why

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Documentation only

---

### Story 11.6: Epic Documentation Sync

**Story ID:** 11-6-epic-documentation-sync
**Component:** `CLAUDE.md`, `_bmad-output/project-context.md`
**Estimate:** Small
**Points:** 1
**Priority:** High
**Dependencies:** Story 11.1, Story 11.2, Story 11.3, Story 11.4, Story 11.5

#### User Story

As a developer (human or AI),
I want project documentation to reflect everything built in Epic 11,
So that future implementation decisions are based on accurate information.

#### Description

Final story in every epic. Audit all changes introduced by the epic and update project documentation accordingly.

#### Current State

Documentation reflects three providers (claude/gemini/codex), SIGTERM-only Unix kill, and no Linux deployment story.

#### Target State

All documentation accurately reflects the post-Epic-11 state: four providers, escalating Unix termination, cursor config surface, Linux deployment guide.

#### Acceptance Criteria

**Given** all implementation stories in Epic 11 are complete
**When** the documentation sync story executes
**Then** every applicable item in the Doc Audit Checklist below is addressed

**Given** a Tier 1 doc has a stale section
**When** the audit identifies it
**Then** the section is updated with accurate information from the implemented code

#### Technical Notes

**Audit Method:** `git diff` against the epic's base branch to enumerate changed files; cross-reference the checklist.

**Do NOT update:** `architecture.md`, `prd.md`, `requirements-cursor-provider.md` — planning artifacts owned by the planning phase. Flag needed changes for course correction instead.

#### Doc Audit Checklist

##### Tier 1: Core Docs (Always Evaluate)

**`CLAUDE.md`:**
- [ ] Provider list mentions CursorProvider (`providers/` section in Architecture overview)
- [ ] "Changing Models" section gains cursor/composer-2.5 entry (valid models, default, auth note)
- [ ] Config example includes `cursor` option and `cli_paths.cursor` override
- [ ] Key Patterns section reflects SIGTERM→SIGKILL escalation

**`_bmad-output/project-context.md`:**
- [ ] Provider ABC rule updated if implementor guidance changed
- [ ] New conventions recorded: write-mode predicate, deny-config marker protocol, tolerant NDJSON parsing
- [ ] Windows-native process rule updated for escalation behavior

##### Tier 2: Reusable Library (Conditional)

- [ ] New reusable pattern worth documenting? (deny-config ownership-marker lifecycle is a candidate)

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Documentation-only changes

---

## Test Impact Summary

### Unit / Integration Tests

| Test File | Stories Affected | Changes |
|-----------|------------------|---------|
| `tests/test_windows.py` (new) | 11.1 | SIGTERM→SIGKILL escalation: grace-window exit, ignore-SIGTERM escalation, mid-escalation death, Windows path unchanged (mocked, platform-independent) |
| `tests/test_config.py` | 11.2 | `provider: cursor` accepted in master/multi; unknown providers still rejected; `cli_paths.cursor` |
| `tests/test_cursor_provider.py` (new) | 11.3, 11.4 | NDJSON dispatch (success + three failure shapes), mode predicate, cost guard, stderr-tail errors, deny-config lifecycle, supports_model matrix |
| `tests/test_cleanup.py` | 11.4 | Resume sweep removes orphaned deny-config via marker; ignores user-authored files |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 11.1–11.6 | None | — | — | Headless CLI tool; no UI surface anywhere in the epic |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] `ruff check src/` passes
- [ ] `mypy src/` passes (strict mode)
- [ ] `pytest -q` passes — full suite green on Windows (NFR1 regression gate)
- [ ] No live CLI invocations anywhere in the test suite (NFR2)
- [ ] Cost-guard warning verified present in logs for a mocked model-mismatch run (NFR3)
- [ ] Documentation sync story completed (Tier 1 docs verified current)
- [ ] `docs/linux-deployment.md` ready for the box (spikes S1–S5 documented with expected outcomes)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Upstream NDJSON contract drift (CLI auto-updates, no version pin) | Medium | Medium | Tolerant parsing (never crash on unknown events); version logged per process; tarball pinning documented as fallback |
| Silent `composer-2.5-fast` switch (6× cost, unfixed upstream) | Medium | High | Cost guard: init-event model check, WARNING log, recorded in `ProviderResult.model` |
| `-p` headless hang regression | Low | Medium | Hard timeout + grace machinery (inherited) + SIGKILL escalation (11.1) |
| Spike S5 fails — composer-2.5 not selectable on the Pro key | Low | High | S5 is the FIRST action on the box, before any reliance; fallback: revisit model/role |
| Deny-config leak crippling a later write-mode run | Low | Medium | Marker-gated `_cleanup()` + crash-recovery sweep (11.4); deny file never created over a user's file |
| Stories 11.3/11.4 developed on Windows without live CLI | Medium | Low | By design: mocked tests only; live behavior validated by the documented gate sequence on the box |

## Rollback Plan

The epic is additive behind the provider boundary. To revert: remove `"cursor"` from the registry map and config validation (Stories 11.2/11.3 commits), delete `providers/cursor.py` and its tests — existing providers are untouched by construction. The Story 11.1 escalation can be reverted independently (single function, single constant) restoring SIGTERM-only behavior. The deny-config sweep in `loop/cleanup.py` is a no-op when no marker exists, so it can remain harmlessly. Each story lands as its own commit via the standard quality-gate auto-commit, so `git revert` granularity matches story granularity.
