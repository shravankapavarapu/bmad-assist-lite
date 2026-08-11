"""Durable records for merges that exhausted the ladder.

A merge that reaches the ``park`` tier keeps its branch and its worktree.
That only helps an operator who can *find* them, so parking also writes one
record per parked merge to a single documented location:

    ``.bmad-assist-lite/parked-merges/<story-id>.yaml``

``list_parked_merges()`` enumerates them and backs the
``bmad-assist-lite parallel list-parked`` subcommand.

Un-parking, for the operator
----------------------------

1. ``bmad-assist-lite parallel list-parked`` — find the story, its branch
   and its worktree path.
2. Inspect or fix the work in the worktree; commit onto the same branch.
3. Delete the record: ``rm .bmad-assist-lite/parked-merges/<story-id>.yaml``.
4. Set the story back to ``backlog`` in ``parallel-state.yaml`` (or delete
   that file to start the epic's parallel run fresh).
5. Re-run ``bmad-assist-lite parallel run --epic N --resume``.  The story
   re-enters the merge ladder at the ``clean`` tier.

:func:`unpark_merge` performs steps 3 and 4 in one call for callers that
want it scripted.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.state import STATE_DIR, MergeAttempt

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info (naive UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)

__all__ = [
    "ParkedMerge",
    "get_parked_dir",
    "list_parked_merges",
    "record_parked_merge",
    "unpark_merge",
]

PARKED_DIRNAME: str = "parked-merges"
TEMP_FILE_SUFFIX: str = ".tmp"


class ParkedMerge(BaseModel):
    """Immutable operator-readable record of a parked merge.

    Attributes:
        story_id: The story whose merge was parked.
        branch: The branch that still holds the work.
        worktree_path: Where that branch is checked out, when known.
        integration_head: Integration head at the moment of parking.
        reason: Why the ladder ended in ``park``.
        attempts: The full tier ladder history for this merge.
        parked_at: Naive-UTC timestamp.

    """

    model_config = ConfigDict(frozen=True)

    story_id: str
    branch: str
    worktree_path: str | None = None
    integration_head: str = ""
    reason: str = ""
    attempts: list[MergeAttempt] = []
    parked_at: datetime = Field(default_factory=lambda: _utc_now())


def get_parked_dir(project_root: Path) -> Path:
    """Return the documented directory holding parked-merge records.

    Args:
        project_root: Path to the project root.

    Returns:
        ``{project_root}/.bmad-assist-lite/parked-merges``.

    """
    return project_root / STATE_DIR / PARKED_DIRNAME


def _record_path(project_root: Path, story_id: str) -> Path:
    return get_parked_dir(project_root) / f"{story_id.replace('/', '-')}.yaml"


def record_parked_merge(project_root: Path, parked: ParkedMerge) -> Path:
    """Write a parked-merge record atomically.

    Args:
        project_root: Path to the project root.
        parked: The record to persist.

    Returns:
        Path the record was written to.

    Raises:
        ParallelError: If the record cannot be written.

    """
    path = _record_path(project_root, parked.story_id)
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as handle:
            yaml.dump(
                parked.model_dump(mode="json"),
                handle,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        os.replace(temp_path, path)
    except Exception as exc:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            logger.warning("Failed to clean up temp file: %s", temp_path)
        raise ParallelError(f"Failed to write parked-merge record to {path}: {exc}") from exc

    logger.warning(
        "[MERGE|%s] PARKED — branch %s preserved; record at %s",
        parked.story_id,
        parked.branch,
        path,
    )
    return path


def list_parked_merges(project_root: Path) -> list[ParkedMerge]:
    """Enumerate every parked merge recorded for this project.

    Unreadable or malformed records are skipped with a warning rather than
    hiding the readable ones.

    Args:
        project_root: Path to the project root.

    Returns:
        Parked merges, oldest first.

    """
    parked_dir = get_parked_dir(project_root)
    if not parked_dir.is_dir():
        return []

    records: list[ParkedMerge] = []
    for path in sorted(parked_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                records.append(ParkedMerge.model_validate(data))
        except Exception:
            logger.warning("Skipping unreadable parked-merge record %s", path, exc_info=True)
    records.sort(key=lambda r: r.parked_at)
    return records


def unpark_merge(project_root: Path, story_id: str) -> bool:
    """Clear a parked-merge record so the story can be re-attempted.

    Removing the record is the documented step 3 of the recovery
    procedure.  It never touches the branch or the worktree — the work
    itself is what parking exists to protect.

    Args:
        project_root: Path to the project root.
        story_id: The parked story.

    Returns:
        ``True`` when a record was removed, ``False`` when there was none.

    """
    path = _record_path(project_root, story_id)
    if not path.exists():
        return False
    path.unlink()
    logger.info("[MERGE|%s] Un-parked — record %s removed", story_id, path)
    return True
