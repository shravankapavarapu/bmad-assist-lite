"""Architect sign-off as a durable artifact bound to the tree it approved.

Incident **I-01** is what this exists for: a story reached ``done`` with zero
implementing commits — one violation in thirteen stories, silent, high-impact,
low-frequency. Sign-off was an instruction inside a prompt, so nothing on disk
recorded that anything had been approved, and nothing could be re-checked later.

Two properties make the artifact worth having:

*Binding.* The record names the **tree** SHA it was signed against
(``git rev-parse HEAD^{tree}``). This is REQ-03.5's mechanism, reused rather
than re-invented: a commit SHA changes on every rebase even when the content is
identical, so commit-SHA binding produces false invalidations, whereas the tree
SHA changes exactly when the content changes. A later edit therefore invalidates
the approval automatically instead of silently outliving it.

*Evidence of work.* A sign-off on a story that produced no commits is the I-01
shape itself, so it is refused by name.

Posture
-------
The guard reports a **reason string or None**; it does not raise and does not
write. Refusing to answer is different from answering "blocked": outside a git
repository, or when git cannot be run, the guard returns ``None`` (cannot judge,
so does not block) rather than manufacturing a refusal it has no evidence for.

Enforcement is opt-in via ``signoff.required`` and defaults to off. Requiring an
artifact that no existing project has yet produced would stop every upgraded
run at its first story; per G8 the field is additive, and REQ-08.6's own
reversibility clause names advisory-by-flag as the intended posture.

This module never writes to ``sprint-status.yaml`` or reads it back into state:
the one-way ``state.yaml`` → ``sprint-status.yaml`` sync stays one-way.
"""

import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from bmad_assist_lite.core.exceptions import StateError
from bmad_assist_lite.providers._windows import get_subprocess_kwargs

logger = logging.getLogger(__name__)

__all__ = [
    "SIGNOFF_DIRNAME",
    "SignoffRecord",
    "current_tree_sha",
    "load_signoff",
    "signoff_blocks_done",
    "signoff_path",
    "story_commit_count",
    "write_signoff",
]

SIGNOFF_DIRNAME: str = "signoffs"
STATE_DIR: str = ".bmad-assist-lite"
TEMP_FILE_SUFFIX: str = ".tmp"

APPROVED_VERDICT: str = "approved"

_GIT_TIMEOUT: int = 30


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info."""
    return datetime.now(UTC).replace(tzinfo=None)


class SignoffRecord(BaseModel):
    """One recorded approval, bound to the tree it was given against.

    Attributes:
        story_id: ``"{epic}.{story}"``.
        tree_sha: ``git rev-parse HEAD^{tree}`` at the moment of sign-off. This
            is the binding: it changes exactly when the content changes.
        commit_sha: The commit at sign-off, for human traceability only.
        verdict: ``"approved"`` or anything else, which is not an approval.
        reviewer: Who signed — a model identifier or a person.
        timestamp: Naive UTC, per the project's timestamp convention.

    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    story_id: str
    tree_sha: str
    commit_sha: str = ""
    verdict: str
    reviewer: str
    timestamp: datetime


# ============================================================================
# Persistence
# ============================================================================


def signoff_path(project_path: Path, story_id: str) -> Path:
    """Resolve the artifact path for one story's sign-off."""
    safe = story_id.replace(".", "-").replace(os.sep, "-")
    return project_path / STATE_DIR / SIGNOFF_DIRNAME / f"story-{safe}.yaml"


def write_signoff(record: SignoffRecord, project_path: Path) -> Path:
    """Write a sign-off artifact atomically (temp + ``os.replace``).

    Args:
        record: The approval to record.
        project_path: Project root.

    Returns:
        The path written.

    Raises:
        StateError: If the artifact cannot be written.

    """
    path = signoff_path(project_path, record.story_id)
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            yaml.dump(
                record.model_dump(mode="json"),
                default_flow_style=False,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    except (OSError, yaml.YAMLError) as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                logger.debug("Could not remove sign-off temp file %s", temp_path)
        raise StateError(f"Failed to write sign-off artifact to {path}: {e}") from e

    return path


def load_signoff(project_path: Path, story_id: str) -> SignoffRecord | None:
    """Read one story's sign-off artifact.

    Args:
        project_path: Project root.
        story_id: Story identifier.

    Returns:
        The record, or None if there is none. A corrupt artifact also reads as
        None — an unreadable approval is not an approval — and is logged.

    """
    path = signoff_path(project_path, story_id)
    if not path.exists():
        return None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return SignoffRecord.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError, TypeError) as e:
        logger.warning("Ignoring unreadable sign-off artifact at %s: %s", path, e)
        return None


# ============================================================================
# Git observations
# ============================================================================


def _git(project_path: Path, *args: str) -> str | None:
    """Run a read-only git command, returning None if it cannot be run."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            **get_subprocess_kwargs(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("git %s failed in %s: %s", args[0] if args else "", project_path, e)
        return None

    if result.returncode != 0:
        logger.debug("git %s returned %d: %s", args[0], result.returncode, result.stderr.strip())
        return None
    return str(result.stdout).strip()


def current_tree_sha(project_path: Path) -> str | None:
    """Resolve the working tree's current tree SHA, or None outside a repo."""
    return _git(project_path, "rev-parse", "HEAD^{tree}")


def story_commit_count(project_path: Path, story_id: str) -> int | None:
    """Count commits that claim to implement ``story_id``.

    Keyed on the subject line this tool's own auto-commit writes,
    ``feat(story-{id}): ...``. A project that commits by hand under a different
    convention will read as zero, which is why the postcondition this feeds is
    opt-in rather than on by default.

    Args:
        project_path: Project root.
        story_id: Story identifier.

    Returns:
        The commit count, or None if git could not be consulted.

    """
    out = _git(project_path, "log", "--oneline", f"--grep=story-{story_id}")
    if out is None:
        return None
    return len([line for line in out.splitlines() if line.strip()])


# ============================================================================
# The postcondition
# ============================================================================


def signoff_blocks_done(project_path: Path, story_id: str) -> str | None:
    """Say why ``story_id`` may not be marked done, or None if it may.

    Args:
        project_path: Project root.
        story_id: Story identifier.

    Returns:
        A human-readable reason to withhold ``done``, or None to permit it.
        None is also returned when git cannot be consulted at all: the guard
        then has no evidence, and inventing a refusal from no evidence would
        block work for a reason that is not true.

    """
    tree = current_tree_sha(project_path)
    if tree is None:
        logger.debug("Not a git repository; sign-off postcondition not evaluated")
        return None

    commits = story_commit_count(project_path, story_id)
    if commits == 0:
        return (
            f"story {story_id} has zero implementing commits "
            f"(no commit matching 'story-{story_id}'). This is incident I-01: "
            "a story marked done with nothing implementing it."
        )

    record = load_signoff(project_path, story_id)
    if record is None:
        return (
            f"story {story_id} has no sign-off artifact "
            f"(expected {signoff_path(project_path, story_id)})."
        )

    if record.verdict != APPROVED_VERDICT:
        return f"story {story_id} sign-off records verdict '{record.verdict}', not approved."

    if record.tree_sha != tree:
        return (
            f"story {story_id} sign-off is stale: it approved tree "
            f"{record.tree_sha[:12]} but the tree is now {tree[:12]}. "
            "The code changed after it was approved."
        )

    return None
