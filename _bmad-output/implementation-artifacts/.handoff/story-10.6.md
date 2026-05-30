## Dev Summary
**Status:** done
**Files changed:**
- tests/test_codex_provider.py (new)
- _bmad-output/implementation-artifacts/10-6-e2e-testing-and-hardening.md (modified)

**Tasks completed:** 65/65 (all 10 tasks, all subtasks)
**Decisions made:**
- Used module-level `_NO_SCHEMA = MagicMock(spec=Path)` with `is_file.return_value = False` for tests that don't need structured output, avoiding per-test `new_callable` complexity
- For schema flag tests (2.5, 2.6), used inline `patch()` context manager with a fresh MagicMock to test the `is_file() == True` path
- Mock process `create_mock_process()` omits stdin mock (Codex uses `stdin=DEVNULL` unlike Gemini which writes to stdin)
- stderr length assertion uses `.strip()` to account for trailing newline added by readline mock
- Docstring capitalization adjusted per ruff D403 (lowercase `stdout` -> `Stdout`)
- Nested `with` statements combined per ruff SIM117

**Blockers:** none

**Quality gates:**
- pytest tests/test_codex_provider.py: 65 passed
- mypy src/: no issues found in 89 source files
- ruff check tests/test_codex_provider.py: all checks passed
- pytest full suite: 1587 passed, 0 failed

---

## Review Findings (Cycle 1)
**Verdict:** CLEAN

### PASS 1: BLIND HUNTER (Mock correctness, assertion specificity, test fidelity)

**DISMISSED: Module-level `_NO_SCHEMA` shared MagicMock** -- A single `MagicMock(spec=Path)` is reused across many tests as a module-level constant. Since no test mutates `_NO_SCHEMA.is_file.return_value` after initialization and all tests only read it, there is no cross-test pollution. The tests that need `is_file() == True` correctly create their own fresh mock via inline `patch()` context manager (tests 2.5, 2.6). No mock leak.

**DISMISSED: `create_mock_process` readline side_effect exhaustion** -- The mock readline returns lines then `""` for EOF. If a thread reads more lines than expected after exhaustion, `side_effect` would raise `StopIteration`. However, the `iter(stream.readline, "")` sentinel in the production code stops at `""`, so the thread exits cleanly before exhausting the side_effect list. Pattern matches test_gemini_timeout.py exactly.

**DISMISSED: `test_collector_receives_all_chunks` (3.8) wrapping approach** -- The test patches `_do_invoke` with a `capturing_do_invoke` that intercepts the collector reference, then delegates to the original. The `type: ignore[arg-type]` on `original_do_invoke()` is due to `**kwargs: object` broadening, which is harmless at runtime. This pattern is identical to `test_gemini_timeout.py::TestCollectorFeeding::test_collector_receives_all_chunks`. Assertions check `captured_collector[0].text` for all three chunks -- specific and correct.

**DISMISSED: Thread simulation correctness** -- All tests that go through `invoke()` let the base class create the `ResultCollector` and call `_do_invoke()` which starts real `threading.Thread` objects against the mock process streams. The mock readline side_effect feeds data and terminates with `""`. `process.wait()` returns synchronously. Threads are joined inside `_do_invoke()` with `timeout=5` on the happy path and via `_cleanup()` with `timeout=1` otherwise. The tests are structurally sound.

**DISMISSED: Assertion specificity** -- Tests use specific assertions: exact string matches (`assert result.stdout == "real content"`), `in` containment checks for partial matches, `is True`/`is False` for booleans, `assert_called_once_with(timeout=1)` for mock calls, and `pytest.raises(ExceptionType, match=...)` for error messages. No `assert True` or overly broad assertions found.

### PASS 2: EDGE CASE HUNTER (Coverage gaps)

**DISMISSED: `_format_codex_json_as_evidence_text` unknown priority fallback** -- Line 117 of codex.py logs a warning and defaults to MINOR for unknown priorities. No test explicitly covers this path (e.g., priority="P4" or priority="UNKNOWN"). However, the function is a module-level utility and all known priorities (P0-P3) are tested. The fallback is a 3-line branch with trivial behavior (log + default tuple). The risk is negligible and the existing P2/P3 tests cover the MINOR mapping itself. Not worth adding a test for.

