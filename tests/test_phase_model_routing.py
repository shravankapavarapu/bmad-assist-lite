"""Tests for per-phase master-model routing.

The routable-phase set is closed at three members. Review and implementation
phases must keep the master model whatever the config says and whoever asks —
these tests are the mechanical enforcement of that constraint.
"""

import logging
from pathlib import Path
from typing import Any

import pytest

from bmad_assist_lite.core.config import (
    NON_LLM_PHASES,
    NON_ROUTABLE_LLM_PHASES,
    PHASE_MODEL_ENV_PREFIX,
    ROUTABLE_PHASES,
    Config,
    PhaseModelsConfig,
    load_config,
    resolve_phase_model,
)
from bmad_assist_lite.core.exceptions import ConfigError
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.autonomy import AutonomyLevel
from bmad_assist_lite.loop.handlers.base import BaseHandler
from bmad_assist_lite.plugins.loader import load_all_plugins
from bmad_assist_lite.plugins.registry import PluginRegistry

ALL_PHASES = [p.value for p in Phase]

NON_ROUTABLE_PHASES = sorted(NON_ROUTABLE_LLM_PHASES | NON_LLM_PHASES)


def _config_data(
    phase_models: dict[str, str] | None = None,
    *,
    master_model: str = "opus",
    effort: str | None = "max",
    multi: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    providers: dict[str, Any] = {
        "master": {"provider": "claude", "model": master_model, "effort": effort},
    }
    if phase_models is not None:
        providers["phase_models"] = phase_models
    if multi is not None:
        providers["multi"] = multi
    return {"providers": providers}


def _load(phase_models: dict[str, str] | None = None, **kwargs: Any) -> Config:
    return load_config(_config_data(phase_models, **kwargs))


class _StubHandler(BaseHandler):
    """Minimal concrete handler used to exercise BaseHandler.get_model()."""

    autonomy = AutonomyLevel.READ_ONLY
    """Model routing is the subject here; the narrowest level keeps it inert."""

    def __init__(self, config: Config, phase: str) -> None:
        super().__init__(config, Path("."))
        self._phase = phase

    @property
    def phase_name(self) -> str:
        return self._phase

    def build_context(self, state: State) -> dict[str, Any]:
        return {}


# ============================================================================
# Classification — the three classes partition Phase
# ============================================================================


class TestPhaseClassification:
    """All 10 Phase members are classified, exactly once."""

    def test_routable_phases_exact_membership(self):
        assert sorted(ROUTABLE_PHASES) == [
            "create_story",
            "retrospective",
            "validate_story_synthesis",
        ]

    def test_non_routable_llm_phases_exact_membership(self):
        assert sorted(NON_ROUTABLE_LLM_PHASES) == [
            "code_review",
            "code_review_synthesis",
            "dev_story",
            "fix_quality_gate",
            "fix_review",
            "validate_story",
        ]

    def test_non_llm_phases_exact_membership(self):
        # dev_gate (SP-A0) is deterministic — it runs the real typecheck + the
        # story's own tests, no LLM — so it joins the non-LLM class.
        assert sorted(NON_LLM_PHASES) == ["dev_gate", "epic_quality_gate", "quality_gate"]

    def test_class_sizes(self):
        # ROUTABLE_PHASES stays closed at 3: the review fix phase joined the
        # NON-routable class, which is where model parity requires it to be.
        # NON_LLM grew to 3 with the deterministic dev_gate (SP-A0).
        assert (len(ROUTABLE_PHASES), len(NON_ROUTABLE_LLM_PHASES), len(NON_LLM_PHASES)) == (
            3,
            6,
            3,
        )

    def test_classes_are_pairwise_disjoint(self):
        assert not ROUTABLE_PHASES & NON_ROUTABLE_LLM_PHASES
        assert not ROUTABLE_PHASES & NON_LLM_PHASES
        assert not NON_ROUTABLE_LLM_PHASES & NON_LLM_PHASES

    def test_classes_partition_the_phase_enum(self):
        union = ROUTABLE_PHASES | NON_ROUTABLE_LLM_PHASES | NON_LLM_PHASES
        assert union == {p.value for p in Phase}

    def test_no_review_phase_is_routable(self):
        for phase in ("code_review", "code_review_synthesis", "validate_story", "dev_story"):
            assert phase not in ROUTABLE_PHASES


# ============================================================================
# REQ-01.1 — the config surface, and behaviour identity without it
# ============================================================================


class TestPhaseModelsConfigSurface:
    """The surface is additive: absent means today's behaviour."""

    @pytest.mark.no_auto_config
    def test_field_is_optional_and_defaults_to_none(self):
        config = _load()
        assert config.providers.phase_models is None

    @pytest.mark.no_auto_config
    @pytest.mark.parametrize("phase", ALL_PHASES)
    def test_no_phase_models_resolves_to_master_for_every_phase(self, phase):
        config = _load()
        assert resolve_phase_model(config, phase, env={}) == "opus"

    @pytest.mark.no_auto_config
    @pytest.mark.parametrize("phase", ALL_PHASES)
    def test_no_phase_models_handler_resolves_to_master_for_every_phase(self, phase):
        config = _load()
        assert _StubHandler(config, phase).get_model() == "opus"

    @pytest.mark.no_auto_config
    def test_named_phase_is_routed_and_others_inherit_master(self):
        config = _load({"create_story": "haiku"})
        assert resolve_phase_model(config, "create_story", env={}) == "haiku"
        for phase in ALL_PHASES:
            if phase != "create_story":
                assert resolve_phase_model(config, phase, env={}) == "opus"

    @pytest.mark.no_auto_config
    def test_accessor_signature_returns_none_when_unset(self):
        config = _load({"create_story": "haiku"})
        assert config.providers.phase_models is not None
        assert config.providers.phase_models.get_model("retrospective", env={}) is None

    @pytest.mark.no_auto_config
    def test_hyphenated_phase_name_is_normalised(self):
        config = _load({"create_story": "haiku"})
        assert resolve_phase_model(config, "create-story", env={}) == "haiku"


# ============================================================================
# REQ-01.1 crit 4 / REQ-01.3 crit 2, 2b — planted-bad configs
# ============================================================================


class TestPlantedBadConfigs:
    """Five non-routable LLM phases, two non-LLM phases, one non-phase key."""

    @pytest.mark.no_auto_config
    @pytest.mark.parametrize("phase", NON_ROUTABLE_PHASES)
    def test_non_routable_phase_warns_and_does_not_raise(self, phase, caplog):
        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.core.config"):
            config = _load({phase: "sonnet"})
        messages = [r.getMessage() for r in caplog.records]
        parity = [m for m in messages if "model parity" in m.lower() and phase in m]
        assert parity, f"no model-parity warning naming {phase}: {messages}"
        assert config is not None

    @pytest.mark.no_auto_config
    @pytest.mark.parametrize("phase", NON_ROUTABLE_PHASES)
    def test_non_routable_phase_resolves_to_master(self, phase):
        config = _load({phase: "sonnet"})
        assert resolve_phase_model(config, phase, env={}) == "opus"

    @pytest.mark.no_auto_config
    @pytest.mark.parametrize("phase", NON_ROUTABLE_PHASES)
    def test_accessor_returns_none_for_non_routable_phase(self, phase):
        """Called directly, as a non-BaseHandler caller would."""
        assert PhaseModelsConfig().get_model(phase, env={}) is None
        assert PhaseModelsConfig().get_model(phase, override="sonnet", env={}) is None
        assert (
            PhaseModelsConfig().get_model(
                phase, env={f"{PHASE_MODEL_ENV_PREFIX}{phase.upper()}": "sonnet"}
            )
            is None
        )

    @pytest.mark.no_auto_config
    def test_unknown_key_raises_config_error_naming_the_key(self):
        with pytest.raises(ConfigError) as exc:
            _load({"not_a_phase": "sonnet"})
        assert "not_a_phase" in str(exc.value)

    @pytest.mark.no_auto_config
    def test_unknown_key_does_not_emit_parity_warning(self, caplog):
        with (
            caplog.at_level(logging.WARNING, logger="bmad_assist_lite.core.config"),
            pytest.raises(ConfigError),
        ):
            _load({"not_a_phase": "sonnet"})
        assert not [r for r in caplog.records if "model parity" in r.getMessage().lower()]

    @pytest.mark.no_auto_config
    def test_non_routable_key_does_not_raise(self, caplog):
        with caplog.at_level(logging.WARNING, logger="bmad_assist_lite.core.config"):
            config = _load({"code_review": "sonnet"})
        assert config.providers.phase_models is not None

    @pytest.mark.no_auto_config
    def test_review_phases_keep_master_when_other_phases_are_routed(self):
        config = _load({"create_story": "haiku", "retrospective": "haiku"})
        for phase in ("dev_story", "code_review_synthesis", "fix_quality_gate"):
            assert resolve_phase_model(config, phase, env={}) == "opus"
            assert _StubHandler(config, phase).get_model() == "opus"


# ============================================================================
# REQ-01.2 — four tiers, one resolution point
# ============================================================================


class TestResolutionOrder:
    """env override > per-invocation argument > per-phase config > master."""

    ENV_KEY = f"{PHASE_MODEL_ENV_PREFIX}CREATE_STORY"

    @pytest.mark.no_auto_config
    @pytest.mark.parametrize(
        ("tier", "env", "override", "phase_models", "expected"),
        [
            (
                "env",
                {ENV_KEY: "env-model"},
                "arg-model",
                {"create_story": "cfg-model"},
                "env-model",
            ),
            ("argument", {}, "arg-model", {"create_story": "cfg-model"}, "arg-model"),
            ("config", {}, None, {"create_story": "cfg-model"}, "cfg-model"),
            ("master", {}, None, None, "opus"),
        ],
    )
    def test_tier_wins_over_the_next(self, tier, env, override, phase_models, expected):
        config = _load(phase_models)
        assert resolve_phase_model(config, "create_story", override=override, env=env) == expected

    @pytest.mark.no_auto_config
    def test_env_tier_reads_os_environ_by_default(self, monkeypatch):
        monkeypatch.setenv(self.ENV_KEY, "env-model")
        config = _load({"create_story": "cfg-model"})
        assert resolve_phase_model(config, "create_story") == "env-model"

    @pytest.mark.no_auto_config
    def test_env_tier_works_without_a_phase_models_section(self):
        config = _load()
        assert (
            resolve_phase_model(config, "create_story", env={self.ENV_KEY: "env-model"})
            == "env-model"
        )

    @pytest.mark.no_auto_config
    def test_env_tier_cannot_route_a_review_phase(self):
        config = _load()
        key = f"{PHASE_MODEL_ENV_PREFIX}CODE_REVIEW"
        assert resolve_phase_model(config, "code_review", env={key: "haiku"}) == "opus"

    @pytest.mark.no_auto_config
    def test_argument_tier_cannot_route_a_review_phase(self):
        config = _load()
        assert resolve_phase_model(config, "code_review", override="haiku", env={}) == "opus"

    @pytest.mark.no_auto_config
    def test_handler_threads_the_per_invocation_argument(self):
        config = _load()
        handler = _StubHandler(config, "create_story")
        assert handler.get_model(model="arg-model") == "arg-model"
        assert _StubHandler(config, "code_review").get_model(model="arg-model") == "opus"

    @pytest.mark.no_auto_config
    def test_multi_models_still_come_from_providers_multi(self):
        config = _load(
            {"create_story": "haiku"},
            multi=[
                {"provider": "gemini", "model": "gemini-2.5-flash"},
                {"provider": "claude", "model": "sonnet"},
            ],
        )
        assert [mc.model for mc in config.providers.multi] == ["gemini-2.5-flash", "sonnet"]
        assert resolve_phase_model(config, "code_review", env={}) == "opus"


# ============================================================================
# REQ-01.3 crit 6 — a plugin-supplied phase is not routable
# ============================================================================


class TestPluginSuppliedPhase:
    """A plugin handler that is not a BaseHandler still cannot route."""

    @pytest.mark.no_auto_config
    def test_plugin_phase_resolves_to_master(self, tmp_path):
        plugins_dir = tmp_path / ".bmad-assist-lite" / "plugins"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "routing_probe.py").write_text(
            '"""Local plugin registering a non-BaseHandler phase."""\n'
            "\n"
            "\n"
            "class ProbeHandler:\n"
            "    phase_name = 'code_review'\n"
            "\n"
            "\n"
            "class ProbePlugin:\n"
            "    name = 'routing-probe'\n"
            "\n"
            "    def register(self, registry):\n"
            "        registry.register_phase_handler('code_review', ProbeHandler)\n"
            "        registry.register_phase_handler('probe_phase', ProbeHandler)\n",
            encoding="utf-8",
        )

        registry = load_all_plugins(PluginRegistry(), plugins_dir=plugins_dir)
        handler_class = registry.get_phase_handler("code_review")
        assert handler_class is not None
        assert not issubclass(handler_class, BaseHandler)

        config = _load({"create_story": "haiku"})
        assert resolve_phase_model(config, "code_review", env={}) == "opus"
        assert resolve_phase_model(config, "probe_phase", env={}) == "opus"
        assert config.providers.phase_models is not None
        assert config.providers.phase_models.get_model("probe_phase", env={}) is None


