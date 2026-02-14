# Story {epic_num}.{story_num}: [Title]

Status: ready-for-dev

## Story

As a [user type],
I want [action],
so that [benefit].

## Acceptance Criteria

1. [Criterion from epic]

## Tasks / Subtasks

- [ ] Task 1 (AC: #1)
  - [ ] Subtask 1.1

## Dev Notes

- Architecture patterns and constraints
- Source tree components to touch
- Testing standards

### Project Structure Notes

### References

## Testing Requirements

<!-- QUALITY-GATE: BLOCKING -->

### Unit Tests (Mandatory - BLOCKING)

- [ ] Test file(s) created for new/modified modules
- [ ] All unit tests pass locally
- [ ] Positive scenario tests (happy path for each AC)
- [ ] Negative scenario tests (see checklist below)
- [ ] Edge case tests where applicable

### Negative Test Checklist (Required)

- [ ] Empty/null/missing input handled
- [ ] Invalid data types or formats rejected
- [ ] Error states produce meaningful messages
- [ ] Boundary conditions tested (min/max values, empty collections)
- [ ] Concurrent/async edge cases addressed (if applicable)

### Integration/E2E Tests (If applicable - BLOCKING)

- [ ] Integration tests cover cross-module interactions
- [ ] E2E tests verify user-facing workflows (if applicable)
- [ ] All integration/E2E tests pass locally

<!-- /QUALITY-GATE -->

## Quality Gates (MANDATORY - BLOCKING)

<!-- QUALITY-GATE: BLOCKING -->

| Gate | Status | Notes |
|------|--------|-------|
| Lint | ☐ Pass | |
| Type Check | ☐ Pass | |
| Build | ☐ Pass | |
| Unit Tests | ☐ Pass | |
| Integration Tests | ☐ Pass | |
| Runtime Verification | ☐ Pass | |

### Runtime Verification Checklist (BLOCKING)

- [ ] Application starts without errors
- [ ] No runtime errors in affected modules
- [ ] No type errors at runtime
- [ ] Affected functionality works as expected

### Integration Smoke Test (BLOCKING)

- [ ] Navigate/invoke affected routes or entry points
- [ ] Verify expected output or content
- [ ] No regressions in adjacent functionality

<!-- /QUALITY-GATE -->

## Dev Agent Record

### Agent Model Used
### Debug Log References
### Completion Notes List
### File List
