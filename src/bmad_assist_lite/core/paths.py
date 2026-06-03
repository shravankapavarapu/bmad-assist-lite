"""Centralized path resolution for all bmad-assist-lite artifacts."""

import logging
from functools import cached_property
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProjectPaths:
    """Resolves all project paths from configuration."""

    DEFAULT_OUTPUT_FOLDER = "{project-root}/_bmad-output"
    DEFAULT_PLANNING_ARTIFACTS = "{project-root}/_bmad-output/planning-artifacts"
    DEFAULT_IMPLEMENTATION_ARTIFACTS = "{project-root}/_bmad-output/implementation-artifacts"
    DEFAULT_PROJECT_KNOWLEDGE = "{project-root}/docs"

    def __init__(self, project_root: Path, config: dict[str, Any] | None = None):
        """Initialize path resolver from project root and optional config."""
        self.project_root = project_root.resolve()
        self._config = config or {}

    def _resolve_path(self, template: str) -> Path:
        """Resolve path template to absolute path."""
        if not template or not template.strip():
            logger.warning("Empty path template, falling back to project_root")
            return self.project_root

        if not template.startswith("{project-root}") and Path(template).is_absolute():
            return Path(template).resolve()

        if not template.startswith("{project-root}"):
            return (self.project_root / template).resolve()

        resolved_str = template.replace("{project-root}", str(self.project_root))
        return Path(resolved_str).resolve()

    def _get_config_path(self, key: str, default: str) -> Path:
        template = self._config.get(key, default)
        return self._resolve_path(template)

    # Base Output Folders

    @cached_property
    def output_folder(self) -> Path:
        """Return the base output folder path."""
        return self._get_config_path("output_folder", self.DEFAULT_OUTPUT_FOLDER)

    @cached_property
    def planning_artifacts(self) -> Path:
        """Return the planning artifacts directory path."""
        return self._get_config_path("planning_artifacts", self.DEFAULT_PLANNING_ARTIFACTS)

    @cached_property
    def implementation_artifacts(self) -> Path:
        """Return the implementation artifacts directory path."""
        return self._get_config_path(
            "implementation_artifacts", self.DEFAULT_IMPLEMENTATION_ARTIFACTS
        )

    @cached_property
    def project_knowledge(self) -> Path:
        """Return the project knowledge directory path."""
        return self._get_config_path("project_knowledge", self.DEFAULT_PROJECT_KNOWLEDGE)

    # Planning Artifacts

    @cached_property
    def epics_dir(self) -> Path:
        """Directory for epic definition files (planning-artifacts)."""
        if "epics" in self._config:
            return self._resolve_path(self._config["epics"])
        return self.planning_artifacts

    @cached_property
    def stories_dir(self) -> Path:
        """Return the stories directory path (implementation-artifacts)."""
        return self.implementation_artifacts

    # Implementation Artifacts

    @cached_property
    def validations_dir(self) -> Path:
        """Return the story validations directory path."""
        return self.implementation_artifacts / "story-validations"

    @cached_property
    def code_reviews_dir(self) -> Path:
        """Return the code reviews directory path."""
        return self.implementation_artifacts / "code-reviews"

    @cached_property
    def retrospectives_dir(self) -> Path:
        """Return the retrospectives directory path."""
        return self.implementation_artifacts / "retrospectives"

    # Project Knowledge

    @cached_property
    def prd_file(self) -> Path:
        """Return the PRD file path."""
        return self.project_knowledge / "prd.md"

    @cached_property
    def architecture_file(self) -> Path:
        """Return the architecture document file path."""
        return self.project_knowledge / "architecture.md"

    @cached_property
    def architecture_files(self) -> list[Path]:
        """Return all architecture document file paths."""
        return sorted(self.project_knowledge.glob("architecture*.md"))

    @cached_property
    def project_context_file(self) -> Path:
        """Return the project context file path."""
        return self.project_knowledge / "project_context.md"

    # Internal State

    @cached_property
    def bmad_assist_dir(self) -> Path:
        """Return the .bmad-assist-lite internal state directory path."""
        return self.project_root / ".bmad-assist-lite"

    @cached_property
    def state_file(self) -> Path:
        """Return the state.yaml file path."""
        return self.bmad_assist_dir / "state.yaml"

    @cached_property
    def sprint_status_file(self) -> Path:
        """Return the sprint-status.yaml file path."""
        return self.implementation_artifacts / "sprint-status.yaml"

    @cached_property
    def plugins_dir(self) -> Path:
        """Local plugins directory for project-specific plugins."""
        return self.bmad_assist_dir / "plugins"

    @cached_property
    def cache_dir(self) -> Path:
        """Return the cache directory path."""
        return self.bmad_assist_dir / "cache"

    @cached_property
    def logs_dir(self) -> Path:
        """Return the logs directory path."""
        return self.bmad_assist_dir / "logs"

    @cached_property
    def lib_docs_dir(self) -> Path:
        """Directory for cached library documentation from Context7."""
        return self.cache_dir / "lib-docs"

    # Helpers

    def ensure_directories(self) -> None:
        """Create all output directories if they don't exist."""
        directories = [
            self.output_folder,
            self.planning_artifacts,
            self.implementation_artifacts,
            self.epics_dir,
            self.validations_dir,
            self.code_reviews_dir,
            self.retrospectives_dir,
            self.bmad_assist_dir,
            self.cache_dir,
        ]
        for directory in directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                raise PermissionError(f"Cannot create directory '{directory}': {e}") from e

    def __repr__(self) -> str:
        """Return string representation."""
        return f"ProjectPaths(project_root={self.project_root})"


# Singleton
_paths_instance: ProjectPaths | None = None


def init_paths(project_root: Path, config: dict[str, Any] | None = None) -> ProjectPaths:
    """Initialize the paths singleton."""
    global _paths_instance
    _paths_instance = ProjectPaths(project_root, config)
    logger.debug("Initialized paths for project: %s", project_root)
    return _paths_instance


def get_paths() -> ProjectPaths:
    """Get the paths singleton instance."""
    if _paths_instance is None:
        raise RuntimeError("Paths not initialized. Call init_paths() first.")
    return _paths_instance


def _reset_paths() -> None:
    """Reset paths singleton for testing."""
    global _paths_instance
    _paths_instance = None
