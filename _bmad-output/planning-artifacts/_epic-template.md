---
stepsCompleted: []
inputDocuments: []
---

# {{project_name}} - Epic {{N}} Breakdown

## Epic {{N}}: {{epic_title}}

**Epic ID:** Epic-{{N}}
**Created:** {{date}}
**Status:** Draft | Ready for Development | In Progress | Done
**Priority:** High | Medium | Low
**Points:** {{total_points}}
**Stories:** {{story_count}}

### Overview

{{Brief description of what this epic accomplishes and why it matters.}}

### Business Goal

{{Clear statement of the business outcome this epic delivers.}}

### Strategic Context

- {{How this epic fits into the broader product strategy}}
- {{Key business drivers or user needs}}

### Dependencies

- {{List any prerequisite epics, completed work, or external dependencies}}

### Context7 Library Documentation

<!-- List external libraries that need up-to-date documentation during development.
     Resolve each library name via Context7's resolve-library-id tool to get the exact ID.
     Only include libraries that developers will actively use, mock, or integrate with.
     Do NOT include transitive dependencies (e.g., Radix UI when using shadcn/ui wrappers). -->

| Library | Context7 ID | Query Focus | Stories |
|---------|-------------|-------------|---------|
| {{library_name}} | {{/org/project}} | {{what to query for, e.g., "mocking database queries"}} | {{story IDs that need this}} |

<!-- Example for a testing epic:
| Vitest | /vitest-dev/vitest | vi.mock patterns, test lifecycle hooks | 6-1, 6-2, 6-3, 6-4 |
| Testing Library React | /testing-library/react-testing-library | render, screen, fireEvent, async utilities | 6-2, 6-3 |
| Playwright | /microsoft/playwright | page locators, assertions, test fixtures | 6-5, 6-6 |
| Drizzle ORM | /drizzle-team/drizzle-orm | query builder API, select/insert/update/delete | 6-1, 6-4 |
-->

### Context Requirements

<!-- Declare which sections from planning documents are needed for story creation.
     This controls what context the automated story-creation pipeline loads,
     reducing prompt size from ~56K tokens (full docs) to ~16K tokens (relevant sections only).

     CONVENTIONS:
     - Section Names: Use exact H2/H3 header text from the source document, semicolon-separated
     - (full):  Load the entire document (use for small docs like project-context.md)
     - (skip):  Do not load this document at all
     - Headers are a public API: renaming a header in a planning doc requires updating all
       referencing epics. See architecture.md "Document Header Stability Contract" section.
-->

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | {{Section Name 1; Section Name 2; Section Name 3}} |
| `prd.md` | {{Section Name 1; Section Name 2}} |
| `ux-design-specification.md` | {{Section Name 1; Section Name 2}} or `(skip)` |
| `project-context.md` | `(full)` or `(optional)` |

<!-- CONVENTIONS:
     - (optional): Document/section is nice-to-have but not required for story creation.
       Missing optional references produce a WARNING. Missing non-optional references produce an ERROR
       that blocks story creation until resolved.
-->

<!-- PRE-FINALIZATION CHECKLIST (Architect must complete before epic is ready):
     1. For each row in Context Requirements table above:
        - Open the referenced document
        - Verify every semicolon-separated section name exists as an H2 or H3 header
     2. If sections are MISSING:
        - Add the section to the referenced document with architectural content
        - Do NOT use placeholder/stub text - write real architectural decisions
     3. If a section name doesn't match exactly, update THIS table to match the actual header
     4. Validation: The compiler will ERROR on missing non-optional sections at story creation time
-->

<!-- Example for a booking/conversion epic:
| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Starter Template Evaluation; Third-Party Integration (Cal.com); Analytics Integration; Performance Optimization |
| `prd.md` | Executive Summary; Functional Requirements |
| `ux-design-specification.md` | (skip) |
| `project-context.md` | (full) |
-->

### Recommended Story Order

1. {{N}}-1-{{kebab-title}} - {{reason for ordering}}
2. {{N}}-2-{{kebab-title}} - {{reason for ordering}}

