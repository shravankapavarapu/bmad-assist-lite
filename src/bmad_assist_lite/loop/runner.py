"""Main BMAD loop orchestration."""

import logging
from pathlib import Path

from bmad_assist_lite.core.config import Config
from bmad_assist_lite.core.state import Phase, State, get_state_path, load_state, save_state
from bmad_assist_lite.loop.dispatch import execute_phase, init_handlers
from bmad_assist_lite.loop.locking import running_lock
from bmad_assist_lite.loop.signals import (
    register_signal_handlers,
    reset_shutdown,
    shutdown_requested,
    unregister_signal_handlers,
)
from bmad_assist_lite.core.sprint_sync import trigger_sync
from bmad_assist_lite.loop.cleanup import cleanup_for_phase
from bmad_assist_lite.loop.transitions import advance_epic, advance_story
from bmad_assist_lite.loop.types import LoopExitReason

logger = logging.getLogger(__name__)

__all__ = ["run_loop"]


def _print_phase_banner(phase_name: str, epic: int | str | None, story: str | None) -> None:
    """Print phase banner to console."""
    banner = f" [{phase_name.upper().replace('_', ' ')}]"
    if epic is not None:
        banner += f" Epic {epic}"
    if story is not None:
        banner += f" Story {story}"

    separator = "\u2501" * 45
    print(f"\n{separator}")
    print(banner)
    print(separator)


def run_loop(
    config: Config,
    project_path: Path,
    epics: list[int],
    stories_for_epic: dict[int, list[str]],
    resume_state: State | None = None,
) -> LoopExitReason:
    """Run the main BMAD development loop.

    Args:
        config: Application configuration.
        project_path: Path to the project root.
        epics: List of epic numbers to process.
        stories_for_epic: Mapping of epic number to story IDs.
        resume_state: Optional state to resume from.

    Returns:
        LoopExitReason indicating how the loop ended.
    """
    # Get phase configuration
    story_phases = config.loop.story
    epic_teardown = config.loop.epic_teardown

    # Initialize
    reset_shutdown()
    register_signal_handlers()

    state_path = get_state_path(project_path)

    try:
        with running_lock(project_path):
            # Initialize handlers
            init_handlers(config, project_path)

            # Set up initial state
            if resume_state is not None:
                state = resume_state
                logger.info(
                    "Resuming from state: epic=%s story=%s phase=%s",
                    state.current_epic,
                    state.current_story,
                    state.current_phase,
                )
                if state.current_phase is not None:
                    cleaned = cleanup_for_phase(state.current_phase, project_path)
                    if cleaned:
                        logger.info("Cleaned %d temp files on resume", len(cleaned))
            elif epics and stories_for_epic:
                first_epic = epics[0]
                first_stories = stories_for_epic.get(first_epic, [])
                if not first_stories:
                    logger.error("No stories found for epic %s", first_epic)
                    return LoopExitReason.ERROR

                first_phase = Phase(story_phases[0])
                state = State(
                    current_epic=first_epic,
                    current_story=first_stories[0],
                    current_phase=first_phase,
                )
            else:
                logger.error("No epics or stories to process")
                return LoopExitReason.ERROR

            # Main loop
            while True:
                # Check for shutdown
                if shutdown_requested():
                    logger.info("Shutdown requested, saving state and exiting")
                    save_state(state, state_path)
                    trigger_sync(state, project_path)
                    return LoopExitReason.INTERRUPTED

                if state.current_phase is None:
                    break

                # Print phase banner
                _print_phase_banner(
                    state.current_phase.value,
                    state.current_epic,
                    state.current_story,
                )

                # Execute phase
                result = execute_phase(state)

                # Save state after each phase
                save_state(state, state_path)
                trigger_sync(state, project_path)

                if not result.success:
                    logger.error(
                        "Phase %s failed: %s",
                        state.current_phase.value if state.current_phase else "unknown",
                        result.error,
                    )
                    return LoopExitReason.ERROR

                # Check for phase override
                if result.next_phase is not None:
                    state = state.with_phase(result.next_phase)
                    continue

                # Check if current phase is in epic_teardown
                current_phase_name = (
                    state.current_phase.value if state.current_phase else ""
                )
                if current_phase_name in epic_teardown:
                    # Epic teardown phase - advance to next epic
                    idx = epic_teardown.index(current_phase_name)
                    if idx + 1 < len(epic_teardown):
                        state = state.with_phase(Phase(epic_teardown[idx + 1]))
                        continue

                    # All teardown phases done - advance epic
                    next_state = advance_epic(
                        state, epics, stories_for_epic, story_phases
                    )
                    if next_state is None:
                        logger.info("All epics completed!")
                        return LoopExitReason.COMPLETED
                    state = next_state
                    continue

                # Normal story phase advancement
                stories = stories_for_epic.get(state.current_epic or 0, [])
                new_state = advance_story(state, story_phases, stories)

                if (
                    new_state.current_phase == state.current_phase
                    and new_state.current_story == state.current_story
                ):
                    # Story loop completed - start epic teardown
                    if epic_teardown:
                        state = state.with_phase(Phase(epic_teardown[0]))
                    else:
                        # No teardown - advance epic directly
                        next_state = advance_epic(
                            state, epics, stories_for_epic, story_phases
                        )
                        if next_state is None:
                            logger.info("All epics completed!")
                            return LoopExitReason.COMPLETED
                        state = next_state
                else:
                    state = new_state

            logger.info("Loop completed")
            return LoopExitReason.COMPLETED

    except Exception as e:
        logger.error("Loop crashed: %s", e, exc_info=True)
        return LoopExitReason.ERROR

    finally:
        unregister_signal_handlers()
