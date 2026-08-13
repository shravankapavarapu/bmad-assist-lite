"""Tests for L3 epic-knowledge (goal-run5 Phase 2).

Covers the writer hook (gated OFF = no-op; ON = bounded, durable file; best-effort
on provider failure) and the injector (the brief rides the stable system prompt,
last, only when enabled and present).
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from bmad_assist_lite.compiler.stable_context import build_stable_system_prompt
from bmad_assist_lite.compiler.types import CompilerContext
from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.epic_knowledge import (
    _bound,
    epic_knowledge_path,
    read_epic_knowledge,
    write_epic_knowledge_after_story,
)
from bmad_assist_lite.core.paths import init_paths
from bmad_assist_lite.core.state import State
from bmad_assist_lite.providers.base import READ_ONLY_TOOLS, ProviderResult

_MASTER = {"provider": "claude", "model": "opus"}


def _config(*, enabled: bool, max_chars: int = 8000) -> Any:
    return load_config(
        {
            "providers": {"master": _MASTER},
            "epic_knowledge": {"enabled": enabled, "max_chars": max_chars},
        }
    )


def _fake_provider(brief: str) -> MagicMock:
    provider = MagicMock()
    provider.provider_name = "claude"
    provider.default_model = "opus"
    provider.invoke.return_value = ProviderResult(
        stdout=brief,
        stderr="",
        exit_code=0,
        duration_ms=1,
        model="opus",
        command=("claude",),
        provider_session_id="sess-writer",
    )
    provider.parse_output.side_effect = lambda r: r.stdout.strip()
    return provider


def _ctx(root: Path, epic: int = 3, story: int = 2) -> CompilerContext:
    return CompilerContext(
        project_root=root,
        output_folder=root / "_bmad-output",
        resolved_variables={
            "epic_num": epic,
            "story_num": story,
            "planning_artifacts": str(root / "_bmad-output" / "planning-artifacts"),
        },
    )


def _seed_stable(root: Path) -> None:
    pa = root / "_bmad-output" / "planning-artifacts"
    pa.mkdir(parents=True)
    (root / "_bmad-output" / "implementation-artifacts").mkdir(parents=True)
    (root / "project-context.md").write_text("# Project\nFIXED_CONTEXT\n", encoding="utf-8")
    (pa / "architecture.md").write_text("# Arch\nFIXED_ARCH\n", encoding="utf-8")
    (pa / "epic-3.md").write_text("# Epic 3\nFULL_EPIC_BODY\n", encoding="utf-8")


# ============================================================================
# Path / read / bound helpers
# ============================================================================


class TestPathAndRead:
    def test_path_under_bmad_dir_not_cache(self, tmp_path: Path) -> None:
        init_paths(tmp_path)
        path = epic_knowledge_path(3)
        assert path is not None
        assert path.name == "epic-knowledge-3.md"
        assert path.parent.name == ".bmad-assist-lite"
        assert "cache" not in path.parts  # survives the story-transition sweep

    def test_path_none_when_epic_unknown(self, tmp_path: Path) -> None:
        init_paths(tmp_path)
        assert epic_knowledge_path(None) is None

    def test_read_absent_is_none(self, tmp_path: Path) -> None:
        init_paths(tmp_path)
        assert read_epic_knowledge(3) is None

    def test_read_roundtrip(self, tmp_path: Path) -> None:
        init_paths(tmp_path)
        p = epic_knowledge_path(3)
        assert p is not None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# brief\nCARRY_FORWARD\n", encoding="utf-8")
        assert read_epic_knowledge(3) == "# brief\nCARRY_FORWARD"


class TestBound:
    def test_under_cap_untouched(self) -> None:
        assert _bound("short", 8000) == "short"

    def test_over_cap_truncated_with_marker(self) -> None:
        out = _bound("x" * 10_000, 500)
        assert len(out) <= 500
        assert "truncated" in out

    def test_zero_cap_disables_bounding(self) -> None:
        assert _bound("anything", 0) == "anything"


# ============================================================================
# Writer hook
# ============================================================================


class TestWriterGating:
    def test_disabled_is_noop(self, tmp_path: Path) -> None:
        init_paths(tmp_path)
        cfg = _config(enabled=False)
        provider = _fake_provider("# brief")
        with patch("bmad_assist_lite.providers.get_provider", return_value=provider):
            write_epic_knowledge_after_story(
                cfg, tmp_path, State(current_epic=3, current_story="3.1")
            )
        provider.invoke.assert_not_called()
        assert epic_knowledge_path(3) is not None
        assert not epic_knowledge_path(3).exists()  # type: ignore[union-attr]

    def test_no_story_is_noop(self, tmp_path: Path) -> None:
        init_paths(tmp_path)
        cfg = _config(enabled=True)
        provider = _fake_provider("# brief")
        with patch("bmad_assist_lite.providers.get_provider", return_value=provider):
            write_epic_knowledge_after_story(cfg, tmp_path, State(current_epic=3))
        provider.invoke.assert_not_called()


class TestWriterWrites:
    def _run(self, tmp_path: Path, brief: str, max_chars: int = 8000) -> MagicMock:
        init_paths(tmp_path)
        cfg = _config(enabled=True, max_chars=max_chars)
        provider = _fake_provider(brief)
        with patch("bmad_assist_lite.providers.get_provider", return_value=provider):
            write_epic_knowledge_after_story(
                cfg, tmp_path, State(current_epic=3, current_story="3.1")
            )
        return provider

    def test_writes_brief_to_durable_path(self, tmp_path: Path) -> None:
        self._run(tmp_path, "# Epic 3 — knowledge brief\nDECISION_X\n")
        assert read_epic_knowledge(3) == "# Epic 3 — knowledge brief\nDECISION_X"

    def test_writer_gets_read_only_tools(self, tmp_path: Path) -> None:
        """The model emits text; it must never carry write/execute tools."""
        provider = self._run(tmp_path, "# brief")
        kwargs = provider.invoke.call_args.kwargs
        assert kwargs["allowed_tools"] == list(READ_ONLY_TOOLS)

    def test_writer_uses_master_model(self, tmp_path: Path) -> None:
        provider = self._run(tmp_path, "# brief")
        assert provider.invoke.call_args.kwargs["model"] == "opus"

    def test_output_is_bounded(self, tmp_path: Path) -> None:
        self._run(tmp_path, "# brief\n" + "y" * 10_000, max_chars=800)
        stored = read_epic_knowledge(3)
        assert stored is not None
        assert len(stored) <= 800

    def test_existing_brief_passed_into_prompt(self, tmp_path: Path) -> None:
        init_paths(tmp_path)
        p = epic_knowledge_path(3)
        assert p is not None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Epic 3 brief\nPRIOR_KNOWLEDGE\n", encoding="utf-8")

        cfg = _config(enabled=True)
        provider = _fake_provider("# Epic 3 brief\nPRIOR_KNOWLEDGE\nNEW\n")
        with patch("bmad_assist_lite.providers.get_provider", return_value=provider):
            write_epic_knowledge_after_story(
                cfg, tmp_path, State(current_epic=3, current_story="3.2")
            )
        prompt = provider.invoke.call_args.args[0]
        assert "PRIOR_KNOWLEDGE" in prompt  # merge, not blind append
        assert "3.2" in prompt


class TestWriterBestEffort:
    def test_provider_failure_never_raises(self, tmp_path: Path) -> None:
        init_paths(tmp_path)
        cfg = _config(enabled=True)
        provider = MagicMock()
        provider.invoke.side_effect = RuntimeError("boom")
        with patch("bmad_assist_lite.providers.get_provider", return_value=provider):
            write_epic_knowledge_after_story(
                cfg, tmp_path, State(current_epic=3, current_story="3.1")
            )
        # No brief written, but the run continues.
        assert read_epic_knowledge(3) is None

    def test_empty_brief_keeps_prior(self, tmp_path: Path) -> None:
        init_paths(tmp_path)
        p = epic_knowledge_path(3)
        assert p is not None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# prior\nKEEP_ME\n", encoding="utf-8")

        cfg = _config(enabled=True)
        provider = _fake_provider("   ")  # whitespace-only
        with patch("bmad_assist_lite.providers.get_provider", return_value=provider):
            write_epic_knowledge_after_story(
                cfg, tmp_path, State(current_epic=3, current_story="3.2")
            )
        assert read_epic_knowledge(3) == "# prior\nKEEP_ME"


# ============================================================================
# Injector — the brief rides the stable system prompt, last, only when enabled
# ============================================================================


class TestInjector:
    def test_absent_when_flag_off(self, tmp_path: Path) -> None:
        _seed_stable(tmp_path)
        init_paths(tmp_path)
        _config(enabled=False)
        p = epic_knowledge_path(3)
        assert p is not None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("BRIEF_BODY", encoding="utf-8")

        block = build_stable_system_prompt(_ctx(tmp_path))
        assert block is not None
        assert "BRIEF_BODY" not in block

    def test_absent_when_no_brief(self, tmp_path: Path) -> None:
        _seed_stable(tmp_path)
        init_paths(tmp_path)
        _config(enabled=True)
        block = build_stable_system_prompt(_ctx(tmp_path))
        assert block is not None
        assert 'name="epic_knowledge"' not in block

    def test_present_and_last_when_enabled(self, tmp_path: Path) -> None:
        _seed_stable(tmp_path)
        init_paths(tmp_path)
        _config(enabled=True)
        p = epic_knowledge_path(3)
        assert p is not None
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# brief\nBRIEF_BODY\n", encoding="utf-8")

        block = build_stable_system_prompt(_ctx(tmp_path))
        assert block is not None
        assert "BRIEF_BODY" in block
        assert 'name="epic_knowledge"' in block
        # The volatile brief must sit AFTER the epic-invariant artifacts so the
        # invariant prefix stays byte-identical across stories.
        assert block.index("BRIEF_BODY") > block.index("FULL_EPIC_BODY")
        assert block.index("BRIEF_BODY") > block.index("FIXED_CONTEXT")
