# Story 10.3: Structured Output via --output-schema

**Story ID:** 10-3-structured-output
**Epic:** Epic-10 (Codex CLI Provider)
**Status:** dev-complete
**Points:** 3
**Priority:** High

## Story

As a developer using Codex for code reviews,
I want review findings returned as structured JSON matching a defined schema,
So that findings can be deterministically parsed into Evidence Score calculations without fragile text parsing.

## Description

Create a JSON Schema file for code review output, pass it to Codex CLI via `--output-schema`, and use `--output-last-message` to write the final result to a temp file. Update `_do_invoke()` to use these flags and `parse_output()` to read the structured JSON.

### Current State

No review schema exists. The CodexProvider (Story 10.1) invokes `codex exec --json` and parses NDJSON events from stdout for `item.completed` agent messages. `parse_output()` simply returns `result.stdout.strip()`. Gemini returns plain text that is parsed by the evidence score text parser (regex-based, fragile). The workflows directory at `src/bmad_assist_lite/workflows/` contains workflow templates but no `schemas/` subdirectory.

### Target State

New schema file at `src/bmad_assist_lite/workflows/schemas/codex-review-schema.json`:

```json
{
  "type": "object",
  "properties": {
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "title": {"type": "string"},
          "body": {"type": "string"},
          "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
          "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
          "code_location": {
            "type": "object",
            "properties": {
              "file_path": {"type": "string"},
              "line_range": {"type": "array", "items": {"type": "integer"}}
            }
          }
        },
        "required": ["title", "body", "priority"]
      }
    },
    "overall_verdict": {"type": "string", "enum": ["PASS", "NEEDS_WORK", "REJECT"]},
    "summary": {"type": "string"}
  },
  "required": ["findings", "overall_verdict", "summary"]
}
```

Updated `_do_invoke()` adds:
- `--output-schema <schema_path>` (path to bundled schema file, resolved via `importlib.resources` or `__file__` relative path)
- `--output-last-message <temp_output_path>` (unique temp file per invocation)

Updated `parse_output()`:
- Reads temp output file as JSON
- Falls back to stdout text if file doesn't exist (graceful degradation for older Codex versions)

Updated `_cleanup()`:
- Removes temp output file if it exists (cleanup on success, timeout, or failure)

### Key Technical Details

