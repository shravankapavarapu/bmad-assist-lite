# Story 10.6: End-to-End Testing & Hardening

## Story Metadata

| Field       | Value                                                                          |
|-------------|--------------------------------------------------------------------------------|
| **Story ID**    | 10-6-e2e-testing-and-hardening                                             |
| **Title**       | End-to-End Testing & Hardening                                             |
| **Epic**        | Epic 10 — Codex CLI Provider (Replace Gemini)                              |
| **Status**      | done                                                                       |
| **Points**      | 3                                                                          |
| **Priority**    | High                                                                       |
| **Dependencies**| 10-1-codex-provider-core, 10-2-provider-registry, 10-3-structured-output, 10-4-evidence-score-integration |

---

## User Story

As a developer maintaining bmad-assist-lite,
I want comprehensive tests for the Codex provider,
So that regressions are caught before they reach users.

## Description

Write unit tests with mocked subprocess for all provider behaviors. Perform manual E2E testing with a real Codex CLI invocation. Handle edge cases: CLI not installed, auth expired, rate limits, network failure, malformed output.

The existing test suite has `test_claude_sdk_timeout.py` (53 tests) and `test_gemini_timeout.py` (41 tests) that provide the testing patterns for providers. The new test file follows the same structure with test classes organized by concern area.

## Current State

Two provider test files exist (`test_claude_sdk_timeout.py`, `test_gemini_timeout.py`) providing tested patterns for subprocess mocking, NDJSON stream simulation, cleanup verification, and timeout propagation. No tests exist for the Codex provider.

## Target State

New `tests/test_codex_provider.py` with comprehensive unit tests covering:
- Command construction and subprocess spawning
- NDJSON stream parsing for `item.completed` / `agent_message` events
- Process cleanup and thread lifecycle
- Timeout propagation (TimeoutExpired -> TimeoutError -> base class grace period)
- `parse_output()` with structured JSON -> Evidence Score text and plain text fallback
- `supports_model()` acceptance and rejection
- Error conditions: CLI not found, auth expired, rate limits, network failure, empty/malformed output

---

## Acceptance Criteria

**Given** `pytest tests/test_codex_provider.py` is run
**When** all unit tests execute
**Then** all tests pass with no warnings

**Given** a manual E2E test with a real Codex CLI
**When** the full loop runs with `provider: codex` in multi config
**Then** the review phase completes successfully with parseable Evidence Score

**Given** `codex` is not in PATH
**When** the provider is invoked
**Then** `ProviderError` is raised with message "Codex CLI not found. Is 'codex' in PATH?"

---

## Task List

### Task 1: Create test file scaffold and helpers

- [x] 1.1: Create `tests/test_codex_provider.py` with module docstring, imports, and helper functions
- [x] 1.2: Create `make_ndjson_line()` helper for building NDJSON event lines (analogous to `make_json_line()` in test_gemini_timeout.py)
- [x] 1.3: Create `make_item_completed_agent_message(text)` helper that builds `{"type": "item.completed", "item": {"type": "agent_message", "content": [{"type": "output_text", "text": "..."}]}}` NDJSON lines
- [x] 1.4: Create `make_item_completed_command_execution(cmd)` helper for tool use events
- [x] 1.5: Create `create_mock_process()` helper matching the pattern in test_gemini_timeout.py (configurable stdout_content, stderr_content, returncode, wait_side_effect) but using `stdin=DEVNULL` pattern (no stdin mock needed)
- [x] 1.6: Create `build_ndjson_stream(*messages)` helper to combine NDJSON lines

### Task 2: TestInvocation class — Command construction and subprocess spawning

- [x] 2.1: `test_command_includes_exec_json_model` — Verify command list includes `["codex", "exec", "--json", "--model", model_name, prompt]`
- [x] 2.2: `test_model_flag_uses_explicit_model` — When `model="gpt-5.3-codex"` is passed, command uses that model
- [x] 2.3: `test_model_flag_uses_default_when_none` — When `model=None`, uses `"codex-mini-latest"` default
- [x] 2.4: `test_stdin_is_devnull` — Verify `Popen` is called with `stdin=subprocess.DEVNULL` (Windows hang bug workaround)
- [x] 2.5: `test_output_schema_flag_added` — When schema file exists, command includes `--output-schema <path>` and `--output-last-message <temp_path>`
- [x] 2.6: `test_output_last_message_flag_added` — Verify `--output-last-message` points to a temp file in `.bmad-assist-lite/cache/`
- [x] 2.7: `test_normal_completion_returns_result` — Full NDJSON stream with agent_message -> `ProviderResult` with `timed_out=False`
- [x] 2.8: `test_cleanup_called_on_success` — `_cleanup()` is called even on success (base class finally block)
- [x] 2.9: `test_duration_ms_is_non_negative` — Duration is measured and non-negative
- [x] 2.10: `test_tool_restriction_prompt` — `allowed_tools` parameter produces TOOL ACCESS RESTRICTIONS text in prompt
- [x] 2.11: `test_invoke_is_base_class_method` — `CodexProvider.invoke is BaseProvider.invoke` (not overridden)

