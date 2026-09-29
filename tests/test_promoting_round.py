"""The promoting round is the round the review loop EXITS from.

Two park classes came out of a real epic run, and both had the same root: the
machinery equated "the promoting round" with "the round at the iteration cap".

1. Every story that took a fix round parked. The middle round is a delta
   re-review; when it came back clean the loop exited on it, the verdict
   record said ``full_pass=False``, and the gate refuses to promote on a
   delta. Fix: a clean delta loops back to code_review for one FULL round,
   which becomes the promoting round.
2. A story clean on round 1 could park too. The auto audit trigger fires
   unconditionally only at the cap, and a clean round 1 exits at iteration 0;
   with quiet risk signals the only round carried no audit. Fix: a clean full
   round with an approving verdict and a required-but-missing audit runs the
   audit before its record is written.

Providers are mocked throughout; nothing here reaches a model.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.core.verdict import load_review_verdict, verdict_blocks_done
from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler
from bmad_assist_lite.loop.handlers.code_review_synthesis import (
    CodeReviewSynthesisHandler,
)
from bmad_assist_lite.loop.review_loop import ReviewOutcome
from bmad_assist_lite.providers.base import READ_ONLY_TOOLS, ProviderResult
from bmad_assist_lite.validation.findings import (
    Bucket,
    Finding,
    FindingSet,
    Severity,
    render_findings_block,
)

STORY = "3.1"

PROVIDERS: dict[str, Any] = {
    "master": {"provider": "claude", "model": "opus", "effort": "high"},
    "multi": [{"provider": "claude", "model": "fable"}],
}

AUDIT_TABLE_PASS = (
    "| AC | Verdict | Evidence |\n"
    "|----|---------|----------|\n"
    "| AC1 | COMPLETE | src/a.py:10 |\n"
    "| AC2 | COMPLETE | src/b.py:4 |\n"
)
AUDIT_TABLE_FAIL = AUDIT_TABLE_PASS + "| AC3 | PARTIAL | consumer never reads it |\n"


def _config(
    *,
    structured: bool = False,
    delta: bool = True,
    ac_audit: dict[str, bool] | None = None,
    cap: int = 2,
) -> Any:
    return load_config(
        {
            "providers": PROVIDERS,
            "loop": {"review_max_iterations": cap},
            "speed": {
                "structured_review": structured,
                "delta_round2": delta,
                "lean_review": False,
                "remove_stagger": False,
            },
            "ac_audit": ac_audit or {"enabled": False, "auto": False},
        }
    )


def _seed_cache(
    project: Path,
    meta: dict[str, Any] | None,
    *,
    verdict: str | None = "PASS",
    reviews: list[dict[str, Any]] | None = None,
) -> Path:
    cache = project / ".bmad-assist-lite" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "reviews": reviews if reviews is not None else [],
        "evidence_score": (
            {"total_score": -1.0, "verdict": verdict} if verdict is not None else None
        ),
    }
    if meta is not None:
        payload["round_meta"] = meta
    path = cache / "reviews.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _meta(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "story_id": STORY,
        "review_iteration": 1,
        "full_pass": True,
        "audit_required": False,
        "audit_ran": False,
        "audit_passed": None,
    }
    base.update(overrides)
    return base


def _state(iteration: int = 1) -> State:
    state = State(current_epic=3, current_story=STORY)
    state.review_story_id = STORY
    state.review_iteration = iteration
    return state


def _clean() -> FindingSet:
    return FindingSet(findings=())


def _result(stdout: str, exit_code: int = 0) -> ProviderResult:
    return ProviderResult(
        stdout=stdout, stderr="" if exit_code == 0 else "boom", exit_code=exit_code,
        duration_ms=1, model="opus", command=("claude",),
    )


def _audit_provider(stdout: str = AUDIT_TABLE_PASS, exit_code: int = 0) -> MagicMock:
    provider = MagicMock()
    provider.provider_name = "claude"
    provider.default_model = "opus"
    provider.invoke.return_value = _result(stdout, exit_code)
    provider.parse_output.side_effect = lambda raw: raw.stdout
    return provider


@contextmanager
def _patch_audit(provider: MagicMock) -> Iterator[None]:
    """Route the synthesis's on-exit audit to ``provider``, with no compiler work."""
    with patch(
        "bmad_assist_lite.providers.get_provider", return_value=provider
    ), patch.object(
        CodeReviewSynthesisHandler,
        "render_prompt",
        side_effect=lambda state, workflow_name=None: f"WF:{workflow_name}",
    ), patch(
        "bmad_assist_lite.loop.handlers.code_review_synthesis.git_diff",
        return_value="diff --git a/x b/x",
    ):
        yield


