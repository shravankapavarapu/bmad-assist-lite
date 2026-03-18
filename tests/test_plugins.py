"""Tests for the plugin system.

Covers: PluginRegistry operations, protocol conformance checks.
"""


from bmad_assist_lite.plugins.protocols import PhasePlugin, ProviderPlugin, WorkflowPlugin
from bmad_assist_lite.plugins.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Mock plugin classes that satisfy the protocols
# ---------------------------------------------------------------------------


class _MockProviderPlugin:
    """Minimal class satisfying ProviderPlugin protocol."""

    name = "mock-provider"

    def register(self, registry):
        pass


class _MockPhasePlugin:
    """Minimal class satisfying PhasePlugin protocol."""

    name = "mock-phase"

    def register(self, registry):
        pass


class _MockWorkflowPlugin:
    """Minimal class satisfying WorkflowPlugin protocol."""

    name = "mock-workflow"

    def register(self, registry):
        pass


# Dummy provider class to register in the registry
class _DummyProvider:
    pass


class _DummyProviderV2:
    pass


# ---------------------------------------------------------------------------
# PluginRegistry — providers
# ---------------------------------------------------------------------------


class TestPluginRegistryProviders:
    """Tests for provider registration and retrieval."""

    def test_register_and_get(self):
        """Register a provider class and retrieve it by name."""
        registry = PluginRegistry()
        registry.register_provider("test", _DummyProvider)

        result = registry.get_provider("test")
        assert result is _DummyProvider

    def test_override(self):
        """Later registration overrides an earlier one."""
        registry = PluginRegistry()
        registry.register_provider("test", _DummyProvider)
        registry.register_provider("test", _DummyProviderV2)

        result = registry.get_provider("test")
        assert result is _DummyProviderV2

    def test_missing_returns_none(self):
        """Getting an unregistered provider returns None."""
        registry = PluginRegistry()
        result = registry.get_provider("nonexistent")
        assert result is None

    def test_providers_property(self):
        """The providers property returns a copy of all registered providers."""
        registry = PluginRegistry()
        registry.register_provider("a", _DummyProvider)
        registry.register_provider("b", _DummyProviderV2)

        providers = registry.providers
        assert "a" in providers
        assert "b" in providers
        assert len(providers) == 2


# ---------------------------------------------------------------------------
# PluginRegistry — phase handlers
# ---------------------------------------------------------------------------


class _DummyHandler:
    pass


class TestPluginRegistryPhaseHandlers:
    """Tests for phase handler registration."""

    def test_register_and_get_phase(self):
        """Register a phase handler and retrieve it."""
        registry = PluginRegistry()
        registry.register_phase_handler("atdd", _DummyHandler)

        result = registry.get_phase_handler("atdd")
        assert result is _DummyHandler

    def test_missing_phase_returns_none(self):
        """Getting an unregistered phase handler returns None."""
        registry = PluginRegistry()
        assert registry.get_phase_handler("missing") is None


# ---------------------------------------------------------------------------
# PluginRegistry — workflows
# ---------------------------------------------------------------------------


class TestPluginRegistryWorkflows:
    """Tests for workflow registration."""

    def test_register_and_get_workflow(self):
        """Register a workflow compiler module and retrieve it."""
        registry = PluginRegistry()
        mock_module = object()
        registry.register_workflow("custom", mock_module)

        result = registry.get_workflow_compiler("custom")
        assert result is mock_module

    def test_missing_workflow_returns_none(self):
        """Getting an unregistered workflow compiler returns None."""
        registry = PluginRegistry()
        assert registry.get_workflow_compiler("missing") is None


# ---------------------------------------------------------------------------
# PluginRegistry — loaded plugins tracking
# ---------------------------------------------------------------------------


class TestPluginRegistryLoaded:
    """Tests for the loaded-plugins tracking list."""

    def test_mark_loaded(self):
        """mark_loaded records plugin names in order."""
        registry = PluginRegistry()
        registry.mark_loaded("plugin-a")
        registry.mark_loaded("plugin-b")

        assert registry.loaded_plugins == ["plugin-a", "plugin-b"]


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Verify that mock classes satisfy the runtime-checkable protocols."""

    def test_provider_plugin_protocol(self):
        """A class with 'name' and 'register' satisfies ProviderPlugin."""
        plugin = _MockProviderPlugin()
        assert isinstance(plugin, ProviderPlugin)

    def test_phase_plugin_protocol(self):
        """A class with 'name' and 'register' satisfies PhasePlugin."""
        plugin = _MockPhasePlugin()
        assert isinstance(plugin, PhasePlugin)

    def test_workflow_plugin_protocol(self):
        """A class with 'name' and 'register' satisfies WorkflowPlugin."""
        plugin = _MockWorkflowPlugin()
        assert isinstance(plugin, WorkflowPlugin)

    def test_non_conforming_class(self):
        """A plain object does NOT satisfy ProviderPlugin."""

        class NotAPlugin:
            pass

        assert not isinstance(NotAPlugin(), ProviderPlugin)

    def test_provider_plugin_has_expected_attributes(self):
        """ProviderPlugin protocol declares 'name' and 'register'."""
        # Check that the protocol's members are present on conforming instances
        plugin = _MockProviderPlugin()
        assert hasattr(plugin, "name")
        assert callable(plugin.register)
