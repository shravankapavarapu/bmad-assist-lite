"""Orchestrator log file for parallel story execution.

Provides structured logging to ``parallel-run.log`` in the project root.
Events are written with ``[ORCHESTRATOR]``, ``[MERGE|{story}]``, and
``[QG|post-merge|{story}]`` prefixes at appropriate severity levels
(INFO, WARNING, ERROR).

Uses append mode so consecutive runs build a continuous log with
run-start headers and run-end delimiters for readability.

.. warning::

    This file shadows Python's stdlib ``logging`` module.  All references
    to the stdlib are via ``import logging as _logging`` to avoid a
    self-import.
"""

from __future__ import annotations

import logging as _logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bmad_assist_lite.parallel.merger import GateResult

# ============================================================================
# Constants
# ============================================================================

_LOGGER_NAME = "bmad_assist_lite.parallel"
_LOG_FILENAME = "parallel-run.log"
_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
_TRUNCATION_LIMIT = 2000
_TRUNCATION_MARKER = "[truncated] "
_RUN_SEPARATOR = "=" * 80

# Module-level logger for internal use
_logger = _logging.getLogger(_LOGGER_NAME)

# Track the FileHandler instance for teardown
_file_handler: _logging.FileHandler | None = None
# Track the original logger level for restoration on teardown
_original_logger_level: int | None = None


# ============================================================================
# Setup / Teardown
# ============================================================================


def setup_parallel_log(project_root: Path) -> None:
    """Configure a dedicated FileHandler for orchestrator logging.

    Creates (or appends to) ``parallel-run.log`` in the project root.
    Uses UTF-8 encoding and a ``[%(asctime)s] [%(levelname)s] %(message)s``
    format.  The handler is attached to the ``bmad_assist_lite.parallel``
    logger namespace so only parallel-module events flow to the file.

    Idempotent: calling twice does not add duplicate handlers.

    Args:
        project_root: Path to the project root directory.

    """
    global _file_handler, _original_logger_level  # noqa: PLW0603

    if _file_handler is not None:
        # Already set up — idempotent
        return

    log_path = project_root / _LOG_FILENAME

    fh = _logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setLevel(_logging.DEBUG)
    fh.setFormatter(_logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))

    logger = _logging.getLogger(_LOGGER_NAME)
    logger.addHandler(fh)

    # Save original logger level for restoration on teardown
    _original_logger_level = logger.level

    # Ensure the logger level allows messages through
    if logger.level == _logging.NOTSET or logger.level > _logging.DEBUG:
        logger.setLevel(_logging.DEBUG)

    _file_handler = fh


def teardown_parallel_log() -> None:
    """Close and remove the FileHandler from the parallel logger.

    Safe to call even if ``setup_parallel_log()`` was never called.

    """
    global _file_handler, _original_logger_level  # noqa: PLW0603

    if _file_handler is None:
        return

    logger = _logging.getLogger(_LOGGER_NAME)
    logger.removeHandler(_file_handler)
    _file_handler.close()
    _file_handler = None

    # Restore the original logger level to avoid persistent side effects
    if _original_logger_level is not None:
        logger.setLevel(_original_logger_level)
        _original_logger_level = None


# ============================================================================
# Run Header / Footer
# ============================================================================


def log_run_header(
    base_branch: str,
    epic: int,
    max_concurrency: int,
    story_count: int,
) -> None:
    """Write the run-start header to the parallel log.

    Includes timestamp (via formatter), base branch, epic number,
    max concurrency, and story count.

    Args:
        base_branch: The git branch stories are based on.
        epic: The epic number being executed.
        max_concurrency: Maximum parallel story slots.
        story_count: Total number of stories in the run.

    """
    _logger.info(_RUN_SEPARATOR)
    _logger.info("[ORCHESTRATOR] Parallel run started")
    _logger.info(
        "[ORCHESTRATOR] base_branch=%s  epic=%d  max_concurrency=%d  stories=%d",
        base_branch,
        epic,
        max_concurrency,
        story_count,
    )
    _logger.info(_RUN_SEPARATOR)


def log_run_complete(
    total_stories: int,
    completed: int,
    blocked: int,
    failed: int,
) -> None:
    """Write the run-end footer to the parallel log.

    Serves as a delimiter between consecutive append-mode runs.

    Args:
        total_stories: Total number of stories in the run.
        completed: Number of stories that completed successfully.
        blocked: Number of stories that are blocked.
        failed: Number of stories that failed.

    """
    _logger.info(_RUN_SEPARATOR)
    _logger.info(
        "[ORCHESTRATOR] Parallel run complete — "
        "total=%d  completed=%d  blocked=%d  failed=%d",
        total_stories,
        completed,
        blocked,
        failed,
    )
    _logger.info(_RUN_SEPARATOR)


# ============================================================================
# Event Helpers
# ============================================================================


def log_story_started(story_id: str, worktree_path: Path) -> None:
    """Log that a story subprocess has been spawned.

    Args:
        story_id: The story identifier (e.g. ``"3.2"``).
        worktree_path: Path to the story's git worktree.

    """
    _logger.info(
        "[ORCHESTRATOR] Story %s started in worktree %s",
        story_id,
        worktree_path,
    )


