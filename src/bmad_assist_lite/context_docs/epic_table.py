"""Parser for Context7 Library Documentation tables in epic markdown files."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Heading pattern: ## to #### with "Context7 Library Documentation" (case-insensitive)
_HEADING_RE = re.compile(r"^#{2,4}\s+Context7\s+Library\s+Documentation", re.IGNORECASE)


@dataclass(frozen=True)
class EpicLibrarySpec:
    """A library specification parsed from an epic's Context7 table.

    Attributes:
        name: Human-readable library name (e.g. "Vitest").
        context7_id: Context7 library ID (e.g. "/vitest-dev/vitest").
        query_focus: Targeted query for Context7 docs fetch.
        stories: Story numbers within the epic that need this library.

    """

    name: str
    context7_id: str
    query_focus: str
    stories: list[str]


def _extract_story_number(entry: str) -> str:
    """Extract the story number from a story reference.

    Handles formats like "6-1" -> "1", "6.1" -> "1", bare "1" -> "1".
    Strips the epic prefix since the epic context is already known at injection time.
    """
    entry = entry.strip()
    # "6-1" or "6.1" format: take the part after the separator
    for sep in ("-", "."):
        if sep in entry:
            return entry.rsplit(sep, 1)[-1].strip()
    return entry


def _find_column_indices(header_cells: list[str]) -> dict[str, int] | None:
    """Map expected column names to their indices from a header row.

    Returns None if required columns are missing.
    """
    mapping: dict[str, int] = {}
    for i, cell in enumerate(header_cells):
        normalized = cell.strip().lower()
        if "context7" in normalized and "id" in normalized:
            mapping["context7_id"] = i
        elif "query" in normalized or "focus" in normalized:
            mapping["query_focus"] = i
        elif "stories" in normalized or "story" in normalized:
            mapping["stories"] = i
        elif "library" in normalized or "name" in normalized:
            mapping["name"] = i

    required = {"name", "context7_id", "query_focus", "stories"}
    if not required.issubset(mapping.keys()):
        missing = required - mapping.keys()
        logger.warning("Context7 table missing required columns: %s", missing)
        return None
    return mapping


def parse_context7_table(epic_content: str) -> list[EpicLibrarySpec] | None:
    """Parse a Context7 Library Documentation table from epic markdown.

    Looks for a heading matching ``## Context7 Library Documentation``
    (case-insensitive, h2-h4) followed by a markdown table with columns:
    Library Name, Context7 ID, Query Focus, Stories.

    Returns:
        List of EpicLibrarySpec if a valid table is found, None otherwise.

    """
    lines = epic_content.splitlines()
    heading_idx: int | None = None

    # Find the heading
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
            # Skip blank lines between heading and table
            if not table_lines:
                continue
            break
        if stripped.startswith("|"):
            table_lines.append(stripped)
        else:
            # Non-table, non-blank line after heading → stop
            if table_lines:
                break
            # Text between heading and table (description paragraph) — skip
            continue

    if len(table_lines) < 3:
        # Need at least header + separator + 1 data row
        return None

    # Parse header row
    header_cells = [c.strip() for c in table_lines[0].split("|")]
    # Remove empty strings from leading/trailing pipes
    header_cells = [c for c in header_cells if c]

    col_map = _find_column_indices(header_cells)
    if col_map is None:
        return None

    # Skip separator row (line with |---|)
    data_start = 1
    if re.match(r"\|[\s:|-]+\|", table_lines[data_start]):
        data_start = 2

    # Parse data rows
    specs: list[EpicLibrarySpec] = []
    for line in table_lines[data_start:]:
        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c or c == ""]
        # Filter out truly empty leading/trailing from pipe splitting
        # Re-parse: split by | and take inner cells
        raw = line.strip().strip("|")
        cells = [c.strip() for c in raw.split("|")]

        max_idx = max(col_map.values())
        if len(cells) <= max_idx:
            logger.debug("Skipping short table row: %s", line)
            continue

        name = cells[col_map["name"]]
        context7_id = cells[col_map["context7_id"]]
        query_focus = cells[col_map["query_focus"]]
        stories_raw = cells[col_map["stories"]]

        if not name or not context7_id:
            continue

        stories = [
            _extract_story_number(s) for s in stories_raw.split(",") if s.strip()
        ]

        specs.append(
            EpicLibrarySpec(
                name=name,
                context7_id=context7_id,
                query_focus=query_focus,
                stories=stories,
            )
        )

    return specs if specs else None


def get_story_lib_mapping(
    specs: list[EpicLibrarySpec],
) -> dict[str, list[str]]:
    """Build a story_num -> [lib_names] mapping from a list of EpicLibrarySpec.

    Returns:
        Dict mapping story number strings to lists of library names.

    """
    mapping: dict[str, list[str]] = {}
    for spec in specs:
        for story_num in spec.stories:
            mapping.setdefault(story_num, []).append(spec.name)
    return mapping
