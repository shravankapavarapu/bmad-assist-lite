"""Phase dispatch and execution."""

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from bmad_assist_lite.core.exceptions import StateError
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.types import PhaseHandler, PhaseResult

if TYPE_CHECKING:
    from bmad_assist_lite.core.config import Config
    from bmad_assist_lite.loop.handlers.base import BaseHandler

logger = logging.getLogger(__name__)

__all__ = ["init_handlers", "get_handler", "execute_phase"]

_handler_instances: dict[Phase, "BaseHandler"] = {}
_handlers_initialized: bool = False


def init_handlers(config: "Config", project_path: Path) -> None:
    """Initialize handler instances."""
    global _handler_instances, _handlers_initialized

    from bmad_assist_lite.loop.handlers import (
        CodeReviewHandler,
        CodeReviewSynthesisHandler,
        CreateStoryHandler,
        DevStoryHandler,
        RetrospectiveHandler,
        ValidateStoryHandler,
        ValidateStorySynthesisHandler,
    )

    _handler_instances = {
        Phase.CREATE_STORY: CreateStoryHandler(config, project_path),
        Phase.VALIDATE_STORY: ValidateStoryHandler(config, project_path),
        Phase.VALIDATE_STORY_SYNTHESIS: ValidateStorySynthesisHandler(config, project_path),
        Phase.DEV_STORY: DevStoryHandler(config, project_path),
        Phase.CODE_REVIEW: CodeReviewHandler(config, project_path),
        Phase.CODE_REVIEW_SYNTHESIS: CodeReviewSynthesisHandler(config, project_path),
        Phase.RETROSPECTIVE: RetrospectiveHandler(config, project_path),
    }
    _handlers_initialized = True
    logger.debug("Initialized %d phase handlers", len(_handler_instances))


def reset_handlers() -> None:
    """Reset handler state. For testing."""
    global _handler_instances, _handlers_initialized
    _handler_instances = {}
    _handlers_initialized = False


def get_handler(phase: Phase) -> PhaseHandler:
    """Get the handler function for a workflow phase."""
    if _handlers_initialized and phase in _handler_instances:
        return _handler_instances[phase].execute

    raise StateError(f"No handler registered for phase: {phase!r}")


def execute_phase(state: State) -> PhaseResult:
    """Execute a single workflow phase and return its result.

    Never raises exceptions - all errors returned as PhaseResult.fail().
    """
    start_time = time.perf_counter()

    if state.current_phase is None:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        result = PhaseResult.fail("Cannot execute phase: no current phase set")
        return replace(result, outputs={**result.outputs, "duration_ms": duration_ms})

    phase = state.current_phase
    phase_name = phase.value

    logger.info("Starting phase: %s", phase_name)

    try:
        handler = get_handler(phase)
    except StateError as e:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        logger.error("Phase %s dispatch failed: %s", phase_name, e)
        result = PhaseResult.fail(str(e))
        return replace(result, outputs={**result.outputs, "duration_ms": duration_ms})

    try:
        handler_result = handler(state)
        if not isinstance(handler_result, PhaseResult):
            raise TypeError(
                f"Handler returned {type(handler_result).__name__}, expected PhaseResult"
            )
    except Exception as e:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        logger.error("Phase %s handler failed: %s", phase_name, e, exc_info=True)
        result = PhaseResult.fail(f"Handler error: {e}")
        return replace(result, outputs={**result.outputs, "duration_ms": duration_ms})

    duration_ms = int((time.perf_counter() - start_time) * 1000)
    logger.info(
        "Phase %s completed: success=%s duration=%dms",
        phase_name,
        handler_result.success,
        duration_ms,
    )

    new_outputs = {**handler_result.outputs, "duration_ms": duration_ms}
    return replace(handler_result, outputs=new_outputs)
