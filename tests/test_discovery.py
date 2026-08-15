"""Tests for workflow input-pattern discovery in the compiler.

Covers the four defect classes swept across the bundled ``workflow.yaml`` files:
CWD-relative patterns, silent zero-file resolutions, story naming forms, and
numeric over-match (story ``1.1`` must not absorb story ``1.10``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from bmad_assist_lite.compiler.discovery import (
    _glob_files,
    _resolve_pattern_variables,
    discover_files,
)
from bmad_assist_lite.compiler.types import CompilerContext, WorkflowIR
from bmad_assist_lite.core.exceptions import CompilerError

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "src" / "bmad_assist_lite" / "workflows"

PATTERN_KEYS = ("sharded", "whole", "pattern")

STORY_WORKFLOWS = (
    "code-review",
    "code-review-synthesis",
    "dev-story",
    "fix-quality-gate",
    "validate-story",
)


# ============================================================================
# Helpers
# ============================================================================


@contextmanager
def working_dir(path: Path) -> Iterator[None]:
    """Temporarily change the process CWD."""
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _bundled_workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS_DIR.glob("*/workflow.yaml"))
    assert files, f"No bundled workflow.yaml found under {WORKFLOWS_DIR}"
    return files


def _make_context(
    project_root: Path,
    workflow_name: str,
    epic_num: str = "1",
    story_num: str = "1",
) -> CompilerContext:
    """Build a CompilerContext bound to a bundled workflow's real config."""
    config_path = WORKFLOWS_DIR / workflow_name / "workflow.yaml"
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    output_folder = project_root / "_bmad-output"
    context = CompilerContext(
        project_root=project_root,
        output_folder=output_folder,
        resolved_variables={
            "epic_num": epic_num,
            "story_num": story_num,
            "planning_artifacts": str(output_folder / "planning-artifacts"),
            "implementation_artifacts": str(output_folder / "implementation-artifacts"),
        },
    )
    context.workflow_ir = WorkflowIR(
        name=workflow_name,
        config_path=config_path,
        instructions_path=config_path.parent / "instructions.xml",
        template_path=None,
        validation_path=None,
        raw_config=raw_config,
        raw_instructions="",
    )
    return context


def _make_project(tmp_path: Path, story_files: list[str]) -> Path:
    """Create a project root with the given story filenames."""
    root = tmp_path / "project"
    stories = root / "_bmad-output" / "implementation-artifacts"
    stories.mkdir(parents=True)
    (root / "_bmad-output" / "planning-artifacts").mkdir(parents=True, exist_ok=True)
    for name in story_files:
        (stories / name).write_text(f"# {name}\n", encoding="utf-8")
    return root


# ============================================================================
# Defect 1 — CWD-relative patterns
# ============================================================================


class TestPatternRooting:
    """Patterns must resolve independently of the process CWD."""

    def test_bundled_patterns_resolve_to_absolute_paths(self, tmp_path: Path) -> None:
        context = _make_context(tmp_path / "root", "fix-quality-gate")
        unrooted: list[str] = []

        for wf in _bundled_workflow_files():
            doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
            for input_name, cfg in (doc.get("input_file_patterns") or {}).items():
                for key in PATTERN_KEYS:
                    raw = cfg.get(key)
                    if not raw:
                        continue
                    resolved = _resolve_pattern_variables(str(raw), context)
                    if not Path(resolved).is_absolute():
                        unrooted.append(f"{wf.parent.name}/{input_name}/{key}: {raw}")

        assert not unrooted, "CWD-relative patterns found:\n  " + "\n  ".join(unrooted)

    def test_relative_pattern_globs_against_project_root_not_cwd(self, tmp_path: Path) -> None:
        root = _make_project(tmp_path, ["story-1.1.md"])
        foreign = tmp_path / "foreign-cwd"
        foreign.mkdir()

        with working_dir(foreign):
            files = _glob_files("**/story-1.1.md", "story_file", root)

        assert [f.name for f in files] == ["story-1.1.md"]

    @pytest.mark.parametrize("workflow_name", STORY_WORKFLOWS)
    def test_story_file_resolves_from_foreign_cwd(self, tmp_path: Path, workflow_name: str) -> None:
        root = _make_project(tmp_path, ["story-1.1.md"])
        foreign = tmp_path / "foreign-cwd"
        foreign.mkdir(exist_ok=True)
        context = _make_context(root, workflow_name)

        with working_dir(foreign):
            discovered = discover_files(context)

        assert [f.name for f in discovered["story_file"]] == ["story-1.1.md"]


# ============================================================================
# Defect 2 — silent zero-file resolutions
# ============================================================================


