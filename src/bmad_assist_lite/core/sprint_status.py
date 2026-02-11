"""Sprint status model and YAML persistence.

Provides a human-readable view of development progress via sprint-status.yaml.
Uses atomic write (temp + os.replace) for safe persistence.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SPRINT_STATUS_FILENAME = "sprint-status.yaml"
SPRINT_STATUS_DIR = ".bmad-assist-lite"
TEMP_FILE_SUFFIX = ".tmp"

VALID_STATUSES = frozenset({
    "backlog",
    "ready-for-dev",
    "in-progress",
    "review",
    "done",
    "blocked",
    "deferred",
    "optional",
})

DONE_STATUSES = frozenset({"done", "complete", "completed"})


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SprintStatus(BaseModel):
    """Sprint-level development status tracking."""

    generated: datetime = Field(default_factory=_utc_now)
    development_status: dict[str, str] = Field(default_factory=dict)

    # --- Story status ---

    def get_story_status(self, story_id: str) -> str | None:
        """Get status for a story. Supports dot notation (1.2) and dash (1-2)."""
        key = self._find_key(story_id)
        if key is not None:
            return self.development_status[key]
        return None

    def set_story_status(self, story_id: str, status: str) -> None:
        """Set status for a story. Uses story-X.Y key format."""
        key = self._find_key(story_id)
        if key is None:
            normalized = story_id.replace(".", "-")
            key = f"story-{normalized}"
        self.development_status[key] = status
        self.generated = _utc_now()

    def is_story_done(self, story_id: str) -> bool:
        """Check if a story is marked as done."""
        status = self.get_story_status(story_id)
        return status is not None and status.lower() in DONE_STATUSES

    # --- Epic status ---

    def get_epic_status(self, epic_id: int | str) -> str | None:
        """Get status for an epic."""
        key = f"epic-{epic_id}"
        return self.development_status.get(key)

    def set_epic_status(self, epic_id: int | str, status: str) -> None:
        """Set status for an epic."""
        key = f"epic-{epic_id}"
        self.development_status[key] = status
        self.generated = _utc_now()

    def is_epic_done(self, epic_id: int | str) -> bool:
        """Check if an epic is marked as done."""
        status = self.get_epic_status(epic_id)
        return status is not None and status.lower() in DONE_STATUSES

    # --- Key resolution ---

    def _find_key(self, story_id: str) -> str | None:
        """Find matching key for a story ID.

        Supports dot notation (1.2) and searches for dash variant (1-2) prefix.
        """
        normalized = story_id.replace(".", "-")
        prefix = f"story-{normalized}"

        # Exact match first
        if prefix in self.development_status:
            return prefix

        # Prefix search (e.g., "story-1-2" matches "story-1-2-title")
        for key in self.development_status:
            if key.startswith(prefix):
                return key

        return None


def get_sprint_status_path(project_root: Path) -> Path:
    """Get resolved sprint-status.yaml path for a project."""
    return (project_root / SPRINT_STATUS_DIR / SPRINT_STATUS_FILENAME).resolve()


def save_sprint_status(sprint_status: SprintStatus, path: str | Path) -> None:
    """Save sprint status to YAML file using atomic write (temp + os.replace)."""
    path = Path(path).expanduser()
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = sprint_status.model_dump(mode="json")

        with open(temp_path, "w", encoding="utf-8") as f:
            yaml.dump(
                data, f, default_flow_style=False, sort_keys=False, allow_unicode=True
            )

        os.replace(temp_path, path)

    except OSError as e:
        if temp_path.exists():
            temp_path.unlink()
        logger.error("Failed to save sprint status to %s: %s", path, e)
        raise


def load_sprint_status(path: str | Path) -> SprintStatus:
    """Load sprint status from YAML file. Returns fresh SprintStatus() if missing/empty."""
    path = Path(path).expanduser()

    if not path.exists():
        logger.debug("No sprint status file at %s, returning empty", path)
        return SprintStatus()

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot read sprint status at %s: %s", path, e)
        return SprintStatus()

    if not content.strip():
        logger.debug("Empty sprint status file at %s", path)
        return SprintStatus()

    try:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            logger.warning("Sprint status corrupted at %s: expected dict", path)
            return SprintStatus()
        return SprintStatus.model_validate(data)
    except (yaml.YAMLError, Exception) as e:
        logger.warning("Sprint status parse error at %s: %s", path, e)
        return SprintStatus()
