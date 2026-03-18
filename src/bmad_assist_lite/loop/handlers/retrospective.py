"""RETROSPECTIVE phase handler."""

from typing import Any

from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.base import BaseHandler


class RetrospectiveHandler(BaseHandler):
    """Runs epic retrospective after all stories complete."""

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "retrospective"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context for this phase."""
        return self._build_common_context(state)
