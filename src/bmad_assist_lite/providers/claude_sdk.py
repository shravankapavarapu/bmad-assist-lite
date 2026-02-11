"""Claude Agent SDK-based provider implementation."""

import asyncio
import logging
import threading
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

from bmad_assist_lite.core.exceptions import (
    ProviderError,
    ProviderTimeoutError,
)
from bmad_assist_lite.providers.base import (
    BaseProvider,
    ProviderResult,
    validate_settings_file,
)

logger = logging.getLogger(__name__)

SUPPORTED_MODELS: frozenset[str] = frozenset({"opus", "sonnet", "haiku"})
DEFAULT_TIMEOUT: int = 300


class ClaudeSDKProvider(BaseProvider):
    """Claude Code SDK-based provider implementation."""

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def default_model(self) -> str | None:
        return "sonnet"

    def supports_model(self, model: str) -> bool:
        return model in SUPPORTED_MODELS or model.startswith("claude-")

    async def _invoke_async(
        self,
        prompt: str,
        model: str,
        settings: Path | None,
        cwd: Path | None,
        allowed_tools: list[str] | None = None,
    ) -> str:
        options = ClaudeAgentOptions(
            model=model,
            permission_mode="acceptEdits",
            settings=str(settings) if settings is not None else None,
            cwd=cwd,
            tools=allowed_tools if allowed_tools is not None else None,
        )

        response_parts: list[str] = []

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            response_parts.append(block.text)
        except (CLINotFoundError, ProcessError):
            raise

        if not response_parts:
            raise ProviderError("No response received from SDK")

        return "".join(response_parts)

    def invoke(
        self,
        prompt: str,
        *,
        model: str | None = None,
        timeout: int | None = None,
        settings_file: Path | None = None,
        cwd: Path | None = None,
        allowed_tools: list[str] | None = None,
        color_index: int | None = None,
    ) -> ProviderResult:
        _ = color_index

        if timeout is not None and timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")

        effective_model = model or self.default_model or "sonnet"
        effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

        if not self.supports_model(effective_model):
            raise ProviderError(
                f"Unsupported model '{effective_model}' for Claude provider. "
                f"Supported: {', '.join(sorted(SUPPORTED_MODELS))} or claude-* identifiers"
            )

        validated_settings = validate_settings_file(
            settings_file, self.provider_name, effective_model
        ) if settings_file else None

        logger.debug(
            "Invoking Claude SDK: model=%s, timeout=%ds, prompt_len=%d",
            effective_model, effective_timeout, len(prompt),
        )

        start_time = time.perf_counter()
        command: tuple[str, ...] = ("sdk", "query", effective_model)

        try:
            from bmad_assist_lite.core.async_utils import run_async_in_thread

            response_text = run_async_in_thread(
                asyncio.wait_for(
                    self._invoke_async(
                        prompt, effective_model, validated_settings, cwd, allowed_tools
                    ),
                    timeout=effective_timeout,
                )
            )
        except TimeoutError as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            raise ProviderTimeoutError(f"SDK timeout after {effective_timeout}s") from e
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
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Unexpected SDK error: {e}") from e

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        logger.info(
            "Claude SDK completed: duration=%dms, response_len=%d",
            duration_ms, len(response_text),
        )

        return ProviderResult(
            stdout=response_text,
            stderr="",
            exit_code=0,
            duration_ms=duration_ms,
            model=effective_model,
            command=command,
        )

    def parse_output(self, result: ProviderResult) -> str:
        return result.stdout.strip()
