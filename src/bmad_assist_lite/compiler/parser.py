"""BMAD workflow file parsing.

Parses workflow.yaml, instructions.xml, and workflow.md frontmatter.
All parsing is STRUCTURAL only - variable resolution handled separately.
"""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

from bmad_assist_lite.compiler.types import WorkflowIR
from bmad_assist_lite.core.exceptions import ParserError

logger = logging.getLogger(__name__)


def parse_workflow_md_frontmatter(workflow_md_path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from workflow.md file."""
    if not workflow_md_path.exists():
        return {}

    try:
        content = workflow_md_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot read workflow.md: %s", e)
        return {}

    if not content.startswith("---"):
        return {}

    end_marker_pos = content.find("---", 3)
    if end_marker_pos == -1:
        return {}

    frontmatter = content[3:end_marker_pos].strip()
    if not frontmatter:
        return {}

    try:
        result = yaml.safe_load(frontmatter)
        if result is None:
            return {}
        if not isinstance(result, dict):
            logger.warning("workflow.md frontmatter is not a dict, ignoring")
            return {}
        return result
    except yaml.YAMLError as e:
        logger.warning("Invalid YAML in workflow.md frontmatter: %s", e)
        return {}


def parse_workflow_config(config_path: Path) -> dict[str, Any]:
    """Parse workflow.yaml configuration file."""
    if not config_path.exists():
        raise ParserError(f"Configuration file not found: {config_path}")

    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ParserError(f"Cannot read configuration file: {config_path}\n  Error: {e}") from e

    if not content.strip():
        return {}

    try:
        result = yaml.safe_load(content)
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise ParserError(
                f"Invalid configuration in {config_path}: "
                f"root must be a mapping, got {type(result).__name__}"
            )
        return result
    except yaml.YAMLError as e:
        line_info = ""
        if hasattr(e, "problem_mark") and e.problem_mark is not None:
            mark = e.problem_mark
            line_info = f"\n  Line {mark.line + 1}, column {mark.column + 1}"
        raise ParserError(f"Invalid YAML in {config_path}:{line_info}\n  {e}") from e


def parse_workflow_instructions(instructions_path: Path) -> str:
    """Parse and validate instructions.xml file."""
    if not instructions_path.exists():
        raise ParserError(f"Instructions file not found: {instructions_path}")

    try:
        content = instructions_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ParserError(
            f"Cannot read instructions file: {instructions_path}\n  Error: {e}"
        ) from e

    # For .md files, skip XML validation
    if instructions_path.suffix.lower() == ".md":
        return content

    # Security: Reject XML with DOCTYPE/ENTITY declarations
    if "<!DOCTYPE" in content or "<!ENTITY" in content:
        raise ParserError(
            f"Invalid XML in {instructions_path}: "
            "DOCTYPE and ENTITY declarations are not allowed"
        )

    try:
        ET.fromstring(content)
    except ET.ParseError as e:
        line_info = ""
        if hasattr(e, "position") and e.position is not None:
            line, col = e.position
            line_info = f"\n  Line {line}, column {col}"
        raise ParserError(f"Invalid XML in {instructions_path}:{line_info}\n  {e}") from e

    return content


def parse_workflow(workflow_dir: Path) -> WorkflowIR:
    """Parse BMAD workflow directory into WorkflowIR."""
    workflow_dir = workflow_dir.resolve()

    yaml_path = workflow_dir / "workflow.yaml"
    md_path = workflow_dir / "workflow.md"

    yaml_exists = yaml_path.exists()
    md_exists = md_path.exists()

    if not yaml_exists and not md_exists:
        raise ParserError(f"workflow.yaml not found in: {workflow_dir}")

    raw_config: dict[str, Any] = {}

    if md_exists:
        md_config = parse_workflow_md_frontmatter(md_path)
        raw_config.update(md_config)

    if yaml_exists:
        yaml_config = parse_workflow_config(yaml_path)
        raw_config.update(yaml_config)

    config_path = yaml_path if yaml_exists else md_path

    # Determine instructions path
    instructions_key = raw_config.get("instructions", "")
    if isinstance(instructions_key, str) and "{" in instructions_key:
        instructions_path = workflow_dir / "instructions.xml"
        if not instructions_path.exists():
            instructions_path = workflow_dir / "instructions.md"
    elif isinstance(instructions_key, str) and instructions_key:
        if ".." in instructions_key or instructions_key.startswith("/"):
            raise ParserError(
                f"Invalid instructions path in {config_path}: "
                f"path '{instructions_key}' contains path traversal"
            )
        instructions_path = workflow_dir / instructions_key
    else:
        instructions_path = workflow_dir / "instructions.xml"
        if not instructions_path.exists():
            instructions_path = workflow_dir / "instructions.md"

    raw_instructions = ""
    if instructions_path.exists():
        raw_instructions = parse_workflow_instructions(instructions_path)
    else:
        raise ParserError(f"instructions file not found: {instructions_path}")

    # Extract template path
    template_value = raw_config.get("template")
    if template_value is False:
        template_path: str | None = None
    elif isinstance(template_value, str):
        template_path = template_value
    else:
        template_path = None

    # Extract validation/checklist path
    validation_value = raw_config.get("validation")
    if validation_value is False:
        validation_path: str | None = None
    elif isinstance(validation_value, str):
        validation_path = validation_value
    else:
        validation_path = None

    name = raw_config.get("name")
    if not name or not isinstance(name, str):
        name = workflow_dir.name

    return WorkflowIR(
        name=name,
        config_path=config_path.resolve(),
        instructions_path=instructions_path.resolve(),
        template_path=template_path,
        validation_path=validation_path,
        raw_config=raw_config,
        raw_instructions=raw_instructions,
    )
