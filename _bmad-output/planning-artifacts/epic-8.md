---
stepsCompleted: []
inputDocuments:
  - 'architecture.md'
  - 'project-context.md'
---

# bmad-assist-lite-parallel-stories - Epic 8 Breakdown

## Epic 8: Context Requirements Validation & Compiler Hardening

**Epic ID:** Epic-8
**Created:** 2026-03-21
**Status:** Draft
**Priority:** High
**Points:** 5
**Stories:** 3

### Overview

Upgrade the compiler's context filter to enforce Context Requirements references at story creation time. Missing architecture sections or planning documents currently produce silent warnings, allowing stories to be created with degraded context. This epic makes missing non-optional references a hard error, adds an `(optional)` convention for nice-to-have references, and improves error messaging to guide resolution.

### Business Goal

Prevent story quality degradation by ensuring all referenced architectural context is actually available when stories are created. Missing context leads to incomplete stories, which cascade into implementation gaps and code review issues.

### Strategic Context

- Discovered during epic-5 story creation: 4 architecture sections and 2 documents referenced but missing
- Root cause: architecture.md wasn't updated when new epics introduced new subsystems
- Epic template now includes a pre-finalization checklist, but compiler enforcement is the safety net
- Aligns with "fail fast" principle — catch context gaps at compilation, not during implementation

### Dependencies

- None — this modifies compiler internals only, independent of parallel execution epics

### Context7 Library Documentation

<!-- No external libraries needed -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Existing System Architecture; Implementation Patterns & Consistency Rules; Enforcement Guidelines |
| `project-context.md` | `(full)` `(optional)` |

<!-- PRE-FINALIZATION CHECKLIST:
     [x] architecture.md > Existing System Architecture - exists (line 63)
     [x] architecture.md > Implementation Patterns & Consistency Rules - exists (line 345)
     [x] architecture.md > Enforcement Guidelines - exists (line 478)
     [x] project-context.md - exists at _bmad-output/project-context.md, marked optional
-->

### Recommended Story Order

1. 8-1-context-filter-error-on-missing-sections - Core change: upgrade WARNING to ERROR with actionable messages
2. 8-2-optional-reference-convention - Adds `(optional)` marker support so non-critical references can remain warnings
3. 8-3-epic-documentation-sync - Update project docs to reflect new compiler behavior

---

### Story 8.1: Context Filter Error on Missing Sections

**Story ID:** 8-1-context-filter-error-on-missing-sections
**Component:** `src/bmad_assist_lite/compiler/context_filter.py`
**Estimate:** Medium
**Points:** 2
**Priority:** High
**Dependencies:** []

#### User Story

As a developer using bmad-assist-lite,
I want story creation to fail when referenced architecture sections don't exist,
So that I never create stories with missing context that leads to implementation gaps.

#### Description

Modify the context filter in `compiler/context_filter.py` to raise `CompilerError` instead of logging a warning when a Context Requirements section or document is not found in discovered files. The error message must be actionable: list all missing references and suggest how to fix them.

#### Current State

`context_filter.py` logs `WARNING` level messages when sections or documents are not found:
```python
logger.warning("Context Requirements: section '%s' not found in '%s'", ...)
logger.warning("Context Requirements: document '%s' not found in discovered files", ...)
```
Story creation continues with degraded context.

#### Target State

- Missing non-optional sections raise `CompilerError` with an actionable message
- Error message lists ALL missing references (not just the first one)
- Error message includes fix instructions: "Update the document with these sections, or mark them (optional) in the epic file"
- Example error output:
  ```
  CompilerError: Epic 5 Context Requirements reference missing sections:

    architecture.md:
      - Crash Recovery
      - Blocked Story Handling

    Discovered files missing:
      - project-context.md

  Fix: Add missing sections to the referenced documents,
       or mark non-critical references as (optional) in the epic file.
  ```

#### Acceptance Criteria

**Given** an epic Context Requirements table references section "Crash Recovery" in architecture.md
**When** architecture.md does not contain an H2 or H3 header matching "Crash Recovery"
**Then** the compiler raises `CompilerError` with the missing section name and file
**And** story creation is blocked

**Given** an epic references document `prd.md` in Context Requirements
**When** `prd.md` is not found in the workflow's discovered files
**Then** the compiler raises `CompilerError` listing the missing document

**Given** an epic has multiple missing sections across multiple documents
**When** the compiler validates Context Requirements
**Then** ALL missing references are collected and reported in a single error (not one-at-a-time)

#### Technical Notes

- The context filter logic is in `compiler/context_filter.py` around line 401-408
- Collect all missing references first, then raise a single `CompilerError` with the full list
- The `_build_filename_to_key_map()` function maps filenames to discovered file keys — missing documents won't appear in this map
- Section matching uses `extract_section()` from `discovery.py` which raises `CompilerError` on missing sections — currently caught and logged as warning

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Compiler-internal change, no user-facing behavior affected

---

### Story 8.2: Optional Reference Convention

