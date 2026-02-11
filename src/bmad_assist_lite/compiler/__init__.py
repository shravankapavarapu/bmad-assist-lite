"""BMAD Workflow Compiler module.

Public API for compiling BMAD workflows into standalone prompts.
"""

from bmad_assist_lite.compiler.core import (
    WorkflowCompiler,
    compile_workflow,
    get_workflow_compiler,
)
from bmad_assist_lite.compiler.discovery import (
    LoadStrategy,
    discover_files,
    extract_section,
    load_file_contents,
)
from bmad_assist_lite.compiler.output import (
    GeneratedOutput,
    generate_output,
    validate_token_budget,
)
from bmad_assist_lite.compiler.parser import parse_workflow
from bmad_assist_lite.compiler.types import CompiledWorkflow, CompilerContext, WorkflowIR
from bmad_assist_lite.compiler.variables import resolve_variables
from bmad_assist_lite.core.exceptions import (
    AmbiguousFileError,
    CompilerError,
    ParserError,
    VariableError,
)

__all__ = [
    "compile_workflow",
    "get_workflow_compiler",
    "parse_workflow",
    "resolve_variables",
    "discover_files",
    "load_file_contents",
    "extract_section",
    "generate_output",
    "validate_token_budget",
    "LoadStrategy",
    "WorkflowCompiler",
    "CompilerError",
    "ParserError",
    "VariableError",
    "AmbiguousFileError",
    "CompilerContext",
    "CompiledWorkflow",
    "WorkflowIR",
    "GeneratedOutput",
]
