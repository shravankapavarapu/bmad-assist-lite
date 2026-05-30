# Story 10.1 — Handoff

**Epic:** Epic 10 — Codex CLI Provider
**Story file:** _bmad-output/implementation-artifacts/10-1-codex-provider-core.md
**Started:** 2026-05-30T12:05:18Z

---

## Dev Summary
**Status:** done
**Files changed:**
- src/bmad_assist_lite/providers/codex.py (new)
- _bmad-output/implementation-artifacts/10-1-codex-provider-core.md (modified)

**Tasks completed:** 9/9
**Decisions made:**
- Removed `import subprocess` from top-level imports since `DEVNULL`, `PIPE`, `Popen`, `TimeoutExpired` are imported directly from `subprocess` -- ruff flagged the unused import.
- Implemented `_extract_agent_message_text()` as a module-level helper function (not inner function or method) for testability and reuse, with full edge case handling for missing/empty content arrays and unknown block types.
- For `command_execution` events, extract command details from either `item.command` or `item.input` fields (fallback chain) since the exact Codex NDJSON schema for tool execution events may vary.
- `settings_file` parameter is accepted but explicitly ignored with `_ = settings_file` for clarity, matching the story's dev notes about Codex CLI having no settings file concept.

**Blockers:** none

---

## Review Findings (Cycle 1)
**Verdict:** NEEDS_FIXES

### PATCH
- [P1] Reader thread join on normal path has no timeout -- can block forever | File: src/bmad_assist_lite/providers/codex.py:294-295 | Fix: Change `stdout_thread.join()` to `stdout_thread.join(timeout=5)` and `stderr_thread.join()` to `stderr_thread.join(timeout=5)`. Without a timeout, if a reader thread hangs (e.g., stream.readline blocks on a broken pipe after process exits), the main thread blocks forever. The _cleanup() path correctly uses `timeout=1`, but the normal completion path (after process.wait returns) does not.
- [P2] _cleanup() kills process but does not wait for it to be reaped before joining threads | File: src/bmad_assist_lite/providers/codex.py:357-359 | Fix: After `kill_process(process)` on line 359, add `try: process.wait(timeout=3)` `except Exception: pass` before the thread joins. On Windows, pipe handles may not close until the process is fully reaped, so reader threads can remain blocked on `stream.readline()` even after `kill_process()`, causing `thread.join(timeout=1)` to time out and leak the thread.

### DECISION
- [D1] Thread join timeout on normal completion path | File: src/bmad_assist_lite/providers/codex.py:294-295 | Options: A) No timeout (current -- could block forever) B) Add timeout=5 for reasonable wait | Chosen: B | Rationale: After process.wait() returns successfully, the pipes should drain quickly. 5s is generous. Blocking indefinitely is never acceptable. | Confidence: high
- [D2] Whether to add process.wait() after kill_process in _cleanup | File: src/bmad_assist_lite/providers/codex.py:359 | Options: A) Kill only (current) B) Kill then wait(timeout=3) to ensure pipe closure | Chosen: B | Rationale: On Windows, pipe handles may not close until the process handle is fully reaped. Calling wait() after kill ensures the OS releases pipe handles, allowing reader threads to unblock from readline(). Known Windows subprocess pattern. | Confidence: high

### DEFERRED
- [DF1] Gemini provider has identical missing thread-join-timeout on normal path | File: src/bmad_assist_lite/providers/gemini.py:338-339 | Reason: pre-existing issue in a different file, out of scope for this story
- [DF2] No unit tests for CodexProvider | File: tests/ | Reason: Explicitly deferred to Story 10.6 per story spec
- [DF3] _COMMON_TOOL_NAMES duplicated between codex.py:38 and gemini.py:54 | File: src/bmad_assist_lite/providers/codex.py:38 | Reason: DRY violation but refactoring to base.py is out of scope for this story

### DISMISSED
- Count: 7

---

## Fix Summary (Cycle 1)
**Fixes applied:** 4
**Files modified:**
- src/bmad_assist_lite/providers/codex.py

**Issues encountered:** none

---

## Review Findings (Cycle 2)
**Verdict:** NEEDS_FIXES

