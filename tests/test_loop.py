"""Tests for loop types, transitions, and signal handling.

Covers: PhaseResult, LoopExitReason, advance_story, advance_epic,
        shutdown signals.
"""


from datetime import UTC

from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.signals import request_shutdown, reset_shutdown, shutdown_requested
from bmad_assist_lite.loop.transitions import advance_epic, advance_story
from bmad_assist_lite.loop.types import LoopExitReason, PhaseResult

# ---------------------------------------------------------------------------
# PhaseResult
# ---------------------------------------------------------------------------


class TestPhaseResult:
    """Tests for the PhaseResult dataclass factory methods."""

    def test_phase_result_ok(self):
        """PhaseResult.ok() creates a successful result with outputs."""
        result = PhaseResult.ok({"key": "val"})
        assert result.success is True
        assert result.error is None
        assert result.outputs == {"key": "val"}

    def test_phase_result_ok_no_outputs(self):
        """PhaseResult.ok() with no arguments has empty outputs."""
        result = PhaseResult.ok()
        assert result.success is True
        assert result.outputs == {}

    def test_phase_result_fail(self):
        """PhaseResult.fail() creates a failed result with error message."""
        result = PhaseResult.fail("err")
        assert result.success is False
        assert result.error == "err"
        assert result.outputs == {}


# ---------------------------------------------------------------------------
# LoopExitReason
# ---------------------------------------------------------------------------


class TestLoopExitReason:
    """Tests for the LoopExitReason enum."""

    def test_loop_exit_reason_values(self):
        """LoopExitReason has 4 members, one per distinct end of a run."""
        members = list(LoopExitReason)
        assert len(members) == 4
        assert LoopExitReason.COMPLETED in members
        assert LoopExitReason.INTERRUPTED in members
        assert LoopExitReason.ERROR in members
        assert LoopExitReason.BUDGET_EXHAUSTED in members

    def test_loop_exit_reason_string_values(self):
        """String values match expected lowercase names."""
        assert LoopExitReason.COMPLETED.value == "completed"
        assert LoopExitReason.INTERRUPTED.value == "interrupted"
        assert LoopExitReason.ERROR.value == "error"
        assert LoopExitReason.BUDGET_EXHAUSTED.value == "budget_exhausted"


# ---------------------------------------------------------------------------
# advance_story
# ---------------------------------------------------------------------------

# Standard phase list used by the loop
PHASE_LIST = [
    "create_story",
    "validate_story",
    "validate_story_synthesis",
    "dev_story",
    "code_review",
    "code_review_synthesis",
]

STORIES = ["1.1", "1.2", "1.3"]


class TestAdvanceStory:
    """Tests for story-level phase advancement."""

    def test_advance_story_next_phase(self):
        """Advancing from create_story moves to validate_story."""
        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )
        new_state = advance_story(state, PHASE_LIST, STORIES)

        assert new_state.current_phase == Phase.VALIDATE_STORY
        assert new_state.current_story == "1.1"  # Story unchanged

    def test_advance_story_next_story(self):
        """When at last phase, advances to next story with first phase."""
        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CODE_REVIEW_SYNTHESIS,  # Last in PHASE_LIST
        )
        new_state = advance_story(state, PHASE_LIST, STORIES)

        assert new_state.current_story == "1.2"
        assert new_state.current_phase == Phase.CREATE_STORY

    def test_advance_story_all_done(self):
        """When at last story and last phase, returns same state (no change)."""
        state = State(
            current_epic=1,
            current_story="1.3",  # Last story
            current_phase=Phase.CODE_REVIEW_SYNTHESIS,  # Last phase
        )
        new_state = advance_story(state, PHASE_LIST, STORIES)

        # State is returned as-is when all stories are done
        assert new_state.current_story == "1.3"
        assert new_state.current_phase == Phase.CODE_REVIEW_SYNTHESIS

    def test_advance_story_no_current_phase(self):
        """If current_phase is None, returns state unchanged."""
        state = State(current_epic=1, current_story="1.1", current_phase=None)
        new_state = advance_story(state, PHASE_LIST, STORIES)
        assert new_state.current_phase is None


