"""Cursor CLI subprocess-based provider with NDJSON stream parsing.

Implements the BaseProvider Template Method contract:
- _do_invoke() feeds a ResultCollector during NDJSON stream parsing
- _cleanup() terminates the subprocess via terminate_process()
- invoke() is inherited from BaseProvider (not overridden)
"""

import contextlib
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired
from typing import Any

from bmad_assist_lite.core.exceptions import ProviderError, ProviderExitCodeError
from bmad_assist_lite.providers._windows import get_subprocess_kwargs, terminate_process
from bmad_assist_lite.providers.base import (
    COMMON_TOOL_NAMES,
    BaseProvider,
    ExitStatus,
    ProviderResult,
    format_tag,
    resolve_cli_path,
    write_progress,
)
from bmad_assist_lite.providers.result_collector import ResultCollector

logger = logging.getLogger(__name__)

DEFAULT_CURSOR_MODEL: str = "composer-2.5"
"""Default model identifier for Cursor CLI invocations."""

STDERR_TRUNCATE_LENGTH: int = 200
"""Maximum characters of stderr included in error messages."""

# Deny-config constants (Story 11.4: Read-Only Mode & Deny-Config Lifecycle)
CURSOR_DENY_CONFIG_CONTENT: str = json.dumps(
    {"permissions": {"deny": ["Write(**)", "Shell(**)"]}}
)
"""Frozen JSON content for the Cursor CLI project-level deny-config file."""

CURSOR_DENY_CONFIG_MARKER_NAME: str = "cursor-deny-config.marker"
"""Filename for the marker that records deny-config ownership."""

CURSOR_DIR_NAME: str = ".cursor"
"""Name of the Cursor CLI configuration directory."""

CURSOR_CLI_JSON: str = "cli.json"
"""Name of the Cursor CLI project-level config file."""

# Lazy one-per-process version cache (reset via _reset_cursor_cli_version for tests)
_cursor_cli_version: str | None = None


def _reset_cursor_cli_version() -> None:
    """Reset the cached CLI version string for test isolation."""
    global _cursor_cli_version  # noqa: PLW0603
    _cursor_cli_version = None


