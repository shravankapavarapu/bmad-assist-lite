"""Epic-driven context filtering for create-story workflow.

Parses a ``### Context Requirements`` markdown table from the epic file
and filters ``context.file_contents`` so only the requested sections
are kept, reducing prompt size for single-story compilation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from bmad_assist_lite.compiler.types import CompilerContext
from bmad_assist_lite.core.exceptions import CompilerError

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^#{2,4}\s+Context\s+Requirements", re.IGNORECASE)
_OPTIONAL_RE = re.compile(r"\s*\(optional\)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class ContextRequirement:
    """A single row from the Context Requirements table.

    Attributes:
        document: Filename (e.g. ``"architecture.md"``).
        sections: Section header names to extract.
        directive: ``"full"``, ``"skip"``, or ``"sections"``.
        optional: Whether the entire document reference is optional.
        optional_sections: Indices (into ``sections``) that are optional.

    """

    document: str
    sections: list[str]
    directive: str  # "full", "skip", or "sections"
    optional: bool = False
    optional_sections: frozenset[int] = field(default_factory=frozenset)


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

        # Strip (optional) from the raw cell to isolate the directive keyword.
        # "(full) (optional)" → "(full)", "(skip) (optional)" → "(skip)"
        directive_val = _OPTIONAL_RE.sub("", sections_raw).strip().strip("`").strip()

        directive_lower = directive_val.lower()
        if directive_lower == "(skip)":
            directive = "skip"
            sections: list[str] = []
            optional_indices: frozenset[int] = frozenset()
            # Document-level optional: (optional) in a (skip)/(full) directive row
            doc_optional = bool(_OPTIONAL_RE.search(sections_raw))
        elif directive_lower == "(full)" or not directive_val:
            directive = "full"
            sections = []
            optional_indices = frozenset()
            doc_optional = bool(_OPTIONAL_RE.search(sections_raw))
        else:
            directive = "sections"
            # Split by semicolon, then detect per-section (optional)
            raw_parts = [s.strip() for s in sections_raw.split(";") if s.strip()]
            sections = []
            opt_idx: set[int] = set()
            for part in raw_parts:
                # Detect (optional) before stripping backticks
                if _OPTIONAL_RE.search(part):
                    cleaned = _OPTIONAL_RE.sub("", part).strip().strip("`").strip()
                    if cleaned:
                        opt_idx.add(len(sections))
                        sections.append(cleaned)
                else:
                    cleaned = part.strip("`").strip()
                    if cleaned:
                        sections.append(cleaned)
            optional_indices = frozenset(opt_idx)
            # Document-level optional only if ALL sections are optional
            doc_optional = bool(
                optional_indices and len(optional_indices) == len(sections)
            )

        reqs.append(
            ContextRequirement(
                document=document,
                sections=sections,
                directive=directive,
                optional=doc_optional,
                optional_sections=optional_indices,
            )
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
# Epic story-level filtering
# ---------------------------------------------------------------------------

_STORY_HEADING_RE = re.compile(r"^###\s+Story\s+", re.IGNORECASE)


def filter_epic_to_story(context: CompilerContext) -> None:
    """Filter epic content to only the overview + target story section.

    Keeps everything before the first ``### Story`` header (epic overview,
    business goal, dependencies, context tables) plus the single story
    section matching ``epic_num.story_num``.  Other stories and epic-level
    closing sections (Test Impact, Risk, Rollback) are removed.

    No-op if epic content or story_num is missing.
    """
    # Find epic content key
    epic_key: str | None = None
    epic_content: str | None = None
    for key, content in context.file_contents.items():
        if "epic" in key.lower() and content:
            epic_key = key
            epic_content = content
            break

    if not epic_content or not epic_key:
        return

    epic_num = context.resolved_variables.get("epic_num")
    story_num = context.resolved_variables.get("story_num")
    if not epic_num or not story_num:
        return

    lines = epic_content.split("\n")

    # 1. Find the first "### Story" header — everything before is the overview
    first_story_idx: int | None = None
    for i, line in enumerate(lines):
        if _STORY_HEADING_RE.match(line):
            first_story_idx = i
            break

    if first_story_idx is None:
        logger.debug("No ### Story headers found in epic, keeping full content")
        return

    overview = "\n".join(lines[:first_story_idx]).rstrip()

    # 2. Extract the target story section using existing helper
    section_name = f"Story {epic_num}.{story_num}"
    target_story = _extract_section_from_content(epic_content, section_name)

    if target_story is None:
        logger.warning(
            "Could not extract '%s' from epic, keeping full content",
            section_name,
        )
        return

    # 3. Combine overview + target story
    filtered = overview + "\n\n" + target_story.rstrip() + "\n"

    original_len = len(epic_content)
    filtered_len = len(filtered)
    reduction = (1 - filtered_len / original_len) * 100 if original_len else 0
    logger.info(
        "Filtered epic to %s: %d -> %d chars (%.0f%% reduction)",
        section_name,
        original_len,
        filtered_len,
        reduction,
    )

    context.file_contents[epic_key] = filtered


# ---------------------------------------------------------------------------
# Story file: strip appended synthesis reports
# ---------------------------------------------------------------------------

_SYNTHESIS_HEADING_RE = re.compile(
    r"^#\s+(Code Review Synthesis|Validation Synthesis)\b", re.IGNORECASE
)

_QUALITY_GATES_HEADING_RE = re.compile(r"^##\s+Quality\s+Gates\b", re.IGNORECASE)


def _remove_section(lines: list[str], heading_re: re.Pattern[str]) -> list[str]:
    """Remove a markdown section (heading + body) from lines.

    Finds the first line matching *heading_re*, determines its heading
    level, and removes everything up to (but not including) the next
    heading at the same or higher level.
    """
    start_idx: int | None = None
    start_level = 0

    for i, line in enumerate(lines):
        if heading_re.match(line):
            match = re.match(r"^(#+)", line)
            start_idx = i
            start_level = len(match.group(1)) if match else 2
            break

    if start_idx is None:
        return lines

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if lines[i].startswith("#"):
            match = re.match(r"^(#+)", lines[i])
            if match and len(match.group(1)) <= start_level:
                end_idx = i
                break

    return lines[:start_idx] + lines[end_idx:]


def strip_synthesis_reports(context: CompilerContext) -> None:
    """Remove noise sections from the story file for LLM prompts.

    Strips:
    - ``# Code Review Synthesis`` / ``# Validation Synthesis`` — audit
      trails whose findings have already been applied to the story.
    - ``## Quality Gates`` — handled by the dedicated quality_gate phase;
      showing them to LLMs causes them to treat gates as blockers.

    No-op if no ``story_file`` key exists in ``file_contents``.
    """
    story_key = "story_file"
    content = context.file_contents.get(story_key)
    if not content:
        return

    original_len = len(content)

    # 1. Cut appended synthesis reports (everything from first match onward)
    lines = content.split("\n")
    cut_idx: int | None = None
    for i, line in enumerate(lines):
        if _SYNTHESIS_HEADING_RE.match(line):
            cut_idx = i
            break

    if cut_idx is not None:
        lines = lines[:cut_idx]

    # 2. Remove ## Quality Gates section (mid-file)
    lines = _remove_section(lines, _QUALITY_GATES_HEADING_RE)

    trimmed = "\n".join(lines).rstrip() + "\n"
    trimmed_len = len(trimmed)
    reduction = (1 - trimmed_len / original_len) * 100 if original_len else 0
    if trimmed_len < original_len:
        logger.info(
            "Stripped story_file noise sections: %d -> %d chars (%.0f%% reduction)",
            original_len,
            trimmed_len,
            reduction,
        )
        context.file_contents[story_key] = trimmed


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def apply_context_filter(context: CompilerContext) -> None:
    """Filter ``context.file_contents`` based on the epic's Context Requirements table.

    No-op if the epic file is not loaded or no table is found.

    Raises:
        CompilerError: If any referenced documents are missing from discovered
            files or referenced sections are not found in loaded documents.
            All missing references are collected and reported in a single error.

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

    missing_docs: list[str] = []
    missing_sections: dict[str, list[str]] = {}

    for req in reqs:
        doc_lower = req.document.lower()
        content_key = filename_to_key.get(doc_lower)

        # Document not discovered or not loaded — handle missing doc
        if content_key is None or content_key not in context.file_contents:
            if req.directive != "skip":
                if req.optional:
                    logger.warning(
                        "Optional document '%s' not found, skipping",
                        req.document,
                    )
                else:
                    missing_docs.append(req.document)
            continue

        if req.directive == "skip":
            context.file_contents[content_key] = ""
        elif req.directive == "full":
            pass  # no change
        elif req.directive == "sections":
            original = context.file_contents[content_key]
            extracted_parts: list[str] = []
            for idx, section_name in enumerate(req.sections):
                section = _extract_section_from_content(original, section_name)
                if section is None:
                    if idx in req.optional_sections:
                        logger.warning(
                            "Optional section '%s' in '%s' not found, skipping",
                            section_name,
                            req.document,
                        )
                    else:
                        missing_sections.setdefault(req.document, []).append(
                            section_name
                        )
                    continue
                extracted_parts.append(section)
            if extracted_parts:
                context.file_contents[content_key] = "\n\n".join(extracted_parts)

    if missing_docs or missing_sections:
        if missing_sections and missing_docs:
            header = "Context Requirements have unresolved references:"
        elif missing_sections:
            header = "Context Requirements reference missing sections:"
        else:
            header = "Context Requirements reference missing documents:"
        parts: list[str] = [header]
        for doc, sections in missing_sections.items():
            parts.append(f"\n  {doc}:")
            for s in sections:
                parts.append(f"    - {s}")
        if missing_docs:
            parts.append("\n  Discovered files missing:")
            for doc in missing_docs:
                parts.append(f"    - {doc}")
        fix_lines = []
        if missing_sections:
            fix_lines.append(
                "Add missing sections to the referenced documents"
            )
        if missing_docs:
            fix_lines.append(
                "Ensure missing documents exist in the project"
            )
        fix_lines.append(
            "or mark non-critical references as (optional)"
            " in the epic file"
        )
        parts.append(f"\nFix: {', '.join(fix_lines)}.")
        raise CompilerError("\n".join(parts))
