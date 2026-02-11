"""Windows-safe signal handling for shutdown.

On Windows: Only handles SIGINT (Ctrl+C). No SIGTERM, no killpg.
On Unix: Handles both SIGINT and SIGTERM.
"""

import os
import signal
import sys
import threading
from collections.abc import Callable
from types import FrameType

from bmad_assist_lite.loop.types import LoopExitReason

__all__ = [
    "shutdown_requested",
    "request_shutdown",
    "reset_shutdown",
    "register_signal_handlers",
    "unregister_signal_handlers",
]

_shutdown_event = threading.Event()
_received_signal: int | None = None

_previous_sigint: Callable[[int, FrameType | None], None] | int | None = None
_previous_sigterm: Callable[[int, FrameType | None], None] | int | None = None

IS_WINDOWS = sys.platform == "win32"


def shutdown_requested() -> bool:
    """Check if shutdown has been requested."""
    return _shutdown_event.is_set()


def request_shutdown(signum: int) -> None:
    """Request shutdown with the given signal number."""
    global _received_signal
    _received_signal = signum
    _shutdown_event.set()


def reset_shutdown() -> None:
    """Clear shutdown state."""
    global _received_signal
    _received_signal = None
    _shutdown_event.clear()


def get_exit_reason() -> LoopExitReason:
    """Get the appropriate exit reason for the received signal."""
    return LoopExitReason.INTERRUPTED


def _handle_signal(signum: int, frame: FrameType | None) -> None:
    """Handle shutdown signal.

    First Ctrl+C: request graceful shutdown.
    Second Ctrl+C: force exit immediately.
    """
    if _shutdown_event.is_set():
        # Second signal - hard exit
        os._exit(130)
    request_shutdown(signum)


def register_signal_handlers() -> None:
    """Register signal handlers for shutdown."""
    if threading.current_thread() is not threading.main_thread():
        return  # Can only register from main thread

    global _previous_sigint, _previous_sigterm

    _previous_sigint = signal.signal(signal.SIGINT, _handle_signal)

    if not IS_WINDOWS:
        _previous_sigterm = signal.signal(signal.SIGTERM, _handle_signal)


def unregister_signal_handlers() -> None:
    """Restore previous signal handlers."""
    if _previous_sigint is not None:
        signal.signal(signal.SIGINT, _previous_sigint)
    else:
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    if not IS_WINDOWS and _previous_sigterm is not None:
        signal.signal(signal.SIGTERM, _previous_sigterm)
