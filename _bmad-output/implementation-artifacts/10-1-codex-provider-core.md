# Story 10.1: Codex Provider Core

**Story ID:** 10-1-codex-provider-core
**Epic:** Epic-10 (Codex CLI Provider)
**Status:** review
**Points:** 3
**Priority:** High

## Story

As a developer using bmad-assist-lite,
I want a Codex CLI provider that can invoke `codex exec` as a subprocess and collect results,
So that Codex CLI can be used as a code review provider alongside Claude.

## Description

Create `CodexProvider` subclassing `BaseProvider` with `_do_invoke()`, `_cleanup()`, `parse_output()`, and `supports_model()`. The provider invokes `codex exec` via subprocess with `--json` for NDJSON streaming, reads `item.completed` events from stdout for the agent's response text, and feeds the `ResultCollector` for grace period tracking.

### Current State

Two providers exist: `ClaudeSDKProvider` (uses claude-agent-sdk async generator) and `GeminiProvider` (subprocess with JSON stream parsing via reader threads). No Codex provider exists.

### Target State

New `codex.py` module implementing:
- `provider_name` -> `"codex"`
- `default_model` -> `"codex-mini-latest"` (cheapest model available via ChatGPT auth)
- `supports_model()` -- accept `codex-mini-latest`, `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, or any `gpt-` / `codex-` prefixed model
- `_do_invoke()`:
  - Build command: `["codex", "exec", "--json", "--model", model, prompt]`
  - **Auth**: Pass `CODEX_API_KEY` from environment into subprocess env (loaded from `.env` by python-dotenv)
  - **Windows**: `stdin=subprocess.DEVNULL` (avoid hang bug -- Codex issue #20919)
  - **Windows**: use `get_subprocess_kwargs()` from `_windows.py` for `CREATE_NO_WINDOW`
  - Spawn `subprocess.Popen` with `stdout=PIPE, stderr=PIPE`
  - Reader threads parse NDJSON from stdout: extract `item.completed` events where `type == "agent_message"`, feed `collector.add(text)` for each
  - `process.wait(timeout=remaining)` with `TimeoutExpired` -> raise `TimeoutError`
- `_cleanup()`:
  - Track `_current_process`, `_stdout_thread`, `_stderr_thread` (same pattern as Gemini)
  - Kill via `kill_process()` if still running
  - Join reader threads with timeout

### Key Difference from GeminiProvider

- Codex NDJSON events use `type: "item.completed"` with nested `item.type: "agent_message"` for response text
- Gemini uses `type: "message"` with `role: "assistant"`
- Codex also emits `item.type: "command_execution"` events -- log these at INFO level for tool use visibility
- No retry logic needed -- Codex doesn't have Gemini's transient empty-response failures
- Uses `stdin=subprocess.DEVNULL` instead of `stdin=PIPE` (Windows stdin hang bug workaround)

## Acceptance Criteria

1. **Basic invocation** -- Given Codex CLI is installed and `CODEX_API_KEY` is set in the environment, when `CodexProvider().invoke(prompt, model="codex-mini-latest", timeout=300, cwd=project_path)` is called, then it returns a `ProviderResult` with the agent's response text in `stdout`.

2. **Timeout handling** -- Given the codex process exceeds the timeout, when `TimeoutExpired` is raised by `process.wait()`, then `TimeoutError` is raised for base class grace period handling.

3. **Cleanup while running** -- Given `_cleanup()` is called while the process is still running, when `process.poll()` returns `None`, then `kill_process()` terminates the subprocess and reader threads are joined.

4. **CLI not found** -- Given `codex` is not installed (not in PATH), when `_do_invoke()` tries to spawn the subprocess, then `ProviderError("Codex CLI not found. Is 'codex' in PATH?")` is raised.

5. **Windows stdin workaround** -- Given the subprocess is invoked on Windows, when the `Popen` call is constructed, then `stdin=subprocess.DEVNULL` is used (not `PIPE`) to prevent the non-TTY stdin hang bug.

6. **Model support** -- Given a model string, when `supports_model()` is called, then it returns `True` for `codex-mini-latest`, `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.4`, `gpt-5.5`, or any `gpt-`/`codex-` prefixed model, and `False` for non-matching strings like `gemini-2.5-flash` or `opus`.

7. **NDJSON parsing** -- Given the Codex subprocess emits NDJSON events on stdout, when reader threads process the stream, then only `item.completed` events with `item.type == "agent_message"` have their text fed to `collector.add()`, and `item.type == "command_execution"` events are logged at INFO level.

8. **parse_output** -- Given a `ProviderResult`, when `parse_output()` is called, then it returns `result.stdout.strip()`.

## Tasks / Subtasks

- [x] Task 1: Create `src/bmad_assist_lite/providers/codex.py` module file (AC: all)
  - [x] 1.1: Create the file with module docstring following the pattern from `gemini.py`: `"""Codex CLI subprocess-based provider implementation with Windows-safe process management."""`
  - [x] 1.2: Add standard imports: `json`, `logging`, `os`, `shutil`, `threading`, `time` from stdlib; `Path` from pathlib; `DEVNULL`, `PIPE`, `Popen`, `TimeoutExpired` from subprocess
  - [x] 1.3: Add project imports: `ProviderError` from `core.exceptions`; `get_subprocess_kwargs`, `kill_process` from `providers._windows`; `BaseProvider`, `ExitStatus`, `ProviderResult`, `format_tag`, `write_progress` from `providers.base`; `ResultCollector` from `providers.result_collector`
  - [x] 1.4: Add module-level `logger = logging.getLogger(__name__)`

- [x] Task 2: Implement `CodexProvider` class skeleton (AC: #1, #6, #8)
  - [x] 2.1: Define `class CodexProvider(BaseProvider):` with class docstring explaining it uses BaseProvider Template Method (same pattern as GeminiProvider)
  - [x] 2.2: Implement `__init__()` calling `super().__init__()` and initializing `_current_process: Popen[str] | None = None`, `_stdout_thread: threading.Thread | None = None`, `_stderr_thread: threading.Thread | None = None`
  - [x] 2.3: Implement `provider_name` property returning `"codex"`
  - [x] 2.4: Implement `default_model` property returning `"codex-mini-latest"`
  - [x] 2.5: Implement `parse_output(self, result: ProviderResult) -> str` returning `result.stdout.strip()`

- [x] Task 3: Implement `supports_model()` (AC: #6)
  - [x] 3.1: Accept any model string starting with `"gpt-"` or `"codex-"` prefix (case-sensitive, matching OpenAI naming convention)
  - [x] 3.2: Also accept the exact string `"codex-mini-latest"` (already covered by prefix match)
  - [x] 3.3: Return `False` for any other model string (e.g., `"gemini-2.5-flash"`, `"opus"`, `"sonnet"`)

- [x] Task 4: Implement `_do_invoke()` -- command construction and subprocess spawn (AC: #1, #4, #5)
  - [x] 4.1: Add method signature matching `BaseProvider._do_invoke()` exactly: `prompt`, `collector`, `model`, `timeout`, `settings_file`, `cwd`, `allowed_tools`, `color_index` keyword args
  - [x] 4.2: Validate `timeout > 0`, raise `ValueError` if not (consistent with GeminiProvider)
  - [x] 4.3: Resolve effective model: `model or self.default_model or "codex-mini-latest"`
  - [x] 4.4: CLI existence check: `shutil.which("codex")` -- if `None`, raise `ProviderError("Codex CLI not found. Is 'codex' in PATH?")`
  - [x] 4.5: Build command list: `[codex_bin, "exec", "--json", "--model", effective_model, final_prompt]` where `codex_bin` is the resolved path from `shutil.which()`
  - [x] 4.6: Build subprocess environment: `os.environ.copy()`, add `GIT_WORK_TREE`, `GIT_DIR`, `PWD` from `cwd` if provided (same pattern as GeminiProvider)
  - [x] 4.7: Build `allowed_tools` restriction prompt suffix if `allowed_tools` is not None (same pattern as GeminiProvider -- append tool restriction warning to prompt)
  - [x] 4.8: Spawn `Popen` with `stdin=subprocess.DEVNULL` (NOT `PIPE` -- Windows hang bug workaround), `stdout=PIPE`, `stderr=PIPE`, `text=True`, `encoding="utf-8"`, `errors="replace"`, `cwd=cwd`, `env=env`, plus `**get_subprocess_kwargs()`
  - [x] 4.9: Store process reference: `self._current_process = process`
  - [x] 4.10: Wrap `Popen` call in `try/except FileNotFoundError` -> raise `ProviderError("Codex CLI not found. Is 'codex' in PATH?")` (fallback in case `shutil.which` returned a stale path)

- [x] Task 5: Implement `_do_invoke()` -- NDJSON stream reader thread (AC: #7)
  - [x] 5.1: Define inner function `process_ndjson_stream(stream, text_parts, color_idx, result_collector)` that reads lines from stdout
  - [x] 5.2: For each non-empty line, parse as JSON via `json.loads(stripped)`
  - [x] 5.3: Check `msg.get("type")` -- handle these event types:
    - `"item.completed"`: Check nested `msg.get("item", {}).get("type")`:
      - If `"agent_message"`: Extract text from `msg["item"]["content"]` (array of content blocks -- iterate, join text blocks where `block.get("type") == "output_text"`). Feed `result_collector.add(text)` and append to `text_parts`. Log preview via `write_progress()` with `format_tag("ASSISTANT", color_idx)`
      - If `"command_execution"`: Log at INFO level via `write_progress()` with `format_tag("TOOL Codex", color_idx)` and the command details from `msg["item"]`
    - Other types: silently skip (Codex emits many internal events)
  - [x] 5.4: Wrap JSON parsing in `try/except json.JSONDecodeError: pass` (skip malformed lines, same as Gemini)
  - [x] 5.5: Close stream at end of iteration

- [x] Task 6: Implement `_do_invoke()` -- stderr reader thread (AC: #1)
  - [x] 6.1: Define inner function `read_stderr(stream, chunks)` following exact same pattern as GeminiProvider
  - [x] 6.2: Read lines, append to `stderr_chunks`, close stream

- [x] Task 7: Implement `_do_invoke()` -- thread management and process wait (AC: #1, #2)
  - [x] 7.1: Create and start `stdout_thread` targeting `process_ndjson_stream` and `stderr_thread` targeting `read_stderr`
  - [x] 7.2: Store thread references: `self._stdout_thread = stdout_thread`, `self._stderr_thread = stderr_thread`
  - [x] 7.3: Call `process.wait(timeout=timeout)` -- on `TimeoutExpired`, raise `TimeoutError(f"Codex CLI timeout after {timeout}s")` with `from None` (same pattern as GeminiProvider; do NOT call `kill_process()` here -- `_cleanup()` handles it)
  - [x] 7.4: After successful wait, join both threads
  - [x] 7.5: Calculate `duration_ms` from start time
  - [x] 7.6: Check `returncode != 0`: classify via `ExitStatus.from_code()`, log error with stderr truncated, raise `ProviderExitCodeError` (import from `core.exceptions`)
  - [x] 7.7: Build and return `ProviderResult` with `stdout=response_text` (joined from `text_parts`), `stderr`, `exit_code`, `duration_ms`, `model`, `command` tuple

- [x] Task 8: Implement `_cleanup()` (AC: #3)
  - [x] 8.1: Capture current `_current_process`, `_stdout_thread`, `_stderr_thread` into local variables
  - [x] 8.2: Reset all three instance variables to `None` immediately (same as GeminiProvider -- reset before cleanup to prevent re-entry)
  - [x] 8.3: If process is not None and `process.poll() is None`, call `kill_process(process)` with warning log
  - [x] 8.4: Join `stdout_thread` and `stderr_thread` with `timeout=1` if not None

- [x] Task 9: Handle NDJSON content block extraction (AC: #7)
  - [x] 9.1: Define helper function or inline logic to extract text from Codex `item.completed` events. The `item.content` field is an array of content blocks: `[{"type": "output_text", "text": "..."}, ...]`. Iterate blocks, collect text from blocks where `type == "output_text"`, join with empty string
  - [x] 9.2: Handle edge cases: empty `content` array, missing `content` key, blocks with unknown `type` (skip them)

## Dev Notes

### Decisions Made Under Ambiguity

1. **NDJSON event structure**: The epic describes extracting text from `item.completed` events where `item.type == "agent_message"`. Based on OpenAI Codex CLI documentation patterns, the response text is in `item.content` as an array of content blocks with `type: "output_text"`. If the actual structure differs at runtime, the reader thread will log unrecognized structures at DEBUG level and fall back gracefully (empty text extraction).

2. **No retry logic**: Unlike GeminiProvider which has retry logic for transient empty-response failures (MAX_RETRIES=5), CodexProvider has no retry loop. The epic explicitly states "No retry logic needed in this story -- Codex doesn't have Gemini's transient empty-response failures."

3. **Tool restriction prompt**: Following the exact same pattern as GeminiProvider for `allowed_tools` -- appending a restriction warning to the prompt text. Codex CLI does not have a native tool restriction flag, so prompt-level restriction is the mechanism.

4. **Exit code handling**: Using `ProviderExitCodeError` for non-zero exit codes (same as GeminiProvider). No special handling for specific Codex exit codes in this story -- that belongs to Story 10.6 (E2E Testing & Hardening) which adds rate limit, auth expired, and network failure detection.

5. **`CODEX_API_KEY` passthrough**: The epic says to pass `CODEX_API_KEY` from environment into subprocess env. Since we use `os.environ.copy()` for the subprocess env (same as GeminiProvider), the key is automatically passed through if present. No explicit handling needed -- python-dotenv loads it into `os.environ` at config load time.

6. **Settings file**: `settings_file` parameter is accepted in the signature (required by BaseProvider ABC) but not used by CodexProvider -- Codex CLI does not have a settings file concept. The parameter is silently ignored (no validation call needed).

### Architecture Patterns & Constraints

- **Frozen Pydantic models**: Not applicable -- `CodexProvider` is a plain class, not a Pydantic model
- **BaseProvider Template Method**: `invoke()` is inherited. Only `_do_invoke()`, `_cleanup()`, `parse_output()`, `supports_model()` are implemented
- **Subprocess pattern**: `Popen` with `get_subprocess_kwargs()` from `_windows.py`. `stdin=DEVNULL` (not `PIPE`). `stdout=PIPE`, `stderr=PIPE`. Thread-based stream reading
- **Logging**: `logger = logging.getLogger(__name__)` at module top. Use `write_progress()` for user-visible console output with color tagging
- **Path handling**: `pathlib.Path` only via the `cwd` parameter
- **Exception hierarchy**: `ProviderError` for CLI-not-found, `ProviderExitCodeError` for non-zero exit codes
- **Type annotations**: Full type hints on all functions including return types (mypy strict mode)
- **Union syntax**: Use `X | None`, not `Optional[X]`
- **Imports**: Absolute imports only
- **Line length**: 100 characters max (ruff)

### Project Structure

Files to create:
- `src/bmad_assist_lite/providers/codex.py` -- new provider module

Files NOT modified in this story:
- `providers/__init__.py` -- registry integration is Story 10.2
- No config changes needed
- No test file in this story -- comprehensive tests are Story 10.6

### References

- `src/bmad_assist_lite/providers/gemini.py` -- closest architectural match (subprocess + NDJSON)
- `src/bmad_assist_lite/providers/base.py` -- BaseProvider ABC with Template Method
- `src/bmad_assist_lite/providers/_windows.py` -- `get_subprocess_kwargs()`, `kill_process()`
- `src/bmad_assist_lite/providers/result_collector.py` -- `ResultCollector` for streaming chunks
- `src/bmad_assist_lite/core/exceptions.py` -- `ProviderError`, `ProviderExitCodeError`
- Epic 10: `_bmad-output/planning-artifacts/epic-10.md` -- full epic with all stories

## Testing Requirements

Testing is deferred to Story 10.6 (E2E Testing & Hardening) which creates `tests/test_codex_provider.py` with comprehensive unit tests. However, the implementation should be structured to be testable:

- `__init__()` initializes all instance state for mock verification
- `_do_invoke()` uses `shutil.which()` which is easily mockable
- `Popen` is called with explicit kwargs, mockable via `unittest.mock.patch`
- Stream reader functions are defined as inner functions but operate on injectable streams
- `_cleanup()` uses `kill_process()` from `_windows.py` which is independently testable

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/providers/codex.py` | **PASS** |
| Typecheck | `mypy src/bmad_assist_lite/providers/codex.py` | **PASS** |
| Tests | Deferred to Story 10.6 | **N/A** |

