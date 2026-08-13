"""Base handler class for phase execution."""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from bmad_assist_lite.compiler import compile_workflow
from bmad_assist_lite.compiler.types import CompilerContext
from bmad_assist_lite.core.config import Config, get_phase_timeout, resolve_phase_model
from bmad_assist_lite.core.exceptions import CompilerError, ConfigError, ProviderExitCodeError
from bmad_assist_lite.core.paths import get_paths
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.autonomy import (
    AutonomyLevel,
    allowed_tools_for,
    assert_tools_match_level,
)
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers import get_provider
from bmad_assist_lite.providers.base import BaseProvider, ProviderResult, write_progress

logger = logging.getLogger(__name__)

#: Sentinel for ``invoke_provider`` overrides, so an explicit ``None`` / ``[]``
#: (e.g. "no tools") is distinguishable from "caller did not override".
_UNSET: Any = object()


class BaseHandler(ABC):
    """Abstract base class for phase handlers.

    Every concrete handler must declare an :class:`AutonomyLevel` as the class
    attribute ``autonomy``, stating what the phase is permitted to do. There is
    no default: a permissive default is how the old prose rule quietly failed
    to cover phases added after it was written.
    """

    autonomy: AutonomyLevel

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Refuse a concrete phase handler that has not declared its autonomy.

        Abstract intermediates are exempt — they are not phases. Everything
        else must answer the question before it can be defined at all, which is
        what turns the ladder from documentation into a guard.
        """
        super().__init_subclass__(**kwargs)

        if getattr(cls, "__abstractmethods__", None):
            return
        if not isinstance(getattr(cls, "autonomy", None), AutonomyLevel):
            raise TypeError(
                f"{cls.__name__} must declare an `autonomy` level "
                f"(one of {[lvl.name for lvl in AutonomyLevel]}). "
                "Every phase states what it is permitted to do; there is no default."
            )

    def __init__(self, config: Config, project_path: Path) -> None:
        """Initialize the handler with config and project path."""
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
            "solutions": self._solutions_block(state),
        }

    def _solutions_block(self, state: State) -> str:
        """Previously solved problems relevant to this phase, or an empty string.

        Tagged by phase and epic so a phase is only handed solutions that were
        filed as relevant to it. Off by default, and the disabled path does no
        filesystem work at all.
        """
        if not self.config.solutions.enabled:
            return ""

        from bmad_assist_lite.core.solutions import context_block_for

        tags = {self.phase_name}
        if state.current_epic is not None:
            tags.add(f"epic-{state.current_epic}")

        return context_block_for(
            self.project_path,
            tags,
            limit=self.config.solutions.max_injected,
            max_chars=self.config.solutions.max_injected_chars,
        )

    def render_prompt(self, state: State) -> str:
        """Render prompt using compiler."""
        workflow_name = self.phase_name.replace("_", "-")

        paths = get_paths()

        resolved_variables: dict[str, str | int | None] = {
            "epic_num": state.current_epic,
            "story_num": self._extract_story_num(state.current_story),
            "planning_artifacts": str(paths.planning_artifacts),
            "implementation_artifacts": str(paths.implementation_artifacts),
        }

        context = CompilerContext(
            project_root=self.project_path,
            output_folder=paths.output_folder,
            project_knowledge=paths.project_knowledge,
            resolved_variables=resolved_variables,
            stable_prefix=self.config.compiler.stable_prefix,
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

    def get_model(self, *, model: str | None = None, attempt: int = 1) -> str | None:
        """Resolve the model for this phase's provider invocation.

        Delegates to the single four-tier resolution point, which refuses to
        route any phase outside the closed routable set.
        """
        return resolve_phase_model(self.config, self.phase_name, override=model, attempt=attempt)

    def get_allowed_tools(self) -> list[str] | None:
        """Tool restriction applied to the master invocation.

        Derived from the phase's declared :class:`AutonomyLevel` rather than
        chosen per handler, so the permission and the tool set cannot drift
        apart. ``None`` means unrestricted, which is what ``EXECUTE`` resolves
        to. An override that widens the result is caught at the invocation
        point by :func:`assert_tools_match_level`.
        """
        return allowed_tools_for(self.autonomy)

    def build_system_prompt(self, state: State) -> str | None:
        """The epic's stable context as an appended, cached system prompt, or None.

        Gated on ``compiler.stable_prefix``. The block is byte-identical across the
        epic's phases and stories, so it caches once and is read cheaply on every
        later call. Off by default, in which case the CLI's default system prompt
        is used unchanged.
        """
        if not self.config.compiler.stable_prefix:
            return None

        from bmad_assist_lite.compiler.stable_context import build_stable_system_prompt

        paths = get_paths()
        context = CompilerContext(
            project_root=self.project_path,
            output_folder=paths.output_folder,
            project_knowledge=paths.project_knowledge,
            resolved_variables={
                "epic_num": state.current_epic,
                "story_num": self._extract_story_num(state.current_story),
                "planning_artifacts": str(paths.planning_artifacts),
                "implementation_artifacts": str(paths.implementation_artifacts),
            },
        )
        return build_stable_system_prompt(context)

    def _reviewer_stagger(self, system_prompt: str | None) -> float:
        """Seconds to delay each successive reviewer lane's start.

        The stagger only ever existed to let reviewer-1 warm the L1 stable-context
        cache before the others begin; it is already 0 when no system prompt is in
        play. SP-4 (``speed.remove_stagger``) drops it unconditionally.
        """
        if self.config.speed.remove_stagger:
            return 0.0
        return self.config.parallel_delay if system_prompt else 0.0

    def invoke_provider(
        self,
        prompt: str,
        *,
        model: str | None = None,
        attempt: int = 1,
        system_prompt: str | None = None,
        resume: str | None = None,
        allowed_tools: Any = _UNSET,
        effort: Any = _UNSET,
    ) -> ProviderResult:
        """Invoke the provider with the given prompt.

        Args:
            prompt: The compiled prompt to send.
            model: Per-invocation model override, honoured only for routable phases.
            attempt: 1-based attempt number; a retry escalates back to the master model.
            system_prompt: Stable context to append as a cached system prompt, or None.
            resume: Session id to resume (session reuse). Claude-only; None starts
                a fresh session. Used by the synthesis lane's round-2 self-resume.
            allowed_tools: Override the phase's tool allowlist. Left unset, the
                phase's declared autonomy resolves it; pass ``[]`` for a tool-free
                call (e.g. the SP-1 structured adjudication, which must not fix or
                explore). Still checked against the declared level.
            effort: Override the reasoning effort. Left unset, the master effort is
                used; SP-3 lowers it a notch for the lean review path.

        """
        provider = self.get_provider()
        model = self.get_model(model=model, attempt=attempt)
        timeout = get_phase_timeout(self.config, self.phase_name)
        if allowed_tools is _UNSET:
            allowed_tools = self.get_allowed_tools()
        if effort is _UNSET:
            effort = self.config.providers.master.effort

        # G12: the declaration above is a class attribute and the resolver is an
        # overridable method, so neither proves anything on its own. This is the
        # check that binds, on the path the provider is actually reached by.
        assert_tools_match_level(self.autonomy, allowed_tools, phase=self.phase_name)

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
            allowed_tools=allowed_tools,
            effort=effort,
            system_prompt=system_prompt,
            resume=resume,
        )

    def execute(self, state: State) -> PhaseResult:
        """Execute the handler."""
        try:
            prompt = self.render_prompt(state)
            system_prompt = self.build_system_prompt(state)
            result = self.invoke_provider(prompt, system_prompt=system_prompt)

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
