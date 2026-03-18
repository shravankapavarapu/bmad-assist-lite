"""Type definitions for the loop package."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeAlias

from bmad_assist_lite.core.state import Phase, State

__all__ = ["LoopExitReason", "PhaseResult", "PhaseHandler"]


class LoopExitReason(StrEnum):
    """Reason for run_loop() exit."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ERROR = "error"


@dataclass(frozen=True)
class PhaseResult:
    """Result of executing a workflow phase handler."""

    success: bool
    next_phase: Phase | None = None
    error: str | None = None
    outputs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, outputs: dict[str, Any] | None = None) -> "PhaseResult":
        """Create a successful phase result."""
        return cls(success=True, outputs=dict(outputs) if outputs is not None else {})

    @classmethod
    def fail(cls, error: str) -> "PhaseResult":
        """Create a failed phase result."""
        return cls(success=False, error=error)


PhaseHandler: TypeAlias = Callable[[State], PhaseResult]
