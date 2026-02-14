"""Gemini CLI subprocess-based provider implementation with Windows-safe process management."""

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from subprocess import PIPE, Popen, TimeoutExpired
from typing import Any

from bmad_assist_lite.core.exceptions import (
    ProviderError,
    ProviderExitCodeError,
    ProviderTimeoutError,
)
from bmad_assist_lite.providers.base import (
    BaseProvider,
    ExitStatus,
    ProviderResult,
    extract_tool_details,
    format_tag,
    validate_settings_file,
    write_progress,
)
from bmad_assist_lite.providers._windows import get_subprocess_kwargs, kill_process

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: int = 300
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

_COMMON_TOOL_NAMES: frozenset[str] = frozenset(
    {"Edit", "Write", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Read"}
)


class GeminiProvider(BaseProvider):
    """Gemini CLI subprocess-based provider with Windows-safe process management."""

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def default_model(self) -> str | None:
        return "gemini-2.5-flash"

    def supports_model(self, model: str) -> bool:
        return True  # Let Gemini CLI validate

    def invoke(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: int | None = None,
        settings_file: Path | None = None,
        cwd: Path | None = None,
        allowed_tools: list[str] | None = None,
        color_index: int | None = None,
    ) -> ProviderResult:
        if timeout is not None and timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")

        effective_model = model or self.default_model or "gemini-2.5-flash"
        effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

        if settings_file:
            validate_settings_file(settings_file, self.provider_name, effective_model)

        # Build tool restriction prompt if needed
        restricted_tools: list[str] | None = None
        final_prompt = prompt
        if allowed_tools is not None:
            allowed_set = set(allowed_tools)
            restricted_tools = sorted(_COMMON_TOOL_NAMES - allowed_set)
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

        # Resolve full path to gemini CLI (needed on Windows for .cmd scripts)
        gemini_bin = shutil.which("gemini")
        if gemini_bin is None:
            raise ProviderError("Gemini CLI not found. Is 'gemini' in PATH?")

        command: list[str] = [
            gemini_bin,
            "-m",
            effective_model,
            "--output-format",
            "stream-json",
            "--yolo",
        ]

        last_error: ProviderExitCodeError | None = None
        returncode = 1

        for attempt in range(MAX_RETRIES):
            if attempt > 0:
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                logger.warning(
                    "Gemini CLI retry %d/%d after %.1fs", attempt + 1, MAX_RETRIES, delay
                )
                time.sleep(delay)

            response_text_parts: list[str] = []
            stderr_chunks: list[str] = []
            raw_stdout_lines: list[str] = []
            session_id: str | None = None
            start_time = time.perf_counter()

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

                if process.stdin:
                    process.stdin.write(final_prompt)
                    process.stdin.close()

                def process_json_stream(
                    stream: Any,
                    text_parts: list[str],
                    raw_lines: list[str],
                    color_idx: int | None,
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
                                    details = extract_tool_details(tool_name, tool_params)
                                    display_name = _GEMINI_TOOL_NAME_MAP.get(tool_name, tool_name)
                                    tag = format_tag(f"TOOL {display_name}", color_idx)
                                    write_progress(f"{tag} {details}" if details else f"{tag}")
                            elif msg_type == "result":
                                if logger.isEnabledFor(logging.INFO):
                                    stats = msg.get("stats", {})
                                    tag = format_tag("RESULT", color_idx)
                                    write_progress(
                                        f"{tag} tokens={stats.get('total_tokens', 0)} "
                                        f"duration={stats.get('duration_ms', 0)}ms"
                                    )
                            elif msg_type == "error":
                                if logger.isEnabledFor(logging.INFO):
                                    tag = format_tag("ERROR", color_idx)
                                    write_progress(f"{tag} {msg.get('message', str(msg))}")
                        except json.JSONDecodeError:
                            pass
                    stream.close()

                def read_stderr(stream: Any, chunks: list[str]) -> None:
                    for line in iter(stream.readline, ""):
                        chunks.append(line)
                    stream.close()

                stdout_thread = threading.Thread(
                    target=process_json_stream,
                    args=(process.stdout, response_text_parts, raw_stdout_lines, color_index),
                )
                stderr_thread = threading.Thread(
                    target=read_stderr,
                    args=(process.stderr, stderr_chunks),
                )
                stdout_thread.start()
                stderr_thread.start()

                try:
                    returncode = process.wait(timeout=effective_timeout)
                except TimeoutExpired:
                    kill_process(process)
                    stdout_thread.join(timeout=1)
                    stderr_thread.join(timeout=1)
                    duration_ms = int((time.perf_counter() - start_time) * 1000)

                    partial_result = ProviderResult(
                        stdout="".join(response_text_parts),
                        stderr="".join(stderr_chunks),
                        exit_code=-1,
                        duration_ms=duration_ms,
                        model=effective_model,
                        command=tuple(command),
                    )
                    raise ProviderTimeoutError(
                        f"Gemini CLI timeout after {effective_timeout}s",
                        partial_result=partial_result,
                    ) from None

                stdout_thread.join()
                stderr_thread.join()

            except FileNotFoundError as e:
                raise ProviderError("Gemini CLI not found. Is 'gemini' in PATH?") from e

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            stderr_content = "".join(stderr_chunks)

            if returncode != 0:
                exit_status = ExitStatus.from_code(returncode)
                stderr_truncated = (
                    stderr_content[:STDERR_TRUNCATE_LENGTH] if stderr_content else "(empty)"
                )
                message = f"Gemini CLI failed with exit code {returncode}: {stderr_truncated}"

                error = ProviderExitCodeError(
                    message,
                    exit_code=returncode,
                    exit_status=exit_status,
                    stderr=stderr_content,
                    command=tuple(command),
                )

                is_transient = not stderr_content.strip() and exit_status == ExitStatus.ERROR
                if is_transient and attempt < MAX_RETRIES - 1:
                    last_error = error
                    continue

                raise error

            break  # Success

        response_text = "".join(response_text_parts)

        logger.info(
            "Gemini CLI completed: duration=%dms, exit_code=%d, text_len=%d",
            duration_ms,
            returncode,
            len(response_text),
        )

        return ProviderResult(
            stdout=response_text,
            stderr="".join(stderr_chunks) if "stderr_chunks" in dir() else "",
            exit_code=returncode,
            duration_ms=duration_ms,
            model=effective_model,
            command=tuple(command),
            provider_session_id=session_id,
        )

    def parse_output(self, result: ProviderResult) -> str:
        return result.stdout.strip()
