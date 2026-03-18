"""Plugin discovery and loading for bmad-assist-lite.

Discovery mechanisms (in order):
1. Built-in defaults (always loaded first)
2. Python entry points (bmad_assist_lite.plugins group)
3. Local directory ({project}/.bmad-assist-lite/plugins/*.py)

Later registrations override earlier ones.
"""

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from bmad_assist_lite.plugins.protocols import PhasePlugin, ProviderPlugin, WorkflowPlugin
from bmad_assist_lite.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "bmad_assist_lite.plugins"


def _load_builtin_providers(registry: PluginRegistry) -> None:
    """Register built-in providers (Claude + Gemini)."""
    from bmad_assist_lite.providers.claude_sdk import ClaudeSDKProvider
    from bmad_assist_lite.providers.gemini import GeminiProvider

    registry.register_provider("claude", ClaudeSDKProvider)
    registry.register_provider("gemini", GeminiProvider)
    logger.debug("Loaded built-in providers: claude, gemini")


def _load_builtin_phase_handlers(registry: PluginRegistry) -> None:
    """Register built-in phase handlers (10 phases)."""
    from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler
    from bmad_assist_lite.loop.handlers.code_review_synthesis import (
        CodeReviewSynthesisHandler,
    )
    from bmad_assist_lite.loop.handlers.create_story import CreateStoryHandler
    from bmad_assist_lite.loop.handlers.dev_story import DevStoryHandler
    from bmad_assist_lite.loop.handlers.epic_quality_gate import EpicQualityGateHandler
    from bmad_assist_lite.loop.handlers.fix_quality_gate import FixQualityGateHandler
    from bmad_assist_lite.loop.handlers.quality_gate import QualityGateHandler
    from bmad_assist_lite.loop.handlers.retrospective import RetrospectiveHandler
    from bmad_assist_lite.loop.handlers.validate_story import ValidateStoryHandler
    from bmad_assist_lite.loop.handlers.validate_story_synthesis import (
        ValidateStorySynthesisHandler,
    )

    registry.register_phase_handler("create_story", CreateStoryHandler)
    registry.register_phase_handler("validate_story", ValidateStoryHandler)
    registry.register_phase_handler("validate_story_synthesis", ValidateStorySynthesisHandler)
    registry.register_phase_handler("dev_story", DevStoryHandler)
    registry.register_phase_handler("code_review", CodeReviewHandler)
    registry.register_phase_handler("code_review_synthesis", CodeReviewSynthesisHandler)
    registry.register_phase_handler("quality_gate", QualityGateHandler)
    registry.register_phase_handler("fix_quality_gate", FixQualityGateHandler)
    registry.register_phase_handler("epic_quality_gate", EpicQualityGateHandler)
    registry.register_phase_handler("retrospective", RetrospectiveHandler)
    logger.debug("Loaded built-in phase handlers (10 phases)")


def _load_entry_point_plugins(registry: PluginRegistry) -> None:
    """Load plugins from Python entry points."""
    try:
        if sys.version_info >= (3, 12):
            from importlib.metadata import entry_points

            eps = entry_points(group=ENTRY_POINT_GROUP)
        else:
            from importlib.metadata import entry_points

            all_eps = entry_points()
            eps: Any = all_eps.get(ENTRY_POINT_GROUP, [])

        for ep in eps:
            try:
                plugin_class = ep.load()
                plugin = plugin_class()

                if isinstance(plugin, (ProviderPlugin, PhasePlugin, WorkflowPlugin)):
                    plugin.register(registry)
                    registry.mark_loaded(plugin.name)
                    logger.info("Loaded entry point plugin: %s", plugin.name)
                else:
                    logger.warning(
                        "Entry point '%s' does not implement a known plugin protocol",
                        ep.name,
                    )
            except Exception as e:
                logger.warning("Failed to load entry point plugin '%s': %s", ep.name, e)

    except Exception as e:
        logger.debug("Entry point discovery failed: %s", e)


def _load_local_plugins(registry: PluginRegistry, plugins_dir: Path) -> None:
    """Load plugins from local project directory.

    Scans {project}/.bmad-assist-lite/plugins/*.py for plugin modules.
    Each module should define a class that implements one of the plugin protocols.

    SECURITY NOTE: Local plugins execute arbitrary Python code. Ensure the
    plugins directory is only writable by trusted users. An attacker with
    write access to this directory can execute arbitrary code.
    """
    if not plugins_dir.exists() or not plugins_dir.is_dir():
        logger.debug("No local plugins directory: %s", plugins_dir)
        return

    # Resolve to canonical path to prevent symlink attacks
    plugins_dir = plugins_dir.resolve()

    logger.warning(
        "Loading local plugins from %s — ensure this directory is trusted",
        plugins_dir,
    )

    for py_file in sorted(plugins_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module_name = f"bmad_assist_lite_local_plugin_{py_file.stem}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                logger.warning("Cannot load plugin: %s", py_file)
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Look for plugin classes in the module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and attr_name != "type" and isinstance(attr, type):
                    try:
                        instance = attr()
                        if isinstance(instance, (ProviderPlugin, PhasePlugin, WorkflowPlugin)):
                            instance.register(registry)
                            registry.mark_loaded(instance.name)
                            logger.info(
                                "Loaded local plugin: %s from %s", instance.name, py_file.name
                            )
                    except (TypeError, AttributeError):
                        continue

        except Exception as e:
            logger.warning("Failed to load local plugin '%s': %s", py_file.name, e)


def load_all_plugins(
    registry: PluginRegistry,
    plugins_dir: Path | None = None,
) -> PluginRegistry:
    """Load all plugins in priority order.

    1. Built-in defaults
    2. Entry point plugins
    3. Local directory plugins

    Later registrations override earlier ones.

    Args:
        registry: The PluginRegistry to populate.
        plugins_dir: Optional local plugins directory path.

    Returns:
        The populated registry.

    """
    # 1. Built-in defaults
    _load_builtin_providers(registry)
    _load_builtin_phase_handlers(registry)

    # 2. Entry point plugins
    _load_entry_point_plugins(registry)

    # 3. Local plugins
    if plugins_dir is not None:
        _load_local_plugins(registry, plugins_dir)

    logger.info(
        "Plugin loading complete: %d providers, %d phase handlers, %d plugins loaded",
        len(registry.providers),
        len(registry.phase_handlers),
        len(registry.loaded_plugins),
    )

    return registry
