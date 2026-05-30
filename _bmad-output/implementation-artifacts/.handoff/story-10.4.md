# Story 10.4 — Handoff

**Epic:** Epic 10 — Codex CLI Provider
**Story file:** _bmad-output/implementation-artifacts/10-4-evidence-score-integration.md
**Started:** 2026-05-30T13:00:00Z

---

## Dev Summary
**Status:** done
**Files changed:**
- src/bmad_assist_lite/providers/codex.py (modified)
- _bmad-output/implementation-artifacts/10-4-evidence-score-integration.md (modified)

**Tasks completed:** 7/7
**Decisions made:**
- Used Unicode escape sequences (\U0001f534, \U0001f7e0, \U0001f7e1, \U0001f7e2) for emoji constants instead of literal emoji characters, ensuring cross-platform compatibility and avoiding encoding issues in editors/terminals
- _DEFAULT_CLEAN_PASS_COUNT set to 5 per story spec (architecture, correctness, testing, performance, security)
- Unknown priority values default to MINOR with logger.warning() rather than raising an exception, matching the graceful degradation pattern
- Clean pass row only emitted when findings list is empty AND overall_verdict is exactly "PASS" (not NEEDS_WORK or REJECT with zero findings)
- Score values formatted with float (e.g., +3.0 not +3) which the evidence parser regex handles correctly via \d+(?:\.\d+)?
- Validation of schema keys uses all() check for ("findings", "overall_verdict", "summary") -- returns None on partial match to avoid false positives on non-review JSON

**Blockers:** none

---

## Review Findings (Cycle 1)
**Verdict:** NEEDS_FIXES

### PASS 1: BLIND HUNTER (parse_output changes)

- **JSON detection:** Robust. Uses `json.loads()` + `isinstance(data, dict)` + `all()` check for 3 required keys (`findings`, `overall_verdict`, `summary`). Returns `None` on any mismatch, preventing false positives on non-review JSON. `TypeError` is also caught (handles `None` input).
- **Priority mapping:** Correct. P0->CRITICAL (+3.0), P1->IMPORTANT (+1.0), P2->MINOR (+0.3), P3->MINOR (+0.3). Unknown priorities default to MINOR with `logger.warning()`.
- **Score values:** Correct. Float formatting (`+3.0`, `+1.0`, `+0.3`) is parsed correctly by the regex `\d+(?:\.\d+)?`.
- **Table format:** Matches `_FINDING_TABLE_PATTERN` regex expectations (verified via roundtrip test).
- **Clean pass formatting:** Correct. `| \U0001f7e2 CLEAN PASS | 5 |` matches `_CLEAN_PASS_TABLE_PATTERN`. Only emitted when `findings` is empty AND `overall_verdict == "PASS"`.

### PASS 2: EDGE CASE HUNTER

#### PATCH: Pipe characters in title/body break table parsing

**Severity: IMPORTANT** -- Pipe characters (`|`) in finding `title` or `body` fields produce malformed markdown table rows that the evidence score regex cannot parse. The formatted row becomes `| CRITICAL | Has | pipe: in | text | -- | +3.0 |` which has 6 columns instead of 4, causing `_FINDING_TABLE_PATTERN` to misparse or fail to match entirely.

**Verified:** Constructed JSON with `title: "Has | pipe"` and `body: "in | text"` -- the regex returns 0 matches. The finding is silently lost.

**Fix:** Sanitize pipe characters in `title` and `body` before formatting the table row. Replace `|` with `/` or strip them:
```python
title = finding.get("title", "").replace("|", "/")
body = finding.get("body", "").replace("|", "/")
```

#### PATCH: Newline characters in title/body produce malformed table rows

**Severity: MINOR** -- Newlines in `title` or `body` create multi-line table rows. The regex happens to match (because `[^|]+` matches newlines), but the markdown table is visually broken. In practice, Codex's schema-constrained JSON is unlikely to produce newlines in these fields, but it is not sanitized.

**Fix:** Replace newlines with spaces in the same sanitization step:
```python
title = finding.get("title", "").replace("|", "/").replace("\n", " ")
body = finding.get("body", "").replace("|", "/").replace("\n", " ")
```

#### DEFERRED: Empty findings + NEEDS_WORK/REJECT produces unparseable output

