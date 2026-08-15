"""Data models for the BMAD workflow compiler."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkflowIR:
    """Intermediate representation of a parsed BMAD workflow."""

    name: str
    config_path: Path
    instructions_path: Path
    template_path: str | None
    validation_path: str | None
    raw_config: dict[str, Any]
    raw_instructions: str
    output_template: str | None = None


@dataclass(frozen=True)
class CompiledWorkflow:
    """Final compiled workflow output ready for LLM consumption."""

    workflow_name: str
    mission: str
    context: str
    variables: dict[str, Any]
    instructions: str
    output_template: str
    token_estimate: int = 0


@dataclass
class CompilerContext:
    """Context passed to workflow-specific compilers during compilation."""

    project_root: Path
    output_folder: Path
    project_knowledge: Path | None = None
    cwd: Path | None = None
    workflow_ir: WorkflowIR | None = None
    resolved_variables: dict[str, Any] = field(default_factory=dict)
    discovered_files: dict[str, list[Path]] = field(default_factory=dict)
    file_contents: dict[str, str] = field(default_factory=dict)
    per_file_contents: dict[str, str] = field(default_factory=dict)
    links_only: bool = False
    stable_prefix: bool = False
