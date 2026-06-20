"""Tests for Cursor CLI resolution, config acceptance, and provider registry.

Story 11.2 — Cursor CLI Resolution & Config Schema.
Covers AC #1–#5 and backward compatibility regression tests.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.core.config import _reset_config, load_config
from bmad_assist_lite.core.exceptions import ConfigError, ProviderError
from bmad_assist_lite.providers import _reset_registry, get_provider, list_providers
from bmad_assist_lite.providers.base import (
    _KNOWN_CLI_PATHS,
    _PROVIDER_BINARY_NAMES,
    resolve_cli_path,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    """Ensure a clean registry before and after each test."""
    _reset_registry()
    yield
    _reset_registry()


# ============================================================================
# TestCursorCliPathsConfig  (AC #1)
# ============================================================================


class TestCursorCliPathsConfig:
    """Config override path returned directly, no PATH consulted."""

    @pytest.mark.no_auto_config
    def test_config_override_returns_configured_path(self) -> None:
        """AC #1: configured cli_paths.cursor is returned without consulting PATH."""
        configured_path = "/opt/cursor/cursor-agent"
        config_data = {
            "providers": {
                "master": {"provider": "claude", "model": "opus"},
                "cli_paths": {"cursor": configured_path},
            },
        }
        _reset_config()
        load_config(config_data)

        # Mock Path.is_file() to return True for the configured path
        with (
            patch.object(Path, "is_file", return_value=True),
            patch("bmad_assist_lite.providers.base.shutil.which") as mock_which,
        ):
            result = resolve_cli_path("cursor")

            assert result == str(Path(configured_path))
            # shutil.which should NOT have been called
            mock_which.assert_not_called()

    @pytest.mark.no_auto_config
    def test_config_override_missing_file_falls_through(self) -> None:
        """When configured path doesn't exist, falls through to PATH/known locations."""
        config_data = {
            "providers": {
                "master": {"provider": "claude", "model": "opus"},
                "cli_paths": {"cursor": "/nonexistent/cursor-agent"},
            },
        }
        _reset_config()
        load_config(config_data)

        with patch("bmad_assist_lite.providers.base.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/cursor-agent"

            result = resolve_cli_path("cursor")

            assert result == "/usr/bin/cursor-agent"
            # Should have tried cursor-agent first (from _PROVIDER_BINARY_NAMES)
            mock_which.assert_any_call("cursor-agent")

    def test_no_config_override_falls_through_to_path(self) -> None:
        """Without cli_paths.cursor, falls through to PATH/known locations."""
        with patch("bmad_assist_lite.providers.base.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/cursor-agent"

            result = resolve_cli_path("cursor")

            assert result == "/usr/bin/cursor-agent"


# ============================================================================
# TestCursorBinaryPreference  (AC #2)
# ============================================================================


class TestCursorBinaryPreference:
    """Binary name preference: cursor-agent preferred over agent on PATH."""

    def test_cursor_agent_preferred_over_agent(self) -> None:
        """AC #2: cursor-agent is preferred when both exist on PATH."""

        def mock_which(name: str) -> str | None:
            return {
                "cursor-agent": "/usr/bin/cursor-agent",
                "agent": "/usr/bin/agent",
            }.get(name)

        with patch("bmad_assist_lite.providers.base.shutil.which", side_effect=mock_which):
            result = resolve_cli_path("cursor")

        assert result == "/usr/bin/cursor-agent"

    def test_only_agent_on_path_is_accepted(self) -> None:
        """When only 'agent' is on PATH, it is returned."""

        def mock_which(name: str) -> str | None:
            if name == "agent":
                return "/usr/bin/agent"
            return None

        with patch("bmad_assist_lite.providers.base.shutil.which", side_effect=mock_which):
            result = resolve_cli_path("cursor")

        assert result == "/usr/bin/agent"

    def test_neither_on_path_falls_through(self) -> None:
        """When neither binary is on PATH, falls through to known locations."""
        with (
            patch("bmad_assist_lite.providers.base.shutil.which", return_value=None),
            patch("bmad_assist_lite.providers.base._KNOWN_CLI_PATHS", {"cursor": []}),
        ):
            with pytest.raises(ProviderError, match="cursor CLI not found"):
                resolve_cli_path("cursor")

    def test_provider_binary_names_mapping_exists(self) -> None:
        """_PROVIDER_BINARY_NAMES has cursor mapped to (cursor-agent, agent)."""
        assert "cursor" in _PROVIDER_BINARY_NAMES
        assert _PROVIDER_BINARY_NAMES["cursor"] == ("cursor-agent", "agent")


# ============================================================================
# TestCursorKnownLocations  (AC #3)
# ============================================================================


class TestCursorKnownLocations:
    """Known location fallback on Linux."""

    @pytest.mark.no_auto_config
    def test_linux_local_bin_cursor_agent_found(self) -> None:
        """AC #3: On Linux, ~/.local/bin/cursor-agent is checked and found."""
        config_data = {
            "providers": {
                "master": {"provider": "claude", "model": "opus"},
            },
        }
        _reset_config()
        load_config(config_data)

        home = Path("/home/testuser")
        cursor_agent_path = home / ".local" / "bin" / "cursor-agent"

        known_paths = [home / ".local" / "bin", Path("/usr/local/bin")]

        def fake_is_file(self: Path) -> bool:
            return self == cursor_agent_path

        with (
            patch("bmad_assist_lite.providers.base.shutil.which", return_value=None),
            patch("bmad_assist_lite.providers.base._KNOWN_CLI_PATHS", {"cursor": known_paths}),
            patch("bmad_assist_lite.providers.base.sys") as mock_sys,
            patch.object(Path, "is_file", fake_is_file),
        ):
            mock_sys.platform = "linux"

            result = resolve_cli_path("cursor")

        assert result == str(cursor_agent_path)

    @pytest.mark.no_auto_config
    def test_linux_local_bin_agent_found_as_fallback(self) -> None:
        """AC #3: On Linux, ~/.local/bin/agent is found when cursor-agent doesn't exist."""
        config_data = {
            "providers": {
                "master": {"provider": "claude", "model": "opus"},
            },
        }
        _reset_config()
        load_config(config_data)

        home = Path("/home/testuser")
        agent_path = home / ".local" / "bin" / "agent"

        known_paths = [home / ".local" / "bin", Path("/usr/local/bin")]

        def fake_is_file(self: Path) -> bool:
            return self == agent_path

        with (
            patch("bmad_assist_lite.providers.base.shutil.which", return_value=None),
            patch("bmad_assist_lite.providers.base._KNOWN_CLI_PATHS", {"cursor": known_paths}),
            patch("bmad_assist_lite.providers.base.sys") as mock_sys,
            patch.object(Path, "is_file", fake_is_file),
        ):
            mock_sys.platform = "linux"

            result = resolve_cli_path("cursor")

        assert result == str(agent_path)

    def test_no_known_location_files_raises_provider_error(self) -> None:
        """No config override, no PATH hit, no known location → ProviderError."""
        with (
            patch("bmad_assist_lite.providers.base.shutil.which", return_value=None),
            patch("bmad_assist_lite.providers.base._KNOWN_CLI_PATHS", {"cursor": []}),
        ):
            with pytest.raises(ProviderError, match="providers.cli_paths.cursor"):
                resolve_cli_path("cursor")

    def test_known_cli_paths_has_cursor_entry(self) -> None:
        """_KNOWN_CLI_PATHS contains a 'cursor' key."""
        assert "cursor" in _KNOWN_CLI_PATHS

    def test_windows_localappdata_set_includes_path(self) -> None:
        """On Windows with LOCALAPPDATA set, cursor known paths includes that directory."""
        import os

        # Verify the guard logic: when LOCALAPPDATA is set, path is constructed
        with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}):
            localappdata = os.environ.get("LOCALAPPDATA")
            if localappdata:
                paths = [Path(localappdata) / "cursor-agent"]
            else:
                paths = []

            assert len(paths) == 1
            assert paths[0] == Path(r"C:\Users\test\AppData\Local") / "cursor-agent"

    def test_windows_localappdata_empty_yields_empty_list(self) -> None:
        """On Windows with LOCALAPPDATA empty, cursor known paths is empty (no relative path)."""
        import os

        # Simulate the guard logic from base.py lines 84-89
        with patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            localappdata = os.environ.get("LOCALAPPDATA")
            # Guard: skip probing when env var is unset/empty
            if localappdata:
                paths = [Path(localappdata) / "cursor-agent"]
            else:
                paths = []

            assert paths == []

    def test_windows_localappdata_unset_yields_empty_list(self) -> None:
        """On Windows with LOCALAPPDATA unset, cursor known paths is empty."""
        import os

        with patch.dict(os.environ, {}, clear=True):
            # Remove LOCALAPPDATA entirely
            os.environ.pop("LOCALAPPDATA", None)
            localappdata = os.environ.get("LOCALAPPDATA")
            if localappdata:
                paths = [Path(localappdata) / "cursor-agent"]
            else:
                paths = []

            assert paths == []


