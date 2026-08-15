"""Tests for SP-D0 forensic dev-stream capture in ClaudeSDKProvider.

Covers ``forensics.capture_stream`` at the provider layer:
- Capture ON writes one JSONL line per content block (text / thinking / tool_use
  / tool_result) with the right ``kind`` / ``chars`` / ``name``, in stream order.
- ``tool_result`` records store only a char count + error flag, never the
  (model-input) content.
- Capture OFF (path=None) and the default (no arg) write no file AND leave
  ``result.stdout`` byte-identical to the ON case — capture is side-effect-free.
- Flush-per-line: a stream that raises mid-way still leaves the blocks written
  before the raise on disk (a timed-out/cancelled dev call keeps a partial
  artifact).
- A path that cannot be opened disables capture silently — the call still
  succeeds.
- ``forensics.capture_stream`` defaults False.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from bmad_assist_lite.core.exceptions import ProviderError
from bmad_assist_lite.providers.claude_sdk import ClaudeSDKProvider

# ============================================================================
# Helpers
# ============================================================================

_TOOL_INPUT = {"file_path": "/app/x.py", "content": "print(1)\n"}


def make_result_message() -> ResultMessage:
    """A minimal terminal envelope so the stream is well-formed."""
    return ResultMessage(
        subtype="success",
        duration_ms=9000,
        duration_api_ms=8500,
        is_error=False,
        num_turns=1,
        session_id="sess-stream",
        total_cost_usd=0.01,
        usage=None,
    )


async def make_fake_query(messages: list[Any]) -> Any:
    """Async generator yielding the given messages."""
    for msg in messages:
        yield msg


async def make_raising_query(messages: list[Any], exc: Exception) -> Any:
    """Async generator yielding messages, then raising — mirrors a died stream."""
    for msg in messages:
        yield msg
    raise exc


def full_stream() -> list[Any]:
    """A realistic dev turn: assistant text+thinking+tool_use, then a tool_result."""
    assistant = AssistantMessage(
        content=[
            TextBlock(text="Let me implement this."),
            ThinkingBlock(thinking="I should edit the file", signature="sig"),
            ToolUseBlock(id="t1", name="Write", input=dict(_TOOL_INPUT)),
        ],
        model="sonnet",
    )
    user = UserMessage(
        content=[ToolResultBlock(tool_use_id="t1", content="File written", is_error=False)]
    )
    return [assistant, user, make_result_message()]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL artifact into a list of records."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ============================================================================
# TestCaptureOn — one line per block, correct schema
# ============================================================================


class TestCaptureOn:
    """With a path, every content block is retained as one JSONL line."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_one_line_per_block_in_stream_order(
        self, mock_query: MagicMock, tmp_path: Path
    ) -> None:
        """text, thinking, tool_use, tool_result — one line each, in order."""
        path = tmp_path / "dev-stream-3.1.jsonl"
        mock_query.return_value = make_fake_query(full_stream())

        result = ClaudeSDKProvider().invoke("prompt", timeout=300, stream_capture_path=path)

        assert path.exists()
        lines = read_jsonl(path)
        assert [r["kind"] for r in lines] == ["text", "thinking", "tool_use", "tool_result"]
        assert [r["role"] for r in lines] == ["assistant", "assistant", "assistant", "user"]
        # seq is contiguous from 0; the whole turn shares turn index 1.
        assert [r["seq"] for r in lines] == [0, 1, 2, 3]
        assert [r["turn"] for r in lines] == [1, 1, 1, 1]
        # The collector still only ever saw the TextBlock.
        assert result.stdout == "Let me implement this."

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_block_fields_and_char_counts(self, mock_query: MagicMock, tmp_path: Path) -> None:
        """Each kind carries its declared fields and an accurate char count."""
        path = tmp_path / "dev-stream-x.jsonl"
        mock_query.return_value = make_fake_query(full_stream())

        ClaudeSDKProvider().invoke("prompt", timeout=300, stream_capture_path=path)

        text, thinking, tool_use, tool_result = read_jsonl(path)

        assert text["text"] == "Let me implement this."
        assert text["chars"] == len("Let me implement this.")

        assert thinking["text"] == "I should edit the file"
        assert thinking["chars"] == len("I should edit the file")

        assert tool_use["name"] == "Write"
        assert tool_use["input"] == _TOOL_INPUT
        assert tool_use["chars"] == len(json.dumps(_TOOL_INPUT))

        # tool_result stores char count + is_error ONLY — never the content.
        assert tool_result["chars"] == len("File written")
        assert tool_result["is_error"] is False
        assert "content" not in tool_result
        assert "text" not in tool_result

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_turn_increments_per_assistant_message(
        self, mock_query: MagicMock, tmp_path: Path
    ) -> None:
        """Two assistant messages land on turns 1 and 2."""
        path = tmp_path / "dev-stream-multi.jsonl"
        stream = [
            AssistantMessage(content=[TextBlock(text="first")], model="sonnet"),
            AssistantMessage(content=[TextBlock(text="second")], model="sonnet"),
            make_result_message(),
        ]
        mock_query.return_value = make_fake_query(stream)

        ClaudeSDKProvider().invoke("prompt", timeout=300, stream_capture_path=path)

        lines = read_jsonl(path)
        assert [r["turn"] for r in lines] == [1, 2]
        assert [r["seq"] for r in lines] == [0, 1]


