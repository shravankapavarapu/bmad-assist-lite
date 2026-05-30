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
import uuid
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired
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
    format_tag,
    write_progress,
)
from bmad_assist_lite.providers.result_collector import ResultCollector

logger = logging.getLogger(__name__)

STDERR_TRUNCATE_LENGTH: int = 200

# Priority-to-severity mapping for Evidence Score integration (Story 10.4)
# Maps Codex structured JSON priority strings to (severity_label, emoji, score) tuples.
_PRIORITY_TO_SEVERITY: dict[str, tuple[str, str, float]] = {
    "P0": ("CRITICAL", "\U0001f534", 3.0),
    "P1": ("IMPORTANT", "\U0001f7e0", 1.0),
    "P2": ("MINOR", "\U0001f7e1", 0.3),
    "P3": ("MINOR", "\U0001f7e1", 0.3),
}

# Default number of clean review categories for clean pass formatting.
# Represents: architecture, correctness, testing, performance, security.
_DEFAULT_CLEAN_PASS_COUNT: int = 5

# Path to the bundled JSON Schema for structured review output
_REVIEW_SCHEMA_PATH: Path = (
    Path(__file__).resolve().parent.parent
    / "workflows"
    / "schemas"
    / "codex-review-schema.json"
)



def _format_codex_json_as_evidence_text(json_str: str) -> str | None:
    """Convert Codex structured JSON output to evidence score text format.

    Attempts to parse *json_str* as JSON matching the Codex review schema
    (``findings``, ``overall_verdict``, ``summary`` top-level keys).  If the
    JSON is valid and matches the schema, returns a formatted text string
    containing an Evidence Score Summary table that the existing evidence
    score parser can recognise.

    Args:
        json_str: Raw JSON string (potentially from ``--output-last-message``).

    Returns:
        Formatted evidence text if *json_str* is valid Codex review JSON,
        or ``None`` if it is not valid JSON or does not match the schema.

    """
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None

    # Validate expected top-level keys
    if not isinstance(data, dict):
        return None
    if not all(k in data for k in ("findings", "overall_verdict", "summary")):
        return None

    findings = data.get("findings", [])
    if not isinstance(findings, list):
        return None

    overall_verdict: str = data.get("overall_verdict", "")
    summary: str = data.get("summary", "")

    lines: list[str] = [
        "## Evidence Score Summary",
        "",
        "| Severity | Description | Source | Score |",
        "|----------|-------------|--------|-------|",
    ]

    for finding in findings:
        if not isinstance(finding, dict):
            continue
        title = finding.get("title", "").replace("|", "/").replace("\n", " ")
        body = finding.get("body", "").replace("|", "/").replace("\n", " ")
        priority = finding.get("priority", "")

        severity_info = _PRIORITY_TO_SEVERITY.get(priority)
        if severity_info is None:
            logger.warning(
                "Unknown Codex priority %r, defaulting to MINOR",
                priority,
            )
            severity_info = ("MINOR", "\U0001f7e1", 0.3)

        severity_label, emoji, score = severity_info

        # Extract source from code_location.file_path (optional)
        code_location = finding.get("code_location")
        source = "—"  # em-dash default
        if isinstance(code_location, dict):
            file_path = code_location.get("file_path")
            if file_path:
                source = str(file_path).replace("|", "/")

        description = f"{title}: {body}" if title and body else (title or body or "")
        lines.append(
            f"| {emoji} {severity_label} | {description} | {source} | +{score} |"
        )

    # Clean pass: empty findings + PASS verdict
    if not findings and overall_verdict == "PASS":
        lines.append(
            f"| \U0001f7e2 CLEAN PASS | {_DEFAULT_CLEAN_PASS_COUNT} |"
        )

    # Append verdict and summary
    lines.append("")
    lines.append(f"**Overall Verdict:** {overall_verdict}")
    lines.append("")
    lines.append(f"**Summary:** {summary}")

    return "\n".join(lines)


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
        self._temp_output_path: Path | None = None
        self._structured_output: str | None = None

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
        """Extract response text from provider result.

        When cached structured JSON is available (from ``_do_invoke()``),
        attempts to convert Codex review JSON into evidence score text
        format via ``_format_codex_json_as_evidence_text()``.  If the JSON
        matches the review schema, returns the formatted evidence text.
        If conversion returns ``None`` (not review JSON), falls back to the
        raw cached JSON string.  Falls back to ``result.stdout.strip()``
        if no structured output was captured at all.
        """
        if self._structured_output is not None:
            formatted = _format_codex_json_as_evidence_text(
                self._structured_output,
            )
            if formatted is not None:
                logger.debug(
                    "parse_output: converted structured JSON to "
                    "evidence text format",
                )
                return formatted
            logger.debug(
                "parse_output: structured JSON is not review schema, "
                "returning raw JSON",
            )
            return self._structured_output

        logger.debug("parse_output: using stdout text fallback")
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
        ]

        # Add --output-schema and --output-last-message for structured output
        if _REVIEW_SCHEMA_PATH.is_file():
            temp_dir = (
                (cwd or Path.cwd()) / ".bmad-assist-lite" / "cache"
            )
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_output = (
                temp_dir / f"codex-review-{uuid.uuid4().hex[:8]}.json"
            )
            self._temp_output_path = temp_output
            command.extend([
                "--output-schema",
                str(_REVIEW_SCHEMA_PATH),
                "--output-last-message",
                str(temp_output),
            ])
            logger.debug(
                "Structured output: schema=%s, output=%s",
                _REVIEW_SCHEMA_PATH,
                temp_output,
            )
        else:
            logger.warning(
                "Review schema not found at %s, proceeding without "
                "--output-schema (structured output disabled)",
                _REVIEW_SCHEMA_PATH,
            )

        command.append(final_prompt)

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

        # Read structured output file before _cleanup() deletes it
        if self._temp_output_path is not None:
            try:
                content = self._temp_output_path.read_text(encoding="utf-8")
                if content.strip():
                    # Validate it is parseable JSON before caching
                    json.loads(content)
                    self._structured_output = content.strip()
                    logger.debug(
                        "Cached structured JSON output from %s",
                        self._temp_output_path,
                    )
                else:
                    logger.debug(
                        "Structured output file is empty, will use stdout fallback",
                    )
            except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Failed to read structured output from %s (%s), "
                    "will use stdout fallback",
                    self._temp_output_path,
                    exc,
                )

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
        """Terminate subprocess, join reader threads, and remove temp files.

        Called by the base class invoke() in a finally block -- guaranteed to run
        on success, timeout, and unexpected exceptions.

        If the subprocess is still running (poll() returns None), calls
        kill_process() for platform-safe termination. Joins reader threads
        with a short timeout to prevent thread leaks. Removes the temp output
        file created for ``--output-last-message`` if it exists.
        """
        process = self._current_process
        stdout_thread = self._stdout_thread
        stderr_thread = self._stderr_thread
        temp_output = self._temp_output_path

        # Reset state first to prevent re-entry
        self._current_process = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._temp_output_path = None
        # Note: _structured_output is intentionally NOT reset here.
        # It must survive _cleanup() so parse_output() can read it afterward.

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

        # Clean up temp output file (may be locked by antivirus on Windows)
        if temp_output is not None:
            with contextlib.suppress(OSError):
                temp_output.unlink(missing_ok=True)
