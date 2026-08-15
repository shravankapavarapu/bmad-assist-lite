"""The per-phase autonomy ladder: what each phase is permitted to do.

The rule this replaces was real but unenforced. ``CLAUDE.md`` states it in
prose — *"code-review runs multiple LLMs in parallel → read-only checks only;
code-review-synthesis runs a single Master LLM → safe for command execution"* —
and two handlers implemented it by overriding ``get_allowed_tools()`` by hand.
That is a convention: it holds for the phases someone remembered, and a phase
added later inherits the permissive default in silence.

The ladder makes the permission a **declared, typed property of the phase**.
``BaseHandler`` refuses to define a subclass that does not declare one, so the
question cannot be skipped, and the level is resolved into a tool set at the one
point where it matters — the provider invocation.

The rungs
---------
``NON_LLM``    The phase makes no provider call at all. Asking it for a tool set
              is a bug, not a permissive answer (ADR-0006 keeps the quality
              gates deterministic).
``READ_ONLY``  Read/Glob/Grep. The judging phases: a reviewer that can write can
              fix what it is judging and then approve it (F-13).
``WRITE``      May read and edit files; may not run shell commands.
``EXECUTE``    Unrestricted, including shell. The phases that must run the build.

Why the level is checked at the invocation point
------------------------------------------------
Per G12, a declaration that only exists as a class attribute is a smoke alarm:
one subclass overriding ``get_allowed_tools()`` defeats it, and the override is
exactly the shape of the bug the ladder exists to prevent. So the declared level
is re-checked against the tool set actually being handed to the provider, on the
real invocation path, where no amount of indirection can route around it.
"""

import logging
from enum import Enum

from bmad_assist_lite.core.exceptions import ConfigError
from bmad_assist_lite.providers.base import READ_ONLY_TOOLS

logger = logging.getLogger(__name__)

__all__ = [
    "NON_LLM_PHASES",
    "WRITE_TOOLS",
    "AutonomyLevel",
    "allowed_tools_for",
    "assert_tools_match_level",
]


class AutonomyLevel(Enum):
    """What a phase is permitted to do during its provider invocation."""

    NON_LLM = "non_llm"
    READ_ONLY = "read_only"
    WRITE = "write"
    EXECUTE = "execute"


WRITE_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "Edit", "Write")
"""Read plus file editing, deliberately without ``Bash``."""

NON_LLM_PHASES: frozenset[str] = frozenset({"quality_gate", "epic_quality_gate"})
"""Phases with no provider path at all (ADR-0006).

Their handlers do not subclass ``BaseHandler``, so the absence is structural
rather than merely declared; a test asserts that stays true.
"""

# Tools that write to the workspace or run commands. A read-only phase holding
# any of these is the F-13 failure, so this set is what the runtime guard tests.
_FORBIDDEN_FOR_READ_ONLY: frozenset[str] = frozenset(
    {"Edit", "Write", "Bash", "WebFetch", "WebSearch"}
)


def allowed_tools_for(level: AutonomyLevel) -> list[str] | None:
    """Resolve a level into the tool allowlist passed to the provider.

    Args:
        level: The phase's declared autonomy level.

    Returns:
        The allowlist, or None for an unrestricted invocation.

    Raises:
        ConfigError: If asked for the tool set of a non-LLM phase. Returning
            None here would read as "unrestricted" and hand a deterministic
            gate a full tool surface, so the ambiguity is refused instead.

    """
    if level is AutonomyLevel.NON_LLM:
        raise ConfigError(
            "A non-LLM phase has no provider invocation and therefore no tool set. "
            "Declaring NON_LLM and then invoking a provider is a contradiction "
            "(ADR-0006 keeps the quality gates deterministic)."
        )
    if level is AutonomyLevel.READ_ONLY:
        return list(READ_ONLY_TOOLS)
    if level is AutonomyLevel.WRITE:
        return list(WRITE_TOOLS)
    return None


def assert_tools_match_level(level: AutonomyLevel, tools: list[str] | None, *, phase: str) -> None:
    """Check a resolved tool set against the level the phase declared.

    This is the G12 runtime half of the ladder. The declaration is a class
    attribute and the resolver is an overridable method, so the static shape
    proves nothing on its own; this runs on the real invocation path.

    Args:
        level: The declared level.
        tools: The allowlist about to be handed to the provider.
        phase: Phase name, for the error message.

    Raises:
        ConfigError: If the tool set grants more than the level permits.

    """
    if level is AutonomyLevel.NON_LLM:
        raise ConfigError(
            f"Phase '{phase}' is declared non-LLM but reached a provider invocation. "
            "The quality gates are deterministic by decision (ADR-0006)."
        )

    if level is AutonomyLevel.READ_ONLY:
        if tools is None:
            raise ConfigError(
                f"Phase '{phase}' is declared read_only but was given an "
                "unrestricted tool set. A reviewer that can write can fix the "
                "code it is judging and then approve it (F-13)."
            )
        granted = _FORBIDDEN_FOR_READ_ONLY & set(tools)
        if granted:
            raise ConfigError(
                f"Phase '{phase}' is declared read_only but was granted "
                f"{sorted(granted)}. A reviewer that can write can fix the code "
                "it is judging and then approve it (F-13)."
            )
        return

    if level is AutonomyLevel.WRITE and (tools is None or "Bash" in tools):
        raise ConfigError(
            f"Phase '{phase}' is declared write but was granted shell access. "
            "Raise its declared level to EXECUTE if it genuinely needs to run "
            "commands, rather than widening it at the call site."
        )
