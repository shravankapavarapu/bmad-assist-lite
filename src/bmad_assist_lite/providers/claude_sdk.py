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
    resolve_cli_path,
    validate_settings_file,
)
from bmad_assist_lite.providers.result_collector import ResultCollector

logger = logging.getLogger(__name__)

SUPPORTED_MODELS: frozenset[str] = frozenset({"opus", "sonnet", "haiku"})

# claude-agent-sdk >= 0.2.x escalation quirk: when the CLI emits a result with
# is_error=true but subtype="success" and an empty errors array, then exits
# non-zero, the SDK builds the message
# f"Claude Code returned an error result: {subtype}" and re-raises it as a bare
# Exception (claude_agent_sdk/_internal/query.py:340-344, 851-852). The turn
# actually succeeded; the non-zero process exit is ancillary. Genuine failures
# carry a different subtype (e.g. error_max_turns) or joined error messages, so
# matching the prefix AND requiring the subtype to be exactly "success" keeps
# this narrow and lets real errors propagate.
_SDK_ERROR_RESULT_PREFIX = "Claude Code returned an error result:"
_SDK_BENIGN_SUCCESS_SUBTYPE = "success"


def _is_benign_success_error(exc: Exception) -> bool:
    """Return True if exc is the SDK's benign "error result: success" escalation.

    Only matches when the trailing subtype is exactly "success"; any other
    subtype (real error) or joined error message does not match, so genuine
    failures are never swallowed.
    """
    message = str(exc).strip()
    if not message.startswith(_SDK_ERROR_RESULT_PREFIX):
        return False
    subtype = message[len(_SDK_ERROR_RESULT_PREFIX) :].strip()
    return subtype == _SDK_BENIGN_SUCCESS_SUBTYPE


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

    def _resolve_cli_path(self) -> str | None:
        """Resolve the system Claude Code CLI path, best-effort.

        Pointing the SDK at the system ``claude`` CLI (via
        ``ClaudeAgentOptions.cli_path``) avoids the older ``claude.exe`` the SDK
        ships in its ``_bundled`` directory, which has exhibited long-turn
        output truncation. Resolution is best-effort: if no system CLI is found,
        returns ``None`` so the SDK falls back to its bundled binary rather than
        failing the invocation.
        """
        try:
            cli_path = resolve_cli_path(self.provider_name)
            logger.debug("Using system Claude CLI for SDK: %s", cli_path)
            return cli_path
        except ProviderError:
            logger.debug("No system 'claude' CLI resolved; SDK will use its bundled binary.")
            return None

    async def _invoke_async_with_collector(
        self,
        prompt: str,
        model: str,
        settings: Path | None,
        cwd: Path | None,
        collector: ResultCollector,
        allowed_tools: list[str] | None = None,
        effort: str | None = None,
    ) -> str:
        extra_args: dict[str, str | None] = {}
        if effort:
            extra_args["effort"] = effort

        options = ClaudeAgentOptions(
            model=model,
            permission_mode="acceptEdits",
            settings=str(settings) if settings is not None else None,
            cwd=cwd,
            tools=allowed_tools if allowed_tools is not None else None,
            extra_args=extra_args,
            cli_path=self._resolve_cli_path(),
        )

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            collector.add(block.text)
        except Exception as e:
            # Swallow only the benign "error result: success" escalation, and
            # only when the turn produced real output. Everything else (real
            # SDK errors, CLINotFoundError, empty-output benign quirk) re-raises
            # to the typed handlers in _do_invoke().
            if _is_benign_success_error(e) and collector.text.strip():
                logger.warning(
                    "Claude SDK reported a non-zero exit with subtype 'success' "
                    "after a completed turn (known CLI/SDK 0.2.x quirk); treating "
                    "%d chars of collected output as success.",
                    len(collector.text),
                )
            else:
                raise

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
        effort: str | None = None,
        color_index: int | None = None,
    ) -> ProviderResult:
        """Execute Claude SDK with the given prompt and return the result."""
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
                        effort,
                    ),
                    timeout=timeout,
                )
            )
        except CLINotFoundError as e:
            raise ProviderError("Claude Code not found. Is 'claude' installed and in PATH?") from e
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
            logger.debug(
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
            logger.warning("Failed to terminate claude process PID %d — may be orphaned", pid)

    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from provider result."""
        return result.stdout.strip()
