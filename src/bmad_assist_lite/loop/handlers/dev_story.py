"""DEV_STORY phase handler."""

from pathlib import Path
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

    def _stream_capture_path(self, state: State) -> Path | None:
        """Resolve the dev-stream forensic JSONL path when capture is enabled.

        Off by default: returns None unless ``forensics.capture_stream`` is set,
        in which case the dev call's turn-by-turn stream is retained at
        ``.bmad-assist-lite/cache/dev-stream-<story>.jsonl`` (a fresh file per
        invoke — truncated on open — so it reflects the last dev attempt). The
        cache directory is resolved exactly as ``quality_gate.py`` does.
        """
        if not self.config.forensics.capture_stream:
            return None
        cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        story_id = state.current_story or "unknown"
        return cache_dir / f"dev-stream-{story_id}.jsonl"
