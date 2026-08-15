"""Tests for the CREATE_STORY handler's resume-path skip predicate.

The skip is authorised by two independent conditions, and both are load-bearing:

* a **resume path** (a story file on disk during a *fresh* run is a stale
  artifact from an earlier crashed run, not a completed one), and
* **structural validity** of the story file — never mere existence, which is
  the predicate that produced a story marked done with zero implementing
  commits.
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.core.config import get_config
from bmad_assist_lite.core.paths import init_paths
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.handlers.create_story import CreateStoryHandler
from bmad_assist_lite.loop.run_mode import set_resume_mode
from bmad_assist_lite.loop.story_paths import resolve_story_candidates, resolve_story_path
from bmad_assist_lite.loop.story_validity import check_story_reusable
from bmad_assist_lite.providers.base import ProviderResult

# ============================================================================
# Fixtures and builders
# ============================================================================


VALID_STORY = """# Story 1.2: Add the widget

Status: ready-for-dev

## Story

As a user,
I want a widget,
so that I can widget.

## Acceptance Criteria

1. The widget exists.

## Tasks / Subtasks

- [ ] Task 1 (AC: #1)
  - [ ] Subtask 1.1

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/` | **PENDING** |
| Tests | `pytest -q` | **PENDING** |
"""


def _write_story(stories_dir: Path, name: str, content: str) -> Path:
    stories_dir.mkdir(parents=True, exist_ok=True)
    path = stories_dir / name
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Initialise the paths singleton against an empty project tree."""
    init_paths(tmp_path)
    return tmp_path


@pytest.fixture
def stories_dir(project: Path) -> Path:
    from bmad_assist_lite.core.paths import get_paths

    d = get_paths().stories_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def state() -> State:
    return State(current_epic=1, current_story="1.2", current_phase=Phase.CREATE_STORY)


@pytest.fixture
def handler(project: Path) -> CreateStoryHandler:
    """A handler whose LLM path is stubbed so entering it is observable."""
    h = CreateStoryHandler(get_config(), project)
    h.render_prompt = MagicMock(return_value="prompt")  # type: ignore[method-assign]
    h.invoke_provider = MagicMock(  # type: ignore[method-assign]
        return_value=ProviderResult(
            stdout="created",
            stderr="",
            exit_code=0,
            duration_ms=1,
            model="opus",
            command="stub",
        )
    )
    return h


def _ran_llm(handler: CreateStoryHandler) -> bool:
    return bool(handler.invoke_provider.called)  # type: ignore[attr-defined]


# ============================================================================
# The resume condition (REQ-04.1 criteria 4, 4b, 4c — ADR-0008 §4)
# ============================================================================


class TestResumeCondition:
    """The skip is reachable only on a resume path."""

    def test_fresh_run_with_fully_valid_story_does_not_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """NEG, load-bearing (criterion 4c).

        A fully valid story file present on a *fresh* run is a stale artifact
        from an earlier crashed run, not a completed one. Trusting it is the
        hollow-story failure this predicate exists to prevent.
        """
        _write_story(stories_dir, "story-1.2.md", VALID_STORY)
        set_resume_mode(False)

        result = handler.execute(state)

        assert result.success
        assert _ran_llm(handler), "fresh run must re-create the story, not trust what is on disk"
        assert not result.outputs.get("skipped")

    def test_predicate_is_not_invoked_at_all_on_a_fresh_run(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """Criterion 4b — the resume condition is evaluated *before* the predicate."""
        _write_story(stories_dir, "story-1.2.md", VALID_STORY)
        set_resume_mode(False)

        with patch(
            "bmad_assist_lite.loop.handlers.create_story.check_story_reusable"
        ) as predicate:
            handler.execute(state)

        predicate.assert_not_called()

    def test_resume_with_fully_valid_story_skips(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """Criterion 4 — the same file on a resume path does skip."""
        path = _write_story(stories_dir, "story-1.2.md", VALID_STORY)
        set_resume_mode(True)

        result = handler.execute(state)

        assert result.success
        assert not _ran_llm(handler), "resume with a valid story must not re-run the LLM phase"
        assert result.outputs.get("skipped") is True
        assert result.outputs.get("story_path") == str(path)

    def test_resume_mode_does_not_leak_into_a_later_fresh_run(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """The run-scoped flag is a singleton; a fresh run must reset it."""
        _write_story(stories_dir, "story-1.2.md", VALID_STORY)
        set_resume_mode(True)
        set_resume_mode(False)

        handler.execute(state)

        assert _ran_llm(handler)


# ============================================================================
# Structural defects (REQ-04.1 criteria 1, 2, 6)
# ============================================================================


class TestStructuralDefectsBlockTheSkip:
    """Each individual structural defect refuses the skip, on a resume path."""

    @pytest.fixture(autouse=True)
    def _resume(self) -> None:
        set_resume_mode(True)

    def test_empty_file_does_not_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        _write_story(stories_dir, "story-1.2.md", "")

        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_whitespace_only_file_does_not_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        _write_story(stories_dir, "story-1.2.md", "   \n\n \t\n")

        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_title_only_file_does_not_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        _write_story(stories_dir, "story-1.2.md", "# Story 1.2: Add the widget\n")

        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_missing_required_heading_does_not_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """A story with everything except the Tasks / Subtasks heading."""
        content = VALID_STORY.replace(
            "## Tasks / Subtasks\n\n- [ ] Task 1 (AC: #1)\n  - [ ] Subtask 1.1\n", ""
        )
        _write_story(stories_dir, "story-1.2.md", content)

        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_missing_acceptance_criteria_does_not_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        content = VALID_STORY.replace("## Acceptance Criteria\n\n1. The widget exists.\n", "")
        _write_story(stories_dir, "story-1.2.md", content)

        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_empty_acceptance_criteria_section_does_not_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """The heading alone is not an acceptance-criteria section."""
        content = VALID_STORY.replace("1. The widget exists.\n", "")
        _write_story(stories_dir, "story-1.2.md", content)

        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_missing_quality_gates_table_does_not_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """X-04: gate commands are read from this table first.

        A story file without it does not merely look thin — it silently
        degrades the quality gate for that story.
        """
        content = VALID_STORY.split("## Quality Gates")[0] + "## Quality Gates\n\nTBD.\n"
        _write_story(stories_dir, "story-1.2.md", content)

        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_wrong_story_id_does_not_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """Criterion 3 — a file for a different story authorises nothing."""
        _write_story(
            stories_dir, "story-1.2.md", VALID_STORY.replace("# Story 1.2:", "# Story 3.9:")
        )

        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")


# ============================================================================
# The predicate collects every defect (criterion 6)
# ============================================================================


class TestDefectsAreCollectedTogether:
    """Mirrors compiler/context_filter.py's accumulate-then-report pattern."""

    def test_partial_story_reports_every_defect_not_just_the_first(
        self, stories_dir: Path
    ) -> None:
        _write_story(
            stories_dir,
            "story-1.2.md",
            "# Story 1.2: Add the widget\n\n## Acceptance Criteria\n",
        )

        verdict = check_story_reusable("1.2")

        assert not verdict.reusable
        blob = verdict.summary()
        assert "Acceptance Criteria" in blob, "empty AC section must be reported"
        assert "Tasks / Subtasks" in blob, "missing Tasks heading must be reported"
        assert "Quality Gates" in blob, "missing Quality Gates table must be reported"
        assert len(verdict.defects) >= 3, f"expected all defects, got {verdict.defects}"

    def test_summary_is_actionable_and_names_the_file(self, stories_dir: Path) -> None:
        path = _write_story(stories_dir, "story-1.2.md", "# Story 1.2: t\n")

        verdict = check_story_reusable("1.2")

        assert str(path) in verdict.summary()


# ============================================================================
# Path resolution (criterion 5) and inconclusive inputs (REQ-04.4 criterion 2)
# ============================================================================


class TestPathResolution:
    """The predicate uses the same resolution as quality_gate._resolve_story_path."""

    @pytest.fixture(autouse=True)
    def _resume(self) -> None:
        set_resume_mode(True)

    def test_primary_naming_form_is_recognised(self, stories_dir: Path) -> None:
        _write_story(stories_dir, "story-1.2.md", VALID_STORY)

        assert check_story_reusable("1.2").reusable

    def test_alternate_naming_form_is_recognised(self, stories_dir: Path) -> None:
        _write_story(stories_dir, "1-2-add-the-widget.md", VALID_STORY)

        assert check_story_reusable("1.2").reusable

    def test_alternate_form_is_anchored_and_does_not_over_match(
        self, stories_dir: Path
    ) -> None:
        """Story 1.2 must not resolve to 1-20-*.md (the T24 over-match trap)."""
        _write_story(stories_dir, "1-20-other-story.md", VALID_STORY)

        candidates = resolve_story_candidates("1.2", stories_dir)

        assert candidates == []

    def test_quality_gate_handler_shares_the_resolution(
        self, project: Path, state: State, stories_dir: Path
    ) -> None:
        """ADR-0008 §3 — one resolution, imported rather than duplicated."""
        from bmad_assist_lite.loop.handlers.quality_gate import QualityGateHandler

        path = _write_story(stories_dir, "story-1.2.md", VALID_STORY)
        qg = QualityGateHandler(get_config(), project)

        assert qg._resolve_story_path(state) == path
        assert resolve_story_path("1.2") == path

    def test_unresolvable_story_path_falls_through_to_running_the_phase(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """No story file at all — nothing to reuse."""
        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_ambiguous_story_path_falls_through_to_running_the_phase(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """Two alternate-form files match — inconclusive, so do the work."""
        _write_story(stories_dir, "1-2-add-the-widget.md", VALID_STORY)
        _write_story(stories_dir, "1-2-add-the-widget-v2.md", VALID_STORY)

        result = handler.execute(state)

        assert _ran_llm(handler), "ambiguity must fail toward doing the work"
        assert not result.outputs.get("skipped")

    def test_malformed_story_id_falls_through_to_running_the_phase(
        self, handler: CreateStoryHandler, stories_dir: Path
    ) -> None:
        bad = State(current_epic=1, current_story="nonsense", current_phase=Phase.CREATE_STORY)

        result = handler.execute(bad)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_unreadable_story_file_falls_through_to_running_the_phase(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        """An unreadable file is inconclusive, never a licence to skip."""
        _write_story(stories_dir, "story-1.2.md", VALID_STORY)

        with patch.object(Path, "read_text", side_effect=OSError("boom")):
            result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")

    def test_a_directory_named_like_a_story_does_not_authorise_a_skip(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        (stories_dir / "story-1.2.md").mkdir(parents=True)

        result = handler.execute(state)

        assert _ran_llm(handler)
        assert not result.outputs.get("skipped")


# ============================================================================
# The skip is visible (criterion 4 — console and record)
# ============================================================================


class TestTheSkipIsVisible:
    """A silent skip is indistinguishable from a phase that ran and did nothing."""

    @pytest.fixture(autouse=True)
    def _resume(self) -> None:
        set_resume_mode(True)

    def test_skip_is_logged_at_info_with_the_reason_and_the_file_path(
        self,
        handler: CreateStoryHandler,
        state: State,
        stories_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        path = _write_story(stories_dir, "story-1.2.md", VALID_STORY)

        with caplog.at_level(logging.INFO, logger="bmad_assist_lite.loop.handlers.create_story"):
            handler.execute(state)

        records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert records, "the skip must be logged at INFO"
        blob = " ".join(r.getMessage() for r in records)
        assert str(path) in blob
        assert "resume" in blob.lower()

    def test_skip_is_announced_on_the_console(
        self, handler: CreateStoryHandler, state: State, stories_dir: Path
    ) -> None:
        _write_story(stories_dir, "story-1.2.md", VALID_STORY)

        with patch(
            "bmad_assist_lite.loop.handlers.create_story.write_progress"
        ) as progress:
            handler.execute(state)

        written = " ".join(str(c.args[0]) for c in progress.call_args_list)
        assert "story-1.2.md" in written
        assert "skip" in written.lower() or "reus" in written.lower()

    def test_refusal_to_skip_records_why_at_info(
        self,
        handler: CreateStoryHandler,
        state: State,
        stories_dir: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A present-but-defective file must say so, not fail silently open."""
        _write_story(stories_dir, "story-1.2.md", "# Story 1.2: t\n")

        with caplog.at_level(logging.INFO, logger="bmad_assist_lite.loop.handlers.create_story"):
            handler.execute(state)

        blob = " ".join(r.getMessage() for r in caplog.records)
        assert "Quality Gates" in blob
