"""Tests for the ``effort`` keyword propagation through the provider Template Method.

``BaseProvider.invoke()`` accepts ``effort`` and must forward it to
``_do_invoke()``. Every provider's ``_do_invoke()`` must accept the keyword,
whether or not it acts on it, so that forwarding never raises ``TypeError``.
"""

from pathlib import Path
from typing import Any

import pytest

from bmad_assist_lite.core.exceptions import ProviderError
from bmad_assist_lite.providers.base import BaseProvider, ProviderResult
from bmad_assist_lite.providers.claude_sdk import ClaudeSDKProvider
from bmad_assist_lite.providers.codex import CodexProvider
from bmad_assist_lite.providers.cursor import CursorProvider
from bmad_assist_lite.providers.gemini import GeminiProvider
from bmad_assist_lite.providers.result_collector import ResultCollector

# ============================================================================
# Helpers
# ============================================================================


class _RecordingProvider(BaseProvider):
    """Minimal concrete provider that records the kwargs ``_do_invoke`` receives."""

    def __init__(self) -> None:
        self.received: dict[str, Any] = {}
        self.cleanup_called = False

    @property
    def provider_name(self) -> str:
        return "recording"

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
        self.received = {
            "prompt": prompt,
            "model": model,
            "timeout": timeout,
            "settings_file": settings_file,
            "cwd": cwd,
            "allowed_tools": allowed_tools,
            "effort": effort,
            "color_index": color_index,
        }
        collector.add("ok")
        return ProviderResult(
            stdout="ok",
            stderr="",
            exit_code=0,
            duration_ms=1,
            model=model,
            command=(self.provider_name,),
        )

    def _cleanup(self) -> None:
        self.cleanup_called = True

    def parse_output(self, result: ProviderResult) -> str:
        return result.stdout

    def supports_model(self, model: str) -> bool:
        return True


ALL_PROVIDER_CLASSES = [
    ClaudeSDKProvider,
    CodexProvider,
    CursorProvider,
    GeminiProvider,
]


# ============================================================================
# The load-bearing regression test
# ============================================================================


class TestEffortReachesDoInvoke:
    """``effort`` passed to ``invoke()`` must arrive at ``_do_invoke()``."""

    def test_effort_reaches_do_invoke(self):
        """The load-bearing regression: effort must not be silently dropped."""
        provider = _RecordingProvider()

        provider.invoke("prompt", model="opus", effort="max")

        assert provider.received["effort"] == "max"

    def test_effort_none_reaches_do_invoke_as_none(self):
        """Omitting effort forwards None, not a missing key."""
        provider = _RecordingProvider()

        provider.invoke("prompt", model="opus")

        assert "effort" in provider.received
        assert provider.received["effort"] is None

    @pytest.mark.parametrize("value", ["low", "medium", "high", "xhigh", "max"])
    def test_every_documented_effort_value_is_forwarded_verbatim(self, value):
        """Each documented effort level arrives unmodified."""
        provider = _RecordingProvider()

        provider.invoke("prompt", effort=value)

        assert provider.received["effort"] == value

    def test_other_kwargs_still_forwarded_alongside_effort(self):
        """Adding effort must not disturb the other forwarded keywords."""
        provider = _RecordingProvider()

        provider.invoke(
            "prompt",
            model="sonnet",
            timeout=42,
            allowed_tools=["Read"],
            effort="high",
            color_index=3,
        )

        assert provider.received["model"] == "sonnet"
        assert provider.received["timeout"] == 42
        assert provider.received["allowed_tools"] == ["Read"]
        assert provider.received["color_index"] == 3
        assert provider.received["effort"] == "high"


# ============================================================================
# Signature contract: no provider may TypeError on effort
# ============================================================================


