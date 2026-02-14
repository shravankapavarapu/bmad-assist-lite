"""Story and epic transition logic."""

import logging

from bmad_assist_lite.core.state import Phase, State

logger = logging.getLogger(__name__)

__all__ = ["advance_story", "advance_epic"]


def advance_story(
    state: State,
    phase_list: list[str],
    stories: list[str],
) -> State:
    """Advance to the next phase or next story.

    Args:
        state: Current state.
        phase_list: Ordered list of phase names for story loop.
        stories: List of story IDs in current epic.

    Returns:
        Updated state with next phase or next story.
    """
    current_phase = state.current_phase
    if current_phase is None:
        return state

    current_phase_name = current_phase.value

    # Find current phase in list
    if current_phase_name in phase_list:
        idx = phase_list.index(current_phase_name)
        if idx + 1 < len(phase_list):
            # More phases in story loop
            next_phase = Phase(phase_list[idx + 1])
            logger.info("Advancing to next phase: %s", next_phase.value)
            return state.with_phase(next_phase)

    # Story loop completed - advance to next story
    current_story = state.current_story
    if current_story and current_story in stories:
        story_idx = stories.index(current_story)
        if story_idx + 1 < len(stories):
            next_story = stories[story_idx + 1]
            first_phase = Phase(phase_list[0])
            logger.info("Story %s completed. Advancing to story %s", current_story, next_story)
            return state.with_story(next_story).with_phase(first_phase)

    # All stories completed
    logger.info("All stories in epic %s completed", state.current_epic)
    return state


def advance_epic(
    state: State,
    epics: list[int],
    stories_for_epic: dict[int, list[str]],
    phase_list: list[str],
) -> State | None:
    """Advance to the next epic or return None if all done.

    Args:
        state: Current state.
        epics: List of epic numbers.
        stories_for_epic: Mapping of epic number to story IDs.
        phase_list: Ordered phase list.

    Returns:
        Updated state or None if all epics completed.
    """
    current_epic = state.current_epic
    if current_epic is None:
        return None

    if current_epic is not None and current_epic in epics:
        epic_idx = epics.index(int(current_epic))
        if epic_idx + 1 < len(epics):
            next_epic = epics[epic_idx + 1]
            next_stories = stories_for_epic.get(next_epic, [])
            if next_stories:
                first_phase = Phase(phase_list[0])
                logger.info("Epic %s completed. Advancing to epic %s", current_epic, next_epic)
                completed = list(state.completed_epics)
                if current_epic not in completed:
                    completed.append(current_epic)
                return state.model_copy(
                    update={
                        "current_epic": next_epic,
                        "current_story": next_stories[0],
                        "current_phase": first_phase,
                        "completed_epics": completed,
                    }
                )

    return None
