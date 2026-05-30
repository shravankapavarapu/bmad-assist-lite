# Story 10.4: Evidence Score Integration

**Story ID:** 10-4-evidence-score-integration
**Epic:** Epic-10 (Codex CLI Provider)
**Status:** dev-complete
**Points:** 2
**Priority:** High

## Story

As a developer using bmad-assist-lite with Codex reviews,
I want Codex findings to produce correct Evidence Scores,
So that the synthesis phase receives properly scored review data.

## Description

Map Codex's structured JSON findings (P0-P3 priorities) into the text format expected by the existing evidence score parser. This avoids creating a parallel parsing path -- the Codex provider's `parse_output()` formats structured JSON into the standard evidence text format, and the existing parser handles it unchanged.

### Current State

Evidence score parser (`validation/evidence_score.py`) parses text output looking for severity markers (CRITICAL, IMPORTANT, MINOR, CLEAN PASS) in table or bullet format. It works with unstructured text from Claude and Gemini reviews. The `CodexProvider.parse_output()` (from Story 10.3) returns the raw structured JSON string from the `--output-last-message` temp file, or falls back to `result.stdout.strip()` for plain text. No conversion from structured JSON to the evidence score text format exists.

### Target State

`CodexProvider.parse_output()` converts the structured JSON into formatted text that the existing evidence score parser recognizes:

```
## Evidence Score Summary

| Severity | Description | Source | Score |
|----------|-------------|--------|-------|
| 🔴 CRITICAL | {finding.title}: {finding.body} | {finding.code_location.file_path} | +3 |
| 🟠 IMPORTANT | {finding.title}: {finding.body} | {finding.code_location.file_path} | +1 |
| 🟡 MINOR | {finding.title}: {finding.body} | {finding.code_location.file_path} | +0.3 |
| 🟢 CLEAN PASS | N categories clean | — | -{N*0.5} |
```

Priority mapping:
- P0 → CRITICAL (+3)
- P1 → IMPORTANT (+1)
- P2 → MINOR (+0.3)
- P3 → MINOR (+0.3)

If `overall_verdict` is `"PASS"` and findings is empty, format as clean passes.

### Key Technical Details

- **Zero changes to `evidence_score.py`** -- All transformation happens in `CodexProvider.parse_output()`. The existing evidence score parser's regex patterns (`_FINDING_TABLE_PATTERN`, `_CLEAN_PASS_TABLE_PATTERN`) already handle the table format.
- **JSON-to-text conversion** -- `parse_output()` must detect whether the cached `_structured_output` is valid JSON matching the Codex review schema structure. If it is, convert it to the evidence score text table format. If it is plain text (fallback from Story 10.3), pass through unchanged.
- **Priority mapping constants** -- Define a `_PRIORITY_TO_SEVERITY` dict mapping `"P0"` → `("CRITICAL", "🔴", 3.0)`, `"P1"` → `("IMPORTANT", "🟠", 1.0)`, `"P2"` → `("MINOR", "🟡", 0.3)`, `"P3"` → `("MINOR", "🟡", 0.3)`.
- **Clean pass formatting** -- When `overall_verdict == "PASS"` and `findings` is empty, generate a table row with `🟢 CLEAN PASS` and a count. The parser expects `| 🟢 CLEAN PASS | N |` format (matched by `_CLEAN_PASS_TABLE_PATTERN`).
- **Evidence Score parser patterns** -- The table pattern (`_FINDING_TABLE_PATTERN`) expects: `| 🔴 CRITICAL | description | source | +3 |`. The clean pass table pattern (`_CLEAN_PASS_TABLE_PATTERN`) expects: `| 🟢 CLEAN PASS | N |`. The formatted output must match these exact regex patterns.
- **Source field** -- Use `finding.code_location.file_path` if available, otherwise use `"—"` (em-dash). The `code_location` and `file_path` fields are optional in the JSON schema.

## Acceptance Criteria

