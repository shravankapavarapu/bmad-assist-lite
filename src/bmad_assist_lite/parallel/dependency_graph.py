"""Dependency graph for epic story DAG construction.

Parses story dependencies from parsed EpicStory objects and builds a directed
acyclic graph (DAG) representing the dependency relationships between stories.
Provides efficient queries for roots, dependencies, dependents, cycle detection,
and scheduling score computation.
"""

import logging
import re
from collections import deque

from bmad_assist_lite.bmad.parser import EpicStory
from bmad_assist_lite.parallel.exceptions import ParallelError

logger = logging.getLogger(__name__)

# Default priority when EpicStory.priority is empty or non-numeric
_DEFAULT_PRIORITY = 5

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
    logger.warning("[DependencyGraph] Could not parse dependency string: %r", raw)
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
            ParallelError: If duplicate story numbers are found in the input,
                or if a circular dependency is detected.

        """
        self._forward: dict[str, list[str]] = {}
        self._reverse: dict[str, list[str]] = {}
        self._dep_count: dict[str, int] = {}
        self._priorities: dict[str, int] = {}
        self._scores: dict[str, int] = {}

        self._build(stories)
        self.detect_cycles()
        self._scores = self._compute_scores()

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
                logger.error("[DependencyGraph] %s", msg)
                raise ParallelError(msg)
            known_ids.add(sid)
            ordered_ids.append(sid)

        # Initialize adjacency lists for every known story (insertion order)
        for sid in ordered_ids:
            self._forward[sid] = []
            self._reverse[sid] = []
            self._dep_count[sid] = 0

        # Parse priorities
        for story in stories:
            sid = story.number
            try:
                self._priorities[sid] = int(story.priority)
            except ValueError:
                self._priorities[sid] = _DEFAULT_PRIORITY

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
                        "[DependencyGraph] Story %s lists itself as a dependency;"
                        " skipping self-reference",
                        sid,
                    )
                    continue

                # Skip unknown dependencies
                if dep_id not in known_ids:
                    logger.warning(
                        "[DependencyGraph] Story %s depends on %s which is not in the epic;"
                        " skipping",
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
            "[DependencyGraph] Built dependency graph: %d stories, %d roots",
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

    @property
    def scores(self) -> dict[str, int]:
        """Return the computed scheduling scores for all stories.

        Returns:
            Dict mapping story_id to its scheduling score.

        """
        return dict(self._scores)

    def score_of(self, story_id: str) -> int:
        """Return the scheduling score for a specific story.

        Args:
            story_id: The story ID to query.

        Returns:
            The scheduling score for the story.

        Raises:
            KeyError: If story_id is not in the graph.

        """
        if story_id not in self._scores:
            raise KeyError(f"Story {story_id!r} not found in graph")
        return self._scores[story_id]

    # ========================================================================
    # Cycle detection
    # ========================================================================

    def detect_cycles(self) -> None:
        """Detect circular dependencies using DFS with three-color marking.

        Iterate over ALL nodes to cover disconnected subgraphs. If a cycle is
        found, reconstruct the cycle path and raise ParallelError.

        Raises:
            ParallelError: If a circular dependency is detected, with the
                cycle path in the error message.

        """
        # Three-color marking: WHITE=unvisited, GRAY=in-progress, BLACK=done
        white = 0
        gray = 1
        black = 2

        color: dict[str, int] = dict.fromkeys(self._forward, white)
        # Track the DFS path for cycle reconstruction
        path: list[str] = []

        def _dfs(node: str) -> list[str] | None:
            """Perform DFS from node, returning cycle path if found."""
            color[node] = gray
            path.append(node)

            for neighbor in self._forward[node]:
                if color[neighbor] == gray:
                    # Found a cycle — reconstruct path from neighbor to neighbor
                    cycle_start = path.index(neighbor)
                    cycle = path[cycle_start:] + [neighbor]
                    return cycle
                if color[neighbor] == white:
                    result = _dfs(neighbor)
                    if result is not None:
                        return result

            path.pop()
            color[node] = black
            return None

        # Check all nodes (handles disconnected subgraphs)
        for story_id in self._forward:
            if color[story_id] == white:
                cycle = _dfs(story_id)
                if cycle is not None:
                    cycle_str = " -> ".join(cycle)
                    msg = f"Circular dependency: {cycle_str}"
                    logger.error("[DependencyGraph] %s", msg)
                    raise ParallelError(msg)

        logger.debug(
            "[DependencyGraph] Cycle detection complete: no cycles found in %d stories",
            len(self._forward),
        )

    # ========================================================================
    # Scheduling score computation
    # ========================================================================

    def _compute_scores(self) -> dict[str, int]:
        """Compute scheduling scores for all stories.

        Score formula: (1000 * unblock_potential) + (100 * depth_score) + (10 * priority)

        Where:
        - unblock_potential: count of all transitive downstream dependents
        - depth_score: max_depth - node_depth (roots get highest)
        - priority: parsed from EpicStory.priority, default 5

        Returns:
            Dict mapping story_id to its scheduling score.

        """
        if not self._forward:
            return {}

        unblock = self._compute_unblock_potential()
        depths = self._compute_topological_depths()

        max_depth = max(depths.values()) if depths else 0

        result: dict[str, int] = {}
        for sid in self._forward:
            unblock_potential = unblock[sid]
            depth_score = max_depth - depths[sid]
            priority = self._priorities.get(sid, _DEFAULT_PRIORITY)
            score = (1000 * unblock_potential) + (100 * depth_score) + (10 * priority)
            result[sid] = score
            logger.debug(
                "[DependencyGraph] Score for %s: %d "
                "(unblock=%d, depth_score=%d, priority=%d)",
                sid,
                score,
                unblock_potential,
                depth_score,
                priority,
            )

        return result

    def _compute_unblock_potential(self) -> dict[str, int]:
        """Compute transitive downstream dependent count for each story.

        Use BFS on reverse adjacency to count all stories transitively
        dependent on each story.

        Returns:
            Dict mapping story_id to count of transitive dependents.

        """
        result: dict[str, int] = {}

        for sid in self._forward:
            # BFS on reverse adjacency from sid
            visited: set[str] = set()
            queue: deque[str] = deque()
            for dep in self._reverse[sid]:
                if dep not in visited:
                    visited.add(dep)
                    queue.append(dep)
            while queue:
                current = queue.popleft()
                for dep in self._reverse[current]:
                    if dep not in visited:
                        visited.add(dep)
                        queue.append(dep)
            result[sid] = len(visited)

        return result

    # ========================================================================
    # Ready story discovery
    # ========================================================================

    def are_dependencies_satisfied(self, story_id: str, done_ids: set[str]) -> bool:
        """Check whether all dependencies of a story are satisfied.

        Performs O(1) set membership lookup per dependency, making the total
        cost O(k) where k is the number of direct dependencies (typically 0-3).

        Args:
            story_id: The story ID to check.
            done_ids: Set of story IDs that have been completed.

        Returns:
            True if every dependency of story_id is present in done_ids,
            or if the story has no dependencies (vacuously satisfied).

        Raises:
            KeyError: If story_id is not in the graph.

        """
        if story_id not in self._forward:
            raise KeyError(f"Story {story_id!r} not found in graph")
        return all(dep_id in done_ids for dep_id in self._forward[story_id])

    def get_ready_stories(
        self,
        done_ids: set[str],
        in_flight_ids: set[str],
        blocked_ids: set[str],
    ) -> list[str]:
        """Determine which stories are ready to execute.

        A story is "ready" if ALL of the following hold:
        - All its dependencies are in done_ids (satisfied).
        - The story itself is NOT in done_ids (already completed).
        - The story itself is NOT in in_flight_ids (currently running).
        - The story itself is NOT in blocked_ids (failed/blocked).

        Ready stories are sorted by descending scheduling score so that the
        orchestrator can pick the highest-value stories first. Stories with
        equal scores are sorted by natural numeric order for deterministic
        tiebreaking.

        This is a pure query method — it does not mutate any internal state.
        The orchestrator calls this after every story completion or block event
        with updated sets. Re-evaluation is simply calling this method again.

        Args:
            done_ids: Set of story IDs that completed successfully.
            in_flight_ids: Set of story IDs currently being executed.
            blocked_ids: Set of story IDs that are blocked/failed.

        Returns:
            List of story IDs that are ready to execute, sorted by descending
            scheduling score with natural numeric tiebreaking.

        """
        ready: list[str] = []

        for story_id in self._forward:
            if story_id in done_ids or story_id in in_flight_ids or story_id in blocked_ids:
                continue
            if self.are_dependencies_satisfied(story_id, done_ids):
                ready.append(story_id)

        # Sort by descending score, then by natural numeric order for tiebreaking
        ready.sort(key=lambda sid: (-self._scores[sid], _story_sort_key(sid)))

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[DependencyGraph] Ready stories: %s (scores: %s)",
                ready,
                {sid: self._scores[sid] for sid in ready},
            )

        return ready

    # ========================================================================
    # Topological depth computation
    # ========================================================================

    def _compute_topological_depths(self) -> dict[str, int]:
        """Compute longest path from any root to each node.

        Use BFS (Kahn's algorithm style) to compute topological depth as
        the longest path from any root to each node.

        Returns:
            Dict mapping story_id to its topological depth.

        """
        depths: dict[str, int] = dict.fromkeys(self._forward, 0)
        # in-degree based on forward edges (story depends on = forward edge)
        in_degree: dict[str, int] = {sid: len(self._forward[sid]) for sid in self._forward}

        queue: deque[str] = deque()
        for sid in self._forward:
            if in_degree[sid] == 0:
                queue.append(sid)

        while queue:
            current = queue.popleft()
            # For each story that depends on current (reverse edges)
            for dependent in self._reverse[current]:
                new_depth = depths[current] + 1
                if new_depth > depths[dependent]:
                    depths[dependent] = new_depth
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        return depths
