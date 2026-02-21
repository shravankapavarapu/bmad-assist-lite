"""Epic-driven context filtering for create-story workflow.

Parses a ``### Context Requirements`` markdown table from the epic file
and filters ``context.file_contents`` so only the requested sections
are kept, reducing prompt size for single-story compilation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from bmad_assist_lite.compiler.types import CompilerContext

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^#{2,4}\s+Context\s+Requirements", re.IGNORECASE)


@dataclass(frozen=True)
class ContextRequirement:
    """A single row from the Context Requirements table.

    Attributes:
        document: Filename (e.g. ``"architecture.md"``).
        sections: Section header names to extract.
        directive: ``"full"``, ``"skip"``, or ``"sections"``.

    """

    document: str
    sections: list[str]
    directive: str  # "full", "skip", or "sections"


# ---------------------------------------------------------------------------
# Table parsing
# ---------------------------------------------------------------------------


def _find_column_indices(header_cells: list[str]) -> dict[str, int] | None:
    """Map expected column names to their indices from a header row."""
    mapping: dict[str, int] = {}
    for i, cell in enumerate(header_cells):
        normalized = cell.strip().lower()
        if "document" in normalized or "file" in normalized:
            mapping["document"] = i
        elif "section" in normalized:
            mapping["sections"] = i

    required = {"document", "sections"}
    if not required.issubset(mapping.keys()):
        missing = required - mapping.keys()
        logger.warning("Context Requirements table missing required columns: %s", missing)
        return None
    return mapping


def parse_context_requirements(epic_content: str) -> list[ContextRequirement] | None:
    """Parse a ``### Context Requirements`` table from epic markdown.

    Returns ``None`` if no table is found (backwards compatible — full load).
    """
    lines = epic_content.splitlines()
    heading_idx: int | None = None

    for i, line in enumerate(lines):
        if _HEADING_RE.match(line.strip()):
            heading_idx = i
            break

    if heading_idx is None:
        return None

    # Collect table lines after the heading
    table_lines: list[str] = []
    for line in lines[heading_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            if not table_lines:
                continue
            break
        if stripped.startswith("|"):
            table_lines.append(stripped)
        else:
            if table_lines:
                break
            continue

    if len(table_lines) < 3:
        return None

    # Parse header
    header_cells = [c.strip() for c in table_lines[0].strip().strip("|").split("|")]
    col_map = _find_column_indices(header_cells)
    if col_map is None:
        return None

    # Skip separator row
    data_start = 1
    if re.match(r"\|[\s:|-]+\|", table_lines[data_start]):
        data_start = 2

    reqs: list[ContextRequirement] = []
    for line in table_lines[data_start:]:
        raw = line.strip().strip("|")
        cells = [c.strip() for c in raw.split("|")]

        max_idx = max(col_map.values())
        if len(cells) <= max_idx:
            logger.debug("Skipping short table row: %s", line)
            continue

        document = cells[col_map["document"]].strip("`").strip()
        sections_raw = cells[col_map["sections"]].strip("`").strip()

        if not document:
            continue

        sections_lower = sections_raw.lower()
        if sections_lower == "(skip)":
            directive = "skip"
            sections: list[str] = []
        elif sections_lower == "(full)" or not sections_raw:
            directive = "full"
            sections = []
        else:
            directive = "sections"
            sections = [s.strip().strip("`") for s in sections_raw.split(";") if s.strip()]

        reqs.append(
            ContextRequirement(document=document, sections=sections, directive=directive)
        )

    return reqs if reqs else None


# ---------------------------------------------------------------------------
# Filename-to-key mapping
# ---------------------------------------------------------------------------


def _build_filename_to_key_map(discovered_files: dict[str, list[Path]]) -> dict[str, str]:
    """Map lowercase filenames to their pattern keys.

    E.g. ``{"architecture.md": "architecture_file"}``.
    """
    mapping: dict[str, str] = {}
    for key, paths in discovered_files.items():
        for p in paths:
            # Extract just the filename from either PurePosixPath or PureWindowsPath
            if isinstance(p, (PurePosixPath, PureWindowsPath)):
                name = p.name.lower()
            else:
                name = str(p).rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
            mapping[name] = key
    return mapping


# ---------------------------------------------------------------------------
# Section extraction (in-memory variant)
# ---------------------------------------------------------------------------


def _extract_section_from_content(content: str, section_name: str) -> str | None:
    """Extract a section from markdown content by header match.

    Returns ``None`` instead of raising when the section is not found.
    Uses the same normalization logic as ``discovery.extract_section()``.
    """
    lines = content.split("\n")
    normalized_id = re.sub(r"[-._]", " ", section_name.lower()).strip()

    start_idx: int | None = None
    start_level = 0

    for i, line in enumerate(lines):
        if not line.startswith("#"):
            continue
        match = re.match(r"^(#+)", line)
        if not match:
            continue

        level = len(match.group(1))
        header_text = line.lstrip("#").strip().lower()
        normalized_header = re.sub(r"[-._]", " ", header_text)

        # Try word-boundary match first, fall back to substring containment
        # for headers with special chars like parentheses
        pattern = r"\b" + re.escape(normalized_id) + r"\b"
        if re.search(pattern, normalized_header) or normalized_id in normalized_header:
            start_idx = i
            start_level = level
            break

    if start_idx is None:
        return None

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        if line.startswith("#"):
            match = re.match(r"^(#+)", line)
            if match and len(match.group(1)) <= start_level:
                end_idx = i
                break

    return "\n".join(lines[start_idx:end_idx])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def apply_context_filter(context: CompilerContext) -> None:
    """Filter ``context.file_contents`` based on the epic's Context Requirements table.

    No-op if the epic file is not loaded or no table is found.
    """
    # Find the epic content in file_contents
    epic_content: str | None = None
    for key, content in context.file_contents.items():
        if "epic" in key.lower() and content:
            epic_content = content
            break

    if not epic_content:
        return

    reqs = parse_context_requirements(epic_content)
    if reqs is None:
        return

    filename_to_key = _build_filename_to_key_map(context.discovered_files)

    for req in reqs:
        doc_lower = req.document.lower()
        content_key = filename_to_key.get(doc_lower)
        if content_key is None:
            logger.warning(
                "Context Requirements: document '%s' not found in discovered files",
                req.document,
            )
            continue

        if content_key not in context.file_contents:
            logger.warning(
                "Context Requirements: key '%s' for document '%s' has no loaded content",
                content_key,
                req.document,
            )
            continue

        if req.directive == "skip":
            context.file_contents[content_key] = ""
        elif req.directive == "full":
            pass  # no change
        elif req.directive == "sections":
            original = context.file_contents[content_key]
            extracted_parts: list[str] = []
            for section_name in req.sections:
                section = _extract_section_from_content(original, section_name)
                if section is None:
                    logger.warning(
                        "Context Requirements: section '%s' not found in '%s'",
                        section_name,
                        req.document,
                    )
                    continue
                extracted_parts.append(section)
            if extracted_parts:
                context.file_contents[content_key] = "\n\n".join(extracted_parts)
            else:
                logger.warning(
                    "Context Requirements: no sections matched for '%s', keeping full content",
                    req.document,
                )
