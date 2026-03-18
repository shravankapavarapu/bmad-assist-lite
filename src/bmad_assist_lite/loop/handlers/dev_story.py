"""DEV_STORY phase handler."""

from typing import Any

from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.base import BaseHandler


class DevStoryHandler(BaseHandler):
    """Master LLM implements the story."""

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "dev_story"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context for this phase."""
        return self._build_common_context(state)
