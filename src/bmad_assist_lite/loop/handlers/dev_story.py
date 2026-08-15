"""DEV_STORY phase handler."""

from typing import Any

from bmad_assist_lite.core.config import Config, LeanDev
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.autonomy import AutonomyLevel
from bmad_assist_lite.loop.handlers.base import BaseHandler


def resolve_dev_lean_mode(config: Config, state: State) -> LeanDev:
    """The lean-dev mode the current dev_story attempt uses.

    Under SP-A1 adaptive mode the mode is decided by the attempt, not the static
    config: the first attempt is forced ``full`` (lean-first) and any fallback
    retry (``dev_attempt`` >= 1) is forced ``off``. Outside adaptive mode the
    static ``speed.lean_dev`` mode applies unchanged. Shared by the dev prompt
    and the dev-gate record so both agree on what actually ran.
    """
    if config.speed.lean_dev_adaptive:
        return LeanDev.FULL if state.dev_attempt == 0 else LeanDev.OFF
    return config.speed.lean_dev


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
        """Compile the dev prompt, appending the lean-dev addendum for the mode.

        With ``speed.lean_dev`` ``off`` (default) this returns exactly the base
        prompt, byte-for-byte -- the addendum is opt-in and additive. ``full``
        appends the whole-phase run7 addendum; ``report_only`` appends the
        report-scoped variant (SP-D1b).
        """
        prompt = super().render_prompt(state)
        mode = resolve_dev_lean_mode(self.config, state)
        if mode is LeanDev.FULL:
            prompt = f"{prompt}\n\n{self._lean_dev_addendum()}"
        elif mode is LeanDev.REPORT_ONLY:
            prompt = f"{prompt}\n\n{self._lean_dev_report_addendum()}"
        return prompt

    @staticmethod
    def _lean_dev_report_addendum() -> str:
        """SP-D1b report-scoped economy: trim only the FINAL REPORT.

        The decoupling probe. Unlike the full addendum, it says nothing about
        how the work is done during implementation — no narration rule, no
        Write-over-Edit, no restatement rule — so the working phase (where the
        run7 thinking-suppression coupling was measured) is left untouched. Only
        the final write-up is economised. If opus's thinking recovers toward the
        OFF anchor under this variant, the coupling lived in the working-phase
        rules, not the report rule.
        """
        return (
            "<lean-dev-report>\n"
            "Output economy applies to your FINAL REPORT ONLY. Work exactly as "
            "you normally would while implementing: narrate, explore, edit, and "
            "think as much as the task needs -- nothing about HOW you build "
            "changes.\n"
            "- In your final report only, do NOT re-print the code you wrote or "
            "file contents. Give a brief summary and any findings only, under "
            "~1000 tokens. The diff is the record of what changed; the report is "
            "not.\n"
            "Still required in full: implement every acceptance criterion, write "
            "the tests the story calls for, and run the checks.\n"
            "</lean-dev-report>"
        )

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
