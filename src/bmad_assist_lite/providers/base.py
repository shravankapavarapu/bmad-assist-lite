"""Abstract base class and data structures for CLI provider implementations."""

import logging
import os
import shutil
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any

from bmad_assist_lite.core.exceptions import ProviderTimeoutError
from bmad_assist_lite.providers.result_collector import ResultCollector

logger = logging.getLogger(__name__)

# Shared output locking for concurrent providers
_OUTPUT_LOCK = threading.Lock()

# Timeout contract constants (Story 7.3)
DEFAULT_TIMEOUT: int = 300
"""Default timeout in seconds when invoke() receives timeout=None."""

MIN_GRACE_PERIOD_SECONDS: int = 60
"""Floor for grace period duration in seconds."""

GRACE_PERIOD_RATIO: float = 0.25
"""Fraction of phase timeout used for grace period calculation."""

ACTIVE_STREAM_THRESHOLD: float = 30.0
"""Seconds of silence before a stream is considered stale."""

MIN_USEFUL_RESPONSE_CHARS: int = 200
"""Minimum partial text length worth returning as a useful result."""

# ANSI color codes for provider differentiation
PROVIDER_COLORS: tuple[str, ...] = (
    "\033[35m",  # Magenta
    "\033[32m",  # Green
    "\033[34m",  # Blue
    "\033[95m",  # Bright Magenta
    "\033[92m",  # Bright Green
    "\033[94m",  # Bright Blue
)
RESET_COLOR = "\033[0m"

# Common tool names shared by providers for allowed_tools restriction prompts
COMMON_TOOL_NAMES: frozenset[str] = frozenset(
    {"Edit", "Write", "Bash", "Glob", "Grep", "WebFetch", "WebSearch", "Read"}
)

# Known install locations per platform, checked when shutil.which() fails.
# Keyed by CLI name; values are lists of candidate paths per platform.
_KNOWN_CLI_PATHS: dict[str, list[Path]] = {
    "codex": (
        [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "OpenAI" / "Codex" / "bin",
            Path(os.environ.get("APPDATA", "")) / "npm",
        ]
        if sys.platform == "win32"
        else [
            Path.home() / ".local" / "bin",
            Path("/usr/local/bin"),
            Path.home() / ".npm-global" / "bin",
            Path.home() / ".npm" / "bin",
        ]
    ),
    "gemini": (
        [
            Path(os.environ.get("APPDATA", "")) / "npm",
        ]
        if sys.platform == "win32"
        else [
            Path.home() / ".local" / "bin",
            Path("/usr/local/bin"),
            Path.home() / ".npm-global" / "bin",
            Path.home() / ".npm" / "bin",
        ]
    ),
}

# Grace period polling interval in seconds
_GRACE_POLL_INTERVAL: float = 2.0


def resolve_cli_path(cli_name: str) -> str:
    """Resolve the full path to a CLI binary.

    Resolution order:
    1. Config override (``providers.cli_paths.<cli_name>``)
    2. ``shutil.which()`` (PATH lookup)
    3. Known platform-specific install locations
    """
    from bmad_assist_lite.core.exceptions import ProviderError

    try:
        from bmad_assist_lite.core.config import get_config

        config = get_config()
    except Exception:
        config = None

    if config and hasattr(config.providers, "cli_paths") and config.providers.cli_paths:
        override: str | None = getattr(config.providers.cli_paths, cli_name, None)
        if override:
            p = Path(override)
            if p.is_file():
                logger.debug("Resolved %s via config cli_paths: %s", cli_name, p)
                return str(p)
            logger.warning("Configured cli_paths.%s=%s not found, falling back", cli_name, override)

    found = shutil.which(cli_name)
    if found:
        logger.debug("Resolved %s via PATH: %s", cli_name, found)
        return found

    suffixes = [".cmd", ".exe", ""] if sys.platform == "win32" else [""]
    for directory in _KNOWN_CLI_PATHS.get(cli_name, []):
        for suffix in suffixes:
            candidate = directory / f"{cli_name}{suffix}"
            if candidate.is_file():
                logger.info("Found %s at known path: %s", cli_name, candidate)
                return str(candidate)

    raise ProviderError(
        f"{cli_name} CLI not found. Checked PATH and known install locations. "
        f"Set providers.cli_paths.{cli_name} in config to specify the path explicitly."
    )


