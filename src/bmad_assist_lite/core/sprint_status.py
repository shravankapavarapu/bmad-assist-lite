"""Sprint status model and YAML persistence.

Provides a human-readable view of development progress via sprint-status.yaml.
Uses atomic write (temp + os.replace) for safe persistence.
Surgical text updates preserve YAML comments, quoting, and formatting.
"""

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

SPRINT_STATUS_FILENAME = "sprint-status.yaml"
TEMP_FILE_SUFFIX = ".tmp"

VALID_STATUSES = frozenset(
    {
        "backlog",
        "ready-for-dev",
        "in-progress",
        "review",
        "done",
        "blocked",
        "deferred",
        "optional",
    }
)

DONE_STATUSES = frozenset({"done", "complete", "completed"})

# Statuses that should NOT appear in the work queue
INACTIVE_STATUSES = DONE_STATUSES | frozenset({"blocked", "deferred", "optional"})


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info."""
    return datetime.now(UTC).replace(tzinfo=None)


class SprintStatus(BaseModel):
    """Sprint-level development status tracking.

    Supports two entry formats in development_status:
    - Simple: ``{"1-1-setup": "backlog"}`` (value is the status string)
    - Rich:   ``{"1-1-setup": {"status": "done", "title": "...", ...}}``
      (value is a dict with a ``status`` key)

    Extra top-level fields (project, totals, current_sprint, etc.) are preserved
    via ``extra="allow"`` so they survive load/save round-trips.
    """

    model_config = ConfigDict(extra="allow")

    generated: datetime = Field(default_factory=_utc_now)
    development_status: dict[str, str | dict[str, Any]] = Field(default_factory=dict)

    # --- Entry helpers ---

    @staticmethod
    def _extract_status(entry: str | dict[str, Any]) -> str | None:
        """Extract the status string from a simple or rich entry."""
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            status = entry.get("status")
            return str(status) if status is not None else None
        return None

    def _set_entry_status(self, key: str, status: str) -> None:
        """Set status on an entry, preserving rich dict format if present."""
        existing = self.development_status.get(key)
        if isinstance(existing, dict):
            existing["status"] = status
        else:
            self.development_status[key] = status

    # --- Story status ---

    def get_story_status(self, story_id: str) -> str | None:
        """Get status for a story. Supports dot notation (1.2) and dash (1-2)."""
        key = self._find_key(story_id)
        if key is not None:
            return self._extract_status(self.development_status[key])
        return None

    def set_story_status(self, story_id: str, status: str) -> None:
        """Set status for a story. Uses story-X.Y key format."""
        key = self._find_key(story_id)
        if key is None:
            normalized = story_id.replace(".", "-")
            key = f"story-{normalized}"
        self._set_entry_status(key, status)
        self.generated = _utc_now()

    def is_story_done(self, story_id: str) -> bool:
        """Check if a story is marked as done."""
        status = self.get_story_status(story_id)
        return status is not None and status.lower() in DONE_STATUSES

    # --- Epic status ---

    def get_epic_status(self, epic_id: int | str) -> str | None:
        """Get status for an epic."""
        key = f"epic-{epic_id}"
        entry = self.development_status.get(key)
        if entry is None:
            return None
        return self._extract_status(entry)

    def set_epic_status(self, epic_id: int | str, status: str) -> None:
        """Set status for an epic."""
        key = f"epic-{epic_id}"
        self._set_entry_status(key, status)
        self.generated = _utc_now()

    def is_epic_done(self, epic_id: int | str) -> bool:
        """Check if an epic is marked as done."""
        status = self.get_epic_status(epic_id)
        return status is not None and status.lower() in DONE_STATUSES

    # --- Backlog discovery ---

    def find_next_backlog_story(self) -> tuple[int, int, str] | None:
        """Find the first backlog story in development_status.

        Skips epic entries (keys starting with 'epic-') and retrospective entries.
        Parses key format '{epic_num}-{story_num}-{title}' (e.g., '1-2-user-auth').

        Returns:
            (epic_num, story_num, full_key) or None if no backlog stories.

        """
        result = self.find_backlog_stories()
        return result[0] if result else None

    def find_backlog_stories(self) -> list[tuple[int, int, str]]:
        """Find all actionable stories in development_status order.

        Returns stories that still need work: backlog, drafted, ready-for-dev,
        in-progress, review. Skips done/blocked/deferred/optional stories,
        epic entries, and retrospective entries.

        Parses key format '{epic_num}-{story_num}-{title}' (e.g., '1-2-user-auth').

        Returns:
            List of (epic_num, story_num, full_key) tuples in insertion order.

        """
        results: list[tuple[int, int, str]] = []
        for key, entry in self.development_status.items():
            if key.startswith("epic-"):
                continue
            if "retrospective" in key:
                continue
            status = self._extract_status(entry)
            if status is None or status.lower() in INACTIVE_STATUSES:
                continue
            parsed = self._parse_story_key(key)
            if parsed is not None:
                results.append(parsed)
        return results

    @staticmethod
    def _parse_story_key(key: str) -> tuple[int, int, str] | None:
        """Parse a story key like '1-2-user-auth' into (epic_num, story_num, key).

        Returns None if the key doesn't match expected format.
        """
        parts = key.split("-", 2)
        if len(parts) < 2:
            return None
        try:
            epic_num = int(parts[0])
            story_num = int(parts[1])
            return (epic_num, story_num, key)
        except ValueError:
            return None

    # --- Key resolution ---

    def _find_key(self, story_id: str) -> str | None:
        """Find matching key for a story ID.

        Supports dot notation (1.2) and searches for:
        1. story-X-Y exact match
        2. story-X-Y-* prefix match (e.g., story-1-2-title)
        3. X-Y exact match (bare key without story- prefix)
        4. X-Y-* prefix match (e.g., 6-1-blog-data-layer)
        """
        normalized = story_id.replace(".", "-")
        story_prefix = f"story-{normalized}"
        bare_prefix = f"{normalized}-"

        # Exact match: story-X-Y
        if story_prefix in self.development_status:
            return story_prefix

        # Exact match: X-Y (bare)
        if normalized in self.development_status:
            return normalized

        # Prefix search: story-X-Y-* then X-Y-*
        for key in self.development_status:
            if key.startswith(story_prefix):
                return key

        for key in self.development_status:
            if key.startswith(bare_prefix):
                return key

        return None


def get_sprint_status_path(project_root: Path) -> Path:
    """Get resolved sprint-status.yaml path for a project.

    Resolves to _bmad-output/implementation-artifacts/sprint-status.yaml.
    """
    return (
        project_root / "_bmad-output" / "implementation-artifacts" / SPRINT_STATUS_FILENAME
    ).resolve()


def _patch_yaml_value(text: str, key: str, new_value: str) -> tuple[str, bool]:
    r"""Surgically update a key's value in raw YAML text, preserving formatting.

    Handles both simple (``key: value``) and rich dict (``key:\n  status: value``) formats.
    Preserves the original quoting style (double, single, or unquoted).

    Returns:
        (modified_text, was_found) tuple.

    """
    lines = text.split("\n")
    escaped_key = re.escape(key)
    key_pattern = re.compile(rf"^(\s*){escaped_key}:(.*)$")

    for i, line in enumerate(lines):
        m = key_pattern.match(line)
        if not m:
            continue
        indent = m.group(1)
        rest = m.group(2).strip()

        if rest:
            # Simple format: "  key: value" — replace preserving quote style
            lines[i] = f"{indent}{key}: {_preserve_quoting(rest, new_value)}"
            return "\n".join(lines), True

        # Rich dict format: "  key:" with no inline value
        # Search for "status:" within the indented block
        for j in range(i + 1, min(i + 20, len(lines))):
            next_line = lines[j]
            stripped = next_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Check if we've exited the block (same or lower indent)
            next_indent = len(next_line) - len(next_line.lstrip())
            if next_indent <= len(indent) and stripped:
                break
            status_match = re.match(r"^(\s*)status:\s*(.*)$", next_line)
            if status_match:
                s_indent = status_match.group(1)
                old_val = status_match.group(2).strip()
                lines[j] = f"{s_indent}status: {_preserve_quoting(old_val, new_value)}"
                return "\n".join(lines), True

    return text, False


def _preserve_quoting(old_value: str, new_value: str) -> str:
    """Apply the quoting style from old_value to new_value."""
    if old_value.startswith('"') and old_value.endswith('"'):
        return f'"{new_value}"'
    if old_value.startswith("'") and old_value.endswith("'"):
        return f"'{new_value}'"
    return new_value


def save_sprint_status(sprint_status: SprintStatus, path: str | Path) -> None:
    """Save sprint status to YAML file using atomic write (temp + os.replace).

    When file exists: uses surgical text updates to preserve YAML comments,
    quoting style, and formatting. Only changed status values are modified.
    When file is new: uses yaml.dump for initial creation.
    """
    path = Path(path).expanduser()
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        if not path.exists():
            # Fresh file — use yaml.dump
            _write_fresh_sprint_status(sprint_status, temp_path)
            os.replace(temp_path, path)
            return

        # Existing file — surgical updates to preserve formatting
        raw_text = path.read_text(encoding="utf-8")

        # Parse original to detect what changed
        try:
            original_data = yaml.safe_load(raw_text)
            if not isinstance(original_data, dict):
                original_data = {}
        except Exception:
            original_data = {}

        original_dev = original_data.get("development_status", {})
        new_dev = sprint_status.development_status

        # Update generated date
        gen = sprint_status.generated
        date_str = gen.strftime("%Y-%m-%d") if gen else _utc_now().strftime("%Y-%m-%d")
        gen_pattern = re.compile(r"^(generated:\s*)(.+)$", re.MULTILINE)
        gen_match = gen_pattern.search(raw_text)
        if gen_match:
            old_gen_val = gen_match.group(2).strip()
            new_gen_val = _preserve_quoting(old_gen_val, date_str)
            raw_text = raw_text[: gen_match.start()] + (
                f"{gen_match.group(1)}{new_gen_val}"
            ) + raw_text[gen_match.end():]

        # Update changed development_status entries
        for key, new_entry in new_dev.items():
            old_entry = original_dev.get(key)
            new_status = SprintStatus._extract_status(new_entry)
            old_status = SprintStatus._extract_status(old_entry) if old_entry else None

            if new_status and new_status != old_status:
                raw_text, found = _patch_yaml_value(raw_text, key, new_status)
                if not found:
                    logger.debug("Key %s not found in file for surgical update", key)

        # Atomic write
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(raw_text)

        os.replace(temp_path, path)

    except OSError as e:
        if temp_path.exists():
            temp_path.unlink()
        logger.error("Failed to save sprint status to %s: %s", path, e)
        raise


def _write_fresh_sprint_status(sprint_status: SprintStatus, path: Path) -> None:
    """Write a brand new sprint-status.yaml file."""
    gen = sprint_status.generated
    data: dict[str, Any] = {
        "generated": gen.strftime("%Y-%m-%d") if gen else _utc_now().strftime("%Y-%m-%d"),
        "development_status": sprint_status.development_status,
    }
    # Include extra fields from model
    if sprint_status.model_extra:
        for k, v in sprint_status.model_extra.items():
            data[k] = v

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


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
