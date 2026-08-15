"""State data model for bmad-assist-lite development loop."""

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bmad_assist_lite.core.exceptions import StateError

logger = logging.getLogger(__name__)

STATE_FILENAME: str = "state.yaml"
STATE_DIR: str = ".bmad-assist-lite"
TEMP_FILE_SUFFIX = ".tmp"


# ============================================================================
# Unknown-key policy for every model that persists state to disk
# ============================================================================
#
# Three models write state to disk, and they must not answer "what happens to a
# key I do not recognise?" by accident. Two of them used to leave `extra`
# undeclared and so inherited Pydantic's silent `ignore`, while the third
# declared `allow`; the result was that a mistyped key vanished from two files
# and survived in the third, with nothing in the source saying so on purpose.
#
# The rule is one sentence: **a file this tool alone writes drops unknown keys;
# a file other authors also write preserves them.**
#
# That split is deliberate, and flattening it in either direction breaks
# something real — both halves are pinned by tests in
# `tests/test_state_extra_policy.py`:
#
#   * Making the tool-owned models `allow` would remove a live guard. `State`
#     is mutated in place by `update_position()`, and under `allow` Pydantic
#     accepts `state.current_phse = ...` as a brand-new field instead of
#     raising. Dropping is also self-cleaning: a bad key leaves on the next
#     save instead of being copied forward forever.
#   * Making `SprintStatus` `ignore` would destroy user data. `sprint-status.yaml`
#     is the documented single source of truth for story discovery and is also
#     written by BMAD planning skills and edited by hand; it carries top-level
#     keys this tool does not model (`project`, `current_sprint`, `totals`).
#     Under `ignore` every save would silently strip them.
#
# Dropping is never silent: `log_ignored_fields()` warns once per source before
# validation, so the tolerance that makes upgrades survivable does not also make
# a typo invisible.

EXTRA_POLICY: dict[str, str] = {
    # Tool-owned files: only this tool writes them, so an unknown key is either
    # version skew or a mistake. Drop it (loudly) rather than copy it forward.
    "State": "ignore",
    "ParallelState": "ignore",
    "StoryState": "ignore",
    "MergeAttempt": "ignore",
    "GateObservation": "ignore",
    # Co-authored file: dropping would delete content we did not write.
    "SprintStatus": "allow",
}

