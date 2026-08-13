"""Tests for bmad_assist_lite.core.config."""

import pytest

from bmad_assist_lite.core.config import (
    Config,
    EpicKnowledgeConfig,
    LoopConfig,
    QualityGateConfig,
    SessionReuseConfig,
    SpeedConfig,
    TimeoutsConfig,
    _deep_merge,
    _reset_config,
    get_config,
    get_phase_timeout,
    load_config,
    notch_down_effort,
)
from bmad_assist_lite.core.exceptions import ConfigError
from bmad_assist_lite.core.state import Phase

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
        # Changed in 7.1: was 200 (fell through to timeouts.default), now 600
        # because retrospective has a phase-specific default that wins over timeouts.default
        assert get_phase_timeout(cfg, "retrospective") == 600

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


class TestQualityGateConfig:
    """Tests for QualityGateConfig."""

    def test_quality_gate_defaults(self):
        """Default QualityGateConfig has all None commands and 120s timeout."""
        qg = QualityGateConfig()
        assert qg.lint is None
        assert qg.typecheck is None
        assert qg.build is None
        assert qg.test is None
        assert qg.command_timeout == 120

    def test_quality_gate_in_config(self):
        """quality_gate config section is parsed correctly."""
        _reset_config()
        cfg = load_config({
            "providers": {"master": {"provider": "claude", "model": "opus"}},
            "quality_gate": {
                "lint": "ruff check src/",
                "test": "pytest",
                "command_timeout": 60,
            },
        })
        assert cfg.quality_gate is not None
        assert cfg.quality_gate.lint == "ruff check src/"
        assert cfg.quality_gate.test == "pytest"
        assert cfg.quality_gate.command_timeout == 60

    def test_quality_gate_optional(self):
        """quality_gate is None by default."""
        _reset_config()
        cfg = load_config({
            "providers": {"master": {"provider": "claude", "model": "opus"}},
        })
        assert cfg.quality_gate is None


class TestLoopConfigDefaults:
    """Tests for default loop configuration."""

    def test_loop_config_defaults(self):
        """Default LoopConfig has 7 story phases and 2 epic_teardown phases."""
        lc = LoopConfig()
        assert lc.story == [
            "create_story",
            "validate_story",
            "validate_story_synthesis",
            "dev_story",
            "code_review",
            "code_review_synthesis",
            "quality_gate",
        ]
        assert lc.epic_teardown == ["epic_quality_gate", "retrospective"]
        assert len(lc.story) == 7
        assert len(lc.epic_teardown) == 2


# ============================================================================
# Phase defaults coverage regression test
# ============================================================================


class TestPhaseDefaultsCoverage:
    """Regression test: every Phase enum member must have a _PHASE_DEFAULTS entry.

    Uses the Phase enum from state.py for 100% coverage of all phases,
    including detour phases like fix_quality_gate that aren't in LoopConfig lists.
    """

    @pytest.mark.parametrize("phase", list(Phase), ids=lambda p: p.value)
    def test_all_phases_have_defaults(self, phase: Phase):
        """Each phase in the Phase enum must have an entry in _PHASE_DEFAULTS."""
        assert phase.value in TimeoutsConfig._PHASE_DEFAULTS, (
            f"Phase '{phase.value}' is missing from TimeoutsConfig._PHASE_DEFAULTS. "
            f"Add a timeout default for this phase."
        )


# ============================================================================
# Direct unit tests for new/changed default values
# ============================================================================