# ============================================================================
# TestCaptureOff — default OFF changes nothing
# ============================================================================


class TestCaptureOff:
    """Without a path, no file is written and stdout is byte-identical."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_off_and_default_are_side_effect_free(
        self, mock_query: MagicMock, tmp_path: Path
    ) -> None:
        """path=None and the default arg write nothing and match the ON stdout."""
        on_path = tmp_path / "on.jsonl"
        mock_query.return_value = make_fake_query(full_stream())
        on = ClaudeSDKProvider().invoke("prompt", timeout=300, stream_capture_path=on_path)

        mock_query.return_value = make_fake_query(full_stream())
        off = ClaudeSDKProvider().invoke("prompt", timeout=300, stream_capture_path=None)

        mock_query.return_value = make_fake_query(full_stream())
        default = ClaudeSDKProvider().invoke("prompt", timeout=300)

        # Byte-identical stdout across all three.
        assert off.stdout == on.stdout
        assert default.stdout == on.stdout
        # The only JSONL file that exists is the ON-case artifact.
        assert list(tmp_path.glob("*.jsonl")) == [on_path]


# ============================================================================
# TestPartialCapture — flush-per-line survives a raising/cancelled stream
# ============================================================================


class TestPartialCapture:
    """Blocks flushed before a mid-stream raise stay on disk."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_blocks_before_the_raise_are_retained(
        self, mock_query: MagicMock, tmp_path: Path
    ) -> None:
        """A stream that dies after two blocks still leaves those two captured."""
        path = tmp_path / "dev-stream-partial.jsonl"
        assistant = AssistantMessage(
            content=[
                TextBlock(text="chunk one"),
                ThinkingBlock(thinking="mid-thought", signature="s"),
            ],
            model="sonnet",
        )
        mock_query.return_value = make_raising_query([assistant], RuntimeError("stream died"))

        with pytest.raises(ProviderError):
            ClaudeSDKProvider().invoke("prompt", timeout=300, stream_capture_path=path)

        assert path.exists()
        lines = read_jsonl(path)
        assert [r["kind"] for r in lines] == ["text", "thinking"]
        assert lines[0]["text"] == "chunk one"


# ============================================================================
# TestCaptureNeverBreaksTheCall — capture I/O failures are swallowed
# ============================================================================


class TestCaptureNeverBreaksTheCall:
    """An unusable capture path disables capture but never fails the call."""

    @patch("bmad_assist_lite.providers.claude_sdk.query")
    def test_unopenable_path_is_a_silent_noop(
        self, mock_query: MagicMock, tmp_path: Path
    ) -> None:
        """A directory cannot be opened for writing → capture no-ops, call OK."""
        bad = tmp_path / "a-directory"
        bad.mkdir()
        mock_query.return_value = make_fake_query(full_stream())

        result = ClaudeSDKProvider().invoke("prompt", timeout=300, stream_capture_path=bad)

        assert result.stdout == "Let me implement this."
        assert result.exit_code == 0


# ============================================================================
# TestConfigDefault — capture is off by default
# ============================================================================


class TestConfigDefault:
    """The feature ships disabled."""

    def test_capture_stream_defaults_false(self) -> None:
        """forensics.capture_stream is False unless explicitly enabled."""
        from bmad_assist_lite.core.config import ForensicsConfig

        assert ForensicsConfig().capture_stream is False


# ============================================================================
# TestDevHandlerGating — only dev_story sets a path, and only when enabled
# ============================================================================


class TestDevHandlerGating:
    """The dev handler resolves a per-story path iff capture is enabled."""

    def _handler(self, tmp_path: Path, *, capture_stream: bool) -> Any:
        from bmad_assist_lite.core.config import load_config
        from bmad_assist_lite.loop.handlers.dev_story import DevStoryHandler

        config = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "forensics": {"capture_stream": capture_stream},
            }
        )
        return DevStoryHandler(config, tmp_path)

    def test_disabled_yields_none(self, tmp_path: Path) -> None:
        """Capture off → no path, so the dev call runs exactly as before."""
        from bmad_assist_lite.core.state import State

        handler = self._handler(tmp_path, capture_stream=False)
        state = State(current_epic=3, current_story="3.1")

        assert handler._stream_capture_path(state) is None

    def test_enabled_yields_story_scoped_path(self, tmp_path: Path) -> None:
        """Capture on → dev-stream-<story>.jsonl under the project cache dir."""
        from bmad_assist_lite.core.state import State

        handler = self._handler(tmp_path, capture_stream=True)
        state = State(current_epic=3, current_story="3.1")

        path = handler._stream_capture_path(state)
        assert path == tmp_path / ".bmad-assist-lite" / "cache" / "dev-stream-3.1.jsonl"
        # The cache directory is created eagerly, matching quality_gate.py.
        assert path is not None and path.parent.is_dir()

    def test_base_handler_never_captures(self, tmp_path: Path) -> None:
        """Phase-scoping: a non-dev handler returns None even with capture on.

        Only DevStoryHandler overrides the hook, so no other phase captures.
        """
        from bmad_assist_lite.core.config import load_config
        from bmad_assist_lite.core.state import State
        from bmad_assist_lite.loop.handlers.code_review import CodeReviewHandler

        config = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "forensics": {"capture_stream": True},
            }
        )
        handler = CodeReviewHandler(config, tmp_path)
        state = State(current_epic=3, current_story="3.1")

        assert handler._stream_capture_path(state) is None