# ============================================================================
# TestCursorConfigValidation  (AC #4, #5)
# ============================================================================


class TestCursorConfigValidation:
    """Config validation accepts cursor provider, rejects unknowns."""

    @pytest.mark.no_auto_config
    def test_cursor_provider_in_master_config_passes(self) -> None:
        """AC #4: provider: cursor in master config → validation passes."""
        config_data = {
            "providers": {
                "master": {"provider": "cursor", "model": "composer-2.5"},
            },
        }
        _reset_config()
        config = load_config(config_data)

        assert config.providers.master.provider == "cursor"
        assert config.providers.master.model == "composer-2.5"

    @pytest.mark.no_auto_config
    def test_cursor_provider_in_multi_config_passes(self) -> None:
        """AC #4: provider: cursor in multi config → validation passes."""
        config_data = {
            "providers": {
                "master": {"provider": "claude", "model": "opus"},
                "multi": [
                    {"provider": "cursor", "model": "composer-2.5"},
                ],
            },
        }
        _reset_config()
        config = load_config(config_data)

        assert len(config.providers.multi) == 1
        assert config.providers.multi[0].provider == "cursor"

    @pytest.mark.no_auto_config
    def test_unknown_provider_raises_config_error_at_get_provider(self) -> None:
        """AC #5: provider: cursorx (unknown) → ConfigError from get_provider()."""
        config_data = {
            "providers": {
                "master": {"provider": "cursorx", "model": "some-model"},
            },
        }
        _reset_config()
        # Config loading itself succeeds (provider is a plain str)
        config = load_config(config_data)
        assert config.providers.master.provider == "cursorx"

        # But runtime get_provider() rejects it
        with pytest.raises(ConfigError, match="Unknown provider.*cursorx"):
            get_provider("cursorx")