class TestPhaseDefaultValues:
    """Direct tests for specific _PHASE_DEFAULTS values.

    Uses TimeoutsConfig() directly (not get_phase_timeout()) to bypass
    the autouse reset_config_singleton fixture which loads MINIMAL_CONFIG_DATA
    without a timeouts section.
    """

    def test_retrospective_default_is_600(self):
        """Retrospective phase default timeout is 600s."""
        tc = TimeoutsConfig()
        assert tc.get_timeout("retrospective") == 600

    def test_code_review_synthesis_default_is_1200(self):
        """code_review_synthesis phase default timeout is 1200s."""
        tc = TimeoutsConfig()
        assert tc.get_timeout("code_review_synthesis") == 1200

    def test_fix_quality_gate_default_is_900(self):
        """fix_quality_gate phase default timeout is 900s."""
        tc = TimeoutsConfig()
        assert tc.get_timeout("fix_quality_gate") == 900

    def test_explicit_config_overrides_phase_default(self):
        """Explicitly setting retrospective timeout overrides the phase default."""
        _reset_config()
        cfg = load_config({
            "providers": {"master": {"provider": "claude", "model": "opus"}},
            "timeouts": {"retrospective": 120},
        })
        assert cfg.timeouts is not None
        assert cfg.timeouts.get_timeout("retrospective") == 120

    def test_unknown_phase_falls_back_to_global_default(self):
        """A phase not in _PHASE_DEFAULTS falls back to the global default."""
        tc = TimeoutsConfig(default=250)
        assert tc.get_timeout("nonexistent_phase") == 250


# ============================================================================
# Self-review detection (REQ-11.2)
# ============================================================================


class TestSelfReviewDetection:
    """providers.multi empty (or master-equal) means the master reviews itself."""

    def test_empty_multi_is_detected(self):
        """An explicit empty multi list yields a warning message."""
        from bmad_assist_lite.core.config import self_review_warning

        _reset_config()
        cfg = load_config(
            {"providers": {"master": {"provider": "claude", "model": "opus"}, "multi": []}}
        )
        assert self_review_warning(cfg) is not None

    def test_missing_multi_key_is_detected(self):
        """Omitting providers.multi is the default case and is detected."""
        from bmad_assist_lite.core.config import self_review_warning

        _reset_config()
        cfg = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
        assert self_review_warning(cfg) is not None

    def test_entry_equal_to_master_is_detected(self):
        """NEG: a reviewer with the same provider AND model as master is self-review."""
        from bmad_assist_lite.core.config import self_review_warning

        _reset_config()
        cfg = load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "opus"},
                    "multi": [{"provider": "claude", "model": "opus"}],
                }
            }
        )
        assert self_review_warning(cfg) is not None

    def test_same_provider_different_model_is_not_detected(self):
        """NEG: claude/sonnet reviewing claude/opus is an independent reviewer."""
        from bmad_assist_lite.core.config import self_review_warning

        _reset_config()
        cfg = load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "opus"},
                    "multi": [{"provider": "claude", "model": "sonnet"}],
                }
            }
        )
        assert self_review_warning(cfg) is None

    def test_message_names_the_condition_and_the_remedy(self):
        """The message must name the config key, the violation, and the fix."""
        from bmad_assist_lite.core.config import self_review_warning

        _reset_config()
        cfg = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
        message = self_review_warning(cfg)
        assert message is not None
        assert "providers.multi" in message
        assert "self-verification" in message
        assert "Fix:" in message
        assert "bmad-assist-lite.yaml" in message

    def test_phase_name_appears_when_supplied(self):
        """The phase-run variant names the phase that is degrading."""
        from bmad_assist_lite.core.config import self_review_warning

        _reset_config()
        cfg = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
        message = self_review_warning(cfg, phase="code_review")
        assert message is not None
        assert "code_review" in message


@pytest.mark.no_auto_config
class TestSelfReviewWarningAtLoad:
    """The warning fires at config load, not only when the phase runs."""

    def test_warning_logged_on_load(self, caplog):
        """load_config() with no multi logs a WARNING."""
        import logging

        _reset_config()
        caplog.set_level(logging.WARNING, logger="bmad_assist_lite.core.config")
        load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})

        text = "\n".join(r.message for r in caplog.records)
        assert "providers.multi" in text
        assert "Fix:" in text

    def test_no_warning_logged_for_valid_multi(self, caplog):
        """NEG: an independent reviewer produces no self-review warning."""
        import logging

        _reset_config()
        caplog.set_level(logging.WARNING, logger="bmad_assist_lite.core.config")
        load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "opus"},
                    "multi": [{"provider": "claude", "model": "sonnet"}],
                }
            }
        )

        text = "\n".join(r.message for r in caplog.records)
        assert "self-verification" not in text

    def test_explicit_empty_multi_still_loads(self, caplog):
        """G8 non-breaking: `multi: []` is a legal, loadable config."""
        _reset_config()
        cfg = load_config(
            {"providers": {"master": {"provider": "claude", "model": "opus"}, "multi": []}}
        )
        assert cfg.providers.multi == []