def format_tag(tag: str, color_index: int | None) -> str:
    """Format a tag like [ASSISTANT] with optional color."""
    if color_index is not None and color_index >= 0:
        color = PROVIDER_COLORS[color_index % len(PROVIDER_COLORS)]
        return f"{color}[{tag}]{RESET_COLOR}"
    return f"[{tag}]"


def write_progress(line: str) -> None:
    """Write a progress line to stdout (and run log) with locking."""
    with _OUTPUT_LOCK:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode(errors="replace").decode(errors="replace"), flush=True)
        logger.info(line)


def extract_tool_details(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Extract human-readable details from tool input."""
    normalized_name = tool_name
    if tool_name == "run_shell_command":
        normalized_name = "Bash"
    elif tool_name == "read_file":
        normalized_name = "Read"
    elif tool_name == "edit_file":
        normalized_name = "Edit"
    elif tool_name == "write_file":
        normalized_name = "Write"
    elif tool_name in ("list_directory", "glob"):
        normalized_name = "Glob"
    elif tool_name in ("grep", "search_file_content"):
        normalized_name = "Grep"

    if normalized_name in ("Read", "Edit", "Write"):
        file_path: str = str(
            tool_input.get("file_path") or tool_input.get("path") or tool_input.get("file_id", "?")
        )
        if "/" in file_path:
            parts = file_path.split("/")
            if len(parts) > 3:
                file_path = ".../" + "/".join(parts[-3:])
        return file_path

    elif normalized_name == "Bash":
        command: str = str(tool_input.get("command") or tool_input.get("args", "?"))
        preview = command[:60].replace("\n", " ")
        if len(command) > 60:
            preview += "..."
        return preview

    elif normalized_name == "Grep":
        pattern = tool_input.get("pattern", "?")
        path = tool_input.get("path", ".")
        return f"'{pattern}' in {path}"

    elif normalized_name == "Glob":
        pattern = tool_input.get("pattern") or tool_input.get("path", "?")
        return f"'{pattern}'"

    return ""


def read_stream_lines(
    stream: Any,
    chunks: list[str],
    callback: Callable[[str], None] | None = None,
) -> None:
    """Read lines from stream, accumulating in chunks."""
    for line in iter(stream.readline, ""):
        chunks.append(line)
        if callback is not None:
            callback(line)
    stream.close()


def start_stream_reader_threads(
    process: Any,
    stdout_chunks: list[str],
    stderr_chunks: list[str],
    stdout_callback: Callable[[str], None] | None = None,
    stderr_callback: Callable[[str], None] | None = None,
) -> tuple[threading.Thread, threading.Thread]:
    """Start threads for concurrent stdout/stderr reading."""
    stdout_thread = threading.Thread(
        target=read_stream_lines,
        args=(process.stdout, stdout_chunks, stdout_callback),
    )
    stderr_thread = threading.Thread(
        target=read_stream_lines,
        args=(process.stderr, stderr_chunks, stderr_callback),
    )
    stdout_thread.start()
    stderr_thread.start()
    return stdout_thread, stderr_thread


class ExitStatus(Enum):
    """Semantic classification of process exit codes."""

    SUCCESS = auto()
    ERROR = auto()
    MISUSE = auto()
    CANNOT_EXECUTE = auto()
    NOT_FOUND = auto()
    INVALID_EXIT = auto()
    SIGNAL = auto()

    @classmethod
    def from_code(cls, exit_code: int) -> "ExitStatus":
        """Classify a process exit code into a semantic status."""
        if exit_code == 0:
            return cls.SUCCESS
        if exit_code == 2:
            return cls.MISUSE
        if exit_code == 126:
            return cls.CANNOT_EXECUTE
        if exit_code == 127:
            return cls.NOT_FOUND
        if exit_code == 128:
            return cls.INVALID_EXIT
        if exit_code > 128:
            return cls.SIGNAL
        return cls.ERROR

    @staticmethod
    def get_signal_number(exit_code: int) -> int | None:
        """Return the signal number from exit code, or None if not signal."""
        if exit_code > 128:
            return exit_code - 128
        return None


def resolve_settings_file(settings_path: str | None, base_dir: Path) -> Path | None:
    """Resolve settings file path from configuration."""
    if settings_path is None:
        return None
    path = Path(settings_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def validate_settings_file(
    settings_file: Path | None, provider_name: str, model: str
) -> Path | None:
    """Validate settings file existence, logging warning if missing."""
    if settings_file is None:
        return None
    if not settings_file.exists():
        logger.warning(
            "Settings file not found: path=%s, provider=%s, model=%s",
            settings_file,
            provider_name,
            model,
        )
        return None
    if not settings_file.is_file():
        logger.warning(
            "Settings path is not a file: path=%s, provider=%s, model=%s",
            settings_file,
            provider_name,
            model,
        )
        return None
    return settings_file


@dataclass(frozen=True)
class ProviderResult:
    """Result of a CLI provider invocation."""

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    model: str | None
    command: tuple[str, ...]
    provider_session_id: str | None = None
    timed_out: bool = False


class BaseProvider(ABC):
    """Abstract base class for CLI provider implementations.

    Uses the Template Method pattern: concrete invoke() defines the algorithm
    skeleton (create collector -> call _do_invoke() -> handle timeout -> cleanup).
    Subclasses override _do_invoke() and _cleanup() hooks.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier string."""
        ...

    @property
    def default_model(self) -> str | None:
        """Return the default model identifier, or None."""
        return None

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
        """Execute LLM provider with the given prompt.

        Template method that creates a ResultCollector, delegates to _do_invoke(),
        handles TimeoutError with grace period logic, and ensures _cleanup() is called.

        Args:
            prompt: The prompt text to send to the provider.
            model: Model identifier, or None for provider default.
            timeout: Timeout in seconds, or None for DEFAULT_TIMEOUT.
            settings_file: Optional path to provider settings file.
            cwd: Working directory for the provider process.
            allowed_tools: List of tool names the provider may use.
            color_index: Index for ANSI color differentiation in output.

        Returns:
            ProviderResult with timed_out=False on success, or timed_out=True
            if timeout occurred but sufficient partial text was captured.

        Raises:
            ProviderTimeoutError: If timeout occurs and partial text is insufficient.

        """
        # Resolve timeout=None to default int before any arithmetic
        resolved_timeout: int = timeout if timeout is not None else DEFAULT_TIMEOUT
        collector = ResultCollector()
        command: tuple[str, ...] = (self.provider_name, model or "default")
        start_time = time.monotonic()

        try:
            result = self._do_invoke(
                prompt,
                collector=collector,
                model=model,
                timeout=resolved_timeout,
                settings_file=settings_file,
                cwd=cwd,
                allowed_tools=allowed_tools,
                color_index=color_index,
            )
            return result
        except TimeoutError:
            return self._handle_timeout(
                collector, resolved_timeout, model, command, start_time
            )
        finally:
            try:
                self._cleanup()
            except Exception:
                logger.warning("_cleanup() raised an exception", exc_info=True)

    @abstractmethod
    def _do_invoke(
        self,
        prompt: str,
        *,
        collector: ResultCollector,
        model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        settings_file: Path | None = None,
        cwd: Path | None = None,
        allowed_tools: list[str] | None = None,
        color_index: int | None = None,
    ) -> ProviderResult:
        """Provider-specific invocation that must call collector.add() as chunks arrive.

        Implementations should raise TimeoutError when their internal timeout fires.
        The concrete invoke() catches TimeoutError and delegates to _handle_timeout().

        Args:
            prompt: The prompt text to send to the provider.
            collector: ResultCollector to accumulate streaming chunks into.
            model: Model identifier, or None for provider default.
            timeout: Timeout in seconds (always an int, resolved by invoke()).
            settings_file: Optional path to provider settings file.
            cwd: Working directory for the provider process.
            allowed_tools: List of tool names the provider may use.
            color_index: Index for ANSI color differentiation in output.

        Returns:
            ProviderResult on successful completion.

        Raises:
            TimeoutError: When the provider's internal timeout fires.

        """
        ...

    @abstractmethod
    def _cleanup(self) -> None:
        """Provider-specific resource teardown (kill process, close connection, etc.).

        Called in the finally block of invoke(), guaranteed to run on success,
        timeout, and unexpected exceptions.
        """
        ...

    @abstractmethod
    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from CLI output."""
        ...

    @abstractmethod
    def supports_model(self, model: str) -> bool:
        """Return True if this provider supports the given model."""
        ...

    def _handle_timeout(
        self,
        collector: ResultCollector,
        timeout: int,
        model: str | None,
        command: tuple[str, ...],
        start_time: float,
    ) -> ProviderResult:
        """Handle timeout with grace period decision logic.

        If the collector is actively streaming (last chunk within ACTIVE_STREAM_THRESHOLD),
        grants a proportional grace period. After grace (or if inactive), checks
        accumulated text length to decide between returning a partial result or raising.

        Args:
            collector: The ResultCollector with accumulated chunks.
            timeout: The resolved timeout value in seconds (never None).
            model: Model identifier for the ProviderResult.
            command: Command tuple for the ProviderResult.
            start_time: Monotonic timestamp from invoke() start for duration calculation.

        Returns:
            ProviderResult with timed_out=True if sufficient partial text exists.

        Raises:
            ProviderTimeoutError: If partial text is below MIN_USEFUL_RESPONSE_CHARS.

        """
        logger.warning(
            "Timeout fired after %ds for command=%s, model=%s",
            timeout,
            command,
            model,
        )

        # Check if stream is still active → grant grace period
        if collector.is_active(ACTIVE_STREAM_THRESHOLD):
            grace_seconds = max(MIN_GRACE_PERIOD_SECONDS, int(timeout * GRACE_PERIOD_RATIO))
            logger.warning(
                "Stream still active, granting %ds grace period (timeout=%ds)",
                grace_seconds,
                timeout,
            )
            self._wait_for_grace(collector, grace_seconds)
        else:
            logger.warning("Stream silent at timeout, no grace period granted")

        # After grace (or if not active), check accumulated text
        partial_text = collector.text
        duration_ms = int((time.monotonic() - start_time) * 1000)
        if len(partial_text) >= MIN_USEFUL_RESPONSE_CHARS:
            logger.warning(
                "Returning partial result: %d chars captured (duration=%dms)",
                len(partial_text),
                duration_ms,
            )
            return ProviderResult(
                stdout=partial_text,
                stderr="",
                exit_code=0,
                duration_ms=duration_ms,
                model=model,
                command=command,
                timed_out=True,
            )

        # Partial text too small — raise with partial result attached
        partial_result: ProviderResult | None = None
        if partial_text:
            partial_result = ProviderResult(
                stdout=partial_text,
                stderr="",
                exit_code=-1,
                duration_ms=duration_ms,
                model=model,
                command=command,
                timed_out=True,
            )

        raise ProviderTimeoutError(
            f"Provider timed out after {timeout}s with only {len(partial_text)} chars "
            f"(minimum {MIN_USEFUL_RESPONSE_CHARS} required)",
            partial_result=partial_result,
        )

    def _wait_for_grace(self, collector: ResultCollector, grace_seconds: int) -> None:
        """Poll collector activity for up to grace_seconds.

        Checks collector.is_active() in a loop with short sleep intervals.
        Returns None always — the grace period only extends the window for chunks
        to arrive. _handle_timeout() evaluates the accumulated text afterward.

        If the collector stops being active during grace (no new chunks within
        ACTIVE_STREAM_THRESHOLD), stops waiting early — the provider has stalled.

        Args:
            collector: The ResultCollector to monitor for activity.
            grace_seconds: Maximum duration to wait in seconds.

        """
        logger.warning(
            "Entering grace period: %ds, current chunks=%d",
            grace_seconds,
            collector.chunk_count,
        )
        start = time.monotonic()

        while (time.monotonic() - start) < grace_seconds:
            time.sleep(_GRACE_POLL_INTERVAL)

            if not collector.is_active(ACTIVE_STREAM_THRESHOLD):
                logger.info(
                    "Grace period: stream stalled after %.1fs, exiting early",
                    time.monotonic() - start,
                )
                return

            logger.info(
                "Grace period: stream still active, %.1fs elapsed of %ds, "
                "chunks=%d",
                time.monotonic() - start,
                grace_seconds,
                collector.chunk_count,
            )

        logger.info(
            "Grace period expired after %ds, final chunks=%d",
            grace_seconds,
            collector.chunk_count,
        )