## File List

- `src/bmad_assist_lite/providers/codex.py` (new) -- CodexProvider implementation

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-05-30 | Story created from Epic 10, Story 10.1 | Claude (bmad-create-story) |
| 2026-05-30 | Implemented all 9 tasks: CodexProvider with NDJSON parsing, cleanup, model support | Claude (bmad-dev-story) |

## Dev Agent Record

- **Agent:** Claude Opus 4.6 (bmad-dev-story)
- **Date:** 2026-05-30
- **Tasks completed:** 9/9
- **Quality gates:** Lint PASS, Typecheck PASS, Tests N/A (deferred to Story 10.6)
- **Decisions:**
  1. Removed `import subprocess` from imports since `DEVNULL`, `PIPE`, `Popen`, `TimeoutExpired` are imported directly from `subprocess` -- ruff flagged the top-level import as unused.
  2. Implemented `_extract_agent_message_text()` as a module-level helper function (not an inner function or method) for testability and reuse, with full edge case handling for missing/empty content arrays and unknown block types.
  3. For `command_execution` events, extract command details from either `item.command` or `item.input` fields (fallback chain) since the exact Codex NDJSON schema for tool execution events may vary.
  4. `settings_file` parameter is accepted but explicitly ignored with `_ = settings_file` assignment for clarity, matching the story's dev notes about Codex CLI having no settings file concept.
- **Blockers:** None