@pytest.mark.no_auto_config
class TestInitTemplateShipsAReviewer:
    """A fresh `init` config carries an independent reviewer (D-0005 option D)."""

    def _init_project(self, tmp_path):
        from typer.testing import CliRunner

        from bmad_assist_lite.cli import app

        result = CliRunner().invoke(app, ["init", "--project", str(tmp_path)])
        assert result.exit_code == 0, result.output
        return tmp_path / "bmad-assist-lite.yaml"

    def test_fresh_config_parses_with_non_empty_multi(self, tmp_path):
        """The emitted template loads and carries at least one reviewer."""
        from bmad_assist_lite.core.config import load_config_with_project

        config_path = self._init_project(tmp_path)
        _reset_config()
        cfg = load_config_with_project(
            tmp_path, global_config_path=tmp_path / "no-such-global.yaml"
        )
        assert config_path.exists()
        assert len(cfg.providers.multi) >= 1

    def test_fresh_config_reviewer_is_master_disjoint(self, tmp_path):
        """No shipped reviewer duplicates the master provider+model pair."""
        from bmad_assist_lite.core.config import load_config_with_project, self_review_warning

        self._init_project(tmp_path)
        _reset_config()
        cfg = load_config_with_project(
            tmp_path, global_config_path=tmp_path / "no-such-global.yaml"
        )
        assert self_review_warning(cfg) is None

    def test_fresh_config_ships_a_claude_reviewer(self, tmp_path):
        """D-0005 option D: a second Claude reviewer ships by default."""
        from bmad_assist_lite.core.config import load_config_with_project

        self._init_project(tmp_path)
        _reset_config()
        cfg = load_config_with_project(
            tmp_path, global_config_path=tmp_path / "no-such-global.yaml"
        )
        assert any(mc.provider == "claude" for mc in cfg.providers.multi)

    def test_fresh_config_names_no_codex(self, tmp_path):
        """NEG (G2 / rule 2.1): Codex appears nowhere in a fresh config."""
        from bmad_assist_lite.core.config import load_config_with_project

        config_path = self._init_project(tmp_path)
        _reset_config()
        cfg = load_config_with_project(
            tmp_path, global_config_path=tmp_path / "no-such-global.yaml"
        )
        assert cfg.providers.master.provider != "codex"
        assert all(mc.provider != "codex" for mc in cfg.providers.multi)
        assert cfg.providers.cli_paths.codex is None

        # Strip comments: "# claude, gemini, codex, cursor" documents the valid
        # values, it does not configure a provider.
        active_lines = [
            line.split("#")[0]
            for line in config_path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert not any("codex" in line.lower() for line in active_lines)

class TestSelfReviewWarningDoesNotOverFire:
    """A duplicate reviewer is only self-verification when NO independent one exists.

    Regression guard. The warning previously fired whenever ANY `providers.multi` entry
    matched the master, even alongside a genuinely independent reviewer — so a config
    pairing an independent model with a same-capability duplicate (BMAD-METHOD v6.11 asks
    review subagents to run at the session's capability) was flagged as a violation.

    That is alarm fatigue: a warning that fires on a good config trains operators to ignore
    the one that matters. The docstring already promised "Returns None when an independent
    reviewer is configured"; the implementation did not honour it.
    """

    def _config(self, multi):
        from bmad_assist_lite.core.config import load_config

        return load_config(
            {
                "providers": {
                    "master": {"provider": "claude", "model": "claude-opus-5"},
                    "multi": multi,
                }
            }
        )

    def test_independent_reviewer_alongside_a_duplicate_is_silent(self):
        from bmad_assist_lite.core.config import self_review_warning

        config = self._config(
            [
                {"provider": "claude", "model": "claude-fable-5"},
                {"provider": "claude", "model": "claude-opus-5"},
            ]
        )
        assert self_review_warning(config) is None

    def test_duplicate_alone_still_warns(self):
        from bmad_assist_lite.core.config import self_review_warning

        config = self._config([{"provider": "claude", "model": "claude-opus-5"}])
        warning = self_review_warning(config)
        assert warning is not None
        assert "multi" in warning.lower()

    def test_empty_multi_still_warns(self):
        from bmad_assist_lite.core.config import load_config, self_review_warning

        config = load_config(
            {"providers": {"master": {"provider": "claude", "model": "claude-opus-5"}}}
        )
        assert self_review_warning(config) is not None


# ============================================================================
# L2/L3 context-economy flags (goal-run5 Phase 2)
# ============================================================================


class TestSessionReuseConfig:
    """session_reuse.reviewer_self_resume (L2) — default OFF, opt-in."""

    def test_default_off(self):
        assert SessionReuseConfig().reviewer_self_resume is False

    def test_minimal_config_defaults_off(self):
        _reset_config()
        cfg = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
        assert cfg.session_reuse.reviewer_self_resume is False

    def test_opt_in_via_config(self):
        _reset_config()
        cfg = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "session_reuse": {"reviewer_self_resume": True},
            }
        )
        assert cfg.session_reuse.reviewer_self_resume is True

    def test_frozen(self):
        with pytest.raises((TypeError, ValueError, AttributeError)):
            SessionReuseConfig().reviewer_self_resume = True  # type: ignore[misc]


