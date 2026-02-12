"""CLI provider integration module.

Provider Registry:
    - ClaudeSDKProvider: Claude integration using claude-agent-sdk
    - GeminiProvider: Gemini CLI subprocess provider

Registry Functions:
    - get_provider(): Get provider instance by name
    - list_providers(): List all registered provider names
    - register_provider(): Register custom provider
"""

from typing import TYPE_CHECKING, Any

from .base import (
    BaseProvider,
    ExitStatus,
    ProviderResult,
    resolve_settings_file,
    validate_settings_file,
    write_progress,
    format_tag,
    PROVIDER_COLORS,
    RESET_COLOR,
)

if TYPE_CHECKING:
    from .claude_sdk import ClaudeSDKProvider as ClaudeSDKProvider
    from .gemini import GeminiProvider as GeminiProvider

__all__ = [
    "BaseProvider",
    "ClaudeSDKProvider",
    "ExitStatus",
    "GeminiProvider",
    "ProviderResult",
    "get_provider",
    "list_providers",
    "register_provider",
    "resolve_settings_file",
    "validate_settings_file",
    "write_progress",
    "format_tag",
    "PROVIDER_COLORS",
    "RESET_COLOR",
]

# Lazy loading for heavy provider imports
_lazy_imports = {
    "ClaudeSDKProvider": ".claude_sdk",
    "GeminiProvider": ".gemini",
}


def __getattr__(name: str) -> type[Any]:
    if name in _lazy_imports:
        import importlib

        module = importlib.import_module(_lazy_imports[name], __package__)
        cls: type[Any] = getattr(module, name)
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Registry
from bmad_assist_lite.core.exceptions import ConfigError

_REGISTRY: dict[str, type[BaseProvider]] = {}


def _init_default_providers() -> None:
    from .claude_sdk import ClaudeSDKProvider
    from .gemini import GeminiProvider

    _REGISTRY.update(
        {
            "claude": ClaudeSDKProvider,
            "gemini": GeminiProvider,
        }
    )


def get_provider(name: str) -> BaseProvider:
    if not _REGISTRY:
        _init_default_providers()
    if not name or not name.strip():
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ConfigError(f"Provider name cannot be empty. Available: {available}")
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ConfigError(f"Unknown provider: '{name}'. Available: {available}")
    return _REGISTRY[name]()


def list_providers() -> frozenset[str]:
    if not _REGISTRY:
        _init_default_providers()
    return frozenset(_REGISTRY.keys())


def register_provider(name: str, provider_class: type[BaseProvider]) -> None:
    """Register a custom provider. Later registrations override earlier ones."""
    if not _REGISTRY:
        _init_default_providers()
    if not name or not name.strip():
        raise ConfigError("Provider name cannot be empty")
    if not isinstance(provider_class, type) or not issubclass(provider_class, BaseProvider):
        raise TypeError(
            f"provider_class must be a subclass of BaseProvider, got {type(provider_class)}"
        )
    _REGISTRY[name] = provider_class


def normalize_model_name(name: str) -> str:
    return name.replace("_", "-")


def denormalize_model_name(name: str) -> str:
    return name.replace("-", "_")
