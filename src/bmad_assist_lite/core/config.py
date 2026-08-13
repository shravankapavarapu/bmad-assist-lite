"""Configuration loading for bmad-assist-lite.

2-tier config hierarchy:
1. Global: ~/.bmad-assist-lite/config.yaml (base defaults)
2. Project: {project}/bmad-assist-lite.yaml (project overrides)
"""

import copy
import logging
import os
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from bmad_assist_lite.core.exceptions import ConfigError
from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.validation.findings import Severity

logger = logging.getLogger(__name__)

GLOBAL_CONFIG_PATH: Path = Path.home() / ".bmad-assist-lite" / "config.yaml"
PROJECT_CONFIG_NAME: str = "bmad-assist-lite.yaml"
MAX_CONFIG_SIZE: int = 1_048_576  # 1MB


# ============================================================================
# Config Models
# ============================================================================


class MasterProviderConfig(BaseModel):
    """Configuration for Master LLM provider."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(..., description="Provider name: claude, codex, cursor, gemini")
    model: str = Field(..., description="Model identifier: sonnet, opus, gemini-2.5-flash, etc.")
    model_name: str | None = Field(None, description="Display name override")
    settings: str | None = Field(None, description="Path to provider settings JSON")
    effort: str | None = Field(
        None,
        description="Claude effort: low|medium|high|xhigh|max (Opus 4.7 only; ignored elsewhere)",
    )

    @property
    def display_model(self) -> str:
        """Return the display name, falling back to model identifier."""
        return self.model_name or self.model

    @property
    def settings_path(self) -> Path | None:
        """Return the resolved settings file path, or None."""
        if self.settings is None:
            return None
        return Path(self.settings).expanduser()


class MultiProviderConfig(BaseModel):
    """Configuration for Multi LLM validator."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(..., description="Provider name: claude, codex, cursor, gemini")
    model: str = Field(..., description="Model identifier")
    model_name: str | None = Field(None, description="Display name override")
    settings: str | None = Field(None, description="Path to provider settings JSON")
    effort: str | None = Field(
        None,
        description="Claude effort: low|medium|high|xhigh|max (Opus 4.7 only; ignored elsewhere)",
    )

    @property
    def display_model(self) -> str:
        """Return the display name, falling back to model identifier."""
        return self.model_name or self.model

    @property
    def settings_path(self) -> Path | None:
        """Return the resolved settings file path, or None."""
        if self.settings is None:
            return None
        return Path(self.settings).expanduser()


class CliPathsConfig(BaseModel):
    """Override paths for CLI-based provider binaries."""

    model_config = ConfigDict(frozen=True)

    claude: str | None = Field(None, description="Absolute path to claude binary")
    codex: str | None = Field(None, description="Absolute path to codex binary")
    cursor: str | None = Field(None, description="Absolute path to cursor/agent binary")
    gemini: str | None = Field(None, description="Absolute path to gemini binary")


# ============================================================================
# Per-phase model routing
# ============================================================================

# The phases whose master model may be overridden. This set is CLOSED: widening
# it requires an architecture decision record, because the excluded phases are
# excluded on doctrine, not on convenience.
#
# Review, validation and implementation phases are excluded to preserve model
# parity — BMAD-METHOD v6.11 mandates that review subagents run at the same
# model capability as the session that produced the work. Routing a cheaper
# model into any of them silently degrades the judgement the loop depends on.
ROUTABLE_PHASES: frozenset[str] = frozenset(
    {
        "create_story",
        "validate_story_synthesis",
        "retrospective",
    }
)

# LLM phases that resolve a model but must never route it: model parity.
NON_ROUTABLE_LLM_PHASES: frozenset[str] = frozenset(
    {
        "validate_story",
        "dev_story",
        "code_review",
        "code_review_synthesis",
        "fix_quality_gate",
        # The review fixer answers a review at model parity. Routing a cheaper
        # model here would have it fix findings it cannot fully understand.
        "fix_review",
    }
)
# Note: the L3 epic-knowledge writer (``write_epic_knowledge``) is intentionally
# absent from every set here. It is invoked from a story-completion hook, not the
# phase loop, so it is not a ``Phase`` enum member and must not appear in the
# classification that partitions that enum. It runs at master capability by
# construction (the hook uses ``providers.master.model`` directly).