### Task 3: TestNDJSONParsing class — Extract agent_message text from item.completed events

- [x] 3.1: `test_agent_message_text_extracted` — Single `item.completed` with `agent_message` -> text appears in result.stdout
- [x] 3.2: `test_multiple_agent_messages_captured` — Multiple `item.completed` events -> all text captured and concatenated
- [x] 3.3: `test_command_execution_events_ignored_in_text` — `item.completed` with `command_execution` type does NOT appear in response text
- [x] 3.4: `test_malformed_ndjson_skipped` — Invalid JSON lines are silently skipped without error
- [x] 3.5: `test_mixed_valid_and_invalid_lines` — Mix of valid NDJSON and garbage -> only valid agent_message text captured
- [x] 3.6: `test_empty_ndjson_stream` — Empty stdout -> empty response text, no errors
- [x] 3.7: `test_agent_message_with_empty_content` — `item.completed` with `agent_message` but empty/missing content array -> no text added
- [x] 3.8: `test_collector_receives_all_chunks` — Verify ResultCollector.add() is called for each agent_message chunk

### Task 4: TestCleanup class — Process termination and thread joining

- [x] 4.1: `test_cleanup_kills_running_process` — When `process.poll()` returns `None`, `kill_process()` is called
- [x] 4.2: `test_cleanup_skips_dead_process` — When `process.poll()` returns `0`, `kill_process()` is NOT called
- [x] 4.3: `test_cleanup_handles_none_process` — With `_current_process=None`, no exception, no kill
- [x] 4.4: `test_cleanup_joins_threads` — stdout and stderr threads are joined with `timeout=1`
- [x] 4.5: `test_cleanup_resets_all_state` — After cleanup, `_current_process`, `_stdout_thread`, `_stderr_thread`, `_temp_output_path` are all `None`
- [x] 4.6: `test_cleanup_removes_temp_file` — Temp output file created for `--output-last-message` is deleted
- [x] 4.7: `test_cleanup_preserves_structured_output` — `_structured_output` is NOT reset by cleanup (needed by `parse_output()`)
- [x] 4.8: `test_cleanup_exception_caught_by_base_class` — `_cleanup()` raising OSError does not mask the provider result

### Task 5: TestTimeout class — TimeoutExpired -> TimeoutError propagation

- [x] 5.1: `test_timeout_expired_becomes_timeout_error` — `TimeoutExpired` from `process.wait()` -> `TimeoutError` raised for base class
- [x] 5.2: `test_timeout_with_enough_text_returns_partial` — Timeout with >= 200 chars of streamed text -> `ProviderResult` with `timed_out=True`
- [x] 5.3: `test_timeout_with_no_response_raises_error` — Timeout with no streamed text -> `ProviderTimeoutError`
- [x] 5.4: `test_timeout_collector_has_partial_content` — Collector accumulates text from chunks delivered before timeout
- [x] 5.5: `test_cleanup_called_on_timeout_path` — `_cleanup()` called on timeout path via base class finally block
- [x] 5.6: `test_timeout_zero_raises_value_error` — `timeout=0` raises `ValueError("timeout must be positive")`
- [x] 5.7: `test_timeout_negative_raises_value_error` — Negative timeout raises `ValueError`

### Task 6: TestParseOutput class — JSON -> Evidence Score text formatting and plain text fallback

- [x] 6.1: `test_structured_json_formatted_as_evidence_text` — Cached structured JSON with findings -> Evidence Score Summary table text
- [x] 6.2: `test_p0_maps_to_critical` — P0 finding -> "CRITICAL" severity with +3 score
- [x] 6.3: `test_p1_maps_to_important` — P1 finding -> "IMPORTANT" severity with +1 score
- [x] 6.4: `test_p2_maps_to_minor` — P2 finding -> "MINOR" severity with +0.3 score
- [x] 6.5: `test_p3_maps_to_minor` — P3 finding -> "MINOR" severity with +0.3 score
- [x] 6.6: `test_clean_pass_formatting` — Empty findings + verdict "PASS" -> CLEAN PASS row
- [x] 6.7: `test_plain_text_fallback` — No cached structured output -> returns `result.stdout.strip()`
- [x] 6.8: `test_non_review_json_returns_raw` — Cached JSON that does not match review schema (missing required keys) -> returns raw JSON string
- [x] 6.9: `test_code_location_included_in_source` — Finding with `code_location.file_path` -> appears in Source column
- [x] 6.10: `test_verdict_and_summary_appended` — `overall_verdict` and `summary` appended at end of formatted text

### Task 7: TestSupportsModel class — Accepted and rejected model names

- [x] 7.1: `test_accepts_codex_mini_latest` — `supports_model("codex-mini-latest")` returns `True`
- [x] 7.2: `test_accepts_gpt_prefix` — `supports_model("gpt-5.3-codex")` returns `True`
- [x] 7.3: `test_accepts_gpt_5_4_mini` — `supports_model("gpt-5.4-mini")` returns `True`
- [x] 7.4: `test_accepts_gpt_5_5` — `supports_model("gpt-5.5")` returns `True`
- [x] 7.5: `test_accepts_codex_prefix` — `supports_model("codex-anything")` returns `True`
- [x] 7.6: `test_rejects_claude_model` — `supports_model("claude-sonnet-4-5-20250929")` returns `False`
- [x] 7.7: `test_rejects_gemini_model` — `supports_model("gemini-2.5-flash")` returns `False`
- [x] 7.8: `test_rejects_arbitrary_string` — `supports_model("random-model")` returns `False`
- [x] 7.9: `test_rejects_empty_string` — `supports_model("")` returns `False`

