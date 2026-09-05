"""Tests for state -> sprint-status synchronization."""


from bmad_assist_lite.core.sprint_status import (
    SprintStatus,
    get_sprint_status_path,
    load_sprint_status,
)
from bmad_assist_lite.core.sprint_sync import (
    PHASE_TO_STATUS,
    sync_state_to_sprint,
    trigger_sync,
)
from bmad_assist_lite.core.state import Phase, State


class TestPhaseMapping:
    """Tests for PHASE_TO_STATUS mapping."""

    def test_all_phases_mapped(self):
        """Every Phase enum value has a mapping."""
        for phase in Phase:
            assert phase.value in PHASE_TO_STATUS

    def test_retrospective_maps_to_done(self):
        """RETROSPECTIVE phase maps to done status."""
        assert PHASE_TO_STATUS[Phase.RETROSPECTIVE.value] == "done"

    def test_code_review_maps_to_review(self):
        """CODE_REVIEW phase maps to review status."""
        assert PHASE_TO_STATUS[Phase.CODE_REVIEW.value] == "review"

    def test_create_story_maps_to_ready_for_dev(self):
        """CREATE_STORY phase maps to ready-for-dev status."""
        assert PHASE_TO_STATUS[Phase.CREATE_STORY.value] == "ready-for-dev"


class TestSyncStateToSprint:
    """Tests for the pure sync function."""

    def test_sync_current_story_status(self):
        """Current story gets status from current phase."""
        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CODE_REVIEW,
        )
        ss = SprintStatus()
        sync_state_to_sprint(state, ss)
        assert ss.get_story_status("1.1") == "review"

    def test_sync_completed_stories(self):
        """Completed stories are marked as done."""
        state = State(
            current_epic=1,
            current_story="1.3",
            current_phase=Phase.CREATE_STORY,
            completed_stories=["1.1", "1.2"],
        )
        ss = SprintStatus()
        sync_state_to_sprint(state, ss)
        assert ss.is_story_done("1.1")
        assert ss.is_story_done("1.2")
        assert ss.get_story_status("1.3") == "ready-for-dev"

    def test_sync_completed_epics(self):
        """Completed epics are marked as done."""
        state = State(
            current_epic=2,
            current_story="2.1",
            current_phase=Phase.CREATE_STORY,
            completed_epics=[1],
        )
        ss = SprintStatus()
        sync_state_to_sprint(state, ss)
        assert ss.is_epic_done(1)
        assert ss.get_epic_status(2) == "in-progress"

    def test_sync_current_epic_in_progress(self):
        """Current epic is marked as in-progress if not done."""
        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.DEV_STORY,
        )
        ss = SprintStatus()
        sync_state_to_sprint(state, ss)
        assert ss.get_epic_status(1) == "in-progress"

    def test_sync_does_not_override_done_epic(self):
        """Sync does not change a done epic back to in-progress."""
        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.DEV_STORY,
            completed_epics=[1],
        )
        ss = SprintStatus()
        sync_state_to_sprint(state, ss)
        # Epic 1 is in completed_epics, so it should stay done
        assert ss.is_epic_done(1)


class TestTriggerSync:
    """Tests for the convenience trigger_sync function."""

    def test_trigger_sync_creates_file(self, tmp_path):
        """trigger_sync creates sprint-status.yaml."""
        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )
        trigger_sync(state, tmp_path)
        ss_path = get_sprint_status_path(tmp_path)
        assert ss_path.exists()

        loaded = load_sprint_status(ss_path)
        assert loaded.get_story_status("1.1") == "ready-for-dev"

    def test_trigger_sync_swallows_exceptions(self, tmp_path, monkeypatch):
        """trigger_sync never raises, even on errors."""

        def bad_save(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("bmad_assist_lite.core.sprint_sync.save_sprint_status", bad_save)

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )
        # Should not raise
        trigger_sync(state, tmp_path)


class TestDonePostconditions:
    """Review-owns-done: the sequential sync consults the verdict gate."""

    def test_gate_active_by_default_and_dissents_without_evidence(self, tmp_path):
        """The default loop runs code_review_synthesis, so the three-witness
        gate is on — and a story with no artifacts has not earned done."""
        from bmad_assist_lite.core.sprint_sync import _done_postconditions

        check = _done_postconditions(tmp_path)
        assert check is not None
        reason = check("3.1")
        assert reason is not None
        assert "no recorded review verdict" in reason

    def test_gate_off_when_loop_has_no_review_phase(self, tmp_path):
        """A loop that never reviews cannot produce the evidence; demanding
        it would withhold done for an unsatisfiable reason."""
        from bmad_assist_lite.core.config import load_config
        from bmad_assist_lite.core.sprint_sync import _done_postconditions

        load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "loop": {"story": ["create_story", "dev_story", "quality_gate"]},
            }
        )
        assert _done_postconditions(tmp_path) is None

    def test_sync_withholds_done_and_keeps_parked_status(self, tmp_path):
        """A completed story whose gate dissents stays at its last status —
        the parked `review` row the out-of-band pass picks up."""
        state = State(current_epic=3, completed_stories=["3.1"])
        sprint = SprintStatus(development_status={"3-1-parked": "review"})

        synced = sync_state_to_sprint(
            state, sprint, signoff_check=lambda sid: "verdict gate dissents"
        )

        assert synced.get_story_status("3.1") == "review"
