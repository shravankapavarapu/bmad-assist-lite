"""Git subprocess wrapper for parallel story execution.

Provides a platform-safe git command wrapper that all parallel components
use for consistent error handling. All git operations in the parallel
module MUST use ``_run_git()`` instead of raw ``subprocess.run()``.
"""

import logging
import subprocess
from pathlib import Path

from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.providers._windows import get_subprocess_kwargs

logger = logging.getLogger(__name__)


def _run_git(
    args: list[str],
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute a git command with platform-safe subprocess settings.

    Args:
        args: Git subcommand and arguments (e.g. ``["status"]``).
        cwd: Working directory for the git command.
        check: If True (default), raise ``ParallelError`` on non-zero exit.

    Returns:
        The ``CompletedProcess`` result from ``subprocess.run``.

    Raises:
        ParallelError: If ``args`` is empty, if ``check=True`` and the
            command exits with a non-zero return code, or if the git
            executable cannot be found or executed.

    """
    if not args:
        raise ParallelError("_run_git requires at least one argument")

    logger.debug("git %s (cwd=%s)", " ".join(args), cwd)

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            **get_subprocess_kwargs(),
        )
    except FileNotFoundError as exc:
        raise ParallelError("git executable not found on PATH") from exc
    except OSError as exc:
        raise ParallelError(f"failed to execute git: {exc}") from exc

    if check and result.returncode != 0:
        raise ParallelError(f"git {args[0]} failed: {result.stderr.strip()}")

    return result


def get_current_branch(cwd: Path) -> str:
    """Return the name of the currently checked-out branch.

    Args:
        cwd: Repository working directory.

    Returns:
        Branch name as a stripped string. Returns the literal string
        ``"HEAD"`` when in detached HEAD state.

    Raises:
        ParallelError: If the underlying git command fails.

    """
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    return result.stdout.strip()


def is_protected_branch(branch: str) -> bool:
    """Check whether a branch name is protected.

    Args:
        branch: Branch name to check.

    Returns:
        True if the branch is ``main`` or ``master``, False otherwise.

    """
    return branch in ("main", "master")
