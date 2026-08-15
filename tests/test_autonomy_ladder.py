"""Every phase declares what it is allowed to do, and the declaration is enforced.

Before this, "code review is read-only, synthesis may execute commands" was a
sentence in `CLAUDE.md` plus two hand-written `get_allowed_tools()` overrides.
Two phases had been remembered; nothing made the next phase remember, and
nothing stopped a subclass from quietly widening one that had.

The ladder turns that convention into a typed, per-phase declaration resolved at
the invocation point. The tests below cover the declaration (a new phase cannot
skip it), the mapping (a level means one tool set), and — per G12 — the runtime
guards that hold when the static shape is defeated by a subclass override.
"""

import pytest

from bmad_assist_lite.core.exceptions import ConfigError
from bmad_assist_lite.loop.autonomy import (
    NON_LLM_PHASES,
    AutonomyLevel,
    allowed_tools_for,
)
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler
from bmad_assist_lite.loop.handlers.code_review_synthesis import CodeReviewSynthesisHandler
from bmad_assist_lite.loop.handlers.create_story import CreateStoryHandler
from bmad_assist_lite.loop.handlers.dev_story import DevStoryHandler
from bmad_assist_lite.loop.handlers.epic_quality_gate import EpicQualityGateHandler
from bmad_assist_lite.loop.handlers.fix_quality_gate import FixQualityGateHandler
from bmad_assist_lite.loop.handlers.fix_review import FixReviewHandler
from bmad_assist_lite.loop.handlers.quality_gate import QualityGateHandler
from bmad_assist_lite.loop.handlers.retrospective import RetrospectiveHandler
from bmad_assist_lite.loop.handlers.validate_story import ValidateStoryHandler
from bmad_assist_lite.loop.handlers.validate_story_synthesis import (
    ValidateStorySynthesisHandler,
)
from bmad_assist_lite.providers.base import READ_ONLY_TOOLS

WRITE_OR_EXEC = frozenset({"Edit", "Write", "Bash", "WebFetch", "WebSearch"})

ALL_LLM_HANDLERS = [
    CreateStoryHandler,
    ValidateStoryHandler,
    ValidateStorySynthesisHandler,
    DevStoryHandler,
    CodeReviewHandler,
    CodeReviewSynthesisHandler,
    FixQualityGateHandler,
    FixReviewHandler,
    RetrospectiveHandler,
]


# ============================================================================
# The declaration
# ============================================================================


@pytest.mark.parametrize("handler_cls", ALL_LLM_HANDLERS, ids=lambda c: c.__name__)
def test_every_llm_handler_declares_a_level(handler_cls: type[BaseHandler]) -> None:
    """No phase is allowed to inherit its permissions by accident."""
    assert isinstance(handler_cls.autonomy, AutonomyLevel)


def test_a_new_phase_cannot_skip_the_declaration() -> None:
    """LOAD-BEARING: defining a handler without a level is an import-time error.

    This is what makes the ladder a guard rather than a convention: the next
    phase cannot be added without answering the question.
    """
    with pytest.raises(TypeError, match="autonomy"):

        class _Undeclared(BaseHandler):
            @property
            def phase_name(self) -> str:
                return "undeclared"

            def build_context(self, state: object) -> dict[str, object]:
                return {}


def test_a_declared_plugin_phase_is_accepted() -> None:
    """NEG — the guard rejects only the omission, not third-party phases."""

    class _Declared(BaseHandler):
        autonomy = AutonomyLevel.READ_ONLY

        @property
        def phase_name(self) -> str:
            return "declared"

        def build_context(self, state: object) -> dict[str, object]:
            return {}

    assert _Declared.autonomy is AutonomyLevel.READ_ONLY


# ============================================================================
# The mapping: one level means exactly one tool set
# ============================================================================


def test_read_only_maps_to_the_shared_allowlist() -> None:
    assert allowed_tools_for(AutonomyLevel.READ_ONLY) == list(READ_ONLY_TOOLS)


def test_write_cannot_run_shell_commands() -> None:
    tools = allowed_tools_for(AutonomyLevel.WRITE)
    assert tools is not None
    assert "Bash" not in tools
    assert {"Edit", "Write"} <= set(tools)


def test_execute_is_unrestricted() -> None:
    assert allowed_tools_for(AutonomyLevel.EXECUTE) is None


def test_non_llm_has_no_tool_set_at_all() -> None:
    """A non-LLM phase has no provider invocation, so asking is a bug."""
    with pytest.raises(ConfigError, match="non-LLM"):
        allowed_tools_for(AutonomyLevel.NON_LLM)


# ============================================================================
# The two constraints that must not move (T25/F-13, ADR-0006)
# ============================================================================


@pytest.mark.parametrize(
    "handler_cls", [ValidateStoryHandler, CodeReviewHandler], ids=lambda c: c.__name__
)
def test_judging_phases_are_read_only(handler_cls: type[BaseHandler]) -> None:
    """F-13/T25: a reviewer with write tools can fix what it is judging."""
    assert handler_cls.autonomy is AutonomyLevel.READ_ONLY
    tools = allowed_tools_for(handler_cls.autonomy)
    assert tools is not None
    assert not (WRITE_OR_EXEC & set(tools))


def test_quality_gate_phases_stay_non_llm() -> None:
    """ADR-0006: the gate is deterministic, so it has no provider path at all."""
    assert NON_LLM_PHASES == frozenset({"quality_gate", "epic_quality_gate"})
    for handler_cls in (QualityGateHandler, EpicQualityGateHandler):
        assert not issubclass(handler_cls, BaseHandler), (
            f"{handler_cls.__name__} must not inherit the provider invocation path"
        )


def test_synthesis_may_execute_commands() -> None:
    """Single-master phases run the build; that asymmetry is the point."""
    assert CodeReviewSynthesisHandler.autonomy is AutonomyLevel.EXECUTE


# ============================================================================
# G12: runtime guards, because a class attribute can be overridden
# ============================================================================


class _WideningReviewer(CodeReviewHandler):
    """A subclass that tries to hand write tools to a read-only phase."""

    def get_allowed_tools(self) -> list[str] | None:
        return ["Read", "Write", "Bash"]


def test_widening_a_read_only_phase_is_caught_at_invocation(monkeypatch) -> None:
    """LOAD-BEARING (G12): the declared level is re-checked where it is used.

    The declaration is a class attribute and `get_allowed_tools()` is a method,
    so the static shape is defeated by one override. The invocation point asserts
    the resolved tool set still matches the declared level, which is the check
    that actually binds.
    """
    from bmad_assist_lite.core.config import load_config

    config = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
    handler = _WideningReviewer(config, __import__("pathlib").Path("."))

    with pytest.raises(ConfigError, match="read_only"):
        handler.invoke_provider("prompt")


def test_a_non_llm_level_refuses_to_invoke_a_provider() -> None:
    """LOAD-BEARING: declaring NON_LLM and then calling a provider is refused."""
    from pathlib import Path

    from bmad_assist_lite.core.config import load_config

    class _SneakyGate(BaseHandler):
        autonomy = AutonomyLevel.NON_LLM

        @property
        def phase_name(self) -> str:
            return "quality_gate"

        def build_context(self, state: object) -> dict[str, object]:
            return {}

    config = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
    handler = _SneakyGate(config, Path("."))

    with pytest.raises(ConfigError, match="non-LLM"):
        handler.invoke_provider("prompt")
