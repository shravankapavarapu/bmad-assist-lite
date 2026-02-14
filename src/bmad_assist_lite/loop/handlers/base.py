"""Base handler class for phase execution."""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from bmad_assist_lite.compiler import compile_workflow
from bmad_assist_lite.compiler.types import CompilerContext
from bmad_assist_lite.core.config import Config, get_phase_timeout
from bmad_assist_lite.core.exceptions import CompilerError, ConfigError, ProviderExitCodeError
from bmad_assist_lite.core.paths import get_paths
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers import get_provider
from bmad_assist_lite.providers.base import BaseProvider, ProviderResult, write_progress

logger = logging.getLogger(__name__)


class BaseHandler(ABC):
    """Abstract base class for phase handlers."""

    def __init__(self, config: Config, project_path: Path) -> None:
        self.config = config
        self.project_path = project_path

    @property
    @abstractmethod
    def phase_name(self) -> str:
        """Phase name (e.g., 'create_story')."""
        ...

    @abstractmethod
    def build_context(self, state: State) -> dict[str, Any]:
        """Build template context from state."""
        ...

    def _extract_story_num(self, story_id: str | None) -> str | None:
        """Extract story number from story ID like '1.2' -> '2'."""
        if story_id and "." in story_id:
            return story_id.split(".")[1]
        return None

    def _build_common_context(self, state: State) -> dict[str, Any]:
        """Build common context variables."""
        return {
            "epic_num": state.current_epic,
            "story_num": self._extract_story_num(state.current_story),
            "story_id": state.current_story,
            "project_path": str(self.project_path),
        }

    def render_prompt(self, state: State) -> str:
        """Render prompt using compiler."""
        workflow_name = self.phase_name.replace("_", "-")

        resolved_variables: dict[str, str | int | None] = {
            "epic_num": state.current_epic,
            "story_num": self._extract_story_num(state.current_story),
        }

        paths = get_paths()

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

    def get_provider(self) -> BaseProvider:
        """Get the master provider instance."""
        provider_name = self.config.providers.master.provider
        return get_provider(provider_name)

    def get_model(self) -> str | None:
        """Get the model name for provider invocation."""
        return self.config.providers.master.model

    def invoke_provider(self, prompt: str) -> ProviderResult:
        """Invoke the provider with the given prompt."""
        provider = self.get_provider()
        model = self.get_model()
        timeout = get_phase_timeout(self.config, self.phase_name)

        model_display = model or provider.default_model or "default"
        timeout_display = f"{timeout}s" if timeout else "no limit"
        write_progress(
            f"  Invoking {provider.provider_name} ({model_display})"
            f" timeout={timeout_display}..."
        )

        logger.info(
            "Invoking %s with model=%s, timeout=%s",
            provider.provider_name,
            model,
            timeout,
        )

        return provider.invoke(
            prompt,
            model=model,
            timeout=timeout,
            cwd=self.project_path,
        )

    def execute(self, state: State) -> PhaseResult:
        """Execute the handler."""
        try:
            prompt = self.render_prompt(state)
            result = self.invoke_provider(prompt)

            if result.exit_code != 0:
                error_msg = result.stderr or f"Provider exited with code {result.exit_code}"
                return PhaseResult.fail(error_msg)

            return PhaseResult.ok(
                {
                    "response": result.stdout,
                    "model": result.model,
                    "duration_ms": result.duration_ms,
                }
            )

        except ConfigError as e:
            logger.error("Handler config error: %s", e)
            return PhaseResult.fail(str(e))
        except ProviderExitCodeError as e:
            logger.error("Provider error: %s", e)
            return PhaseResult.fail(str(e))
        except Exception as e:
            logger.error("Handler execution failed: %s", e, exc_info=True)
            return PhaseResult.fail(f"Handler error: {e}")
