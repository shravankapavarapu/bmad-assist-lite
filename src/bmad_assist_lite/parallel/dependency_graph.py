"""Dependency graph for epic story DAG construction.

Parses story dependencies from parsed EpicStory objects and builds a directed
acyclic graph (DAG) representing the dependency relationships between stories.
Provides efficient queries for roots, dependencies, and dependents.
"""

import logging
import re

from bmad_assist_lite.bmad.parser import EpicStory
from bmad_assist_lite.parallel.exceptions import ParallelError

logger = logging.getLogger(__name__)

# Pattern to strip "Story " prefix from dependency strings like "Story 3.2"
_STORY_PREFIX_RE = re.compile(r"^\s*(?:Story\s+)?(\d+\.\d+)\s*$", re.IGNORECASE)


def _story_sort_key(story_id: str) -> tuple[int, ...]:
    """Return a numeric sort key for a story ID string.

    Splits "2.10" into (2, 10) for natural numeric ordering, avoiding the
    lexicographic pitfall where "1.10" < "1.2".

    Args:
        story_id: A dot-separated story ID, e.g. "2.10".

    Returns:
        Tuple of integers for comparison.

    """
    try:
        return tuple(int(part) for part in story_id.split("."))
    except ValueError:
        return (0,)


def _normalize_dependency(raw: str) -> str | None:
    """Normalize a raw dependency string to a canonical story ID.

    Args:
        raw: Raw dependency string, e.g. "Story 3.2", "3.2", "  Story 3.2  ".

    Returns:
        Normalized story ID (e.g. "3.2"), or None if the string cannot be parsed.

    """
    match = _STORY_PREFIX_RE.match(raw)
    if match:
        return match.group(1)
    logger.warning("Could not parse dependency string: %r", raw)
    return None


class DependencyGraph:
    """Directed acyclic graph of story dependencies.

    Accepts a list of EpicStory objects (from bmad/parser.py) and constructs
    forward and reverse adjacency lists for efficient dependency queries.

    Forward adjacency: story_id → list of story_ids it depends on.
    Reverse adjacency: story_id → list of story_ids that depend on it.
    """

    def __init__(self, stories: list[EpicStory]) -> None:
        """Construct the dependency graph from parsed stories.

        Args:
            stories: List of EpicStory objects from the epic parser.

        Raises:
            ParallelError: If duplicate story numbers are found in the input.

        """
        self._forward: dict[str, list[str]] = {}
        self._reverse: dict[str, list[str]] = {}
        self._dep_count: dict[str, int] = {}

        self._build(stories)

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return (
            f"DependencyGraph(stories={self.story_count}, "
            f"roots={len(self.roots)})"
        )

    def _build(self, stories: list[EpicStory]) -> None:
        """Build the forward and reverse adjacency lists.

        Args:
            stories: List of EpicStory objects to build the graph from.

        Raises:
            ParallelError: If duplicate story numbers are found.

        """
        # Collect all known story IDs first, checking for duplicates.
        # Use a list to preserve input order (deterministic dict key order).
        known_ids: set[str] = set()
        ordered_ids: list[str] = []
        for story in stories:
            sid = story.number
            if sid in known_ids:
                msg = f"Duplicate story number: {sid!r}"
                logger.error(msg)
                raise ParallelError(msg)
            known_ids.add(sid)
            ordered_ids.append(sid)

        # Initialize adjacency lists for every known story (insertion order)
        for sid in ordered_ids:
            self._forward[sid] = []
            self._reverse[sid] = []
            self._dep_count[sid] = 0

        # Build edges
        for story in stories:
            sid = story.number
            seen_deps: set[str] = set()

            for raw_dep in story.dependencies:
                dep_id = _normalize_dependency(raw_dep)
                if dep_id is None:
                    continue

                # Skip self-references
                if dep_id == sid:
                    logger.warning(
                        "Story %s lists itself as a dependency; skipping self-reference",
                        sid,
                    )
                    continue

                # Skip unknown dependencies
                if dep_id not in known_ids:
                    logger.warning(
                        "Story %s depends on %s which is not in the epic; skipping",
                        sid,
                        dep_id,
                    )
                    continue

                # Deduplicate silently
                if dep_id in seen_deps:
                    continue
                seen_deps.add(dep_id)

                # Add forward edge: sid depends on dep_id
                self._forward[sid].append(dep_id)
                # Add reverse edge: dep_id is depended on by sid
                self._reverse[dep_id].append(sid)
                self._dep_count[sid] += 1

        logger.debug(
            "Built dependency graph: %d stories, %d roots",
            len(known_ids),
            sum(1 for c in self._dep_count.values() if c == 0),
        )

    @property
    def roots(self) -> list[str]:
        """Return story IDs with no dependencies (out-degree 0 in forward graph).

        Stories with zero forward edges (no dependencies) are roots. This is
        equivalent to in-degree 0 in the reverse graph.

        Returns:
            List of story IDs that have zero dependencies, sorted by numeric
            value for deterministic natural ordering.

        """
        root_ids = [sid for sid, count in self._dep_count.items() if count == 0]
        return sorted(root_ids, key=_story_sort_key)

    def dependencies_of(self, story_id: str) -> list[str]:
        """Return the list of story IDs that the given story depends on.

        Args:
            story_id: The story ID to query.

        Returns:
            List of story IDs that this story depends on.

        Raises:
            KeyError: If story_id is not in the graph.

        """
        if story_id not in self._forward:
            raise KeyError(f"Story {story_id!r} not found in graph")
        return list(self._forward[story_id])

    def dependents_of(self, story_id: str) -> list[str]:
        """Return the list of story IDs that depend on the given story.

        Args:
            story_id: The story ID to query.

        Returns:
            List of story IDs that depend on this story.

        Raises:
            KeyError: If story_id is not in the graph.

        """
        if story_id not in self._reverse:
            raise KeyError(f"Story {story_id!r} not found in graph")
        return list(self._reverse[story_id])

    @property
    def all_story_ids(self) -> list[str]:
        """Return all story IDs in the graph.

        Returns:
            Sorted list of all story IDs in natural numeric order.

        """
        return sorted(self._forward.keys(), key=_story_sort_key)

    @property
    def story_count(self) -> int:
        """Return the number of stories in the graph.

        Returns:
            Number of stories.

        """
        return len(self._forward)
