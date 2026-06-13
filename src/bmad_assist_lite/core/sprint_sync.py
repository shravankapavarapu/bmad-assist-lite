"""One-way state → sprint-status synchronization.

After each state save, the current state is projected onto sprint-status.yaml
to maintain a human-readable view of development progress.

This is non-fatal: sync errors are logged as warnings and never propagated.
"""

import logging
import os
from pathlib import Path

from bmad_assist_lite.core.sprint_status import (
    SprintStatus,
    get_sprint_status_path,
    load_sprint_status,
    save_sprint_status,
)
from bmad_assist_lite.core.state import Phase, State

logger = logging.getLogger(__name__)

# Maps workflow phases to sprint status strings
PHASE_TO_STATUS: dict[str, str] = {
    Phase.CREATE_STORY.value: "ready-for-dev",
    Phase.VALIDATE_STORY.value: "in-progress",
    Phase.VALIDATE_STORY_SYNTHESIS.value: "in-progress",
    Phase.DEV_STORY.value: "in-progress",
    Phase.CODE_REVIEW.value: "review",
    Phase.CODE_REVIEW_SYNTHESIS.value: "review",
    Phase.QUALITY_GATE.value: "review",
    Phase.FIX_QUALITY_GATE.value: "in-progress",
    Phase.EPIC_QUALITY_GATE.value: "review",
    Phase.RETROSPECTIVE.value: "done",
}


def sync_state_to_sprint(state: State, sprint_status: SprintStatus) -> SprintStatus:
    """Apply state changes to sprint status model (pure function).

    - Sets current story status based on current phase
    - Marks completed stories as done
    - Marks completed epics as done
    - Marks current epic as in-progress

    Args:
        state: Current loop state.
        sprint_status: Existing sprint status to update.

    Returns:
        Updated sprint status (same object, mutated).

    """
    # Mark completed stories as done
    for story_id in state.completed_stories:
        if not sprint_status.is_story_done(story_id):
            sprint_status.set_story_status(story_id, "done")

    # Mark failed QA stories as blocked
    for story_id in state.failed_qa_stories:
        sprint_status.set_story_status(story_id, "blocked")

    # Mark completed epics as done
    for epic_id in state.completed_epics:
        if not sprint_status.is_epic_done(epic_id):
            sprint_status.set_epic_status(epic_id, "done")

    # Set current story status based on phase
    if state.current_story and state.current_phase:
        phase_status = PHASE_TO_STATUS.get(state.current_phase.value, "in-progress")
        sprint_status.set_story_status(state.current_story, phase_status)

    # Mark current epic as in-progress (unless already done)
    if state.current_epic is not None and not sprint_status.is_epic_done(state.current_epic):
        sprint_status.set_epic_status(state.current_epic, "in-progress")

    return sprint_status


def trigger_sync(state: State, project_path: Path) -> None:
    """Convenience: load sprint status, sync, save. Never raises.

    Args:
        state: Current loop state.
        project_path: Path to the project root.

    """
    if os.environ.get("BMAD_PARALLEL_MODE") == "1":
        logger.debug("Sprint sync bypassed (BMAD_PARALLEL_MODE=1)")
        return

    try:
        ss_path = get_sprint_status_path(project_path)
        sprint_status = load_sprint_status(ss_path)
        sync_state_to_sprint(state, sprint_status)
        save_sprint_status(sprint_status, ss_path)
    except Exception as e:
        logger.warning("Sprint status sync failed (non-fatal): %s", e)