# Deterministic phases that invoke no LLM at all, so there is no model to route.
NON_LLM_PHASES: frozenset[str] = frozenset(
    {
        "quality_gate",
        "epic_quality_gate",
    }
)

# Every phase this module knows how to classify. A phase absent from this set is
# a typo in the config rather than a parity violation, and the two are reported
# differently.
CLASSIFIED_PHASES: frozenset[str] = ROUTABLE_PHASES | NON_ROUTABLE_LLM_PHASES | NON_LLM_PHASES

PHASE_MODEL_ENV_PREFIX: str = "BMAD_ASSIST_LITE_MODEL_"


class PhaseModelsConfig(BaseModel):
    """Per-phase overrides for the master model.

    One optional field per routable phase, mirroring :class:`TimeoutsConfig`.
    Omitted phases inherit ``providers.master.model``.
    """

    model_config = ConfigDict(frozen=True)

    create_story: str | None = None
    validate_story_synthesis: str | None = None
    retrospective: str | None = None

    def get_model(
        self,
        phase: str,
        *,
        override: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str | None:
        """Resolve the routed model for a phase, or ``None`` to inherit master.

        This is the sole enforcement point for the routable-phase constraint:
        it returns ``None`` for every phase outside ``ROUTABLE_PHASES``, whatever
        this config holds and whoever calls it. Callers that never touch
        ``BaseHandler`` — plugin-supplied phase handlers among them — therefore
        inherit the constraint instead of escaping it.

        Args:
            phase: Phase name, with either underscores or hyphens.
            override: Per-invocation model, outranked only by the environment.
            env: Environment mapping to read; defaults to ``os.environ``.

        """
        phase_key = phase.replace("-", "_")
        if phase_key not in ROUTABLE_PHASES:
            return None

        environ = os.environ if env is None else env
        env_model = environ.get(f"{PHASE_MODEL_ENV_PREFIX}{phase_key.upper()}")
        if env_model:
            return env_model

        if override is not None:
            return override

        value: str | None = getattr(self, phase_key, None)
        return value


_EMPTY_PHASE_MODELS = PhaseModelsConfig()


class ProviderConfig(BaseModel):
    """Provider configuration section."""

    model_config = ConfigDict(frozen=True)

    master: MasterProviderConfig
    multi: list[MultiProviderConfig] = Field(default_factory=list)
    cli_paths: CliPathsConfig = Field(default_factory=CliPathsConfig)
    phase_models: PhaseModelsConfig | None = Field(
        default=None, description="Per-phase master-model overrides (routable phases only)"
    )
    hermetic: bool = Field(
        default=False,
        description=(
            "Run without the target project's MCP servers. bmad-assist-lite uses no MCP "
            "itself, but it invokes provider CLIs in the target project's working "
            "directory, so the CLI starts whatever that project's .mcp.json declares — "
            "the documented source of un-reaped server pile-ups and host contention "
            "during long runs. On Claude this maps to the SDK's strict_mcp_config "
            "(CLI flag --strict-mcp-config); since mcp_servers is never populated, the "
            "result is no MCP servers at all. Other providers accept and ignore it. "
            "Defaults to False: a run that needs MCP tools during dev_story must keep "
            "today's behaviour unless the operator opts out."
        ),
    )


class TimeoutsConfig(BaseModel):
    """Per-phase timeout configuration."""

    model_config = ConfigDict(frozen=True)

    default: int = Field(default=300, description="Default timeout in seconds")
    create_story: int | None = None
    validate_story: int | None = None
    validate_story_synthesis: int | None = None
    dev_story: int | None = None
    code_review: int | None = None
    code_review_synthesis: int | None = None
    quality_gate: int | None = None
    fix_quality_gate: int | None = None
    fix_review: int | None = None
    epic_quality_gate: int | None = None
    retrospective: int | None = None
    write_epic_knowledge: int | None = None

    # Phases that need longer timeouts than default (300s)
    _PHASE_DEFAULTS: ClassVar[dict[str, int]] = {
        "create_story": 900,
        "validate_story": 900,
        "validate_story_synthesis": 900,
        "dev_story": 1800,
        "code_review": 1200,
        "code_review_synthesis": 1200,
        "quality_gate": 300,
        "fix_quality_gate": 900,
        "fix_review": 900,
        "epic_quality_gate": 600,
        "retrospective": 600,
        "write_epic_knowledge": 600,
    }

    def get_timeout(self, phase: str) -> int:
        """Get timeout for a specific phase.

        Priority: explicit per-phase config > phase-specific default > global default.
        """
        phase_key = phase.replace("-", "_")
        value: int | None = getattr(self, phase_key, None)
        if value is not None:
            return value
        return self._PHASE_DEFAULTS.get(phase_key, self.default)


class ProjectPathsConfig(BaseModel):
    """Configurable project paths."""

    model_config = ConfigDict(frozen=True)

    output_folder: str | None = None
    planning_artifacts: str | None = None
    implementation_artifacts: str | None = None
    project_knowledge: str | None = None


class ContextDocsConfig(BaseModel):
    """Configuration for Context7 library documentation fetching."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="Enable library doc fetching")
    max_libs: int = Field(default=8, description="Maximum libraries to fetch docs for")
    max_tokens_per_lib: int = Field(
        default=5000, description="Max tokens of docs per library from Context7"
    )


class QualityGateConfig(BaseModel):
    """Fallback commands for quality gate checks."""

    model_config = ConfigDict(frozen=True)

    lint: str | None = None
    typecheck: str | None = None
    build: str | None = None
    test: str | None = None
    test_unit: str | None = None
    command_timeout: int = Field(default=120, description="Timeout per command in seconds")
    max_retries: int = Field(default=2, description="Max LLM fix attempts before skipping story")


class AutoCommitConfig(BaseModel):
    """Configuration for auto-committing story changes after code review synthesis."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="Auto-commit after code_review_synthesis")


class CompilerConfig(BaseModel):
    """Prompt-compiler knobs.

    ``stable_prefix`` carries the epic's stable, unfiltered artifacts (project
    context, PRD, UX, architecture, epic) as an *appended* system prompt so their
    bytes form a warm, byte-identical prompt-cache prefix shared across every
    phase and story of the epic. It cannot ride a shared user-message prefix: the
    CLI keys the user-message cache on the whole prompt, so a stable prefix with a
    differing tail misses entirely (measured). Off by default.
    """

    model_config = ConfigDict(frozen=True)

    stable_prefix: bool = Field(
        default=False,
        description="Carry stable epic artifacts as a cached (appended) system prompt",
    )


class SolutionsConfig(BaseModel):
    """The compounding solutions store: off by default, bounded when on.

    Opt-in because it changes what reaches a prompt, and because an empty store
    would otherwise cost every phase a directory scan for nothing.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(
        default=False, description="Consult docs/solutions/ during dev and synthesis phases"
    )
    max_records: int = Field(
        default=200, ge=1, description="Cap on stored records; oldest evicted beyond it"
    )
    max_injected: int = Field(
        default=5, ge=0, description="Cap on records injected into a single phase"
    )
    max_injected_chars: int = Field(
        default=4000, ge=0, description="Hard character cap on the injected block"
    )


class SessionReuseConfig(BaseModel):
    """Reuse a reviewer/synthesis Claude session across review rounds (L2).

    Off by default and backward-compatible: when disabled, every review round
    cold-starts a fresh session exactly as today. When enabled, each reviewer
    lane in the multi-LLM fan-out (``code_review`` / ``validate_story``) keeps
    its own round-1 session id, and a round-2 re-review resumes *that same
    reviewer's* session (``resume=<id>``) so it re-reads only the fix diff
    instead of recompiling the full story context. ``code_review_synthesis``
    likewise resumes its own round-1 synthesis session on round 2.

    The reviewer independence rule (F-13) is preserved *structurally*: a lane
    only ever resumes a session it wrote itself, keyed by phase+index+provider+
    model. No lane can resume the dev/master session -- those ids are never
    written into the reviewer holder. Reuse is Claude-only; a non-Claude
    reviewer silently ignores the flag.
    """

    model_config = ConfigDict(frozen=True)

    reviewer_self_resume: bool = Field(
        default=False,
        description=(
            "Resume a reviewer/synthesis lane's own round-1 Claude session on "
            "round-2 re-review (never the dev session; Claude-only)"
        ),
    )


class EpicKnowledgeConfig(BaseModel):
    """A curated, bounded epic-knowledge brief carried across an epic's stories (L3).

    Off by default. When enabled, at each story's completion the master writes /
    updates a small ``epic-knowledge-<epic>.md`` brief (architectural decisions,
    gotchas, file-map deltas, conventions). Subsequent stories in the epic load
    it inside the stable, cached system-prompt region (see ``compiler.stable_prefix``),
    so later stories start "smart" without recompiling prior story transcripts.

    Bounded on purpose: an unbounded accumulation would defeat the whole point of
    context economy. ``max_chars`` hard-caps the written brief; a naive
    epic-scoped transcript resume is explicitly rejected in favour of this
    curated artifact.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(
        default=False,
        description="Write and inject a curated epic-knowledge brief across the epic's stories",
    )
    max_chars: int = Field(
        default=8000,
        ge=0,
        description="Hard character cap on the written brief (~2k tokens); excess is truncated",
    )


class SignoffConfig(BaseModel):
    """Whether a recorded architect sign-off is a precondition of ``done``.

    Defaults to off. The postcondition is real, but requiring an artifact that
    no existing project has produced yet would stop every upgraded run at its
    first story, and it keys the "was anything implemented?" half on this tool's
    own auto-commit subject line, which a project committing by hand will not
    match. REQ-08.6's reversibility clause names exactly this posture —
    advisory by default, enforcing by flag.
    """

    model_config = ConfigDict(frozen=True)

    required: bool = Field(
        default=False,
        description=(
            "Refuse to mark a story done unless a sign-off artifact exists whose "
            "tree SHA matches the current tree (guards incident I-01)"
        ),
    )


class ForensicsConfig(BaseModel):
    """Retention policy for story-scoped forensic artifacts.

    ``synthesis-diff-*`` patches and ``qa-failures-*`` reports are the evidence
    a data-gated decision about the validation phases needs, but they are
    written into the story-scoped cache that is wiped on every story
    transition. When enabled, the transition archives them under
    ``.bmad-assist-lite/cache/forensics/<story_id>/`` instead of deleting them,
    keeping at most ``max_stories`` stories' worth.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(
        default=True, description="Archive forensic artifacts across story transitions"
    )
    max_stories: int = Field(
        default=20,
        ge=1,
        description="Retention cap — number of stories kept, oldest evicted first",
    )


class LoopConfig(BaseModel):
    """Loop phase ordering and run-level budget configuration.

    Both budgets are optional and default to ``None`` = unlimited, so an
    existing config keeps today's unbounded behaviour on upgrade. They exist
    because an unbounded run is expensive: a run that stops on a budget exits
    with its own code and can be continued with ``--resume``.
    """

    model_config = ConfigDict(frozen=True)

    max_stories: int | None = Field(
        default=None,
        ge=1,
        description="Stop the run after this many stories (None = unlimited)",
    )
    max_runtime: float | None = Field(
        default=None,
        gt=0,
        description="Stop the run after this many wall-clock seconds (None = unlimited)",
    )
    max_cost_usd: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Stop the run once this many US dollars of provider spend have been "
            "recorded for it (None = unlimited). Measured from the persisted "
            "per-phase metrics, and counted per run so --resume starts fresh."
        ),
    )
    review_max_iterations: int = Field(
        default=1,
        ge=0,
        description=(
            "Cap on review -> fix -> re-review iterations per story. 0 disables "
            "the loop entirely (the kill switch). Deliberately NOT named "
            "code_review_max_iterations: that key belongs to the BMAD job's own "
            "cycle, and a grep for either name should land in exactly one world."
        ),
    )
    story: list[str] = Field(
        default_factory=lambda: [
            "create_story",
            "validate_story",
            "validate_story_synthesis",
            "dev_story",
            "code_review",
            "code_review_synthesis",
            "quality_gate",
        ]
    )
    epic_teardown: list[str] = Field(default_factory=lambda: ["epic_quality_gate", "retrospective"])


DEFAULT_LOOP_CONFIG = LoopConfig()


class ReviewConfig(BaseModel):
    """Which findings block, and whether a follow-up pass is worth paying for.

    The follow-up formula is harvested from upstream's unattended review step;
    its constants are not portable — they were tuned against a different
    finding distribution — so they are configuration, with the upstream values
    as the starting point rather than as a target.
    """

    model_config = ConfigDict(frozen=True)

    blocking_severity: Severity = Field(
        default=Severity.MEDIUM,
        description=(
            "Lowest severity that can drive a fix iteration. Findings below it "
            "are still recorded; they are culled from the loop, not the record."
        ),
    )
    followup_medium_weight: int = Field(
        default=3, ge=0, description="Weight of a medium finding in the follow-up score"
    )
    followup_low_weight: int = Field(
        default=1, ge=0, description="Weight of a low finding in the follow-up score"
    )
    followup_threshold: int = Field(
        default=5,
        ge=0,
        description="Score at or above which another review pass is recommended",
    )

    @field_validator("blocking_severity", mode="before")
    @classmethod
    def _coerce_blocking_severity(cls, value: object) -> object:
        if isinstance(value, str):
            return Severity.parse(value)
        return value


class Config(BaseModel):
    """Main bmad-assist-lite configuration model."""

    model_config = ConfigDict(frozen=True)

    providers: ProviderConfig
    timeout: int = Field(default=300, description="Global timeout in seconds")
    timeouts: TimeoutsConfig | None = None
    paths: ProjectPathsConfig = Field(default_factory=ProjectPathsConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    parallel_delay: float = Field(default=1.0, description="Delay between parallel LLM calls")
    context_docs: ContextDocsConfig | None = Field(
        default=None, description="Context7 library documentation fetching"
    )
    quality_gate: QualityGateConfig | None = Field(
        default=None, description="Fallback quality gate commands"
    )
    auto_commit: AutoCommitConfig = Field(default_factory=AutoCommitConfig)
    compiler: CompilerConfig = Field(default_factory=CompilerConfig)
    signoff: SignoffConfig = Field(default_factory=SignoffConfig)
    solutions: SolutionsConfig = Field(default_factory=SolutionsConfig)
    session_reuse: SessionReuseConfig = Field(default_factory=SessionReuseConfig)
    epic_knowledge: EpicKnowledgeConfig = Field(default_factory=EpicKnowledgeConfig)
    forensics: ForensicsConfig = Field(default_factory=ForensicsConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    parallel: ParallelConfig | None = Field(
        default=None, description="Parallel story execution configuration"
    )


# ============================================================================
# Config Loading
# ============================================================================

_config: Config | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base dictionary."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load and parse a YAML file with safety checks."""
    try:
        with path.open("r", encoding="utf-8") as f:
            content = f.read(MAX_CONFIG_SIZE + 1)

        if len(content) > MAX_CONFIG_SIZE:
            raise ConfigError(f"Config file {path} exceeds 1MB limit.")

        parsed = yaml.safe_load(content)

        if parsed is None:
            raise ConfigError(f"Config file {path} is empty.")

        if not isinstance(parsed, dict):
            raise ConfigError(
                f"Config file {path} must contain a YAML mapping, got {type(parsed).__name__}."
            )

        return parsed
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e
    except IsADirectoryError as e:
        raise ConfigError(f"{path} is a directory, not a config file.") from e
    except PermissionError as e:
        raise ConfigError(f"Permission denied reading {path}: {e}") from e
    except OSError as e:
        raise ConfigError(f"Cannot read config file {path}: {e}") from e


_SELF_REVIEW_REMEDY = (
    "Fix: add at least one reviewer under `providers.multi` in bmad-assist-lite.yaml "
    "that differs from `providers.master`, e.g.\n"
    "  providers:\n"
    "    multi:\n"
    "      - provider: claude\n"
    "        model: sonnet"
)


def self_review_warning(config: Config, phase: str | None = None) -> str | None:
    """Return a warning when review phases would be judged by the master itself.

    Two conditions qualify: an empty ``providers.multi`` (the master is the only
    reviewer available, so the model that wrote the code reviews it), and a
    ``providers.multi`` entry identical to the master on both provider and
    model. Returns ``None`` when an independent reviewer is configured.

    Args:
        config: The loaded configuration.
        phase: Optional phase name, included in the message when the warning is
            emitted at the point the affected phase runs.

    """
    master = config.providers.master
    where = f"Phase `{phase}`: " if phase else ""

    if not config.providers.multi:
        return (
            f"{where}`providers.multi` is empty, so review and validation fall back to the "
            f"master provider ({master.provider}/{master.model}) — the model that wrote the "
            f"work also judges it. This violates the no-self-verification principle and makes "
            f"the resulting review unreliable.\n{_SELF_REVIEW_REMEDY}"
        )

    duplicates = [
        mc
        for mc in config.providers.multi
        if mc.provider == master.provider and mc.model == master.model
    ]
    # A duplicate is only a self-verification problem when NO independent reviewer exists.
    # If at least one reviewer differs from the master, the independent check is present and
    # the duplicate is deliberate model parity (BMAD-METHOD v6.11 asks review subagents to run
    # at the session's capability) — warning there is alarm fatigue, and a warning that fires
    # on a good config trains operators to ignore the one that matters. This matches the
    # docstring's stated contract, which the previous implementation did not honour.
    independents = [
        mc
        for mc in config.providers.multi
        if not (mc.provider == master.provider and mc.model == master.model)
    ]
    if duplicates and not independents:
        return (
            f"{where}`providers.multi` contains a reviewer identical to the master "
            f"({master.provider}/{master.model}), so the model that wrote the work is also "
            f"one of its judges. This violates the no-self-verification principle.\n"
            f"{_SELF_REVIEW_REMEDY}"
        )

    return None


def _inspect_phase_model_keys(config_data: dict[str, Any]) -> None:
    """Classify raw ``providers.phase_models`` keys before the model is built.

    Pydantic drops unknown keys silently, and a key naming a real phase is
    indistinguishable from a typo once it has been dropped. Reading the raw
    mapping first keeps the two apart: a key that names no phase at all is a
    mistake and raises, while a key that names a non-routable phase is a parity
    violation that warns and is ignored — the resolution point is what enforces
    the constraint, so a load-time raise would only duplicate it, and it could
    never see a plugin-registered phase anyway.
    """
    providers = config_data.get("providers")
    if not isinstance(providers, dict):
        return
    phase_models = providers.get("phase_models")
    if not isinstance(phase_models, dict):
        return

    unknown = [key for key in phase_models if str(key).replace("-", "_") not in CLASSIFIED_PHASES]
    if unknown:
        raise ConfigError(
            f"Unknown phase(s) in providers.phase_models: {', '.join(sorted(map(str, unknown)))}. "
            f"Valid phases: {', '.join(sorted(CLASSIFIED_PHASES))}."
        )

    for key in phase_models:
        phase_key = str(key).replace("-", "_")
        if phase_key in ROUTABLE_PHASES:
            continue
        reason = (
            "it runs no LLM, so there is no model to route"
            if phase_key in NON_LLM_PHASES
            else "a cheaper model there would degrade the judgement the loop depends on"
        )
        logger.warning(
            "providers.phase_models.%s is ignored: `%s` is not routable — %s. It will use "
            "providers.master.model. The routable set is closed to preserve model parity "
            "(BMAD-METHOD v6.11); routable phases: %s.",
            phase_key,
            phase_key,
            reason,
            ", ".join(sorted(ROUTABLE_PHASES)),
        )


def load_config(config_data: dict[str, Any]) -> Config:
    """Load and validate configuration from a dictionary."""
    global _config
    if not isinstance(config_data, dict):
        raise ConfigError(f"config_data must be a dict, got {type(config_data).__name__}")
    try:
        _inspect_phase_model_keys(config_data)
        _config = Config.model_validate(config_data)
    except ValidationError as e:
        _config = None
        raise ConfigError(f"Configuration validation failed: {e}") from e
    except ConfigError:
        _config = None
        raise

    warning = self_review_warning(_config)
    if warning is not None:
        logger.warning("%s", warning)
    return _config


def get_config() -> Config:
    """Get the loaded configuration singleton."""
    if _config is None:
        raise ConfigError("Config not loaded. Call load_config() first.")
    return _config


def _reset_config() -> None:
    """Reset config singleton for testing."""
    global _config
    _config = None


# Tool's package source root — where bmad-assist-lite's own code lives.
# .env with API keys lives here, separate from any target project.
# core/config.py -> core/ -> bmad_assist_lite/ -> src/ -> repo root
_TOOL_SOURCE_ROOT: Path = Path(__file__).resolve().parent.parent.parent.parent


def load_config_with_project(
    project_path: str | Path | None = None,
    *,
    global_config_path: str | Path | None = None,
) -> Config:
    """Load configuration with 2-tier hierarchy.

    Merges: global (~/.bmad-assist-lite/config.yaml) <- project (bmad-assist-lite.yaml)
    """
    global _config

    resolved_project = Path.cwd() if project_path is None else Path(project_path).expanduser()
    resolved_global = (
        GLOBAL_CONFIG_PATH if global_config_path is None else Path(global_config_path).expanduser()
    )

    # Load .env from the tool's own source root (API keys belong to the tool, not the target)
    env_file = _TOOL_SOURCE_ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file)
            logger.debug("Loaded .env from %s", env_file)
        except ImportError:
            logger.debug(".env found but python-dotenv not installed")

    global_exists = resolved_global.exists() and resolved_global.is_file()
    project_config_path = resolved_project / PROJECT_CONFIG_NAME
    project_exists = project_config_path.exists() and project_config_path.is_file()

    if not global_exists and not project_exists:
        raise ConfigError("No configuration found. Run 'bmad-assist-lite init' to create config.")

    global_data: dict[str, Any] = {}
    project_data: dict[str, Any] | None = None

    if global_exists:
        try:
            global_data = _load_yaml_file(resolved_global)
        except ConfigError:
            _config = None
            raise

    if project_exists:
        try:
            project_data = _load_yaml_file(project_config_path)
        except ConfigError:
            _config = None
            raise

    merged_data = global_data
    if project_data is not None:
        merged_data = _deep_merge(merged_data, project_data)

    try:
        return load_config(merged_data)
    except ConfigError as e:
        sources = []
        if global_exists:
            sources.append(f"global ({resolved_global})")
        if project_exists:
            sources.append(f"project ({project_config_path})")
        merged_str = " + ".join(sources)
        raise ConfigError(f"Invalid configuration (from {merged_str}): {e}") from e


