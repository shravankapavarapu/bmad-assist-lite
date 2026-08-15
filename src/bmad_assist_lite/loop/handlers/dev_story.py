"""DEV_STORY phase handler."""

from typing import Any

from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.autonomy import AutonomyLevel
from bmad_assist_lite.loop.handlers.base import BaseHandler


class DevStoryHandler(BaseHandler):
    """Master LLM implements the story."""

    autonomy = AutonomyLevel.EXECUTE
    """Implements the story and runs its tests."""

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "dev_story"

    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context for this phase."""
        return self._build_common_context(state)

    def render_prompt(self, state: State) -> str:
        """Compile the dev prompt, appending the SP-D1 lean-dev addendum when enabled.

        With ``speed.lean_dev`` off (default) this returns exactly the base
        prompt, byte-for-byte -- the addendum is opt-in and additive.
        """
        prompt = super().render_prompt(state)
        if self.config.speed.lean_dev:
            prompt = f"{prompt}\n\n{self._lean_dev_addendum()}"
        return prompt

    @staticmethod
    def _lean_dev_addendum() -> str:
        """SP-D1 output-economy addendum: fewer tokens describing the work, same work.

        Targets only *description* overhead (between-tool narration, restatement,
        re-emitted code, a bloated final report). It must never tell dev to think
        less, skip tests, or write less code: that would be an effort notch, which
        run6 showed degrades judgment, and which the A/B's stream-decomposition and
        replay/AC guards are designed to catch.
        """
        return (
            "<lean-dev>\n"
            "Output economy: do the SAME work; emit fewer tokens describing it. "
            "These directions change how you communicate, never what you build -- "
            "write the same code, the same tests, and think as hard as the task "
            "needs.\n"
            '- Do not narrate between tool calls. Skip "Now I will...", "Next, let '
            'me...", running commentary, and status recaps. Act, do not announce.\n'
            "- Do not restate the story, the acceptance criteria, or your plan more "
            "than once; assume they remain in context.\n"
            "- Prefer a single Write of a complete new file over a chain of "
            "incremental Edits to build it up. Use Edit to change existing code.\n"
            "- When you use Edit, keep the old/new context minimal -- just enough to "
            "anchor the change uniquely; do not paste large surrounding blocks.\n"
            "- In your final report, do NOT re-print the code you wrote or file "
            "contents. Give a brief summary and any findings only, under ~1000 "
            "tokens. The diff is the record of what changed; the report is not.\n"
            "Still required in full: implement every acceptance criterion, write the "
            "tests the story calls for, and run the checks. Economy is about how you "
            "describe the work, never about doing less of it.\n"
            "</lean-dev>"
        )
