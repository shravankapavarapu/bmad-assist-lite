"""Compiler for retrospective workflow."""

import importlib.resources
from pathlib import Path
from typing import Any

from bmad_assist_lite.compiler.discovery import discover_files, load_file_contents
from bmad_assist_lite.compiler.output import generate_output
from bmad_assist_lite.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist_lite.compiler.variables import resolve_variables
from bmad_assist_lite.core.exceptions import CompilerError


class RetrospectiveCompiler:
    """Compiler for retrospective workflow."""

    @property
    def workflow_name(self) -> str:
        """Return the workflow name."""
        return "retrospective"

    def get_workflow_dir(self, context: CompilerContext) -> Path:
        """Return bundled workflow directory."""
        ref = importlib.resources.files("bmad_assist_lite.workflows") / "retrospective"
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
        """Compile retrospective workflow."""
        if context.workflow_ir is None:
            raise CompilerError("workflow_ir not set")

        ir = context.workflow_ir

        # Resolve variables
        invocation_vars = {
            k: v for k, v in context.resolved_variables.items() if isinstance(v, (str, int, float))
        }
        resolved = resolve_variables(context, invocation_vars)

        # Discover and load files
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

        # Load output template
        output_template = ""
        if ir.template_path:
            template_path = Path(ir.template_path)
            if not template_path.is_absolute():
                template_path = ir.config_path.parent / template_path
            if template_path.exists():
                output_template = template_path.read_text(encoding="utf-8")

        compiled = CompiledWorkflow(
            workflow_name=self.workflow_name,
            mission=mission,
            context=context_text,
            variables=resolved,
            instructions=ir.raw_instructions,
            output_template=output_template,
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
            output_template=output_template,
            token_estimate=output.token_estimate,
        )
