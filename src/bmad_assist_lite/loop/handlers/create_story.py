"""CREATE_STORY phase handler."""

from typing import Any

from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.base import BaseHandler


class CreateStoryHandler(BaseHandler):
    """Creates a new story file from epic context using Master LLM."""

    @property
    def phase_name(self) -> str:
        return "create_story"

    def build_context(self, state: State) -> dict[str, Any]:
        return self._build_common_context(state)
