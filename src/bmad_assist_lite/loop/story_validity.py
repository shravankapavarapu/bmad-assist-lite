"""Structural validation of an existing story file.

This is the predicate that authorises skipping ``create_story`` on a resume
path. It exists because the obvious predicate is unsafe: a story file being
*present* has already, once, been taken as evidence that the work behind it was
done, and it was not — the story was marked done with no implementing commit at
all. Presence is an artifact, not an outcome.

So the check is structural, and it is deliberately biased. Every defect refuses
the skip and the phase runs again; re-running costs one phase, whereas skipping
a phase that was needed costs a hollow story plus everything built on top of it.
Anything inconclusive — an unreadable file, an ambiguous name — refuses too.

Defects accumulate rather than short-circuit, mirroring the compiler's
Context Requirements validation, so a partial story file yields one actionable
message instead of a sequence of single-defect runs.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from bmad_assist_lite.core.paths import get_paths
from bmad_assist_lite.core.quality_gates import parse_quality_gates_table
from bmad_assist_lite.loop.story_paths import resolve_story_candidates

logger = logging.getLogger(__name__)

__all__ = ["StoryReuseVerdict", "check_story_reusable", "REQUIRED_HEADINGS"]


# Headings a story must carry to be reusable. "Quality Gates" is not decorative:
# the gate commands for a story are read from its table first and config second,
# so reusing a story file without one silently degrades that story's gate.
REQUIRED_HEADINGS: tuple[str, ...] = (
    "Acceptance Criteria",
    "Tasks / Subtasks",
    "Quality Gates",
)

# Headings whose section must also carry content — a bare heading is not a section.
REQUIRE_NON_EMPTY_BODY: frozenset[str] = frozenset({"Acceptance Criteria", "Tasks / Subtasks"})


@dataclass(frozen=True)
class StoryReuseVerdict:
    """Whether an existing story file may be reused, and why not if it may not."""

    path: Path | None
    defects: tuple[str, ...]

    @property
    def reusable(self) -> bool:
        """Return ``True`` only when a file resolved and carries no defect."""
        return self.path is not None and not self.defects

    def summary(self) -> str:
        """Render every defect as one actionable message."""
        where = str(self.path) if self.path is not None else "no story file"
        if not self.defects:
            return f"{where}: reusable"
        parts = [f"{where} cannot be reused:"]
        parts.extend(f"  - {d}" for d in self.defects)
        parts.append(
            "Fix: complete the story file, or delete it so create_story regenerates it."
        )
        return "\n".join(parts)


def _normalize_heading(text: str) -> str:
    """Collapse spacing so 'Tasks/Subtasks' and 'Tasks / Subtasks' compare equal."""
    return " ".join(text.replace("/", " / ").split()).casefold()


def _section_body(content: str, heading: str) -> str | None:
    """Return the body under ``heading``, or ``None`` when the heading is absent."""
    target = _normalize_heading(heading)
    lines = content.splitlines()

    start: int | None = None
    level = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        hashes = len(stripped) - len(stripped.lstrip("#"))
        if _normalize_heading(stripped.lstrip("#")) == target:
            start = i
            level = hashes
            break

    if start is None:
        return None

    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if hashes <= level:
                end = i
                break

    return "\n".join(lines[start + 1 : end])


def _story_id_in(content: str) -> str | None:
    """Extract the ``{epic}.{story}`` id from the story's title line."""
    match = re.search(r"^#\s*Story\s+(\d+\.\d+)\b", content, re.MULTILINE)
    return match.group(1) if match else None


def _collect_defects(content: str, story_id: str) -> list[str]:
    """Collect every structural reason the content may not be reused."""
    defects: list[str] = []

    for heading in REQUIRED_HEADINGS:
        body = _section_body(content, heading)
        if body is None:
            defects.append(f"missing required section: {heading}")
            continue
        if heading in REQUIRE_NON_EMPTY_BODY and not body.strip():
            defects.append(f"section is empty: {heading}")

    if _section_body(content, "Quality Gates") is not None and not parse_quality_gates_table(
        content
    ):
        defects.append(
            "Quality Gates section has no parseable table row "
            "(| Gate | `command` | **PENDING** |)"
        )

    found_id = _story_id_in(content)
    if found_id is None:
        defects.append("no '# Story {epic}.{story}:' title line, so the story id is unverifiable")
    elif found_id != story_id:
        defects.append(f"story id mismatch: file declares {found_id}, expected {story_id}")

    return defects


def check_story_reusable(story_id: str | None) -> StoryReuseVerdict:
    """Decide whether an existing story file may stand in for ``create_story``.

    Requires all of: the file resolves via the loop's shared resolution and is
    unambiguous; it is a regular file with content after stripping whitespace;
    it carries the required structural sections including a parseable Quality
    Gates table; and the story id it declares is the one being created.

    Args:
        story_id: Story identifier in ``"{epic}.{story}"`` form.

    Returns:
        A verdict whose ``reusable`` is ``True`` only when every condition
        holds. Anything unresolvable, ambiguous or unreadable is a refusal.

    """
    if not story_id:
        return StoryReuseVerdict(path=None, defects=("no story id in state",))

    candidates = resolve_story_candidates(story_id, get_paths().stories_dir)
    if not candidates:
        return StoryReuseVerdict(
            path=None, defects=(f"no story file resolves for story {story_id}",)
        )
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        return StoryReuseVerdict(
            path=None,
            defects=(f"story {story_id} resolves ambiguously to {len(candidates)} files: {names}",),
        )

    path = candidates[0]
    if not path.is_file():
        return StoryReuseVerdict(path=None, defects=(f"{path} is not a regular file",))

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return StoryReuseVerdict(path=path, defects=(f"story file is unreadable: {e}",))

    if not content.strip():
        return StoryReuseVerdict(path=path, defects=("story file is empty",))

    return StoryReuseVerdict(path=path, defects=tuple(_collect_defects(content, story_id)))
