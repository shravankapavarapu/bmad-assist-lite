"""Core compiler module with WorkflowCompiler protocol and dynamic loading."""

import importlib
import logging
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from bmad_assist_lite.compiler.parser import parse_workflow
from bmad_assist_lite.compiler.types import CompiledWorkflow, CompilerContext
from bmad_assist_lite.core.exceptions import CompilerError

logger = logging.getLogger(__name__)


@runtime_checkable
class WorkflowCompiler(Protocol):
    """Protocol for workflow-specific compilers."""

    @property
    def workflow_name(self) -> str: ...

    def get_workflow_dir(self, context: CompilerContext) -> Path: ...

    def get_required_files(self) -> list[str]: ...

    def get_variables(self) -> dict[str, Any]: ...

    def validate_context(self, context: CompilerContext) -> None: ...

    def compile(self, context: CompilerContext) -> CompiledWorkflow: ...


_WORKFLOW_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def get_workflow_compiler(workflow_name: str) -> WorkflowCompiler:
    """Load workflow compiler by name."""
    if not workflow_name or not workflow_name.strip():
        raise CompilerError("Workflow name cannot be empty")

    normalized_name = workflow_name.strip()

    if not _WORKFLOW_NAME_PATTERN.fullmatch(normalized_name):
        raise CompilerError(f"Invalid workflow name: '{workflow_name}'")

    module_name = normalized_name.replace("-", "_")
    module_path = f"bmad_assist_lite.compiler.workflows.{module_name}"

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        missing_module = getattr(e, "name", None)
        if missing_module in (module_path, module_name):
            raise CompilerError(f"Workflow not found: '{normalized_name}'") from e
        raise CompilerError(f"Workflow '{normalized_name}' has import errors: {e}") from e
    except (SyntaxError, ImportError) as e:
        raise CompilerError(f"Workflow '{normalized_name}' has errors: {e}") from e

    class_name = "".join(word.capitalize() for word in module_name.split("_")) + "Compiler"
    compiler_class = getattr(module, class_name, None)

    if compiler_class is None:
        raise CompilerError(
            f"Workflow module missing compiler class: expected {class_name} in {module_path}"
        )

    try:
        instance: WorkflowCompiler = compiler_class()
    except Exception as e:
        raise CompilerError(f"Workflow failed to instantiate: {e}") from e

    return instance


def compile_workflow(
    workflow_name: str,
    context: CompilerContext,
) -> CompiledWorkflow:
    """Compile a workflow by name with given context."""
    compiler = get_workflow_compiler(workflow_name)
    compiler.validate_context(context)

    workflow_dir = compiler.get_workflow_dir(context)
    workflow_ir = parse_workflow(workflow_dir)

    context.workflow_ir = workflow_ir

    return compiler.compile(context)