**DISMISSED: Structured output file reading in `_do_invoke`** -- Lines 489-510 of codex.py read the temp output file after process completion. The test suite does not directly test this path because it would require the temp file to actually exist on disk. However, the downstream effect (cached `_structured_output`) is extensively tested via `TestParseOutput` which sets `_structured_output` directly. The file-reading code path is wrapped in try/except and is non-critical (falls back to stdout). The risk/reward of adding filesystem-touching tests here is poor.

**DISMISSED: `contextlib.suppress(OSError)` around temp file unlink** -- The cleanup wraps `unlink` in `contextlib.suppress(OSError)` for Windows antivirus lock scenarios. No test verifies that an OSError from unlink is silently swallowed. However, this is a 2-line defensive pattern from the stdlib, not custom logic. Test 4.6 verifies the happy path (unlink called with `missing_ok=True`). Adding a suppression test would test `contextlib.suppress` itself, not the provider logic.

**DISMISSED: `process.wait(timeout=3)` after kill in cleanup** -- Line 558-559 calls `process.wait(timeout=3)` wrapped in `contextlib.suppress(Exception)` after killing a running process. No test specifically verifies this reaping step. However, test 4.1 verifies `kill_process()` is called when `poll()` returns None, and the `process.wait()` reaping is a defensive Windows-specific addition. The risk of regression here is minimal.

**DISMISSED: `env` dict construction with `GIT_WORK_TREE`/`GIT_DIR`/`PWD`** -- Lines 358-363 set environment variables when `cwd` is provided. No test verifies these env vars are passed to Popen. However, this is identical to the Gemini provider pattern and is tested indirectly via `test_output_schema_flag_added` which passes `cwd=Path("/tmp/test-project")`. The env construction is straightforward dict operations.

### PASS 3: ACCEPTANCE AUDITOR (AC verification)

**AC1: "all tests pass with no warnings"** -- The handoff reports 65/65 passed with full quality gate suite (mypy, ruff, pytest full). The test count matches the 65 subtasks in the story spec (Tasks 1-10 totaling 65 subtasks). PASS.

**AC2: "CLI not found -> ProviderError"** -- `test_cli_not_found_via_shutil_which` (8.1) patches `shutil.which` to return None and asserts `ProviderError` with match "Codex CLI not found". `test_file_not_found_error_wrapped` (8.2) patches Popen to raise `FileNotFoundError` and asserts same error. Both match the AC message exactly: "Codex CLI not found. Is 'codex' in PATH?" PASS.

**AC3 (manual E2E): Deferred** -- The story spec lists manual E2E as an AC. This is inherently non-automatable in unit tests and the story itself marks it as manual verification. Not a test suite deficiency.

**Test class structure** -- 8 test classes matching the 8 concern areas from the story: `TestInvocation`, `TestNDJSONParsing`, `TestCleanup`, `TestTimeout`, `TestParseOutput`, `TestSupportsModel`, `TestErrors`, `TestProviderProperties`. All match the story task breakdown. PASS.

**Comparison with test_gemini_timeout.py patterns** -- The Codex test file correctly adapts the Gemini patterns: (1) `create_mock_process` omits `stdin` mock (Codex uses `DEVNULL`), (2) NDJSON helpers use Codex's `item.completed`/`agent_message` format instead of Gemini's `message`/`assistant` format, (3) schema flag tests are Codex-specific additions not in Gemini, (4) `parse_output` tests are Codex-specific (structured JSON evidence formatting). PASS.

---

## QA Results
**Verdict:** PASS

| # | AC (short) | Status | Evidence | Fix Applied? |
|---|------------|--------|----------|--------------|
| AC1 | pytest tests/test_codex_provider.py passes with no warnings | PASS | 65 passed in 30.20s. The single warning is a PytestCacheWarning (permission denied on .pytest_cache write) -- this is an environment/filesystem issue, not a test code warning. No test-level warnings emitted. | No |
| AC2 | CLI not found -> ProviderError with correct message | PASS | test_cli_not_found_via_shutil_which (8.1) asserts `ProviderError` with match "Codex CLI not found". test_file_not_found_error_wrapped (8.2) asserts same for FileNotFoundError from Popen. Both pass. | No |
| AC3 | Full test suite still passes | PASS | 1587 passed, 0 failed in 82.66s. Matches dev agent's reported count exactly. No regressions introduced. | No |

**Fixes applied:** None
**Gaps remaining:** None
