"""Tests for SP-4 reviewer stagger removal (goal-run6)."""

from __future__ import annotations

from typing import Any

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler

PROVIDERS: dict[str, Any] = {
    "master": {"provider": "claude", "model": "opus"},
    "multi": [{"provider": "claude", "model": "fable"}],
}


def _handler(tmp_path, speed: dict[str, Any], parallel_delay: float = 8.0) -> CodeReviewHandler:
    config = load_config(
        {"providers": PROVIDERS, "parallel_delay": parallel_delay, "speed": speed}
    )
    return CodeReviewHandler(config, tmp_path)


class TestReviewerStagger:
    def test_default_removes_stagger_even_with_system_prompt(self, tmp_path):
        # remove_stagger defaults ON since the 2026-08-16 flip.
        handler = _handler(tmp_path, {})
        assert handler._reviewer_stagger("SYS") == 0.0

    def test_opt_out_restores_stagger_when_system_prompt(self, tmp_path):
        handler = _handler(tmp_path, {"remove_stagger": False})
        assert handler._reviewer_stagger("SYS") == 8.0

    def test_default_zero_without_system_prompt(self, tmp_path):
        handler = _handler(tmp_path, {})
        assert handler._reviewer_stagger(None) == 0.0

    def test_remove_stagger_forces_zero_even_with_system_prompt(self, tmp_path):
        handler = _handler(tmp_path, {"remove_stagger": True})
        assert handler._reviewer_stagger("SYS") == 0.0
        assert handler._reviewer_stagger(None) == 0.0
