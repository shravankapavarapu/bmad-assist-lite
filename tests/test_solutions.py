"""The compounding solutions store: bounded, deduplicated, and ours.

A loop that re-derives the same fix every epic pays for it every epic. The store
keeps what was already solved so a later story can consult it.

Two constraints shape the design, and both come from the transcripts rather than
from taste:

* **Bounded.** The warning is that this file bloats. Every field is capped at
  construction, the store is capped in record count, and the injected block is
  capped in characters. None of those is advisory.
* **Ours only.** Records summarise this tool's own structured artifacts. Raw
  provider transcripts are never stored — Roo shipped a cheap condensing model
  over tool-call-heavy history and reverted it, because that history does not
  summarise across model families.
"""

from datetime import datetime
from pathlib import Path

import pytest

from bmad_assist_lite.core.solutions import (
    MAX_FIELD_CHARS,
    SolutionRecord,
    load_solutions,
    render_context_block,
    select_for_tags,
    solutions_dir,
    to_markdown,
    write_solution,
)


def _record(slug: str = "flaky-import", **over: object) -> SolutionRecord:
    fields: dict[str, object] = {
        "slug": slug,
        "tags": ("dev_story", "epic-1"),
        "category": "quality-gate",
        "symptom": "mypy reports an untyped decorator on every handler",
        "root_cause": "the decorator returned Any",
        "fix": "annotate the decorator's return type",
        "prevention": "strict mypy already catches this when the stub is typed",
        "story_id": "1.1",
        "timestamp": datetime(2026, 8, 11, 12, 0, 0),
    }
    fields.update(over)
    return SolutionRecord(**fields)  # type: ignore[arg-type]


class TestRecordShape:
    """The record is frozen, and bounded at construction."""

    def test_is_frozen(self) -> None:
        with pytest.raises(Exception):
            _record().fix = "other"  # type: ignore[misc]

    def test_long_fields_are_truncated_not_stored_whole(self) -> None:
        """LOAD-BEARING: a transcript pasted into a field cannot bloat the store."""
        record = _record(symptom="x" * (MAX_FIELD_CHARS * 10))
        assert len(record.symptom) <= MAX_FIELD_CHARS + 1

    def test_tags_are_bounded_and_normalised(self) -> None:
        record = _record(tags=tuple(f"Tag{i}" for i in range(50)))
        assert len(record.tags) <= 8
        assert all(t == t.lower() for t in record.tags)

    def test_fingerprint_ignores_slug_and_timestamp(self) -> None:
        """Dedup keys on the problem, not on when or what it was called."""
        a = _record(slug="one", timestamp=datetime(2026, 1, 1))
        b = _record(slug="two", timestamp=datetime(2026, 9, 9))
        assert a.fingerprint == b.fingerprint

    def test_fingerprint_differs_on_a_different_fix(self) -> None:
        assert _record().fingerprint != _record(fix="something else").fingerprint


class TestPersistence:
    """Markdown with YAML frontmatter, written atomically."""

    def test_round_trips_through_markdown(self, tmp_path: Path) -> None:
        record = _record()
        write_solution(record, tmp_path)
        loaded = load_solutions(tmp_path)
        assert len(loaded) == 1
        assert loaded[0].fix == record.fix
        assert loaded[0].tags == record.tags

    def test_writes_yaml_frontmatter(self) -> None:
        text = to_markdown(_record())
        assert text.startswith("---\n")
        assert "tags:" in text

    def test_leaves_no_temp_file(self, tmp_path: Path) -> None:
        write_solution(_record(), tmp_path)
        assert not list(solutions_dir(tmp_path).glob("*.tmp"))

    def test_a_duplicate_is_not_written_twice(self, tmp_path: Path) -> None:
        """LOAD-BEARING: dedup is what keeps the store from growing per run."""
        assert write_solution(_record(slug="first"), tmp_path) is not None
        assert write_solution(_record(slug="second"), tmp_path) is None
        assert len(load_solutions(tmp_path)) == 1

    def test_the_store_is_capped(self, tmp_path: Path) -> None:
        """LOAD-BEARING: the record count is bounded, oldest evicted first."""
        for i in range(10):
            write_solution(
                _record(slug=f"s{i}", fix=f"fix number {i}", timestamp=datetime(2026, 1, 1 + i)),
                tmp_path,
                max_records=4,
            )
        remaining = load_solutions(tmp_path)
        assert len(remaining) == 4
        assert min(r.timestamp for r in remaining) == datetime(2026, 1, 7)

    def test_an_unreadable_file_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        write_solution(_record(), tmp_path)
        (solutions_dir(tmp_path) / "broken.md").write_text("not frontmatter\n")
        assert len(load_solutions(tmp_path)) == 1

    def test_missing_store_reads_as_empty(self, tmp_path: Path) -> None:
        assert load_solutions(tmp_path) == []


