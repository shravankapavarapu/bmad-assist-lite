"""The bounded review -> fix -> re-review loop (REQ-08.1-08.3).

The cap is a backstop; convergence is the real stop condition. A cap answers
"how long do we pay?"; the finding-set hash answers "are we still buying
anything?". Both are proven here by tests that count provider invocations
rather than by inspection.
"""

from __future__ import annotations

import pytest

from bmad_assist_lite.core.config import Config, LoopConfig, ReviewConfig
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.review_loop import (
    ReviewDecision,
    ReviewOutcome,
    decide_review_loop,
)
from bmad_assist_lite.validation.findings import (
    Bucket,
    Finding,
    FindingSet,
    Severity,
)


def _finding(title: str = "Unchecked index", **overrides: object) -> Finding:
    base: dict[str, object] = {
        "file": "src/a.py",
        "anchor": "items[0]",
        "line": 10,
        "severity": Severity.HIGH,
        "bucket": Bucket.PATCH,
        "title": title,
    }
    base.update(overrides)
    return Finding(**base)  # type: ignore[arg-type]


def _set(*findings: Finding) -> FindingSet:
    return FindingSet(findings=findings)


REVIEW = ReviewConfig()


# ---------------------------------------------------------------------------
# REQ-08.2 — the cap
# ---------------------------------------------------------------------------


class TestTheCap:
    def test_the_default_cap_is_two(self) -> None:
        """Operator decision (2026-09-05, review-owns-done): the smallest cap
        where the cheap delta fix-verification round still exists, since the
        final round is never a delta. Supersedes D-0006 = C (cap 1)."""
        assert LoopConfig().review_max_iterations == 2

    def test_the_cap_is_validated_non_negative(self) -> None:
        with pytest.raises(Exception):
            LoopConfig(review_max_iterations=-1)

    def test_neg_cap_zero_disables_the_loop_entirely(self) -> None:
        """REQ-08.2 crit 3 — the designed kill switch."""
        decision = decide_review_loop(
            _set(_finding()),
            iteration=0,
            max_iterations=0,
            previous_hashes=(),
            review=REVIEW,
            story_id="1.1",
        )
        assert decision.outcome is ReviewOutcome.DISABLED
        assert decision.proceeds is True

    def test_neg_a_fixer_that_never_fixes_terminates_at_exactly_the_cap(self) -> None:
        """REQ-08.2 crit 2 (load-bearing). A fixer that changes *something*
        every pass (so the hash never repeats) must still stop at the cap."""
        max_iterations = 2
        hashes: list[str] = []
        spent = 0

        for attempt in range(10):
            findings = _set(_finding(title=f"Issue variant {attempt}"))
            decision = decide_review_loop(
                findings,
                iteration=spent,
                max_iterations=max_iterations,
                previous_hashes=tuple(hashes),
                review=REVIEW,
                story_id="1.1",
            )
            hashes.append(decision.finding_hash)
            if decision.outcome is not ReviewOutcome.FIX:
                break
            spent += 1

        assert spent == max_iterations
        assert decision.outcome is ReviewOutcome.CAP_EXHAUSTED
        assert decision.blocked is True

    def test_exhaustion_is_mark_blocked_and_move_on_not_abort(self) -> None:
        """REQ-08.2 crit 5, mirroring failed_qa_stories."""
        decision = decide_review_loop(
            _set(_finding()),
            iteration=1,
            max_iterations=1,
            previous_hashes=("something-else",),
            review=REVIEW,
            story_id="1.1",
        )
        assert decision.outcome is ReviewOutcome.CAP_EXHAUSTED
        assert decision.proceeds is True, "the run continues to the next story"
        assert decision.blocked is True


# ---------------------------------------------------------------------------
# REQ-08.3 — convergence by finding-set hash, not by counting
# ---------------------------------------------------------------------------


