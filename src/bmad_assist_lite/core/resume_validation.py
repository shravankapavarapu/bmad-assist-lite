"""Resume validation against sprint-status.yaml.

When resuming from saved state, validates that the current story/epic
hasn't been marked as done in sprint-status.yaml. If so, advances
the state past completed work.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from bmad_assist_lite.core.sprint_status import (
    get_sprint_status_path,
    load_sprint_status,
)
from bmad_assist_lite.core.state import Phase, State

logger = logging.getLogger(__name__)

# Maximum iterations to prevent infinite loops
_MAX_ITERATIONS = 500


@dataclass
class ResumeValidationResult:
    """Result of resume state validation."""

    state: State
    stories_skipped: list[str] = field(default_factory=list)
    epics_skipped: list[int] = field(default_factory=list)
    advanced: bool = False
    project_complete: bool = False

    def summary(self) -> str:
        """Human-readable summary of validation actions."""
        parts: list[str] = []
        if self.stories_skipped:
            parts.append(
                f"Skipped {len(self.stories_skipped)} done stories: "
                f"{', '.join(self.stories_skipped)}"
            )
        if self.epics_skipped:
            parts.append(
                f"Skipped {len(self.epics_skipped)} done epics: "
                f"{', '.join(str(e) for e in self.epics_skipped)}"
            )
        if self.project_complete:
            parts.append("All remaining work is complete")
        if not parts:
            return "No adjustments needed"
        return "; ".join(parts)


def validate_resume_state(
    state: State,
    project_path: Path,
    epics: list[int],
    stories_for_epic: dict[int, list[str]],
    phase_list: list[str],
) -> ResumeValidationResult:
    """Validate and potentially advance resume state past done work.

    Checks sprint-status.yaml for stories/epics that were marked done
    (e.g., by manual editing or a previous partial run). If the current
    position is done, advances to the next undone story/epic.

    Safety: NEVER advances past Phase.RETROSPECTIVE.
    Iteration limit: prevents infinite loop on corrupted data.

    Args:
        state: Resume state from state.yaml.
        project_path: Project root path.
        epics: Ordered list of epic numbers.
        stories_for_epic: Mapping of epic number to story IDs.
        phase_list: Ordered phase list for story loop.

    Returns:
        ResumeValidationResult with potentially advanced state.

    """
    result = ResumeValidationResult(state=state)

    ss_path = get_sprint_status_path(project_path)
    sprint_status = load_sprint_status(ss_path)

    if not sprint_status.development_status:
        return result

    iterations = 0
    current = state

    while iterations < _MAX_ITERATIONS:
        iterations += 1

        # Safety: never advance past RETROSPECTIVE
        if current.current_phase == Phase.RETROSPECTIVE:
            break

        current_epic = current.current_epic
        current_story = current.current_story

        if current_epic is None or current_story is None:
            break

        # Check if current story is done
        if not sprint_status.is_story_done(current_story):
            break

        # Story is done — try to skip to next story
        result.stories_skipped.append(current_story)
        result.advanced = True

        stories = stories_for_epic.get(current_epic, [])  # type: ignore[arg-type]
        if current_story in stories:
            story_idx = stories.index(current_story)
            if story_idx + 1 < len(stories):
                # More stories in this epic
                next_story = stories[story_idx + 1]
                current = current.with_story(next_story).with_phase(Phase(phase_list[0]))
                continue

        # All stories in epic are done — try next epic
        if current_epic in epics:
            result.epics_skipped.append(current_epic)  # type: ignore[arg-type]
            epic_idx = epics.index(current_epic)  # type: ignore[arg-type]
            if epic_idx + 1 < len(epics):
                next_epic = epics[epic_idx + 1]
                next_stories = stories_for_epic.get(next_epic, [])
                if next_stories:
                    current = current.model_copy(
                        update={
                            "current_epic": next_epic,
                            "current_story": next_stories[0],
                            "current_phase": Phase(phase_list[0]),
                        }
                    )
                    continue

        # All epics done
        result.project_complete = True
        break

    result.state = current
    return result
