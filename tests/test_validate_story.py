"""Tests for the VALIDATE_STORY handler's empty-multi fallback.

Mirrors ``tests/test_code_review.py`` — the same fallback, the same defect
class: with no independent validator the master validates its own story, and
it must at minimum do so read-only and loudly.
"""

import logging
from typing import Any
from unittest.mock import MagicMock, patch

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.validate_story import ValidateStoryHandler
from bmad_assist_lite.providers.base import READ_ONLY_TOOLS, ProviderResult

# ============================================================================
# Helpers
# ============================================================================


CONFIG_EMPTY_MULTI: dict[str, Any] = {
    "providers": {
        "master": {"provider": "claude", "model": "opus"},
        "multi": [],
    },
}


def _fake_provider() -> MagicMock:
    """Return a provider double that records its invocation kwargs."""
    provider = MagicMock()
    provider.provider_name = "claude"
    provider.default_model = "opus"
    provider.invoke.return_value = ProviderResult(
        stdout="validation body",
        stderr="",
        exit_code=0,
        duration_ms=1,
        model="opus",
        command=("claude",),
    )
    return provider


def _run_fallback(tmp_path: Any, config_data: dict[str, Any]) -> MagicMock:
    """Execute the handler through its empty-multi fallback; return the provider."""
    config = load_config(config_data)
    handler = ValidateStoryHandler(config, tmp_path)
    provider = _fake_provider()

    with patch(
        "bmad_assist_lite.loop.handlers.base.get_provider", return_value=provider
    ), patch.object(ValidateStoryHandler, "render_prompt", return_value="PROMPT"):
        handler.execute(State(current_epic=1, current_story="1.1"))

    return provider


# ============================================================================
# Part 1 — the fallback validator is read-only
# ============================================================================


class TestFallbackToolRestriction:
    """The master fallback must be as tool-restricted as the multi path."""

    def test_fallback_invocation_receives_read_only_tools(self, tmp_path):
        """LOAD-BEARING: an empty multi still yields a read-only validator."""
        provider = _run_fallback(tmp_path, CONFIG_EMPTY_MULTI)

        provider.invoke.assert_called_once()
        kwargs = provider.invoke.call_args.kwargs
        assert kwargs["allowed_tools"] == list(READ_ONLY_TOOLS)

    def test_fallback_validator_cannot_run_shell_commands(self, tmp_path):
        """NEG: Bash/Write/Edit never reach the fallback validator."""
        provider = _run_fallback(tmp_path, CONFIG_EMPTY_MULTI)

        allowed = provider.invoke.call_args.kwargs["allowed_tools"]
        assert allowed is not None
        for forbidden in ("Bash", "Write", "Edit"):
            assert forbidden not in allowed


# ============================================================================
# Part 2 — the degradation is loud and actionable at phase run
# ============================================================================


class TestPhaseRunWarning:
    """The self-review condition is announced where the phase runs."""

    def test_warning_fires_at_phase_run(self, tmp_path, caplog):
        """An empty multi logs a warning naming the phase and the condition."""
        caplog.set_level(logging.WARNING)
        _run_fallback(tmp_path, CONFIG_EMPTY_MULTI)

        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("validate_story" in m for m in warnings)
        assert any("providers.multi" in m for m in warnings)

    def test_warning_names_the_remedy(self, tmp_path, caplog):
        """A warning that does not say what to do is a defect — it must."""
        caplog.set_level(logging.WARNING)
        _run_fallback(tmp_path, CONFIG_EMPTY_MULTI)

        text = "\n".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert "providers.multi" in text
        assert "Fix:" in text
