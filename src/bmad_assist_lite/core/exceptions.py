"""Custom exception hierarchy for bmad-assist-lite."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bmad_assist_lite.providers.base import ExitStatus, ProviderResult

__all__ = [
    "BmadAssistError",
    "CancelledError",
    "ConfigError",
    "ConfigValidationError",
    "ParserError",
    "StateError",
    "ProviderError",
    "ProviderTimeoutError",
    "ProviderExitCodeError",
    "CompilerError",
    "TokenBudgetError",
    "ContextError",
    "VariableError",
    "AmbiguousFileError",
]


class BmadAssistError(Exception):
    """Base exception for all bmad-assist-lite errors."""
    pass


class CancelledError(BmadAssistError):
    """Raised when operation is cancelled via shutdown event."""
    pass


class ConfigError(BmadAssistError):
    """Configuration loading or validation error."""
    pass


class ConfigValidationError(ConfigError):
    """Validation error with structured Pydantic details."""

    def __init__(self, message: str, errors: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.errors = errors


class ParserError(BmadAssistError):
    """BMAD file parsing error."""
    pass


class StateError(BmadAssistError):
    """State persistence or recovery error."""
    pass


class ProviderError(BmadAssistError):
    """CLI provider execution error."""
    pass


class ProviderTimeoutError(ProviderError):
    """CLI provider timeout error with optional partial output."""

    def __init__(
        self,
        message: str,
        partial_result: "ProviderResult | None" = None,
    ) -> None:
        super().__init__(message)
        self.partial_result = partial_result


class ProviderExitCodeError(ProviderError):
    """CLI provider exit code error with semantic classification."""

    def __init__(
        self,
        message: str,
        exit_code: int,
        exit_status: "ExitStatus",
        stderr: str = "",
        stdout: str = "",
        command: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.exit_status = exit_status
        self.stderr = stderr
        self.stdout = stdout
        self.command = command


class CompilerError(BmadAssistError):
    """BMAD workflow compilation error."""
    pass


class TokenBudgetError(CompilerError):
    """Token budget exceeded during compilation."""
    pass


class ContextError(CompilerError):
    """Context building error during compilation."""
    pass


class VariableError(BmadAssistError):
    """Variable resolution error in workflow compilation."""

    def __init__(
        self,
        message: str,
        variable_name: str = "",
        sources_checked: list[str] | None = None,
        suggestion: str = "",
    ) -> None:
        super().__init__(message)
        self.variable_name = variable_name
        self.sources_checked = sources_checked or []
        self.suggestion = suggestion


class AmbiguousFileError(BmadAssistError):
    """Ambiguous file match in workflow compilation."""

    def __init__(
        self,
        message: str,
        pattern_name: str = "",
        candidates: list[Path] | None = None,
        suggestion: str = "",
    ) -> None:
        super().__init__(message)
        self.pattern_name = pattern_name
        self.candidates = candidates or []
        self.suggestion = suggestion
