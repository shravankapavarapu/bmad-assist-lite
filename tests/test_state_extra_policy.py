"""The unknown-key policy of every persisted state model is declared, not inherited.

Three models persist state to disk and they did not agree about unknown keys.
Two of them (``State``, ``ParallelState``) never declared a policy at all and so
took Pydantic's silent runtime default of ``extra='ignore'``; ``SprintStatus``
declared ``extra='allow'`` and kept them. A key typed into the wrong file
therefore vanished from two files and survived in the third, and nothing in the
source said that was intended.

These tests pin the policy per model *and* pin the two behavioural facts that
make the split correct, so that a future edit which flattens the three to one
setting fails here with the reason attached rather than silently trading one
kind of data loss for another.
"""

import pytest
from pydantic import BaseModel

from bmad_assist_lite.core.state import (
    EXTRA_POLICY,
    PRESERVE_UNKNOWN_KEYS,
    State,
    log_ignored_fields,
)
from bmad_assist_lite.core.sprint_status import SprintStatus
from bmad_assist_lite.parallel.state import (
    GateObservation,
    MergeAttempt,
    ParallelState,
    StoryState,
)

# ============================================================================
# The policy is declared on every persisted model
# ============================================================================

TOOL_OWNED = [State, ParallelState, StoryState, MergeAttempt, GateObservation]


@pytest.mark.parametrize("model_cls", TOOL_OWNED, ids=lambda c: c.__name__)
def test_tool_owned_models_declare_drop_explicitly(model_cls: type[BaseModel]) -> None:
    """Tool-owned state models declare ``extra='ignore'`` rather than inheriting it."""
    assert model_cls.model_config.get("extra") == "ignore", (
        f"{model_cls.__name__} must declare its unknown-key policy explicitly. "
        "Relying on Pydantic's default is what made the three models disagree."
    )


def test_externally_authored_model_declares_preserve() -> None:
    """``SprintStatus`` keeps unknown keys: the file has authors other than us."""
    assert SprintStatus.model_config.get("extra") == "allow"


def test_every_persisted_model_is_registered_in_the_policy_table() -> None:
    """The table is the documentation, so it must name every persisted model."""
    assert set(EXTRA_POLICY) == {c.__name__ for c in [*TOOL_OWNED, SprintStatus]}
    for model_cls in [*TOOL_OWNED, SprintStatus]:
        declared = EXTRA_POLICY[model_cls.__name__]
        assert model_cls.model_config.get("extra") == declared


# ============================================================================
# Why the split is correct — the two facts that forbid flattening it
# ============================================================================


def test_preserving_unknown_keys_would_break_typo_detection_on_state() -> None:
    """``State`` is mutated in place, so ``extra='allow'`` would accept typos.

    ``update_position()`` assigns attributes on a live ``State``. Under
    ``extra='allow'`` a misspelled attribute name silently becomes a new field
    instead of raising, which is the failure this policy prevents.
    """
    state = State()
    with pytest.raises(ValueError):
        state.current_phse = "dev_story"  # type: ignore[attr-defined]


def test_dropping_unknown_keys_would_destroy_sprint_status_content() -> None:
    """``SprintStatus`` round-trips top-level keys this tool does not model."""
    raw = {
        "project": "demo",
        "current_sprint": 4,
        "totals": {"done": 3},
        "development_status": {"story-1-1": "done"},
    }
    dumped = SprintStatus.model_validate(raw).model_dump()
    for key in ("project", "current_sprint", "totals"):
        assert key in dumped, f"{key} must survive a load/save round-trip"


def test_dropped_keys_are_never_silent() -> None:
    """A tool-owned model that drops a key says so, once, per source."""
    from bmad_assist_lite.core.state import _reset_ignored_field_log

    _reset_ignored_field_log()
    ignored = log_ignored_fields(State, {"current_story": "1.1", "bogus": 1}, "s.yaml")
    assert ignored == ("bogus",)


def test_preserve_flag_matches_the_declared_policy() -> None:
    """``PRESERVE_UNKNOWN_KEYS`` is the single readable statement of the rule."""
    assert PRESERVE_UNKNOWN_KEYS == {"SprintStatus"}