**Severity: MINOR** -- When `findings` is empty and `overall_verdict` is not `"PASS"` (e.g., `NEEDS_WORK`), the formatted output contains no finding rows, no clean pass row, and no score line. The evidence score parser returns `None`, meaning the validator is excluded from aggregation entirely. This is an unlikely edge case (Codex schema constrains `NEEDS_WORK` to imply findings exist), but the schema does not enforce it.

**Rationale for DEFERRED:** The Codex schema enum for `overall_verdict` is `["PASS", "NEEDS_WORK", "REJECT"]`. Codex is unlikely to return NEEDS_WORK with zero findings because the schema prompt instructs it to list findings. If this does occur, silent exclusion from aggregation is a safe degradation (better than a crash). Can be addressed if observed in production.

#### DISMISSED: Missing `code_location` handling

Correctly handles all three cases: (1) `code_location` absent -> em-dash, (2) `code_location` present but `file_path` missing -> em-dash, (3) `code_location.file_path` present -> used as source. Verified via test.

#### DISMISSED: `findings` type validation

Correctly rejects `findings` as string, dict, or other non-list types (returns `None`). Non-dict items within the findings array are skipped with `continue`. Verified via test.

### PASS 3: ACCEPTANCE AUDITOR

- **AC1 (Score calculation):** VERIFIED. Roundtrip test: 1 P0 + 2 P1 + 1 P2 -> `parse_evidence_findings()` returns `score=5.3`, `verdict=MAJOR_REWORK`, `findings=4`. Matches expected value exactly.
- **AC2 (Clean pass):** VERIFIED. Roundtrip test: empty findings + PASS -> `parse_evidence_findings()` returns `score=-2.5`, `verdict=PASS`, `clean_passes=5`. Matches expected negative score.
- **AC3 (Plain text fallback):** VERIFIED by code inspection. When `_structured_output is None`, `parse_output()` returns `result.stdout.strip()`. When `_structured_output` is non-JSON text, `_format_codex_json_as_evidence_text()` returns `None` and the raw text passes through.
- **AC4 (Roundtrip):** VERIFIED. Both AC1 and AC2 roundtrips confirmed programmatically.
- **AC5 (Partial code_location):** VERIFIED. Missing `code_location` and missing `file_path` both produce em-dash. Confirmed via test.
- **AC6 (Summary/verdict included):** VERIFIED. Output contains `**Overall Verdict:** {verdict}` and `**Summary:** {summary}` after the table.

### Summary

Two issues found requiring fixes, both in `_format_codex_json_as_evidence_text()`:

1. **PATCH (IMPORTANT):** Pipe characters in `title`/`body` break table regex parsing -- findings are silently lost. Must sanitize `|` characters before formatting table rows.
2. **PATCH (MINOR):** Newline characters in `title`/`body` produce malformed markdown table rows. Should sanitize `\n` to spaces.

One edge case deferred (empty findings + non-PASS verdict) as it is schema-unlikely and degrades safely.

---

## Fix Summary (Cycle 1)
**Fixes applied:** 2
**Files modified:**
- src/bmad_assist_lite/providers/codex.py

**Issues encountered:** none

---

## Review Findings (Cycle 2)
**Verdict:** NEEDS_FIXES

### Cycle 1 Fix Verification

Both cycle 1 fixes are correctly applied and working:

1. **Pipe sanitization in title/body (IMPORTANT):** VERIFIED. `title` and `body` fields now call `.replace("|", "/").replace("\n", " ")` at line 111-112 of `codex.py`. Roundtrip test with `title="Has | pipe"` and `body="in | text"` produces 1 finding parsed correctly (score=3.0). Previously returned 0 findings.

2. **Newline sanitization in title/body (MINOR):** VERIFIED. Same `.replace("\n", " ")` chain. Roundtrip test with multi-line title/body produces 1 finding parsed correctly. Table rows are now single-line.

### AC Re-verification (Post Cycle 1 Fixes)

- **AC1 (Score calculation):** PASS. 1 P0 + 2 P1 + 1 P2 = 5.3, verdict MAJOR_REWORK.
- **AC2 (Clean pass):** PASS. 5 clean passes, score=-2.5, verdict PASS.
- **AC3 (Plain text fallback):** PASS. Non-JSON returns None, raw text passes through.
- **AC4 (Roundtrip):** PASS. All roundtrip tests match expected values.
- **AC5 (Partial code_location):** PASS. Missing code_location and missing file_path both produce em-dash.
- **AC6 (Summary/verdict):** PASS. Output contains `**Overall Verdict:**` and `**Summary:**` lines.
- **Lint:** PASS. `ruff check` clean.
- **Typecheck:** PASS. `mypy` clean.