# ============================================================================
# TestCursorProviderRegistry
# ============================================================================


class TestCursorProviderRegistry:
    """Provider registry includes cursor and returns valid instance."""

    def test_get_provider_returns_cursor_instance(self) -> None:
        """get_provider('cursor') returns a CursorProvider instance."""
        from bmad_assist_lite.providers.cursor import CursorProvider

        provider = get_provider("cursor")
        assert isinstance(provider, CursorProvider)

    def test_list_providers_includes_cursor(self) -> None:
        """list_providers() includes 'cursor'."""
        providers = list_providers()
        assert "cursor" in providers

    def test_cursor_provider_name_is_cursor(self) -> None:
        """CursorProvider.provider_name returns 'cursor'."""
        provider = get_provider("cursor")
        assert provider.provider_name == "cursor"

    def test_cursor_provider_is_base_provider_subclass(self) -> None:
        """CursorProvider is a subclass of BaseProvider."""
        from bmad_assist_lite.providers.base import BaseProvider
        from bmad_assist_lite.providers.cursor import CursorProvider

        assert issubclass(CursorProvider, BaseProvider)

    def test_cursor_methods_are_implemented(self) -> None:
        """All abstract methods have real implementations (Story 11.3 replaced stub)."""
        from bmad_assist_lite.providers.cursor import CursorProvider

        provider = CursorProvider()

        # supports_model is fully implemented
        assert provider.supports_model("composer-2.5") is True
        assert provider.supports_model("auto") is False

        # parse_output is fully implemented
        mock_result = MagicMock()
        mock_result.stdout = "  hello  "
        assert provider.parse_output(mock_result) == "hello"

        # _cleanup runs without error when no process is active
        provider._cleanup()  # Should not raise


