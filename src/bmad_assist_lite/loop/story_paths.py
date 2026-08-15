"""Story file path resolution shared by the loop's phase handlers.

Two naming forms are in use for a story file, and every consumer must recognise
the same two in the same order or they will disagree about which file a story
is: the primary ``story-{epic}.{story}.md`` and the alternate
``{epic}-{story}-*.md``. The alternate form is anchored on a trailing hyphen so
story ``1.2`` cannot pull in ``1-20-*.md``.
"""

import logging
from pathlib import Path

from bmad_assist_lite.core.paths import get_paths

logger = logging.getLogger(__name__)

__all__ = ["resolve_story_candidates", "resolve_story_path"]


def resolve_story_candidates(story_id: str | None, stories_dir: Path) -> list[Path]:
    """Return every story file that ``story_id`` could name, best match first.

    The primary naming form is exclusive: when ``story-{epic}.{story}.md``
    exists it is the answer and the alternate form is not consulted. Only the
    alternate form can produce more than one candidate, and callers that must
    not act on an ambiguous match check the length.

    Args:
        story_id: Story identifier in ``"{epic}.{story}"`` form.
        stories_dir: Directory holding story markdown files.

    Returns:
        Ordered list of candidate paths; empty when nothing resolves.

    """
    if not story_id:
        return []

    parts = story_id.split(".")
    if len(parts) != 2 or not all(parts):
        return []

    epic_num, story_num = parts

    primary = stories_dir / f"story-{epic_num}.{story_num}.md"
    if primary.exists():
        return [primary]

    return sorted(stories_dir.glob(f"{epic_num}-{story_num}-*.md"))


def resolve_story_path(story_id: str | None) -> Path | None:
    """Resolve the story file for ``story_id``, or ``None`` if none is found."""
    candidates = resolve_story_candidates(story_id, get_paths().stories_dir)
    return candidates[0] if candidates else None
