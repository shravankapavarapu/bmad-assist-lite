"""The dedicated code_review_synthesis provider role (providers.synthesis).

The epic-11 journal pull (2026-09-07) located the review loop's judgment loss
in the synthesis seat: the cheap master assigns every severity, applies the
blocking threshold, and arbitrates reviewer lanes whose measured consensus was
0-7%. This role puts a stronger model on that seat. Absent, behaviour must be
byte-identical to before — the master synthesizes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.loop.handlers.code_review_synthesis import (
    CodeReviewSynthesisHandler,
)

BASE: dict[str, Any] = {
    "providers": {
        "master": {"provider": "claude", "model": "claude-opus-5", "effort": "medium"},
        "multi": [{"provider": "claude", "model": "claude-fable-5", "effort": "xhigh"}],
    },
    "loop": {"review_max_iterations": 2},
}


def _with_synthesis() -> dict[str, Any]:
    config = json.loads(json.dumps(BASE))
    config["providers"]["synthesis"] = {
        "provider": "claude",
        "model": "claude-fable-5",
        "effort": "xhigh",
    }
    return config


class TestSynthesisRoleResolution:
    def test_neg_absent_role_resolves_the_master_model(self, tmp_path: Path) -> None:
        handler = CodeReviewSynthesisHandler(load_config(BASE), tmp_path)
        assert handler.get_model() == "claude-opus-5"

    def test_configured_role_resolves_its_own_model(self, tmp_path: Path) -> None:
        handler = CodeReviewSynthesisHandler(load_config(_with_synthesis()), tmp_path)
        assert handler.get_model() == "claude-fable-5"

    def test_neg_no_attempt_escalation_back_to_master(self, tmp_path: Path) -> None:
        """Refuse the retry downgrade.

        Escalation exists for routed models CHEAPER than the master; this
        role is stronger, so a retry must not downgrade.
        """
        handler = CodeReviewSynthesisHandler(load_config(_with_synthesis()), tmp_path)
        assert handler.get_model(attempt=2) == "claude-fable-5"

    def test_role_effort_is_carried_unless_the_call_site_sets_one(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        handler = CodeReviewSynthesisHandler(load_config(_with_synthesis()), tmp_path)
        captured: dict[str, Any] = {}

        import bmad_assist_lite.loop.handlers.base as base_module

        def _fake_base_invoke(self: Any, prompt: str, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return None

        monkeypatch.setattr(base_module.BaseHandler, "invoke_provider", _fake_base_invoke)
        handler.invoke_provider("PROMPT")
        assert captured.get("effort") == "xhigh"

        captured.clear()
        handler.invoke_provider("PROMPT", effort="low")
        assert captured.get("effort") == "low"

    def test_other_handlers_are_untouched_by_the_role(self, tmp_path: Path) -> None:
        """The role is scoped to the synthesis seat.

        The dev handler must keep resolving the master.
        """
        from bmad_assist_lite.loop.handlers.dev_story import DevStoryHandler

        handler = DevStoryHandler(load_config(_with_synthesis()), tmp_path)
        assert handler.get_model() == "claude-opus-5"
