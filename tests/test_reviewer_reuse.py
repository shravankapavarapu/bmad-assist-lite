"""Tests for L2 reviewer-lane self-resume (goal-run5 Phase 2).

Three layers:
  * the holder helper (gating, keying, pruning, F-13 structural safety),
  * the Claude provider setting resumed_session_id/session_reused on a resume,
  * the code_review fan-out threading resume + capturing round-1 sessions so a
    round-2 re-review resumes each reviewer's OWN session.
"""

from typing import Any
from unittest.mock import MagicMock, patch

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers import reviewer_reuse
from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler
from bmad_assist_lite.providers.base import ProviderResult
from bmad_assist_lite.providers.claude_sdk import ClaudeSDKProvider

_MASTER = {"provider": "claude", "model": "opus"}
_MULTI = [{"provider": "claude", "model": "sonnet"}, {"provider": "claude", "model": "haiku"}]


def _config(*, reviewer_self_resume: bool) -> Any:
    return load_config(
        {
            "providers": {"master": _MASTER, "multi": _MULTI},
            "session_reuse": {"reviewer_self_resume": reviewer_self_resume},
        }
    )


# ============================================================================
# Holder helper
# ============================================================================


class TestLaneKey:
    def test_includes_story_phase_index_provider_model(self) -> None:
        k = reviewer_reuse.lane_key("3.1", "code_review", 0, "claude", "sonnet")
        assert k == "3.1#code_review#0#claude#sonnet"

    def test_distinct_across_every_dimension(self) -> None:
        base = reviewer_reuse.lane_key("3.1", "code_review", 0, "claude", "sonnet")
        assert base != reviewer_reuse.lane_key("3.2", "code_review", 0, "claude", "sonnet")
        assert base != reviewer_reuse.lane_key("3.1", "validate_story", 0, "claude", "sonnet")
        assert base != reviewer_reuse.lane_key("3.1", "code_review", 1, "claude", "sonnet")
        assert base != reviewer_reuse.lane_key("3.1", "code_review", 0, "claude", "haiku")

    def test_none_model_renders_default(self) -> None:
        assert reviewer_reuse.lane_key("3.1", "code_review", 0, "claude", None).endswith("#default")


class TestResumeIdFor:
    def _state(self) -> State:
        s = State(current_epic=3, current_story="3.1")
        s.reviewer_session_ids = {"3.1#code_review#0#claude#sonnet": "sess-1"}
        return s

    def test_none_when_flag_off(self) -> None:
        cfg = _config(reviewer_self_resume=False)
        got = reviewer_reuse.resume_id_for(
            self._state(), cfg, provider="claude", key="3.1#code_review#0#claude#sonnet"
        )
        assert got is None

    def test_none_for_non_claude(self) -> None:
        cfg = _config(reviewer_self_resume=True)
        got = reviewer_reuse.resume_id_for(
            self._state(), cfg, provider="gemini", key="3.1#code_review#0#claude#sonnet"
        )
        assert got is None

    def test_none_when_nothing_stored(self) -> None:
        cfg = _config(reviewer_self_resume=True)
        got = reviewer_reuse.resume_id_for(
            State(current_story="3.1"), cfg, provider="claude", key="missing"
        )
        assert got is None

    def test_returns_stored_id_when_on(self) -> None:
        cfg = _config(reviewer_self_resume=True)
        got = reviewer_reuse.resume_id_for(
            self._state(), cfg, provider="claude", key="3.1#code_review#0#claude#sonnet"
        )
        assert got == "sess-1"


class TestCaptureSession:
    def _result(self, sid: str | None) -> ProviderResult:
        return ProviderResult(
            stdout="x",
            stderr="",
            exit_code=0,
            duration_ms=1,
            model="claude",
            command=("claude",),
            provider_session_id=sid,
        )

    def test_noop_when_flag_off(self) -> None:
        cfg = _config(reviewer_self_resume=False)
        s = State(current_story="3.1")
        reviewer_reuse.capture_session(
            s, cfg, story_id="3.1", provider="claude", key="k", result=self._result("sess-1")
        )
        assert s.reviewer_session_ids == {}

    def test_noop_for_non_claude(self) -> None:
        cfg = _config(reviewer_self_resume=True)
        s = State(current_story="3.1")
        reviewer_reuse.capture_session(
            s, cfg, story_id="3.1", provider="gemini", key="k", result=self._result("sess-1")
        )
        assert s.reviewer_session_ids == {}

    def test_noop_when_no_session_id(self) -> None:
        cfg = _config(reviewer_self_resume=True)
        s = State(current_story="3.1")
        reviewer_reuse.capture_session(
            s, cfg, story_id="3.1", provider="claude", key="k", result=self._result(None)
        )
        assert s.reviewer_session_ids == {}

    def test_stores_when_on(self) -> None:
        cfg = _config(reviewer_self_resume=True)
        s = State(current_story="3.1")
        reviewer_reuse.capture_session(
            s,
            cfg,
            story_id="3.1",
            provider="claude",
            key="3.1#code_review#0#claude#sonnet",
            result=self._result("sess-1"),
        )
        assert s.reviewer_session_ids["3.1#code_review#0#claude#sonnet"] == "sess-1"

    def test_prunes_stale_story_entries(self) -> None:
        """A previous story's lanes are pruned so the holder stays bounded."""
        cfg = _config(reviewer_self_resume=True)
        s = State(current_story="3.2")
        s.reviewer_session_ids = {
            "3.1#code_review#0#claude#sonnet": "old",  # stale story
            "3.2#code_review#0#claude#sonnet": "keep",  # current story
        }
        reviewer_reuse.capture_session(
            s,
            cfg,
            story_id="3.2",
            provider="claude",
            key="3.2#code_review#1#claude#haiku",
            result=self._result("new"),
        )
        assert "3.1#code_review#0#claude#sonnet" not in s.reviewer_session_ids
        assert s.reviewer_session_ids["3.2#code_review#0#claude#sonnet"] == "keep"
        assert s.reviewer_session_ids["3.2#code_review#1#claude#haiku"] == "new"

    def test_dev_session_id_never_written_by_reviewers(self) -> None:
        """F-13 structural: only reviewer lanes write here; a dev key never appears.

        The helper writes exactly the key it is given, and every caller keys by
        the reviewer/synthesis lane. There is no code path that writes a
        dev_story session id into this holder, so a reviewer can never resume it.
        """
        cfg = _config(reviewer_self_resume=True)
        s = State(current_story="3.1")
        reviewer_reuse.capture_session(
            s,
            cfg,
            story_id="3.1",
            provider="claude",
            key="3.1#code_review#0#claude#sonnet",
            result=self._result("sess-review"),
        )
        assert all("dev_story" not in k for k in s.reviewer_session_ids)


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
# code_review fan-out: round-1 cold + capture, round-2 self-resume
# ============================================================================