class TestRequiredDeclaration:
    """Every bundled input declares `required:` explicitly."""

    def test_every_bundled_input_declares_required(self) -> None:
        undeclared: list[str] = []

        for wf in _bundled_workflow_files():
            doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
            for input_name, cfg in (doc.get("input_file_patterns") or {}).items():
                if "required" not in cfg:
                    undeclared.append(f"{wf.parent.name}/{input_name}")

        assert not undeclared, "Inputs with no explicit `required:` key:\n  " + "\n  ".join(
            undeclared
        )

    @pytest.mark.parametrize("workflow_name", STORY_WORKFLOWS)
    def test_missing_story_file_raises_actionable_error(
        self, tmp_path: Path, workflow_name: str
    ) -> None:
        root = _make_project(tmp_path, [])
        context = _make_context(root, workflow_name)

        with pytest.raises(CompilerError) as exc_info:
            discover_files(context)

        message = str(exc_info.value)
        assert "story_file" in message
        assert "story-1.1.md" in message
        assert "1-1-" in message
        assert "required: false" in message

    def test_optional_input_missing_does_not_raise(self, tmp_path: Path) -> None:
        root = _make_project(tmp_path, ["story-1.1.md"])
        context = _make_context(root, "dev-story")

        discovered = discover_files(context)

        assert discovered["project_context"] == []


# ============================================================================
# Defect 3 — both story naming forms
# ============================================================================


class TestStoryNamingForms:
    """Both the primary and the generated story filename forms resolve."""

    @pytest.mark.parametrize("workflow_name", STORY_WORKFLOWS)
    def test_primary_form_resolves(self, tmp_path: Path, workflow_name: str) -> None:
        root = _make_project(tmp_path, ["story-1.1.md"])
        context = _make_context(root, workflow_name)

        discovered = discover_files(context)

        assert [f.name for f in discovered["story_file"]] == ["story-1.1.md"]

    @pytest.mark.parametrize("workflow_name", STORY_WORKFLOWS)
    def test_alternate_form_resolves(self, tmp_path: Path, workflow_name: str) -> None:
        root = _make_project(tmp_path, ["1-1-first-story.md"])
        context = _make_context(root, workflow_name)

        discovered = discover_files(context)

        assert [f.name for f in discovered["story_file"]] == ["1-1-first-story.md"]


# ============================================================================
# Defect 4 — numeric over-match
# ============================================================================


class TestOverMatch:
    """Story 1.1 must not absorb stories 1.10 / 1.11."""

    @pytest.mark.parametrize("workflow_name", STORY_WORKFLOWS)
    def test_alternate_form_does_not_match_two_digit_siblings(
        self, tmp_path: Path, workflow_name: str
    ) -> None:
        root = _make_project(
            tmp_path,
            ["1-1-first-story.md", "1-10-tenth-story.md", "1-11-eleventh-story.md"],
        )
        context = _make_context(root, workflow_name)

        discovered = discover_files(context)

        assert [f.name for f in discovered["story_file"]] == ["1-1-first-story.md"]

    @pytest.mark.parametrize("workflow_name", STORY_WORKFLOWS)
    def test_primary_form_does_not_match_two_digit_siblings(
        self, tmp_path: Path, workflow_name: str
    ) -> None:
        root = _make_project(tmp_path, ["story-1.1.md", "story-1.10.md"])
        context = _make_context(root, workflow_name)

        discovered = discover_files(context)

        assert [f.name for f in discovered["story_file"]] == ["story-1.1.md"]

    def test_epic_pattern_does_not_match_two_digit_siblings(self, tmp_path: Path) -> None:
        root = _make_project(tmp_path, [])
        planning = root / "_bmad-output" / "planning-artifacts"
        (planning / "epic-1.md").write_text("# Epic 1\n", encoding="utf-8")
        (planning / "epic-10.md").write_text("# Epic 10\n", encoding="utf-8")
        (planning / "epic-11.md").write_text("# Epic 11\n", encoding="utf-8")
        context = _make_context(root, "create-story")

        discovered = discover_files(context)

        assert [f.name for f in discovered["epic_file"]] == ["epic-1.md"]

    def test_retrospective_epic_pattern_does_not_over_match(self, tmp_path: Path) -> None:
        root = _make_project(tmp_path, [])
        planning = root / "_bmad-output" / "planning-artifacts"
        (planning / "epic-1-foundation.md").write_text("# Epic 1\n", encoding="utf-8")
        (planning / "epic-10-later.md").write_text("# Epic 10\n", encoding="utf-8")
        context = _make_context(root, "retrospective")

        discovered = discover_files(context)

        assert [f.name for f in discovered["epics_file"]] == ["epic-1-foundation.md"]