**Story ID:** 8-2-optional-reference-convention
**Component:** `src/bmad_assist_lite/compiler/context_filter.py`
**Estimate:** Small
**Points:** 1
**Priority:** High
**Dependencies:** [8-1-context-filter-error-on-missing-sections]

#### User Story

As an epic author,
I want to mark certain Context Requirements references as optional,
So that nice-to-have context doesn't block story creation when unavailable.

#### Description

Add support for an `(optional)` marker in the Context Requirements table. When a section or document is marked `(optional)`, missing references produce a WARNING (current behavior) instead of an ERROR (new default from story 8.1). This allows epics to declare "I'd like this context if available, but can proceed without it."

#### Current State

After story 8.1, all missing references are hard errors. No way to distinguish critical vs. nice-to-have references.

#### Target State

- `(optional)` marker recognized in the Sections to Load column
- Supported formats:
  - `(full) (optional)` — load full document if available, warn if missing
  - `Section Name (optional)` — load section if available, warn if missing
  - `Section A; Section B (optional); Section C` — only Section B is optional
- Missing optional references log WARNING (not ERROR)
- Missing non-optional references still raise CompilerError (from story 8.1)
- The `(optional)` text is stripped before section name matching

#### Acceptance Criteria

**Given** an epic Context Requirements table has `project-context.md` with `(full) (optional)`
**When** `project-context.md` does not exist in discovered files
**Then** a WARNING is logged (not an error) and story creation continues

**Given** a row has `Crash Recovery; State Persistence (optional); Blocked Story Handling`
**When** "State Persistence" section is missing from architecture.md
**Then** a WARNING is logged for "State Persistence" only
**And** missing "Crash Recovery" or "Blocked Story Handling" would raise CompilerError

**Given** all non-optional references are present and optional ones are missing
**When** the compiler validates Context Requirements
**Then** story creation proceeds with warnings for missing optional refs

#### Technical Notes

- Parse `(optional)` marker during section name splitting (semicolon-separated)
- Strip `(optional)` before passing to section matcher
- For document-level optional: check the full cell text for `(optional)`
- Maintain backward compatibility: no `(optional)` marker = required (ERROR on missing)
- Update the epic template comment to document the convention (already done)

#### E2E Impact

- **E2E Action:** None
- **Affected Spec:** N/A
- **data-testid Changes:** None
- **Rationale:** Compiler-internal change, no user-facing behavior affected

---

### Story 8.3: Epic Documentation Sync

**Story ID:** 8-3-epic-documentation-sync
**Component:** `docs/`, `CLAUDE.md`, `_bmad-output/project-context.md`
**Estimate:** Small
**Points:** 1
**Priority:** High
**Dependencies:** [8-1-context-filter-error-on-missing-sections, 8-2-optional-reference-convention]

#### User Story

As a developer (human or AI),
I want project documentation to reflect the compiler hardening from Epic 8,
So that future implementation decisions are based on accurate information.

#### Description

Final story in every epic. Audit all changes introduced by the epic and update project documentation accordingly.

#### Current State

Documentation reflects the project state before Epic 8 began.

#### Target State

All documentation accurately reflects the project state after Epic 8 completion.

#### Acceptance Criteria

**Given** all implementation stories in Epic 8 are complete
**When** the documentation sync story executes
**Then** every applicable item in the Doc Audit Checklist is addressed

**Given** a Tier 1 doc has a stale section
**When** the audit identifies it
**Then** the section is updated with accurate information from the implemented code

#### Technical Notes

**Audit Method:** Run `git diff main...HEAD --name-only` to identify all files changed in this epic.

**Do NOT update:** `architecture.md`, `prd.md`. These are planning artifacts owned by the planning phase.

#### Doc Audit Checklist

##### Tier 1: Core Docs (Always Evaluate)

**`CLAUDE.md`:**
- [ ] Document new compiler behavior: missing Context Requirements sections now ERROR by default
- [ ] Document `(optional)` convention for Context Requirements
- [ ] Update compiler subsystem description if needed

**`_bmad-output/project-context.md`:**
- [ ] Add rule about Context Requirements validation (error vs warning)
- [ ] Document `(optional)` convention as a code convention

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
| `tests/test_context_filter.py` | 8-1, 8-2 | New tests for error on missing sections, optional marker parsing |

### E2E Test Impact

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| 8.1 | None | -- | -- | Compiler-internal |
| 8.2 | None | -- | -- | Compiler-internal |
| 8.3 | None | -- | -- | Documentation only |

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests updated and passing (`pytest -q --tb=short --no-header`)
- [ ] `ruff check src/ && mypy src/` passes
- [ ] Documentation sync story completed (Tier 1 core docs verified current)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Existing epics break due to new errors | Medium | Medium | Update all existing epic Context Requirements before enabling |
| Optional marker parsing edge cases | Low | Low | Comprehensive unit tests for all format variations |

## Rollback Plan

Revert the `context_filter.py` changes to restore WARNING behavior. No data migration needed — this is a compile-time check only.