---

### Story {{N}}.1: {{story_title}}

**Story ID:** {{N}}-1-{{kebab-title}}
**Component:** `{{primary file or module path}}`
**Estimate:** Small | Medium | Large
**Points:** {{points}}
**Priority:** High | Medium | Low
**Dependencies:** []

#### User Story

As a {{user_type}},
I want {{capability}},
So that {{value_benefit}}.

#### Description

{{What this story accomplishes. Be specific about the change.}}

#### Current State

{{Describe or show the current behavior/code that will change. Include code snippets if applicable.}}

#### Target State

{{Describe or show the desired behavior/code after implementation. Include code snippets if applicable.}}

#### Acceptance Criteria

**Given** {{precondition}}
**When** {{action}}
**Then** {{expected_outcome}}

**Given** {{precondition}}
**When** {{action}}
**Then** {{expected_outcome}}

#### Technical Notes

{{Implementation hints, interface changes, file paths, gotchas, libraries to use.}}

#### E2E Impact

<!-- Evaluate using: Does this story change user-visible behavior on an existing page,
     add a new page/route, or modify an interactive element (form, nav, modal)?
     Pick ONE: None | Create | Modify | Visual Only -->

- **E2E Action:** None | Create | Modify | Visual Only
- **Affected Spec:** `{{tests/e2e/feature/feature.spec.ts}}` or "New spec needed"
- **data-testid Changes:** {{List new data-testids to add, or "None"}}
- **Rationale:** {{Why E2E is or isn't needed. e.g., "New form adds interactive elements requiring functional E2E coverage" or "Backend-only change, no user-visible behavior affected"}}

<!-- E2E Action guide:
     None         → Backend-only, internal refactor, no user-visible change
     Create       → New page/route, new user flow, new interactive feature
     Modify       → Changed behavior on existing page (updated flow, removed feature)
     Visual Only  → Cosmetic/styling change that needs visual regression screenshot update -->

---

### Story {{N}}.2: {{story_title}}

**Story ID:** {{N}}-2-{{kebab-title}}
**Component:** `{{primary file or module path}}`
**Estimate:** Small | Medium | Large
**Points:** {{points}}
**Priority:** High | Medium | Low
**Dependencies:** []

#### User Story

As a {{user_type}},
I want {{capability}},
So that {{value_benefit}}.

#### Description

{{What this story accomplishes.}}

#### Current State

{{Current behavior/code.}}

#### Target State

{{Desired behavior/code.}}

#### Acceptance Criteria

**Given** {{precondition}}
**When** {{action}}
**Then** {{expected_outcome}}

#### Technical Notes

{{Implementation details.}}

#### E2E Impact

- **E2E Action:** None | Create | Modify | Visual Only
- **Affected Spec:** `{{tests/e2e/feature/feature.spec.ts}}` or "New spec needed"
- **data-testid Changes:** {{List new data-testids to add, or "None"}}
- **Rationale:** {{Why E2E is or isn't needed}}

---

<!-- Copy the Story block above for additional stories (Story N.3, N.4, etc.) -->

---

### Story {{N}}.{{LAST}}: Epic Documentation Sync

**Story ID:** {{N}}-{{LAST}}-epic-documentation-sync
**Component:** `docs/`, `CLAUDE.md`, `docs/project-context.md`
**Estimate:** Small
**Points:** 1
**Priority:** High
**Dependencies:** [All prior stories in this epic]

#### User Story

As a developer (human or AI),
I want project documentation to reflect everything built in Epic {{N}},
So that future implementation decisions are based on accurate information.

#### Description

Final story in every epic. Audit all changes introduced by the epic and update project documentation accordingly. Uses a two-tier system: Tier 1 (core docs) is always evaluated; Tier 2 (reusable library) is conditional based on what the epic introduced.

#### Current State

Documentation reflects the project state before Epic {{N}} began.

#### Target State

All documentation accurately reflects the project state after Epic {{N}} completion.

#### Acceptance Criteria

**Given** all implementation stories in Epic {{N}} are complete
**When** the documentation sync story executes
**Then** every applicable item in the Doc Audit Checklist is addressed

**Given** a Tier 1 doc has a stale section
**When** the audit identifies it
**Then** the section is updated with accurate information from the implemented code

**Given** the epic introduced a new reusable pattern, feature, or architectural decision
**When** the audit identifies it
**Then** the corresponding `docs/reusable/` file is created or updated

#### Technical Notes

**Audit Method:** Run `git diff main...HEAD --name-only` (or diff against the epic's base branch) to identify all files changed in this epic. Cross-reference changed paths against the checklist below.

**Do NOT update:** `architecture.md`, `prd.md`, `ux-design-specification.md`. These are planning artifacts owned by the planning phase. If they need changes, flag them for a course correction.

#### Doc Audit Checklist

##### Tier 1: Core Docs (Always Evaluate)

**`CLAUDE.md`:**
- [ ] New routes added? → Update "Project Structure" tree
- [ ] New env vars introduced? → Update "Environment Variables" section
- [ ] New scripts added to package.json? → Update "Available Scripts" section
- [ ] New component directories created? → Update "Project Structure" tree
- [ ] New feature area built? → Add feature section (follow existing patterns: Blog System, Contact Form, etc.)
- [ ] New API routes created? → Update "Common Tasks" section

**`docs/project-context.md`:**
- [ ] New routes added? → Update "Known Routes" table
- [ ] Package versions bumped? → Update "Technology Stack & Versions" table
- [ ] New code conventions established? → Update relevant rules section
- [ ] New test patterns introduced? → Update "Testing Rules" section

##### Tier 2: Reusable Library (Conditional)

Only create/update these if the epic introduced something applicable:

- [ ] New reusable code pattern? → Create `docs/reusable/patterns/{pattern-name}.md`
- [ ] New feature fully implemented? → Create/update `docs/reusable/implementation/{feature}.md`
- [ ] Significant architectural decision made? → Create `docs/reusable/decisions/{NNN}-{decision}.md` using `_template.md`
- [ ] New feature requirements worth templating? → Create `docs/reusable/requirements/{feature}-requirements.md`
- [ ] Any `docs/reusable/BACKLOG.md` items implicitly completed? → Mark them done

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
| {{test file path}} | {{story IDs}} | {{description of test changes}} |

### E2E Test Impact

<!-- Consolidate the per-story E2E Impact sections into a single epic-level view.
     This helps QA and reviewers see the full E2E scope at a glance. -->

| Story | E2E Action | Spec File | New data-testids | Notes |
|-------|------------|-----------|------------------|-------|
| {{N}}.1 | Create / Modify / Visual Only / None | `{{spec path}}` | `{{testid-1}}`, `{{testid-2}}` | {{brief rationale}} |
| {{N}}.2 | None | — | — | Backend-only |

<!-- data-testid conventions (see docs/testing/e2e-conventions.md):
     - Naming: kebab-case `{feature}-{element}` (e.g., blog-hero, contact-form)
     - Required on: page sections, form containers, cards, list items, modals
     - Not required on: decorative elements, icons, individual text spans
     - Selector priority: getByRole > getByLabel > getByText > getByTestId -->

## Definition of Done (Epic Level)

- [ ] All stories completed and merged
- [ ] Unit tests updated and passing (`pnpm test:unit`)
- [ ] E2E tests updated/created per story E2E Impact sections
- [ ] E2E tests passing (`pnpm test:e2e`)
- [ ] New data-testids follow kebab-case `{feature}-{element}` convention
- [ ] Lighthouse scores maintain 85+ thresholds
- [ ] `pnpm lint && pnpm typecheck && pnpm build:ci` passes
- [ ] Manual QA on desktop and mobile
- [ ] Documentation sync story completed (Tier 1 core docs verified current)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| {{risk description}} | Low/Med/High | Low/Med/High | {{mitigation strategy}} |

## Rollback Plan

{{How to revert if things go wrong. Be specific about what to revert.}}
