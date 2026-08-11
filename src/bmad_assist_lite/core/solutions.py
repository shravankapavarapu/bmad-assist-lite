"""A compounding store of solved problems, so the loop stops re-deriving fixes.

Each epic that re-discovers the same fix pays for it again. This module keeps
what was already solved as a small, durable, greppable set of markdown files
under ``docs/solutions/``, and hands the relevant ones back to the phases that
would otherwise work them out from scratch.

This also discharges ADR-0001's re-filed "option C" — the on-disk work log that
was removed from the continuity option set. It returns here in the one shape the
evidence supports: not a running narrative of what happened, but a deduplicated
index of problems whose solutions are known.

Bounded, at three levels, none of them advisory
-----------------------------------------------
The transcripts' warning about this pattern is that the file bloats until it is
worthless. So the bounds are structural rather than documented:

1. **Per field**, at construction — a field longer than ``MAX_FIELD_CHARS`` is
   truncated by a validator, so an oversized value cannot be stored even by a
   caller that wants to.
2. **Per store** — ``write_solution`` evicts the oldest records beyond
   ``max_records``.
3. **Per injection** — ``render_context_block`` is capped in characters, so the
   store growing can never grow a prompt.

Deduplication is by content fingerprint over the problem and its fix, not by
slug or timestamp: the same problem solved again writes nothing.

Our own artifacts only
----------------------
Records summarise this tool's structured artifacts — findings, gate failures,
retrospective conclusions. **Raw provider transcripts are never summarised into
this store.** Roo shipped a cheap condensing model over tool-call-heavy history
and reverted it, because that history does not summarise across model families.
The per-field cap is what makes that a property of the code rather than a note.
"""

import hashlib
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from bmad_assist_lite.core.exceptions import StateError

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_FIELD_CHARS",
    "MAX_TAGS",
    "SOLUTIONS_DIRNAME",
    "SolutionRecord",
    "load_solutions",
    "render_context_block",
    "select_for_tags",
    "solutions_dir",
    "to_markdown",
    "write_solution",
]

SOLUTIONS_DIRNAME: str = "solutions"
TEMP_FILE_SUFFIX: str = ".tmp"

MAX_FIELD_CHARS: int = 600
"""Longest a single prose field may be. Enforced by a validator, not by callers."""

MAX_TAGS: int = 8
DEFAULT_MAX_RECORDS: int = 200
DEFAULT_MAX_INJECTED: int = 5
DEFAULT_MAX_INJECTED_CHARS: int = 4000

_FRONTMATTER = re.compile(r"^---\n(?P<yaml>.*?)\n---\n(?P<body>.*)$", re.S)
_SLUG_SAFE = re.compile(r"[^a-z0-9-]+")

_FINGERPRINT_FIELDS = ("category", "symptom", "root_cause", "fix")
"""Which fields identify a problem. Slug and timestamp deliberately excluded."""


def _truncate(value: str) -> str:
    """Cap a prose field, marking that it was cut."""
    value = value.strip()
    if len(value) <= MAX_FIELD_CHARS:
        return value
    return value[:MAX_FIELD_CHARS] + "…"