class TestSelection:
    """Tag match, bounded, most useful first."""

    def test_selects_only_records_sharing_a_tag(self) -> None:
        a = _record(slug="a", tags=("dev_story",), fix="a")
        b = _record(slug="b", tags=("retrospective",), fix="b")
        assert select_for_tags([a, b], {"dev_story"}, limit=5) == [a]

    def test_no_tag_overlap_selects_nothing(self) -> None:
        """NEG — an unrelated store must not be injected into every phase."""
        a = _record(tags=("retrospective",))
        assert select_for_tags([a], {"dev_story"}, limit=5) == []

    def test_more_overlap_ranks_higher(self) -> None:
        broad = _record(slug="broad", tags=("dev_story", "epic-1"), fix="broad")
        narrow = _record(slug="narrow", tags=("dev_story",), fix="narrow")
        ranked = select_for_tags([narrow, broad], {"dev_story", "epic-1"}, limit=5)
        assert ranked[0] is broad

    def test_selection_is_limited(self) -> None:
        records = [_record(slug=f"s{i}", fix=f"f{i}") for i in range(20)]
        assert len(select_for_tags(records, {"dev_story"}, limit=3)) == 3


class TestInjection:
    """The injected block is capped in size, whatever the store holds."""

    def test_block_is_character_bounded(self) -> None:
        """LOAD-BEARING: the store may grow; what reaches a prompt may not."""
        records = [_record(slug=f"s{i}", fix=f"fix {i} " + "y" * 400) for i in range(50)]
        block = render_context_block(records, max_chars=500)
        assert len(block) <= 500

    def test_empty_store_renders_nothing(self) -> None:
        assert render_context_block([], max_chars=500) == ""

    def test_block_carries_the_fix(self) -> None:
        block = render_context_block([_record()], max_chars=2000)
        assert "annotate the decorator's return type" in block


class TestConfigAndWiring:
    """Opt-in and additive (G8), and reachable from the phases that benefit."""

    def test_disabled_by_default(self) -> None:
        from bmad_assist_lite.core.config import load_config

        config = load_config(
            {"providers": {"master": {"provider": "claude", "model": "opus"}}}
        )
        assert config.solutions.enabled is False

    def test_can_be_enabled(self) -> None:
        from bmad_assist_lite.core.config import load_config

        config = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "solutions": {"enabled": True, "max_injected": 2},
            }
        )
        assert config.solutions.enabled is True
        assert config.solutions.max_injected == 2

    def test_dev_story_context_is_empty_when_disabled(self, tmp_path: Path) -> None:
        """NEG — the default path does no store work and injects nothing."""
        from bmad_assist_lite.core.config import load_config
        from bmad_assist_lite.core.state import State
        from bmad_assist_lite.loop.handlers.dev_story import DevStoryHandler

        config = load_config(
            {"providers": {"master": {"provider": "claude", "model": "opus"}}}
        )
        handler = DevStoryHandler(config, tmp_path)
        context = handler.build_context(State(current_epic=1, current_story="1.1"))
        assert context["solutions"] == ""

    def test_dev_story_context_injects_matching_solutions(self, tmp_path: Path) -> None:
        """The payoff: a solved problem reaches the phase that would re-derive it."""
        from bmad_assist_lite.core.config import load_config
        from bmad_assist_lite.core.state import State
        from bmad_assist_lite.loop.handlers.dev_story import DevStoryHandler

        write_solution(_record(tags=("dev_story", "epic-1")), tmp_path)
        config = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "solutions": {"enabled": True},
            }
        )
        handler = DevStoryHandler(config, tmp_path)
        context = handler.build_context(State(current_epic=1, current_story="1.1"))
        assert "annotate the decorator's return type" in context["solutions"]
