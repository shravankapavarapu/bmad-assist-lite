"""Comprehensive tests for DependencyGraph DAG construction."""

import logging
import time

import pytest

from bmad_assist_lite.bmad.parser import EpicStory
from bmad_assist_lite.parallel.dependency_graph import DependencyGraph
from bmad_assist_lite.parallel.exceptions import ParallelError


# ============================================================================
# Helper factory
# ============================================================================


def _story(number: str, title: str = "", deps: list[str] | None = None) -> EpicStory:
    """Create an EpicStory with minimal required fields."""
    return EpicStory(
        number=number,
        title=title or f"Story {number}",
        dependencies=deps if deps is not None else [],
    )


# ============================================================================
# Task 5.1: DAG construction from EpicStory objects
# ============================================================================


class TestDAGConstruction:
    """Test DAG construction from EpicStory objects with known dependency structure."""

    def test_basic_dag_construction(self) -> None:
        """Build DAG from stories with known dependencies and verify edges."""
        stories = [
            _story("2.1", deps=[]),
            _story("2.2", deps=["Story 2.1"]),
            _story("2.3", deps=["Story 2.1", "Story 2.2"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.story_count == 3
        assert graph.dependencies_of("2.1") == []
        assert graph.dependencies_of("2.2") == ["2.1"]
        assert sorted(graph.dependencies_of("2.3")) == ["2.1", "2.2"]
        assert graph.roots == ["2.1"]

    def test_forward_adjacency_correctness(self) -> None:
        """Forward adjacency maps story → its dependencies."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["Story 3.1"]),
            _story("3.3", deps=["Story 3.1", "Story 3.2"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.dependencies_of("3.1") == []
        assert graph.dependencies_of("3.2") == ["3.1"]
        assert sorted(graph.dependencies_of("3.3")) == ["3.1", "3.2"]

    def test_reverse_adjacency_correctness(self) -> None:
        """Reverse adjacency maps story → who depends on it."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["Story 3.1"]),
            _story("3.3", deps=["Story 3.1", "Story 3.2"]),
        ]
        graph = DependencyGraph(stories)
        assert sorted(graph.dependents_of("3.1")) == ["3.2", "3.3"]
        assert graph.dependents_of("3.2") == ["3.3"]
        assert graph.dependents_of("3.3") == []


# ============================================================================
# Task 5.2: Roots property returns correct independent stories
# ============================================================================


class TestRoots:
    """Test roots returns correct independent stories."""

    def test_roots_identifies_independent_stories(self) -> None:
        """Stories with no dependencies are roots."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=[]),
        ]
        graph = DependencyGraph(stories)
        assert sorted(graph.roots) == ["1.1", "1.3"]

    def test_single_root_in_chain(self) -> None:
        """Linear chain A → B → C has only A as root."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.2"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.roots == ["1.1"]


# ============================================================================
# Task 5.3: Forward and reverse adjacency correctness (additional)
# ============================================================================


class TestAdjacencyLists:
    """Test forward and reverse adjacency list correctness."""

    def test_diamond_pattern_forward(self) -> None:
        """Diamond: D depends on B and C, B and C depend on A."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.1"]),
            _story("1.4", deps=["Story 1.2", "Story 1.3"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.1") == []
        assert graph.dependencies_of("1.2") == ["1.1"]
        assert graph.dependencies_of("1.3") == ["1.1"]
        assert sorted(graph.dependencies_of("1.4")) == ["1.2", "1.3"]

    def test_diamond_pattern_reverse(self) -> None:
        """Diamond: A is depended on by B and C."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.1"]),
            _story("1.4", deps=["Story 1.2", "Story 1.3"]),
        ]
        graph = DependencyGraph(stories)
        assert sorted(graph.dependents_of("1.1")) == ["1.2", "1.3"]
        assert graph.dependents_of("1.2") == ["1.4"]
        assert graph.dependents_of("1.3") == ["1.4"]
        assert graph.dependents_of("1.4") == []

    def test_diamond_roots(self) -> None:
        """Diamond pattern: only A is root."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.1"]),
            _story("1.4", deps=["Story 1.2", "Story 1.3"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.roots == ["1.1"]


# ============================================================================
# Task 5.4: Stories with no dependencies treated as roots
# ============================================================================


class TestAllIndependentStories:
    """Test that stories without dependencies are treated as roots."""

    def test_all_independent_are_roots(self) -> None:
        """All stories with no deps are roots."""
        stories = [
            _story("1.1"),
            _story("1.2"),
            _story("1.3"),
        ]
        graph = DependencyGraph(stories)
        assert sorted(graph.roots) == ["1.1", "1.2", "1.3"]

    def test_missing_dependencies_field_treated_as_empty(self) -> None:
        """EpicStory with default empty dependencies is a root."""
        story = EpicStory(number="1.1", title="Test")
        graph = DependencyGraph([story])
        assert graph.roots == ["1.1"]


# ============================================================================
# Task 5.5: Dependency string normalization
# ============================================================================


class TestDependencyNormalization:
    """Test dependency string normalization (e.g., 'Story 3.2' → '3.2')."""

    def test_story_prefix_stripped(self) -> None:
        """'Story 3.2' is normalized to '3.2'."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["Story 3.1"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.dependencies_of("3.2") == ["3.1"]

    def test_raw_number_without_prefix(self) -> None:
        """'3.1' without 'Story ' prefix is also accepted."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["3.1"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.dependencies_of("3.2") == ["3.1"]

    def test_mixed_formats(self) -> None:
        """Mix of 'Story X.Y' and 'X.Y' formats."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
            _story("3.3", deps=["Story 3.1", "3.2"]),
        ]
        graph = DependencyGraph(stories)
        assert sorted(graph.dependencies_of("3.3")) == ["3.1", "3.2"]

    def test_extra_whitespace_trimmed(self) -> None:
        """Leading/trailing whitespace in dependency strings is trimmed."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["  Story 3.1  "]),
        ]
        graph = DependencyGraph(stories)
        assert graph.dependencies_of("3.2") == ["3.1"]


# ============================================================================
# Task 5.6: Edge cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases: empty list, single story, all independent, linear chain."""

    def test_empty_story_list(self) -> None:
        """Zero stories produce an empty graph with no roots."""
        graph = DependencyGraph([])
        assert graph.story_count == 0
        assert graph.roots == []
        assert graph.all_story_ids == []

    def test_single_story_no_deps(self) -> None:
        """One story with no dependencies is the sole root."""
        graph = DependencyGraph([_story("1.1")])
        assert graph.story_count == 1
        assert graph.roots == ["1.1"]
        assert graph.all_story_ids == ["1.1"]

    def test_linear_chain(self) -> None:
        """A → B → C: only A is root."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.2"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.roots == ["1.1"]
        assert graph.dependencies_of("1.3") == ["1.2"]
        assert graph.dependents_of("1.1") == ["1.2"]

    def test_all_independent(self) -> None:
        """All independent stories are roots."""
        stories = [_story(f"1.{i}") for i in range(1, 6)]
        graph = DependencyGraph(stories)
        assert len(graph.roots) == 5


# ============================================================================
# Task 5.7: Unknown dependency references
# ============================================================================


class TestUnknownDependencies:
    """Test unknown dependency references are logged as warning and skipped."""

    def test_unknown_dep_logged_and_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """Dependency referencing story not in the epic is warned and skipped."""
        stories = [
            _story("1.1", deps=["Story 9.9"]),
        ]
        with caplog.at_level(logging.WARNING):
            graph = DependencyGraph(stories)

        assert graph.dependencies_of("1.1") == []
        assert "9.9" in caplog.text

    def test_unknown_dep_does_not_create_edge(self) -> None:
        """Unknown dependencies do not appear in adjacency lists."""
        stories = [
            _story("1.1", deps=["Story 99.99"]),
            _story("1.2", deps=["Story 1.1"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.1") == []
        assert graph.dependents_of("1.1") == ["1.2"]
        assert graph.roots == ["1.1"]


# ============================================================================
# Task 5.8: Self-referencing dependency
# ============================================================================


class TestSelfReference:
    """Test self-referencing dependency logged as warning and skipped."""

    def test_self_reference_logged_and_skipped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Story listing itself as dependency is warned and skipped."""
        stories = [
            _story("1.1", deps=["Story 1.1"]),
        ]
        with caplog.at_level(logging.WARNING):
            graph = DependencyGraph(stories)

        assert graph.dependencies_of("1.1") == []
        assert "self" in caplog.text.lower() or "1.1" in caplog.text