class SolutionRecord(BaseModel):
    """One solved problem, in the shape a later story can act on.

    Attributes:
        slug: Filename stem; human-facing identity only.
        tags: What this is relevant to (phase names, ``epic-N``, topic words).
            Lowercased, deduplicated and capped at ``MAX_TAGS``.
        category: Coarse grouping, e.g. ``"quality-gate"``.
        symptom: What was observed.
        root_cause: Why it happened.
        fix: What resolved it — the field a later story actually needs.
        prevention: The rule that stops a recurrence.
        story_id: Story the solution came from, if any.
        timestamp: Naive UTC.

    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    slug: str
    tags: tuple[str, ...] = ()
    category: str = ""
    symptom: str = ""
    root_cause: str = ""
    fix: str = ""
    prevention: str = ""
    story_id: str | None = None
    timestamp: datetime

    @field_validator("symptom", "root_cause", "fix", "prevention", "category")
    @classmethod
    def _cap_prose(cls, value: str) -> str:
        """Cap prose at construction, so an oversized value is never stored."""
        return _truncate(value)

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Lowercase, deduplicate (order-preserving) and cap the tag list."""
        seen: list[str] = []
        for tag in value:
            cleaned = tag.strip().lower()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return tuple(seen[:MAX_TAGS])

    @field_validator("slug")
    @classmethod
    def _safe_slug(cls, value: str) -> str:
        """Reduce the slug to a filesystem-safe stem."""
        slug = _SLUG_SAFE.sub("-", value.strip().lower()).strip("-")
        return slug or "solution"

    @property
    def fingerprint(self) -> str:
        """Content hash of the problem and its fix, for deduplication."""
        material = "\x1f".join(
            " ".join(str(getattr(self, name)).split()).lower() for name in _FINGERPRINT_FIELDS
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ============================================================================
# Serialization
# ============================================================================


def solutions_dir(project_path: Path) -> Path:
    """Resolve the store directory for a project."""
    return project_path / "docs" / SOLUTIONS_DIRNAME


def to_markdown(record: SolutionRecord) -> str:
    """Render a record as markdown with a YAML frontmatter header.

    Markdown rather than a data file on purpose: these are meant to be read and
    grepped by a person as readily as by the loop.
    """
    front = {
        "slug": record.slug,
        "tags": list(record.tags),
        "category": record.category,
        "story_id": record.story_id,
        "timestamp": record.timestamp.isoformat(),
        "fingerprint": record.fingerprint,
    }
    header = yaml.dump(front, default_flow_style=False, sort_keys=False)
    return (
        f"---\n{header}---\n\n"
        f"# {record.slug}\n\n"
        f"## Symptom\n\n{record.symptom}\n\n"
        f"## Root cause\n\n{record.root_cause}\n\n"
        f"## Fix\n\n{record.fix}\n\n"
        f"## Prevention\n\n{record.prevention}\n"
    )


def _section(body: str, name: str) -> str:
    """Pull one ``## name`` section out of a rendered record."""
    match = re.search(rf"^## {name}\n\n(.*?)(?=\n## |\Z)", body, re.S | re.M)
    return match.group(1).strip() if match else ""


def from_markdown(text: str) -> SolutionRecord | None:
    """Parse a record back, or None if the file is not one.

    A file that does not parse is skipped rather than raised: the store lives in
    ``docs/`` where a person may reasonably drop an unrelated markdown file, and
    one stray file must not make the whole store unreadable.
    """
    match = _FRONTMATTER.match(text)
    if match is None:
        return None

    try:
        front = yaml.safe_load(match.group("yaml")) or {}
        if not isinstance(front, dict):
            return None
        body = match.group("body")
        return SolutionRecord(
            slug=str(front.get("slug", "solution")),
            tags=tuple(front.get("tags") or ()),
            category=str(front.get("category", "")),
            story_id=front.get("story_id"),
            timestamp=_parse_timestamp(front.get("timestamp")),
            symptom=_section(body, "Symptom"),
            root_cause=_section(body, "Root cause"),
            fix=_section(body, "Fix"),
            prevention=_section(body, "Prevention"),
        )
    except (yaml.YAMLError, ValidationError, TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> datetime:
    """Read a timestamp tolerantly; an unreadable one sorts oldest."""
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.min


# ============================================================================
# Store
# ============================================================================


def load_solutions(project_path: Path) -> list[SolutionRecord]:
    """Read every parseable record, newest last.

    Args:
        project_path: Project root.

    Returns:
        The records, oldest first. A missing store reads as empty.

    """
    directory = solutions_dir(project_path)
    if not directory.is_dir():
        return []

    records: list[SolutionRecord] = []
    for path in sorted(directory.glob("*.md")):
        try:
            record = from_markdown(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            logger.debug("Unreadable solution file skipped: %s", path)
            continue
        if record is None:
            logger.debug("Not a solution record, skipped: %s", path)
            continue
        records.append(record)

    return sorted(records, key=lambda r: r.timestamp)


def write_solution(
    record: SolutionRecord,
    project_path: Path,
    *,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> Path | None:
    """Add a record to the store unless it is already known.

    Args:
        record: The solution to store.
        project_path: Project root.
        max_records: Cap on stored records; oldest are evicted beyond it.

    Returns:
        The path written, or None if an identical problem was already stored.

    Raises:
        StateError: If the record cannot be written.

    """
    existing = load_solutions(project_path)
    if any(r.fingerprint == record.fingerprint for r in existing):
        logger.debug("Solution already stored (fingerprint %s)", record.fingerprint[:12])
        return None

    directory = solutions_dir(project_path)
    path = directory / f"{record.slug}.md"
    if path.exists():
        path = directory / f"{record.slug}-{record.fingerprint[:8]}.md"
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)

    try:
        directory.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(to_markdown(record), encoding="utf-8")
        os.replace(temp_path, path)
    except (OSError, yaml.YAMLError) as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                logger.debug("Could not remove solutions temp file %s", temp_path)
        raise StateError(f"Failed to write solution record to {path}: {e}") from e

    _evict(project_path, max_records)
    return path


def _evict(project_path: Path, max_records: int) -> None:
    """Drop the oldest records until the store is within its cap."""
    if max_records <= 0:
        return

    directory = solutions_dir(project_path)
    dated: list[tuple[datetime, Path]] = []
    for path in directory.glob("*.md"):
        try:
            record = from_markdown(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if record is not None:
            dated.append((record.timestamp, path))

    for _, path in sorted(dated, key=lambda item: item[0])[: max(0, len(dated) - max_records)]:
        try:
            path.unlink()
            logger.debug("Evicted oldest solution record %s", path.name)
        except OSError:
            logger.debug("Could not evict solution record %s", path)


# ============================================================================
# Retrieval
# ============================================================================


def select_for_tags(
    records: list[SolutionRecord],
    tags: set[str],
    *,
    limit: int = DEFAULT_MAX_INJECTED,
) -> list[SolutionRecord]:
    """Pick the records relevant to ``tags``, most relevant first.

    Relevance is tag overlap, then recency. Records sharing no tag are excluded
    rather than ranked last: injecting an unrelated solution into every phase is
    how a store like this becomes noise that gets ignored.

    Args:
        records: Candidate records.
        tags: Tags describing the current phase and story.
        limit: Maximum records to return.

    Returns:
        The selected records.

    """
    wanted = {t.strip().lower() for t in tags if t and t.strip()}
    if not wanted or limit <= 0:
        return []

    scored = [(len(wanted & set(r.tags)), r) for r in records]
    matching = [(overlap, r) for overlap, r in scored if overlap > 0]
    matching.sort(key=lambda item: (item[0], item[1].timestamp), reverse=True)
    return [r for _, r in matching[:limit]]


def render_context_block(
    records: list[SolutionRecord],
    *,
    max_chars: int = DEFAULT_MAX_INJECTED_CHARS,
) -> str:
    """Render selected records as a bounded markdown block for a prompt.

    Args:
        records: Records to render, most relevant first.
        max_chars: Hard cap on the returned string.

    Returns:
        The block, or an empty string when there is nothing to say. Never longer
        than ``max_chars`` — the store may grow without the prompt growing.

    """
    if not records or max_chars <= 0:
        return ""

    header = "## Previously solved problems\n\n"
    parts: list[str] = [header]
    length = len(header)

    for record in records:
        entry = (
            f"### {record.slug}\n"
            f"- Symptom: {record.symptom}\n"
            f"- Root cause: {record.root_cause}\n"
            f"- Fix: {record.fix}\n"
            f"- Prevention: {record.prevention}\n\n"
        )
        if length + len(entry) > max_chars:
            break
        parts.append(entry)
        length += len(entry)

    block = "".join(parts)
    return "" if block == header else block[:max_chars]


def context_block_for(
    project_path: Path,
    tags: set[str],
    *,
    limit: int = DEFAULT_MAX_INJECTED,
    max_chars: int = DEFAULT_MAX_INJECTED_CHARS,
) -> str:
    """Load, select and render in one step, never raising.

    Injection sits on the phase path, so a broken store must degrade to "no
    extra context" rather than ending the run.
    """
    try:
        return render_context_block(
            select_for_tags(load_solutions(project_path), tags, limit=limit),
            max_chars=max_chars,
        )
    except Exception:
        logger.warning("Solutions store unavailable; injecting nothing", exc_info=True)
        return ""
