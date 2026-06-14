# Story 11.6: Epic Documentation Sync

Status: in-progress

## Story

As a developer (human or AI),
I want project documentation to reflect everything built in Epic 11,
so that future implementation decisions are based on accurate information.

## Acceptance Criteria

1. **All Doc Audit Checklist items addressed:** Given all implementation stories (11.1–11.5) in Epic 11 are complete, when the documentation sync story executes, then every applicable item in the Doc Audit Checklist below is evaluated and addressed.

2. **`CLAUDE.md` provider list updated:** Given CursorProvider was added in Stories 11.2–11.4, when the audit runs, then the `providers/` section in the Architecture overview mentions CursorProvider.

3. **`CLAUDE.md` "Changing Models" section updated:** Given cursor/composer-2.5 is now a valid provider, when the audit runs, then the section includes cursor model values, default, and auth requirements. *(Note: Story 11.5 may have already addressed this — verify and fill gaps.)*

4. **`CLAUDE.md` config example updated:** Given `provider: cursor` and `cli_paths.cursor` are now valid config, when the audit runs, then the config example section includes these options.

5. **`CLAUDE.md` Key Patterns section updated:** Given Story 11.1 implemented SIGTERM→SIGKILL escalation, when the audit runs, then the Key Patterns section reflects the new escalation behavior.

6. **`project-context.md` updated with new conventions:** Given the epic introduced tolerant NDJSON parsing, write-mode predicate, deny-config marker protocol, and escalation behavior, when the audit runs, then these conventions are recorded in `_bmad-output/project-context.md`.

7. **Planning artifacts NOT modified:** Given `architecture.md`, `prd.md`, and `requirements-cursor-provider.md` are planning-phase artifacts, when the audit runs, then they are NOT updated — any needed changes are flagged as notes only.

## Doc Audit Checklist

### Tier 1: Core Docs (Always Evaluate)

**`CLAUDE.md`:**
- [x] Provider list mentions CursorProvider (`providers/` section in Architecture overview)
- [x] "Changing Models" section has cursor/composer-2.5 entry (valid models, default, auth note)
- [x] Config example includes `cursor` option and `cli_paths.cursor` override
- [x] Key Patterns section reflects SIGTERM→SIGKILL escalation

**`_bmad-output/project-context.md`:**
- [x] Provider ABC rule updated if implementor guidance changed (new provider count: 4)
- [x] New conventions recorded: write-mode predicate (`allowed_tools is None`), deny-config marker protocol, tolerant NDJSON parsing
- [x] Windows-native process management rule updated for SIGTERM→SIGKILL escalation behavior

### Tier 2: Reusable Library (Conditional)
- [x] Evaluate deny-config ownership-marker lifecycle as a reusable pattern worth documenting

## Tasks / Subtasks

