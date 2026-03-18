"""Tests for bmad_assist_lite.core.paths."""

import pytest

from bmad_assist_lite.core.paths import ProjectPaths, _reset_paths, get_paths, init_paths

# ============================================================================
# ProjectPaths defaults
# ============================================================================


class TestDefaultPaths:
    """Tests for default path resolution."""

    def test_default_paths(self, tmp_path):
        """Default paths resolve relative to project_root."""
        paths = ProjectPaths(tmp_path)
        resolved_root = tmp_path.resolve()

        assert paths.project_root == resolved_root
        assert paths.output_folder == resolved_root / "_bmad-output"
        assert paths.planning_artifacts == resolved_root / "_bmad-output" / "planning-artifacts"
        assert paths.project_knowledge == resolved_root / "docs"
        assert (
            paths.implementation_artifacts
            == resolved_root / "_bmad-output" / "implementation-artifacts"
        )

    def test_state_file_path(self, tmp_path):
        """state_file resolves under .bmad-assist-lite directory."""
        paths = ProjectPaths(tmp_path)
        assert paths.state_file == tmp_path.resolve() / ".bmad-assist-lite" / "state.yaml"


# ============================================================================
# Custom paths via config dict
# ============================================================================


class TestCustomPaths:
    """Tests for config-overridden paths."""

    def test_custom_output_folder(self, tmp_path):
        """Passing output_folder in config overrides the default."""
        config = {"output_folder": "{project-root}/custom-output"}
        paths = ProjectPaths(tmp_path, config)
        assert paths.output_folder == tmp_path.resolve() / "custom-output"

    def test_custom_project_knowledge(self, tmp_path):
        """Passing project_knowledge in config overrides the default."""
        config = {"project_knowledge": "{project-root}/knowledge"}
        paths = ProjectPaths(tmp_path, config)
        assert paths.project_knowledge == tmp_path.resolve() / "knowledge"

    def test_relative_path_config(self, tmp_path):
        """A relative path (no {project-root} prefix) resolves from project_root."""
        config = {"output_folder": "my-output"}
        paths = ProjectPaths(tmp_path, config)
        assert paths.output_folder == (tmp_path / "my-output").resolve()


# ============================================================================
# Singleton
# ============================================================================


class TestPathsSingleton:
    """Tests for the paths singleton lifecycle."""

    def test_paths_singleton(self, tmp_path):
        """init_paths() then get_paths() returns the same instance."""
        _reset_paths()
        initialized = init_paths(tmp_path)
        retrieved = get_paths()
        assert retrieved is initialized
        assert retrieved.project_root == tmp_path.resolve()

    def test_paths_not_initialized_raises(self):
        """get_paths() before init_paths() raises RuntimeError."""
        _reset_paths()
        with pytest.raises(RuntimeError, match="not initialized"):
            get_paths()

    def test_reset_clears_singleton(self, tmp_path):
        """_reset_paths() clears the singleton so get_paths() raises again."""
        init_paths(tmp_path)
        _reset_paths()
        with pytest.raises(RuntimeError, match="not initialized"):
            get_paths()
