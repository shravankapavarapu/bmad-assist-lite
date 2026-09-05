"""Tests for SP-2 scoped delta round-2 review (goal-run6)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.git import UNTRACKED_HEADING, git_diff
from bmad_assist_lite.core.paths import _reset_paths, init_paths
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.code_review import (
    _MAX_INLINE_DIFF_CHARS,
    CodeReviewHandler,
)

BASE_PROVIDERS: dict[str, Any] = {
    "master": {"provider": "claude", "model": "opus"},
    "multi": [{"provider": "claude", "model": "fable"}],
}

STORY = "3.1"


# The run6 levers default ON since the 2026-08-16 flip. These tests were written
# against an all-off baseline where each test opts in exactly the lever(s) it
# names — the helper pins that baseline so the isolation semantics survive.
_SPEED_OFF: dict[str, Any] = {
    "structured_review": False,
    "delta_round2": False,
    "lean_review": False,
    "remove_stagger": False,
}


def _handler(project: Path, speed: dict[str, Any]) -> CodeReviewHandler:
    config = load_config({"providers": BASE_PROVIDERS, "speed": {**_SPEED_OFF, **speed}})
    return CodeReviewHandler(config, project)


def _seed_findings(project: Path, story: str, text: str) -> None:
    cache = project / ".bmad-assist-lite" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"review-findings-{story}.md").write_text(text, encoding="utf-8")


def _round2_state(story: str = STORY) -> State:
    """A state that is genuinely in round 2 of the CURRENT story."""
    state = State(current_epic=3, current_story=story)
    state.review_iteration = 1
    state.review_story_id = story
    return state


class TestReviewPromptSelection:
    def test_round1_uses_full_prompt(self, tmp_path):
        handler = _handler(tmp_path, {"delta_round2": True})
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"):
            prompt = handler._review_prompt(State(current_epic=3, current_story=STORY))
        assert prompt == "FULL PROMPT"

    def test_round2_uses_delta_prompt(self, tmp_path):
        _seed_findings(tmp_path, STORY, "R1 blocking finding X")
        handler = _handler(tmp_path, {"delta_round2": True})
        with patch("bmad_assist_lite.loop.handlers.code_review.git_diff", return_value="DIFF BODY"), patch.object(
            CodeReviewHandler, "render_prompt", side_effect=AssertionError("should not compile full prompt")
        ):
            prompt = handler._review_prompt(_round2_state())
        assert "Round-2 re-review" in prompt
        assert "R1 blocking finding X" in prompt
        assert "DIFF BODY" in prompt

    def test_delta_off_round2_still_full(self, tmp_path):
        handler = _handler(tmp_path, {"delta_round2": False})  # explicit opt-out
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"):
            prompt = handler._review_prompt(_round2_state())
        assert prompt == "FULL PROMPT"

    def test_stale_iteration_from_previous_story_uses_full_prompt(self, tmp_path):
        # review_iteration is only reset by the synthesis AFTER code_review runs,
        # so a new story's round-1 arrives with the previous story's counter.
        # The review_story_id guard must keep it on the full prompt.
        _seed_findings(tmp_path, "2.9", "previous story's findings")
        handler = _handler(tmp_path, {"delta_round2": True})
        state = State(current_epic=3, current_story=STORY)
        state.review_iteration = 1
        state.review_story_id = "2.9"  # stale: synthesis has not seen 3.1 yet
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"):
            prompt = handler._review_prompt(state)
        assert prompt == "FULL PROMPT"

    def test_missing_findings_falls_back_to_full_prompt(self, tmp_path):
        # A scoped "verify the fixes" prompt with no findings to verify is
        # vacuous; the handler must run a full re-review instead.
        handler = _handler(tmp_path, {"delta_round2": True})
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"):
            prompt = handler._review_prompt(_round2_state())
        assert prompt == "FULL PROMPT"

    def test_structured_addendum_appended_to_delta(self, tmp_path):
        _seed_findings(tmp_path, STORY, "R1 finding")
        handler = _handler(tmp_path, {"delta_round2": True, "structured_review": True})
        with patch("bmad_assist_lite.loop.handlers.code_review.git_diff", return_value="DIFF"):
            prompt = handler._review_prompt(_round2_state())
        assert "Round-2 re-review" in prompt
        assert "BMAD-FINDINGS" in prompt  # SP-1 addendum rides along


class TestDeltaPromptContent:
    def test_missing_diff_is_tolerated(self, tmp_path):
        _seed_findings(tmp_path, STORY, "R1 finding")
        handler = _handler(tmp_path, {"delta_round2": True})
        with patch("bmad_assist_lite.loop.handlers.code_review.git_diff", return_value=None):
            prompt = handler._build_delta_review_prompt(_round2_state())
        assert prompt is not None
        assert "no uncommitted diff detected" in prompt

    def test_missing_findings_returns_none(self, tmp_path):
        handler = _handler(tmp_path, {"delta_round2": True})
        with patch("bmad_assist_lite.loop.handlers.code_review.git_diff", return_value="DIFF"):
            assert handler._build_delta_review_prompt(_round2_state()) is None

    def test_oversized_diff_is_capped(self, tmp_path):
        _seed_findings(tmp_path, STORY, "R1 finding")
        handler = _handler(tmp_path, {"delta_round2": True})
        huge = "x" * (_MAX_INLINE_DIFF_CHARS + 10_000)
        with patch("bmad_assist_lite.loop.handlers.code_review.git_diff", return_value=huge):
            prompt = handler._build_delta_review_prompt(_round2_state())
        assert prompt is not None
        assert "diff truncated for length" in prompt
        assert len(prompt) < len(huge)

    def test_story_resolved_via_shared_resolver_alternate_form(self, tmp_path):
        # The repo's story resolver knows the alternate `{epic}-{story}-*.md`
        # naming form; the delta prompt must use it, not its own glob.
        _reset_paths()
        try:
            paths = init_paths(tmp_path)
            stories = paths.stories_dir
            stories.mkdir(parents=True, exist_ok=True)
            (stories / "3-1-blog-ui.md").write_text(
                "STORY BODY WITH ACCEPTANCE CRITERIA", encoding="utf-8"
            )
            _seed_findings(tmp_path, STORY, "R1 finding")
            handler = _handler(tmp_path, {"delta_round2": True})
            with patch(
                "bmad_assist_lite.loop.handlers.code_review.git_diff", return_value="DIFF"
            ):
                prompt = handler._build_delta_review_prompt(_round2_state())
        finally:
            _reset_paths()
        assert prompt is not None
        assert "STORY BODY WITH ACCEPTANCE CRITERIA" in prompt


class TestLeanReview:
    def test_lean_addendum_inlines_diff_and_demands_findings_only(self, tmp_path):
        handler = _handler(tmp_path, {"lean_review": True})
        with patch(
            "bmad_assist_lite.loop.handlers.code_review.git_diff", return_value="THE DIFF"
        ), patch.object(CodeReviewHandler, "render_prompt", return_value="FULL"):
            prompt = handler._review_prompt(State(current_epic=3, current_story=STORY))
        assert "FULL" in prompt
        assert "changed-code-diff" in prompt and "THE DIFF" in prompt
        assert "findings ONLY" in prompt
        # The diff-scoped instruction explicitly supersedes the compiled
        # workflow's "read every file" requirement.
        assert "supersedes" in prompt

    def test_lean_without_diff_keeps_discovery_instructions(self, tmp_path):
        # No diff to scope to: the addendum keeps the findings-only economy but
        # must not reference a diff that is not there, nor supersede discovery.
        handler = _handler(tmp_path, {"lean_review": True})
        with patch(
            "bmad_assist_lite.loop.handlers.code_review.git_diff", return_value=None
        ), patch.object(CodeReviewHandler, "render_prompt", return_value="FULL"):
            prompt = handler._review_prompt(State(current_epic=3, current_story=STORY))
        assert "findings ONLY" in prompt
        assert "changed-code-diff" not in prompt
        assert "diff above" not in prompt

    def test_lean_off_adds_no_addendum(self, tmp_path):
        handler = _handler(tmp_path, {})
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL"):
            prompt = handler._review_prompt(State(current_epic=3, current_story=STORY))
        assert prompt == "FULL"

    def test_delta_round2_skips_lean_addendum(self, tmp_path):
        # When both delta_round2 and lean_review are on, round-2 uses the delta
        # prompt (already lean) and must NOT also append the lean addendum.
        _seed_findings(tmp_path, STORY, "R1 finding")
        handler = _handler(tmp_path, {"delta_round2": True, "lean_review": True})
        with patch(
            "bmad_assist_lite.loop.handlers.code_review.git_diff", return_value="DIFF"
        ):
            prompt = handler._review_prompt(_round2_state())
        assert "Round-2 re-review" in prompt
        assert "<lean-review>" not in prompt


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=tmp_path, check=True)


def _init_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    f = tmp_path / "a.txt"
    f.write_text("one\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    return f


class TestGitDiffHelper:
    def test_returns_none_outside_repo(self, tmp_path):
        # tmp_path is not a git repo -> git diff exits non-zero -> None.
        assert git_diff(tmp_path) is None

    def test_captures_working_tree_diff(self, tmp_path):
        f = _init_repo(tmp_path)
        f.write_text("two\n")
        diff = git_diff(tmp_path)
        assert diff is not None and "-one" in diff and "+two" in diff
        stat = git_diff(tmp_path, stat=True)
        assert stat is not None and "a.txt" in stat

    def test_captures_staged_changes(self, tmp_path):
        # The diff is load-bearing for review scope: a change the fixer staged
        # must not become invisible.
        f = _init_repo(tmp_path)
        f.write_text("two\n")
        _git(tmp_path, "add", ".")
        diff = git_diff(tmp_path)
        assert diff is not None and "-one" in diff and "+two" in diff

    def test_lists_untracked_files(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "brand_new.py").write_text("print('hi')\n")
        diff = git_diff(tmp_path)
        assert diff is not None
        assert UNTRACKED_HEADING in diff
        assert "brand_new.py" in diff


class TestFinalRoundNeverDelta:
    """Review-owns-done: the promoting round is always a full review."""

    def test_final_round_uses_full_prompt(self, tmp_path):
        """At the cap (default 2) the round's verdict can promote to done, so
        the scoped delta — which cannot see unfixed criteria outside its diff
        — must not be the promoting review."""
        handler = _handler(tmp_path, {"delta_round2": True})
        _seed_findings(tmp_path, STORY, "round-1 findings body")
        state = _round2_state()
        state.review_iteration = 2  # == default cap
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"):
            prompt = handler._review_prompt(state)
        assert "FULL PROMPT" in prompt
        assert "Round-2 re-review" not in prompt
        assert handler._round_was_delta is False

    def test_middle_round_may_still_be_delta(self, tmp_path):
        """Below the cap the round can spawn another fix, so the cheap scoped
        re-review is allowed."""
        handler = _handler(tmp_path, {"delta_round2": True})
        _seed_findings(tmp_path, STORY, "round-1 findings body")
        state = _round2_state()  # iteration 1 < default cap 2
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"):
            prompt = handler._review_prompt(state)
        assert "Round-2 re-review" in prompt
        assert handler._round_was_delta is True

    def test_cap_one_has_no_delta_round_at_all(self, tmp_path):
        """With cap 1 the only re-review IS the promoting one — full."""
        from bmad_assist_lite.core.config import load_config

        config = load_config(
            {
                "providers": BASE_PROVIDERS,
                "speed": {**_SPEED_OFF, "delta_round2": True},
                "loop": {"review_max_iterations": 1},
            }
        )
        handler = CodeReviewHandler(config, tmp_path)
        _seed_findings(tmp_path, STORY, "round-1 findings body")
        state = _round2_state()  # iteration 1 == cap 1
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"):
            prompt = handler._review_prompt(state)
        assert "FULL PROMPT" in prompt
        assert handler._round_was_delta is False