# ============================================================================
# REQ-01.4 — route by model, never by effort
# ============================================================================


class TestRouteByModelNotEffort:
    def test_phase_models_config_has_no_effort_field(self):
        assert not [f for f in PhaseModelsConfig.model_fields if "effort" in f]

    def test_phase_models_config_fields_are_exactly_the_routable_phases(self):
        assert set(PhaseModelsConfig.model_fields) == set(ROUTABLE_PHASES)

    @pytest.mark.no_auto_config
    def test_effort_is_identical_across_routed_and_unrouted_phases(self):
        config = _load({"create_story": "haiku"})
        routed = _StubHandler(config, "create_story")
        unrouted = _StubHandler(config, "dev_story")
        assert routed.get_model() == "haiku"
        assert unrouted.get_model() == "opus"
        assert config.providers.master.effort == "max"


# ============================================================================
# REQ-01.5 — failure-triggered escalation
# ============================================================================


class TestEscalation:
    @pytest.mark.no_auto_config
    def test_first_attempt_uses_the_routed_model(self):
        config = _load({"create_story": "haiku"})
        assert resolve_phase_model(config, "create_story", attempt=1, env={}) == "haiku"

    @pytest.mark.no_auto_config
    def test_retry_escalates_to_the_master_model(self):
        config = _load({"create_story": "haiku"})
        assert resolve_phase_model(config, "create_story", attempt=2, env={}) == "opus"
        assert _StubHandler(config, "create_story").get_model(attempt=2) == "opus"

    @pytest.mark.no_auto_config
    def test_escalation_is_logged_at_info_with_both_models(self, caplog):
        config = _load({"create_story": "haiku"})
        with caplog.at_level(logging.INFO, logger="bmad_assist_lite.core.config"):
            resolve_phase_model(config, "create_story", attempt=2, env={})
        escalations = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.INFO and "haiku" in r.getMessage()
        ]
        assert escalations
        assert "opus" in escalations[0]
        assert "create_story" in escalations[0]

    @pytest.mark.no_auto_config
    def test_no_escalation_log_for_a_non_routable_phase(self, caplog):
        config = _load({"create_story": "haiku"})
        with caplog.at_level(logging.INFO, logger="bmad_assist_lite.core.config"):
            assert resolve_phase_model(config, "code_review", attempt=5, env={}) == "opus"
        assert not [r for r in caplog.records if "escalat" in r.getMessage().lower()]
