"""Run shell commands and capture output for quality gate checks."""

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from bmad_assist_lite.providers._windows import get_subprocess_kwargs, terminate_process
from bmad_assist_lite.providers.base import ExitStatus

logger = logging.getLogger(__name__)

COMMAND_NOT_FOUND_EXIT_CODE: int = 127
"""Synthesised for ``FileNotFoundError`` on every platform.

``ExitStatus.from_code`` therefore keeps 127 mapped to ``NOT_FOUND`` on Windows too,
alongside ``cmd.exe``'s native 9009.
"""

COMMAND_TIMEOUT_EXIT_CODE: int = 124


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
        """Return True if the command exited with code 0."""
        return self.exit_code == 0

    @property
    def exit_status(self) -> ExitStatus:
        """Classify this result's exit code with the shared semantic classifier.

        This is the single classification vocabulary in the tool — no module on the
        gate path may define a second exit-code map.
        """
        return ExitStatus.from_code(self.exit_code)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


def clean_test_output(text: str) -> str:
    """Strip ANSI codes and passing test lines from test runner output.

    Keeps: failing test/file lines, FAIL assertion blocks, test summary,
    stderr output.
    Removes: all passing test lines (✓ markers at any indent level).
    """
    text = strip_ansi(text)
    kept: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        # Skip all lines starting with ✓ (passing tests and passing files)
        if stripped.startswith("✓"):
            continue
        kept.append(line)
    return "\n".join(kept)


def run_command(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
    """Run a shell command and capture its output.

    Uses Popen instead of subprocess.run so we can kill the entire process
    tree on timeout (Windows shell=True only kills cmd.exe, leaving children).

    Non-fatal: catches TimeoutExpired and FileNotFoundError,
    returning a CommandResult with non-zero exit code.
    """
    start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            **get_subprocess_kwargs(),
        )
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill entire process tree, not just the shell
            terminate_process(proc.pid)
            # Drain any remaining pipe data after kill
            try:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()
                stdout_bytes, stderr_bytes = b"", b""
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.warning("Command timed out after %ds: %s", timeout, command)
            return CommandResult(
                command=command,
                exit_code=COMMAND_TIMEOUT_EXIT_CODE,
                stdout=stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
                stderr=f"Command timed out after {timeout}s",
                duration_ms=duration_ms,
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        return CommandResult(
            command=command,
            exit_code=proc.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
        )
    except FileNotFoundError as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.warning("Command not found: %s (%s)", command, e)
        return CommandResult(
            command=command,
            exit_code=COMMAND_NOT_FOUND_EXIT_CODE,
            stdout="",
            stderr=f"Command not found: {e}",
            duration_ms=duration_ms,
        )
