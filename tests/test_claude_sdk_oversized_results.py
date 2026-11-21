"""Tests for the three guards against an oversized stream message.

An image read with the Read tool makes one stream message that can pass the
SDK's buffer and end the phase. The provider raises the buffer, refuses a Read
of a large image before it runs, and logs any tool result large enough to
matter.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    UserMessage,
)

from bmad_assist_lite.providers import claude_sdk
from bmad_assist_lite.providers.claude_sdk import (
    ClaudeSDKProvider,
    _image_read_hooks,
    _refuse_oversized_image_read,
    _warn_on_large_tool_results,
)


def _read_call(path: Path | str, cwd: Path | None = None) -> dict[str, Any]:
    call: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(path)},
        "tool_use_id": "toolu_1",
    }
    if cwd is not None:
        call["cwd"] = str(cwd)
    return call


def _decide(call: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_refuse_oversized_image_read(call, "toolu_1", None))


def _write(path: Path, size: int) -> Path:
    path.write_bytes(b"\0" * size)
    return path


class TestImageReadGuard:
    def test_refuses_an_image_over_the_cap(self, tmp_path: Path) -> None:
        big = _write(
            tmp_path / "campaign-hub-desktop-linux.png", claude_sdk._IMAGE_READ_CAP_BYTES + 1
        )
        out = _decide(_read_call(big))["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert "campaign-hub-desktop-linux.png" in out["permissionDecisionReason"]
        assert "downscaled" in out["permissionDecisionReason"]

    def test_allows_an_image_at_the_cap(self, tmp_path: Path) -> None:
        small = _write(tmp_path / "icon.PNG", claude_sdk._IMAGE_READ_CAP_BYTES)
        assert _decide(_read_call(small)) == {}

    def test_allows_a_large_file_that_is_not_an_image(self, tmp_path: Path) -> None:
        big = _write(tmp_path / "run.log", claude_sdk._IMAGE_READ_CAP_BYTES * 4)
        assert _decide(_read_call(big)) == {}

    def test_resolves_a_relative_path_against_the_session_directory(self, tmp_path: Path) -> None:
        _write(tmp_path / "shot.jpeg", claude_sdk._IMAGE_READ_CAP_BYTES + 1)
        out = _decide(_read_call("shot.jpeg", cwd=tmp_path))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.parametrize(
        "call",
        [
            _read_call("/no/such/file.png"),
            {"tool_name": "Read", "tool_input": {}},
            {"tool_name": "Read", "tool_input": None},
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            {},
        ],
    )
    def test_has_no_opinion_when_it_cannot_measure(self, call: dict[str, Any]) -> None:
        assert _decide(call) == {}

    def test_hook_table_guards_the_read_tool(self) -> None:
        table = _image_read_hooks()
        assert table is not None
        (matcher,) = table["PreToolUse"]
        assert matcher.matcher == "Read"
        assert matcher.hooks == [_refuse_oversized_image_read]


class TestLargeToolResultWarning:
    def _message(self, chars: int) -> UserMessage:
        return UserMessage(content=[ToolResultBlock(tool_use_id="toolu_big", content="x" * chars)])

    def test_warns_over_the_threshold(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=claude_sdk.__name__):
            _warn_on_large_tool_results(self._message(claude_sdk._TOOL_RESULT_WARN_CHARS + 1))
        assert any("toolu_big" in r.getMessage() for r in caplog.records)

    def test_is_quiet_at_the_threshold(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=claude_sdk.__name__):
            _warn_on_large_tool_results(self._message(claude_sdk._TOOL_RESULT_WARN_CHARS))
        assert caplog.records == []

    def test_sizes_block_content_given_as_a_list(self, caplog: pytest.LogCaptureFixture) -> None:
        image = {
            "type": "image",
            "source": {"type": "base64", "data": "A" * (claude_sdk._TOOL_RESULT_WARN_CHARS + 1)},
        }
        message = UserMessage(content=[ToolResultBlock(tool_use_id="toolu_img", content=[image])])
        with caplog.at_level(logging.WARNING, logger=claude_sdk.__name__):
            _warn_on_large_tool_results(message)
        assert any("toolu_img" in r.getMessage() for r in caplog.records)

    def test_ignores_a_message_with_plain_text_content(self) -> None:
        _warn_on_large_tool_results(UserMessage(content="hello"))
        _warn_on_large_tool_results(object())


class TestProviderWiring:
    def test_invoke_passes_the_buffer_and_the_guard_and_warns_without_capture(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        seen: dict[str, Any] = {}
        messages = [
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="toolu_wired",
                        content="x" * (claude_sdk._TOOL_RESULT_WARN_CHARS + 1),
                    )
                ]
            ),
            AssistantMessage(content=[TextBlock(text="done " * 40)], model="sonnet"),
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="sess",
                total_cost_usd=0.0,
                usage=None,
            ),
        ]

        async def fake_query(prompt: str, options: Any) -> Any:
            seen["options"] = options
            for message in messages:
                yield message

        with (
            patch.object(claude_sdk, "query", fake_query),
            caplog.at_level(logging.WARNING, logger=claude_sdk.__name__),
        ):
            result = ClaudeSDKProvider().invoke("hi", model="sonnet")

        assert result.exit_code == 0
        assert seen["options"].max_buffer_size == 32 * 1024 * 1024
        (matcher,) = seen["options"].hooks["PreToolUse"]
        assert matcher.hooks == [_refuse_oversized_image_read]
        assert any("toolu_wired" in r.getMessage() for r in caplog.records)