class TestDoInvokeSignatureContract:
    """Every ``_do_invoke()`` must accept ``effort`` as a keyword-only arg."""

    def test_abstract_do_invoke_declares_effort(self):
        """The Template Method contract itself declares the parameter."""
        import inspect

        params = inspect.signature(BaseProvider._do_invoke).parameters

        assert "effort" in params
        assert params["effort"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["effort"].default is None

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDER_CLASSES, ids=lambda c: c.__name__)
    def test_provider_do_invoke_accepts_effort(self, provider_cls):
        """Each concrete provider accepts effort without a TypeError."""
        import inspect

        params = inspect.signature(provider_cls._do_invoke).parameters

        assert "effort" in params, f"{provider_cls.__name__}._do_invoke drops effort"
        assert params["effort"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["effort"].default is None

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDER_CLASSES, ids=lambda c: c.__name__)
    def test_binding_effort_does_not_raise_type_error(self, provider_cls):
        """Binding the forwarded call signature must not raise TypeError.

        This is the Codex/Cursor regression guard: before the fix, forwarding
        ``effort`` raised ``TypeError: unexpected keyword argument 'effort'``
        on every single call to those two providers.
        """
        import inspect

        sig = inspect.signature(provider_cls._do_invoke)

        # Raises TypeError if the provider does not accept `effort`.
        sig.bind(
            None,
            "prompt",
            collector=ResultCollector(),
            model=None,
            timeout=300,
            settings_file=None,
            cwd=None,
            allowed_tools=None,
            effort="max",
            color_index=None,
        )

    @pytest.mark.parametrize("provider_cls", ALL_PROVIDER_CLASSES, ids=lambda c: c.__name__)
    def test_binding_effort_none_does_not_raise_type_error(self, provider_cls):
        """effort=None must bind for every provider too."""
        import inspect

        sig = inspect.signature(provider_cls._do_invoke)

        sig.bind(
            None,
            "prompt",
            collector=ResultCollector(),
            model=None,
            timeout=300,
            settings_file=None,
            cwd=None,
            allowed_tools=None,
            effort=None,
            color_index=None,
        )


# ============================================================================
# Per-provider behaviour
# ============================================================================


def _stub_claude_sdk(monkeypatch) -> dict[str, Any]:
    """Stub ClaudeAgentOptions and query, returning the captured option kwargs.

    The stubbed ``query`` yields nothing, so the call ends deterministically in
    ``ProviderError("No response received from SDK")`` after the options — and
    therefore ``extra_args`` — have been built.
    """
    captured: dict[str, Any] = {}

    class _FakeOptions:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    async def _empty_query(*args: Any, **kwargs: Any):
        return
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr("bmad_assist_lite.providers.claude_sdk.ClaudeAgentOptions", _FakeOptions)
    monkeypatch.setattr("bmad_assist_lite.providers.claude_sdk.query", _empty_query)
    monkeypatch.setattr(ClaudeSDKProvider, "_resolve_cli_path", lambda self: None)
    return captured


class TestClaudeAppliesEffort:
    """Claude is the only provider that acts on ``effort``."""

    async def test_effort_lands_in_extra_args(self, monkeypatch):
        """A non-empty effort becomes ``extra_args['effort']``."""
        captured = _stub_claude_sdk(monkeypatch)
        provider = ClaudeSDKProvider()

        with pytest.raises(ProviderError):
            await provider._invoke_async_with_collector(
                "prompt", "opus", None, None, ResultCollector(), None, "max"
            )

        assert captured["extra_args"] == {"effort": "max"}

    async def test_effort_none_leaves_extra_args_empty(self, monkeypatch):
        """No effort means no ``--effort`` flag is forwarded to the CLI."""
        captured = _stub_claude_sdk(monkeypatch)
        provider = ClaudeSDKProvider()

        with pytest.raises(ProviderError):
            await provider._invoke_async_with_collector(
                "prompt", "opus", None, None, ResultCollector(), None, None
            )

        assert captured["extra_args"] == {}

    @pytest.mark.parametrize("value", ["low", "medium", "high", "xhigh", "max"])
    async def test_each_effort_level_reaches_extra_args(self, monkeypatch, value):
        """Every documented level survives the full invoke -> extra_args path."""
        captured = _stub_claude_sdk(monkeypatch)
        provider = ClaudeSDKProvider()

        with pytest.raises(ProviderError):
            await provider._invoke_async_with_collector(
                "prompt", "opus", None, None, ResultCollector(), None, value
            )

        assert captured["extra_args"] == {"effort": value}


