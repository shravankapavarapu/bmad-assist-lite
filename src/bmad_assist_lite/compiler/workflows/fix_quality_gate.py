"""Compiler for fix-quality-gate workflow."""

import importlib.resources
from pathlib import Path
from typing import Any

from bmad_assist_lite.compiler.discovery import discover_files, load_file_contents
from bmad_assist_lite.compiler.output import generate_output
from bmad_assist_lite.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist_lite.compiler.variables import resolve_variables
from bmad_assist_lite.core.exceptions import CompilerError


class FixQualityGateCompiler:
    """Compiler for fix-quality-gate workflow."""

    @property
    def workflow_name(self) -> str:
        """Return the workflow name."""
        return "fix-quality-gate"

    def get_workflow_dir(self, context: CompilerContext) -> Path:
        """Return bundled workflow directory."""
        ref = importlib.resources.files("bmad_assist_lite.workflows") / "fix-quality-gate"
        return Path(str(ref))

    def get_required_files(self) -> list[str]:
        """Return glob patterns for required files."""
        return []

    def get_variables(self) -> dict[str, Any]:
        """Return default variables for this workflow."""
        return {}

    def validate_context(self, context: CompilerContext) -> None:
        """Validate that project root exists."""
        if not context.project_root.exists():
            raise CompilerError(f"Project root not found: {context.project_root}")

    def compile(self, context: CompilerContext) -> CompiledWorkflow:
        """Compile fix-quality-gate workflow."""
        if context.workflow_ir is None:
            raise CompilerError("workflow_ir not set")

        ir = context.workflow_ir

        # Resolve variables
        invocation_vars = {
            k: v for k, v in context.resolved_variables.items() if isinstance(v, (str, int, float))
        }
        resolved = resolve_variables(context, invocation_vars)

        # Discover and load files (workflow has no patterns, but call for consistency)
        discover_files(context)
        load_file_contents(context)

        # Build context from loaded files
        context_text = "\n\n".join(
            f"--- {name} ---\n{content}"
            for name, content in context.file_contents.items()
            if content
        )

        # Get mission from config
        mission = resolved.get("mission", f"Execute {self.workflow_name} workflow")
        if not isinstance(mission, str):
            mission = str(mission)

        compiled = CompiledWorkflow(
            workflow_name=self.workflow_name,
            mission=mission,
            context=context_text,
            variables=resolved,
            instructions=ir.raw_instructions,
            output_template="",
        )

        # Generate XML output
        output = generate_output(
            compiled, context.project_root, context.file_contents, context.links_only, context.stable_prefix
        )

        return CompiledWorkflow(
            workflow_name=self.workflow_name,
            mission=mission,
            context=output.xml,
            variables=resolved,
            instructions=ir.raw_instructions,
            output_template="",
            token_estimate=output.token_estimate,
        )
