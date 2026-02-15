"""Phase dispatch and execution."""

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bmad_assist_lite.core.exceptions import StateError
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.types import PhaseHandler, PhaseResult

if TYPE_CHECKING:
    from bmad_assist_lite.core.config import Config

logger = logging.getLogger(__name__)

__all__ = ["init_handlers", "get_handler", "execute_phase"]

# Any because non-LLM handlers (QualityGateHandler, EpicQualityGateHandler) don't extend BaseHandler
_handler_instances: dict[Phase, Any] = {}
_handlers_initialized: bool = False


def init_handlers(config: "Config", project_path: Path) -> None:
    """Initialize handler instances."""
    global _handler_instances, _handlers_initialized

    from bmad_assist_lite.loop.handlers import (
        CodeReviewHandler,
        CodeReviewSynthesisHandler,
        CreateStoryHandler,
        DevStoryHandler,
        EpicQualityGateHandler,
        FixQualityGateHandler,
        QualityGateHandler,
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
        Phase.QUALITY_GATE: QualityGateHandler(config, project_path),
        Phase.FIX_QUALITY_GATE: FixQualityGateHandler(config, project_path),
        Phase.EPIC_QUALITY_GATE: EpicQualityGateHandler(config, project_path),
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
        handler_fn: PhaseHandler = _handler_instances[phase].execute
        return handler_fn

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
    duration_str = _format_duration(duration_ms)
    status_icon = "\u2714" if handler_result.success else "\u2718"
    print(f"  {status_icon} Phase completed in {duration_str}", flush=True)

    logger.info(
        "Phase %s completed: success=%s duration=%dms",
        phase_name,
        handler_result.success,
        duration_ms,
    )

    new_outputs = {**handler_result.outputs, "duration_ms": duration_ms}
    return replace(handler_result, outputs=new_outputs)


def _format_duration(ms: int) -> str:
    """Format milliseconds into a human-readable duration string."""
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"