PRESERVE_UNKNOWN_KEYS: frozenset[str] = frozenset(
    name for name, policy in EXTRA_POLICY.items() if policy == "allow"
)
"""Models that keep unknown keys, because something other than this tool wrote them."""


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info."""
    return datetime.now(UTC).replace(tzinfo=None)


# Ignored-field warnings already emitted, keyed by (source, sorted field names).
# Loading is not a once-per-run event — resume, sync and the hygiene sweep all
# read these files — so an un-deduplicated warning would bury the log it is
# meant to make readable.
_reported_ignored_fields: set[tuple[str, tuple[str, ...]]] = set()


def log_ignored_fields(
    model_cls: type[BaseModel], data: dict[str, Any], source: str
) -> tuple[str, ...]:
    """Warn once about keys a model will silently drop.

    Pydantic's runtime default is ``extra='ignore'``: a state file written by
    a newer version loads cleanly and its unknown keys vanish without a word.
    Tolerance is what we want on load; silence is not, because the dropped key
    may be the one that explains the run.

    Note this is a *runtime* concern and unrelated to ``init_forbid_extra``,
    which is a mypy-plugin setting and has no effect on validation.

    Args:
        model_cls: The model the data is about to be validated against.
        data: The raw mapping read from disk.
        source: Where it was read from, used to deduplicate the warning.

    Returns:
        The ignored field names, sorted.

    """
    ignored = tuple(sorted(set(data) - set(model_cls.model_fields)))
    if not ignored:
        return ()

    key = (source, ignored)
    if key not in _reported_ignored_fields:
        _reported_ignored_fields.add(key)
        logger.warning(
            "%s at %s: ignoring %d unknown field(s) written by another version: %s",
            model_cls.__name__,
            source,
            len(ignored),
            ", ".join(ignored),
        )
    return ignored


def _reset_ignored_field_log() -> None:
    """Clear the once-per-source warning memo. For testing."""
    _reported_ignored_fields.clear()


class Phase(Enum):
    """Workflow phases for the development loop.

    10 phases in configurable order:
        1. CREATE_STORY - Create story context from epic
        2. VALIDATE_STORY - Multi-LLM validation of story
        3. VALIDATE_STORY_SYNTHESIS - Master LLM synthesizes validation
        4. DEV_STORY - Master LLM implements story
        5. CODE_REVIEW - Multi-LLM code review
        6. CODE_REVIEW_SYNTHESIS - Master LLM synthesizes review
        7. QUALITY_GATE - Deterministic quality gate checks (non-LLM)
        8. FIX_QUALITY_GATE - LLM fix attempt for failed quality gates
        9. EPIC_QUALITY_GATE - Project-wide quality gate (non-LLM, epic teardown)
       10. RETROSPECTIVE - Epic retrospective (after last story)

    FIX_REVIEW is an eleventh phase but deliberately not a listed one: like
    FIX_QUALITY_GATE it is a detour reached only via a ``next_phase`` override,
    never a member of ``loop.story``.
    """

    CREATE_STORY = "create_story"
    VALIDATE_STORY = "validate_story"
    VALIDATE_STORY_SYNTHESIS = "validate_story_synthesis"
    DEV_STORY = "dev_story"
    CODE_REVIEW = "code_review"
    CODE_REVIEW_SYNTHESIS = "code_review_synthesis"
    QUALITY_GATE = "quality_gate"
    FIX_QUALITY_GATE = "fix_quality_gate"
    FIX_REVIEW = "fix_review"
    EPIC_QUALITY_GATE = "epic_quality_gate"
    RETROSPECTIVE = "retrospective"


class State(BaseModel):
    """Persistent state for the development loop.

    Unknown keys are dropped (and warned about) per ``EXTRA_POLICY``: this file
    has one writer, and the model is mutated in place, so ``extra='allow'``
    would turn a misspelled attribute assignment into a silent new field.
    """

    model_config = ConfigDict(extra="ignore")

    current_epic: int | str | None = None
    current_story: str | None = None
    current_phase: Phase | None = None
    completed_stories: list[str] = Field(default_factory=list)
    completed_epics: list[int | str] = Field(default_factory=list)
    failed_qa_stories: list[str] = Field(default_factory=list)
    qa_retry_count: int = 0
    # Review loop, mirroring the qa_retry_count mechanism rather than inventing
    # a new one. ``review_finding_hashes`` holds one normalised set hash per
    # review pass of the current story; a repeat is what proves the fixer is
    # stuck rather than merely slow.
    review_iteration: int = 0
    review_finding_hashes: list[str] = Field(default_factory=list)
    review_blocked_stories: list[str] = Field(default_factory=list)
    review_story_id: str | None = None
    # L2 (goal-run5): per-reviewer-lane Claude session ids, so a round-2
    # re-review resumes its OWN round-1 session. Keyed by
    # ``story#phase#index#provider#model`` so a session can NEVER be resumed
    # across stories, phases, or reviewer lanes -- the F-13 independence rule
    # made structural. Written and read only by the reviewer/synthesis lanes
    # (never the dev/master chain); stale-story entries are pruned on write.
    reviewer_session_ids: dict[str, str] = Field(default_factory=dict)
    started_at: datetime | None = None
    updated_at: datetime | None = None
    story_started_at: datetime | None = None
    phase_started_at: datetime | None = None

    def with_phase(self, phase: Phase) -> "State":
        """Return a copy with updated phase."""
        return self.model_copy(update={"current_phase": phase, "updated_at": _utc_now()})

    def with_story(self, story: str) -> "State":
        """Return a copy with updated story."""
        return self.model_copy(update={"current_story": story, "updated_at": _utc_now()})

    def with_epic(self, epic: int | str) -> "State":
        """Return a copy with updated epic."""
        return self.model_copy(update={"current_epic": epic, "updated_at": _utc_now()})


def save_state(state: State, path: str | Path) -> None:
    """Save state to YAML file using atomic write (temp + os.replace)."""
    path = Path(path).expanduser()
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = state.model_dump(mode="json")

        with open(temp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        os.replace(temp_path, path)

    except OSError as e:
        if temp_path.exists():
            temp_path.unlink()
        raise StateError(f"Failed to save state to {path}: {e}") from e


def load_state(path: str | Path) -> State:
    """Load state from YAML file. Returns fresh State() if missing/empty."""
    path = Path(path).expanduser()

    # Clean orphaned temp files
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)
    if temp_path.exists():
        logger.warning("Removing orphaned temp file: %s", temp_path)
        try:
            temp_path.unlink()
        except OSError as e:
            raise StateError(f"Cannot remove orphaned temp file {temp_path}: {e}") from e

    if not path.exists():
        logger.info("No state file at %s, starting fresh", path)
        return State()

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise StateError(f"Cannot read state file at {path}: {e}") from e
    except UnicodeDecodeError as e:
        raise StateError(f"State file at {path} is not valid UTF-8: {e}") from e

    if not content.strip():
        logger.info("Empty state file at %s, starting fresh", path)
        return State()

    try:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            raise StateError(
                f"State file corrupted at {path}: expected dict, got {type(data).__name__}"
            )
        log_ignored_fields(State, data, str(path))
        return State.model_validate(data)
    except yaml.YAMLError as e:
        raise StateError(f"State file corrupted at {path}: invalid YAML") from e
    except ValidationError as e:
        raise StateError(f"State file validation failed at {path}: {e}") from e


def update_position(
    state: State,
    *,
    epic: int | str | None = None,
    story: str | None = None,
    phase: Phase | None = None,
) -> None:
    """Update current position in the development loop."""
    now = _utc_now()

    if epic is not None:
        state.current_epic = epic
    if story is not None:
        state.current_story = story
    if phase is not None:
        state.current_phase = phase

    if state.started_at is None:
        state.started_at = now
    state.updated_at = now


def mark_story_completed(state: State) -> None:
    """Mark current story as completed (idempotent)."""
    if state.current_story is None:
        raise StateError("Cannot mark story completed: no current story set")

    if state.current_story not in state.completed_stories:
        state.completed_stories.append(state.current_story)
    state.updated_at = _utc_now()


def advance_state(state: State, phase_list: list[str]) -> dict[str, Any]:
    """Advance state to the next phase in the workflow."""
    if state.current_phase is None:
        raise StateError("Cannot advance state: no current phase set")

    previous = state.current_phase
    current_value = previous.value

    try:
        current_idx = phase_list.index(current_value)
    except ValueError as e:
        raise StateError(f"Cannot advance state: phase {previous!r} not in phase sequence") from e

    if current_idx + 1 >= len(phase_list):
        state.updated_at = _utc_now()
        return {
            "previous_phase": previous,
            "new_phase": previous,
            "transitioned": False,
            "epic_complete": True,
        }

    next_value = phase_list[current_idx + 1]
    try:
        next_phase = Phase(next_value)
    except ValueError as e:
        raise StateError(f"Invalid phase '{next_value}' in phase sequence") from e

    state.current_phase = next_phase
    state.updated_at = _utc_now()

    return {
        "previous_phase": previous,
        "new_phase": next_phase,
        "transitioned": True,
        "epic_complete": False,
    }


def get_state_path(project_root: Path | None = None) -> Path:
    """Get resolved state file path for a project."""
    if project_root is not None:
        return (project_root / STATE_DIR / STATE_FILENAME).resolve()
    return (Path.cwd() / STATE_DIR / STATE_FILENAME).resolve()


def start_phase_timing(state: State) -> None:
    """Mark phase execution start time."""
    now = _utc_now()
    state.phase_started_at = now
    if state.story_started_at is None:
        state.story_started_at = now
    state.updated_at = now


def start_story_timing(state: State) -> None:
    """Mark story execution start time."""
    now = _utc_now()
    state.story_started_at = now
    state.phase_started_at = now
    state.updated_at = now


def get_phase_duration_ms(state: State) -> int:
    """Calculate phase duration in milliseconds."""
    if state.phase_started_at is None:
        return 0
    delta = _utc_now() - state.phase_started_at
    return int(delta.total_seconds() * 1000)


def get_story_duration_ms(state: State) -> int:
    """Calculate story total duration in milliseconds."""
    if state.story_started_at is None:
        return 0
    delta = _utc_now() - state.story_started_at
    return int(delta.total_seconds() * 1000)


@dataclass
class ResumePoint:
    """Information about where to resume the loop."""

    epic: int | str | None
    story: str | None
    phase: Phase | None
    is_fresh_start: bool
    completed_stories: list[str] = field(default_factory=list)


def get_resume_point(state_path: str | Path) -> ResumePoint:
    """Determine resume point from persisted state."""
    state = load_state(state_path)

    has_position = (
        state.current_epic is not None
        and state.current_story is not None
        and state.current_phase is not None
    )

    if not has_position:
        return ResumePoint(
            epic=None,
            story=None,
            phase=None,
            is_fresh_start=True,
            completed_stories=list(state.completed_stories),
        )

    return ResumePoint(
        epic=state.current_epic,
        story=state.current_story,
        phase=state.current_phase,
        is_fresh_start=False,
        completed_stories=list(state.completed_stories),
    )
