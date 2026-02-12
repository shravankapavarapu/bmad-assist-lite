"""CREATE_STORY phase handler."""

import logging
from typing import Any

from bmad_assist_lite.cli import load_story_queue_cache
from bmad_assist_lite.core.paths import get_paths
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.base import BaseHandler

logger = logging.getLogger(__name__)


class CreateStoryHandler(BaseHandler):
    """Creates a new story file from epic context using Master LLM."""

    @property
    def phase_name(self) -> str:
        return "create_story"

    def build_context(self, state: State) -> dict[str, Any]:
        ctx = self._build_common_context(state)

        # Inject story_key from cached story queue
        paths = get_paths()
        cache = load_story_queue_cache(paths.cache_dir)
        if cache and state.current_story:
            key_map = cache.get("story_key_map", {})
            story_key = key_map.get(state.current_story)
            if story_key:
                ctx["story_key"] = story_key
                logger.debug("Resolved story_key=%s for %s", story_key, state.current_story)

        return ctx

    def render_prompt(self, state: State) -> str:
        """Render prompt with story_key from cached story queue."""
        from bmad_assist_lite.compiler import compile_workflow
        from bmad_assist_lite.compiler.types import CompilerContext
        from bmad_assist_lite.core.exceptions import CompilerError, ConfigError

        workflow_name = self.phase_name.replace("_", "-")

        resolved_variables: dict[str, Any] = {
            "epic_num": state.current_epic,
            "story_num": self._extract_story_num(state.current_story),
        }

        # Add story_key from cache
        paths = get_paths()
        cache = load_story_queue_cache(paths.cache_dir)
        if cache and state.current_story:
            key_map = cache.get("story_key_map", {})
            story_key = key_map.get(state.current_story)
            if story_key:
                resolved_variables["story_key"] = story_key

        context = CompilerContext(
            project_root=self.project_path,
            output_folder=paths.output_folder,
            project_knowledge=paths.project_knowledge,
            resolved_variables=resolved_variables,
        )

        try:
            compiled = compile_workflow(workflow_name, context)
            logger.info(
                "Compiled prompt for %s (tokens: ~%d)",
                workflow_name,
                compiled.token_estimate,
            )
            return compiled.context
        except CompilerError as e:
            raise ConfigError(f"Failed to compile workflow: {workflow_name}\n  {e}") from e