### Task 8: TestErrors class — CLI not found, auth errors, empty response

- [x] 8.1: `test_cli_not_found_via_shutil_which` — `shutil.which("codex")` returns `None` -> `ProviderError("Codex CLI not found...")`
- [x] 8.2: `test_file_not_found_error_wrapped` — `Popen` raises `FileNotFoundError` -> `ProviderError("Codex CLI not found...")`
- [x] 8.3: `test_auth_error_exit_code` — Non-zero exit code with stderr containing auth error text -> `ProviderExitCodeError`
- [x] 8.4: `test_rate_limit_exit_code` — Non-zero exit code with stderr containing rate limit text -> `ProviderExitCodeError`
- [x] 8.5: `test_network_failure_exit_code` — Non-zero exit code with stderr containing network error text -> `ProviderExitCodeError`
- [x] 8.6: `test_empty_response_returns_empty_string` — Zero exit code but empty stdout -> `ProviderResult` with empty `stdout`
- [x] 8.7: `test_stderr_truncated_in_error_message` — Long stderr (> 200 chars) is truncated in the error message

### Task 9: TestProviderProperties class — Basic provider properties

- [x] 9.1: `test_provider_name` — `provider_name` returns `"codex"`
- [x] 9.2: `test_default_model` — `default_model` returns `"codex-mini-latest"`
- [x] 9.3: `test_is_base_provider_subclass` — `isinstance(provider, BaseProvider)` is `True`
- [x] 9.4: `test_initial_process_is_none` — New instance has `_current_process=None`
- [x] 9.5: `test_initial_threads_are_none` — New instance has `_stdout_thread=None`, `_stderr_thread=None`

### Task 10: Verify all tests pass

- [x] 10.1: Run `pytest tests/test_codex_provider.py -v --tb=short` and verify all tests pass (65/65 passed)
- [x] 10.2: Run `mypy src/` to verify no type errors introduced (no issues found in 89 source files)
- [x] 10.3: Run `ruff check src/ tests/` to verify no lint errors (all checks passed for test file)
- [x] 10.4: Run full test suite `pytest -q --tb=line --no-header` to verify no regressions (1587 passed)

---

## File List

| File | Action | Description |
|------|--------|-------------|
| `tests/test_codex_provider.py` | CREATE | Comprehensive unit tests for CodexProvider |

---

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-05-30 | Story created from epic-10 story 10.6 | AI |

---

## Testing Requirements

<!-- QUALITY-GATE: BLOCKING -->

### Unit Tests (BLOCKING)

- All TestInvocation tests pass (command construction, model flags, stdin=DEVNULL, schema flags)
- All TestNDJSONParsing tests pass (agent_message extraction, malformed handling, empty streams)
- All TestCleanup tests pass (process kill, thread join, state reset, temp file removal)
- All TestTimeout tests pass (TimeoutExpired -> TimeoutError, partial results, ValueError on invalid timeout)
- All TestParseOutput tests pass (JSON -> evidence text, priority mapping, clean pass, plain text fallback)
- All TestSupportsModel tests pass (accepted prefixes, rejected models)
- All TestErrors tests pass (CLI not found, auth/rate-limit/network errors, empty response)
- All TestProviderProperties tests pass (name, model, subclass, initial state)

### Quality Gates

| Gate | Command | Blocking |
|------|---------|----------|
| Unit Tests | `pytest tests/test_codex_provider.py -v --tb=short` | Yes |
| Type Check | `mypy src/` | Yes |
| Lint | `ruff check src/ tests/` | Yes |
| Full Suite | `pytest -q --tb=line --no-header` | Yes |

## Technical Notes

- Follow `test_gemini_timeout.py` structure closely: organize tests by class (`TestInvocation`, `TestCleanup`, `TestTimeout`, etc.)
- Mock `subprocess.Popen` via `@patch("bmad_assist_lite.providers.codex.Popen")` to return pre-recorded NDJSON streams
- Mock `shutil.which` via `@patch("bmad_assist_lite.providers.codex.shutil")` to control CLI detection
- Mock `get_subprocess_kwargs` via `@patch("bmad_assist_lite.providers.codex.get_subprocess_kwargs", return_value={})`
- Use `conftest.py` autouse fixtures for singleton resets (automatic)
- The Codex NDJSON format differs from Gemini: uses `type: "item.completed"` with nested `item.type: "agent_message"` and `item.content: [{"type": "output_text", "text": "..."}]`
- For `parse_output()` tests: set `provider._structured_output` directly to cached JSON to test formatting without subprocess mocking
- `_REVIEW_SCHEMA_PATH` may need mocking in tests where the schema file doesn't exist on the test runner
