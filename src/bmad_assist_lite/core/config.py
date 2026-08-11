"""Configuration loading for bmad-assist-lite.

2-tier config hierarchy:
1. Global: ~/.bmad-assist-lite/config.yaml (base defaults)
2. Project: {project}/bmad-assist-lite.yaml (project overrides)
"""

import copy
import logging
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bmad_assist_lite.core.exceptions import ConfigError
from bmad_assist_lite.parallel.config import ParallelConfig

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


class ProviderConfig(BaseModel):
    """Provider configuration section."""

    model_config = ConfigDict(frozen=True)

    master: MasterProviderConfig
    multi: list[MultiProviderConfig] = Field(default_factory=list)
    cli_paths: CliPathsConfig = Field(default_factory=CliPathsConfig)


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
    epic_quality_gate: int | None = None
    retrospective: int | None = None

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
        "epic_quality_gate": 600,
        "retrospective": 600,
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
    """Loop phase ordering configuration."""

    model_config = ConfigDict(frozen=True)

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
    forensics: ForensicsConfig = Field(default_factory=ForensicsConfig)
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
    if duplicates:
        return (
            f"{where}`providers.multi` contains a reviewer identical to the master "
            f"({master.provider}/{master.model}), so the model that wrote the work is also "
            f"one of its judges. This violates the no-self-verification principle.\n"
            f"{_SELF_REVIEW_REMEDY}"
        )

    return None


def load_config(config_data: dict[str, Any]) -> Config:
    """Load and validate configuration from a dictionary."""
    global _config
    if not isinstance(config_data, dict):
        raise ConfigError(f"config_data must be a dict, got {type(config_data).__name__}")
    try:
        _config = Config.model_validate(config_data)
    except ValidationError as e:
        _config = None
        raise ConfigError(f"Configuration validation failed: {e}") from e

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


def get_loop_config(config: Config | None = None) -> LoopConfig:
    """Get loop configuration from config or default."""
    if config is not None:
        return config.loop
    try:
        return get_config().loop
    except ConfigError:
        return DEFAULT_LOOP_CONFIG