1. **Score calculation for mixed findings** -- Given Codex returns JSON with 1 P0, 2 P1, and 1 P2 finding, when `parse_output()` converts to text and the evidence parser scores it, then the score is: +3 + 1 + 1 + 0.3 = +5.3 (MAJOR REWORK).

2. **Clean pass formatting** -- Given Codex returns JSON with no findings and verdict `"PASS"`, when `parse_output()` converts to text and the evidence parser scores it, then the score reflects clean passes (negative score).

3. **Plain text fallback** -- Given Codex returns plain text instead of JSON (schema fallback), when `parse_output()` receives the text, then it passes through unchanged for the existing text parser to handle.

4. **Roundtrip verification** -- Given structured JSON from Codex, when `parse_output()` formats it as text and `parse_evidence_findings()` parses that text, then the parsed `EvidenceScoreReport` has the correct number of findings at each severity level and the calculated score matches the expected value.

5. **Partial code_location handling** -- Given a finding JSON without `code_location` or with `code_location` missing `file_path`, when `parse_output()` formats it, then the source column shows `"—"` and the finding is still correctly parsed.

6. **Summary and verdict included** -- Given structured JSON with a `summary` and `overall_verdict`, when `parse_output()` converts to text, then the summary text and verdict are included in the output so the synthesis phase can reference them.

## Tasks / Subtasks

