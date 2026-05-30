# Story 10.3 — Handoff

**Epic:** Epic 10 — Codex CLI Provider
**Story file:** _bmad-output/implementation-artifacts/10-3-structured-output.md
**Started:** 2026-05-30T12:35:00Z

---

## Dev Summary
**Status:** done
**Files changed:**
- src/bmad_assist_lite/workflows/schemas/codex-review-schema.json (new)
- src/bmad_assist_lite/providers/codex.py (modified)
- _bmad-output/implementation-artifacts/10-3-structured-output.md (modified)

**Tasks completed:** 7/7
**Decisions made:**
- Used `__file__`-based relative path resolution (`Path(__file__).resolve().parent.parent / ...`) for schema path instead of `importlib.resources`, as it is simpler and avoids API version differences across Python versions
- Schema path resolved as module-level constant `_REVIEW_SCHEMA_PATH` rather than a class method, since the path is static and shared across all instances
- `parse_output()` validates JSON via `json.loads()` before returning, but returns the raw file content string (not the parsed dict) to match the `str` return type contract
- Temp file cleanup resets `self._temp_output_path = None` at the top of `_cleanup()` (alongside other state resets) before the actual unlink, to prevent re-entry races
- When `cwd` is None, falls back to `Path.cwd()` for temp file directory (consistent with the story spec using project cwd)
- No changes to `pyproject.toml` needed -- existing `workflows/**/*` glob recursively matches `workflows/schemas/*.json`
- Lint (ruff) and typecheck (mypy) both pass clean

**Blockers:** none

---

## Review Findings (Cycle 1)
**Verdict:** NEEDS_FIXES

### PATCH 1 — _cleanup() deletes temp file and nulls path before parse_output() can read it (BUG - P0)

**Severity:** CRITICAL (structured output feature is dead on arrival)

**Analysis:**

The BaseProvider lifecycle is: `invoke()` calls `_do_invoke()`, then unconditionally runs `_cleanup()` in the `finally` block (base.py lines 305-309), then returns `ProviderResult` to the caller. The caller later calls `parse_output(result)` on the same provider instance.

In `_cleanup()` (codex.py line 427), `self._temp_output_path` is set to `None` and the temp file is deleted (lines 445-447). This means by the time `parse_output()` is called, `self._temp_output_path is None` is always `True`, so the method ALWAYS takes the stdout fallback path (line 148-149). The structured JSON output file is never read.

This means:
- AC1 (schema-guided output) is NOT met in practice — the JSON file is written by Codex but deleted before it can be read
- AC5 (parse_output reads structured JSON) is NOT met — structured JSON is never returned
- The entire feature (structured output) is a no-op

**Fix approach:** `parse_output()` must read the temp file BEFORE `_cleanup()` deletes it. Two options:

**Option A (recommended):** Move the temp file read into `_do_invoke()` itself. After `process.wait()` succeeds and before returning `ProviderResult`, read the temp file content and store it in the `ProviderResult.stdout` field (or a new field). Then `parse_output()` works from the already-read data, not the file. This way `_cleanup()` can safely delete the file.

**Option B:** Change `_cleanup()` to NOT delete the temp file or null the path. Instead, have `parse_output()` read the file and then delete it. But this breaks the cleanup guarantee if `parse_output()` is never called (e.g., on error paths where the handler catches an exception before calling parse_output).

**Option C:** Read the file content into `self._structured_output: str | None` during `_do_invoke()` after the process completes, and have `parse_output()` use `self._structured_output` instead of reading the file. `_cleanup()` continues to delete the file and reset the path.

Option C is the cleanest: minimal changes, preserves the cleanup guarantee, and `parse_output()` works regardless of when it's called.

**File:** `src/bmad_assist_lite/providers/codex.py`

### PATCH 2 — parse_output() is never called by any handler (DEFERRED - not a bug in this story)

**Severity:** LOW (informational)

**Analysis:**

