"""Plugin system for bmad-assist-lite.

Three plugin protocols enable extensibility:
- ProviderPlugin: Register new LLM providers
- PhasePlugin: Add new phases to the loop
- WorkflowPlugin: Add new workflow templates and compilers

Discovery mechanisms (in order):
1. Built-in defaults (Claude + Gemini, 10 phase handlers)
2. Python entry points (bmad_assist_lite.plugins group)
3. Local directory ({project}/.bmad-assist-lite/plugins/*.py)
"""

from bmad_assist_lite.plugins.protocols import PhasePlugin, ProviderPlugin, WorkflowPlugin
from bmad_assist_lite.plugins.registry import PluginRegistry

__all__ = [
    "PhasePlugin",
    "ProviderPlugin",
    "WorkflowPlugin",
    "PluginRegistry",
]
