"""Lock file management for concurrent run prevention.

Windows-safe PID checking using ctypes.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from bmad_assist_lite.core.exceptions import StateError

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Win32 API constant for OpenProcess access rights
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

__all__ = ["running_lock"]


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    if pid <= 0:
        return False

    if IS_WINDOWS:
        return _is_pid_alive_windows(pid)
    return _is_pid_alive_unix(pid)


def _is_pid_alive_windows(pid: int) -> bool:
    """Windows PID check using ctypes."""
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def _is_pid_alive_unix(pid: int) -> bool:
    """Unix PID check using signal 0."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_lock_file(lock_path: Path) -> tuple[int | None, str | None]:
    """Read PID and timestamp from lock file."""
    try:
        content = lock_path.read_text().strip().split("\n")
        if len(content) >= 2:
            pid = int(content[0].strip())
            timestamp = content[1].strip()
            return pid, timestamp
    except (ValueError, IndexError, OSError):
        pass
    return None, None


def _try_exclusive_create(lock_path: Path, content: str) -> bool:
    """Atomically create lock file using exclusive mode.

    Returns True if we created the file, False if it already exists.
    This eliminates the TOCTOU race between checking and creating.
    """
    try:
        fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        return False


@contextmanager
def running_lock(project_path: Path) -> Generator[Path, None, None]:
    """Context manager for .bmad-assist-lite/running.lock file.

    When ``BMAD_PARALLEL_MODE=1`` is set, the lock is skipped entirely
    because the parent orchestrator already holds it.  Subprocesses
    (story worktrees, teardown) run under the parent's lock.
    """
    if os.environ.get("BMAD_PARALLEL_MODE") == "1":
        lock_path = project_path / ".bmad-assist-lite" / "running.lock"
        yield lock_path
        return

    lock_dir = project_path / ".bmad-assist-lite"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "running.lock"

    lock_content = f"{os.getpid()}\n{datetime.now(UTC).isoformat()}\n"

    # Try atomic exclusive create first (eliminates TOCTOU race)
    if not _try_exclusive_create(lock_path, lock_content):
        # Lock file exists — check if the owning process is still alive
        existing_pid, lock_timestamp = _read_lock_file(lock_path)
        if existing_pid is not None and _is_pid_alive(existing_pid):
            raise StateError(
                f"Another bmad-assist-lite run is already active (PID {existing_pid}). "
                f"Remove stale lock: {lock_path}"
            )

        # Stale lock from dead process — remove and retry exclusive create
        logger.warning(
            "Removing stale lock from dead process %s (locked at %s)",
            existing_pid,
            lock_timestamp,
        )
        with contextlib.suppress(OSError):
            lock_path.unlink()

        if not _try_exclusive_create(lock_path, lock_content):
            # Another process grabbed the lock between our unlink and create
            raise StateError(
                f"Another bmad-assist-lite run acquired the lock. Lock file: {lock_path}"
            )

    try:
        yield lock_path
    finally:
        # Only remove if we still own it (our PID)
        try:
            existing_pid, _ = _read_lock_file(lock_path)
            if existing_pid == os.getpid():
                lock_path.unlink()
        except (FileNotFoundError, OSError):
            pass
