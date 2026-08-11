"""Run-scoped mode flags shared by the loop and its phase handlers.

A handler occasionally needs to know *how the run started* rather than where it
currently is. The resume flag is the only such fact today: a story artifact on
disk means something different on a resume (work already done) than on a fresh
run (a stale leftover from an earlier crashed run), and the difference decides
whether a phase may be skipped.

The flag is deliberately **not** derived from the filesystem and **not** stored
on ``State`` — ``State`` is persisted, and "this run was resumed" is a property
of the invocation, not of the saved position. It follows the project's
singleton-with-reset convention so tests can isolate it.
"""

import logging

logger = logging.getLogger(__name__)

__all__ = ["set_resume_mode", "is_resume_run", "_reset_run_mode"]


_resume_run: bool = False


def set_resume_mode(resume: bool) -> None:
    """Record whether the active run was started with ``--resume``."""
    global _resume_run
    _resume_run = resume
    logger.debug("Run mode: resume=%s", resume)


def is_resume_run() -> bool:
    """Return ``True`` when the active run was started with ``--resume``."""
    return _resume_run


def _reset_run_mode() -> None:
    """Reset the run mode to its fresh-run default. For testing."""
    global _resume_run
    _resume_run = False
