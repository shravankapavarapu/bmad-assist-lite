"""XML output generation module for BMAD workflow compiler."""

import hashlib
import json
import logging
import re as _re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bmad_assist_lite.compiler.types import CompiledWorkflow
from bmad_assist_lite.core.exceptions import CompilerError, TokenBudgetError

logger = logging.getLogger(__name__)

LARGE_OUTPUT_THRESHOLD: int = 100 * 1024
CHARS_PER_TOKEN_ESTIMATE: int = 4
DEFAULT_SOFT_LIMIT_TOKENS: int = 15_000
DEFAULT_HARD_LIMIT_TOKENS: int = 20_000
SOFT_LIMIT_RATIO: float = 0.75

__all__ = [
    "generate_output",
    "GeneratedOutput",
    "validate_token_budget",
    "DEFAULT_SOFT_LIMIT_TOKENS",
    "DEFAULT_HARD_LIMIT_TOKENS",
    "SOFT_LIMIT_RATIO",
]


def _escape_xml_attr(value: str) -> str:
    """Escape string for use in XML attribute value."""
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _escape_xml_text(value: str) -> str:
    """Escape string for use as XML text content."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap_cdata(content: str) -> str:
    """Wrap content in CDATA section."""
    if not content:
        return ""
    if "]]>" in content:
        parts = content.split("]]>")
        return "<![CDATA[" + "]]]]><![CDATA[>".join(parts) + "]]>"
    return f"<![CDATA[{content}]]>"


@dataclass(frozen=True)
class GeneratedOutput:
    """Final generated XML output with metadata."""

    xml: str
    token_estimate: int
    size_bytes: int


FILE_ORDER_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("project_context", "project-context"),
    ("prd",),
    ("ux",),
    ("architecture",),
    ("library-docs",),
    ("epics", "epic-"),
)


def _serialize_value(value: Any) -> str:
    """Serialize value for XML variable element."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as e:
        raise CompilerError(
            f"Variable has non-JSON-serializable type: {type(value).__name__}"
        ) from e


def _get_file_order_key(path: str) -> tuple[int, str]:
    """Get ordering key for a file path (recency-bias ordering)."""
    path_lower = path.lower()

    if "sprint-artifacts" in path_lower and _re.search(r"/\d+-\d+-[^/]+\.md$", path_lower):
        return (len(FILE_ORDER_PATTERNS) + 1, path)

    for idx, patterns in enumerate(FILE_ORDER_PATTERNS):
        for pattern in patterns:
            if pattern in path_lower:
                return (idx, path)
    return (len(FILE_ORDER_PATTERNS), path)


def _normalize_path(path: Path) -> str:
    """Normalize path to absolute path string with forward slashes."""
    return str(path.resolve()).replace("\\", "/")


def _generate_file_id(path_str: str) -> str:
    """Generate deterministic short ID from file path."""
    return hashlib.sha256(path_str.encode("utf-8")).hexdigest()[:8]


def _build_context_section(
    context_files: dict[str, str],
    project_root: Path,
    links_only: bool = False,
) -> tuple[str, dict[str, str]]:
    """Build the <context> section with ordered files and IDs."""
    file_elements: list[str] = []
    path_to_id: dict[str, str] = {}

    sorted_paths = sorted(context_files.keys(), key=_get_file_order_key)

    for path_str in sorted_paths:
        if not path_str:
            continue

        content = context_files[path_str]
        if not content:
            continue

        path = Path(path_str)
        abs_path = _normalize_path(path)
        file_id = _generate_file_id(abs_path)
        path_to_id[abs_path] = file_id

        if links_only:
            try:
                token_approx = path.stat().st_size // 4
            except (OSError, FileNotFoundError):
                token_approx = len(content) // 4
            file_elements.append(
                f'<file id="{file_id}" path="{_escape_xml_attr(abs_path)}" '
                f'token_approx="{token_approx}" />'
            )
        else:
            file_elements.append(
                f'<file id="{file_id}" path="{_escape_xml_attr(abs_path)}">'
                f"{_wrap_cdata(content)}</file>"
            )

    xml = "<context>\n" + "\n".join(file_elements) + "\n</context>"
    return xml, path_to_id


