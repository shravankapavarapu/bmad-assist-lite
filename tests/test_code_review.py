"""Tests for the CODE_REVIEW handler's empty-multi fallback.

The fallback exists because ``providers.multi`` may legally be empty.  When it
is, the master model reviews its own work — a Rule-3 (no self-verification)
degradation that must be loud, and must never carry write tools.
"""

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler
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
        stdout="review body",
        stderr="",
        exit_code=0,
        duration_ms=1,
        model="opus",
        command=("claude",),
    )
    return provider


def _stub_multi_run(result: Any) -> Any:
    """Replace run_async_in_thread, closing the coroutine it is handed."""

    def _runner(coro: Any) -> Any:
        coro.close()
        return result

    return _runner


def _run_fallback(tmp_path: Any, config_data: dict[str, Any]) -> MagicMock:
    """Execute the handler through its empty-multi fallback; return the provider."""
    config = load_config(config_data)
    handler = CodeReviewHandler(config, tmp_path)
    provider = _fake_provider()

    with patch(
        "bmad_assist_lite.loop.handlers.base.get_provider", return_value=provider
    ), patch.object(CodeReviewHandler, "render_prompt", return_value="PROMPT"):
        handler.execute(State(current_epic=1, current_story="1.1"))

    return provider


# ============================================================================
# Part 1 — the fallback reviewer is read-only
# ============================================================================


class TestFallbackToolRestriction:
    """The master fallback must be as tool-restricted as the multi path."""

    def test_fallback_invocation_receives_read_only_tools(self, tmp_path):
        """LOAD-BEARING: an empty multi still yields a read-only reviewer."""
        provider = _run_fallback(tmp_path, CONFIG_EMPTY_MULTI)

        provider.invoke.assert_called_once()
        kwargs = provider.invoke.call_args.kwargs
        assert kwargs["allowed_tools"] == list(READ_ONLY_TOOLS)

    def test_fallback_reviewer_cannot_run_shell_commands(self, tmp_path):
        """NEG: Bash/Write/Edit never reach the fallback reviewer."""
        provider = _run_fallback(tmp_path, CONFIG_EMPTY_MULTI)

        allowed = provider.invoke.call_args.kwargs["allowed_tools"]
        assert allowed is not None
        for forbidden in ("Bash", "Write", "Edit"):
            assert forbidden not in allowed

    def test_missing_multi_key_behaves_like_empty_multi(self, tmp_path):
        """Omitting providers.multi entirely is the default case, and is guarded."""
        provider = _run_fallback(
            tmp_path, {"providers": {"master": {"provider": "claude", "model": "opus"}}}
        )

        assert provider.invoke.call_args.kwargs["allowed_tools"] == list(READ_ONLY_TOOLS)


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
        assert any("code_review" in m for m in warnings)
        assert any("providers.multi" in m for m in warnings)

    def test_warning_names_the_remedy(self, tmp_path, caplog):
        """A warning that does not say what to do is a defect — it must."""
        caplog.set_level(logging.WARNING)
        _run_fallback(tmp_path, CONFIG_EMPTY_MULTI)

        text = "\n".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert "providers.multi" in text
        assert "Fix:" in text
        assert "reviewer" in text.lower()

    def test_no_warning_when_an_independent_reviewer_is_configured(self, tmp_path, caplog):
        """NEG: a properly configured multi list stays silent."""
        caplog.set_level(logging.WARNING)
        config = load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "opus"},
                    "multi": [{"provider": "claude", "model": "sonnet"}],
                }
            }
        )
        handler = CodeReviewHandler(config, tmp_path)
        with patch.object(CodeReviewHandler, "render_prompt", return_value="PROMPT"), patch(
            "bmad_assist_lite.loop.handlers.code_review.run_async_in_thread",
            side_effect=_stub_multi_run(
                [{"reviewer": "Reviewer-1", "response": "ok", "exit_code": 0}]
            ),
        ):
            handler.execute(State(current_epic=1, current_story="1.1"))

        text = "\n".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert "self-verification" not in text


@pytest.mark.parametrize("phase_handler", [CodeReviewHandler])
class TestMasterDuplicateReviewer:
    """A reviewer identical to the master is self-review with extra steps."""

    def test_multi_entry_equal_to_master_is_detected(self, tmp_path, caplog, phase_handler):
        """NEG: same provider AND same model as master triggers the warning."""
        caplog.set_level(logging.WARNING)
        config = load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "opus"},
                    "multi": [{"provider": "claude", "model": "opus"}],
                }
            }
        )
        handler = phase_handler(config, tmp_path)
        with patch.object(phase_handler, "render_prompt", return_value="PROMPT"), patch(
            "bmad_assist_lite.loop.handlers.code_review.run_async_in_thread",
            side_effect=_stub_multi_run(
                [{"reviewer": "Reviewer-1", "response": "ok", "exit_code": 0}]
            ),
        ):
            handler.execute(State(current_epic=1, current_story="1.1"))

        text = "\n".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert "providers.multi" in text
