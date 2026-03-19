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


def _story(
    number: str,
    title: str = "",
    deps: list[str] | None = None,
    priority: str = "",
) -> EpicStory:
    """Create an EpicStory with minimal required fields."""
    return EpicStory(
        number=number,
        title=title or f"Story {number}",
        dependencies=deps if deps is not None else [],
        priority=priority,
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
        """DAG construction + cycle detection + scoring + ready discovery for 50 stories in <1s."""
        stories = []
        for i in range(1, 51):
            deps = [f"Story 1.{j}" for j in range(1, i) if j % 3 == 0]
            stories.append(_story(f"1.{i}", deps=deps))

        start = time.monotonic()
        graph = DependencyGraph(stories)

        # Verify get_ready_stories also completes within the time budget
        ready = graph.get_ready_stories(set(), set(), set())
        assert len(ready) > 0

        # Simulate incremental completion of first 25 stories
        done: set[str] = set()
        for story_id in graph.all_story_ids[:25]:
            done.add(story_id)
            graph.get_ready_stories(done, set(), set())

        elapsed = time.monotonic() - start

        assert elapsed < 1.0
        assert graph.story_count == 50
        # Verify scoring was also computed
        assert len(graph.scores) == 50


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


# ============================================================================
# Story 2.2 Task 4.1–4.7: Cycle detection tests
# ============================================================================


class TestCycleDetection:
    """Test circular dependency detection."""

    def test_simple_2_node_cycle_raises(self) -> None:
        """A depends on B and B depends on A raises ParallelError with cycle path."""
        stories = [
            _story("2.1", deps=["Story 2.2"]),
            _story("2.2", deps=["Story 2.1"]),
        ]
        with pytest.raises(ParallelError, match="Circular dependency"):
            DependencyGraph(stories)

    def test_3_node_cycle_raises(self) -> None:
        """A→B→C→A cycle raises ParallelError with cycle path."""
        stories = [
            _story("2.1", deps=["Story 2.3"]),
            _story("2.2", deps=["Story 2.1"]),
            _story("2.3", deps=["Story 2.2"]),
        ]
        with pytest.raises(ParallelError, match="Circular dependency"):
            DependencyGraph(stories)

    def test_self_cycle_no_error(self) -> None:
        """Self-reference is silently removed by _build(), so detect_cycles() should not trigger."""
        stories = [
            _story("1.1", deps=["Story 1.1"]),
        ]
        # Should not raise — self-refs are stripped in _build()
        graph = DependencyGraph(stories)
        assert graph.dependencies_of("1.1") == []

    def test_valid_dag_diamond_no_cycle(self) -> None:
        """Valid diamond DAG does NOT raise (no false positives)."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.1"]),
            _story("1.4", deps=["Story 1.2", "Story 1.3"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.story_count == 4

    def test_disconnected_subgraphs_with_cycle(self) -> None:
        """One valid subgraph + one with cycle → cycle detected."""
        stories = [
            # Valid subgraph
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            # Cyclic subgraph
            _story("2.1", deps=["Story 2.2"]),
            _story("2.2", deps=["Story 2.1"]),
        ]
        with pytest.raises(ParallelError, match="Circular dependency"):
            DependencyGraph(stories)

    def test_linear_chain_no_cycle(self) -> None:
        """Linear chain A→B→C has no cycle."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.2"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.story_count == 3

    def test_cycle_error_message_contains_path(self) -> None:
        """Cycle error message contains the specific cycle path."""
        stories = [
            _story("2.1", deps=["Story 2.2"]),
            _story("2.2", deps=["Story 2.1"]),
        ]
        with pytest.raises(ParallelError, match=r"2\.\d+ -> 2\.\d+ -> 2\.\d+"):
            DependencyGraph(stories)

    def test_detect_cycles_called_from_init(self) -> None:
        """Constructing DependencyGraph with cyclic stories raises from __init__."""
        stories = [
            _story("3.1", deps=["Story 3.2"]),
            _story("3.2", deps=["Story 3.1"]),
        ]
        with pytest.raises(ParallelError, match="Circular dependency"):
            DependencyGraph(stories)


# ============================================================================
# Story 2.2 Task 4.8–4.16: Scheduling score tests
# ============================================================================


class TestSchedulingScores:
    """Test scheduling score computation."""

    def test_linear_chain_scoring(self) -> None:
        """Linear chain: A→B→C — A has highest score, C has lowest."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.2"]),
        ]
        graph = DependencyGraph(stories)
        scores = graph.scores
        assert scores["1.1"] > scores["1.2"] > scores["1.3"]

    def test_diamond_scoring(self) -> None:
        """Diamond: A←{B,C}←D — A has highest unblock_potential (3 transitive)."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.1"]),
            _story("1.4", deps=["Story 1.2", "Story 1.3"]),
        ]
        graph = DependencyGraph(stories)
        scores = graph.scores
        # 1.1 unblocks all 3 transitively
        assert scores["1.1"] > scores["1.2"]
        assert scores["1.1"] > scores["1.3"]
        assert scores["1.1"] > scores["1.4"]
        # Exact values: max_depth=2
        # 1.1: unblock=3, depth_score=2 (2-0), priority=5 → 3000+200+50=3250
        # 1.2: unblock=1, depth_score=1 (2-1), priority=5 → 1000+100+50=1150
        # 1.3: unblock=1, depth_score=1 (2-1), priority=5 → 1000+100+50=1150
        # 1.4: unblock=0, depth_score=0 (2-2), priority=5 → 0+0+50=50
        assert scores["1.1"] == 3250
        assert scores["1.2"] == 1150
        assert scores["1.3"] == 1150
        assert scores["1.4"] == 50

    def test_all_independent_scoring(self) -> None:
        """All independent stories have unblock_potential=0 and same score."""
        stories = [_story(f"1.{i}") for i in range(1, 4)]
        graph = DependencyGraph(stories)
        scores = graph.scores
        # All should have same score: unblock=0, depth_score=0, priority=5 → 50
        assert scores["1.1"] == scores["1.2"] == scores["1.3"]
        assert scores["1.1"] == 50

    def test_empty_graph_scoring(self) -> None:
        """Empty graph returns empty dict."""
        graph = DependencyGraph([])
        assert graph.scores == {}

    def test_single_story_scoring(self) -> None:
        """Single story: unblock=0, depth_score=0, priority=5 → score=50."""
        graph = DependencyGraph([_story("1.1")])
        assert graph.scores["1.1"] == 50

    def test_wide_fan_out_scoring(self) -> None:
        """One root with 5 direct dependents — root scores highest."""
        stories = [_story("1.1", deps=[])]
        for i in range(2, 7):
            stories.append(_story(f"1.{i}", deps=["Story 1.1"]))
        graph = DependencyGraph(stories)
        scores = graph.scores
        # Root unblocks 5 stories
        for i in range(2, 7):
            assert scores["1.1"] > scores[f"1.{i}"]

    def test_scoring_with_numeric_priority(self) -> None:
        """Explicit numeric priority='8' produces 10*8=80 in priority component."""
        stories = [_story("1.1", priority="8")]
        graph = DependencyGraph(stories)
        # unblock=0, depth_score=0, priority=8 → score = 10*8 = 80
        assert graph.scores["1.1"] == 80

    def test_scoring_with_non_numeric_priority(self) -> None:
        """Non-numeric priority='high' falls back to default 5 (10*5=50)."""
        stories = [_story("1.1", priority="high")]
        graph = DependencyGraph(stories)
        # unblock=0, depth_score=0, priority=5 → score = 50
        assert graph.scores["1.1"] == 50

    def test_scoring_with_empty_priority(self) -> None:
        """Empty priority='' falls back to default 5 (10*5=50)."""
        stories = [_story("1.1", priority="")]
        graph = DependencyGraph(stories)
        assert graph.scores["1.1"] == 50

    def test_score_of_accessor(self) -> None:
        """score_of() returns correct score for a story."""
        stories = [_story("1.1")]
        graph = DependencyGraph(stories)
        assert graph.score_of("1.1") == 50

    def test_score_of_unknown_raises(self) -> None:
        """score_of() raises KeyError for unknown story."""
        graph = DependencyGraph([_story("1.1")])
        with pytest.raises(KeyError):
            graph.score_of("9.9")

    def test_scores_property_returns_copy(self) -> None:
        """scores property returns a copy, not the internal dict."""
        graph = DependencyGraph([_story("1.1")])
        s1 = graph.scores
        s2 = graph.scores
        assert s1 == s2
        assert s1 is not s2

    def test_linear_chain_unblock_values(self) -> None:
        """Linear chain: A unblocks 2, B unblocks 1, C unblocks 0."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.2"]),
        ]
        graph = DependencyGraph(stories)
        scores = graph.scores
        # A: unblock=2, depth_score=2, priority=5 → 2000+200+50=2250
        # B: unblock=1, depth_score=1, priority=5 → 1000+100+50=1150
        # C: unblock=0, depth_score=0, priority=5 → 0+0+50=50
        assert scores["1.1"] == 2250
        assert scores["1.2"] == 1150
        assert scores["1.3"] == 50

    def test_scoring_formula_components(self) -> None:
        """Verify the formula: (1000 * unblock) + (100 * depth_score) + (10 * priority)."""
        # Single root with one dependent, priority=3
        stories = [
            _story("1.1", deps=[], priority="3"),
            _story("1.2", deps=["Story 1.1"], priority="7"),
        ]
        graph = DependencyGraph(stories)
        scores = graph.scores
        # 1.1: unblock=1, depth_score=1 (max_depth=1, depth=0), priority=3
        # → 1000*1 + 100*1 + 10*3 = 1130
        assert scores["1.1"] == 1130
        # 1.2: unblock=0, depth_score=0 (max_depth=1, depth=1), priority=7
        # → 1000*0 + 100*0 + 10*7 = 70
        assert scores["1.2"] == 70


# ============================================================================
# Story 2.3 Task 3.12–3.15: are_dependencies_satisfied() tests
# ============================================================================


class TestAreDependenciesSatisfied:
    """Test are_dependencies_satisfied() method."""

    def test_all_deps_satisfied(self) -> None:
        """Returns True when all dependencies are in done_ids."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["Story 3.1"]),
            _story("3.3", deps=["Story 3.1", "Story 3.2"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.are_dependencies_satisfied("3.3", {"3.1", "3.2"}) is True

    def test_dep_missing_returns_false(self) -> None:
        """Returns False when any dependency is missing from done_ids."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["Story 3.1"]),
            _story("3.3", deps=["Story 3.1", "Story 3.2"]),
        ]
        graph = DependencyGraph(stories)
        # 3.2 is missing from done_ids
        assert graph.are_dependencies_satisfied("3.3", {"3.1"}) is False

    def test_no_dependencies_vacuously_true(self) -> None:
        """Returns True for a story with no dependencies (vacuously satisfied)."""
        stories = [_story("3.1", deps=[])]
        graph = DependencyGraph(stories)
        assert graph.are_dependencies_satisfied("3.1", set()) is True

    def test_unknown_story_raises_key_error(self) -> None:
        """Raises KeyError for unknown story_id."""
        graph = DependencyGraph([_story("3.1")])
        with pytest.raises(KeyError, match="9.9"):
            graph.are_dependencies_satisfied("9.9", set())

    def test_single_dep_satisfied(self) -> None:
        """Returns True when the single dependency is in done_ids."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["Story 3.1"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.are_dependencies_satisfied("3.2", {"3.1"}) is True

    def test_single_dep_not_satisfied(self) -> None:
        """Returns False when the single dependency is NOT in done_ids."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["Story 3.1"]),
        ]
        graph = DependencyGraph(stories)
        assert graph.are_dependencies_satisfied("3.2", set()) is False


# ============================================================================
# Story 2.3 Task 3.1–3.11, 3.16–3.17: get_ready_stories() tests
# ============================================================================


class TestReadyStories:
    """Test get_ready_stories() method."""

    def test_basic_readiness_no_deps(self) -> None:
        """Stories with no deps and nothing done/in-flight/blocked are ready."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
            _story("3.3", deps=["Story 3.1"]),
        ]
        graph = DependencyGraph(stories)
        ready = graph.get_ready_stories(set(), set(), set())
        # 3.1 and 3.2 should be ready (no deps), 3.3 should NOT (dep 3.1 not done)
        assert "3.1" in ready
        assert "3.2" in ready
        assert "3.3" not in ready

    def test_score_based_ordering(self) -> None:
        """Higher-scored stories appear first in the returned list."""
        # 3.1 has a dependent (3.3), 3.2 does not — 3.1 should score higher
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
            _story("3.3", deps=["Story 3.1"]),
        ]
        graph = DependencyGraph(stories)
        ready = graph.get_ready_stories(set(), set(), set())
        # 3.1 unblocks 3.3 so has higher score → should be first
        # Only 3.1 and 3.2 are ready (3.3 dep not satisfied)
        assert len(ready) == 2
        assert ready[0] == "3.1"
        assert ready[1] == "3.2"
        assert "3.3" not in ready

    def test_dependency_satisfaction(self) -> None:
        """Story becomes ready only when ALL deps are in done_ids."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
            _story("3.3", deps=["Story 3.1", "Story 3.2"]),
        ]
        graph = DependencyGraph(stories)
        # Only 3.1 done — 3.3 still not ready (needs 3.2 too)
        ready = graph.get_ready_stories({"3.1"}, set(), set())
        assert "3.3" not in ready
        # Both done — 3.3 now ready
        ready = graph.get_ready_stories({"3.1", "3.2"}, set(), set())
        assert "3.3" in ready

    def test_re_evaluation_after_completion(self) -> None:
        """Calling with updated done_ids after completion returns newly-unblocked stories."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
            _story("3.3", deps=["Story 3.1"]),
        ]
        graph = DependencyGraph(stories)
        # Initially, 3.3 not ready
        ready1 = graph.get_ready_stories(set(), set(), set())
        assert "3.3" not in ready1

        # After 3.1 completes, 3.3 becomes ready
        ready2 = graph.get_ready_stories({"3.1"}, set(), set())
        assert "3.3" in ready2

    def test_in_flight_exclusion(self) -> None:
        """Stories in in_flight_ids are excluded even if deps are satisfied."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
        ]
        graph = DependencyGraph(stories)
        ready = graph.get_ready_stories(set(), {"3.2"}, set())
        assert "3.1" in ready
        assert "3.2" not in ready

    def test_blocked_exclusion(self) -> None:
        """Stories in blocked_ids are excluded."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
        ]
        graph = DependencyGraph(stories)
        ready = graph.get_ready_stories(set(), set(), {"3.1"})
        assert "3.1" not in ready
        assert "3.2" in ready

    def test_blocked_dependency_cascade(self) -> None:
        """If a dependency is blocked (not done), dependents are NOT ready.

        Uses a two-dep scenario so the blocked cascade is the distinguishing factor:
        3.4 depends on 3.1 (blocked) and 3.2 (done). Even though 3.2 is satisfied,
        3.4 should NOT be ready because 3.1 is blocked (not in done_ids).
        """
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
            _story("3.3", deps=[]),
            _story("3.4", deps=["Story 3.1", "Story 3.2"]),
        ]
        graph = DependencyGraph(stories)
        # 3.2 is done, 3.1 is blocked — 3.4 should NOT be ready
        ready = graph.get_ready_stories({"3.2"}, set(), {"3.1"})
        assert "3.4" not in ready  # blocked dep cascade
        assert "3.1" not in ready  # blocked itself
        assert "3.2" not in ready  # already done
        assert "3.3" in ready  # independent, not blocked/done

    def test_all_stories_done(self) -> None:
        """Returns empty list when all stories are in done_ids."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=["Story 3.1"]),
        ]
        graph = DependencyGraph(stories)
        ready = graph.get_ready_stories({"3.1", "3.2"}, set(), set())
        assert ready == []

    def test_empty_graph(self) -> None:
        """Returns empty list for an empty DependencyGraph."""
        graph = DependencyGraph([])
        ready = graph.get_ready_stories(set(), set(), set())
        assert ready == []

    def test_single_story_no_deps(self) -> None:
        """Single story with no deps is ready when not done/in-flight/blocked."""
        graph = DependencyGraph([_story("3.1")])
        ready = graph.get_ready_stories(set(), set(), set())
        assert ready == ["3.1"]

    def test_diamond_re_evaluation(self) -> None:
        """Leaf node becomes ready only when both parents are done."""
        stories = [
            _story("1.1", deps=[]),
            _story("1.2", deps=["Story 1.1"]),
            _story("1.3", deps=["Story 1.1"]),
            _story("1.4", deps=["Story 1.2", "Story 1.3"]),
        ]
        graph = DependencyGraph(stories)
        # Only 1.1 done — 1.2 and 1.3 ready, but not 1.4
        ready = graph.get_ready_stories({"1.1"}, set(), set())
        assert "1.2" in ready
        assert "1.3" in ready
        assert "1.4" not in ready

        # 1.2 done but not 1.3 — 1.4 still not ready
        ready = graph.get_ready_stories({"1.1", "1.2"}, set(), set())
        assert "1.4" not in ready

        # Both 1.2 and 1.3 done — 1.4 now ready
        ready = graph.get_ready_stories({"1.1", "1.2", "1.3"}, set(), set())
        assert "1.4" in ready

    def test_tiebreaker_ordering(self) -> None:
        """Stories with equal scores sorted by natural numeric order."""
        stories = [
            _story("3.3", deps=[]),
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
        ]
        graph = DependencyGraph(stories)
        ready = graph.get_ready_stories(set(), set(), set())
        # All independent → equal scores → tiebreaker = natural numeric order
        assert ready == ["3.1", "3.2", "3.3"]

    def test_tiebreaker_with_numeric_ids(self) -> None:
        """Natural numeric order: 1.2 before 1.10."""
        stories = [
            _story("1.10", deps=[]),
            _story("1.2", deps=[]),
            _story("1.1", deps=[]),
        ]
        graph = DependencyGraph(stories)
        ready = graph.get_ready_stories(set(), set(), set())
        assert ready == ["1.1", "1.2", "1.10"]

    def test_done_story_excluded(self) -> None:
        """Stories in done_ids are not returned as ready."""
        stories = [
            _story("3.1", deps=[]),
            _story("3.2", deps=[]),
        ]
        graph = DependencyGraph(stories)
        ready = graph.get_ready_stories({"3.1"}, set(), set())
        assert "3.1" not in ready
        assert "3.2" in ready

    def test_debug_logging(self, caplog: pytest.LogCaptureFixture) -> None:
        """get_ready_stories() produces debug-level log output."""
        stories = [_story("3.1", deps=[])]
        graph = DependencyGraph(stories)
        with caplog.at_level(logging.DEBUG):
            graph.get_ready_stories(set(), set(), set())
        assert "Ready stories" in caplog.text