def _build_variables_section(
    variables: dict[str, Any],
    path_to_id: dict[str, str] | None = None,
) -> str:
    """Build the <variables> section with sorted variables."""
    var_elements: list[str] = []

    for name in sorted(variables.keys()):
        if not name or name.startswith("(") or name.endswith(")"):
            continue
        value = variables[name]
        serialized = _serialize_value(value)
        var_elements.append(
            f'<var name="{_escape_xml_attr(name)}">{_escape_xml_text(serialized)}</var>'
        )

    return "<variables>\n" + "\n".join(var_elements) + "\n</variables>"


def _build_file_index_section(path_to_id: dict[str, str]) -> str:
    """Build the <file-index> section."""
    if not path_to_id:
        return "<file-index />"

    entries: list[str] = []
    for path, file_id in sorted(path_to_id.items()):
        entries.append(f'<entry id="{file_id}" path="{_escape_xml_attr(path)}" />')

    return "<file-index>\n" + "\n".join(entries) + "\n</file-index>"


def generate_output(
    compiled: CompiledWorkflow,
    project_root: Path | None = None,
    context_files: dict[str, str] | None = None,
    links_only: bool = False,
) -> GeneratedOutput:
    """Generate XML output from compiled workflow."""
    if project_root is None:
        project_root = Path.cwd()

    parts: list[str] = []
    path_to_id: dict[str, str] = {}

    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append("<compiled-workflow>")
    parts.append(f"<mission>{_wrap_cdata(compiled.mission)}</mission>")

    if context_files is not None:
        context_xml, path_to_id = _build_context_section(context_files, project_root, links_only)
        parts.append(context_xml)
    else:
        parts.append(f"<context>{_wrap_cdata(compiled.context)}</context>")

    parts.append(_build_variables_section(compiled.variables, path_to_id))
    parts.append(_build_file_index_section(path_to_id))

    if compiled.instructions:
        stripped = compiled.instructions.strip()
        while stripped.startswith("<!--"):
            end = stripped.find("-->")
            if end == -1:
                break
            stripped = stripped[end + 3 :].strip()

        is_markdown = stripped.startswith("#") or not stripped.startswith("<")
        if is_markdown:
            parts.append(f"<instructions>{_wrap_cdata(compiled.instructions)}</instructions>")
        else:
            parts.append(f"<instructions>{compiled.instructions}</instructions>")
    else:
        parts.append("<instructions></instructions>")

    parts.append(f"<output-template>{_wrap_cdata(compiled.output_template)}</output-template>")
    parts.append("</compiled-workflow>")

    xml_str = "\n".join(parts)

    try:
        ET.fromstring(xml_str)
    except ET.ParseError as e:
        raise CompilerError(f"Generated XML is malformed: {e}") from e

    size_bytes = len(xml_str.encode("utf-8"))
    token_estimate = len(xml_str) // CHARS_PER_TOKEN_ESTIMATE

    return GeneratedOutput(xml=xml_str, token_estimate=token_estimate, size_bytes=size_bytes)


def validate_token_budget(
    token_estimate: int,
    hard_limit: int = DEFAULT_HARD_LIMIT_TOKENS,
) -> list[str]:
    """Validate token count against budget limits."""
    if hard_limit == 0:
        return []

    warnings: list[str] = []
    soft_limit = (
        DEFAULT_SOFT_LIMIT_TOKENS
        if hard_limit == DEFAULT_HARD_LIMIT_TOKENS
        else int(hard_limit * SOFT_LIMIT_RATIO)
    )

    if token_estimate > hard_limit:
        raise TokenBudgetError(
            f"Token budget exceeded: ~{token_estimate:,} tokens (limit: {hard_limit:,})"
        )

    if token_estimate > soft_limit:
        warnings.append(
            f"Compiled prompt nearing limit: ~{token_estimate:,} tokens (soft: {soft_limit:,})"
        )

    return warnings