- Schema file must be included in `pyproject.toml` as package data (already covered by `workflows/**/*` glob)
- Temp file path: `project_root / ".bmad-assist-lite" / "cache" / f"codex-review-{uuid4().hex[:8]}.json"`
- Known bug: `--output-schema` can incorrectly apply to intermediate messages (Codex issue #19816) -- `--output-last-message` is the authoritative source
- Known bug: JSON schema field names may drift between Codex CLI versions (#4776) -- add version-tolerant parsing
- The `--output-last-message` flag writes Codex's final response to a file rather than relying on NDJSON stdout parsing for the structured result

## Acceptance Criteria

1. **Schema-guided output** -- Given a review prompt is sent to Codex with `--output-schema`, when Codex completes the review, then the output file contains valid JSON matching the review schema.

2. **Schema file bundled as package data** -- Given the schema file is bundled as package data under `workflows/schemas/`, when `_do_invoke()` constructs the command, then it resolves the schema path via `importlib.resources` or `__file__` relative path.

3. **Graceful fallback for older Codex** -- Given Codex is an older version that doesn't support `--output-schema`, when the flag is unrecognized, then the provider falls back to reading stdout text (same as Gemini behavior).

4. **Temp file cleanup** -- Given the temp output file is created, when the invocation completes (success or failure), then the temp file is cleaned up in `_cleanup()`.

5. **parse_output reads structured JSON** -- Given `parse_output()` is called with a `ProviderResult` that has an associated temp output file, when the file contains valid JSON, then the structured JSON content is returned.

6. **parse_output fallback** -- Given `parse_output()` is called but the temp output file does not exist or is empty, when the fallback triggers, then it returns `result.stdout.strip()` (same behavior as Story 10.1).

7. **Temp file path uniqueness** -- Given multiple concurrent Codex invocations, when each generates a temp file path, then each path is unique (uuid-based naming prevents collisions).

## Tasks / Subtasks

- [x] Task 1: Create JSON schema file for code review output (AC: #1, #2)
  - [x] 1.1: Create directory `src/bmad_assist_lite/workflows/schemas/`
  - [x] 1.2: Create `codex-review-schema.json` with the schema from the Target State section (object with `findings` array, `overall_verdict` enum, and `summary` string)
  - [x] 1.3: Verify `pyproject.toml` package-data glob `workflows/**/*` already covers `workflows/schemas/*.json` (no change needed -- the existing `workflows/**/*` glob recursively matches all files)

- [x] Task 2: Update `pyproject.toml` if needed for schema package data (AC: #2)
  - [x] 2.1: Verify the existing `[tool.setuptools.package-data]` entry `bmad_assist_lite = ["workflows/**/*"]` covers the new `workflows/schemas/` subdirectory
  - [x] 2.2: If the glob does not cover it, add explicit entry -- but `**/*` should match recursively, so this is likely a verification-only task with no code change

- [x] Task 3: Add schema path resolution and temp file path generation to `CodexProvider` (AC: #2, #7)
  - [x] 3.1: Add `import uuid` and `from importlib import resources as importlib_resources` (or use `__file__`-based relative path) to the imports in `codex.py`
  - [x] 3.2: Add a module-level or class-level helper to resolve the schema file path: locate `workflows/schemas/codex-review-schema.json` relative to the package directory using `Path(__file__).resolve().parent.parent / "workflows" / "schemas" / "codex-review-schema.json"` (consistent with the existing pattern of `__file__`-based path resolution in the codebase)
  - [x] 3.3: Add instance variable `self._temp_output_path: Path | None = None` to `__init__()` for tracking the temp file per invocation
  - [x] 3.4: Add a method or inline logic in `_do_invoke()` to generate a unique temp file path: use `cwd / ".bmad-assist-lite" / "cache" / f"codex-review-{uuid.uuid4().hex[:8]}.json"` if `cwd` is provided, else fall back to a `tempfile.mktemp()`-style path

- [x] Task 4: Update `_do_invoke()` to add `--output-schema` and `--output-last-message` flags (AC: #1, #3)
  - [x] 4.1: After building the base command list `[codex_bin, "exec", "--json", "--model", effective_model, final_prompt]`, insert `--output-schema` and `--output-last-message` flags before the prompt argument
  - [x] 4.2: Resolve schema path using the helper from Task 3.2. If schema file does not exist at the resolved path (e.g., package not installed properly), log a warning and skip both `--output-schema` and `--output-last-message` flags (graceful degradation)
  - [x] 4.3: Generate temp output path using the helper from Task 3.4. Store it in `self._temp_output_path`
  - [x] 4.4: Insert `["--output-schema", str(schema_path), "--output-last-message", str(temp_output_path)]` into the command list
  - [x] 4.5: Ensure the parent directory of the temp output path exists (call `temp_output_path.parent.mkdir(parents=True, exist_ok=True)`)

- [x] Task 5: Update `parse_output()` to read structured JSON from temp file (AC: #5, #6)
  - [x] 5.1: Check if `self._temp_output_path` is not None and the file exists
  - [x] 5.2: If yes, read the file content and attempt `json.loads()` -- on success, return the JSON string (the raw file content, which is the structured JSON the LLM produced)
  - [x] 5.3: If the file doesn't exist, is empty, or JSON parsing fails, fall back to `result.stdout.strip()` (same as Story 10.1 behavior)
  - [x] 5.4: Log at DEBUG level when using the structured output path vs. the fallback path, for diagnostics

- [x] Task 6: Add fallback handling for missing or invalid temp file (AC: #3, #6)
  - [x] 6.1: In `parse_output()`, wrap the temp file read in `try/except (FileNotFoundError, json.JSONDecodeError, OSError)` to handle edge cases (file deleted between check and read, invalid JSON, permission errors)
  - [x] 6.2: On any exception, log at WARNING level with the exception details and fall back to `result.stdout.strip()`
  - [x] 6.3: In `_do_invoke()`, if the schema file is not found at the resolved path, proceed without `--output-schema`/`--output-last-message` flags (the provider still works -- just without structured output)

- [x] Task 7: Add temp file cleanup in `_cleanup()` (AC: #4)
  - [x] 7.1: In `_cleanup()`, after the existing process kill and thread join logic, check if `self._temp_output_path` is not None
  - [x] 7.2: If the temp file exists, delete it via `self._temp_output_path.unlink(missing_ok=True)` (safe for concurrent/race conditions)
  - [x] 7.3: Reset `self._temp_output_path = None` after cleanup (prevent stale references)
  - [x] 7.4: Wrap the unlink in `contextlib.suppress(OSError)` for robustness (file may be locked by antivirus on Windows)

## Dev Notes

### Architecture Patterns & Constraints

- **Schema file location**: `src/bmad_assist_lite/workflows/schemas/codex-review-schema.json` -- under `workflows/` so the existing `pyproject.toml` package-data glob `workflows/**/*` covers it automatically
- **Path resolution**: Use `Path(__file__).resolve().parent.parent / "workflows" / "schemas" / "codex-review-schema.json"` since `codex.py` is in `providers/` which is a sibling of `workflows/`. This is the simplest approach and avoids `importlib.resources` API version differences
- **Temp file ownership**: The temp file is created per `_do_invoke()` invocation and cleaned up in `_cleanup()`. The base class `invoke()` guarantees `_cleanup()` runs in a `finally` block, so temp files are always cleaned up
- **Frozen Pydantic models**: Not applicable -- `CodexProvider` is a plain class, not a Pydantic model. The `_temp_output_path` instance variable is mutable (set per invocation, reset in cleanup)
- **Atomic writes**: Not applicable for the temp output file -- it is written by Codex CLI (external process), not by our code. We only read and delete it
- **Exception hierarchy**: No new exception types needed. Use existing `ProviderError` for schema-not-found (if we choose to error rather than degrade gracefully)
- **Type annotations**: Full type hints on all new/modified functions including return types (mypy strict mode)
- **Union syntax**: Use `X | None`, not `Optional[X]`
- **Imports**: Absolute imports only
- **Line length**: 100 characters max (ruff)
- **Logging**: Use `logger = logging.getLogger(__name__)` (already defined). Use `logger.debug()` for structured output path selection, `logger.warning()` for fallback triggers

### Dependencies on Previous Stories

- **Story 10.1 (Codex Provider Core)**: Provides the `CodexProvider` class with `_do_invoke()`, `_cleanup()`, `parse_output()`. This story modifies all three methods.
- **Story 10.2 (Provider Registry)**: Registered `CodexProvider` in the provider registry. No changes to registry needed in this story.

### Downstream Stories

- **Story 10.4 (Evidence Score Integration)**: Will consume the structured JSON output from `parse_output()` and convert it to the text format expected by the evidence score parser. This story provides the raw structured data; 10.4 handles the transformation.

### Known Codex CLI Bugs

- **Issue #19816**: `--output-schema` can incorrectly apply to intermediate messages, not just the final response. Mitigation: use `--output-last-message` as the authoritative source for the final structured response.
- **Issue #4776**: JSON schema field names may drift between Codex CLI versions. Mitigation: use version-tolerant parsing in `parse_output()` -- if the JSON doesn't match expected structure, fall back to stdout text.

### References

- `src/bmad_assist_lite/providers/codex.py` -- target file for provider modifications
- `src/bmad_assist_lite/providers/base.py` -- BaseProvider ABC (invoke/cleanup lifecycle)
- `src/bmad_assist_lite/workflows/` -- existing workflow templates directory
- `pyproject.toml` -- package-data configuration
- Epic 10: `_bmad-output/planning-artifacts/epic-10.md` -- Story 10.3 specification

## Testing Requirements

Testing is deferred to Story 10.6 (E2E Testing & Hardening) which creates `tests/test_codex_provider.py` with comprehensive unit tests. However, the implementation should be structured to be testable:

- Schema path resolution can be verified by checking the resolved path exists
- Temp file path generation is deterministic (uuid-based) and can be verified for uniqueness
- `parse_output()` can be tested with mock `ProviderResult` objects and temp files containing known JSON
- `_cleanup()` temp file deletion can be verified by creating a temp file and confirming it's removed
- Fallback behavior can be tested by calling `parse_output()` without a temp file or with invalid JSON

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/providers/codex.py` | |
| Typecheck | `mypy src/bmad_assist_lite/providers/codex.py` | |
| Tests | Deferred to Story 10.6 | N/A |

## File List

- `src/bmad_assist_lite/workflows/schemas/codex-review-schema.json` (new) -- JSON Schema for Codex review structured output
- `src/bmad_assist_lite/providers/codex.py` (modified) -- Add `--output-schema`, `--output-last-message` flags, temp file management, structured JSON parsing in `parse_output()`
- `pyproject.toml` (verified, likely not modified) -- Existing `workflows/**/*` glob should cover `schemas/` subdirectory

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-05-30 | Story created from Epic 10, Story 10.3 | Claude (bmad-create-story) |
