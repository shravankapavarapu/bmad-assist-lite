"""Tests for the compiler pipeline.

Covers: workflow compiler loading, CompilerContext defaults, variable resolution.
"""

from pathlib import Path

import pytest

from bmad_assist_lite.compiler.core import get_workflow_compiler
from bmad_assist_lite.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist_lite.compiler.variables import resolve_variables
from bmad_assist_lite.core.exceptions import CompilerError


# ---------------------------------------------------------------------------
# get_workflow_compiler
# ---------------------------------------------------------------------------


class TestGetWorkflowCompiler:
    """Tests for dynamic workflow compiler loading."""

    def test_get_workflow_compiler_create_story(self):
        """get_workflow_compiler('create-story') returns compiler with correct name."""
        compiler = get_workflow_compiler("create-story")
        assert compiler.workflow_name == "create-story"

    @pytest.mark.parametrize(
        "workflow_name",
        [
            "create-story",
            "validate-story",
            "validate-story-synthesis",
            "dev-story",
            "code-review",
            "code-review-synthesis",
            "retrospective",
        ],
    )
    def test_get_workflow_compiler_all_workflows(self, workflow_name: str):
        """All 7 bundled workflow names should load without error."""
        compiler = get_workflow_compiler(workflow_name)
        assert compiler.workflow_name == workflow_name

    def test_get_workflow_compiler_invalid_name(self):
        """Non-existent workflow name raises CompilerError."""
        with pytest.raises(CompilerError):
            get_workflow_compiler("nonexistent-workflow")

    def test_get_workflow_compiler_empty_name(self):
        """Empty string raises CompilerError."""
        with pytest.raises(CompilerError):
            get_workflow_compiler("")


# ---------------------------------------------------------------------------
# CompilerContext defaults
# ---------------------------------------------------------------------------


class TestCompilerContext:
    """Tests for CompilerContext dataclass defaults."""

    def test_compiler_context_defaults(self, tmp_path: Path):
        """CompilerContext initialises with correct default values."""
        ctx = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "_output",
        )

        assert ctx.project_root == tmp_path
        assert ctx.output_folder == tmp_path / "_output"
        assert ctx.project_knowledge is None
        assert ctx.cwd is None
        assert ctx.workflow_ir is None
        assert ctx.resolved_variables == {}
        assert ctx.discovered_files == {}
        assert ctx.file_contents == {}
        assert ctx.links_only is False


# ---------------------------------------------------------------------------
# resolve_variables
# ---------------------------------------------------------------------------


class TestResolveVariables:
    """Tests for the variable resolution pipeline."""

    @staticmethod
    def _make_context_with_ir(tmp_path: Path) -> CompilerContext:
        """Build a minimal CompilerContext with a WorkflowIR attached."""
        config_path = tmp_path / "workflow.yaml"
        config_path.write_text("", encoding="utf-8")

        instructions_path = tmp_path / "instructions.md"
        instructions_path.write_text("", encoding="utf-8")

        ir = WorkflowIR(
            name="test-workflow",
            config_path=config_path,
            instructions_path=instructions_path,
            template_path=None,
            validation_path=None,
            raw_config={},
            raw_instructions="",
        )

        ctx = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "_output",
            workflow_ir=ir,
        )
        return ctx

    def test_resolve_variables_basic(self, tmp_path: Path):
        """Invocation params are merged into resolved variables."""
        ctx = self._make_context_with_ir(tmp_path)
        result = resolve_variables(ctx, {"foo": "bar", "num": 42})

        assert result["foo"] == "bar"
        assert result["num"] == 42
        # Hard overrides are always applied
        assert result["user_skill_level"] == "expert"
        assert result["communication_language"] == "English"

    def test_resolve_variables_no_workflow_ir_raises(self, tmp_path: Path):
        """Calling resolve_variables without workflow_ir raises VariableError."""
        from bmad_assist_lite.core.exceptions import VariableError

        ctx = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "_output",
        )
        with pytest.raises(VariableError):
            resolve_variables(ctx, {})

    def test_resolve_variables_updates_context(self, tmp_path: Path):
        """resolve_variables stores results in context.resolved_variables."""
        ctx = self._make_context_with_ir(tmp_path)
        result = resolve_variables(ctx, {"key": "value"})

        assert ctx.resolved_variables is result
        assert ctx.resolved_variables["key"] == "value"