def get_phase_timeout(config: Config, phase: str) -> int:
    """Get timeout for a specific workflow phase."""
    if config.timeouts is not None:
        return config.timeouts.get_timeout(phase)
    return config.timeout


def resolve_phase_model(
    config: Config,
    phase: str,
    *,
    override: str | None = None,
    attempt: int = 1,
    env: dict[str, str] | None = None,
) -> str:
    """Resolve the master model to use for a phase.

    Four tiers, highest first: environment override, per-invocation argument,
    per-phase config, inherited master model. The first three are gated on the
    routable-phase membership test inside
    :meth:`PhaseModelsConfig.get_model`, which is the only place that test
    decides a model.

    A retry escalates back to the master model, so a route that turns out to be
    too cheap costs one attempt rather than a stuck phase.

    Args:
        config: The loaded configuration.
        phase: Phase name, with either underscores or hyphens.
        override: Per-invocation model override.
        attempt: 1-based attempt number; anything above 1 escalates to master.
        env: Environment mapping to read; defaults to ``os.environ``.

    """
    master = config.providers.master.model
    phase_models = config.providers.phase_models or _EMPTY_PHASE_MODELS

    routed = phase_models.get_model(phase, override=override, env=env)
    if routed is None or routed == master:
        return master

    if attempt > 1:
        logger.info(
            "Phase %s: escalating from routed model %s to master model %s on attempt %d",
            phase,
            routed,
            master,
            attempt,
        )
        return master

    return routed


def get_loop_config(config: Config | None = None) -> LoopConfig:
    """Get loop configuration from config or default."""
    if config is not None:
        return config.loop
    try:
        return get_config().loop
    except ConfigError:
        return DEFAULT_LOOP_CONFIG