def _cached_meta(project: Path) -> dict[str, Any]:
    cache = project / ".bmad-assist-lite" / "cache" / "reviews.json"
    return json.loads(cache.read_text(encoding="utf-8"))["round_meta"]


# ============================================================================
# The flag on the code_review side
# ============================================================================


def _seed_findings(project: Path) -> None:
    cache = project / ".bmad-assist-lite" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"review-findings-{STORY}.md").write_text("R1 finding", encoding="utf-8")


class TestForceFullReviewFlag:
    def test_flag_defaults_off_and_old_state_files_load(self) -> None:
        assert State().force_full_review is False
        assert State.model_validate({"current_story": "1.1"}).force_full_review is False

    def test_flag_round_trips_through_yaml(self, tmp_path: Path) -> None:
        from bmad_assist_lite.core.state import load_state, save_state

        state = _state()
        state.force_full_review = True
        path = tmp_path / "state.yaml"
        save_state(state, path)
        assert load_state(path).force_full_review is True

    def test_middle_round_is_delta_without_the_flag(self, tmp_path: Path) -> None:
        handler = CodeReviewHandler(_config(), tmp_path)
        assert handler._is_delta_round(_state(iteration=1)) is True

    def test_flag_makes_the_middle_round_full(self, tmp_path: Path) -> None:
        handler = CodeReviewHandler(_config(), tmp_path)
        state = _state(iteration=1)
        state.force_full_review = True
        assert handler._is_delta_round(state) is False

    def test_forced_round_runs_the_full_prompt_and_records_full_pass(
        self, tmp_path: Path
    ) -> None:
        """Ping-pong guard: the forced round's round_meta says full_pass=True,
        so the synthesis's loop-back condition cannot fire again from it."""
        _seed_findings(tmp_path)
        handler = CodeReviewHandler(_config(), tmp_path)
        state = _state(iteration=1)
        state.force_full_review = True
        reviews = [{"reviewer": "Reviewer-1", "response": "Evidence Score: -2", "exit_code": 0}]
        captured: dict[str, Any] = {}

        def _runner(coro: Any) -> Any:
            coro.close()
            return reviews

        real_build = handler._build_lanes

        def _spy_build(s: State) -> list[dict[str, Any]]:
            lanes = real_build(s)
            captured["flag_during_build"] = s.force_full_review
            captured["prompt"] = lanes[0]["prompt"]
            return lanes

        with patch.object(
            CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"
        ), patch.object(handler, "_build_lanes", side_effect=_spy_build), patch(
            "bmad_assist_lite.loop.handlers.code_review.run_async_in_thread",
            side_effect=_runner,
        ):
            result = handler.execute(state)

        assert result.success
        assert captured["flag_during_build"] is True, "flag must hold while lanes are built"
        assert captured["prompt"] == "FULL PROMPT"
        assert state.force_full_review is False, "the forced round consumes the flag"
        assert _cached_meta(tmp_path)["full_pass"] is True

    def test_next_round_after_the_forced_one_is_delta_again(self, tmp_path: Path) -> None:
        """The flag is consumed once: it does not turn every later round full."""
        _seed_findings(tmp_path)
        handler = CodeReviewHandler(_config(cap=3), tmp_path)
        state = _state(iteration=1)
        state.force_full_review = True
        reviews = [{"reviewer": "Reviewer-1", "response": "x", "exit_code": 0}]

        def _runner(coro: Any) -> Any:
            coro.close()
            return reviews

        with patch.object(
            CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"
        ), patch(
            "bmad_assist_lite.loop.handlers.code_review.run_async_in_thread",
            side_effect=_runner,
        ):
            handler.execute(state)
        state.review_iteration = 2  # a fix round later, still below cap 3
        assert handler._is_delta_round(state) is True


# ============================================================================
# Fix A: a clean delta loops back for one full promoting review
# ============================================================================


