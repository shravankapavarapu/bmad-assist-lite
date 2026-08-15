"""Gemini CLI subprocess-based provider implementation with Windows-safe process management.

Implements the BaseProvider Template Method contract (Story 7.3):
- _do_invoke() feeds a ResultCollector during JSON stream parsing
- _cleanup() terminates the subprocess via kill_process()
- invoke() is inherited from BaseProvider (not overridden)
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from subprocess import PIPE, Popen, TimeoutExpired
from typing import Any

from bmad_assist_lite.core.exceptions import (
    ProviderError,
    ProviderExitCodeError,
)
from bmad_assist_lite.providers._windows import get_subprocess_kwargs, kill_process
from bmad_assist_lite.providers.base import (
    COMMON_TOOL_NAMES,
    BaseProvider,
    ExitStatus,
    ProviderResult,
    extract_tool_details,
    format_tag,
    is_hermetic,
    resolve_cli_path,
    validate_settings_file,
    write_progress,
)
from bmad_assist_lite.providers.result_collector import ResultCollector

logger = logging.getLogger(__name__)

PROMPT_TRUNCATE_LENGTH: int = 100
STDERR_TRUNCATE_LENGTH: int = 200
MAX_RETRIES: int = 5
RETRY_BASE_DELAY: float = 2.0
RETRY_MAX_DELAY: float = 30.0

_GEMINI_TOOL_NAME_MAP: dict[str, str] = {
    "run_shell_command": "Bash",
    "edit_file": "Edit",
    "write_file": "Write",
    "read_file": "Read",
    "list_directory": "Glob",
    "glob": "Glob",
    "grep": "Grep",
    "search_file_content": "Grep",
}



class GeminiProvider(BaseProvider):
    """Gemini CLI subprocess-based provider with Windows-safe process management.

    Uses the BaseProvider Template Method: invoke() is inherited and drives the
    full lifecycle (create collector -> _do_invoke() -> handle timeout -> _cleanup()).
    This class only implements the hooks: _do_invoke(), _cleanup(), parse_output(),
    and supports_model().
    """

    def __init__(self) -> None:
        """Initialize provider with process and thread tracking for cleanup."""
        super().__init__()
        self._current_process: Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider identifier string."""
        return "gemini"

    @property
    def default_model(self) -> str | None:
        """Return the default model identifier."""
        return "gemini-2.5-flash"

    def supports_model(self, model: str) -> bool:
        """Return True; Gemini CLI validates models at runtime."""
        return True  # Let Gemini CLI validate

    def _do_invoke(
        self,
        prompt: str,
        *,
        collector: ResultCollector,
        model: str | None = None,
        timeout: int = 300,
        settings_file: Path | None = None,
        cwd: Path | None = None,
        allowed_tools: list[str] | None = None,
        effort: str | None = None,
        color_index: int | None = None,
        system_prompt: str | None = None,
        resume: str | None = None,
    ) -> ProviderResult:
        """Execute Gemini CLI with the given prompt and return the result."""
        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")

        if effort:
            logger.debug("Gemini ignores effort=%s (Claude-only feature)", effort)

        if is_hermetic():
            logger.debug("Gemini ignores hermetic=True (Claude-only mechanism)")

        effective_model = model or self.default_model or "gemini-2.5-flash"

        if settings_file:
            validate_settings_file(settings_file, self.provider_name, effective_model)

        # Build tool restriction prompt if needed
        final_prompt = prompt
        if allowed_tools is not None:
            allowed_set = set(allowed_tools)
            restricted_tools = sorted(COMMON_TOOL_NAMES - allowed_set)
            if restricted_tools:
                allowed_str = ", ".join(allowed_tools)
                restricted_str = ", ".join(restricted_tools)
                restriction_warning = (
                    "\n\n**CRITICAL - TOOL ACCESS RESTRICTIONS:**\n"
                    f"You are a CODE REVIEWER with LIMITED tool access.\n\n"
                    f"ALLOWED tools ONLY: {allowed_str}\n"
                    f"FORBIDDEN tools (NEVER USE): {restricted_str}\n\n"
                    "You CANNOT modify any files - this is READ-ONLY.\n"
                )
                final_prompt = prompt + restriction_warning

        gemini_bin = resolve_cli_path("gemini")
        logger.debug("Gemini CLI resolved to: %s", gemini_bin)

        command: list[str] = [
            gemini_bin,
            "-m",
            effective_model,
            "--output-format",
            "stream-json",
            "--yolo",
            "-p", ".",
        ]

        returncode = 1
        session_id: str | None = None
        stderr_chunks: list[str] = []
        duration_ms = 0

        # Track loop start for remaining timeout calculation (Task 1.10)
        loop_start = time.monotonic()

        for attempt in range(MAX_RETRIES):
            if attempt > 0:
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                logger.warning(
                    "Gemini CLI retry %d/%d after %.1fs", attempt + 1, MAX_RETRIES, delay
                )
                time.sleep(delay)

            response_text_parts: list[str] = []
            stderr_chunks = []
            raw_stdout_lines: list[str] = []
            session_id = None
            start_time = time.monotonic()

            # Calculate remaining timeout (Task 1.10)
            elapsed = time.monotonic() - loop_start
            remaining = timeout - int(elapsed)
            if remaining <= 0:
                raise TimeoutError(
                    f"Gemini CLI timeout: no time remaining after {attempt} retries"
                )

            # Collector feeding strategy (Task 1.6):
            # Always feed collector.add() during streaming so the base class
            # _handle_timeout() has data for grace period evaluation if timeout
            # occurs. The collector may accumulate data from failed retries,
            # but the SUCCESS PATH uses per-attempt response_text_parts (clean,
            # scoped to the successful attempt) instead of collector.text.
            # This prevents contamination on the success path while ensuring
            # the timeout path always has collector data for grace period logic.

            try:
                env = os.environ.copy()
                if cwd is not None:
                    env["GIT_WORK_TREE"] = str(cwd)
                    env["GIT_DIR"] = str(cwd / ".git")
                    env["PWD"] = str(cwd)

                # Use Windows-safe subprocess kwargs
                popen_kwargs = get_subprocess_kwargs()
                process = Popen(
                    command,
                    stdin=PIPE,
                    stdout=PIPE,
                    stderr=PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=cwd,
                    env=env,
                    **popen_kwargs,
                )

                # Track current process for _cleanup() (Task 2.2)
                self._current_process = process

                if process.stdin:
                    process.stdin.write(final_prompt)
                    process.stdin.close()

                def process_json_stream(
                    stream: Any,
                    text_parts: list[str],
                    raw_lines: list[str],
                    color_idx: int | None,
                    result_collector: ResultCollector,
                ) -> None:
                    nonlocal session_id
                    for line in iter(stream.readline, ""):
                        raw_lines.append(line)
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            msg = json.loads(stripped)
                            msg_type = msg.get("type", "")
                            if msg_type == "init":
                                session_id = msg.get("session_id", "?")
                            elif msg_type == "message":
                                if msg.get("role") == "assistant":
                                    content = msg.get("content", "")
                                    if content:
                                        text_parts.append(content)
                                        # Feed collector for grace period tracking
                                        # (Task 1.3, 3.2)
                                        result_collector.add(content)
                                        if logger.isEnabledFor(logging.INFO):
                                            preview = content[:200] + (
                                                "..." if len(content) > 200 else ""
                                            )
                                            tag = format_tag("ASSISTANT", color_idx)
                                            write_progress(f"{tag} {preview}")
                            elif msg_type == "tool_use":
                                if logger.isEnabledFor(logging.INFO):
                                    tool_name = msg.get("tool_name", "?")
                                    tool_params = msg.get("parameters", {})
                                    details = extract_tool_details(
                                        tool_name, tool_params
                                    )
                                    display_name = _GEMINI_TOOL_NAME_MAP.get(
                                        tool_name, tool_name
                                    )
                                    tag = format_tag(f"TOOL {display_name}", color_idx)
                                    write_progress(
                                        f"{tag} {details}" if details else f"{tag}"
                                    )
                            elif msg_type == "result":
                                if logger.isEnabledFor(logging.INFO):
                                    stats = msg.get("stats", {})
                                    tag = format_tag("RESULT", color_idx)
                                    write_progress(
                                        f"{tag} tokens={stats.get('total_tokens', 0)} "
                                        f"duration={stats.get('duration_ms', 0)}ms"
                                    )
                            elif msg_type == "error" and logger.isEnabledFor(
                                logging.INFO
                            ):
                                tag = format_tag("ERROR", color_idx)
                                write_progress(
                                    f"{tag} {msg.get('message', str(msg))}"
                                )
                        except json.JSONDecodeError:
                            pass
                    stream.close()

                def read_stderr(stream: Any, chunks: list[str]) -> None:
                    for line in iter(stream.readline, ""):
                        chunks.append(line)
                    stream.close()

                stdout_thread = threading.Thread(
                    target=process_json_stream,
                    args=(
                        process.stdout,
                        response_text_parts,
                        raw_stdout_lines,
                        color_index,
                        collector,
                    ),
                )
                stderr_thread = threading.Thread(
                    target=read_stderr,
                    args=(process.stderr, stderr_chunks),
                )

                # Track threads for _cleanup() (Task 2.4)
                self._stdout_thread = stdout_thread
                self._stderr_thread = stderr_thread

                stdout_thread.start()
                stderr_thread.start()

                try:
                    returncode = process.wait(timeout=remaining)
                except TimeoutExpired:
                    # Re-raise as TimeoutError for base class (Task 1.4)
                    # Do NOT call kill_process here — _cleanup() handles it
                    raise TimeoutError(
                        f"Gemini CLI timeout after {timeout}s"
                    ) from None

                stdout_thread.join(timeout=5)
                stderr_thread.join(timeout=5)

            except FileNotFoundError as e:
                raise ProviderError(
                    f"Gemini CLI binary not found at resolved path: {gemini_bin}. "
                    "Set providers.cli_paths.gemini in config to specify explicitly."
                ) from e

            duration_ms = int((time.monotonic() - start_time) * 1000)
            stderr_content = "".join(stderr_chunks)

            if returncode != 0:
                exit_status = ExitStatus.from_code(returncode)
                stderr_truncated = (
                    stderr_content[:STDERR_TRUNCATE_LENGTH]
                    if stderr_content
                    else "(empty)"
                )
                message = (
                    f"Gemini CLI failed with exit code {returncode}: {stderr_truncated}"
                )

                error = ProviderExitCodeError(
                    message,
                    exit_code=returncode,
                    exit_status=exit_status,
                    stderr=stderr_content,
                    command=tuple(command),
                )

                is_transient = (
                    not stderr_content.strip() and exit_status == ExitStatus.ERROR
                )
                if is_transient and attempt < MAX_RETRIES - 1:
                    continue

                raise error

            break  # Success

        # Use per-attempt response_text_parts (clean, scoped to successful attempt)
        # instead of collector.text, which may contain data from failed retries.
        # The collector is fed on every attempt for timeout grace period support,
        # so collector.text may be contaminated — response_text_parts is authoritative.
        response_text = "".join(response_text_parts)

        logger.info(
            "Gemini CLI completed: duration=%dms, exit_code=%d, text_len=%d",
            duration_ms,
            returncode,
            len(response_text),
        )

        return ProviderResult(
            stdout=response_text,
            stderr="".join(stderr_chunks),
            exit_code=returncode,
            duration_ms=duration_ms,
            model=effective_model,
            command=tuple(command),
            provider_session_id=session_id,
        )

    def _cleanup(self) -> None:
        """Terminate subprocess and join reader threads.

        Called by the base class invoke() in a finally block — guaranteed to run
        on success, timeout, and unexpected exceptions.

        If the subprocess is still running (poll() returns None), calls
        kill_process() for platform-safe termination. Joins reader threads
        with a short timeout to prevent thread leaks.
        """
        process = self._current_process
        stdout_thread = self._stdout_thread
        stderr_thread = self._stderr_thread

        # Reset state first (Task 2.5)
        self._current_process = None
        self._stdout_thread = None
        self._stderr_thread = None

        # Kill process if still running (Task 2.3)
        if process is not None and process.poll() is None:
            logger.warning("Killing Gemini subprocess (still running at cleanup)")
            kill_process(process)

        # Join threads to prevent leaks (Task 2.4)
        if stdout_thread is not None:
            stdout_thread.join(timeout=1)
        if stderr_thread is not None:
            stderr_thread.join(timeout=1)

    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from provider result."""
        return result.stdout.strip()
