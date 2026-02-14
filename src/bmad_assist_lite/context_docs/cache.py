"""Flat file cache and per-epic tracking for library documentation."""

import contextlib
import logging
import os
import re
from pathlib import Path

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

    def _load_epic_libs(self) -> dict[str, list[str]]:
        """Load the epic-libs mapping. Returns empty dict on error."""
        if not self._epic_libs_path.exists():
            return {}
        try:
            data = yaml.safe_load(self._epic_libs_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to read epic-libs.yaml: %s", e)
            return {}

    def _save_epic_libs(self, data: dict[str, list[str]]) -> None:
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
        """Get libraries associated with an epic. None means not yet resolved."""
        data = self._load_epic_libs()
        return data.get(epic_key)

    def set_epic_libs(self, epic_key: str, libraries: list[str]) -> None:
        """Set libraries associated with an epic."""
        data = self._load_epic_libs()
        data[epic_key] = libraries
        self._save_epic_libs(data)

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