class TestCleanDeltaLoopsBack:
    def test_decide_and_record_writes_nothing_and_sets_the_flag(
        self, tmp_path: Path
    ) -> None:
        _seed_cache(tmp_path, _meta(full_pass=False))
        handler = CodeReviewSynthesisHandler(_config(), tmp_path)
        state = _state()

        decision = handler._decide_and_record(state, _clean())

        assert decision.outcome is ReviewOutcome.CLEAN
        assert load_review_verdict(tmp_path, STORY) is None
        assert state.force_full_review is True
        assert handler._next_phase(decision) is Phase.CODE_REVIEW
        assert state.review_iteration == 1, "a re-review is not a fix round"

    def test_the_loop_back_is_announced(self, tmp_path: Path) -> None:
        _seed_cache(tmp_path, _meta(full_pass=False))
        handler = CodeReviewSynthesisHandler(_config(), tmp_path)
        with patch(
            "bmad_assist_lite.loop.handlers.code_review_synthesis.write_progress"
        ) as progress:
            handler._decide_and_record(_state(), _clean())
        lines = " ".join(str(c.args[0]) for c in progress.call_args_list)
        assert "clean delta re-review cannot promote" in lines
        assert "one full promoting review" in lines

    def test_legacy_path_routes_back_to_code_review(self, tmp_path: Path) -> None:
        _seed_cache(
            tmp_path,
            _meta(full_pass=False),
            reviews=[{"reviewer": "Reviewer-1", "response": "fine", "exit_code": 0}],
        )
        handler = CodeReviewSynthesisHandler(_config(structured=False), tmp_path)
        clean = "Report.\n\n" + render_findings_block([])
        state = _state()
        with patch.object(
            handler, "invoke_provider", return_value=_result(clean)
        ), patch.object(CodeReviewSynthesisHandler, "render_prompt", return_value="P"):
            result = handler.execute(state)

        assert result.success
        assert "structured_review" not in result.outputs
        assert result.next_phase is Phase.CODE_REVIEW
        assert state.force_full_review is True
        assert load_review_verdict(tmp_path, STORY) is None

    def test_structured_path_routes_back_to_code_review(self, tmp_path: Path) -> None:
        _seed_cache(
            tmp_path,
            _meta(full_pass=False),
            reviews=[
                {
                    "reviewer": "Reviewer-1",
                    "response": "ok\n\n" + render_findings_block([]),
                    "exit_code": 0,
                }
            ],
        )
        handler = CodeReviewSynthesisHandler(_config(structured=True), tmp_path)
        clean = "Terse.\n\n" + render_findings_block([])
        state = _state()
        with patch.object(
            handler, "invoke_provider", return_value=_result(clean)
        ), patch.object(CodeReviewSynthesisHandler, "render_prompt", return_value="P"):
            result = handler.execute(state)

        assert result.success
        assert result.outputs.get("structured_review") is True
        assert result.next_phase is Phase.CODE_REVIEW
        assert state.force_full_review is True
        assert load_review_verdict(tmp_path, STORY) is None

    def test_forced_full_round_clean_records_and_does_not_loop(
        self, tmp_path: Path
    ) -> None:
        """Round two of the pair: the forced round is full, so it promotes."""
        handler = CodeReviewSynthesisHandler(_config(), tmp_path)
        state = _state()

        _seed_cache(tmp_path, _meta(full_pass=False))
        first = handler._decide_and_record(state, _clean())
        assert handler._next_phase(first) is Phase.CODE_REVIEW

        # code_review consumed the flag and ran the full round.
        state.force_full_review = False
        _seed_cache(tmp_path, _meta(full_pass=True))
        second = handler._decide_and_record(state, _clean())

        assert second.outcome is ReviewOutcome.CLEAN
        assert handler._next_phase(second) is None, "no second loop-back"
        assert state.force_full_review is False
        record = load_review_verdict(tmp_path, STORY)
        assert record is not None
        assert record.full_pass is True
        assert record.verdict == "PASS"
        assert _record_dissent_reason(tmp_path) is None

    def test_two_consecutive_clean_rounds_are_not_non_convergent(
        self, tmp_path: Path
    ) -> None:
        """Same (empty) finding-set hash twice in a row must not read as a
        stuck fixer: CLEAN is decided before the hash check."""
        handler = CodeReviewSynthesisHandler(_config(), tmp_path)
        state = _state()
        _seed_cache(tmp_path, _meta(full_pass=False))
        first = handler._decide_and_record(state, _clean())
        _seed_cache(tmp_path, _meta(full_pass=True))
        second = handler._decide_and_record(state, _clean())

        assert first.finding_hash == second.finding_hash
        assert second.finding_hash in state.review_finding_hashes
        assert first.outcome is ReviewOutcome.CLEAN
        assert second.outcome is ReviewOutcome.CLEAN
        assert state.review_blocked_stories == []

    def test_missing_meta_does_not_loop_back(self, tmp_path: Path) -> None:
        """No evidence of a delta is not evidence of one: without a meta for
        this story the old conservative record is written instead, so the
        loop can never spin on a cache that will stay missing."""
        _seed_cache(tmp_path, None)
        handler = CodeReviewSynthesisHandler(_config(), tmp_path)
        state = _state()
        decision = handler._decide_and_record(state, _clean())
        assert handler._next_phase(decision) is None
        assert state.force_full_review is False
        record = load_review_verdict(tmp_path, STORY)
        assert record is not None and record.full_pass is False

    def test_a_delta_that_needs_a_fix_still_goes_to_the_fixer(
        self, tmp_path: Path
    ) -> None:
        _seed_cache(tmp_path, _meta(full_pass=False))
        handler = CodeReviewSynthesisHandler(_config(cap=3), tmp_path)
        state = _state()
        blocking = FindingSet(
            findings=(
                Finding(file="a.py", anchor="f", severity=Severity.HIGH,
                        bucket=Bucket.PATCH, title="still broken"),
            )
        )
        decision = handler._decide_and_record(state, blocking)
        assert handler._next_phase(decision) is Phase.FIX_REVIEW
        assert state.force_full_review is False

    def test_loop_back_does_not_leak_into_the_next_decision(
        self, tmp_path: Path
    ) -> None:
        handler = CodeReviewSynthesisHandler(_config(), tmp_path)
        _seed_cache(tmp_path, _meta(full_pass=False))
        handler._decide_and_record(_state(), _clean())
        assert handler._loop_back_to_review is True

        other = State(current_epic=3, current_story="3.2")
        _seed_cache(tmp_path, _meta(story_id="3.2", full_pass=True))
        decision = handler._decide_and_record(other, _clean())
        assert handler._next_phase(decision) is None

    def test_a_clean_delta_skips_the_on_exit_audit(self, tmp_path: Path) -> None:
        """Fix A wins: the forced full round carries the audit by escalation."""
        _seed_cache(tmp_path, _meta(full_pass=False, audit_required=True))
        handler = CodeReviewSynthesisHandler(
            _config(ac_audit={"enabled": False, "auto": True}), tmp_path
        )
        provider = _audit_provider()
        with _patch_audit(provider):
            handler._decide_and_record(_state(), _clean())
        provider.invoke.assert_not_called()