- [x] Task 1: Study evidence score parser patterns to understand expected text format (AC: #1, #4)
  - [x] 1.1: Read `_FINDING_TABLE_PATTERN` regex in `validation/evidence_score.py` -- it expects `| 🔴 CRITICAL | description | source | +score |` format with emoji prefix, severity keyword, pipe-delimited columns
  - [x] 1.2: Read `_CLEAN_PASS_TABLE_PATTERN` regex -- it expects `| 🟢 CLEAN PASS | N |` format where N is the integer count of clean categories
  - [x] 1.3: Read `_EVIDENCE_SCORE_PATTERN` regex -- it matches `Evidence Score: X.X` or `| **Evidence Score** | **X.X** |` for the total score line
  - [x] 1.4: Confirm that the table header row (`| Severity | Description | Source | Score |`) and separator row (`|----------|-------------|--------|-------|`) are not matched by the regex and are safe to include for readability

- [x] Task 2: Define priority-to-severity mapping constants (AC: #1, #4)
  - [x] 2.1: Add a module-level `_PRIORITY_TO_SEVERITY` dict in `codex.py` mapping priority strings to tuples: `{"P0": ("CRITICAL", "🔴", 3.0), "P1": ("IMPORTANT", "🟠", 1.0), "P2": ("MINOR", "🟡", 0.3), "P3": ("MINOR", "🟡", 0.3)}`
  - [x] 2.2: Add a `_DEFAULT_CLEAN_PASS_COUNT` constant (e.g., `5`) for clean pass formatting when the Codex JSON has no findings and verdict is PASS -- this represents the number of clean review categories (architecture, correctness, testing, performance, security)

- [x] Task 3: Implement JSON-to-text conversion function (AC: #1, #2, #4, #5, #6)
  - [x] 3.1: Create a private function `_format_codex_json_as_evidence_text(json_str: str) -> str | None` in `codex.py` that attempts to parse the JSON and convert it to the evidence score text format
  - [x] 3.2: Parse `json_str` with `json.loads()` -- on `json.JSONDecodeError`, return `None` (signals fallback to plain text)
  - [x] 3.3: Validate the parsed dict has the expected top-level keys (`findings`, `overall_verdict`, `summary`) -- if missing, return `None` (not a Codex review JSON, could be other structured output)
  - [x] 3.4: Build the header: `## Evidence Score Summary\n\n| Severity | Description | Source | Score |\n|----------|-------------|--------|-------|`
  - [x] 3.5: For each finding in `findings` array, extract `title`, `body`, `priority`, and optional `code_location.file_path`. Map priority via `_PRIORITY_TO_SEVERITY`. Format as `| {emoji} {severity} | {title}: {body} | {source_or_dash} | +{score} |`
  - [x] 3.6: Handle unknown priority values (not P0-P3) by defaulting to MINOR with a `logger.warning()` message
  - [x] 3.7: Handle clean pass case: if `findings` is empty and `overall_verdict == "PASS"`, add a clean pass row: `| 🟢 CLEAN PASS | {count} |` using `_DEFAULT_CLEAN_PASS_COUNT`
  - [x] 3.8: Append summary and verdict after the table: `\n\n**Overall Verdict:** {overall_verdict}\n\n**Summary:** {summary}`
  - [x] 3.9: Return the complete formatted text string

- [x] Task 4: Update `parse_output()` to use JSON-to-text conversion (AC: #3, #4)
  - [x] 4.1: In `parse_output()`, when `self._structured_output is not None`, call `_format_codex_json_as_evidence_text(self._structured_output)` first
  - [x] 4.2: If the conversion returns a non-None string, return it (structured JSON was successfully converted to evidence text)
  - [x] 4.3: If the conversion returns `None`, fall back to returning `self._structured_output` as-is (it may be valid JSON but not matching the review schema -- let downstream handle it)
  - [x] 4.4: Keep the existing fallback to `result.stdout.strip()` when `self._structured_output is None` (plain text fallback from Story 10.3)

- [x] Task 5: Handle clean pass scenario (empty findings + PASS verdict) (AC: #2)
  - [x] 5.1: In `_format_codex_json_as_evidence_text()`, when `findings` is an empty list and `overall_verdict == "PASS"`, do NOT add any finding rows
  - [x] 5.2: Add the clean pass table row in the format the parser expects: `| 🟢 CLEAN PASS | 5 |` (5 = default clean categories count representing architecture, correctness, testing, performance, security review areas)
  - [x] 5.3: The existing `_CLEAN_PASS_TABLE_PATTERN` regex matches `| 🟢 CLEAN PASS | N |` and extracts N as the clean pass count, which feeds into `calculate_evidence_score()` as `-0.5 * N`

- [x] Task 6: Handle plain text fallback (pass through unchanged) (AC: #3)
  - [x] 6.1: Verify that when `self._structured_output is None` (Story 10.3 fallback path -- temp file missing or invalid), `parse_output()` returns `result.stdout.strip()` unchanged
  - [x] 6.2: Verify that when `self._structured_output` contains plain text (not valid JSON), `_format_codex_json_as_evidence_text()` returns `None` and the raw text is passed through
  - [x] 6.3: These fallback paths should already work from Story 10.3's implementation -- this task is verification that the new conversion logic does not break them

- [x] Task 7: Verify roundtrip: JSON → text → parsed score matches expected values (AC: #1, #2, #4)
  - [x] 7.1: Manually verify (or add inline comments documenting) the roundtrip for a mixed-findings case: construct a sample JSON with 1 P0, 2 P1, 1 P2 → run through `_format_codex_json_as_evidence_text()` → feed the output to `parse_evidence_findings()` → confirm 1 CRITICAL (score +3), 2 IMPORTANT (score +1 each), 1 MINOR (score +0.3), total = +5.3, verdict = MAJOR_REWORK
  - [x] 7.2: Verify roundtrip for clean pass case: empty findings + PASS verdict → formatted text → `parse_evidence_findings()` → confirm 0 findings, 5 clean passes, score = -2.5, verdict = PASS
  - [x] 7.3: Verify roundtrip for edge case: all P3 findings → formatted as MINOR → parsed correctly with +0.3 each
  - [x] 7.4: These verifications will be codified as automated tests in Story 10.6 (E2E Testing & Hardening) in `tests/test_codex_provider.py`

## Dev Notes

### Architecture Patterns & Constraints

- **Zero changes to `evidence_score.py`** -- The entire transformation lives in `CodexProvider.parse_output()`. This maintains the clean separation: providers produce text, the evidence score parser consumes text. No provider-specific parsing paths in the evidence module.
- **Frozen Pydantic models** -- Not applicable. `CodexProvider` is a plain class, not a Pydantic model. No Pydantic models are created or modified.
- **Exception hierarchy** -- No new exception types needed. JSON parse failures in the conversion function return `None` to signal fallback, not exceptions.
- **Type annotations** -- Full type hints on `_format_codex_json_as_evidence_text()` including return type `str | None` (mypy strict mode). Use `X | None` syntax per project convention.
- **Logging** -- Use existing `logger = logging.getLogger(__name__)`. DEBUG for conversion path selection, WARNING for unknown priority values.
- **Line length** -- 100 characters max (ruff enforced).
- **Imports** -- Only `json` (already imported) is needed. No new external dependencies.

### Dependencies on Previous Stories

- **Story 10.1 (Codex Provider Core)**: Provides the `CodexProvider` class with `__init__`, `parse_output()`, `_do_invoke()`, `_cleanup()`.
- **Story 10.3 (Structured Output)**: Provides `self._structured_output` (cached JSON string from `--output-last-message` temp file), `_REVIEW_SCHEMA_PATH`, and the fallback logic in `parse_output()`.

### Downstream Stories

- **Story 10.6 (E2E Testing & Hardening)**: Will create automated unit tests for the JSON-to-text conversion, roundtrip verification, and fallback behavior in `tests/test_codex_provider.py`.

### Evidence Score Parser Internals (Reference)

The evidence score parser in `validation/evidence_score.py` uses these regex patterns:

- **`_FINDING_TABLE_PATTERN`**: Matches `| {emoji} {SEVERITY} | {description} | {source} | +{score} |` -- extracts severity, description, source, and score from pipe-delimited table rows with emoji prefixes.
- **`_CLEAN_PASS_TABLE_PATTERN`**: Matches `| 🟢 CLEAN PASS | {N} |` -- extracts N as the integer clean pass count.
- **`_EVIDENCE_SCORE_PATTERN`**: Matches `Evidence Score: {X.X}` or `| **Evidence Score** | **{X.X}** |` -- extracts the total score value.

Score calculation: `sum(finding_scores) + (clean_passes * -0.5)`. Verdict thresholds: >= 6.0 REJECT, 4.0-5.9 MAJOR_REWORK, -2.9 to 3.9 PASS, <= -3.0 EXCELLENT.

### References

- `src/bmad_assist_lite/providers/codex.py` -- target file for `parse_output()` modification and new conversion function
- `src/bmad_assist_lite/validation/evidence_score.py` -- evidence score parser (read-only reference, no changes)
- `src/bmad_assist_lite/workflows/schemas/codex-review-schema.json` -- JSON schema defining the structured output format
- Epic 10: `_bmad-output/planning-artifacts/epic-10.md` -- Story 10.4 specification

## Testing Requirements

Testing is deferred to Story 10.6 (E2E Testing & Hardening) which creates `tests/test_codex_provider.py` with comprehensive unit tests including:

- `_format_codex_json_as_evidence_text()` with mixed findings (P0+P1+P2+P3)
- Clean pass formatting (empty findings, PASS verdict)
- Plain text fallback (non-JSON input returns None)
- Invalid JSON fallback (malformed JSON returns None)
- Missing fields fallback (JSON without `findings`/`overall_verdict` returns None)
- Missing `code_location` handling (source shows dash)
- Unknown priority handling (defaults to MINOR with warning)
- Roundtrip tests: JSON → text → `parse_evidence_findings()` → verify score and verdict

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/providers/codex.py` | PASS |
| Typecheck | `mypy src/bmad_assist_lite/providers/codex.py` | PASS |
| Tests | Deferred to Story 10.6 | N/A |

## File List

- `src/bmad_assist_lite/providers/codex.py` (modified) -- Add `_PRIORITY_TO_SEVERITY` mapping, `_format_codex_json_as_evidence_text()` conversion function, update `parse_output()` to use JSON-to-text conversion

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-05-30 | Story created from Epic 10, Story 10.4 | Claude (bmad-create-story) |