NON_CLAUDE_PROVIDERS = [
    (CodexProvider, "bmad_assist_lite.providers.codex", "codex"),
    (CursorProvider, "bmad_assist_lite.providers.cursor", "cursor"),
    (GeminiProvider, "bmad_assist_lite.providers.gemini", "gemini"),
]


class TestNonClaudeProvidersIgnoreEffort:
    """Gemini, Codex and Cursor accept ``effort`` and ignore it without error."""

    @pytest.mark.parametrize(
        ("provider_cls", "module", "logger_name"),
        NON_CLAUDE_PROVIDERS,
        ids=lambda p: getattr(p, "__name__", str(p)),
    )
    def test_do_invoke_runs_past_effort_without_type_error(
        self, provider_cls, module, logger_name, monkeypatch
    ):
        """Execution-driven guard: effort reaches the body, never a TypeError.

        ``resolve_cli_path`` is stubbed to raise so the call aborts just after
        the effort-handling block. A ``ProviderError`` proves execution got that
        far; a ``TypeError`` would prove the keyword was rejected at call time.
        """

        def _no_cli(name: str) -> str:
            raise ProviderError(f"stubbed: no {name} CLI")

        monkeypatch.setattr(f"{module}.resolve_cli_path", _no_cli)

        provider = provider_cls()

        with pytest.raises(ProviderError):
            provider._do_invoke(
                "prompt",
                collector=ResultCollector(),
                model=None,
                timeout=300,
                allowed_tools=None,
                effort="max",
            )

    @pytest.mark.parametrize(
        ("provider_cls", "module", "logger_name"),
        NON_CLAUDE_PROVIDERS,
        ids=lambda p: getattr(p, "__name__", str(p)),
    )
    def test_do_invoke_runs_with_effort_none(self, provider_cls, module, logger_name, monkeypatch):
        """effort=None follows the same path with no behavioural difference."""

        def _no_cli(name: str) -> str:
            raise ProviderError(f"stubbed: no {name} CLI")

        monkeypatch.setattr(f"{module}.resolve_cli_path", _no_cli)

        provider = provider_cls()

        with pytest.raises(ProviderError):
            provider._do_invoke(
                "prompt",
                collector=ResultCollector(),
                model=None,
                timeout=300,
                allowed_tools=None,
                effort=None,
            )

    @pytest.mark.parametrize(
        ("provider_cls", "module", "logger_name"),
        NON_CLAUDE_PROVIDERS,
        ids=lambda p: getattr(p, "__name__", str(p)),
    )
    def test_effort_is_logged_as_ignored(
        self, provider_cls, module, logger_name, monkeypatch, caplog
    ):
        """Each non-Claude provider records that it discarded the value."""
        import logging

        def _no_cli(name: str) -> str:
            raise ProviderError(f"stubbed: no {name} CLI")

        monkeypatch.setattr(f"{module}.resolve_cli_path", _no_cli)

        caplog.set_level(logging.DEBUG, logger=module)

        provider = provider_cls()

        with pytest.raises(ProviderError):
            provider._do_invoke(
                "prompt",
                collector=ResultCollector(),
                model=None,
                timeout=300,
                allowed_tools=None,
                effort="max",
            )

        assert any("effort" in record.getMessage() for record in caplog.records), (
            f"{provider_cls.__name__} does not log that it ignores effort"
        )

    @pytest.mark.parametrize(
        ("provider_cls", "module", "logger_name"),
        NON_CLAUDE_PROVIDERS,
        ids=lambda p: getattr(p, "__name__", str(p)),
    )
    def test_effort_never_becomes_a_cli_argument(self, provider_cls, module, logger_name):
        """G2/rule 2.1: no non-Claude effort behaviour is introduced."""
        import inspect

        source = inspect.getsource(provider_cls._do_invoke)

        assert "--effort" not in source, (
            f"{provider_cls.__name__} must not add non-Claude effort behaviour"
        )
