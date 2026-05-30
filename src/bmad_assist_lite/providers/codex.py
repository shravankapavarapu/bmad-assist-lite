"""Codex CLI subprocess-based provider implementation with Windows-safe process management.

Implements the BaseProvider Template Method contract:
- _do_invoke() feeds a ResultCollector during NDJSON stream parsing
- _cleanup() terminates the subprocess via kill_process()
- invoke() is inherited from BaseProvider (not overridden)
"""

import contextlib
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired
from typing import Any

from bmad_assist_lite.core.exceptions import (
    ProviderError,
    ProviderExitCodeError,
)
from bmad_assist_lite.providers._windows import get_subprocess_kwargs, kill_process
from bmad_assist_lite.providers.base import (
    BaseProvider,
    ExitStatus,
    ProviderResult,
    format_tag,
    write_progress,
)
from bmad_assist_lite.providers.result_collector import ResultCollector

logger = logging.getLogger(__name__)

STDERR_TRUNCATE_LENGTH: int = 200

# Common tool names for allowed_tools restriction prompt
_COMMON_TOOL_NAMES: frozenset[str] = frozenset(
    {"Edit", "Write", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Read"}
)


def _extract_agent_message_text(item: dict[str, Any]) -> str:
    """Extract text from a Codex agent_message item's content blocks.

    The ``item.content`` field is an array of content blocks::

        [{"type": "output_text", "text": "..."}, ...]

    Iterates blocks, collects text from blocks where ``type == "output_text"``,
    and joins with empty string.

    Args:
        item: The ``item`` dict from an ``item.completed`` NDJSON event.

    Returns:
        Joined text from all ``output_text`` blocks, or empty string if none found.

    """
    content = item.get("content")
    if not content or not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "output_text":
            text = block.get("text", "")
            if text:
                parts.append(text)
    return "".join(parts)


class CodexProvider(BaseProvider):
    """Codex CLI subprocess-based provider with Windows-safe process management.

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
        return "codex"

    @property
    def default_model(self) -> str | None:
        """Return the default model identifier."""
        return "codex-mini-latest"

    def supports_model(self, model: str) -> bool:
        """Return True if the model is a known Codex/GPT model.

        Accepts any model string starting with ``gpt-`` or ``codex-`` prefix
        (case-sensitive, matching OpenAI naming convention).
        """
        return model.startswith("gpt-") or model.startswith("codex-")

    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from provider result."""
        return result.stdout.strip()

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
        color_index: int | None = None,
    ) -> ProviderResult:
        """Execute Codex CLI with NDJSON streaming and collector integration.

        Resolves model, spawns subprocess with ``codex exec --json``, parses
        NDJSON events from stdout via reader threads, and feeds collector.
        Raises TimeoutError on subprocess timeout for base class grace period
        handling.

        Args:
            prompt: The prompt text to send to the provider.
            collector: ResultCollector to accumulate streaming chunks into.
            model: Model identifier, or None for provider default.
            timeout: Timeout in seconds (always an int, resolved by invoke()).
            settings_file: Optional path to provider settings file (unused).
            cwd: Working directory for the provider process.
            allowed_tools: List of tool names the provider may use.
            color_index: Index for ANSI color differentiation in output.

        Returns:
            ProviderResult with timed_out=False on successful completion.

        Raises:
            TimeoutError: When subprocess.wait() times out (handled by base class).
            ProviderError: On CLI not found or FileNotFoundError.
            ProviderExitCodeError: On non-zero CLI exit code.
            ValueError: When timeout <= 0.

        """
        _ = settings_file  # Codex CLI has no settings file concept

        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")

        effective_model = model or self.default_model or "codex-mini-latest"

        # Build tool restriction prompt if needed
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

        # Resolve full path to codex CLI
        codex_bin = shutil.which("codex")
        if codex_bin is None:
            raise ProviderError("Codex CLI not found. Is 'codex' in PATH?")

        command: list[str] = [
            codex_bin,
            "exec",
            "--json",
            "--model",
            effective_model,
            final_prompt,
        ]

        response_text_parts: list[str] = []
        stderr_chunks: list[str] = []
        start_time = time.monotonic()

        env = os.environ.copy()
        if cwd is not None:
            env["GIT_WORK_TREE"] = str(cwd)
            env["GIT_DIR"] = str(cwd / ".git")
            env["PWD"] = str(cwd)

        try:
            popen_kwargs = get_subprocess_kwargs()
            process = Popen(
                command,
                stdin=DEVNULL,
                stdout=PIPE,
                stderr=PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                env=env,
                **popen_kwargs,
            )
        except FileNotFoundError as e:
            raise ProviderError(
                "Codex CLI not found. Is 'codex' in PATH?"
            ) from e

        self._current_process = process

        def process_ndjson_stream(
            stream: Any,
            text_parts: list[str],
            color_idx: int | None,
            result_collector: ResultCollector,
        ) -> None:
            """Read NDJSON events from stdout, extracting agent messages."""
            for line in iter(stream.readline, ""):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    msg = json.loads(stripped)
                    msg_type = msg.get("type", "")

                    if msg_type == "item.completed":
                        item = msg.get("item", {})
                        item_type = item.get("type", "")

                        if item_type == "agent_message":
                            text = _extract_agent_message_text(item)
                            if text:
                                text_parts.append(text)
                                result_collector.add(text)
                                if logger.isEnabledFor(logging.INFO):
                                    preview = text[:200] + (
                                        "..." if len(text) > 200 else ""
                                    )
                                    tag = format_tag("ASSISTANT", color_idx)
                                    write_progress(f"{tag} {preview}")

                        elif item_type == "command_execution":
                            if logger.isEnabledFor(logging.INFO):
                                cmd_details = item.get("command", "")
                                if not cmd_details:
                                    cmd_details = item.get("input", "")
                                tag = format_tag("TOOL Codex", color_idx)
                                write_progress(f"{tag} {cmd_details}")

                except json.JSONDecodeError:
                    pass
            stream.close()

        def read_stderr(stream: Any, chunks: list[str]) -> None:
            """Read stderr lines and accumulate."""
            for line in iter(stream.readline, ""):
                chunks.append(line)
            stream.close()

        stdout_thread = threading.Thread(
            target=process_ndjson_stream,
            args=(
                process.stdout,
                response_text_parts,
                color_index,
                collector,
            ),
        )
        stderr_thread = threading.Thread(
            target=read_stderr,
            args=(process.stderr, stderr_chunks),
        )

        self._stdout_thread = stdout_thread
        self._stderr_thread = stderr_thread

        stdout_thread.start()
        stderr_thread.start()

        try:
            returncode = process.wait(timeout=timeout)
        except TimeoutExpired:
            raise TimeoutError(
                f"Codex CLI timeout after {timeout}s"
            ) from None

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

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
                f"Codex CLI failed with exit code {returncode}: "
                f"{stderr_truncated}"
            )
            raise ProviderExitCodeError(
                message,
                exit_code=returncode,
                exit_status=exit_status,
                stderr=stderr_content,
                command=tuple(command),
            )

        response_text = "".join(response_text_parts)

        logger.info(
            "Codex CLI completed: duration=%dms, exit_code=%d, text_len=%d",
            duration_ms,
            returncode,
            len(response_text),
        )

        return ProviderResult(
            stdout=response_text,
            stderr=stderr_content,
            exit_code=returncode,
            duration_ms=duration_ms,
            model=effective_model,
            command=tuple(command),
        )

    def _cleanup(self) -> None:
        """Terminate subprocess and join reader threads.

        Called by the base class invoke() in a finally block -- guaranteed to run
        on success, timeout, and unexpected exceptions.

        If the subprocess is still running (poll() returns None), calls
        kill_process() for platform-safe termination. Joins reader threads
        with a short timeout to prevent thread leaks.
        """
        process = self._current_process
        stdout_thread = self._stdout_thread
        stderr_thread = self._stderr_thread

        # Reset state first to prevent re-entry
        self._current_process = None
        self._stdout_thread = None
        self._stderr_thread = None

        # Kill process if still running
        if process is not None and process.poll() is None:
            logger.warning("Killing Codex subprocess (still running at cleanup)")
            kill_process(process)
            # Wait for process to be fully reaped so pipe handles close on Windows,
            # allowing reader threads to unblock from readline().
            with contextlib.suppress(Exception):
                process.wait(timeout=3)

        # Join threads to prevent leaks
        if stdout_thread is not None:
            stdout_thread.join(timeout=1)
        if stderr_thread is not None:
            stderr_thread.join(timeout=1)
