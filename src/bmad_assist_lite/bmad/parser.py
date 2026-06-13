"""Deterministic BMAD markdown parser.

Parses BMAD documentation files (epics, stories, PRD, architecture) from
markdown format. Uses manual YAML frontmatter parsing (no python-frontmatter
dependency).
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class BmadDocument:
    """Generic BMAD document with optional YAML frontmatter."""

    path: Path
    frontmatter: dict[str, Any] = field(default_factory=dict)
    content: str = ""


@dataclass
class EpicStory:
    """Represents a story within an epic."""

    number: str  # e.g., "1.2"
    title: str
    code: str = ""  # e.g., "STORY-1.2"
    estimate: str = ""
    status: str = ""
    priority: str = ""
    dependencies: list[str] = field(default_factory=list)
    ac_count: int = 0  # Number of acceptance criteria


@dataclass
class EpicDocument:
    """Represents a parsed epic file."""

    path: Path
    epic_number: int
    title: str = ""
    stories: list[EpicStory] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    content: str = ""


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from text.

    Returns (frontmatter_dict, remaining_content).
    """
    if not text.startswith("---"):
        return {}, text

    # Find closing ---
    end_pos = text.find("---", 3)
    if end_pos == -1:
        return {}, text

    frontmatter_text = text[3:end_pos].strip()
    remaining = text[end_pos + 3 :].strip()

    if not frontmatter_text:
        return {}, remaining

    try:
        data = yaml.safe_load(frontmatter_text)
        if data is None or not isinstance(data, dict):
            return {}, remaining
        return data, remaining
    except yaml.YAMLError:
        return {}, remaining


def parse_bmad_file(file_path: Path) -> BmadDocument:
    """Parse a BMAD markdown file with optional frontmatter.

    Args:
        file_path: Path to the markdown file.

    Returns:
        BmadDocument with parsed frontmatter and content.

    """
    text = file_path.read_text(encoding="utf-8")
    frontmatter, content = _parse_frontmatter(text)

    return BmadDocument(
        path=file_path,
        frontmatter=frontmatter,
        content=content,
    )


# Story header patterns (## or ### or deeper, matching bmad-assist)
# Standard: ## Story X.Y: Title  /  ### Story X.Y: Title
_STORY_HEADER_PATTERN = re.compile(
    r"^#{2,}\s+Story\s+(\d+)\.(\d+)\s*:\s*(.+)",
    re.MULTILINE,
)

# Fallback: ## X.Y Title  /  ### X.Y Title (no "Story" prefix)
_STORY_HEADER_FALLBACK = re.compile(
    r"^#{2,}\s+(\d+)\.(\d+)\s+(.+)",
    re.MULTILINE,
)

# Metadata patterns
_STATUS_PATTERN = re.compile(r"\*\*Status:\*\*\s*(.+)", re.IGNORECASE)
_PRIORITY_PATTERN = re.compile(r"\*\*Priority:\*\*\s*(.+)", re.IGNORECASE)
_ESTIMATE_PATTERN = re.compile(r"\*\*Estimate:\*\*\s*(.+)", re.IGNORECASE)
_DEPENDENCIES_PATTERN = re.compile(r"\*\*Dependencies:\*\*\s*(.+)", re.IGNORECASE)

# Acceptance criteria: count checkboxes
_AC_CHECKBOX_PATTERN = re.compile(r"^\s*-\s*\[[ x]\]", re.MULTILINE)


def _extract_story_metadata(section: str) -> dict[str, Any]:
    """Extract metadata from a story section."""
    metadata: dict[str, Any] = {}

    status_match = _STATUS_PATTERN.search(section)
    if status_match:
        metadata["status"] = status_match.group(1).strip()

    priority_match = _PRIORITY_PATTERN.search(section)
    if priority_match:
        metadata["priority"] = priority_match.group(1).strip()

    estimate_match = _ESTIMATE_PATTERN.search(section)
    if estimate_match:
        metadata["estimate"] = estimate_match.group(1).strip()

    deps_match = _DEPENDENCIES_PATTERN.search(section)
    if deps_match:
        deps_text = deps_match.group(1).strip()
        deps_text = deps_text.strip("[]")
        if deps_text:
            deps = [d.strip() for d in deps_text.split(",") if d.strip()]
            metadata["dependencies"] = deps

    # Count acceptance criteria checkboxes
    ac_matches = _AC_CHECKBOX_PATTERN.findall(section)
    metadata["ac_count"] = len(ac_matches)

    return metadata


def _split_sections(content: str) -> list[tuple[str, str]]:
    """Split content into (header, section_body) tuples at ##+ boundaries."""
    sections: list[tuple[str, str]] = []
    lines = content.split("\n")

    current_header = ""
    current_lines: list[str] = []

    for line in lines:
        if re.match(r"^#{2,}\s", line):
            if current_header or current_lines:
                sections.append((current_header, "\n".join(current_lines)))
            current_header = line
            current_lines = []
        else:
            current_lines.append(line)

    if current_header or current_lines:
        sections.append((current_header, "\n".join(current_lines)))

    return sections


def parse_epic_file(file_path: Path, epic_number: int | None = None) -> EpicDocument:
    """Parse an epic file to extract stories.

    Args:
        file_path: Path to the epic markdown file.
        epic_number: Epic number. If None, tries to extract from filename.

    Returns:
        EpicDocument with parsed stories.

    """
    text = file_path.read_text(encoding="utf-8")
    frontmatter, content = _parse_frontmatter(text)

    # Try to get epic number from filename if not provided
    if epic_number is None:
        # Try patterns like "epic-1.md", "epic_1.md", "1-epic.md"
        name = file_path.stem
        num_match = re.search(r"(\d+)", name)
        if num_match:
            epic_number = int(num_match.group(1))
        else:
            epic_number = 0

    # Extract title from first # heading
    title = ""
    title_match = re.match(r"^#\s+(.+)", content, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()

    # Parse stories
    stories: list[EpicStory] = []
    sections = _split_sections(content)

    for header, body in sections:
        # Try standard pattern first
        match = _STORY_HEADER_PATTERN.match(header)
        if not match:
            match = _STORY_HEADER_FALLBACK.match(header)

        if match:
            e_num = match.group(1)
            s_num = match.group(2)
            s_title = match.group(3).strip()

            story_num = f"{e_num}.{s_num}"

            # Extract metadata from section body
            metadata = _extract_story_metadata(body)

            story = EpicStory(
                number=story_num,
                title=s_title,
                code=f"STORY-{story_num}",
                estimate=metadata.get("estimate", ""),
                status=metadata.get("status", ""),
                priority=metadata.get("priority", ""),
                dependencies=metadata.get("dependencies", []),
                ac_count=metadata.get("ac_count", 0),
            )
            stories.append(story)

    if stories:
        logger.debug("Parsed %d stories from %s", len(stories), file_path.name)
    else:
        logger.debug("No stories found in %s", file_path.name)

    return EpicDocument(
        path=file_path,
        epic_number=epic_number,
        title=title,
        stories=stories,
        frontmatter=frontmatter,
        content=content,
    )