- [x] Task 1: Audit `CLAUDE.md` against the checklist (AC: #2, #3, #4, #5)
  - [x] Read `CLAUDE.md` fully to determine current state (Story 11.5 already made some updates)
  - [x] Verify/add CursorProvider mention in Architecture overview / providers section
  - [x] Verify/add cursor/composer-2.5 in "Changing Models" section (models, default `composer-2.5`, `CURSOR_API_KEY` auth, `supports_model()` accepts `composer-*` prefix only)
  - [x] Verify/add `provider: cursor` and `cli_paths.cursor` in config example section
  - [x] Add SIGTERM→SIGKILL escalation to Key Patterns section: `terminate_process()` on Unix now sends SIGTERM → polls `is_pid_alive()` for up to `SIGTERM_GRACE_SECONDS` (5s) → sends SIGKILL if still alive. Constant in `providers/_windows.py`

- [x] Task 2: Audit `_bmad-output/project-context.md` against the checklist (AC: #6)
  - [x] Read `project-context.md` fully to determine current state
  - [x] Update provider count references (3 → 4 providers: claude, codex, cursor, gemini)
  - [x] Add new cursor-specific conventions:
    - Write-mode predicate: `allowed_tools is None` → write mode (single location, single predicate)
    - Deny-config marker protocol: `.cursor/cli.json` created atomically for read-only invocations, ownership tracked via `.bmad-assist-lite/cache/cursor-deny-config.marker`, crash recovery in `cleanup_for_phase()`
    - Tolerant NDJSON parsing: malformed lines logged at DEBUG and skipped, unknown event types silently ignored, result event (not exit code) determines success
  - [x] Update Windows-native process management rule: add SIGTERM→SIGKILL escalation behavior on Unix (`SIGTERM_GRACE_SECONDS = 5` in `_windows.py`, polling via `is_pid_alive()`, escalation to SIGKILL if process survives)

- [x] Task 3: Evaluate Tier 2 items (AC: #1)
  - [x] Assess whether deny-config ownership-marker lifecycle pattern is reusable enough to document as a standalone pattern in `project-context.md`. Record decision (yes/no with rationale) in this story's Completion Notes section regardless of outcome

- [x] Task 4: Verify planning artifacts are NOT modified (AC: #7)
  - [x] Confirm no changes to `architecture.md`, `prd.md`, or `requirements-cursor-provider.md`
  - [x] If any stale sections are found in planning artifacts, document them as notes in this story's completion notes (do NOT edit the files)

## Dev Notes

- **This is the standard epic-closing documentation sync story** — pattern established in Epics 8, 9, and 10
- **Audit method:** Read each target doc, cross-reference with the checklist, and update sections that are stale or missing post-Epic-11 information
- **Story 11.5 already updated `CLAUDE.md`** — it added cursor/composer-2.5 to "Changing Models", added `cli_paths.cursor`, and updated the overview line. Task 1 should verify these changes are accurate and complete, then focus on any gaps (e.g., Key Patterns section for SIGKILL escalation, provider list in Architecture overview)
- **Frozen Pydantic models:** Not applicable (documentation-only story)
- **Atomic writes:** Not applicable
- **Import style:** Not applicable
- **Type annotations:** Not applicable
- **Do NOT update planning artifacts:** `architecture.md`, `prd.md`, `requirements-cursor-provider.md` are owned by the planning phase. Flag any needed corrections as notes, don't edit

### What Changed in Epic 11 (Summary for Audit Reference)

| Story | What Changed | Files Touched |
|-------|-------------|---------------|
| 11.1 | SIGTERM→SIGKILL escalation; `SIGTERM_GRACE_SECONDS = 5` | `providers/_windows.py`, `tests/test_windows.py` (new) |
| 11.2 | Config `cli_paths.cursor` field; multi-binary name resolution (`cursor-agent` before `agent`); provider registry entry; CursorProvider stub | `core/config.py`, `providers/base.py`, `providers/__init__.py`, `providers/cursor.py` (new), `tests/test_cursor_resolution.py` (new) |
| 11.3 | Full CursorProvider: `_do_invoke()`, NDJSON dispatch, cost guard, mode split, `_cleanup()`, `parse_output()`, `supports_model()` | `providers/cursor.py` (rewrite), `tests/test_cursor_provider.py` (new), `tests/conftest.py` |
| 11.4 | Deny-config lifecycle: `.cursor/cli.json` create/cleanup, marker protocol, crash recovery sweep | `providers/cursor.py`, `loop/cleanup.py`, `tests/test_cursor_provider.py`, `tests/test_cleanup.py` |
| 11.5 | `docs/linux-deployment.md` (new), `CLAUDE.md` updates (cursor models, cli_paths, overview) | `docs/linux-deployment.md` (new), `CLAUDE.md` |

### Project Structure Notes

```
CLAUDE.md                              [TOUCH] Verify/complete Story 11.5 updates;
                                               add SIGKILL escalation to Key Patterns

_bmad-output/project-context.md        [TOUCH] Add cursor conventions, update provider
                                               count, update process management rule
```

No production code files modified. No test files modified.

### References

- **Epic file:** `_bmad-output/planning-artifacts/epic-11.md` — Story 11.6 section (Doc Audit Checklist)
- **Architecture:** `architecture.md` — Cursor extension sections (decisions D1–D14, patterns, structure)
- **Prior stories (all complete):**
  - Story 11.1: SIGKILL escalation — `_windows.py` changes, `SIGTERM_GRACE_SECONDS`
  - Story 11.2: Config + resolution + registry — `config.py`, `base.py`, `__init__.py`, `cursor.py` stub
  - Story 11.3: CursorProvider core — full `cursor.py`, test suite, conftest update
  - Story 11.4: Deny-config lifecycle — `cursor.py` additions, `cleanup.py` sweep
  - Story 11.5: Linux deployment docs + CLAUDE.md updates
- **Prior epic doc-sync patterns:** Stories 8.3, 9.3, 10.7

## Testing Requirements

- No automated tests — this is a documentation-only story
- **Manual validation:** Verify that all updated sections in `CLAUDE.md` and `_bmad-output/project-context.md` accurately reflect the implemented code
- **Cross-reference check:** Ensure any commands, config keys, or pattern descriptions mentioned in docs match the actual code as implemented in Stories 11.1–11.5

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/` | **PENDING** |
| Typecheck | `mypy src/` | **PENDING** |
| Tests | `pytest -q --tb=short --no-header` | **PENDING** |

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (claude-opus-4-20250514)

### Debug Log References
None — documentation-only story, no runtime issues encountered.

### Completion Notes List
1. **Task 1 (CLAUDE.md audit):** Story 11.5 had already updated the "Changing Models" section (cursor/composer-2.5 entry) and the config example (`cli_paths.cursor`). Two gaps were filled: (a) Added "Cursor" to the `providers/` subsystem description in the Architecture overview, with details on CursorProvider's NDJSON parsing, write-mode predicate, deny-config lifecycle, and result-event-based success determination. (b) Added SIGTERM→SIGKILL escalation behavior to the "Windows-native" Key Patterns entry.
2. **Task 2 (project-context.md audit):** Updated Provider ABC rule to mention 4 registered providers. Added 3 new cursor-specific convention entries: write-mode predicate, deny-config marker protocol, and tolerant NDJSON parsing. Updated Windows-native process management rule with SIGTERM→SIGKILL escalation details. Updated Last Updated date to 2026-06-13.
3. **Task 3 (Tier 2 evaluation):** Decision: **No** — The deny-config ownership-marker lifecycle is well-documented in the cursor-specific conventions added to `project-context.md` but is too tightly coupled to the provider invocation lifecycle to warrant extraction as a standalone reusable pattern. The atomic-write and crash-recovery pieces are already documented as general patterns.
4. **Task 4 (Planning artifacts):** Confirmed via `git diff` that `architecture.md`, `prd.md`, and `requirements-cursor-provider.md` have zero modifications. No stale sections requiring flagging were identified.

### File List
- `CLAUDE.md` — Updated providers/ subsystem description (added Cursor) and Key Patterns (added SIGTERM→SIGKILL escalation)
- `_bmad-output/project-context.md` — Updated provider count, added 3 cursor-specific conventions, updated process management rule, updated Last Updated date
- `_bmad-output/implementation-artifacts/11-6-epic-documentation-sync.md` — Story file updated with completion status
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — Updated story statuses for 11.1–11.5 (blocked → done), timestamps, and 11.6 status

## Senior Developer Review (AI)

**Date:** 2026-06-13
**Aggregate Evidence Score:** 4.5
**Verdict:** MAJOR REWORK
**Reviewers:** 2 (scores: 5.6, 3.5)

### Issues Found and Fixed

1. **🔴 CRITICAL — Sprint-status corruption (FIXED):** Stories 11.1–11.5 were set to `blocked` despite having committed code. Story 11.5 was regressed from `review` → `blocked`. All five stories corrected to `done`. Timestamps (`last_updated`) also fixed.

2. **🟠 IMPORTANT — Provider ABC rule inaccurate (FIXED):** `project-context.md` stated 4 abstract methods (`invoke()`, etc.) when the actual code has 5 (`_do_invoke()`, `_cleanup()`, etc.). Also stated 6 keyword args when `invoke()` has 7. Corrected.

3. **🟠 IMPORTANT — Provider examples exclude Cursor (FIXED):** `CLAUDE.md` Provider Implementor Reference listed `provider_name` examples as only `"claude"`, `"gemini"`, `"codex"`. Added `"cursor"`.

4. **🟡 MINOR — rule_count stale (FIXED):** `project-context.md` frontmatter had `rule_count: 56` but actual count is 66. Updated.

5. **🟡 MINOR — "Windows-native" heading misleading (FIXED):** Renamed to "Cross-platform process management" in both `CLAUDE.md` and `project-context.md` since the entry now describes Unix SIGTERM→SIGKILL escalation.

6. **🟡 MINOR — Sprint-status timestamps contradictory (FIXED):** `generated` was `2026-06-13` but `last_updated` was `2026-03-18`. Fixed `last_updated` to `2026-06-13`.

### Findings Rejected

1. **R2-F4 (Provider Implementor Reference not updated for cursor-era changes) — FALSE POSITIVE:** Section already lists all 5 required methods correctly. The `color_index` parameter is documented in the `invoke()` method signature in `base.py` and is a concrete method parameter, not something provider implementors need to know about.

2. **R2-F5 (providers/ description unwieldy) — DECLINED:** Subjective style issue. The format is consistent with other subsystem descriptions in the same section. No action taken.

3. **R2-F6 (Story file listed as modified but untracked) — DECLINED:** Cosmetic issue about the story file referencing itself. No impact on deliverables.

4. **R1-F2 (sprint-status.yaml omitted from File List) — FIXED IMPLICITLY:** Added sprint-status.yaml to the File List as part of the fixes applied.

### Runtime Verification

Not applicable — documentation-only story. Only `.md` and `.yaml` files modified. No Python source or test files changed.