def _record_dissent_reason(project: Path) -> str | None:
    """The gate's verdict-record check alone (no story file needed)."""
    from bmad_assist_lite.core.verdict import _record_dissent

    record = load_review_verdict(project, STORY)
    assert record is not None
    return _record_dissent(record, STORY)


# ============================================================================
# Fix D: audit on exit for clean full rounds
# ============================================================================


def _audit_handler(tmp_path: Path) -> CodeReviewSynthesisHandler:
    return CodeReviewSynthesisHandler(
        _config(structured=True, ac_audit={"enabled": False, "auto": True}), tmp_path
    )


class TestAuditOnExit:
    def test_runs_on_a_clean_full_round_without_an_audit(self, tmp_path: Path) -> None:
        _seed_cache(
            tmp_path, _meta(review_iteration=0, audit_required=True), verdict="PASS"
        )
        handler = _audit_handler(tmp_path)
        provider = _audit_provider(AUDIT_TABLE_PASS)
        state = _state(iteration=0)

        with _patch_audit(provider), patch.object(
            handler, "build_system_prompt", return_value="SYS"
        ):
            decision = handler._decide_and_record(state, _clean())

        assert decision.proceeds
        provider.invoke.assert_called_once()
        args, kwargs = provider.invoke.call_args
        assert args[0].startswith("WF:ac-audit")
        assert "<changed-code-diff>" in args[0]
        assert "BMAD-FINDINGS" in args[0], "structured addendum rides along"
        assert kwargs["model"] == "opus"
        assert kwargs["effort"] == "high"
        assert kwargs["allowed_tools"] == list(READ_ONLY_TOOLS)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["system_prompt"] == "SYS"

        meta = _cached_meta(tmp_path)
        assert meta["audit_ran"] is True
        assert meta["audit_passed"] is True
        record = load_review_verdict(tmp_path, STORY)
        assert record is not None
        assert record.audit_ran is True
        assert record.audit_passed is True
        assert _record_dissent_reason(tmp_path) is None
        forensic = tmp_path / ".bmad-assist-lite" / "cache" / f"audit-on-exit-{STORY}.md"
        assert forensic.read_text(encoding="utf-8") == AUDIT_TABLE_PASS

    def test_leaves_the_cached_reviews_list_alone(self, tmp_path: Path) -> None:
        reviews = [{"reviewer": "Reviewer-1", "response": "r", "exit_code": 0}]
        cache = _seed_cache(
            tmp_path, _meta(review_iteration=0, audit_required=True), reviews=reviews
        )
        handler = _audit_handler(tmp_path)
        with _patch_audit(_audit_provider()):
            handler._decide_and_record(_state(iteration=0), _clean())
        assert json.loads(cache.read_text())["reviews"] == reviews

    def test_a_failing_audit_is_recorded_and_parks(self, tmp_path: Path) -> None:
        _seed_cache(tmp_path, _meta(review_iteration=0, audit_required=True))
        handler = _audit_handler(tmp_path)
        with _patch_audit(_audit_provider(AUDIT_TABLE_FAIL)):
            handler._decide_and_record(_state(iteration=0), _clean())
        record = load_review_verdict(tmp_path, STORY)
        assert record is not None
        assert record.audit_ran is True and record.audit_passed is False
        assert "failing" in (_record_dissent_reason(tmp_path) or "")

    @pytest.mark.parametrize("verdict", ["MAJOR_REWORK", "REJECT", None])
    def test_not_run_when_the_verdict_would_park_anyway(
        self, tmp_path: Path, verdict: str | None
    ) -> None:
        """At the cap, so a rework verdict exits (cap-exhausted) rather than
        spending a fix round — the exit still records, and still parks."""
        _seed_cache(
            tmp_path, _meta(review_iteration=2, audit_required=True), verdict=verdict
        )
        handler = _audit_handler(tmp_path)
        provider = _audit_provider()
        with _patch_audit(provider):
            decision = handler._decide_and_record(_state(iteration=2), _clean())
        assert decision.proceeds
        provider.invoke.assert_not_called()
        record = load_review_verdict(tmp_path, STORY)
        assert record is not None and record.audit_ran is False

    def test_verdict_match_is_case_insensitive(self, tmp_path: Path) -> None:
        _seed_cache(
            tmp_path, _meta(review_iteration=0, audit_required=True), verdict="approve"
        )
        handler = _audit_handler(tmp_path)
        provider = _audit_provider()
        with _patch_audit(provider):
            handler._decide_and_record(_state(iteration=0), _clean())
        provider.invoke.assert_called_once()

    def test_not_run_when_the_audit_already_ran(self, tmp_path: Path) -> None:
        _seed_cache(
            tmp_path,
            _meta(review_iteration=0, audit_required=True, audit_ran=True, audit_passed=True),
        )
        handler = _audit_handler(tmp_path)
        provider = _audit_provider()
        with _patch_audit(provider):
            handler._decide_and_record(_state(iteration=0), _clean())
        provider.invoke.assert_not_called()

    def test_not_run_when_the_audit_is_not_required(self, tmp_path: Path) -> None:
        _seed_cache(tmp_path, _meta(review_iteration=0, audit_required=False))
        handler = CodeReviewSynthesisHandler(_config(), tmp_path)
        provider = _audit_provider()
        with _patch_audit(provider):
            handler._decide_and_record(_state(iteration=0), _clean())
        provider.invoke.assert_not_called()

    def test_not_run_on_a_fix_round(self, tmp_path: Path) -> None:
        _seed_cache(tmp_path, _meta(review_iteration=0, audit_required=True))
        handler = _audit_handler(tmp_path)
        provider = _audit_provider()
        blocking = FindingSet(
            findings=(
                Finding(file="a.py", anchor="f", severity=Severity.HIGH,
                        bucket=Bucket.PATCH, title="A"),
                Finding(file="b.py", anchor="g", severity=Severity.HIGH,
                        bucket=Bucket.PATCH, title="B"),
            )
        )
        with _patch_audit(provider):
            decision = handler._decide_and_record(_state(iteration=0), blocking)
        assert not decision.proceeds
        provider.invoke.assert_not_called()

    def test_unparseable_output_records_dissent_and_still_writes(
        self, tmp_path: Path
    ) -> None:
        _seed_cache(tmp_path, _meta(review_iteration=0, audit_required=True))
        handler = _audit_handler(tmp_path)
        with _patch_audit(_audit_provider("I looked around and it seems fine.")):
            handler._decide_and_record(_state(iteration=0), _clean())
        record = load_review_verdict(tmp_path, STORY)
        assert record is not None
        assert record.audit_ran is True
        assert record.audit_passed is None
        assert "unparseable" in (_record_dissent_reason(tmp_path) or "")

    def test_provider_exception_warns_and_still_writes(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING)
        _seed_cache(tmp_path, _meta(review_iteration=0, audit_required=True))
        handler = _audit_handler(tmp_path)
        provider = _audit_provider()
        provider.invoke.side_effect = RuntimeError("provider exploded")
        with _patch_audit(provider):
            decision = handler._decide_and_record(_state(iteration=0), _clean())
        assert decision.proceeds
        assert "provider exploded" in caplog.text
        record = load_review_verdict(tmp_path, STORY)
        assert record is not None
        assert record.audit_ran is False
        assert _cached_meta(tmp_path)["audit_ran"] is False

    def test_nonzero_exit_leaves_the_meta_and_still_writes(self, tmp_path: Path) -> None:
        _seed_cache(tmp_path, _meta(review_iteration=0, audit_required=True))
        handler = _audit_handler(tmp_path)
        with _patch_audit(_audit_provider(AUDIT_TABLE_PASS, exit_code=1)):
            handler._decide_and_record(_state(iteration=0), _clean())
        record = load_review_verdict(tmp_path, STORY)
        assert record is not None and record.audit_ran is False

    def test_prompt_compile_failure_warns_and_still_writes(self, tmp_path: Path) -> None:
        """The ac-audit workflow requires the epic file; a ConfigError from
        compiling it must park the story, not crash the synthesis."""
        from bmad_assist_lite.core.exceptions import ConfigError

        _seed_cache(tmp_path, _meta(review_iteration=0, audit_required=True))
        handler = _audit_handler(tmp_path)
        provider = _audit_provider()
        with patch(
            "bmad_assist_lite.providers.get_provider", return_value=provider
        ), patch.object(
            CodeReviewSynthesisHandler,
            "render_prompt",
            side_effect=ConfigError("epic file missing"),
        ):
            handler._decide_and_record(_state(iteration=0), _clean())
        provider.invoke.assert_not_called()
        record = load_review_verdict(tmp_path, STORY)
        assert record is not None and record.audit_ran is False

    def test_same_prompt_as_the_code_review_lane(self, tmp_path: Path) -> None:
        """One gate, one question: both call sites send identical prompts."""
        config = _config(structured=True, ac_audit={"enabled": True, "auto": False})
        render = lambda state, workflow_name=None: f"WF:{workflow_name}"  # noqa: E731
        with patch.object(
            CodeReviewHandler, "render_prompt", side_effect=render
        ), patch(
            "bmad_assist_lite.loop.handlers.code_review.git_diff", return_value="DIFF"
        ):
            lane_prompt = CodeReviewHandler(config, tmp_path)._audit_prompt(_state())

        _seed_cache(tmp_path, _meta(review_iteration=0, audit_required=True))
        provider = _audit_provider()
        with patch(
            "bmad_assist_lite.providers.get_provider", return_value=provider
        ), patch.object(
            CodeReviewSynthesisHandler, "render_prompt", side_effect=render
        ), patch(
            "bmad_assist_lite.loop.handlers.code_review_synthesis.git_diff",
            return_value="DIFF",
        ):
            CodeReviewSynthesisHandler(config, tmp_path)._decide_and_record(
                _state(iteration=0), _clean()
            )
        assert provider.invoke.call_args.args[0] == lane_prompt


# ============================================================================
# End to end through the gate: story file + record
# ============================================================================


class TestGateAfterTheFixes:
    def test_clean_round_one_with_quiet_trigger_can_now_promote(
        self, tmp_path: Path
    ) -> None:
        stories = tmp_path / "_bmad-output" / "implementation-artifacts"
        stories.mkdir(parents=True)
        (stories / f"story-{STORY}.md").write_text(
            "# Story\n\nStatus: done\n", encoding="utf-8"
        )
        _seed_cache(tmp_path, _meta(review_iteration=0, audit_required=True))
        handler = _audit_handler(tmp_path)
        with _patch_audit(_audit_provider(AUDIT_TABLE_PASS)):
            handler._decide_and_record(_state(iteration=0), _clean())
        assert verdict_blocks_done(tmp_path, STORY) is None
