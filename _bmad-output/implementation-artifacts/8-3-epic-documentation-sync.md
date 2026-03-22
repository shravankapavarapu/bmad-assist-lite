# Story 8.3: Epic Documentation Sync

Status: done

## Story

As a developer (human or AI),
I want project documentation to reflect the compiler hardening from Epic 8,
so that future implementation decisions are based on accurate information about Context Requirements validation behavior.

## Acceptance Criteria

1. **Given** all implementation stories in Epic 8 are complete, **When** the documentation sync story executes, **Then** every applicable item in the Doc Audit Checklist is addressed.
2. **Given** `CLAUDE.md` describes the compiler subsystem, **When** the audit identifies it, **Then** the section is updated to document that missing Context Requirements sections now raise `CompilerError` by default (not warnings).
3. **Given** `CLAUDE.md` describes workflow compilation, **When** the audit identifies it, **Then** the `(optional)` convention for Context Requirements references is documented.
4. **Given** `_bmad-output/project-context.md` contains critical implementation rules, **When** the audit identifies stale content, **Then** a rule about Context Requirements validation (error vs warning) and the `(optional)` convention are added.

## Tasks / Subtasks

- [x] Task 1: Audit changes introduced by Epic 8 (AC: #1)
  - [x] 1.1 Run `git diff main...HEAD --name-only` to identify all files changed in this epic (per epic Technical Notes audit method)
  - [x] 1.2 Review Story 8.1 file list and completion notes to identify all behavioral changes: `CompilerError` on missing Context Requirements sections, batch error reporting, skip-directive exemption
  - [x] 1.3 Review Story 8.2 file list and completion notes to identify all behavioral changes: `(optional)` marker support at document-level and per-section level, `ContextRequirement.optional` and `optional_sections` fields, warning-only for optional missing refs
  - [x] 1.4 Cross-reference `git diff` output with story completion notes to identify which Tier 1 docs have stale sections

- [x] Task 2: Update `CLAUDE.md` (AC: #2, #3)
  - [x] 2.1 In the "Workflow Compilation Pipeline" or "Compiler" description under Core Subsystems, add that `context_filter.py` now validates Context Requirements references — missing non-optional sections/documents raise `CompilerError` at compilation time (not silent warnings)
  - [x] 2.2 Document the `(optional)` convention: epic Context Requirements entries can include `(optional)` marker (document-level: `(full) (optional)`; per-section: `Section Name (optional)`) to allow missing refs to produce warnings instead of errors
  - [x] 2.3 Ensure the compiler subsystem bullet accurately reflects the current context filter behavior

- [x] Task 3: Update `_bmad-output/project-context.md` (AC: #4)
  - [x] 3.1 Add a rule under "Framework-Specific Rules" about Context Requirements validation: the compiler's `apply_context_filter()` raises `CompilerError` for missing non-optional references, collecting all missing items into a single error. Skip-directive on missing documents is silently ignored. References marked `(optional)` produce warnings only.
  - [x] 3.2 Document the `(optional)` convention as a code convention — mention it applies to epic file Context Requirements tables and is parsed case-insensitively
  - [x] 3.3 Update the `Last Updated` date (and frontmatter `date` field) and increment `rule_count` in the frontmatter by the number of rules added (expected: +2, one for Context Requirements validation, one for `(optional)` convention)

- [x] Task 4: Verify no other Tier 1 docs need updates (AC: #1)
  - [x] 4.1 Confirm `architecture.md` and `prd.md` are NOT updated (planning artifacts owned by planning phase, per epic file Technical Notes)
  - [x] 4.2 Confirm no other Tier 1 docs exist that reference compiler behavior or Context Requirements
  - [x] 4.3 Verify consistency between updated `CLAUDE.md` and `project-context.md` — no contradictions in described behavior

## Dev Notes

### Architecture Patterns and Constraints

- **Do NOT update `architecture.md` or `prd.md`** — These are planning artifacts owned by the planning phase, per the epic file's Technical Notes section.
- **Documentation-only story** — No production code changes. No test changes. Only markdown file updates.
- **Atomic file writes not needed** — These are documentation files, not state/config files. Standard file writes are appropriate.
- **Line length 100** — Still applies to any code blocks within documentation, though markdown prose is not strictly length-enforced.

### What Epic 8 Changed (Summary for Doc Updates)

**Story 8.1 — Context Filter Error on Missing Sections:**
- `apply_context_filter()` in `compiler/context_filter.py` now raises `CompilerError` instead of logging warnings when Context Requirements references cannot be resolved
- Missing documents and missing sections are collected into accumulators and reported in a single `CompilerError` with all missing items grouped by category
- Error message includes actionable fix instructions
- **Exception:** Documents referenced with `(skip)` directive that are missing do NOT raise an error (the user's intent — exclude the document — is already satisfied)
- Import added: `CompilerError` from `bmad_assist_lite.core.exceptions`

**Story 8.2 — Optional Reference Convention:**
- `ContextRequirement` frozen dataclass extended with `optional: bool` (default `False`) and `optional_sections: frozenset[int]` fields
- `parse_context_requirements()` detects and strips `(optional)` markers (case-insensitive) at two levels:
  - Document-level: `(full) (optional)` or `(skip) (optional)` — sets `req.optional = True`
  - Per-section: `Section A; Section B (optional); Section C` — records index in `req.optional_sections`
- `apply_context_filter()` logs `logger.warning()` for optional missing refs instead of accumulating them into the `CompilerError`
- Non-optional missing refs still raise `CompilerError` as before (backward compatible)

### Source Tree Components to Touch

1. **`CLAUDE.md`** — Update compiler subsystem description and/or add Context Requirements validation behavior
2. **`_bmad-output/project-context.md`** — Add rules about Context Requirements validation and `(optional)` convention

### Project Structure Notes

```
CLAUDE.md                                    ← Update: compiler subsystem description
_bmad-output/
  project-context.md                         ← Update: add Context Requirements rules
  planning-artifacts/
    architecture.md                          ← DO NOT UPDATE
    prd.md                                   ← DO NOT UPDATE
  implementation-artifacts/
    8-1-context-filter-error-on-missing-sections.md  ← Reference only
    8-2-optional-reference-convention.md              ← Reference only
    8-3-epic-documentation-sync.md                   ← This file
    sprint-status.yaml                                ← Updated by workflow
```

### References

- Epic file: Story 8.3 definition with Doc Audit Checklist
- Story 8.1: `8-1-context-filter-error-on-missing-sections.md` — Completion notes and file list
- Story 8.2: `8-2-optional-reference-convention.md` — Completion notes and file list
- `CLAUDE.md` — Current state of project documentation
- `_bmad-output/project-context.md` — Current state of AI agent rules

## Testing Requirements

- No automated tests — this is a documentation-only story
- Manual verification: read updated docs to confirm they accurately reflect the implemented behavior from Stories 8.1 and 8.2
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

> Note: This is a documentation-only story — no production code or test files were modified. Quality gates are not applicable but should be run as regression checks before epic close.

## Dev Agent Record

### Agent Model Used
Claude Opus 4 (claude-opus-4-20250514)

### Debug Log References
N/A — no issues encountered. Documentation-only story with no code execution.

### Completion Notes List
- **Task 1 (Audit):** Ran `git diff main...HEAD --name-only` (120+ files changed on feature branch). Reviewed Story 8.1 and 8.2 completion notes and file lists. Cross-referenced git diff output with story notes to identify stale Tier 1 docs: `CLAUDE.md` (compiler subsystem description missing context filter validation) and `project-context.md` (no rules about Context Requirements validation or `(optional)` convention).
- **Task 2 (CLAUDE.md):** Updated the `compiler/` bullet under Core Subsystems (line 31) to document: (a) `context_filter.py` validates Context Requirements at compilation time, (b) missing non-optional refs raise `CompilerError` with batch reporting, (c) skip-directive exception for missing documents, (d) `(optional)` marker convention at document-level and per-section level.
- **Task 3 (project-context.md):** Added two new rules under Framework-Specific Rules: "Context Requirements validation" (rule about `CompilerError` for missing refs, batch reporting, skip-directive, optional warnings) and "`(optional)` convention for Context Requirements" (two-level marker support, case-insensitive parsing, section name stripping). Updated frontmatter `date` to `2026-03-22`, `rule_count` from 54 to 56, and `Last Updated` to `2026-03-22`.
- **Task 4 (Verification):** Confirmed `architecture.md` and `prd.md` were NOT modified (planning artifacts per epic Technical Notes). Verified no other Tier 1 docs reference compiler behavior or Context Requirements. Performed cross-doc consistency check: all 6 behavioral aspects are consistently described across CLAUDE.md and project-context.md with no contradictions.

### File List
- `CLAUDE.md` — Modified: updated compiler subsystem bullet (line 31) with Context Requirements validation and `(optional)` convention
- `_bmad-output/project-context.md` — Modified: added 2 rules under Framework-Specific Rules, updated frontmatter `date`/`rule_count`, updated `Last Updated`
- `_bmad-output/implementation-artifacts/8-3-epic-documentation-sync.md` — Updated: status, task checkboxes, quality gates, dev agent record

## Senior Developer Review (AI)

**Verdict:** APPROVE (Evidence Score: 1.7)
**Date:** 2026-03-22

### Applied Fixes
1. **CLAUDE.md compiler bullet restructured** — Split monolithic 4-sentence bullet (line 31) into a main bullet + 2 sub-bullets for readability. Added trailing period. Added brief `(skip)` directive explanation. Both reviewers flagged this as IMPORTANT.

### Rejected Findings
- **README.md as missed Tier 1 doc** (R1-IMPORTANT): FALSE POSITIVE. README.md's `compiler/` reference is a directory listing in the Project Structure section, not a behavioral description requiring Epic 8 updates.
- **Information density asymmetry** (R2-IMPORTANT): FALSE POSITIVE. `CLAUDE.md` is intentionally a quick-reference overview; `project-context.md` provides field-level details. This asymmetry is consistent with how all other subsystems are documented across both files.
- **Sprint-status `in-progress` vs story `review`** (R2-MINOR): FALSE POSITIVE. sprint-status.yaml shows `review` on line 107, matching the story file. Reviewer misread.
- **"5 commands" stale** (R2-MINOR): Out of scope — pre-existing staleness from before Epic 8. Noted for future housekeeping.
- **Missing module paths in project-context.md** (R1-MINOR): The rule on line 51 already references `context_filter.py`, providing sufficient context.
- **sprint-status.yaml not in File List** (R2-MINOR): Workflow-managed file, not a dev agent deliverable.

### Runtime Verification
- **Lint:** N/A (documentation-only, no Python changes)
- **Typecheck:** N/A (documentation-only, no Python changes)
- **Tests:** N/A (documentation-only, no Python changes)
- Note: Regression verification blocked by sandbox. Only markdown files were modified; no impact on Python tooling.
