"""Flat file cache and per-epic tracking for library documentation."""

from __future__ import annotations

import contextlib
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _sanitize_name(name: str) -> str:
    """Sanitize library name for use as a filename."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name.strip().lower())


class LibDocsCache:
    """Cache for library documentation files with per-epic tracking.

    Cache layout:
        {cache_dir}/lib-docs/{sanitized-name}.md   — per-library doc files
        {cache_dir}/epic-libs.yaml                  — epic → libraries mapping
    """

    def __init__(self, cache_dir: Path) -> None:
        """Initialize cache with the given cache directory."""
        self._lib_docs_dir = cache_dir / "lib-docs"
        self._epic_libs_path = cache_dir / "epic-libs.yaml"

    def _lib_path(self, name: str) -> Path:
        return self._lib_docs_dir / f"{_sanitize_name(name)}.md"

    def has_library(self, name: str) -> bool:
        """Check if documentation exists for a library."""
        return self._lib_path(name).exists()

    def read_library(self, name: str) -> str | None:
        """Read cached documentation for a library. Returns None on error."""
        path = self._lib_path(name)
        try:
            return path.read_text(encoding="utf-8") if path.exists() else None
        except OSError as e:
            logger.warning("Failed to read cached docs for %s: %s", name, e)
            return None

    def write_library(self, name: str, content: str) -> None:
        """Atomically write documentation for a library."""
        path = self._lib_path(name)
        temp_path = path.with_suffix(".md.tmp")
        try:
            self._lib_docs_dir.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(content, encoding="utf-8")
            os.replace(temp_path, path)
        except OSError as e:
            logger.warning("Failed to cache docs for %s: %s", name, e)
            if temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()

    # --- Epic-level tracking ---

    def _load_epic_libs(self) -> dict[str, Any]:
        """Load the epic-libs mapping. Returns empty dict on error."""
        if not self._epic_libs_path.exists():
            return {}
        try:
            data = yaml.safe_load(self._epic_libs_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to read epic-libs.yaml: %s", e)
            return {}

    def _save_epic_libs(self, data: dict[str, Any]) -> None:
        """Atomically save the epic-libs mapping."""
        temp_path = self._epic_libs_path.with_suffix(".yaml.tmp")
        try:
            self._epic_libs_path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            os.replace(temp_path, self._epic_libs_path)
        except OSError as e:
            logger.warning("Failed to save epic-libs.yaml: %s", e)
            if temp_path.exists():
                with contextlib.suppress(OSError):
                    temp_path.unlink()

    def get_epic_libs(self, epic_key: str) -> list[str] | None:
        """Get libraries associated with an epic. None means not yet resolved.

        For table-format entries (dict with ``_source: table``), returns the
        ``libs`` list.  For legacy entries (plain list), returns it directly.
        """
        data = self._load_epic_libs()
        value = data.get(epic_key)
        if value is None:
            return None
        if isinstance(value, dict):
            libs = value.get("libs", [])
            return list(libs) if isinstance(libs, list) else []
        return list(value) if isinstance(value, list) else []

    def set_epic_libs(self, epic_key: str, libraries: list[str]) -> None:
        """Set libraries associated with an epic (legacy format)."""
        data = self._load_epic_libs()
        data[epic_key] = libraries
        self._save_epic_libs(data)

    def set_epic_table_libs(
        self,
        epic_key: str,
        all_libs: list[str],
        story_libs: dict[str, list[str]],
    ) -> None:
        """Store table-format epic libraries with per-story mapping."""
        data = self._load_epic_libs()
        data[epic_key] = {
            "_source": "table",
            "libs": all_libs,
            "story_libs": story_libs,
        }
        self._save_epic_libs(data)

    def is_table_source(self, epic_key: str) -> bool:
        """Check if an epic's library data came from an epic table."""
        data = self._load_epic_libs()
        value = data.get(epic_key)
        return isinstance(value, dict) and value.get("_source") == "table"

    def get_libs_for_story(
        self, epic_key: str, story_num: str
    ) -> dict[str, str]:
        """Get cached library docs for a specific story within an epic.

        For table-format epics, returns only the docs mapped to the given
        story number.  Falls back to all epic libs if no story mapping exists.
        """
        data = self._load_epic_libs()
        value = data.get(epic_key)
        if value is None:
            return {}

        if isinstance(value, dict) and value.get("_source") == "table":
            story_map = value.get("story_libs", {})
            lib_names = story_map.get(story_num)
            if lib_names is None:
                # Story not in mapping — fall back to all libs
                lib_names = value.get("libs", [])
        else:
            # Legacy format — return all
            lib_names = value if isinstance(value, list) else []

        result: dict[str, str] = {}
        for name in lib_names:
            content = self.read_library(name)
            if content:
                result[name] = content
        return result

    def get_libs_for_epic(self, epic_key: str) -> dict[str, str]:
        """Get all cached library docs for an epic. Returns {name: content}."""
        libs = self.get_epic_libs(epic_key)
        if libs is None:
            return {}
        result: dict[str, str] = {}
        for name in libs:
            content = self.read_library(name)
            if content:
                result[name] = content
        return result