No handler in `src/bmad_assist_lite/loop/handlers/` calls `parse_output()`. The base handler's `execute()` method (base.py line 134) uses `result.stdout` directly, not `provider.parse_output(result)`. According to the epic plan, Story 10.4 will wire `parse_output()` into the evidence score flow. This is not a bug in story 10.3 — but it means the structured output feature has no consumer yet, and the PATCH 1 bug would have been invisible until 10.4 integration.

**Status:** DEFERRED to Story 10.4.

### DISMISSED 1 — Schema path resolution via __file__

The module-level constant `_REVIEW_SCHEMA_PATH` resolves via `Path(__file__).resolve().parent.parent / "workflows" / "schemas" / "codex-review-schema.json"`. Since `codex.py` is at `providers/codex.py`, `.parent` = `providers/`, `.parent.parent` = `bmad_assist_lite/`, and the schema is at `bmad_assist_lite/workflows/schemas/...`. Path is correct. The `is_file()` guard at line 228 handles the case where the schema is missing (e.g., broken install). AC2 met.

### DISMISSED 2 — Temp file race conditions in cleanup

The `_cleanup()` method snapshots all instance vars into locals (lines 419-421) before resetting them to None (lines 424-427). The `unlink(missing_ok=True)` is wrapped in `contextlib.suppress(OSError)` for Windows antivirus lock resilience. The reset-before-unlink pattern prevents re-entry races correctly. AC4 met.

### DISMISSED 3 — Malformed JSON in temp file

`parse_output()` catches `json.JSONDecodeError` at line 140 and falls back to stdout. AC3/AC6 met.

### DISMISSED 4 — Temp file doesn't exist after run (process exits early)

`parse_output()` catches `FileNotFoundError` at line 140 and falls back to stdout. AC6 met.

### DISMISSED 5 — Schema file missing (pip install issue)

Line 228 checks `_REVIEW_SCHEMA_PATH.is_file()` and logs a warning + skips the flags if missing. Graceful degradation works. AC3 met.

### DISMISSED 6 — Temp file path uniqueness

UUID4 hex[:8] provides ~4 billion unique values. Sufficient for concurrent invocations. AC7 met.

### DISMISSED 7 — pyproject.toml package data glob

`workflows/**/*` at `pyproject.toml:44` recursively matches `workflows/schemas/codex-review-schema.json`. No change needed. AC2 met.

### DISMISSED 8 — JSON schema correctness

Schema file matches the spec exactly: `findings` array with `title`/`body`/`priority` required, `overall_verdict` enum, `summary` string. All three top-level fields required. AC1 met (schema side).

### Summary

One critical bug (PATCH 1) must be fixed before this story can ship. The structured output feature is architecturally broken because `_cleanup()` destroys the temp file before `parse_output()` can read it. The fix is straightforward: read the file content into an instance variable during `_do_invoke()`, and have `parse_output()` use the cached content instead of reading the file.

---

## Fix Summary (Cycle 1)
**Fixes applied:** 1
**Files modified:**
- src/bmad_assist_lite/providers/codex.py

**Issues encountered:** none

---

## Review Findings (Cycle 2)
**Verdict:** CLEAN

### DISMISSED 1 — PATCH 1 fix correctly caches structured output before cleanup

The Cycle 1 fix (Option C) is correctly implemented. `_do_invoke()` now reads the temp file at lines 373-395 and caches the content in `self._structured_output` before returning. `_cleanup()` explicitly does NOT reset `_structured_output` (line 434 comment confirms this is intentional), so `parse_output()` can read the cached value after cleanup runs. The full lifecycle is: `_do_invoke()` reads file -> `_cleanup()` deletes file -> `parse_output()` uses cached string. No new bugs introduced.

### DISMISSED 2 — Provider instance reuse is not a concern

`get_provider()` (providers/__init__.py line 100) creates a fresh instance per call (`_REGISTRY[name]()`). `_structured_output` starts as `None` in `__init__()` and is only set during `_do_invoke()`. No stale state leakage between invocations.

