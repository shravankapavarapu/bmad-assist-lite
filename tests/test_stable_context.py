"""Tests for the stable-context superset builder (L1 / compiler.stable_prefix)."""

from pathlib import Path

from bmad_assist_lite.compiler.stable_context import build_stable_system_prompt
from bmad_assist_lite.compiler.types import CompilerContext
from bmad_assist_lite.core.config import CompilerConfig


def _ctx(root: Path, story: int) -> CompilerContext:
    return CompilerContext(
        project_root=root,
        output_folder=root / "_bmad-output",
        resolved_variables={
            "epic_num": 3,
            "story_num": story,
            "planning_artifacts": str(root / "_bmad-output" / "planning-artifacts"),
        },
    )


def _seed(root: Path) -> None:
    pa = root / "_bmad-output" / "planning-artifacts"
    pa.mkdir(parents=True)
    (root / "_bmad-output" / "implementation-artifacts").mkdir(parents=True)
    (root / "project-context.md").write_text("# Project\nFIXED_CONTEXT\n", encoding="utf-8")
    (pa / "architecture.md").write_text("# Arch\nFIXED_ARCH\n", encoding="utf-8")
    (pa / "epic-3.md").write_text("# Epic 3\nFULL_EPIC_BODY\n", encoding="utf-8")
    (root / "_bmad-output" / "implementation-artifacts" / "story-3.1.md").write_text(
        "# Story 3.1\nSTORY_VOLATILE\n", encoding="utf-8"
    )


class TestStableSuperset:
    def test_loads_stable_epic_artifacts(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        block = build_stable_system_prompt(_ctx(tmp_path, 1))
        assert block is not None
        assert "FIXED_CONTEXT" in block
        assert "FIXED_ARCH" in block
        assert "FULL_EPIC_BODY" in block

    def test_excludes_story_files(self, tmp_path: Path) -> None:
        """The per-story volatile content must NOT enter the cacheable prefix."""
        _seed(tmp_path)
        block = build_stable_system_prompt(_ctx(tmp_path, 1))
        assert block is not None
        assert "STORY_VOLATILE" not in block

    def test_byte_identical_across_stories(self, tmp_path: Path) -> None:
        """The caching precondition: same bytes for every story of the epic."""
        _seed(tmp_path)
        a = build_stable_system_prompt(_ctx(tmp_path, 1))
        b = build_stable_system_prompt(_ctx(tmp_path, 2))
        assert a is not None
        assert a == b

    def test_none_when_no_artifacts(self, tmp_path: Path) -> None:
        (tmp_path / "_bmad-output" / "planning-artifacts").mkdir(parents=True)
        assert build_stable_system_prompt(_ctx(tmp_path, 1)) is None


class TestUserMessageExclusion:
    """Stable artifacts leave the user message under stable_prefix.

    They ride the cached system prompt instead; story/volatile files stay.
    """

    def _compiled(self):  # type: ignore[no-untyped-def]
        from bmad_assist_lite.compiler.types import CompiledWorkflow

        return CompiledWorkflow(
            workflow_name="x",
            mission="m",
            context="",
            variables={},
            instructions="i",
            output_template="t",
        )

    def test_off_keeps_all_files(self) -> None:
        from bmad_assist_lite.compiler.output import generate_output

        files = {"project_context": "PCBODY", "epic_file": "EPICBODY", "story_file": "STORYBODY"}
        xml = generate_output(self._compiled(), context_files=files, stable_prefix=False).xml
        assert "PCBODY" in xml and "EPICBODY" in xml and "STORYBODY" in xml

    def test_on_drops_stable_keeps_story(self) -> None:
        from bmad_assist_lite.compiler.output import generate_output

        files = {
            "project_context": "PCBODY",
            "architecture_file": "ARCHBODY",
            "epic_file": "EPICBODY",
            "story_file": "STORYBODY",
        }
        xml = generate_output(self._compiled(), context_files=files, stable_prefix=True).xml
        assert "STORYBODY" in xml
        assert "PCBODY" not in xml
        assert "ARCHBODY" not in xml
        assert "EPICBODY" not in xml


class TestCompilerConfig:
    def test_stable_prefix_defaults_off(self) -> None:
        assert CompilerConfig().stable_prefix is False
