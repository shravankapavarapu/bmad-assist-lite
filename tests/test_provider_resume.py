"""Tests for the provider-level session-resume plumbing (L4 attribution).

The ``resume`` capability itself is kept (the Claude provider sets
``resumed_session_id``/``session_reused`` for L4 attribution, and
``invoke_provider`` forwards a ``resume`` id to the provider). These two classes
were salvaged from the retired L2 reviewer-self-resume test suite: they exercise
the surviving provider plumbing, not the deleted reuse holder.
"""

from typing import Any
from unittest.mock import MagicMock, patch

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.loop.handlers.code_review_synthesis import (
    CodeReviewSynthesisHandler,
)
from bmad_assist_lite.providers.base import ProviderResult
from bmad_assist_lite.providers.claude_sdk import ClaudeSDKProvider

_MASTER = {"provider": "claude", "model": "opus"}
_MULTI = [{"provider": "claude", "model": "sonnet"}, {"provider": "claude", "model": "haiku"}]


def _config() -> Any:
    return load_config({"providers": {"master": _MASTER, "multi": _MULTI}})


# ============================================================================
# Provider sets the L4 attribution fields on a resume
# ============================================================================


def _fake_query(messages: list[Any]) -> Any:
    async def gen() -> Any:
        for m in messages:
            yield m

    return gen()


def _result_msg(session_id: str = "returned-sess") -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=False,
        num_turns=1,
        session_id=session_id,
        total_cost_usd=0.0,
        usage=None,
    )


class TestProviderResumeAttribution:
    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_resume_sets_l4_fields(self, mock_query: MagicMock) -> None:
        mock_query.return_value = _fake_query(
            [AssistantMessage(content=[TextBlock(text="ok")], model="sonnet"), _result_msg()]
        )
        result = ClaudeSDKProvider().invoke("prompt", timeout=300, resume="prior-sess")
        assert result.resumed_session_id == "prior-sess"
        assert result.session_reused is True
        # provider_session_id remains what the CLI returned, distinct from resumed.
        assert result.provider_session_id == "returned-sess"

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_no_resume_leaves_fields_cold(self, mock_query: MagicMock) -> None:
        mock_query.return_value = _fake_query(
            [AssistantMessage(content=[TextBlock(text="ok")], model="sonnet"), _result_msg()]
        )
        result = ClaudeSDKProvider().invoke("prompt", timeout=300)
        assert result.resumed_session_id is None
        assert result.session_reused is False


# ============================================================================
# invoke_provider forwards resume to the provider
# ============================================================================


class TestInvokeProviderForwardsResume:
    def test_resume_reaches_provider(self, tmp_path: Any) -> None:
        cfg = _config()
        handler = CodeReviewSynthesisHandler(cfg, tmp_path)
        provider = MagicMock()
        provider.provider_name = "claude"
        provider.default_model = "opus"
        provider.invoke.return_value = ProviderResult(
            stdout="synth",
            stderr="",
            exit_code=0,
            duration_ms=1,
            model="opus",
            command=("claude",),
        )
        with patch(
            "bmad_assist_lite.loop.handlers.base.get_provider", return_value=provider
        ):
            handler.invoke_provider("PROMPT", resume="synth-sess-1")
        assert provider.invoke.call_args.kwargs["resume"] == "synth-sess-1"
