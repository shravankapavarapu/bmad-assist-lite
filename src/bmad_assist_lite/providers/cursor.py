"""Cursor CLI subprocess-based provider stub.

Minimal implementation to satisfy registry imports and ``get_provider("cursor")``.
All abstract methods raise ``NotImplementedError`` — full implementation comes
in Story 11.3.
"""

from pathlib import Path

from bmad_assist_lite.providers.base import BaseProvider, ProviderResult
from bmad_assist_lite.providers.result_collector import ResultCollector


class CursorProvider(BaseProvider):
    """Stub provider for Cursor CLI integration.

    Registered in the provider registry so that ``provider: cursor`` is
    accepted in configuration and ``get_provider("cursor")`` returns an
    instance.  All invoke/parse methods raise ``NotImplementedError`` until
    Story 11.3 adds the real implementation.
    """

    @property
    def provider_name(self) -> str:
        """Return the provider identifier string."""
        return "cursor"

    def _do_invoke(
        self,
        prompt: str,
        *,
        collector: ResultCollector,
        model: str | None = None,
        timeout: int = 300,
        settings_file: Path | None = None,
        cwd: Path | None = None,
        allowed_tools: list[str] | None = None,
        color_index: int | None = None,
    ) -> ProviderResult:
        """Provider-specific invocation — not yet implemented."""
        raise NotImplementedError(
            "CursorProvider._do_invoke() is not yet implemented (Story 11.3)"
        )

    def _cleanup(self) -> None:
        """Provider-specific cleanup — not yet implemented."""
        raise NotImplementedError("CursorProvider._cleanup() is not yet implemented (Story 11.3)")

    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from CLI output — not yet implemented."""
        raise NotImplementedError(
            "CursorProvider.parse_output() is not yet implemented (Story 11.3)"
        )

    def supports_model(self, model: str) -> bool:
        """Return True if this provider supports the given model — not yet implemented."""
        raise NotImplementedError(
            "CursorProvider.supports_model() is not yet implemented (Story 11.3)"
        )
