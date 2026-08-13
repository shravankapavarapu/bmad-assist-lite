"""Tests for SP-2 scoped delta round-2 review (goal-run6)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.git import git_diff
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler

BASE_PROVIDERS: dict[str, Any] = {
    "master": {"provider": "claude", "model": "opus"},
    "multi": [{"provider": "claude", "model": "fable"}],
}


def _handler(project: Path, speed: dict[str, Any]) -> CodeReviewHandler:
    config = load_config({"providers": BASE_PROVIDERS, "speed": speed})
    return CodeReviewHandler(config, project)


def _seed_findings(project: Path, story: str, text: str) -> None:
    cache = project / ".bmad-assist-lite" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"review-findings-{story}.md").write_text(text, encoding="utf-8")


class TestReviewPromptSelection:
    def test_round1_uses_full_prompt(self, tmp_path):
        handler = _handler(tmp_path, {"delta_round2": True})
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"):
            prompt = handler._review_prompt(State(current_epic=3, current_story="3.1"))
        assert prompt == "FULL PROMPT"

    def test_round2_uses_delta_prompt(self, tmp_path):
        _seed_findings(tmp_path, "3.1", "R1 blocking finding X")
        handler = _handler(tmp_path, {"delta_round2": True})
        state = State(current_epic=3, current_story="3.1")
        state.review_iteration = 1
        with patch("bmad_assist_lite.loop.handlers.code_review.git_diff", return_value="DIFF BODY"), patch.object(
            CodeReviewHandler, "render_prompt", side_effect=AssertionError("should not compile full prompt")
        ):
            prompt = handler._review_prompt(state)
        assert "Round-2 re-review" in prompt
        assert "R1 blocking finding X" in prompt
        assert "DIFF BODY" in prompt

    def test_delta_off_round2_still_full(self, tmp_path):
        handler = _handler(tmp_path, {})  # delta_round2 default off
        state = State(current_epic=3, current_story="3.1")
        state.review_iteration = 1
        with patch.object(CodeReviewHandler, "render_prompt", return_value="FULL PROMPT"):
            prompt = handler._review_prompt(state)
        assert prompt == "FULL PROMPT"

    def test_structured_addendum_appended_to_delta(self, tmp_path):
        _seed_findings(tmp_path, "3.1", "R1 finding")
        handler = _handler(tmp_path, {"delta_round2": True, "structured_review": True})
        state = State(current_epic=3, current_story="3.1")
        state.review_iteration = 1
        with patch("bmad_assist_lite.loop.handlers.code_review.git_diff", return_value="DIFF"):
            prompt = handler._review_prompt(state)
        assert "Round-2 re-review" in prompt
        assert "BMAD-FINDINGS" in prompt  # SP-1 addendum rides along


class TestDeltaPromptContent:
    def test_missing_diff_is_tolerated(self, tmp_path):
        _seed_findings(tmp_path, "3.1", "R1 finding")
        handler = _handler(tmp_path, {"delta_round2": True})
        state = State(current_epic=3, current_story="3.1")
        state.review_iteration = 1
        with patch("bmad_assist_lite.loop.handlers.code_review.git_diff", return_value=None):
            prompt = handler._build_delta_review_prompt(state)
        assert "no uncommitted diff detected" in prompt


class TestGitDiffHelper:
    def test_returns_none_outside_repo(self, tmp_path):
        # tmp_path is not a git repo -> git diff exits non-zero -> None.
        assert git_diff(tmp_path) is None

    def test_captures_working_tree_diff(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        f = tmp_path / "a.txt"
        f.write_text("one\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
        f.write_text("two\n")
        diff = git_diff(tmp_path)
        assert diff is not None and "-one" in diff and "+two" in diff
        stat = git_diff(tmp_path, stat=True)
        assert stat is not None and "a.txt" in stat