# ============================================================================
# Task 5.9: Performance with 50 stories
# ============================================================================


class TestPerformance:
    """Test performance with 50 stories completes in <1 second."""

    def test_50_stories_under_one_second(self) -> None:
        """DAG construction for 50 stories completes in <1 second (NFR9)."""
        stories = []
        for i in range(1, 51):
            deps = [f"Story 1.{j}" for j in range(1, i) if j % 3 == 0]
            stories.append(_story(f"1.{i}", deps=deps))

        start = time.monotonic()
        graph = DependencyGraph(stories)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0
        assert graph.story_count == 50


# ============================================================================
# Task 5.10: Duplicate story numbers raises ParallelError
# ============================================================================


class TestDuplicateStoryNumbers:
    """Test duplicate story numbers in input raises ParallelError."""

    def test_duplicate_numbers_raises(self) -> None:
        """Two EpicStory objects with same .number raises ParallelError."""
        stories = [
            _story("1.1", title="First"),
            _story("1.1", title="Duplicate"),
        ]
        with pytest.raises(ParallelError, match="[Dd]uplicate"):
            DependencyGraph(stories)


# ============================================================================
# Task 5.11: Duplicate entries in dependency list
# ============================================================================


class TestDuplicateDependencies:
    """Test duplicate entries in dependency list are deduplicated."""

    def test_duplicate_deps_deduplicated(self) -> None:
        """Dependencies ['1.1', '1.1'] produce only one edge."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1", "Story 1.1"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.2") == ["1.1"]

    def test_duplicate_deps_mixed_format(self) -> None:
        """Dependencies ['Story 1.1', '1.1'] (mixed format) produce only one edge."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1", "1.1"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.2") == ["1.1"]