class TestEpicKnowledgeConfig:
    """epic_knowledge (L3) — default OFF, bounded when on."""

    def test_defaults(self):
        cfg = EpicKnowledgeConfig()
        assert cfg.enabled is False
        assert cfg.max_chars == 8000

    def test_minimal_config_defaults_off(self):
        _reset_config()
        cfg = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
        assert cfg.epic_knowledge.enabled is False

    def test_opt_in_via_config(self):
        _reset_config()
        cfg = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "epic_knowledge": {"enabled": True, "max_chars": 4000},
            }
        )
        assert cfg.epic_knowledge.enabled is True
        assert cfg.epic_knowledge.max_chars == 4000

    def test_max_chars_non_negative(self):
        with pytest.raises((ValueError, ConfigError)):
            EpicKnowledgeConfig(max_chars=-1)


class TestSpeedConfig:
    """speed.* (goal-run6 speed pack) — every flag default OFF, opt-in."""

    def test_defaults_all_off(self):
        cfg = SpeedConfig()
        assert cfg.structured_review is False
        assert cfg.delta_round2 is False
        assert cfg.lean_review is False
        assert cfg.remove_stagger is False

    def test_minimal_config_defaults_off(self):
        _reset_config()
        cfg = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
        assert cfg.speed.structured_review is False
        assert cfg.speed.delta_round2 is False
        assert cfg.speed.lean_review is False
        assert cfg.speed.remove_stagger is False

    def test_opt_in_via_config(self):
        _reset_config()
        cfg = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "speed": {
                    "structured_review": True,
                    "delta_round2": True,
                    "lean_review": True,
                    "remove_stagger": True,
                },
            }
        )
        assert cfg.speed.structured_review is True
        assert cfg.speed.delta_round2 is True
        assert cfg.speed.lean_review is True
        assert cfg.speed.remove_stagger is True

    def test_partial_opt_in_leaves_rest_off(self):
        _reset_config()
        cfg = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "speed": {"delta_round2": True},
            }
        )
        assert cfg.speed.delta_round2 is True
        assert cfg.speed.structured_review is False
        assert cfg.speed.lean_review is False
        assert cfg.speed.remove_stagger is False

    def test_frozen(self):
        with pytest.raises((TypeError, ValueError, AttributeError)):
            SpeedConfig().structured_review = True  # type: ignore[misc]


class TestNotchDownEffort:
    """SP-3 effort ladder: one defined step lower, floored at low."""

    def test_none_reads_as_medium(self):
        assert notch_down_effort(None) == "low"

    def test_each_notch(self):
        assert notch_down_effort("max") == "xhigh"
        assert notch_down_effort("xhigh") == "high"
        assert notch_down_effort("high") == "medium"
        assert notch_down_effort("medium") == "low"

    def test_floors_at_low(self):
        assert notch_down_effort("low") == "low"

    def test_unknown_floors_to_low(self):
        assert notch_down_effort("bogus") == "low"

    def test_case_insensitive(self):
        assert notch_down_effort("HIGH") == "medium"
