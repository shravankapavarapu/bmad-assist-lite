"""Windows-safe process management utilities.

Centralizes all platform-specific code for process management:
- Process creation flags (CREATE_NO_WINDOW on Windows)
- Process termination (taskkill on Windows, killpg on Unix)
- PID alive checking (OpenProcess on Windows, kill(0) on Unix)
"""

import logging
import os
import signal
import subprocess
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# Win32 API constants
CREATE_NO_WINDOW = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Unix SIGTERM→SIGKILL escalation grace period (seconds)
SIGTERM_GRACE_SECONDS = 5


def get_subprocess_kwargs() -> dict[str, Any]:
    """Get platform-specific kwargs for subprocess.Popen.

    Returns dict with:
    - Windows: creationflags=CREATE_NO_WINDOW (prevents console flash)
    - Unix: start_new_session=True (own process group for clean termination)
    """
    if IS_WINDOWS:
        return {"creationflags": CREATE_NO_WINDOW}
    else:
        return {"start_new_session": True}


def terminate_process(pid: int) -> bool:
    """Terminate a process and its children.

    Windows: taskkill /F /T /PID (force kill entire tree)
    Unix: killpg(pgid, SIGTERM) then SIGKILL after 5s

    Returns True if termination was attempted (not necessarily successful).
    """
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning("taskkill failed for PID %d: %s", pid, result.stderr.strip())
                return False
            return True
        else:
            try:
                pgid = os.getpgid(pid)  # type: ignore[attr-defined]
                os.killpg(pgid, signal.SIGTERM)  # type: ignore[attr-defined]
            except ProcessLookupError:
                return False

            # Poll for graceful exit before escalating to SIGKILL
            start = time.monotonic()
            while time.monotonic() - start < SIGTERM_GRACE_SECONDS:
                if not is_pid_alive(pid):
                    logger.debug("PID %d exited after SIGTERM within grace period", pid)
                    return True
                time.sleep(0.1)

            # Grace period expired — escalate to SIGKILL
            logger.debug(
                "PID %d still alive after %ds grace period, sending SIGKILL",
                pid,
                SIGTERM_GRACE_SECONDS,
            )
            try:
                os.killpg(pgid, signal.SIGKILL)  # type: ignore[attr-defined]
            except ProcessLookupError:
                # Mid-escalation death: process died between check and SIGKILL
                logger.debug("PID %d died before SIGKILL delivery", pid)
            return True
    except Exception as e:
        logger.warning("Failed to terminate PID %d: %s", pid, e)
        return False


def is_pid_alive(pid: int) -> bool:
    """Check if a process is still running.

    Windows: ctypes OpenProcess + CloseHandle
    Unix: os.kill(pid, 0)
    """
    if pid <= 0:
        return False

    if IS_WINDOWS:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # Process exists but we can't signal it


def kill_process(process: subprocess.Popen) -> None:  # type: ignore[type-arg]
    """Kill a subprocess, handling platform differences.

    Windows: Uses terminate() + taskkill as fallback
    Unix: Uses kill() which sends SIGKILL
    """
    try:
        if IS_WINDOWS:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if process.pid:
                    terminate_process(process.pid)
        else:
            process.kill()
    except Exception as e:
        logger.warning("Failed to kill process: %s", e)
