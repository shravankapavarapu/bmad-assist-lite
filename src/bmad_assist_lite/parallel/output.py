"""Multiplex live output from parallel worktree subprocesses to the console.

Reads async streams from subprocess stdout, prefixes each line with the
story ID (e.g. ``[3.1]``) or ``[ORCHESTRATOR]``, and writes via the
thread-safe ``write_progress()`` function.
"""

import asyncio
import contextlib
import logging

from bmad_assist_lite.providers.base import write_progress

logger = logging.getLogger(__name__)


# ============================================================================
# OutputMultiplexer
# ============================================================================


class OutputMultiplexer:
    """Multiplex prefixed output from multiple async subprocess streams.

    Each subprocess gets a reader task that reads lines from its stdout,
    prefixes them with ``[{story_id}]``, and writes to the console via
    ``write_progress()``. The ``[ORCHESTRATOR]`` prefix is used for
    orchestrator-level messages.
    """

    def __init__(self) -> None:
        """Initialize with empty reader task tracking."""
        self._reader_tasks: dict[str, asyncio.Task[None]] = {}
        # Prefix width accommodates the longest prefix: [ORCHESTRATOR] = 14 chars
        self._prefix_width: int = 14

    # ========================================================================
    # Stream reading
    # ========================================================================

    async def _read_stream(
        self, story_id: str, stream: asyncio.StreamReader
    ) -> None:
        """Read lines from a subprocess stream and write prefixed output.

        Reads until EOF, decoding each line as UTF-8 with replacement
        characters for invalid bytes. Empty lines are passed through
        with prefix only (preserving structural formatting from tools
        like pytest and linters).

        Args:
            story_id: The story identifier for prefix formatting.
            stream: The async stream reader to read from.

        """
        prefix = f"[{story_id}]".ljust(self._prefix_width)

        while True:
            try:
                line = await stream.readline()
                if line == b"":
                    # EOF — subprocess has closed its stdout
                    break

                decoded_line = line.decode("utf-8", errors="replace").rstrip("\n\r")
                await asyncio.to_thread(write_progress, f"{prefix} {decoded_line}")
            except Exception:
                logger.warning(
                    "Error processing output line for story %s, continuing",
                    story_id,
                    exc_info=True,
                )
                # Continue draining the pipe — never exit early while
                # the subprocess may still be alive, as this would fill
                # the OS pipe buffer (~64KB) and deadlock the subprocess.
                # On readline errors (e.g. LimitOverrunError), we must
                # keep trying to read to prevent pipe buffer deadlock.
                continue

    # ========================================================================
    # Reader lifecycle
    # ========================================================================

    def start_reader(
        self, story_id: str, stream: asyncio.StreamReader
    ) -> asyncio.Task[None]:
        """Create and track an async reader task for a subprocess stream.

        Args:
            story_id: The story identifier for prefix formatting.
            stream: The async stream reader to read from.

        Returns:
            The created asyncio task for external tracking/cancellation.

        """
        task = asyncio.create_task(
            self._read_stream(story_id, stream),
            name=f"output-reader-{story_id}",
        )
        self._reader_tasks[story_id] = task
        return task

    async def await_reader(self, story_id: str, timeout: float = 5.0) -> bool:
        """Wait for a reader task to complete naturally (drain to EOF).

        Args:
            story_id: The story identifier whose reader to await.
            timeout: Maximum seconds to wait before returning False.

        Returns:
            True if the reader completed within the timeout, False otherwise.

        """
        task = self._reader_tasks.get(story_id)
        if task is None or task.done():
            return True

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            return True
        except (TimeoutError, asyncio.CancelledError):
            return False
        except Exception:
            logger.warning(
                "Error awaiting output reader for story %s",
                story_id,
                exc_info=True,
            )
            return False

    async def stop_reader(self, story_id: str) -> None:
        """Stop and clean up a reader task for a specific story.

        If the task is still running, cancels it and awaits completion.
        Handles already-completed tasks and unknown story IDs gracefully.

        Args:
            story_id: The story identifier whose reader to stop.

        """
        task = self._reader_tasks.pop(story_id, None)
        if task is None:
            logger.debug("stop_reader called for unknown story %s (no-op)", story_id)
            return

        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        logger.debug("Stopped output reader for story %s", story_id)

    async def stop_all(self) -> None:
        """Cancel all active reader tasks and clear tracking state.

        Uses ``asyncio.gather`` with ``return_exceptions=True`` for
        efficient cleanup of all reader tasks simultaneously.
        """
        tasks = list(self._reader_tasks.values())
        self._reader_tasks.clear()

        for task in tasks:
            if not task.done():
                task.cancel()

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    logger.warning(
                        "Reader task raised during stop_all: %s", result,
                    )

    # ========================================================================
    # Orchestrator output
    # ========================================================================

    async def write_orchestrator(self, message: str) -> None:
        """Write a message with the ``[ORCHESTRATOR]`` prefix.

        Formats the prefix to the same width as story prefixes for
        consistent alignment. Uses ``asyncio.to_thread`` to avoid
        blocking the event loop (``write_progress`` acquires a
        ``threading.Lock``).

        Args:
            message: The message to write.

        """
        prefix = "[ORCHESTRATOR]".ljust(self._prefix_width)
        await asyncio.to_thread(write_progress, f"{prefix} {message}")
