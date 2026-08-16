"""CREATE_STORY phase handler."""

import logging
from typing import Any

from bmad_assist_lite.cli import load_story_queue_cache
from bmad_assist_lite.core.paths import get_paths
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.autonomy import AutonomyLevel
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.run_mode import is_resume_run
from bmad_assist_lite.loop.story_validity import check_story_reusable
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers.base import write_progress

logger = logging.getLogger(__name__)


class CreateStoryHandler(BaseHandler):
    """Creates a new story file from epic context using Master LLM."""

    autonomy = AutonomyLevel.EXECUTE
    """Unrestricted today. A candidate for WRITE, but narrowing it is a
    behaviour change that needs its own evidence, not a silent side effect."""

    @property
    def phase_name(self) -> str:
        """Return the phase name."""
        return "create_story"

    def execute(self, state: State) -> PhaseResult:
        """Create the story, reusing a valid existing file on a resume path.

        On a resume the story file on disk is the output of a phase that
        already completed, so re-running the LLM buys nothing. On a fresh run
        the same file is a leftover from an earlier crashed run, and trusting
        it is how a run finishes fast with nothing to show — so the reuse is
        never even considered there.
        """
        if not is_resume_run():
            return super().execute(state)

        verdict = check_story_reusable(state.current_story)
        if verdict.reusable and verdict.path is not None:
            write_progress(f"  Reusing existing story file: {verdict.path.name} (skipping phase)")
            logger.info(
                "Skipping create_story on resume: story %s already exists and is "
                "structurally valid (%s)",
                state.current_story,
                verdict.path,
            )
            return PhaseResult.ok(
                {
                    "skipped": True,
                    "skip_reason": "resume: existing story file passed structural validation",
                    "story_path": str(verdict.path),
                }
            )
        if verdict.path is not None:
            logger.info("Not reusing existing story file — %s", verdict.summary())

        return super().execute(state)

    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context with story_key from cached queue."""
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

    def render_prompt(self, state: State, workflow_name: str | None = None) -> str:
        """Render prompt with story_key from cached story queue.

        ``workflow_name`` is accepted for signature compatibility with the base
        handler; this handler always compiles its own workflow.
        """
        from bmad_assist_lite.compiler import compile_workflow
        from bmad_assist_lite.compiler.types import CompilerContext
        from bmad_assist_lite.core.exceptions import CompilerError, ConfigError

        workflow_name = self.phase_name.replace("_", "-")

        # Add story_key from cache
        paths = get_paths()

        resolved_variables: dict[str, Any] = {
            "epic_num": state.current_epic,
            "story_num": self._extract_story_num(state.current_story),
            "planning_artifacts": str(paths.planning_artifacts),
            "implementation_artifacts": str(paths.implementation_artifacts),
        }
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
            write_progress(f"  Compiling {workflow_name} prompt...")
            compiled = compile_workflow(workflow_name, context)
            write_progress(f"  Prompt ready (~{compiled.token_estimate} tokens)")
            logger.info(
                "Compiled prompt for %s (tokens: ~%d)",
                workflow_name,
                compiled.token_estimate,
            )
            return compiled.context
        except CompilerError as e:
            raise ConfigError(f"Failed to compile workflow: {workflow_name}\n  {e}") from e