### PATCH: Pipe characters in source field (code_location.file_path) break table parsing

**Severity: MINOR** -- The cycle 1 fix sanitized `title` and `body` for pipe characters but did not sanitize the `source` field derived from `code_location.file_path`. A file path containing `|` (e.g., `src/file|name.c`) produces a malformed table row: `| MINOR | Issue: problem | src/file|name.c | +0.3 |` with 5 columns instead of 4. The regex fails to match and the finding is silently lost.

**Verified:** Constructed JSON with `file_path: "src/file|name.c"` -- `parse_evidence_findings()` returns None (0 findings, 0 clean passes, no score found). The entire report is lost, not just the one finding.

**Practical risk:** Low. Pipe characters are invalid in Windows file paths and extremely rare in Unix file paths. Codex would need to hallucinate a pipe-containing path. However, the fix is trivial (one `.replace("|", "/")` call) and completes the sanitization pattern established by the cycle 1 fixes.

**Fix:** Sanitize pipe characters in the source field after extracting from `code_location`:
```python
source = str(file_path).replace("|", "/")
```

Or apply it unconditionally to the `source` variable before the table row is formatted (after line 131):
```python
source = source.replace("|", "/")
```

### DISMISSED: No new bugs introduced by cycle 1 fixes

The `.replace("|", "/").replace("\n", " ")` chain on `title` and `body` is safe:
- Does not affect empty strings (returns empty string).
- Does not affect strings without pipes or newlines (no-op).
- Preserves all other characters.
- The `/` replacement for `|` is visually reasonable in finding descriptions.
- The space replacement for `\n` is standard single-line normalization.

---

## Fix Summary (Cycle 2)
**Fixes applied:** 1
**Files modified:**
- src/bmad_assist_lite/providers/codex.py

**Issues encountered:** none

---

## QA Results
**Verdict:** PASS

| # | AC (short) | Status | Evidence | Fix Applied? |
|---|------------|--------|----------|--------------|
| 1 | Score calculation: 1 P0 + 2 P1 + 1 P2 = +5.3 | PASS | `_PRIORITY_TO_SEVERITY` maps P0->3.0, P1->1.0, P2->0.3. Formatted table rows match `_FINDING_TABLE_PATTERN` regex. `calculate_evidence_score()` sums 3.0+1.0+1.0+0.3=5.3, `determine_verdict(5.3)` -> MAJOR_REWORK. Roundtrip verified in Cycle 1+2 reviews. | No |
| 2 | Clean pass: empty findings + PASS -> negative score | PASS | Lines 139-142: when `not findings and overall_verdict == "PASS"`, emits `\| \U0001f7e2 CLEAN PASS \| 5 \|`. `_CLEAN_PASS_TABLE_PATTERN` extracts 5. Score = 5 * -0.5 = -2.5 (negative). | No |
| 3 | Plain text fallback passes through unchanged | PASS | `parse_output()` line 229: when `_structured_output is None`, returns `result.stdout.strip()`. When `_structured_output` is non-JSON, `_format_codex_json_as_evidence_text()` catches `JSONDecodeError` -> returns `None` -> raw text returned. | No |
| 4 | Roundtrip verification | PASS | Verified in review cycles: JSON -> `_format_codex_json_as_evidence_text()` -> `parse_evidence_findings()` -> correct findings count, score, verdict for both mixed-findings and clean-pass cases. | No |
| 5 | Partial code_location handling | PASS | Lines 126-131: missing `code_location` or missing `file_path` -> source = em-dash. Verified in Cycle 1 review. | No |
| 6 | Summary and verdict included | PASS | Lines 144-148: appends `**Overall Verdict:** {verdict}` and `**Summary:** {summary}` after the table. | No |

**Fixes applied:** Sanitized pipe characters in `source` field (code_location.file_path) at line 131 of codex.py — added `.replace("|", "/")` to match the sanitization already applied to `title` and `body` fields. This was the only actionable finding from Review Findings (Cycle 2). Lint and typecheck pass.

**Gaps remaining:** None. One edge case deferred from Cycle 1 (empty findings + non-PASS verdict) remains deferred as it is schema-unlikely and degrades safely (silent exclusion from aggregation).
