"""Plugin registry for bmad-assist-lite.

Central registry that plugins use to register their components.
Supports providers, phase handlers, and workflow compilers.
Later registrations override earlier ones (plugins override built-ins).
"""

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Central registry for all plugin components.

    Manages registration of:
    - Providers (LLM CLI adapters)
    - Phase handlers (loop phase implementations)
    - Workflow compilers (workflow compilation modules)
    - Workflow templates (workflow template directories)
    """

    def __init__(self) -> None:
        self._providers: dict[str, type] = {}
        self._phase_handlers: dict[str, type] = {}
        self._workflow_compilers: dict[str, Any] = {}
        self._workflow_templates: dict[str, Path] = {}
        self._loaded_plugins: list[str] = []

    def register_provider(self, name: str, provider_class: type) -> None:
        """Register a provider class by name.

        Later registrations override earlier ones.

        Args:
            name: Provider name (e.g., "codex", "opencode").
            provider_class: BaseProvider subclass.
        """
        if name in self._providers:
            logger.info("Provider '%s' overridden by plugin", name)
        self._providers[name] = provider_class
        logger.debug("Registered provider: %s -> %s", name, provider_class.__name__)

    def register_phase_handler(self, phase_name: str, handler_class: type) -> None:
        """Register a phase handler class.

        Args:
            phase_name: Phase name (e.g., "atdd", "test_review").
            handler_class: BaseHandler subclass.
        """
        if phase_name in self._phase_handlers:
            logger.info("Phase handler '%s' overridden by plugin", phase_name)
        self._phase_handlers[phase_name] = handler_class
        logger.debug("Registered phase handler: %s -> %s", phase_name, handler_class.__name__)

    def register_workflow(
        self,
        name: str,
        compiler_module: Any,
        templates_dir: Path | None = None,
    ) -> None:
        """Register a workflow compiler and optional template directory.

        Args:
            name: Workflow name (e.g., "my-custom-workflow").
            compiler_module: Module with compile() function.
            templates_dir: Optional path to workflow template files.
        """
        if name in self._workflow_compilers:
            logger.info("Workflow compiler '%s' overridden by plugin", name)
        self._workflow_compilers[name] = compiler_module
        if templates_dir is not None:
            self._workflow_templates[name] = templates_dir
        logger.debug("Registered workflow: %s", name)

    def get_provider(self, name: str) -> type | None:
        """Get registered provider class by name."""
        return self._providers.get(name)

    def get_phase_handler(self, phase_name: str) -> type | None:
        """Get registered phase handler class."""
        return self._phase_handlers.get(phase_name)

    def get_workflow_compiler(self, name: str) -> Any | None:
        """Get registered workflow compiler module."""
        return self._workflow_compilers.get(name)

    def get_workflow_templates_dir(self, name: str) -> Path | None:
        """Get registered workflow template directory."""
        return self._workflow_templates.get(name)

    @property
    def providers(self) -> dict[str, type]:
        return dict(self._providers)

    @property
    def phase_handlers(self) -> dict[str, type]:
        return dict(self._phase_handlers)

    @property
    def workflow_compilers(self) -> dict[str, Any]:
        return dict(self._workflow_compilers)

    def mark_loaded(self, plugin_name: str) -> None:
        """Record that a plugin was loaded."""
        self._loaded_plugins.append(plugin_name)

    @property
    def loaded_plugins(self) -> list[str]:
        return list(self._loaded_plugins)
