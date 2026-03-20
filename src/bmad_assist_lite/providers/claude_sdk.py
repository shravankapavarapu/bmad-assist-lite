"""Claude Agent SDK-based provider implementation.

Implements the BaseProvider Template Method contract (Story 7.3):
- _do_invoke() feeds a ResultCollector during async streaming
- _cleanup() terminates orphan claude processes on timeout
- invoke() is inherited from BaseProvider (not overridden)
"""

import asyncio
import logging
import time
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    ProcessError,
    TextBlock,
    query,
)

from bmad_assist_lite.core.exceptions import ProviderError
from bmad_assist_lite.providers._windows import is_pid_alive, terminate_process
from bmad_assist_lite.providers.base import (
    BaseProvider,
    ProviderResult,
    validate_settings_file,
)
from bmad_assist_lite.providers.result_collector import ResultCollector

logger = logging.getLogger(__name__)

SUPPORTED_MODELS: frozenset[str] = frozenset({"opus", "sonnet", "haiku"})


class ClaudeSDKProvider(BaseProvider):
    """Claude Code SDK-based provider implementation.

    Uses the BaseProvider Template Method: invoke() is inherited and drives the
    full lifecycle (create collector -> _do_invoke() -> handle timeout -> _cleanup()).
    This class only implements the hooks: _do_invoke(), _cleanup(), parse_output(),
    and supports_model().
    """

    def __init__(self) -> None:
        """Initialize provider with PID tracking for orphan cleanup."""
        super().__init__()
        self._current_pid: int | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider identifier string."""
        return "claude"

    @property
    def default_model(self) -> str | None:
        """Return the default model identifier."""
        return "sonnet"

    def supports_model(self, model: str) -> bool:
        """Return True if the model is a known Claude model."""
        return model in SUPPORTED_MODELS or model.startswith("claude-")

    async def _invoke_async_with_collector(
        self,
        prompt: str,
        model: str,
        settings: Path | None,
        cwd: Path | None,
        collector: ResultCollector,
        allowed_tools: list[str] | None = None,
    ) -> str:
        """Run the Claude SDK query and feed chunks into the collector.

        Iterates over AssistantMessage objects from the SDK streaming response,
        extracting TextBlock.text values and feeding them to the collector.

        Args:
            prompt: The prompt text to send.
            model: Model identifier (e.g. "sonnet", "opus").
            settings: Optional path to Claude settings file.
            cwd: Working directory for the SDK process.
            collector: ResultCollector to accumulate text chunks into.
            allowed_tools: List of tool names the provider may use.

        Returns:
            The full accumulated text from collector.text.

        Raises:
            CLINotFoundError: If the claude CLI is not found.
            ProcessError: If the SDK subprocess fails.
            ProviderError: If no response text is received.

        """
        options = ClaudeAgentOptions(
            model=model,
            permission_mode="acceptEdits",
            settings=str(settings) if settings is not None else None,
            cwd=cwd,
            tools=allowed_tools if allowed_tools is not None else None,
        )

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        collector.add(block.text)

        if collector.is_empty:
            raise ProviderError("No response received from SDK")

        return collector.text

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
        """Execute Claude SDK with streaming and collector integration.

        Resolves model, validates settings, runs the async query via
        run_async_in_thread(asyncio.wait_for(...)), and returns a ProviderResult.

        TimeoutError from asyncio.wait_for is intentionally NOT caught here —
        it propagates to the base class invoke() which handles grace period logic.

        Args:
            prompt: The prompt text to send to the provider.
            collector: ResultCollector to accumulate streaming chunks into.
            model: Model identifier, or None for provider default.
            timeout: Timeout in seconds (always an int, resolved by invoke()).
            settings_file: Optional path to provider settings file.
            cwd: Working directory for the provider process.
            allowed_tools: List of tool names the provider may use.
            color_index: Index for ANSI color differentiation (unused).

        Returns:
            ProviderResult with timed_out=False on successful completion.

        Raises:
            TimeoutError: When asyncio.wait_for fires (handled by base class).
            ProviderError: On SDK errors (CLINotFoundError, ProcessError, etc.).

        """
        _ = color_index

        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")

        effective_model = model or self.default_model or "sonnet"

        if not self.supports_model(effective_model):
            raise ProviderError(
                f"Unsupported model '{effective_model}' for Claude provider. "
                f"Supported: {', '.join(sorted(SUPPORTED_MODELS))} or claude-* identifiers"
            )

        validated_settings = (
            validate_settings_file(settings_file, self.provider_name, effective_model)
            if settings_file
            else None
        )

        logger.debug(
            "Invoking Claude SDK: model=%s, timeout=%ds, prompt_len=%d",
            effective_model,
            timeout,
            len(prompt),
        )

        start_time = time.monotonic()

        try:
            from bmad_assist_lite.core.async_utils import run_async_in_thread

            response_text = run_async_in_thread(
                asyncio.wait_for(
                    self._invoke_async_with_collector(
                        prompt,
                        effective_model,
                        validated_settings,
                        cwd,
                        collector,
                        allowed_tools,
                    ),
                    timeout=timeout,
                )
            )
        except CLINotFoundError as e:
            raise ProviderError(
                "Claude Code not found. Is 'claude' installed and in PATH?"
            ) from e
        except ProcessError as e:
            exit_code = e.exit_code if e.exit_code is not None else 1
            stderr = e.stderr or ""
            raise ProviderError(
                f"Claude SDK failed with exit code {exit_code}: {stderr[:200]}"
            ) from e
        except (TimeoutError, ProviderError):
            raise
        except Exception as e:
            raise ProviderError(f"Unexpected SDK error: {e}") from e

        duration_ms = int((time.monotonic() - start_time) * 1000)

        logger.info(
            "Claude SDK completed: duration=%dms, response_len=%d",
            duration_ms,
            len(response_text),
        )

        return ProviderResult(
            stdout=response_text,
            stderr="",
            exit_code=0,
            duration_ms=duration_ms,
            model=effective_model,
            command=(self.provider_name, model or "default"),
        )

    def _cleanup(self) -> None:
        """Terminate orphan claude process if PID is tracked and alive.

        Best-effort cleanup: if PID cannot be determined or termination fails,
        logs a warning but does not raise. The base class wraps this in try/except
        so exceptions from _cleanup() never mask the original error.
        """
        pid = self._current_pid
        self._current_pid = None

        if pid is None:
            logger.warning(
                "No PID tracked for cleanup — claude process may be orphaned. "
                "The SDK does not expose subprocess PIDs directly."
            )
            return

        if not is_pid_alive(pid):
            logger.debug("Tracked PID %d already terminated, no cleanup needed", pid)
            return

        logger.warning("Terminating orphan claude process PID %d", pid)
        success = terminate_process(pid)
        if not success:
            logger.warning(
                "Failed to terminate claude process PID %d — may be orphaned", pid
            )

    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from provider result."""
        return result.stdout.strip()