# ============================================================================
# Accessor methods
# ============================================================================


class TestAccessorMethods:
    """Test accessor methods for graph queries."""

    def test_all_story_ids(self) -> None:
        """all_story_ids returns all story IDs in the graph."""
        stories = [
            _story("2.1"),
            _story("2.2"),
            _story("2.3"),
        ]
        graph = DependencyGraph(stories)
        assert sorted(graph.all_story_ids) == ["2.1", "2.2", "2.3"]

    def test_story_count(self) -> None:
        """story_count returns number of stories in the graph."""
        stories = [_story("1.1"), _story("1.2")]
        graph = DependencyGraph(stories)
        assert graph.story_count == 2

    def test_story_count_empty(self) -> None:
        """story_count returns 0 for empty graph."""
        graph = DependencyGraph([])
        assert graph.story_count == 0

    def test_dependencies_of_unknown_story_raises(self) -> None:
        """Querying dependencies of unknown story raises KeyError."""
        graph = DependencyGraph([_story("1.1")])
        with pytest.raises(KeyError):
            graph.dependencies_of("9.9")

    def test_dependents_of_unknown_story_raises(self) -> None:
        """Querying dependents of unknown story raises KeyError."""
        graph = DependencyGraph([_story("1.1")])
        with pytest.raises(KeyError):
            graph.dependents_of("9.9")

    def test_repr(self) -> None:
        """DependencyGraph has a useful repr."""
        stories = [_story("1.1"), _story("1.2", deps=["Story 1.1"])]
        graph = DependencyGraph(stories)
        r = repr(graph)
        assert "stories=2" in r
        assert "roots=1" in r


# ============================================================================
# Unparseable / malformed dependency strings
# ============================================================================


class TestUnparseableDependencies:
    """Test that malformed dependency strings are warned and skipped."""

    def test_empty_string_dep_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """An empty-string dependency is warned and skipped."""
        stories = [_story("1.1", deps=[""])]
        with caplog.at_level(logging.WARNING):
            graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.1") == []
        assert "Could not parse" in caplog.text

    def test_bare_word_dep_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """A bare word like 'foobar' is warned and skipped."""
        stories = [_story("1.1", deps=["foobar"])]
        with caplog.at_level(logging.WARNING):
            graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.1") == []
        assert "foobar" in caplog.text

    def test_story_prefix_only_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """'Story' without a number is warned and skipped."""
        stories = [_story("1.1", deps=["Story"])]
        with caplog.at_level(logging.WARNING):
            graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.1") == []
        assert "Could not parse" in caplog.text

    def test_triple_dotted_id_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """'3.2.1' (triple-dotted) is warned and skipped."""
        stories = [_story("1.1", deps=["3.2.1"])]
        with caplog.at_level(logging.WARNING):
            graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.1") == []
        assert "Could not parse" in caplog.text

    def test_slug_format_dep_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """Slug-format dep '2-1-epic-foo' is warned and skipped."""
        stories = [_story("1.1", deps=["2-1-epic-dependency-parsing"])]
        with caplog.at_level(logging.WARNING):
            graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.1") == []
        assert "Could not parse" in caplog.text

    def test_mixed_valid_and_invalid_deps(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Valid deps are kept, invalid ones are warned and skipped."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1", "foobar", "Story"]),
        ]
        with caplog.at_level(logging.WARNING):
            graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.2") == ["1.1"]
        assert "foobar" in caplog.text


# ============================================================================
# Natural numeric sort order
# ============================================================================


class TestNaturalSortOrder:
    """Test that story IDs are sorted numerically, not lexicographically."""

    def test_roots_natural_order(self) -> None:
        """Roots with IDs like 1.2, 1.10 sort numerically."""
        stories = [_story(f"1.{i}") for i in [10, 2, 1, 20, 3]]
        graph = DependencyGraph(stories)
        assert graph.roots == ["1.1", "1.2", "1.3", "1.10", "1.20"]

    def test_all_story_ids_natural_order(self) -> None:
        """all_story_ids sorts numerically."""
        stories = [_story(f"1.{i}") for i in [10, 2, 1]]
        graph = DependencyGraph(stories)
        assert graph.all_story_ids == ["1.1", "1.2", "1.10"]
