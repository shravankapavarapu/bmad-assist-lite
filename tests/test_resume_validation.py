"""Tests for resume validation against sprint-status.yaml."""


from bmad_assist_lite.core.resume_validation import (
    ResumeValidationResult,
    validate_resume_state,
)
from bmad_assist_lite.core.sprint_status import (
    SprintStatus,
    get_sprint_status_path,
    save_sprint_status,
)
from bmad_assist_lite.core.state import Phase, State

EPICS = [1, 2]
STORIES_FOR_EPIC = {1: ["1.1", "1.2", "1.3"], 2: ["2.1", "2.2"]}
PHASE_LIST = [
    "create_story",
    "validate_story",
    "validate_story_synthesis",
    "dev_story",
    "code_review",
    "code_review_synthesis",
]


def _save_sprint_status(tmp_path, ss):
    """Save sprint status to the correct path for tests."""
    ss_path = get_sprint_status_path(tmp_path)
    ss_path.parent.mkdir(parents=True, exist_ok=True)
    save_sprint_status(ss, ss_path)


class TestResumeValidation:
    """Tests for validate_resume_state."""

    def test_no_sprint_file(self, tmp_path):
        """No sprint-status.yaml means no adjustments."""
        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )
        result = validate_resume_state(state, tmp_path, EPICS, STORIES_FOR_EPIC, PHASE_LIST)
        assert not result.advanced
        assert result.state.current_story == "1.1"

    def test_story_skip(self, tmp_path):
        """Done story is skipped to next story in same epic."""
        ss = SprintStatus()
        ss.set_story_status("1.1", "done")
        _save_sprint_status(tmp_path, ss)

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.DEV_STORY,
        )
        result = validate_resume_state(state, tmp_path, EPICS, STORIES_FOR_EPIC, PHASE_LIST)
        assert result.advanced
        assert result.state.current_story == "1.2"
        assert result.state.current_phase == Phase.CREATE_STORY
        assert "1.1" in result.stories_skipped

    def test_epic_skip(self, tmp_path):
        """When all stories in an epic are done, skip to next epic."""
        ss = SprintStatus()
        ss.set_story_status("1.1", "done")
        ss.set_story_status("1.2", "done")
        ss.set_story_status("1.3", "done")
        _save_sprint_status(tmp_path, ss)

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )
        result = validate_resume_state(state, tmp_path, EPICS, STORIES_FOR_EPIC, PHASE_LIST)
        assert result.advanced
        assert result.state.current_epic == 2
        assert result.state.current_story == "2.1"
        assert 1 in result.epics_skipped

    def test_all_done(self, tmp_path):
        """When all stories in all epics are done, project is complete."""
        ss = SprintStatus()
        for s in ["1.1", "1.2", "1.3", "2.1", "2.2"]:
            ss.set_story_status(s, "done")
        _save_sprint_status(tmp_path, ss)

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )
        result = validate_resume_state(state, tmp_path, EPICS, STORIES_FOR_EPIC, PHASE_LIST)
        assert result.project_complete
        assert result.advanced

    def test_retrospective_safety(self, tmp_path):
        """Never advance past RETROSPECTIVE phase."""
        ss = SprintStatus()
        ss.set_story_status("1.1", "done")
        _save_sprint_status(tmp_path, ss)

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.RETROSPECTIVE,
        )
        result = validate_resume_state(state, tmp_path, EPICS, STORIES_FOR_EPIC, PHASE_LIST)
        # Should NOT advance past retrospective
        assert not result.advanced
        assert result.state.current_phase == Phase.RETROSPECTIVE

    def test_no_current_epic(self, tmp_path):
        """None epic/story means no adjustments."""
        state = State(current_epic=None, current_story=None, current_phase=None)
        result = validate_resume_state(state, tmp_path, EPICS, STORIES_FOR_EPIC, PHASE_LIST)
        assert not result.advanced

    def test_summary_format(self, tmp_path):
        """Summary returns human-readable text."""
        ss = SprintStatus()
        ss.set_story_status("1.1", "done")
        _save_sprint_status(tmp_path, ss)

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )
        result = validate_resume_state(state, tmp_path, EPICS, STORIES_FOR_EPIC, PHASE_LIST)
        summary = result.summary()
        assert "1.1" in summary
        assert "Skipped" in summary

    def test_summary_no_adjustments(self, tmp_path):
        """Summary for no adjustments."""
        result = ResumeValidationResult(
            state=State(
                current_epic=1,
                current_story="1.1",
                current_phase=Phase.CREATE_STORY,
            )
        )
        assert result.summary() == "No adjustments needed"