def log_story_completed(story_id: str, exit_code: int) -> None:
    """Log that a story subprocess has exited.

    Uses INFO for success (exit code 0), WARNING for non-zero exit
    codes that indicate a recoverable issue, and ERROR for negative
    exit codes (killed/cancelled).

    Args:
        story_id: The story identifier.
        exit_code: The subprocess exit code.

    """
    if exit_code == 0:
        _logger.info(
            "[ORCHESTRATOR] Story %s completed successfully (exit_code=0)",
            story_id,
        )
    elif exit_code < 0:
        _logger.error(
            "[ORCHESTRATOR] Story %s terminated (exit_code=%d)",
            story_id,
            exit_code,
        )
    else:
        _logger.warning(
            "[ORCHESTRATOR] Story %s failed (exit_code=%d)",
            story_id,
            exit_code,
        )


def log_merge_queued(story_id: str) -> None:
    """Log that a story has been enqueued for merging.

    Args:
        story_id: The story identifier.

    """
    _logger.info("[ORCHESTRATOR] Story %s queued for merge", story_id)


def log_merge_result(story_id: str, success: bool, error: str | None) -> None:
    """Log the outcome of a merge attempt.

    Args:
        story_id: The story identifier.
        success: Whether the merge succeeded.
        error: Error description if the merge failed, or ``None``.

    """
    if success:
        _logger.info("[MERGE|%s] Merge succeeded", story_id)
    else:
        _logger.error(
            "[MERGE|%s] Merge failed: %s",
            story_id,
            error or "unknown error",
        )


def _truncate_output(text: str) -> str:
    """Truncate text to the last ``_TRUNCATION_LIMIT`` characters.

    If the text exceeds the limit, the last ``_TRUNCATION_LIMIT``
    characters are kept and the ``[truncated]`` marker is prepended.

    Args:
        text: The text to potentially truncate.

    Returns:
        The original text if within limit, or truncated text with marker.

    """
    if len(text) <= _TRUNCATION_LIMIT:
        return text
    return _TRUNCATION_MARKER + text[-_TRUNCATION_LIMIT:]


def log_qg_result(
    story_id: str,
    all_passed: bool,
    gate_results: list[GateResult],
) -> None:
    """Log the outcome of a post-merge quality gate run.

    When gates fail, each failed gate's name, command, exit code, and
    truncated stdout/stderr are included in the log entry.

    Args:
        story_id: The story identifier.
        all_passed: Whether all gates passed.
        gate_results: List of ``GateResult`` objects from the merger module.

    """
    tag = f"[QG|post-merge|{story_id}]"

    if all_passed:
        _logger.info("%s All quality gates passed", tag)
        return

    failed_names = [g.name for g in gate_results if not g.passed]
    _logger.error(
        "%s Quality gate FAILED — failed gates: %s",
        tag,
        ", ".join(failed_names) if failed_names else "unknown",
    )

    for gate in gate_results:
        if gate.passed:
            continue

        _logger.error(
            "%s  Gate: %s  command=%s  exit_code=%d",
            tag,
            gate.name,
            gate.command,
            gate.exit_code,
        )

        truncated_stdout = _truncate_output(gate.stdout or "")
        truncated_stderr = _truncate_output(gate.stderr or "")

        if truncated_stdout.strip():
            _logger.error("%s  [stdout] %s", tag, truncated_stdout)
        if truncated_stderr.strip():
            _logger.error("%s  [stderr] %s", tag, truncated_stderr)


def log_story_blocked(story_id: str, reason: str) -> None:
    """Log that a story has been blocked.

    Args:
        story_id: The story identifier.
        reason: Human-readable reason for the block.

    """
    _logger.warning("[ORCHESTRATOR] Story %s blocked: %s", story_id, reason)


def log_dependency_unlocked(story_id: str, unlocked_by: str) -> None:
    """Log that a story's dependency has been satisfied.

    Args:
        story_id: The story that is now unblocked.
        unlocked_by: The story that completed and unlocked this one.

    """
    _logger.info(
        "[ORCHESTRATOR] Story %s dependency unlocked by %s",
        story_id,
        unlocked_by,
    )


# ============================================================================
# Teardown Logging
# ============================================================================


def log_teardown_result(
    epic_num: int,
    success: bool,
    exit_code: int,
    duration_s: float | None = None,
    error: str | None = None,
) -> None:
    """Log the outcome of an epic teardown subprocess.

    Logs at INFO level on success, ERROR level on failure.  Includes
    the ``[TEARDOWN|epic-{N}]`` tag prefix for filtering and the
    optional duration when provided.

    Args:
        epic_num: The epic number that teardown ran for.
        success: Whether teardown completed successfully.
        exit_code: The subprocess exit code.
        duration_s: Wall-clock duration in seconds, or ``None``.
        error: Error description on failure, or ``None``.

    """
    tag = f"[TEARDOWN|epic-{epic_num}]"
    duration_suffix = f" in {duration_s:.1f}s" if duration_s is not None else ""

    if success:
        _logger.info(
            "%s Epic teardown completed successfully (exit_code=%d)%s",
            tag,
            exit_code,
            duration_suffix,
        )
    else:
        _logger.error(
            "%s Epic teardown failed (exit_code=%d)%s: %s",
            tag,
            exit_code,
            duration_suffix,
            error or "unknown error",
        )
