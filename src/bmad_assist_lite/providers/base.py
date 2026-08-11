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
from bmad_assist_lite.providers.result_collector import CallMetrics, ResultCollector

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

# Read-only tool set for multi-LLM validator/reviewer phases (validate_story,
# code_review). Deliberately excludes Bash and all write tools so that parallel
# read-only phases cannot run shell commands or mutate the workspace. This is the
# multi-LLM safety constraint expressed as a single shared allowlist, so the
# validator and reviewer handlers cannot drift apart.
READ_ONLY_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep")

# Provider→binary-name mapping: ordered tuples of binary names to try per provider.
# After this refactor, resolve_cli_path()'s cli_name parameter is effectively a
# **provider name** (not a binary name), and _KNOWN_CLI_PATHS keys likewise shift
# from CLI/binary names to provider names.  For existing providers (codex, gemini)
# these are identical, so no behavioral change occurs.
_PROVIDER_BINARY_NAMES: dict[str, tuple[str, ...]] = {
    "codex": ("codex",),
    "cursor": ("cursor-agent", "agent"),
    "gemini": ("gemini",),
}

# Known install locations per platform, checked when shutil.which() fails.
# Keyed by provider name; values are lists of candidate directories per platform.
_KNOWN_CLI_PATHS: dict[str, list[Path]] = {
    "claude": (
        # Claude Code's native installer places the binary in ~/.local/bin on
        # all platforms; %APPDATA%\npm covers an npm-global install on Windows.
        [
            Path.home() / ".local" / "bin",
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
    "cursor": (
        (
            # Guard: skip Windows known-path probing if LOCALAPPDATA is unset/empty
            # to avoid creating a relative path from Path("").
            [Path(os.environ["LOCALAPPDATA"]) / "cursor-agent"]
            if os.environ.get("LOCALAPPDATA")
            else []
        )
        if sys.platform == "win32"
        else [
            Path.home() / ".local" / "bin",
            Path("/usr/local/bin"),
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

    ``cli_name`` is a **provider name** (e.g. ``"cursor"``), not necessarily
    the binary name on disk.  The mapping from provider name to one or more
    candidate binary names lives in ``_PROVIDER_BINARY_NAMES``.  Providers
    not listed there default to ``(cli_name,)`` for backward compatibility.

    Resolution order:
    1. Config override (``providers.cli_paths.<cli_name>``) — single explicit path
    2. ``shutil.which()`` (PATH lookup) — tries each binary name in order
    3. Known platform-specific install locations — tries each binary name in order
    """
    from bmad_assist_lite.core.exceptions import ProviderError

    try:
        from bmad_assist_lite.core.config import get_config

        config = get_config()
    except Exception:
        config = None

    # Tier 1: Config override — single explicit path, no multi-name iteration
    if config and hasattr(config.providers, "cli_paths") and config.providers.cli_paths:
        override: str | None = getattr(config.providers.cli_paths, cli_name, None)
        if override:
            p = Path(override)
            if p.is_file():
                logger.debug("Resolved %s via config cli_paths: %s", cli_name, p)
                return str(p)
            logger.warning("Configured cli_paths.%s=%s not found, falling back", cli_name, override)

    # Resolve ordered binary names for this provider (defaults to (cli_name,))
    binary_names = _PROVIDER_BINARY_NAMES.get(cli_name, (cli_name,))

    # Tier 2: PATH lookup — try each binary name in preference order
    for binary_name in binary_names:
        found = shutil.which(binary_name)
        if found:
            logger.debug("Resolved %s via PATH: %s (binary=%s)", cli_name, found, binary_name)
            return found

    # Tier 3: Known platform install locations — try each binary name per directory
    suffixes = [".cmd", ".exe", ""] if sys.platform == "win32" else [""]
    for directory in _KNOWN_CLI_PATHS.get(cli_name, []):
        for binary_name in binary_names:
            for suffix in suffixes:
                candidate = directory / f"{binary_name}{suffix}"
                if candidate.is_file():
                    logger.info(
                        "Found %s at known path: %s (binary=%s)",
                        cli_name,
                        candidate,
                        binary_name,
                    )
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


WINDOWS_COMMAND_NOT_FOUND: int = 9009
"""``cmd.exe`` exit code for "is not recognized as an internal or external command".

126/127 are *shell* conventions and are absent from ``cmd.exe``, which is the default
shell for every ``shell=True`` subprocess on this tool's primary platform. Without this
code a missing command on Windows classifies as a generic error.
"""


def _is_windows_platform(platform: str | None) -> bool:
    """Return True when the given (or current) platform string is Windows."""
    return (platform if platform is not None else sys.platform).startswith("win")


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
    def from_code(cls, exit_code: int, platform: str | None = None) -> "ExitStatus":
        """Classify a process exit code into a semantic status.

        The mapping is platform-aware: ``cmd.exe`` signals command-not-found with
        9009 rather than 127, and Windows has no wait-status signal encoding, so the
        ``> 128`` signal rule is POSIX-only. 126/127 stay mapped on every platform
        because :func:`bmad_assist_lite.core.command_runner.run_command` synthesises
        127 itself for ``FileNotFoundError`` regardless of platform.

        Args:
            exit_code: Raw process exit code.
            platform: Platform string to classify for; defaults to ``sys.platform``.

        Returns:
            The semantic :class:`ExitStatus` for the code on that platform.

        """
        is_windows = _is_windows_platform(platform)
        if exit_code == 0:
            return cls.SUCCESS
        if exit_code == 2:
            return cls.MISUSE
        if is_windows and exit_code == WINDOWS_COMMAND_NOT_FOUND:
            return cls.NOT_FOUND
        if exit_code == 126:
            return cls.CANNOT_EXECUTE
        if exit_code == 127:
            return cls.NOT_FOUND
        if exit_code == 128:
            return cls.INVALID_EXIT
        if not is_windows and exit_code > 128:
            return cls.SIGNAL
        return cls.ERROR

    @staticmethod
    def get_signal_number(exit_code: int, platform: str | None = None) -> int | None:
        """Return the signal number from exit code, or None if not signal."""
        if _is_windows_platform(platform):
            return None
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
    """Result of a CLI provider invocation.

    The trailing metric fields are optional per-call instrumentation. Providers
    that cannot report a metric leave it ``None`` — never ``0``, which would
    silently corrupt any aggregate built from these values.

    Attributes:
        api_duration_ms: Provider-reported API time, distinct from the locally
            measured wall-clock ``duration_ms``.
        input_tokens: Uncached prompt tokens consumed by the call. This is the
            *remainder* after cache hits, not the full prompt size — a total
            prompt is ``input_tokens + cache_read_tokens + cache_creation_tokens``.
        output_tokens: Completion tokens produced by the call.
        cache_read_tokens: Prompt tokens served from the provider's prompt cache.
        cache_creation_tokens: Prompt tokens written into the provider's prompt
            cache by this call.
        total_cost_usd: Provider-reported cost of the call in USD.

    """

    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    model: str | None
    command: tuple[str, ...]
    provider_session_id: str | None = None
    timed_out: bool = False
    api_duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    total_cost_usd: float | None = None


def _timeout_metric_kwargs(metrics: CallMetrics | None) -> dict[str, Any]:
    """Map recorded call metrics onto ProviderResult keyword arguments.

    Args:
        metrics: Metrics the provider recorded on the collector, or None if it
            never reported any.

    Returns:
        The metric keyword arguments, or an empty mapping when no metrics were
        recorded — which leaves every metric field at its ``None`` default. The
        distinction that matters is None versus 0: a timed-out call reporting 0
        tokens is indistinguishable from a cheap call, whereas None is visibly
        missing and can be excluded from an aggregate.

    """
    if metrics is None:
        return {}
    return {
        "provider_session_id": metrics.session_id,
        "api_duration_ms": metrics.api_duration_ms,
        "input_tokens": metrics.input_tokens,
        "output_tokens": metrics.output_tokens,
        "cache_read_tokens": metrics.cache_read_tokens,
        "cache_creation_tokens": metrics.cache_creation_tokens,
        "total_cost_usd": metrics.total_cost_usd,
    }


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
        effort: str | None = None,
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
            effort: Reasoning-effort hint, forwarded to _do_invoke() verbatim.
                Provider-specific; providers that do not support it ignore it.
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
        result: ProviderResult | None = None
        timed_out = False

        try:
            try:
                result = self._do_invoke(
                    prompt,
                    collector=collector,
                    model=model,
                    timeout=resolved_timeout,
                    settings_file=settings_file,
                    cwd=cwd,
                    allowed_tools=allowed_tools,
                    effort=effort,
                    color_index=color_index,
                )
            except TimeoutError:
                timed_out = True
                result = self._handle_timeout(
                    collector, resolved_timeout, model, command, start_time
                )
            return result
        finally:
            try:
                self._cleanup()
            except Exception:
                logger.warning("_cleanup() raised an exception", exc_info=True)
            # Hand the call's measurements to whichever phase is open. This is the
            # one hook that sees every provider call, including the multi-LLM
            # fan-out, because every provider reaches the CLI through this method.
            # A raised timeout never builds a ProviderResult, so the flag is taken
            # from the control flow rather than from the (absent) result — losing
            # it would let the most expensive phase in a run leave no trace.
            self._record_call_metrics(
                model=model or self.default_model,
                result=result,
                timed_out=timed_out,
                start_time=start_time,
            )

    def _record_call_metrics(
        self,
        *,
        model: str | None,
        result: "ProviderResult | None",
        timed_out: bool,
        start_time: float,
    ) -> None:
        """Report this invocation's metrics to the open phase, if any.

        Purely additive instrumentation: it reads the result, never changes it,
        and swallows its own failures. It runs on every phase's hot path, so a
        raise here would be able to end a multi-hour run.

        Args:
            model: Model identifier for the call.
            result: The provider result, or None when the call raised.
            timed_out: True when the provider's timeout fired.
            start_time: Monotonic timestamp taken at the start of invoke().

        """
        try:
            from bmad_assist_lite.core.phase_metrics import record_provider_call

            record_provider_call(
                model=model,
                duration_ms=(
                    result.duration_ms
                    if result is not None
                    else int((time.monotonic() - start_time) * 1000)
                ),
                api_duration_ms=getattr(result, "api_duration_ms", None),
                input_tokens=getattr(result, "input_tokens", None),
                output_tokens=getattr(result, "output_tokens", None),
                cache_read_tokens=getattr(result, "cache_read_tokens", None),
                cache_creation_tokens=getattr(result, "cache_creation_tokens", None),
                total_cost_usd=getattr(result, "total_cost_usd", None),
                timed_out=timed_out or bool(getattr(result, "timed_out", False)),
            )
        except Exception:
            logger.warning("Failed to record provider call metrics", exc_info=True)

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
        effort: str | None = None,
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
            effort: Reasoning-effort hint. Provider-specific; ignore if
                unsupported. Implementations that cannot act on it must still
                accept the keyword so invoke() can forward it unconditionally.
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

        # Carry whatever metrics the provider managed to record before the
        # timeout. A timed-out call that reported zero tokens would bias any
        # aggregate downward exactly where it hurts most: the slowest, most
        # expensive phases are the ones that time out, so their disappearance
        # from the sample makes a lever that slowed things down look like it
        # reduced tokens. Absent metrics stay None so the gap is detectable.
        metric_kwargs = _timeout_metric_kwargs(collector.metrics)

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
                **metric_kwargs,
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
                **metric_kwargs,
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
                "Grace period: stream still active, %.1fs elapsed of %ds, chunks=%d",
                time.monotonic() - start,
                grace_seconds,
                collector.chunk_count,
            )

        logger.info(
            "Grace period expired after %ds, final chunks=%d",
            grace_seconds,
            collector.chunk_count,
        )
