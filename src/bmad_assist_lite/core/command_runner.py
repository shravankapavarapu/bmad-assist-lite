"""Run shell commands and capture output for quality gate checks."""

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from bmad_assist_lite.providers._windows import get_subprocess_kwargs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    """Result of running a shell command."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

    @property
    def success(self) -> bool:
        return self.exit_code == 0


def run_command(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
    """Run a shell command and capture its output.

    Non-fatal: catches TimeoutExpired and FileNotFoundError,
    returning a CommandResult with non-zero exit code.
    """
    start = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=timeout,
            **get_subprocess_kwargs(),
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        return CommandResult(
            command=command,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.warning("Command timed out after %ds: %s", timeout, command)
        return CommandResult(
            command=command,
            exit_code=124,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            duration_ms=duration_ms,
        )
    except FileNotFoundError as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.warning("Command not found: %s (%s)", command, e)
        return CommandResult(
            command=command,
            exit_code=127,
            stdout="",
            stderr=f"Command not found: {e}",
            duration_ms=duration_ms,
        )
