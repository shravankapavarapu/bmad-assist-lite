"""Parallel state persistence for orchestrator crash recovery.

Provides frozen Pydantic models for tracking story lifecycle status,
atomic YAML persistence (temp + os.replace()), and resume-from-crash
support via parallel-state.yaml.
"""

import logging
import os
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bmad_assist_lite.parallel.exceptions import ParallelError

logger = logging.getLogger(__name__)

__all__ = [
    "GateObservation",
    "MergeAttempt",
    "MergeTier",
    "ParallelState",
    "StoryState",
    "StoryStatus",
    "create_initial_state",
    "gate_verdict",
    "get_parallel_state_path",
    "load_state",
    "merge_verdict",
    "save_state",
]

PARALLEL_STATE_FILENAME: str = "parallel-state.yaml"
STATE_DIR: str = ".bmad-assist-lite"
TEMP_FILE_SUFFIX: str = ".tmp"


# ============================================================================
# Timestamp helper
# ============================================================================


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info (naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


# ============================================================================
# Story status enum
# ============================================================================


class StoryStatus(Enum):
    """Lifecycle status for a parallel story.

    Stories progress through: backlog -> in_flight -> merging -> done
    (or blocked at any point after in_flight).
    """

    BACKLOG = "backlog"
    IN_FLIGHT = "in_flight"
    MERGING = "merging"
    DONE = "done"
    BLOCKED = "blocked"


# ============================================================================
# Merge protocol records
# ============================================================================


class MergeTier(Enum):
    """Closed ladder of merge attempt tiers.

    A merge escalates through the ladder in order and never skips a rung.
    ``PARK`` is terminal and is the only way the ladder ends other than a
    successful land — exhausting it never deletes anything.
    """

    CLEAN = "clean"
    AUTO = "auto"
    AI_RESOLVE = "ai-resolve"
    PARK = "park"


class MergeAttempt(BaseModel):
    """Immutable record of one rung of the merge ladder.

    This is an *observation*, not a verdict: it says what was tried, on
    which tree, and what git said.  Whether a story counts as merged is
    computed at read time by :func:`merge_verdict`, which asks git.

    Attributes:
        tier: Which rung of the ladder this attempt was.
        branch: The candidate branch the attempt operated on.
        commit_sha: Candidate tip at the time of the attempt.
        tree_sha: Tree the attempt was computed against.  Every verdict on
            the merge path binds to this, never to ``commit_sha``.
        integration_sha: Integration head the attempt targeted.
        outcome: Short machine-readable outcome token.
        detail: Human-readable explanation.
        duration_ms: Wall-clock duration of the attempt.
        recorded_at: Naive-UTC timestamp.

    """

    model_config = ConfigDict(frozen=True)

    tier: MergeTier
    branch: str
    commit_sha: str
    tree_sha: str
    integration_sha: str = ""
    outcome: str
    detail: str = ""
    duration_ms: int = 0
    recorded_at: datetime = Field(default_factory=lambda: _utc_now())


class GateObservation(BaseModel):
    """Immutable record that a gate ran against a specific tree.

    Nothing here is a verdict that outlives the tree it was computed from:
    the record names the tree, and :func:`gate_verdict` answers "did *this*
    tree pass?" only for a tree SHA that matches.

    Attributes:
        tree_sha: ``git rev-parse HEAD^{tree}`` at the moment the gate ran.
        commit_sha: The commit SHA, kept for human traceability only.
        directory: Working directory the gate executed in.
        result: Raw observed result token (``pass`` / ``fail``).
        classification: Failure classification, when one was made.
        failed_gates: Names of the gates that did not pass.
        duration_ms: Wall-clock duration of the gate run.
        recorded_at: Naive-UTC timestamp.

    """

    model_config = ConfigDict(frozen=True)

    tree_sha: str
    commit_sha: str = ""
    directory: str = ""
    result: str = "fail"
    classification: str = ""
    failed_gates: list[str] = []
    duration_ms: int = 0
    recorded_at: datetime = Field(default_factory=lambda: _utc_now())


def gate_verdict(observations: list[GateObservation], tree_sha: str) -> str:
    """Compute the gate verdict for a tree from recorded observations.

    Verdicts are never persisted; they are derived here, at read time, from
    observations bound to a tree SHA.  A tree with no matching observation
    is ``unknown`` — not ``pass`` — so a rebase that changes the tree
    invalidates the previous result automatically.

    Args:
        observations: Recorded gate observations, in any order.
        tree_sha: The tree the caller wants a verdict for.

    Returns:
        ``"pass"``, ``"fail"`` or ``"unknown"``.

    """
    if not tree_sha:
        return "unknown"
    matching = [o for o in observations if o.tree_sha == tree_sha]
    if not matching:
        return "unknown"
    latest = max(matching, key=lambda o: o.recorded_at)
    return "pass" if latest.result == "pass" else "fail"


def merge_verdict(attempts: list[MergeAttempt], landed_tree_sha: str | None) -> str:
    """Compute whether the recorded attempts amount to a landed merge.

    Args:
        attempts: Recorded merge attempts, in any order.
        landed_tree_sha: The tree SHA git currently reports for the
            integration head's ancestry check, or ``None`` when git says
            the branch did not land.

    Returns:
        ``"landed"``, ``"parked"`` or ``"pending"``.

    """
    if landed_tree_sha:
        return "landed"
    if any(a.tier == MergeTier.PARK for a in attempts):
        return "parked"
    return "pending"


# ============================================================================
# Story state model
# ============================================================================


class StoryState(BaseModel):
    """Frozen state for a single story in parallel execution.

    All mutations must use ``model_copy(update={...})``.
    """

    model_config = ConfigDict(frozen=True)

    status: StoryStatus = StoryStatus.BACKLOG
    worktree_path: Path | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    merge_attempts: list[MergeAttempt] = []
    gate_observations: list[GateObservation] = []
    landed_commit_sha: str | None = None


# ============================================================================
# Parallel state model
# ============================================================================


class ParallelState(BaseModel):
    """Frozen top-level state for parallel orchestration.

    Persisted to ``parallel-state.yaml`` after every status transition.
    All mutations must use ``model_copy(update={...})``.

    The ``epic`` field stores the numeric portion only (e.g., ``3`` not
    ``"Epic-3"``). The caller is responsible for extracting the numeric
    ID from the full epic identifier string.
    """

    model_config = ConfigDict(frozen=True)

    base_branch: str
    epic: int
    started_at: datetime
    stories: dict[str, StoryState]

    def with_story_status(
        self,
        story_id: str,
        status: StoryStatus,
        **kwargs: object,
    ) -> "ParallelState":
        """Return a new state with one story's status updated.

        Creates a shallow copy of the stories dict, updates the
        specified story, and returns a new ``ParallelState`` via
        ``model_copy``. Never mutates the current instance.

        When transitioning to ``backlog`` (e.g., retry after blocked),
        stale fields are cleared unless explicitly overridden via
        ``**kwargs``.

        Args:
            story_id: The story to update (e.g. ``"3.2"``).
            status: The new ``StoryStatus``.
            **kwargs: Additional ``StoryState`` fields to set
                (``worktree_path``, ``started_at``, ``completed_at``,
                ``error``).

        Returns:
            A new ``ParallelState`` with the updated story.

        Raises:
            KeyError: If ``story_id`` is not in the stories dict.

        """
        if story_id not in self.stories:
            msg = f"Unknown story_id: {story_id!r}"
            raise KeyError(msg)

        current_story = self.stories[story_id]

        # Build the update dict for the story
        updates: dict[str, object] = {"status": status}

        # When transitioning to backlog, clear stale fields unless overridden
        if status == StoryStatus.BACKLOG:
            updates["error"] = None
            updates["completed_at"] = None
            updates["worktree_path"] = None
            updates["started_at"] = None

        updates.update(kwargs)

        new_story = current_story.model_copy(update=updates)

        # Shallow copy the stories dict to avoid mutating the frozen state
        new_stories = dict(self.stories)
        new_stories[story_id] = new_story

        return self.model_copy(update={"stories": new_stories})


# ============================================================================
# State persistence — atomic save
# ============================================================================


def save_state(state: ParallelState, path: Path) -> None:
    """Save parallel state to YAML using atomic write (temp + os.replace).

    Args:
        state: The ``ParallelState`` to persist.
        path: Destination file path for the YAML state.

    Raises:
        ParallelError: If the write fails.

    """
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = state.model_dump(mode="json")

        with open(temp_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        os.replace(temp_path, path)

    except Exception as e:
        # Clean up temp file on any failure (OSError, YAMLError, etc.)
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            logger.warning("Failed to clean up temp file: %s", temp_path)
        raise ParallelError(f"Failed to save parallel state to {path}: {e}") from e


# ============================================================================
# State persistence — load with orphan cleanup
# ============================================================================


def load_state(path: Path) -> ParallelState | None:
    """Load parallel state from YAML, cleaning up any orphaned temp files.

    Args:
        path: Path to the ``parallel-state.yaml`` file.

    Returns:
        The loaded ``ParallelState``, or ``None`` if the file does not exist.

    Raises:
        ParallelError: If the file is corrupt, invalid YAML, or fails
            schema validation.

    """
    # Clean up orphaned temp file from a prior crash
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)
    if temp_path.exists():
        logger.warning("Removing orphaned temp file: %s", temp_path)
        try:
            temp_path.unlink()
        except OSError as e:
            raise ParallelError(
                f"Cannot remove orphaned temp file {temp_path}: {e}"
            ) from e

    if not path.exists():
        logger.info("No parallel state file at %s, fresh run", path)
        return None

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ParallelError(f"Cannot read parallel state file at {path}: {e}") from e
    except UnicodeDecodeError as e:
        raise ParallelError(
            f"Parallel state file at {path} is not valid UTF-8: {e}"
        ) from e

    if not content.strip():
        logger.info("Empty parallel state file at %s, treating as fresh run", path)
        return None

    try:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            raise ParallelError(
                f"Parallel state file corrupted at {path}: "
                f"expected dict, got {type(data).__name__}"
            )
        return ParallelState.model_validate(data)
    except yaml.YAMLError as e:
        raise ParallelError(
            f"Parallel state file corrupted at {path}: invalid YAML"
        ) from e
    except ValidationError as e:
        raise ParallelError(
            f"Parallel state file validation failed at {path}: {e}"
        ) from e


# ============================================================================
# Initial state creation
# ============================================================================


def create_initial_state(
    base_branch: str,
    epic: int,
    story_ids: list[str],
) -> ParallelState:
    """Create a fresh ``ParallelState`` with all stories at backlog.

    Args:
        base_branch: The git branch stories are based on.
        epic: The numeric epic identifier (e.g. ``3``).
        story_ids: List of story identifiers (e.g. ``["3.1", "3.2"]``).

    Returns:
        A new ``ParallelState`` with all stories initialized.

    """
    stories = {sid: StoryState(status=StoryStatus.BACKLOG) for sid in story_ids}
    return ParallelState(
        base_branch=base_branch,
        epic=epic,
        started_at=_utc_now(),
        stories=stories,
    )


# ============================================================================
# State file path utility
# ============================================================================


def get_parallel_state_path(project_root: Path | None = None) -> Path:
    """Get resolved path for the parallel state file.

    Args:
        project_root: Project root directory. Defaults to ``Path.cwd()``.

    Returns:
        Resolved path to ``{project_root}/.bmad-assist-lite/parallel-state.yaml``.

    """
    if project_root is not None:
        return (project_root / STATE_DIR / PARALLEL_STATE_FILENAME).resolve()
    return (Path.cwd() / STATE_DIR / PARALLEL_STATE_FILENAME).resolve()