class CursorProvider(BaseProvider):
    """Cursor CLI subprocess-based provider with NDJSON stream parsing.

    Uses the BaseProvider Template Method: invoke() is inherited and drives the
    full lifecycle (create collector -> _do_invoke() -> handle timeout -> cleanup).
    This class only implements the hooks: _do_invoke(), _cleanup(), parse_output(),
    and supports_model().
    """

    def __init__(self) -> None:
        """Initialize provider with process and thread tracking for cleanup."""
        super().__init__()
        self._current_process: Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._deny_config_path: Path | None = None
        self._deny_marker_path: Path | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider identifier string."""
        return "cursor"

    @property
    def default_model(self) -> str | None:
        """Return the default model identifier."""
        return DEFAULT_CURSOR_MODEL

    def supports_model(self, model: str) -> bool:
        """Return True if the model is a Cursor composer model.

        Accepts only models with the ``composer-`` prefix (e.g.
        ``composer-2.5``, ``composer-2.5-fast``, ``composer-1``).
        """
        return model.startswith("composer-")

    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from provider result.

        Returns ``result.stdout.strip()`` which must be Evidence-Score-parseable
        when used as a validator.
        """
        return result.stdout.strip()

    def _build_command(
        self,
        binary: str,
        model: str,
        prompt: str,
        write_mode: bool,
    ) -> list[str]:
        """Construct the Cursor CLI command argv.

        ``--trust`` is always included for headless invocations (D1).
        ``--force`` is only included when ``write_mode`` is True (D2).

        Args:
            binary: Resolved path to the cursor CLI binary.
            model: Model identifier string.
            prompt: The prompt text (appended as final argv element).
            write_mode: Whether to include ``--force`` flag.

        Returns:
            Command list suitable for ``subprocess.Popen``.

        """
        command: list[str] = [
            binary,
            "-p",
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--trust",  # Always included in headless invocations (D1)
        ]
        if write_mode:
            command.append("--force")
        command.append(prompt)
        return command

    def _setup_deny_config(self, cwd: Path, cache_dir: Path) -> None:
        """Create a deny-config file to physically block writes in read-only mode.

        Writes ``CURSOR_DENY_CONFIG_CONTENT`` to ``<cwd>/.cursor/cli.json``
        atomically (temp + ``os.replace``). If a pre-existing user file is found,
        it is left untouched and a DEBUG message is logged.

        Also writes a marker file into ``cache_dir`` recording the absolute path
        of the created deny-config, enabling crash-recovery cleanup.

        Args:
            cwd: Working directory (project root) for the subprocess.
            cache_dir: Path to ``.bmad-assist-lite/cache/`` for the marker file.

        """
        deny_config_path = (cwd / CURSOR_DIR_NAME / CURSOR_CLI_JSON).resolve()

        # Pre-existing user file — do not modify
        if deny_config_path.exists():
            logger.debug(
                "Pre-existing .cursor/cli.json found at %s — not modifying",
                deny_config_path,
            )
            self._deny_config_path = None
            return

        # Ensure directories exist
        try:
            deny_config_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.debug("Failed to create .cursor directory: %s", e)
            return

        # Atomic write of deny-config: temp → os.replace
        # Use PID-based unique temp suffix to avoid collisions with concurrent validators
        pid_suffix = f".{os.getpid()}.tmp"
        temp_deny = deny_config_path.with_suffix(pid_suffix)
        try:
            temp_deny.write_text(CURSOR_DENY_CONFIG_CONTENT, encoding="utf-8")
            os.replace(temp_deny, deny_config_path)
        except OSError as e:
            logger.debug("Failed to write deny-config: %s", e)
            with contextlib.suppress(OSError):
                temp_deny.unlink(missing_ok=True)
            return

        # Atomic write of marker file
        cache_dir.mkdir(parents=True, exist_ok=True)
        marker_path = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        temp_marker = marker_path.with_suffix(pid_suffix)
        try:
            temp_marker.write_text(str(deny_config_path), encoding="utf-8")
            os.replace(temp_marker, marker_path)
        except OSError as e:
            logger.debug("Failed to write deny-config marker: %s", e)
            with contextlib.suppress(OSError):
                temp_marker.unlink(missing_ok=True)
            # Deny-config was written successfully — still track it
            # even if marker failed (cleanup will still work via instance attrs)

        self._deny_config_path = deny_config_path
        self._deny_marker_path = marker_path
        logger.debug("Created deny-config at %s (marker: %s)", deny_config_path, marker_path)

    def _remove_deny_config(self) -> None:
        """Remove the deny-config file and marker created by this invocation.

        Safe to call multiple times. Only removes files this instance created
        (tracked via ``_deny_config_path``). Uses ``missing_ok=True`` for
        idempotency in concurrent scenarios.
        """
        if self._deny_config_path is None:
            return

        try:
            self._deny_config_path.unlink(missing_ok=True)
        except OSError as e:
            logger.debug("Failed to remove deny-config: %s", e)

        if self._deny_marker_path is not None:
            try:
                self._deny_marker_path.unlink(missing_ok=True)
            except OSError as e:
                logger.debug("Failed to remove deny-config marker: %s", e)

        self._deny_config_path = None
        self._deny_marker_path = None

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
    ) -> ProviderResult:
        """Execute Cursor CLI with NDJSON streaming and collector integration.

        Resolves model, spawns subprocess with ``cursor -p --output-format
        stream-json``, parses NDJSON events from stdout via reader threads,
        and feeds collector. Raises TimeoutError on subprocess timeout for
        base class grace period handling.

        Args:
            prompt: The prompt text to send to the provider.
            collector: ResultCollector to accumulate streaming chunks into.
            model: Model identifier, or None for provider default.
            timeout: Timeout in seconds (always an int, resolved by invoke()).
            settings_file: Optional path to provider settings file (unused).
            cwd: Working directory for the provider process.
            allowed_tools: List of tool names the provider may use.
            effort: Accepted for signature compatibility and ignored.
            color_index: Index for ANSI color differentiation in output.

        Returns:
            ProviderResult with timed_out=False on successful completion.

        Raises:
            TimeoutError: When subprocess.wait() times out (handled by base class).
            ProviderError: On CLI not found, binary missing, or stream without result.

        """
        _ = settings_file  # Cursor CLI has no settings file concept

        if effort:
            logger.debug("Cursor ignores effort=%s (Claude-only feature)", effort)

        effective_model = model or self.default_model or DEFAULT_CURSOR_MODEL

        # Resolve binary via shared CLI path resolution
        cursor_bin = resolve_cli_path("cursor")
        logger.debug("Cursor CLI resolved to: %s", cursor_bin)

        # Lazy CLI version logging (once per process)
        global _cursor_cli_version  # noqa: PLW0603
        if _cursor_cli_version is None:
            try:
                version_result = subprocess.run(
                    [cursor_bin, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                _cursor_cli_version = version_result.stdout.strip() or "unknown"
                logger.info("Cursor CLI version: %s", _cursor_cli_version)
            except Exception:
                _cursor_cli_version = "unknown"
                logger.debug("Failed to detect Cursor CLI version", exc_info=True)

        # Derive write mode: allowed_tools=None means master phase (full write)
        write_mode = allowed_tools is None

        # Set up deny-config for read-only invocations (before Popen so CLI reads it)
        if not write_mode and cwd is not None:
            cache_dir = cwd / ".bmad-assist-lite" / "cache"
            self._setup_deny_config(cwd, cache_dir)

        # Build tool restriction prompt if needed (already implemented in Story 11.3)
        final_prompt = prompt
        if allowed_tools is not None:
            allowed_set = set(allowed_tools)
            restricted_tools = sorted(COMMON_TOOL_NAMES - allowed_set)
            if restricted_tools:
                allowed_str = ", ".join(allowed_tools)
                restricted_str = ", ".join(restricted_tools)
                restriction_warning = (
                    "\n\n**CRITICAL - TOOL ACCESS RESTRICTIONS:**\n"
                    "You are a CODE REVIEWER with LIMITED tool access.\n\n"
                    f"ALLOWED tools ONLY: {allowed_str}\n"
                    f"FORBIDDEN tools (NEVER USE): {restricted_str}\n\n"
                    "You CANNOT modify any files - this is READ-ONLY.\n"
                )
                final_prompt = prompt + restriction_warning

        command = self._build_command(cursor_bin, effective_model, final_prompt, write_mode)

        # State for NDJSON dispatch (mutable containers for thread-safe sharing)
        result_text: list[str] = []  # Final result-event text only (AC #1)
        result_session_id: list[str | None] = [None]
        actual_model: list[str] = [effective_model]
        got_result_event: list[bool] = [False]
        result_error_text: list[str | None] = [None]  # Error text from result event
        stderr_chunks: list[str] = []
        start_time = time.monotonic()

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
                **popen_kwargs,
            )
        except FileNotFoundError as e:
            raise ProviderError(
                f"Cursor CLI binary not found at resolved path: {cursor_bin}. "
                "Set providers.cli_paths.cursor in config to specify explicitly."
            ) from e

        self._current_process = process

        def process_ndjson_stream(
            stream: Any,
            text_parts: list[str],
            color_idx: int | None,
            result_collector: ResultCollector,
        ) -> None:
            """Read NDJSON events from stdout and dispatch by type."""
            for line in iter(stream.readline, ""):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    msg = json.loads(stripped)
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed NDJSON line: %s", stripped[:120])
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "system":
                    # System init event — capture model and check for mismatch
                    reported_model = msg.get("model", "")
                    if reported_model:
                        if reported_model != effective_model:
                            logger.warning(
                                "Cursor model mismatch: requested %s, got %s",
                                effective_model,
                                reported_model,
                            )
                        actual_model[0] = reported_model

                elif msg_type == "message":
                    # Assistant message — feed collector for activity/grace,
                    # but do NOT append to text_parts (AC #1: stdout = result
                    # event text only)
                    message = msg.get("message", {})
                    content = message.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                if text:
                                    result_collector.add(text)
                                    if logger.isEnabledFor(logging.INFO):
                                        preview = text[:200] + (
                                            "..." if len(text) > 200 else ""
                                        )
                                        tag = format_tag("ASSISTANT", color_idx)
                                        write_progress(f"{tag} {preview}")

                elif msg_type in ("tool_call_started", "tool_call_completed"):
                    # Tool events mark collector activity (prevents false grace denial)
                    result_collector.add("")
                    if logger.isEnabledFor(logging.INFO):
                        tag = format_tag("TOOL Cursor", color_idx)
                        tool_name = msg.get("tool_name", msg.get("name", ""))
                        write_progress(f"{tag} {msg_type}: {tool_name}")

                elif msg_type == "result":
                    # Terminal result event
                    is_error = msg.get("is_error", False)
                    subtype = msg.get("subtype", "")

                    if is_error or (subtype and subtype != "success"):
                        # Result event indicates error — store in separate
                        # container (no magic sentinel strings)
                        err_text = msg.get("result", "")
                        logger.error(
                            "Cursor result event indicates error: is_error=%s, "
                            "subtype=%s, text=%s",
                            is_error,
                            subtype,
                            err_text[:200],
                        )
                        result_error_text[0] = err_text
                        got_result_event[0] = True
                    else:
                        # Successful result — text_parts gets result-event
                        # text only (AC #1)
                        final_text = msg.get("result", "")
                        session_id = msg.get("session_id")
                        if final_text:
                            text_parts.append(final_text)
                            result_collector.add(final_text)
                        result_session_id[0] = session_id
                        got_result_event[0] = True
                # Unknown event types are silently ignored

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
                result_text,
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
                f"Cursor CLI timeout after {timeout}s"
            ) from None

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

        duration_ms = int((time.monotonic() - start_time) * 1000)
        stderr_content = "".join(stderr_chunks)
        response_text = "".join(result_text)

        # Check for result event error (stored in separate container)
        if result_error_text[0] is not None:
            error_text = result_error_text[0]
            if not stderr_content:
                stderr_truncated = "(empty)"
            elif len(stderr_content) <= STDERR_TRUNCATE_LENGTH:
                stderr_truncated = stderr_content.strip()
            else:
                stderr_truncated = "..." + stderr_content[
                    -STDERR_TRUNCATE_LENGTH:
                ].strip()
            raise ProviderError(
                f"Cursor result event reported error: {error_text[:200]}. "
                f"stderr: {stderr_truncated}"
            )

        # Success determination based on result event, not exit code
        if got_result_event[0]:
            # Result event received — treat as success
            if returncode != 0:
                logger.info(
                    "Cursor CLI exited with code %d after result event "
                    "(known upstream quirk, treating as success)",
                    returncode,
                )

            logger.info(
                "Cursor CLI completed: duration=%dms, exit_code=%d, text_len=%d",
                duration_ms,
                returncode,
                len(response_text),
            )

            return ProviderResult(
                stdout=response_text,
                stderr=stderr_content,
                exit_code=returncode,
                duration_ms=duration_ms,
                model=actual_model[0],
                command=tuple(command),
                provider_session_id=result_session_id[0],
            )

        # No result event received
        if returncode != 0:
            if not stderr_content:
                stderr_truncated = "(empty)"
            elif len(stderr_content) <= STDERR_TRUNCATE_LENGTH:
                stderr_truncated = stderr_content.strip()
            else:
                stderr_truncated = "..." + stderr_content[
                    -STDERR_TRUNCATE_LENGTH:
                ].strip()
            raise ProviderExitCodeError(
                f"Cursor CLI failed with exit code {returncode}, "
                f"no result event received: {stderr_truncated}",
                exit_code=returncode,
                exit_status=ExitStatus.from_code(returncode),
                stderr=stderr_content,
                command=tuple(command),
            )

        # Zero exit but no result event
        raise ProviderError(
            "Cursor CLI stream ended without result event (exit code 0)"
        )

    def _cleanup(self) -> None:
        """Terminate subprocess and join reader threads.

        Called by the base class invoke() in a finally block -- guaranteed to run
        on success, timeout, and unexpected exceptions.

        If the subprocess is still running (poll() returns None), calls
        terminate_process() for platform-safe SIGTERM->SIGKILL escalation.
        Joins reader threads with a short timeout to prevent thread leaks.
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
            logger.warning("Killing Cursor subprocess (still running at cleanup)")
            if process.pid:
                terminate_process(process.pid)
            # Wait for process to be fully reaped so pipe handles close,
            # allowing reader threads to unblock from readline().
            with contextlib.suppress(Exception):
                process.wait(timeout=3)

        # Join threads to prevent leaks
        if stdout_thread is not None:
            stdout_thread.join(timeout=1)
        if stderr_thread is not None:
            stderr_thread.join(timeout=1)

        # Remove deny-config file and marker (guaranteed to run)
        self._remove_deny_config()
