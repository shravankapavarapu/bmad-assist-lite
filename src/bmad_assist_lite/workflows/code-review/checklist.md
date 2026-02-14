# Senior Developer Review - Validation Checklist

## Story and Context Loading
- [ ] Story file loaded
- [ ] Story Status verified as reviewable
- [ ] Epic and Story IDs resolved
- [ ] Architecture/standards docs loaded (as available)
- [ ] Tech stack detected and documented

## Git Reality Check
- [ ] Git repository detected and analyzed
- [ ] `git status --porcelain` executed for uncommitted changes
- [ ] `git diff --name-only` executed for modified files
- [ ] Story File List compared against git reality
- [ ] Discrepancies documented (files in git but not in story, vice versa)

## Acceptance Criteria Validation
- [ ] All ACs extracted from story
- [ ] Each AC checked against implementation
- [ ] AC implementation status documented (IMPLEMENTED/PARTIAL/MISSING)
- [ ] Missing/partial ACs flagged as HIGH severity

## Task Completion Audit
- [ ] All tasks with completed status extracted
- [ ] Each completed task verified against actual code
- [ ] False completion claims flagged as CRITICAL
- [ ] Evidence recorded (file:line references)

## Code Quality Assessment

### SOLID Principles
- [ ] Single Responsibility violations checked
- [ ] Open/Closed principle violations checked
- [ ] Liskov Substitution violations checked
- [ ] Interface Segregation violations checked
- [ ] Dependency Inversion violations checked

### Hidden Bugs Detection
- [ ] Resource leaks identified
- [ ] Race conditions analyzed
- [ ] Edge cases reviewed
- [ ] Off-by-one errors checked
- [ ] Exception handling reviewed
- [ ] Boundary conditions validated

### Abstraction Analysis
- [ ] Over-engineering identified
- [ ] Under-engineering identified
- [ ] Pattern misuse flagged
- [ ] Boundary breaches documented

### Test Quality
- [ ] Lying tests identified (always pass, weak assertions)
- [ ] Missing test coverage noted
- [ ] Mock overuse flagged
- [ ] Edge case coverage assessed

### Story Test Requirements (MANDATORY - BLOCKING)
- [ ] Story's "Testing Requirements" section parsed
- [ ] All mandatory test checkboxes are `[x]` (Unit Tests section)
- [ ] Test files exist on disk for each claimed test
- [ ] Tests include negative scenarios (empty/null input, invalid data, error states)
- [ ] Negative Test Checklist items addressed
- [ ] Integration/E2E tests present if marked applicable
- [ ] Test coverage summary documented

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Unit test file(s) exist | ☐ | |
| Tests pass (claimed) | ☐ | |
| Negative scenarios covered | ☐ | |
| Edge cases covered | ☐ | |
| Integration tests (if applicable) | ☐ | |

### Performance
- [ ] N+1 queries identified
- [ ] Unnecessary allocations found
- [ ] Missing caching opportunities noted
- [ ] Blocking operations in async contexts flagged
- [ ] Algorithm efficiency reviewed

### Tech Debt
- [ ] Hard-coded values documented
- [ ] Magic strings identified
- [ ] Copy-paste code flagged
- [ ] Deprecated API usage noted
- [ ] Tight coupling identified

### Style and Type Safety
- [ ] Naming conventions verified
- [ ] Import organization reviewed
- [ ] Type hints coverage assessed
- [ ] Type correctness verified

### Security
- [ ] Credential exposure checked
- [ ] Injection vectors analyzed (SQL, command, template)
- [ ] Authentication issues reviewed
- [ ] Authorization gaps identified
- [ ] Data exposure risks assessed
- [ ] Dependency vulnerabilities checked

## Evidence Score
- [ ] All findings mapped to severity (CRITICAL/IMPORTANT/MINOR)
- [ ] Clean pass categories counted
- [ ] Evidence Score calculated
- [ ] Verdict determined (EXEMPLARY/APPROVED/MAJOR REWORK/REJECT)

## Runtime Verification (Deferred to Synthesis)

> **NOTE:** Build, test, and lint command execution is performed by the code-review-synthesis
> phase (single-provider, safe for command execution). Code-review runs multiple LLMs in
> parallel — command execution here would cause conflicts.
>
> Reviewers should verify:
> - [ ] Test FILE existence on disk (read-only check)
> - [ ] Test assertions are real (not placeholders or always-passing)
> - [ ] Test coverage of negative scenarios and edge cases
> - [ ] Code compiles/parses without obvious syntax errors (visual inspection)

## BLOCKING ISSUES

Issues that MUST prevent approval:

- [ ] Any mandatory test checkbox unchecked in Testing Requirements
- [ ] Test file claimed but does not exist on disk
- [ ] Happy-path-only tests with no negative scenario coverage
- [ ] Task marked complete `[x]` but implementation not found in code
- [ ] Acceptance Criteria marked MISSING
- [ ] CRITICAL severity security vulnerability
- [ ] Evidence Score verdict of MAJOR REWORK or REJECT

## Review Finalization
- [ ] Minimum 3 issues found (adversarial requirement)
- [ ] Suggested fixes provided for critical issues
- [ ] Review notes appended to story
- [ ] Status updated according to verdict
- [ ] Story saved successfully
