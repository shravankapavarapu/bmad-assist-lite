"""Bootstrap worktrees for parallel story execution.

Provide a three-phase bootstrap pipeline (copy -> setup -> validation)
that prepares git worktrees with the necessary files and dependencies
before an LLM loop begins executing in them.
"""

import contextlib
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.providers._windows import get_subprocess_kwargs, terminate_process

logger = logging.getLogger(__name__)


class BootstrapResult(BaseModel):
    """Result of a bootstrap operation.

    Immutable result object communicating success/failure of bootstrap
    phases. This is the primary error communication mechanism — exceptions
    are not used for expected bootstrap failures.
    """

    model_config = ConfigDict(frozen=True)

    success: bool
    failed_phase: Literal["copy", "setup", "validation"] | None = None
    error_message: str | None = None
    output: str = ""


def copy_files_to_worktree(
    files: list[str],
    project_root: Path,
    worktree_path: Path,
    strict: bool = False,
) -> BootstrapResult:
    """Copy configured files and directories from project root to worktree.

    Iterates the ``files`` list, resolving each entry against
    ``project_root``. Uses ``pathlib.Path.is_dir()`` on the resolved
    source path for robust directory detection (trailing ``/`` may serve
    as a user hint but is not the sole mechanism).

    Args:
        files: Relative paths of files/directories to copy.
        project_root: Absolute path to the project root.
        worktree_path: Absolute path to the target worktree.
        strict: If True, fail on any missing source file/directory.

    Returns:
        BootstrapResult indicating success or failure.

    """
    output_lines: list[str] = []

    for entry in files:
        # Strip trailing slash for path resolution — is_dir() handles detection
        clean_entry = entry.rstrip("/").rstrip("\\")

        if not clean_entry or clean_entry == ".":
            msg = f"[BOOTSTRAP] Invalid copy entry (empty or '.'): {entry!r}"
            logger.error(msg)
            if strict:
                return BootstrapResult(
                    success=False,
                    failed_phase="copy",
                    error_message=msg,
                    output="\n".join(output_lines),
                )
            output_lines.append(f"WARNING: {msg}")
            continue

        source = (project_root / clean_entry).resolve()
        destination = (worktree_path / clean_entry).resolve()

        # Validate source stays within project_root
        resolved_root = project_root.resolve()
        resolved_worktree = worktree_path.resolve()
        if not str(source).startswith(str(resolved_root) + os.sep) and source != resolved_root:
            msg = f"[BOOTSTRAP] Path escapes project root: {entry!r}"
            logger.error(msg)
            if strict:
                return BootstrapResult(
                    success=False,
                    failed_phase="copy",
                    error_message=msg,
                    output="\n".join(output_lines),
                )
            output_lines.append(f"WARNING: {msg}")
            continue

        # Validate destination stays within worktree
        if not str(destination).startswith(
            str(resolved_worktree) + os.sep
        ) and destination != resolved_worktree:
            msg = f"[BOOTSTRAP] Destination escapes worktree: {entry!r}"
            logger.error(msg)
            if strict:
                return BootstrapResult(
                    success=False,
                    failed_phase="copy",
                    error_message=msg,
                    output="\n".join(output_lines),
                )
            output_lines.append(f"WARNING: {msg}")
            continue

        if not source.exists():
            msg = f"[BOOTSTRAP] Source not found: {source}"
            if strict:
                logger.error(msg)
                return BootstrapResult(
                    success=False,
                    failed_phase="copy",
                    error_message=msg,
                    output="\n".join(output_lines),
                )
            logger.warning(msg)
            output_lines.append(f"WARNING: {msg}")
            continue

        # Create parent directories just-in-time after source verification
        try:
            if source.is_dir():
                destination.parent.mkdir(parents=True, exist_ok=True)
                logger.info("[BOOTSTRAP] Copying directory: %s -> %s", source, destination)
                shutil.copytree(source, destination, dirs_exist_ok=True)
                output_lines.append(f"Copied directory: {entry}")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                logger.info("[BOOTSTRAP] Copying file: %s -> %s", source, destination)
                shutil.copy2(source, destination)
                output_lines.append(f"Copied file: {entry}")
        except OSError as exc:
            msg = f"[BOOTSTRAP] Copy failed for {entry!r}: {exc}"
            logger.error(msg)
            if strict:
                return BootstrapResult(
                    success=False,
                    failed_phase="copy",
                    error_message=msg,
                    output="\n".join(output_lines),
                )
            output_lines.append(f"WARNING: {msg}")
            continue

    return BootstrapResult(
        success=True,
        output="\n".join(output_lines),
    )


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Kill the entire process tree for a subprocess.

    Uses platform-appropriate cleanup to prevent orphaned child
    processes when ``shell=True`` is used.

    Args:
        process: The subprocess.Popen instance to kill.

    """
    if process.pid is None:
        return

    terminate_process(process.pid)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # Fallback: use platform-appropriate tree kill, not process.kill()
        # which would only kill the shell on Unix with shell=True
        if sys.platform == "win32":
            terminate_process(process.pid)
        else:
            try:
                pgid = os.getpgid(process.pid)
                os.killpg(pgid, 9)  # SIGKILL
            except (ProcessLookupError, OSError):
                pass
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)


def run_setup_commands(
    commands: list[str],
    worktree_path: Path,
    timeout: int = 120,
) -> BootstrapResult:
    """Run setup commands sequentially in the worktree.

    Each command runs via ``subprocess.Popen()`` with ``shell=True``.
    If any command fails (non-zero exit), remaining commands are skipped.
    On timeout, the entire process tree is killed.

    Args:
        commands: Shell commands to execute sequentially.
        worktree_path: Working directory for command execution.
        timeout: Maximum seconds per command before timeout.

    Returns:
        BootstrapResult indicating success or failure.

    """
    output_lines: list[str] = []

    for i, cmd in enumerate(commands):
        logger.info("[BOOTSTRAP] Running setup command %d/%d: %s", i + 1, len(commands), cmd)
        process = subprocess.Popen(
            cmd,
            cwd=str(worktree_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            shell=True,
            **get_subprocess_kwargs(),
        )

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the process tree first, then drain remaining output
            _kill_process_tree(process)
            try:
                remaining_stdout, remaining_stderr = process.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                remaining_stdout, remaining_stderr = "", ""
            msg = (
                f"[BOOTSTRAP] Setup command timed out after {timeout}s: {cmd}"
            )
            logger.error(msg)
            output_lines.append(f"TIMEOUT: {cmd}")
            if remaining_stdout:
                output_lines.append(remaining_stdout.rstrip())
            if remaining_stderr:
                output_lines.append(remaining_stderr.rstrip())
            return BootstrapResult(
                success=False,
                failed_phase="setup",
                error_message=msg,
                output="\n".join(output_lines),
            )

        if stdout:
            output_lines.append(stdout.rstrip())
        if stderr:
            output_lines.append(stderr.rstrip())

        if process.returncode != 0:
            msg = (
                f"[BOOTSTRAP] Setup command failed (exit {process.returncode}): {cmd}"
            )
            logger.error(msg)
            return BootstrapResult(
                success=False,
                failed_phase="setup",
                error_message=msg,
                output="\n".join(output_lines),
            )

        logger.info("[BOOTSTRAP] Setup command %d/%d succeeded", i + 1, len(commands))

    return BootstrapResult(
        success=True,
        output="\n".join(output_lines),
    )


def run_validation_command(
    command: str,
    worktree_path: Path,
    timeout: int = 120,
) -> BootstrapResult:
    """Run a single validation command in the worktree.

    Uses the same ``subprocess.Popen()`` pattern as setup commands with
    platform-appropriate process tree cleanup on timeout.

    Args:
        command: Shell command to execute for validation.
        worktree_path: Working directory for command execution.
        timeout: Maximum seconds before timeout.

    Returns:
        BootstrapResult indicating success or failure.

    """
    logger.info("[BOOTSTRAP] Running validation command: %s", command)
    process = subprocess.Popen(
        command,
        cwd=str(worktree_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        shell=True,
        **get_subprocess_kwargs(),
    )

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the process tree first, then drain remaining output
        _kill_process_tree(process)
        try:
            remaining_stdout, remaining_stderr = process.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            remaining_stdout, remaining_stderr = "", ""
        msg = (
            f"[BOOTSTRAP] Validation command timed out after {timeout}s: {command}"
        )
        logger.error(msg)
        timeout_output: list[str] = [f"TIMEOUT: {command}"]
        if remaining_stdout:
            timeout_output.append(remaining_stdout.rstrip())
        if remaining_stderr:
            timeout_output.append(remaining_stderr.rstrip())
        return BootstrapResult(
            success=False,
            failed_phase="validation",
            error_message=msg,
            output="\n".join(timeout_output),
        )

    output_parts: list[str] = []
    if stdout:
        output_parts.append(stdout.rstrip())
    if stderr:
        output_parts.append(stderr.rstrip())
    output = "\n".join(output_parts)

    if process.returncode != 0:
        msg = (
            f"[BOOTSTRAP] Validation command failed (exit {process.returncode}): {command}"
        )
        logger.error(msg)
        return BootstrapResult(
            success=False,
            failed_phase="validation",
            error_message=msg,
            output=output,
        )

    logger.info("[BOOTSTRAP] Validation command succeeded")
    return BootstrapResult(
        success=True,
        output=output,
    )


def bootstrap_worktree(
    project_root: Path,
    worktree_path: Path,
    config: ParallelConfig,
    validate: bool = True,
) -> BootstrapResult:
    """Orchestrate the three-phase worktree bootstrap pipeline.

    Executes phases in order: copy -> setup -> validation (if
    ``validate=True``). Returns immediately on any phase failure.
    Short-circuits with success when no bootstrap fields are configured.

    Args:
        project_root: Absolute path to the project root.
        worktree_path: Absolute path to the target worktree.
        config: Parallel execution configuration.
        validate: If True, run the validation command phase.

    Returns:
        BootstrapResult indicating overall success or first failure.

    """
    # No-op when unconfigured
    has_copy = bool(config.copy_to_worktree)
    has_setup = bool(config.setup_commands)
    has_validation = config.validation_command is not None

    if not has_copy and not has_setup and not has_validation:
        logger.debug("[BOOTSTRAP] No bootstrap configuration — skipping")
        return BootstrapResult(success=True)

    accumulated_output: list[str] = []

    # Phase 1: Copy files
    if has_copy:
        logger.info("[BOOTSTRAP] Phase 1/3: Copying files to worktree")
        copy_result = copy_files_to_worktree(
            files=config.copy_to_worktree,
            project_root=project_root,
            worktree_path=worktree_path,
            strict=config.copy_strict,
        )
        if copy_result.output:
            accumulated_output.append(copy_result.output)
        if not copy_result.success:
            return BootstrapResult(
                success=False,
                failed_phase=copy_result.failed_phase,
                error_message=copy_result.error_message,
                output="\n".join(accumulated_output),
            )

    # Phase 2: Setup commands
    if has_setup:
        logger.info("[BOOTSTRAP] Phase 2/3: Running setup commands")
        setup_result = run_setup_commands(
            commands=config.setup_commands,
            worktree_path=worktree_path,
            timeout=config.bootstrap_timeout,
        )
        if setup_result.output:
            accumulated_output.append(setup_result.output)
        if not setup_result.success:
            return BootstrapResult(
                success=False,
                failed_phase=setup_result.failed_phase,
                error_message=setup_result.error_message,
                output="\n".join(accumulated_output),
            )

    # Phase 3: Validation (if enabled)
    if validate and has_validation and config.validation_command is not None:
        logger.info("[BOOTSTRAP] Phase 3/3: Running validation command")
        validation_result = run_validation_command(
            command=config.validation_command,
            worktree_path=worktree_path,
            timeout=config.bootstrap_timeout,
        )
        if validation_result.output:
            accumulated_output.append(validation_result.output)
        if not validation_result.success:
            return BootstrapResult(
                success=False,
                failed_phase=validation_result.failed_phase,
                error_message=validation_result.error_message,
                output="\n".join(accumulated_output),
            )

    logger.info("[BOOTSTRAP] Worktree bootstrap completed successfully")
    return BootstrapResult(
        success=True,
        output="\n".join(accumulated_output),
    )