### PATCH
- [P1] Cycle 1 fix P2 introduced a ruff SIM105 lint failure | File: src/bmad_assist_lite/providers/codex.py:362-365 | Fix: Replace `try: process.wait(timeout=3) except Exception: pass` with `with contextlib.suppress(Exception): process.wait(timeout=3)` and add `import contextlib` to the imports. SIM105 is enforced in pyproject.toml (selected via "SIM" rule group, not in ignore list). Running `ruff check src/bmad_assist_lite/providers/codex.py` currently fails. The Quality Gates table in the story spec shows Lint as PASS, but it no longer passes after the Cycle 1 fix was applied.

### DECISION
- None

### DEFERRED
- None

### DISMISSED
- Count: 12
  - (1-7) Seven items previously dismissed in Cycle 1, re-examined and still not issues
  - (8) Thread safety of `response_text_parts` and `stderr_chunks` lists: only mutated by a single writer thread each (stdout_thread appends to response_text_parts, stderr_thread appends to stderr_chunks), and read by the main thread only after `join()` returns, establishing a proper happens-before. No race condition.
  - (9) `_extract_agent_message_text` not handling non-dict items in content array: line 64 checks `isinstance(block, dict)` before accessing, correctly guarding against unexpected types.
  - (10) `_cleanup` called on normal path (after successful return): the finally block in `BaseProvider.invoke()` calls `_cleanup()` even on success. In `_cleanup()`, `process.poll()` will return the exit code (not None) since `process.wait()` already completed, so the kill branch is skipped. Thread refs are set to None but threads have already been joined on the normal path. No double-free or error.
  - (11) `_cleanup` re-entry safety: instance vars are set to None before any work, so a second call is a no-op. Correct.
  - (12) `process.wait(timeout=3)` in `_cleanup` after `kill_process`: `kill_process` on Windows calls `process.terminate()` then `process.wait(timeout=3)` internally, so the outer `process.wait(timeout=3)` in `_cleanup` is technically redundant on Windows (process already reaped inside `kill_process`). However, it is harmless (wait on an already-reaped process returns immediately) and provides coverage for the Unix path where `kill_process` calls `process.kill()` without waiting. Not a bug, just a belt-and-suspenders pattern.

---

## QA Results
**Verdict:** PASS

| # | AC (short) | Status | Evidence | Fix Applied? |
|---|---|---|---|---|
| 1 | Basic invocation | PASS | codex.py:111-336: _do_invoke builds `codex exec --json --model <model> <prompt>`, spawns Popen, parses NDJSON via reader threads, returns ProviderResult with stdout=response_text | N/A |
| 2 | Timeout handling | PASS | codex.py:288-293: process.wait(timeout=timeout) catches TimeoutExpired, raises TimeoutError with `from None` for base class grace period handling | N/A |
| 3 | Cleanup while running | PASS | codex.py:338-370: _cleanup() captures refs, resets to None, checks process.poll() is None, calls kill_process(), waits for reap, joins threads with timeout=1 | N/A |
| 4 | CLI not found | PASS | codex.py:175-177: shutil.which("codex") check raises ProviderError("Codex CLI not found. Is 'codex' in PATH?"); codex.py:212-215: FileNotFoundError fallback also raises same ProviderError | N/A |
| 5 | Windows stdin workaround | PASS | codex.py:202: stdin=DEVNULL (not PIPE) in Popen call, imported from subprocess at line 17 | N/A |
| 6 | Model support | PASS | codex.py:99-105: supports_model returns True for gpt- or codex- prefix strings, False for others. Covers codex-mini-latest, gpt-5.3-codex, gpt-5.4-mini, gpt-5.4, gpt-5.5 | N/A |
| 7 | NDJSON parsing | PASS | codex.py:219-259: process_ndjson_stream checks type=="item.completed", then item.type=="agent_message" feeds collector.add(text) via _extract_agent_message_text; item.type=="command_execution" logs at INFO via write_progress with format_tag("TOOL Codex") | N/A |
| 8 | parse_output | PASS | codex.py:107-109: returns result.stdout.strip() | N/A |

**Fixes applied:**
- Cycle 2 [P1]: Replaced try/except/pass with `contextlib.suppress(Exception)` at codex.py:363 and added `import contextlib` at line 9. Ruff SIM105 lint now passes.

**Gaps remaining:**
- None
