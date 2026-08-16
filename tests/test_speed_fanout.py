"""Execute-level tests for the speed pack's fan-out effects (goal-run6).

The unit tests in test_config.py / test_remove_stagger.py pin the helpers;
these pin what the reviewer/validator lanes ACTUALLY receive — effort, resume,
prompt addendum, stagger — so a regression at a call site cannot hide behind a
green helper test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler
from bmad_assist_lite.loop.handlers.validate_story import ValidateStoryHandler
from bmad_assist_lite.providers.base import ProviderResult


class _Recorder:
    """Hands out fake claude providers; records every invoke's kwargs."""

    def __init__(self) -> None:
        self.efforts: list[str | None] = []
        self.resumes: list[str | None] = []
        self.prompts: list[str] = []
        self._created = 0

    def make_provider(self) -> Any:
        rec = self
        rec._created += 1
        my_session = f"sess-{rec._created}"

        class _Fake:
            provider_name = "claude"
            default_model = "opus"

            def invoke(self, prompt: str, **kwargs: Any) -> ProviderResult:
                rec.efforts.append(kwargs.get("effort"))
                rec.resumes.append(kwargs.get("resume"))
                rec.prompts.append(prompt)
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


def _run_review_fanout(
    handler: CodeReviewHandler, state: State, rec: _Recorder
) -> None:
    with patch(
        "bmad_assist_lite.loop.handlers.code_review.get_provider",
        side_effect=lambda name: rec.make_provider(),
    ), patch.object(CodeReviewHandler, "render_prompt", return_value="PROMPT"), patch.object(
        CodeReviewHandler, "build_system_prompt", return_value=None
    ):
        handler.execute(state)


class TestDeltaRound2FreshSession:
    def test_round2_runs_fresh_scoped_session(self, tmp_path: Any) -> None:
        # SP-2 contract: a delta round-2 runs a FRESH session (no resumed
        # transcript) scoped to a delta review prompt.
        cfg = load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "opus"},
                    "multi": [
                        {"provider": "claude", "model": "sonnet"},
                        {"provider": "claude", "model": "haiku"},
                    ],
                },
                "speed": {"delta_round2": True},
            }
        )
        handler = CodeReviewHandler(cfg, tmp_path)
        state = State(current_epic=3, current_story="3.1")

        # Round 1: cold.
        rec1 = _Recorder()
        _run_review_fanout(handler, state, rec1)
        assert rec1.resumes == [None, None]

        # Round 2 with delta_round2 on: still fresh, scoped delta prompt.
        state.review_iteration = 1
        state.review_story_id = "3.1"
        cache = tmp_path / ".bmad-assist-lite" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "review-findings-3.1.md").write_text("R1 findings", encoding="utf-8")
        rec2 = _Recorder()
        _run_review_fanout(handler, state, rec2)
        assert rec2.resumes == [None, None]
        assert any("Round-2 re-review" in p for p in rec2.prompts)


class TestLeanReviewLaneEffort:
    def _cfg(self, *, lean: bool) -> Any:
        return load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "opus"},
                    "multi": [
                        {"provider": "claude", "model": "sonnet", "effort": "high"},
                        {"provider": "claude", "model": "haiku", "effort": "medium"},
                    ],
                },
                "speed": {"lean_review": lean},
            }
        )

    def test_lean_on_notches_each_lane(self, tmp_path: Any) -> None:
        handler = CodeReviewHandler(self._cfg(lean=True), tmp_path)
        rec = _Recorder()
        _run_review_fanout(handler, State(current_epic=3, current_story="3.1"), rec)
        assert rec.efforts == ["medium", "low"]

    def test_lean_off_passes_lane_effort_through(self, tmp_path: Any) -> None:
        handler = CodeReviewHandler(self._cfg(lean=False), tmp_path)
        rec = _Recorder()
        _run_review_fanout(handler, State(current_epic=3, current_story="3.1"), rec)
        assert rec.efforts == ["high", "medium"]


class TestRemoveStaggerExecuteLevel:
    def _cfg(self, *, remove: bool) -> Any:
        return load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "opus"},
                    "multi": [
                        {"provider": "claude", "model": "sonnet"},
                        {"provider": "claude", "model": "haiku"},
                    ],
                },
                "parallel_delay": 8.0,
                "speed": {"remove_stagger": remove},
            }
        )

    def _run(self, handler: CodeReviewHandler, rec: _Recorder, sleeper: AsyncMock) -> None:
        with patch(
            "bmad_assist_lite.loop.handlers.code_review.get_provider",
            side_effect=lambda name: rec.make_provider(),
        ), patch.object(CodeReviewHandler, "render_prompt", return_value="PROMPT"), patch.object(
            CodeReviewHandler, "build_system_prompt", return_value="SYS"
        ), patch("asyncio.sleep", sleeper):
            handler.execute(State(current_epic=3, current_story="3.1"))

    def test_stagger_opt_out_sleeps_between_lanes(self, tmp_path: Any) -> None:
        # remove_stagger defaults ON; explicit opt-out restores the stagger sleep.
        sleeper = AsyncMock()
        self._run(CodeReviewHandler(self._cfg(remove=False), tmp_path), _Recorder(), sleeper)
        sleeper.assert_awaited_once_with(8.0)

    def test_remove_stagger_never_sleeps(self, tmp_path: Any) -> None:
        sleeper = AsyncMock()
        self._run(CodeReviewHandler(self._cfg(remove=True), tmp_path), _Recorder(), sleeper)
        sleeper.assert_not_awaited()


class TestValidatorStructuredAddendum:
    def _cfg(self, *, structured: bool) -> Any:
        return load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "opus"},
                    "multi": [{"provider": "claude", "model": "sonnet"}],
                },
                "speed": {"structured_review": structured},
            }
        )

    def _run(self, handler: ValidateStoryHandler, rec: _Recorder) -> None:
        with patch(
            "bmad_assist_lite.loop.handlers.validate_story.get_provider",
            side_effect=lambda name: rec.make_provider(),
        ), patch.object(ValidateStoryHandler, "render_prompt", return_value="VPROMPT"), patch.object(
            ValidateStoryHandler, "build_system_prompt", return_value=None
        ):
            handler.execute(State(current_epic=3, current_story="3.1"))

    def test_structured_on_appends_findings_contract(self, tmp_path: Any) -> None:
        rec = _Recorder()
        self._run(ValidateStoryHandler(self._cfg(structured=True), tmp_path), rec)
        assert rec.prompts and "BMAD-FINDINGS" in rec.prompts[0]

    def test_structured_off_leaves_prompt_alone(self, tmp_path: Any) -> None:
        rec = _Recorder()
        self._run(ValidateStoryHandler(self._cfg(structured=False), tmp_path), rec)
        assert rec.prompts == ["VPROMPT"]
