"""Harvest worktree-local telemetry into the project-level history files.

In parallel mode each story subprocess resolves "project root" to its own
worktree, so its phase-metrics rows (cost/tokens per phase) and audit-trigger
rows (fire decisions) land in ``<worktree>/.bmad-assist-lite/`` — a gitignored
directory the merge cannot carry and worktree cleanup deletes. Before this
harvest existed, the project-level history files silently froze at the last
sequential run (observed in the epic-15 fix verification: trigger journal
stuck at epic 6, per-story metrics at epics 4-6).

The orchestrator calls :func:`harvest_story_telemetry` the moment a story
subprocess completes — the one point every path shares while the worktree
still exists (the merger and the blocked-story path each delete worktrees
later, and post-epic supervisors may remove leftovers the orchestrator never
touches, so a teardown hook would miss).

Append-then-delete gives move semantics: a re-entered completion path (resume)
finds no source file and appends nothing twice, so no row tagging or dedup is
needed. The harvest is best-effort — a failure logs a warning and never fails
the story, matching the trigger writer's own posture.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

#: Telemetry files moved from a story worktree into the project-level
#: ``.bmad-assist-lite/``. History files only — worktree caches are scratch
#: and stay out.
HARVEST_FILENAMES: tuple[str, ...] = (
    "phase-metrics.jsonl",
    "ac-audit-trigger.jsonl",
)

_STATE_DIRNAME = ".bmad-assist-lite"


def harvest_story_telemetry(
    story_id: str,
    worktree_path: Path,
    project_root: Path,
) -> int:
    """Move a story worktree's telemetry rows into the project-level files.

    For each file in :data:`HARVEST_FILENAMES` found under the worktree's
    ``.bmad-assist-lite/``: append its non-empty lines to the same-named
    project-level file, then delete the worktree copy. The delete happens
    only after a successful append, so a crash between the two at worst
    leaves the source for a duplicate-free retry (the append is atomic via
    ``os.replace``) — it never loses rows.

    Args:
        story_id: The story whose worktree is being harvested (for logging).
        worktree_path: Root of the story's worktree.
        project_root: Root of the real project.

    Returns:
        The number of rows harvested across both files. A missing worktree,
        missing source file, or empty source contributes zero and is not an
        error; an unreadable source logs a warning and is skipped.

    """
    harvested = 0
    src_dir = Path(worktree_path) / _STATE_DIRNAME
    dst_dir = Path(project_root) / _STATE_DIRNAME
    for name in HARVEST_FILENAMES:
        src = src_dir / name
        try:
            if not src.is_file():
                continue
            lines = [
                line
                for line in src.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if lines:
                _atomic_append_lines(dst_dir / name, lines)
            src.unlink()
        except OSError as exc:
            logger.warning(
                "[ORCHESTRATOR] Telemetry harvest of %s failed for story %s: %s",
                name,
                story_id,
                exc,
            )
            continue
        if lines:
            harvested += len(lines)
            logger.info(
                "[ORCHESTRATOR] Harvested %d %s row(s) from story %s",
                len(lines),
                name,
                story_id,
            )
    return harvested


def _atomic_append_lines(path: Path, lines: list[str]) -> None:
    """Append lines to a JSONL file atomically (read + append + os.replace).

    Same pattern as the trigger writer's project-level append: rebuild the
    file in a temp sibling and replace, so a reader never sees a torn write
    and a missing trailing newline in the existing file cannot glue rows
    together.
    """
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(existing + "\n".join(lines) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
