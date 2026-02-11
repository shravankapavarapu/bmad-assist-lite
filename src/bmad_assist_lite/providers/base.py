"""Abstract base class and data structures for CLI provider implementations."""

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Shared output locking for concurrent providers
_OUTPUT_LOCK = threading.Lock()

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


def format_tag(tag: str, color_index: int | None) -> str:
    """Format a tag like [ASSISTANT] with optional color."""
    if color_index is not None and color_index >= 0:
        color = PROVIDER_COLORS[color_index % len(PROVIDER_COLORS)]
        return f"{color}[{tag}]{RESET_COLOR}"
    return f"[{tag}]"


def write_progress(line: str) -> None:
    """Write a progress line to stdout with locking."""
    with _OUTPUT_LOCK:
        print(line, flush=True)


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
            settings_file, provider_name, model,
        )
        return None
    if not settings_file.is_file():
        logger.warning(
            "Settings path is not a file: path=%s, provider=%s, model=%s",
            settings_file, provider_name, model,
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


class BaseProvider(ABC):
    """Abstract base class for CLI provider implementations.

    Simplified invoke() signature with 6 keyword args (vs 12 in bmad-assist).
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    def default_model(self) -> str | None:
        return None

    @abstractmethod
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
        """Execute LLM provider with the given prompt."""
        ...

    @abstractmethod
    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from CLI output."""
        ...

    @abstractmethod
    def supports_model(self, model: str) -> bool:
        ...

    def cancel(self) -> None:
        """Cancel any running operation. Default no-op."""
        pass