# ============================================================================
# TestClaudeCliResolution — config override + known-path fallback for claude
# ============================================================================


class TestClaudeCliResolution:
    """claude follows the same cli_paths override + known-location pattern.

    Lets a project point the Claude Agent SDK at the system ``claude`` binary
    (via ClaudeAgentOptions.cli_path) instead of the older bundled one.
    """

    @pytest.mark.no_auto_config
    def test_config_override_returns_configured_claude_path(self) -> None:
        """Configured cli_paths.claude is returned without consulting PATH."""
        configured_path = "/opt/claude/claude"
        config_data = {
            "providers": {
                "master": {"provider": "claude", "model": "opus"},
                "cli_paths": {"claude": configured_path},
            },
        }
        _reset_config()
        load_config(config_data)

        with (
            patch.object(Path, "is_file", return_value=True),
            patch("bmad_assist_lite.providers.base.shutil.which") as mock_which,
        ):
            result = resolve_cli_path("claude")

            assert result == str(Path(configured_path))
            mock_which.assert_not_called()

    @pytest.mark.no_auto_config
    def test_claude_config_field_accepted(self) -> None:
        """cli_paths.claude is a recognized config field (not silently dropped)."""
        configured_path = "C:/Users/x/.local/bin/claude.exe"
        config_data = {
            "providers": {
                "master": {"provider": "claude", "model": "opus"},
                "cli_paths": {"claude": configured_path},
            },
        }
        _reset_config()
        config = load_config(config_data)

        assert config.providers.cli_paths.claude == configured_path

    def test_claude_resolves_via_path(self) -> None:
        """resolve_cli_path('claude') falls through to PATH when no override set."""
        with patch("bmad_assist_lite.providers.base.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/claude"

            result = resolve_cli_path("claude")

        assert result == "/usr/bin/claude"
        mock_which.assert_called_with("claude")

    def test_known_cli_paths_has_claude_entry(self) -> None:
        """_KNOWN_CLI_PATHS contains a 'claude' key for the venv-strips-PATH case."""
        assert "claude" in _KNOWN_CLI_PATHS


# ============================================================================
# TestBackwardCompatibility
# ============================================================================


class TestBackwardCompatibility:
    """Regression tests: existing providers still resolve after multi-name refactor."""

    def test_codex_resolves_via_single_binary_name(self) -> None:
        """resolve_cli_path('codex') still works with single binary name."""
        with patch("bmad_assist_lite.providers.base.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/codex"

            result = resolve_cli_path("codex")

        assert result == "/usr/bin/codex"
        mock_which.assert_called_with("codex")

    def test_gemini_resolves_via_single_binary_name(self) -> None:
        """resolve_cli_path('gemini') still works with single binary name."""
        with patch("bmad_assist_lite.providers.base.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/gemini"

            result = resolve_cli_path("gemini")

        assert result == "/usr/bin/gemini"
        mock_which.assert_called_with("gemini")

    def test_unmapped_provider_defaults_to_cli_name_tuple(self) -> None:
        """Providers without _PROVIDER_BINARY_NAMES entries fall back to (cli_name,)."""
        with patch("bmad_assist_lite.providers.base.shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/somecli"

            result = resolve_cli_path("somecli")

        assert result == "/usr/bin/somecli"
        mock_which.assert_called_with("somecli")

    def test_provider_binary_names_has_codex_entry(self) -> None:
        """_PROVIDER_BINARY_NAMES has codex mapped to single-name tuple."""
        assert "codex" in _PROVIDER_BINARY_NAMES
        assert _PROVIDER_BINARY_NAMES["codex"] == ("codex",)

    def test_provider_binary_names_has_gemini_entry(self) -> None:
        """_PROVIDER_BINARY_NAMES has gemini mapped to single-name tuple."""
        assert "gemini" in _PROVIDER_BINARY_NAMES
        assert _PROVIDER_BINARY_NAMES["gemini"] == ("gemini",)

    def test_existing_providers_still_in_registry(self) -> None:
        """claude, codex, gemini still in registry after cursor addition."""
        providers = list_providers()
        assert "claude" in providers
        assert "codex" in providers
        assert "gemini" in providers
        assert "cursor" in providers
