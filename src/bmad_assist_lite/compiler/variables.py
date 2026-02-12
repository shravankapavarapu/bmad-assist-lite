"""Variable resolution for BMAD workflow compiler.

Simplified from bmad-assist's multi-module variable system into a single file.
Handles: path placeholders, config_source loading, recursive resolution,
story variables, and input_file_patterns.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from bmad_assist_lite.compiler.types import CompilerContext, WorkflowIR
from bmad_assist_lite.core.exceptions import VariableError

logger = logging.getLogger(__name__)

MAX_RECURSION_DEPTH = 10

_SINGLE_BRACE_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_-]*)\}")
_DOUBLE_BRACE_PATTERN = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_-]*)\}\}")
_CONFIG_SOURCE_PATTERN = re.compile(r"\{config_source\}:([a-zA-Z_][a-zA-Z0-9_-]*)")

_SYSTEM_VARIABLES = frozenset({"project-root", "installed_path", "config_source"})

__all__ = ["resolve_variables"]


def _resolve_path_placeholders(
    value: str, context: CompilerContext, workflow_ir: WorkflowIR
) -> str:
    """Resolve path placeholders like {project-root} and {installed_path}."""
    result = value

    # {project-root} -> project root path
    if "{project-root}" in result:
        result = result.replace("{project-root}", str(context.project_root))

    # {installed_path} -> workflow directory (parent of workflow.yaml)
    if "{installed_path}" in result:
        installed = str(workflow_ir.config_path.parent)
        result = result.replace("{installed_path}", installed)

    # {output_folder} -> output folder path
    if "{output_folder}" in result:
        result = result.replace("{output_folder}", str(context.output_folder))

    return result


def _load_external_config(config_path: Path) -> dict[str, Any]:
    """Load external YAML config file."""
    try:
        content = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if data is None:
            return {}
        if not isinstance(data, dict):
            logger.warning("Config source is not a dict: %s", config_path)
            return {}
        return data
    except (yaml.YAMLError, OSError) as e:
        raise VariableError(
            f"Cannot load config source: {config_path}\n  Error: {e}",
            variable_name="config_source",
        ) from e


def _resolve_recursive(
    value: str,
    resolved: dict[str, Any],
    visiting: set[str],
    depth: int,
    current_key: str,
    context: CompilerContext,
    workflow_ir: WorkflowIR,
) -> str:
    """Recursively resolve a single string value."""
    if depth > MAX_RECURSION_DEPTH:
        raise VariableError(
            f"Cannot resolve '{current_key}': max recursion depth ({MAX_RECURSION_DEPTH}) exceeded",
            variable_name=current_key,
        )

    result = value

    for pattern in [_DOUBLE_BRACE_PATTERN, _SINGLE_BRACE_PATTERN]:
        prev_result = None
        while prev_result != result:
            prev_result = result
            for match in pattern.finditer(result):
                var_name = match.group(1)
                full_match = match.group(0)

                if var_name in _SYSTEM_VARIABLES:
                    continue

                if var_name in visiting:
                    cycle_path = " -> ".join(visiting) + f" -> {var_name}"
                    raise VariableError(
                        f"Circular reference detected: {cycle_path}",
                        variable_name=var_name,
                    )

                if var_name in resolved:
                    var_value = resolved[var_name]
                    if not isinstance(var_value, str):
                        var_value = str(var_value)

                    visiting.add(var_name)
                    resolved_value = _resolve_recursive(
                        var_value,
                        resolved,
                        visiting,
                        depth + 1,
                        var_name,
                        context,
                        workflow_ir,
                    )
                    visiting.discard(var_name)

                    result = result.replace(full_match, resolved_value, 1)
                    break

    result = _resolve_path_placeholders(result, context, workflow_ir)
    return result


def _resolve_all_recursive(
    resolved: dict[str, Any],
    context: CompilerContext,
    workflow_ir: WorkflowIR,
) -> dict[str, Any]:
    """Recursively resolve all remaining variable placeholders."""
    result: dict[str, Any] = {}
    for key, value in resolved.items():
        if isinstance(value, str):
            result[key] = _resolve_recursive(value, resolved, set(), 0, key, context, workflow_ir)
        else:
            result[key] = value
    return result


def _compute_story_variables(
    epic_num: int,
    story_num: int,
    story_title: str | None = None,
    date_override: str | None = None,
) -> dict[str, Any]:
    """Compute story-specific variables."""
    now = datetime.now(timezone.utc)
    date_str = date_override or now.strftime("%Y-%m-%d")

    return {
        "epic_num": epic_num,
        "story_num": story_num,
        "story_id": f"{epic_num}.{story_num}",
        "story_key": f"{epic_num}-{story_num}",
        "story_title": story_title or f"Story {epic_num}.{story_num}",
        "date": date_str,
        "timestamp": now.isoformat(),
    }


def resolve_variables(
    context: CompilerContext,
    invocation_params: dict[str, Any],
) -> dict[str, Any]:
    """Resolve all variables in workflow configuration.

    Resolution order (priority low to high):
    1. config_source values (external config)
    2. workflow.yaml raw_config values
    3. invocation_params (CLI arguments)
    4. Computed story variables
    5. Recursive resolution of placeholders
    6. Flattened 'variables' dict
    7. Hard overrides
    """
    if context.workflow_ir is None:
        raise VariableError(
            "Cannot resolve variables: workflow_ir not set in context",
        )

    workflow_ir = context.workflow_ir
    raw_config = workflow_ir.raw_config.copy()
    resolved: dict[str, Any] = {}

    # Step 1: Load config_source (with path boundary check)
    if "config_source" in raw_config:
        config_source_raw = raw_config["config_source"]
        if isinstance(config_source_raw, str):
            config_source_path = _resolve_path_placeholders(config_source_raw, context, workflow_ir)
            config_path = Path(config_source_path).resolve()
            project_root_resolved = context.project_root.resolve()
            if not config_path.is_relative_to(project_root_resolved):
                logger.warning(
                    "config_source '%s' is outside project root, skipping",
                    config_source_path,
                )
            elif config_path.exists():
                external_config = _load_external_config(config_path)
                for key, value in external_config.items():
                    if isinstance(value, str):
                        value = _resolve_path_placeholders(value, context, workflow_ir)
                    resolved[key] = value

    # Step 2: Process workflow.yaml raw_config values
    for key, value in raw_config.items():
        if key == "config_source":
            continue
        if not isinstance(value, str):
            resolved[key] = value
            continue
        if _CONFIG_SOURCE_PATTERN.match(value):
            continue
        resolved[key] = _resolve_path_placeholders(value, context, workflow_ir)

    # Step 3: Apply invocation params
    for key, value in invocation_params.items():
        resolved[key] = value

    # Step 4: Compute story variables
    epic_num = resolved.get("epic_num")
    story_num = resolved.get("story_num")
    if epic_num is not None and story_num is not None:
        date_val = resolved.get("date")
        date_override = None
        if isinstance(date_val, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", date_val):
            date_override = date_val

        story_vars = _compute_story_variables(
            int(epic_num),
            int(story_num),
            resolved.get("story_title"),
            date_override,
        )
        for k, v in story_vars.items():
            if k in ("date", "timestamp"):
                resolved[k] = v
            elif k not in resolved:
                resolved[k] = v

    # Step 5: Recursive resolution
    resolved = _resolve_all_recursive(resolved, context, workflow_ir)

    # Step 6: Flatten 'variables' dict
    for key, value in list(resolved.items()):
        if isinstance(value, dict) and key == "variables":
            for var_name, var_value in value.items():
                if var_name not in resolved:
                    resolved[var_name] = var_value
            del resolved[key]

    # Step 7: Hard overrides
    resolved["user_skill_level"] = "expert"
    resolved["communication_language"] = "English"

    # Remove internal variables
    for key in ["standalone"]:
        resolved.pop(key, None)

    context.resolved_variables = resolved
    return resolved