# ---------------------------------------------------------------------------
# advance_epic
# ---------------------------------------------------------------------------

EPICS = [1, 2, 3]
STORIES_FOR_EPIC = {
    1: ["1.1", "1.2"],
    2: ["2.1", "2.2", "2.3"],
    3: ["3.1"],
}


class TestAdvanceEpic:
    """Tests for epic-level advancement."""

    def test_advance_epic_next_epic(self):
        """Advancing from epic 1 moves to epic 2 with first story and first phase."""
        state = State(
            current_epic=1,
            current_story="1.2",
            current_phase=Phase.CODE_REVIEW_SYNTHESIS,
        )
        new_state = advance_epic(state, EPICS, STORIES_FOR_EPIC, PHASE_LIST)

        assert new_state is not None
        assert new_state.current_epic == 2
        assert new_state.current_story == "2.1"
        assert new_state.current_phase == Phase.CREATE_STORY

    def test_advance_epic_all_done(self):
        """Returns None when all epics are completed."""
        state = State(
            current_epic=3,  # Last epic
            current_story="3.1",
            current_phase=Phase.CODE_REVIEW_SYNTHESIS,
        )
        result = advance_epic(state, EPICS, STORIES_FOR_EPIC, PHASE_LIST)
        assert result is None

    def test_advance_epic_no_current_epic(self):
        """Returns None when current_epic is None."""
        state = State(current_epic=None, current_story=None, current_phase=None)
        result = advance_epic(state, EPICS, STORIES_FOR_EPIC, PHASE_LIST)
        assert result is None

    def test_advance_epic_preserves_completed_lists(self):
        """advance_epic preserves completed_stories and completed_epics."""
        state = State(
            current_epic=1,
            current_story="1.2",
            current_phase=Phase.CODE_REVIEW_SYNTHESIS,
            completed_stories=["1.1", "1.2"],
            completed_epics=[],
        )
        new_state = advance_epic(state, EPICS, STORIES_FOR_EPIC, PHASE_LIST)

        assert new_state is not None
        assert new_state.completed_stories == ["1.1", "1.2"]
        assert 1 in new_state.completed_epics

    def test_advance_epic_preserves_timing(self):
        """advance_epic preserves timing fields from original state."""
        from datetime import datetime

        started = datetime(2025, 1, 1, tzinfo=UTC).replace(tzinfo=None)
        state = State(
            current_epic=1,
            current_story="1.2",
            current_phase=Phase.CODE_REVIEW_SYNTHESIS,
            completed_stories=["1.1"],
            started_at=started,
        )
        new_state = advance_epic(state, EPICS, STORIES_FOR_EPIC, PHASE_LIST)

        assert new_state is not None
        assert new_state.started_at == started
        assert new_state.completed_stories == ["1.1"]


# ---------------------------------------------------------------------------
# Shutdown signals
# ---------------------------------------------------------------------------


class TestShutdownSignals:
    """Tests for shutdown signal request/check/reset cycle."""

    def test_shutdown_signal_initial_state(self):
        """Shutdown is not requested initially (after reset)."""
        reset_shutdown()
        assert shutdown_requested() is False

    def test_shutdown_signal_request_and_check(self):
        """request_shutdown sets the shutdown flag."""
        reset_shutdown()
        assert shutdown_requested() is False

        request_shutdown(2)  # SIGINT = 2
        assert shutdown_requested() is True

    def test_shutdown_signal_reset(self):
        """reset_shutdown clears the shutdown flag."""
        reset_shutdown()
        request_shutdown(2)
        assert shutdown_requested() is True

        reset_shutdown()
        assert shutdown_requested() is False

    def test_shutdown_signal_full_cycle(self):
        """Full cycle: reset -> not requested -> request -> requested -> reset -> not requested."""
        reset_shutdown()
        assert shutdown_requested() is False

        request_shutdown(2)
        assert shutdown_requested() is True

        reset_shutdown()
        assert shutdown_requested() is False
