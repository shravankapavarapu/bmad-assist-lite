"""Tests for bmad_assist_lite.core.config."""

import pytest

from bmad_assist_lite.core.config import (
    Config,
    LoopConfig,
    _deep_merge,
    _reset_config,
    get_config,
    get_phase_timeout,
    load_config,
)
from bmad_assist_lite.core.exceptions import ConfigError


# ============================================================================
# load_config
# ============================================================================


class TestLoadConfig:
    """Tests for load_config and the Config model."""

    def test_load_minimal_config(self):
        """Loading config with only providers.master succeeds and uses defaults."""
        _reset_config()
        cfg = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
        assert isinstance(cfg, Config)
        assert cfg.providers.master.provider == "claude"
        assert cfg.providers.master.model == "opus"
        assert cfg.providers.multi == []
        assert cfg.timeout == 300
        assert cfg.timeouts is None
        assert cfg.parallel_delay == 1.0

    def test_load_full_config(self):
        """Loading config with all sections populates every field."""
        _reset_config()
        data = {
            "providers": {
                "master": {"provider": "claude", "model": "opus", "model_name": "Opus"},
                "multi": [
                    {"provider": "gemini", "model": "gemini-2.5-flash"},
                    {"provider": "claude", "model": "sonnet"},
                ],
            },
            "timeout": 600,
            "timeouts": {
                "default": 300,
                "dev_story": 900,
                "create_story": 120,
            },
            "paths": {
                "output_folder": "/tmp/custom-output",
            },
            "loop": {
                "story": [
                    "create_story",
                    "validate_story",
                    "validate_story_synthesis",
                    "dev_story",
                    "code_review",
                    "code_review_synthesis",
                ],
                "epic_teardown": ["retrospective"],
            },
            "parallel_delay": 2.5,
        }
        cfg = load_config(data)

        assert cfg.providers.master.provider == "claude"
        assert cfg.providers.master.model == "opus"
        assert cfg.providers.master.model_name == "Opus"
        assert len(cfg.providers.multi) == 2
        assert cfg.providers.multi[0].provider == "gemini"
        assert cfg.timeout == 600
        assert cfg.timeouts is not None
        assert cfg.timeouts.dev_story == 900
        assert cfg.timeouts.create_story == 120
        assert cfg.parallel_delay == 2.5
        assert cfg.paths.output_folder == "/tmp/custom-output"
        assert len(cfg.loop.story) == 6
        assert cfg.loop.epic_teardown == ["retrospective"]

    def test_config_missing_providers_fails(self):
        """ConfigError is raised when providers section is missing."""
        _reset_config()
        with pytest.raises(ConfigError, match="validation failed"):
            load_config({"timeout": 300})

    def test_config_missing_master_fails(self):
        """ConfigError when providers exists but master is absent."""
        _reset_config()
        with pytest.raises(ConfigError, match="validation failed"):
            load_config({"providers": {"multi": []}})


# ============================================================================
# _deep_merge
# ============================================================================


class TestDeepMerge:
    """Tests for the _deep_merge helper."""

    def test_flat_merge(self):
        """Override replaces non-dict values in base."""
        base = {"a": 1, "b": 2}
        override = {"b": 99, "c": 3}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 99, "c": 3}

    def test_nested_merge(self):
        """Nested dicts are merged recursively."""
        base = {"x": {"a": 1, "b": 2}, "y": 10}
        override = {"x": {"b": 99, "c": 3}}
        result = _deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 99, "c": 3}, "y": 10}

    def test_deep_merge_does_not_mutate_originals(self):
        """Neither base nor override is mutated."""
        base = {"x": {"a": 1}}
        override = {"x": {"b": 2}}
        _deep_merge(base, override)
        assert base == {"x": {"a": 1}}
        assert override == {"x": {"b": 2}}


# ============================================================================
# get_phase_timeout
# ============================================================================


class TestGetPhaseTimeout:
    """Tests for get_phase_timeout."""

    def test_get_phase_timeout_with_timeouts(self):
        """When config.timeouts is set, per-phase timeout is returned."""
        _reset_config()
        cfg = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "timeouts": {"default": 200, "dev_story": 999},
            }
        )
        assert get_phase_timeout(cfg, "dev_story") == 999
        # create_story has a phase-specific default (900s) since it needs longer
        assert get_phase_timeout(cfg, "create_story") == 900
        # Unknown phase falls back to timeouts.default
        assert get_phase_timeout(cfg, "retrospective") == 200

    def test_get_phase_timeout_without_timeouts(self):
        """When config.timeouts is None, falls back to config.timeout."""
        _reset_config()
        cfg = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "timeout": 450,
            }
        )
        assert cfg.timeouts is None
        assert get_phase_timeout(cfg, "dev_story") == 450
        assert get_phase_timeout(cfg, "create_story") == 450


# ============================================================================
# get_config singleton
# ============================================================================


class TestGetConfigSingleton:
    """Tests for the config singleton."""

    @pytest.mark.no_auto_config
    def test_get_config_before_load_raises(self):
        """get_config() raises ConfigError when load_config() has not been called."""
        with pytest.raises(ConfigError, match="not loaded"):
            get_config()

    def test_get_config_returns_loaded(self):
        """After auto-fixture loads config, get_config() returns a Config."""
        cfg = get_config()
        assert isinstance(cfg, Config)
        assert cfg.providers.master.provider == "claude"


# ============================================================================
# LoopConfig defaults
# ============================================================================


class TestLoopConfigDefaults:
    """Tests for default loop configuration."""

    def test_loop_config_defaults(self):
        """Default LoopConfig has 6 story phases and 1 epic_teardown phase."""
        lc = LoopConfig()
        assert lc.story == [
            "create_story",
            "validate_story",
            "validate_story_synthesis",
            "dev_story",
            "code_review",
            "code_review_synthesis",
        ]
        assert lc.epic_teardown == ["retrospective"]
        assert len(lc.story) == 6
        assert len(lc.epic_teardown) == 1