### DISMISSED 3 — Timeout path falls back correctly

When `TimeoutExpired` fires at line 341, the structured output reading block (lines 373-395) is never reached. `_structured_output` remains `None`. The base class `_handle_timeout()` returns a `ProviderResult` from `collector.text`. `parse_output()` correctly falls back to `result.stdout.strip()`.

### DISMISSED 4 — Non-zero exit code path is safe

`ProviderExitCodeError` raised at line 363 skips the structured output reading. `_structured_output` remains `None`. `_cleanup()` still runs (finally block). Exception propagates to handler which does not call `parse_output()`.

### DISMISSED 5 — All temp file edge cases covered

- File missing: `FileNotFoundError` caught at line 389, falls back to stdout
- File empty: `content.strip()` is falsy at line 377, logs debug, falls back
- File has invalid JSON: `json.JSONDecodeError` caught at line 389, falls back
- File locked by antivirus during cleanup: `contextlib.suppress(OSError)` at line 454
- Schema file missing: `_REVIEW_SCHEMA_PATH.is_file()` check at line 210 skips flags entirely

### DISMISSED 6 — Lint and typecheck clean

`ruff check` and `mypy` both pass with zero errors on the modified file.

### AC Verification

| AC | Status | Evidence |
|----|--------|----------|
| AC1 Schema-guided output | PASS | `--output-schema` flag added at line 219 when schema exists |
| AC2 Schema bundled as package data | PASS | `workflows/**/*` glob at pyproject.toml:44 covers `workflows/schemas/*.json`; `_REVIEW_SCHEMA_PATH` resolves correctly |
| AC3 Graceful fallback for older Codex | PASS | Schema missing guard at line 210; JSON parse failure catch at line 389 |
| AC4 Temp file cleanup | PASS | `_cleanup()` unlinks temp file at line 455 with `missing_ok=True` and `suppress(OSError)` |
| AC5 parse_output reads structured JSON | PASS | `_structured_output` cached at line 380, returned at line 128 |
| AC6 parse_output fallback | PASS | Falls back to `result.stdout.strip()` at line 131 when `_structured_output is None` |
| AC7 Temp file path uniqueness | PASS | UUID4 hex[:8] at line 216 provides collision-resistant naming |

---

## QA Results
**Verdict:** PASS

| # | AC (short) | Status | Evidence | Fix Applied? |
|---|-----------|--------|----------|-------------|
| 1 | Output file contains valid JSON matching review schema | PASS | `_do_invoke()` adds `--output-schema` + `--output-last-message` flags (lines 210-224); reads temp file after process completes, validates with `json.loads()`, caches in `self._structured_output` (lines 373-395); schema file at `workflows/schemas/codex-review-schema.json` matches spec exactly | N/A |
| 2 | Schema path resolved via `__file__` or importlib.resources | PASS | Module-level `_REVIEW_SCHEMA_PATH` uses `Path(__file__).resolve().parent.parent / "workflows" / "schemas" / "codex-review-schema.json"` (lines 40-45); `pyproject.toml` line 44 `workflows/**/*` glob covers it; `is_file()` guard at line 210 handles broken installs | N/A |
| 3 | Fallback to stdout text for older Codex versions | PASS | If schema missing: skips flags entirely (lines 230-235); if temp file missing/empty/invalid JSON: catches `FileNotFoundError`, `json.JSONDecodeError`, `OSError` (line 389) and falls back; `parse_output()` returns `result.stdout.strip()` when `_structured_output is None` (lines 130-131) | N/A |
| 4 | Temp file cleaned up on completion | PASS | `_cleanup()` snapshots path to local var (line 427), resets instance var (line 433), unlinks with `missing_ok=True` wrapped in `contextlib.suppress(OSError)` (lines 452-455); base class guarantees `_cleanup()` runs in `finally` block on all exit paths; `_structured_output` intentionally preserved for `parse_output()` (line 434 comment) | N/A |

**Fixes applied:** None
**Gaps remaining:** None
