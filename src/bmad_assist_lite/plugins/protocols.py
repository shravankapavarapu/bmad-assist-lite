"""Plugin protocols for bmad-assist-lite extensibility.

Three protocols define the plugin contracts:
- ProviderPlugin: Register new LLM providers
- PhasePlugin: Add new phases to the loop
- WorkflowPlugin: Add new workflow templates
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProviderPlugin(Protocol):
    """Protocol for provider plugins.

    Implementations register new LLM providers (e.g., add Codex, OpenCode).

    Example:
        class CodexPlugin:
            name = "codex-provider"

            def register(self, registry):
                from my_codex import CodexProvider
                registry.register_provider("codex", CodexProvider)

    """

    name: str

    def register(self, registry: Any) -> None:
        """Register providers with the plugin registry."""
        ...


@runtime_checkable
class PhasePlugin(Protocol):
    """Protocol for phase plugins.

    Implementations add new phases to the BMAD loop (e.g., TestArch, Deep Verify).

    Example:
        class TestArchPlugin:
            name = "testarch"

            def register(self, registry):
                from my_testarch import ATDDHandler
                registry.register_phase_handler("atdd", ATDDHandler)

    """

    name: str

    def register(self, registry: Any) -> None:
        """Register phase handlers with the plugin registry."""
        ...


@runtime_checkable
class WorkflowPlugin(Protocol):
    """Protocol for workflow plugins.

    Implementations add new workflow templates and their compiler modules.

    Example:
        class CustomWorkflowPlugin:
            name = "custom-workflows"

            def register(self, registry):
                registry.register_workflow("my-workflow", my_compiler_module, templates_dir)

    """

    name: str

    def register(self, registry: Any) -> None:
        """Register workflows with the plugin registry."""
        ...