class TestConvergence:
    def test_neg_an_identical_second_pass_bails_immediately(self) -> None:
        """REQ-08.3 crit 2 (load-bearing) — exits at pass 2, NOT at the cap.

        Counted in provider invocations: with a cap of 3 a counting loop would
        spend three fix rounds; the hash spends one.
        """
        max_iterations = 3
        findings = _set(_finding())
        hashes: list[str] = []
        fix_rounds = 0

        for _ in range(10):
            decision = decide_review_loop(
                findings,
                iteration=fix_rounds,
                max_iterations=max_iterations,
                previous_hashes=tuple(hashes),
                review=REVIEW,
                story_id="1.1",
            )
            hashes.append(decision.finding_hash)
            if decision.outcome is not ReviewOutcome.FIX:
                break
            fix_rounds += 1

        assert fix_rounds == 1, "one fix round, then the repeat is detected"
        assert decision.outcome is ReviewOutcome.NON_CONVERGENT
        assert decision.blocked is True

    def test_the_hash_check_precedes_the_cap_check(self) -> None:
        """A stuck fixer is a bug report; a slow one is not. They must not be
        conflated even when both conditions hold at once."""
        findings = _set(_finding())
        first = decide_review_loop(
            findings, iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        repeat = decide_review_loop(
            findings,
            iteration=1,
            max_iterations=1,
            previous_hashes=(first.finding_hash,),
            review=REVIEW,
            story_id="1.1",
        )
        assert repeat.outcome is ReviewOutcome.NON_CONVERGENT

    def test_neg_genuine_progress_does_not_bail(self) -> None:
        """REQ-08.3 crit 3 — one of two findings fixed, so the loop continues."""
        pass_one = _set(_finding("Alpha"), _finding("Beta", file="src/b.py"))
        first = decide_review_loop(
            pass_one, iteration=0, max_iterations=2, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        assert first.outcome is ReviewOutcome.FIX

        pass_two = _set(_finding("Beta", file="src/b.py"))
        second = decide_review_loop(
            pass_two,
            iteration=1,
            max_iterations=2,
            previous_hashes=(first.finding_hash,),
            review=REVIEW,
            story_id="1.1",
        )
        assert second.outcome is ReviewOutcome.FIX, "the hash changed — keep going"

    def test_bail_reason_distinguishes_cap_exhausted_from_non_convergent(self) -> None:
        """REQ-08.3 crit 5 / ADR-0005 section 4."""
        assert ReviewOutcome.CAP_EXHAUSTED.value == "cap-exhausted"
        assert ReviewOutcome.NON_CONVERGENT.value == "non-convergent"


# ---------------------------------------------------------------------------
# REQ-08.1 / H1 — the loop, and whether the single iteration is spent at all
# ---------------------------------------------------------------------------


class TestTheLoop:
    def test_neg_a_clean_review_performs_zero_extra_iterations(self) -> None:
        """REQ-08.1 crit 3 — the added cost is zero on the happy path."""
        decision = decide_review_loop(
            _set(), iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        assert decision.outcome is ReviewOutcome.CLEAN
        assert decision.proceeds is True
        assert decision.blocked is False

    def test_neg_followup_score_declines_to_spend_on_a_low_severity_set(self) -> None:
        """H1 with the operator's cap of 1: the score decides whether that one
        iteration is spent at all, which is what makes ON-at-1 nearly free.

        Note this is a *second*, independent cull. A set of pure `low`
        findings never reaches the score at all — the severity threshold
        already returned CLEAN. The score's own job is the case below: a
        finding that IS blocking, but not enough of them to be worth an
        extra round (3 x 1 medium = 3, under the threshold of 5).
        """
        one_medium = _set(_finding("Naming could be clearer", severity=Severity.MEDIUM))
        decision = decide_review_loop(
            one_medium, iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        assert decision.outcome is ReviewOutcome.NOT_WORTH_IT
        assert decision.proceeds is True
        assert decision.blocked is False
        assert decision.blocking_count == 1, "it blocked, and was still not worth it"

    def test_a_pure_low_severity_set_is_culled_before_the_score(self) -> None:
        """The threshold and the follow-up score are separate mechanisms."""
        lows = _set(*(_finding(f"Nit {i}", severity=Severity.LOW) for i in range(4)))
        decision = decide_review_loop(
            lows, iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        assert decision.outcome is ReviewOutcome.CLEAN
        assert decision.blocking_count == 0

    def test_a_high_severity_set_does_spend_the_iteration(self) -> None:
        decision = decide_review_loop(
            _set(_finding(severity=Severity.HIGH)),
            iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        assert decision.outcome is ReviewOutcome.FIX
        assert decision.next_phase is Phase.FIX_REVIEW

    def test_neg_a_parse_failure_flags_the_story_and_never_reads_as_clean(self) -> None:
        """REQ-08.4 crit 2 at the consumer."""
        decision = decide_review_loop(
            None, iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        assert decision.outcome is ReviewOutcome.PARSE_FAILED
        assert decision.blocked is True
        assert decision.outcome is not ReviewOutcome.CLEAN

    def test_neg_the_loop_cannot_run_without_a_cap(self) -> None:
        """REQ-08.1 crit 4 — no defaulting to unbounded."""
        with pytest.raises(ValueError, match="cap"):
            decide_review_loop(
                _set(_finding()),
                iteration=0,
                max_iterations=None,  # type: ignore[arg-type]
                previous_hashes=(),
                review=REVIEW,
                story_id="1.1",
            )

    def test_neg_fix_review_is_a_detour_not_a_listed_phase(self) -> None:
        """REQ-08.1 crit 1 — structurally identical to fix_quality_gate."""
        assert "fix_review" not in LoopConfig().story
        assert "fix_review" not in LoopConfig().epic_teardown
        assert Phase.FIX_REVIEW.value == "fix_review"

    def test_the_fix_phase_is_reachable_only_via_next_phase_override(self) -> None:
        decision = decide_review_loop(
            _set(_finding()), iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        assert decision.next_phase is Phase.FIX_REVIEW
        clean = decide_review_loop(
            _set(), iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        assert clean.next_phase is None


# ---------------------------------------------------------------------------
# REQ-08.5 crit 6-7 — exhaustion must not look like a crash
# ---------------------------------------------------------------------------


class TestExhaustionIsExplained:
    @pytest.mark.parametrize(
        "outcome,iteration,previous",
        [
            (ReviewOutcome.CAP_EXHAUSTED, 1, ("other-hash",)),
            (ReviewOutcome.NON_CONVERGENT, 1, None),
        ],
    )
    def test_a_blocked_story_prints_an_actionable_line(
        self, outcome: ReviewOutcome, iteration: int, previous: tuple[str, ...] | None
    ) -> None:
        findings = _set(_finding())
        hashes = previous
        if hashes is None:
            first = decide_review_loop(
                findings, iteration=0, max_iterations=2, previous_hashes=(),
                review=REVIEW, story_id="7.3",
            )
            hashes = (first.finding_hash,)

        decision = decide_review_loop(
            findings,
            iteration=iteration,
            max_iterations=1 if outcome is ReviewOutcome.CAP_EXHAUSTED else 2,
            previous_hashes=hashes,
            review=REVIEW,
            story_id="7.3",
        )

        assert decision.outcome is outcome
        line = decision.console_line
        assert "7.3" in line, "the line names the story"
        assert outcome.value in line, "cap-exhausted and non-convergent are distinguished"
        assert "loop.review_max_iterations" in line, "name the config key to raise"
        assert "continu" in line.lower(), "state that the run continued"
        assert "not a failure" in line.lower() or "not a crash" in line.lower()

    def test_neg_a_blocked_story_is_never_silent(self) -> None:
        """REQ-08.5 crit 7 — 'mark blocked and move on' must not read as
        'moved on for no stated reason'."""
        findings = _set(_finding())
        for iteration, previous in ((1, ("x",)),):
            decision = decide_review_loop(
                findings, iteration=iteration, max_iterations=1,
                previous_hashes=previous, review=REVIEW, story_id="7.3",
            )
            assert decision.blocked is True
            assert decision.console_line.strip(), "a blocked story always says why"

    def test_a_proceeding_decision_needs_no_console_line(self) -> None:
        decision = decide_review_loop(
            _set(), iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="7.3",
        )
        assert decision.blocked is False
        assert decision.console_line == ""


# ---------------------------------------------------------------------------
# State plumbing
# ---------------------------------------------------------------------------


class TestStateFields:
    def test_state_tracks_review_iterations_like_qa_retry_count(self) -> None:
        """REQ-08.1 blast radius: mirror qa_retry_count, do not invent."""
        state = State()
        assert state.review_iteration == 0
        assert state.review_finding_hashes == []
        assert state.review_blocked_stories == []

    def test_review_state_round_trips_through_yaml(self, tmp_path: object) -> None:
        from bmad_assist_lite.core.state import load_state, save_state

        path = tmp_path / "state.yaml"  # type: ignore[operator]
        save_state(
            State(
                current_story="1.1",
                review_iteration=1,
                review_finding_hashes=["abc"],
                review_blocked_stories=["1.0"],
            ),
            path,
        )
        loaded = load_state(path)
        assert loaded.review_iteration == 1
        assert loaded.review_finding_hashes == ["abc"]
        assert loaded.review_blocked_stories == ["1.0"]


class TestConfigSurface:
    def test_review_config_defaults_are_the_harvested_ones(self) -> None:
        review = ReviewConfig()
        assert review.blocking_severity is Severity.MEDIUM
        assert review.followup_medium_weight == 3
        assert review.followup_low_weight == 1
        assert review.followup_threshold == 5

    def test_review_block_is_additive_and_optional(self) -> None:
        config = Config.model_validate(
            {"providers": {"master": {"provider": "claude", "model": "opus"}}}
        )
        assert config.review == ReviewConfig()
        assert config.loop.review_max_iterations == 2

    def test_decision_model_is_frozen(self) -> None:
        decision = decide_review_loop(
            _set(), iteration=0, max_iterations=1, previous_hashes=(),
            review=REVIEW, story_id="1.1",
        )
        assert isinstance(decision, ReviewDecision)
        with pytest.raises(Exception):
            decision.outcome = ReviewOutcome.FIX  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Execution-driven: the loop through the real synthesis handler (G12 partner)
# ---------------------------------------------------------------------------


class TestLoopThroughTheHandler:
    """The static checks above describe the loop; these drive it.

    Provider invocations are counted rather than inspected, because the claims
    being made ("one fix cycle", "bails at pass 2, not at the cap") are claims
    about how much is *paid*.
    """

    @staticmethod
    def _handler(tmp_path, max_iterations: int = 1):
        from bmad_assist_lite.core.config import Config
        from bmad_assist_lite.loop.handlers.code_review_synthesis import (
            CodeReviewSynthesisHandler,
        )

        config = Config.model_validate(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "loop": {"review_max_iterations": max_iterations},
            }
        )
        return CodeReviewSynthesisHandler(config, tmp_path)

    @staticmethod
    def _seed_reviews(tmp_path) -> None:
        import json

        cache = tmp_path / ".bmad-assist-lite" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "reviews.json").write_text(
            json.dumps({"reviews": [{"reviewer": "r1", "response": "looked at it"}]}),
            encoding="utf-8",
        )

    @staticmethod
    def _response(findings_json: str) -> str:
        return (
            "Synthesis report written.\n\n<!-- BMAD-FINDINGS -->\n```json\n"
            + findings_json
            + "\n```\n<!-- /BMAD-FINDINGS -->\n"
        )

    def _drive(self, monkeypatch, tmp_path, responses: list[str], max_iterations: int = 1):
        """Run synthesis once per response, threading state as the runner does."""
        from bmad_assist_lite.providers.base import ProviderResult

        handler = self._handler(tmp_path, max_iterations)
        self._seed_reviews(tmp_path)

        calls: list[str] = []
        queue = list(responses)

        def _fake_invoke(prompt: str, **kwargs: object) -> ProviderResult:
            calls.append(prompt)
            return ProviderResult(
                stdout=queue.pop(0), stderr="", exit_code=0,
                duration_ms=1, model="opus", command=(),
            )

        monkeypatch.setattr(handler, "invoke_provider", _fake_invoke)
        monkeypatch.setattr(handler, "render_prompt", lambda state: "PROMPT")

        state = State(current_story="1.1", current_epic=1)
        results = []
        while queue:
            result = handler.execute(state)
            results.append(result)
            # Mirror the runner: entering the fixer is what spends an iteration.
            if result.next_phase is Phase.FIX_REVIEW:
                state.review_iteration += 1
            else:
                break
        return state, results, calls

    def test_two_blocking_findings_run_one_fix_cycle_then_clear(
        self, monkeypatch, tmp_path
    ) -> None:
        """REQ-08.1 crit 2 — one fix cycle, re-review runs, loop exits on clear."""
        two_blocking = self._response(
            '[{"file":"a.py","anchor":"f","severity":"high","bucket":"patch","title":"A"},'
            '{"file":"b.py","anchor":"g","severity":"high","bucket":"patch","title":"B"}]'
        )
        state, results, calls = self._drive(
            monkeypatch, tmp_path, [two_blocking, self._response("[]")]
        )

        assert results[0].next_phase is Phase.FIX_REVIEW, "one fix cycle is entered"
        assert results[1].next_phase is None, "the loop exits when findings clear"
        assert state.review_iteration == 1
        assert len(calls) == 2
        assert state.review_blocked_stories == []

    def test_neg_a_clean_review_costs_zero_extra_invocations(
        self, monkeypatch, tmp_path
    ) -> None:
        """REQ-08.1 crit 3 — the happy path adds nothing."""
        _, results, calls = self._drive(monkeypatch, tmp_path, [self._response("[]")])
        assert results[0].next_phase is None
        assert len(calls) == 1

    def test_neg_an_unfixed_finding_bails_at_pass_two_not_at_the_cap(
        self, monkeypatch, tmp_path
    ) -> None:
        """REQ-08.3 crit 2 (load-bearing) — counted in provider invocations.

        The cap is 3. A loop that only counted would synthesise 4 times; the
        hash stops it at 2.
        """
        unchanged = self._response(
            '[{"file":"a.py","anchor":"f","severity":"high","bucket":"patch","title":"A"}]'
        )
        state, results, calls = self._drive(
            monkeypatch, tmp_path, [unchanged] * 4, max_iterations=3
        )

        assert len(calls) == 2, "two synthesis calls, not four"
        assert state.review_iteration == 1, "one fix round, not three"
        assert results[-1].outputs["review_outcome"] == "non-convergent"
        assert state.review_blocked_stories == ["1.1"]

    def test_neg_an_unparseable_response_is_not_recorded_as_clean(
        self, monkeypatch, tmp_path
    ) -> None:
        """REQ-08.0 crit 3 / REQ-08.4 crit 2, at the real consumer."""
        state, results, _ = self._drive(
            monkeypatch, tmp_path, ["I reviewed it and it all looks fine to me."]
        )
        assert results[0].outputs["review_outcome"] == "parse-failed"
        assert results[0].next_phase is None
        assert state.review_blocked_stories == ["1.1"]

    def test_findings_artifact_records_below_threshold_findings(
        self, monkeypatch, tmp_path
    ) -> None:
        """REQ-08.4 crit 4 — culled from the loop, kept in the record."""
        low_only = self._response(
            '[{"file":"a.py","anchor":"f","severity":"low","bucket":"patch","title":"Nit"}]'
        )
        _, results, calls = self._drive(monkeypatch, tmp_path, [low_only])

        assert results[0].next_phase is None, "a low finding drives no iteration"
        assert len(calls) == 1
        artifact = (
            tmp_path / ".bmad-assist-lite" / "cache" / "review-findings-1.1.md"
        ).read_text(encoding="utf-8")
        assert "Nit" in artifact, "but it IS recorded"
        assert "clean" in artifact

    def test_the_hash_budget_resets_between_stories(self, monkeypatch, tmp_path) -> None:
        """Hashes are per-story; a collision across stories would misread."""
        from bmad_assist_lite.providers.base import ProviderResult

        handler = self._handler(tmp_path)
        self._seed_reviews(tmp_path)
        same = self._response(
            '[{"file":"a.py","anchor":"f","severity":"high","bucket":"patch","title":"A"}]'
        )
        monkeypatch.setattr(
            handler, "invoke_provider",
            lambda prompt, **kw: ProviderResult(
                stdout=same, stderr="", exit_code=0, duration_ms=1,
                model="opus", command=(),
            ),
        )
        monkeypatch.setattr(handler, "render_prompt", lambda state: "PROMPT")

        state = State(current_story="1.1", current_epic=1)
        assert handler.execute(state).next_phase is Phase.FIX_REVIEW

        state.current_story = "1.2"
        assert handler.execute(state).next_phase is Phase.FIX_REVIEW, (
            "the identical finding on a DIFFERENT story is not non-convergence"
        )
        assert state.review_iteration == 0
        assert state.review_finding_hashes == [handler._parse_review_findings(same).hash]