class _FanoutRecorder:
    """Hands out fake claude providers; records every invoke's resume kwarg."""

    def __init__(self) -> None:
        self.resumes: list[str | None] = []
        self._created = 0

    def make_provider(self) -> Any:
        rec = self
        rec._created += 1
        my_session = f"sess-{rec._created}"  # unique per instance (created serially)

        class _Fake:
            provider_name = "claude"
            default_model = "opus"

            def invoke(self, prompt: str, **kwargs: Any) -> ProviderResult:
                rec.resumes.append(kwargs.get("resume"))
                return ProviderResult(
                    stdout="review body",
                    stderr="",
                    exit_code=0,
                    duration_ms=1,
                    model="claude",
                    command=("claude",),
                    provider_session_id=my_session,
                )

            def parse_output(self, r: ProviderResult) -> str:
                return r.stdout.strip()

        return _Fake()


def _run_fanout(handler: CodeReviewHandler, state: State, rec: _FanoutRecorder) -> None:
    with patch(
        "bmad_assist_lite.loop.handlers.code_review.get_provider",
        side_effect=lambda name: rec.make_provider(),
    ), patch.object(CodeReviewHandler, "render_prompt", return_value="PROMPT"), patch.object(
        CodeReviewHandler, "build_system_prompt", return_value=None
    ):
        handler.execute(state)


class TestFanoutSelfResume:
    def test_round1_cold_then_round2_resumes_own_sessions(self, tmp_path: Any) -> None:
        cfg = _config(reviewer_self_resume=True)
        handler = CodeReviewHandler(cfg, tmp_path)
        state = State(current_epic=3, current_story="3.1")

        # Round 1: both reviewers cold; sessions captured per lane.
        rec1 = _FanoutRecorder()
        _run_fanout(handler, state, rec1)
        assert rec1.resumes == [None, None]
        assert len(state.reviewer_session_ids) == 2
        captured = set(state.reviewer_session_ids.values())
        assert captured == {"sess-1", "sess-2"}

        # Round 2 (same story/state): each lane resumes its OWN round-1 session.
        rec2 = _FanoutRecorder()
        _run_fanout(handler, state, rec2)
        assert set(rec2.resumes) == captured  # order-independent: resumed exactly round-1's ids
        assert None not in rec2.resumes

    def test_flag_off_never_resumes_or_captures(self, tmp_path: Any) -> None:
        cfg = _config(reviewer_self_resume=False)
        handler = CodeReviewHandler(cfg, tmp_path)
        state = State(current_epic=3, current_story="3.1")

        rec1 = _FanoutRecorder()
        _run_fanout(handler, state, rec1)
        assert rec1.resumes == [None, None]
        assert state.reviewer_session_ids == {}

        rec2 = _FanoutRecorder()
        _run_fanout(handler, state, rec2)
        assert rec2.resumes == [None, None]

    def test_new_story_does_not_resume_prior_story(self, tmp_path: Any) -> None:
        cfg = _config(reviewer_self_resume=True)
        handler = CodeReviewHandler(cfg, tmp_path)

        s1 = State(current_epic=3, current_story="3.1")
        _run_fanout(handler, s1, _FanoutRecorder())
        # Carry the same holder into story 3.2 (as the real State does).
        s2 = State(current_epic=3, current_story="3.2")
        s2.reviewer_session_ids = dict(s1.reviewer_session_ids)

        rec = _FanoutRecorder()
        _run_fanout(handler, s2, rec)
        assert rec.resumes == [None, None]  # story 3.2 round 1 is cold, not 3.1's sessions
        # And 3.1's stale lanes were pruned once 3.2 captured.
        assert all(k.startswith("3.2#") for k in s2.reviewer_session_ids)


# ============================================================================
# invoke_provider forwards resume (the synthesis lane's mechanism)
# ============================================================================


class TestInvokeProviderForwardsResume:
    def test_resume_reaches_provider(self, tmp_path: Any) -> None:
        from bmad_assist_lite.loop.handlers.code_review_synthesis import (
            CodeReviewSynthesisHandler,
        )

        cfg = _config(reviewer_self_resume=True)
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
