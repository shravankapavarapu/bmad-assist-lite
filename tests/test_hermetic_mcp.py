"""Tests for the opt-in hermetic run setting (``providers.hermetic``).

The setting makes the Claude provider decline to load whatever MCP servers the
*target project's* ``.mcp.json`` declares. bmad-assist-lite ships no MCP servers
of its own, but it invokes the ``claude`` CLI in the target's working directory,
so the CLI starts that project's servers — the documented source of the
~265 s/run host contention recorded in the June session logs.

Verified against claude-agent-sdk 0.2.135 by introspection, not by document:
``ClaudeAgentOptions.strict_mcp_config`` is a ``bool`` defaulting to ``False``
that the subprocess transport turns into the CLI's ``--strict-mcp-config``
flag ("Only use MCP servers from --mcp-config, ignoring all other MCP
configurations"). Since the provider never populates ``mcp_servers``, the flag
resolves to "no MCP servers at all".

The default is OFF. Turning it on by default would change behaviour for every
downstream user who relies on MCP tools during ``dev_story``, which is a
shipped-default decision the operator has not been asked.
"""

import contextlib
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from bmad_assist_lite.core.config import Config, load_config
from bmad_assist_lite.core.exceptions import ProviderError
from bmad_assist_lite.providers.base import is_hermetic
from bmad_assist_lite.providers.claude_sdk import ClaudeSDKProvider
from bmad_assist_lite.providers.codex import CodexProvider
from bmad_assist_lite.providers.cursor import CursorProvider
from bmad_assist_lite.providers.gemini import GeminiProvider

# ============================================================================
# Helpers
# ============================================================================


def _write_config(tmp_path: Path, body: str) -> Config:
    """Load a project config from YAML text through the real loader."""
    import yaml

    cfg_path = tmp_path / "bmad-assist-lite.yaml"
    cfg_path.write_text(body, encoding="utf-8")
    return load_config(yaml.safe_load(cfg_path.read_text(encoding="utf-8")))


_BASE_PROVIDERS = """
providers:
  master:
    provider: claude
    model: opus
"""


def _cfg(hermetic: bool) -> Config:
    """Build a real Config with ``providers.hermetic`` set."""
    return Config.model_validate(
        {
            "providers": {
                "master": {"provider": "claude", "model": "opus"},
                "hermetic": hermetic,
            }
        }
    )


# ============================================================================
# Config schema — additive, default OFF (G8)
# ============================================================================


class TestHermeticConfigField:
    """``providers.hermetic`` is additive and defaults to False."""

    @pytest.mark.no_auto_config
    def test_absent_key_defaults_to_false(self, tmp_path: Path) -> None:
        """An existing config with no such key loads and behaves identically (G8)."""
        config = _write_config(tmp_path, _BASE_PROVIDERS)
        assert config.providers.hermetic is False

    @pytest.mark.no_auto_config
    def test_key_can_be_enabled(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, _BASE_PROVIDERS + "  hermetic: true\n")
        assert config.providers.hermetic is True

    @pytest.mark.no_auto_config
    def test_key_can_be_explicitly_disabled(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, _BASE_PROVIDERS + "  hermetic: false\n")
        assert config.providers.hermetic is False

    @pytest.mark.no_auto_config
    def test_pre_existing_config_shape_still_loads_unchanged(self, tmp_path: Path) -> None:
        """A full config written before this key existed loads with every field intact."""
        config = _write_config(
            tmp_path,
            """
providers:
  master:
    provider: claude
    model: opus
    effort: max
  multi:
    - provider: claude
      model: sonnet
  cli_paths:
    claude: /usr/bin/claude
timeouts:
  default: 300
  dev_story: 1200
""",
        )
        assert config.providers.master.effort == "max"
        assert [m.model for m in config.providers.multi] == ["sonnet"]
        assert config.providers.cli_paths.claude == "/usr/bin/claude"
        assert config.timeouts.dev_story == 1200
        assert config.providers.hermetic is False


# ============================================================================
# is_hermetic() accessor
# ============================================================================


class TestIsHermetic:
    """The shared accessor reads config defensively, like resolve_cli_path()."""

    def test_defaults_false_under_minimal_config(self) -> None:
        assert is_hermetic() is False

    def test_true_when_config_enables_it(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, _BASE_PROVIDERS + "  hermetic: true\n")
        with patch("bmad_assist_lite.core.config.get_config", return_value=config):
            assert is_hermetic() is True

    def test_never_raises_when_config_is_unavailable(self) -> None:
        """No config loaded is not an error — it means "not hermetic"."""
        with patch(
            "bmad_assist_lite.core.config.get_config",
            side_effect=RuntimeError("config not loaded"),
        ):
            assert is_hermetic() is False


# ============================================================================
# Claude provider — the only provider with a mechanism
# ============================================================================


def _captured_options(hermetic: bool) -> ClaudeAgentOptions:
    """Build the SDK options the provider would use, without running the CLI."""
    import asyncio

    from bmad_assist_lite.providers.result_collector import ResultCollector

    provider = ClaudeSDKProvider()
    captured: list[ClaudeAgentOptions] = []

    def _fake_query(*, prompt: str, options: ClaudeAgentOptions):  # type: ignore[no-untyped-def]
        captured.append(options)

        async def _empty():  # type: ignore[no-untyped-def]
            return
            yield  # pragma: no cover

        return _empty()

    # The stubbed stream is empty, so the provider raises after it has already
    # built and passed the options — which is all this inspects.
    with (
        patch("bmad_assist_lite.providers.claude_sdk.query", _fake_query),
        patch.object(ClaudeSDKProvider, "_resolve_cli_path", return_value=None),
        patch("bmad_assist_lite.core.config.get_config", return_value=_cfg(hermetic)),
        contextlib.suppress(ProviderError),
    ):
        asyncio.run(
            provider._invoke_async_with_collector(
                prompt="hi",
                model="sonnet",
                settings=None,
                cwd=None,
                collector=ResultCollector(),
            )
        )
    assert captured, "query() was never called"
    return captured[0]


class TestClaudeStrictMcpConfig:
    """With the key set, the SDK options carry the strict setting."""

    def test_sdk_field_exists_with_verified_semantics(self) -> None:
        """Guard against an SDK upgrade silently removing the field (F-11's lesson)."""
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(ClaudeAgentOptions)}
        assert "strict_mcp_config" in fields
        assert fields["strict_mcp_config"].type is bool
        assert fields["strict_mcp_config"].default is False

    def test_options_carry_strict_when_hermetic(self) -> None:
        options = _captured_options(hermetic=True)
        assert options.strict_mcp_config is True

    def test_options_unchanged_when_not_hermetic(self) -> None:
        """Without the key, options are exactly what they are today."""
        options = _captured_options(hermetic=False)
        assert options.strict_mcp_config is False

    def test_mcp_servers_stays_empty_so_strict_means_none(self) -> None:
        """``--strict-mcp-config`` allows only servers from ``mcp_servers``.

        The provider must never populate it, or the flag would allow those
        through instead of yielding a hermetic run.
        """
        for hermetic in (True, False):
            options = _captured_options(hermetic=hermetic)
            assert not options.mcp_servers

    def test_strict_flag_reaches_the_cli_argv(self) -> None:
        """The SDK turns the field into the CLI flag — verified, not assumed."""
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

        for hermetic in (True, False):
            transport = SubprocessCLITransport(
                prompt="hi",
                options=ClaudeAgentOptions(strict_mcp_config=hermetic, cli_path="/bin/true"),
            )
            cmd = transport._build_command()
            assert ("--strict-mcp-config" in cmd) is hermetic


# ============================================================================
# The other three providers must not break (G2 — "must not break", not target)
# ============================================================================


NON_CLAUDE_PROVIDERS = [
    (CodexProvider, "bmad_assist_lite.providers.codex"),
    (CursorProvider, "bmad_assist_lite.providers.cursor"),
    (GeminiProvider, "bmad_assist_lite.providers.gemini"),
]


class TestOtherProvidersUnaffected:
    """Accept and ignore with a logger.debug, exactly like ``effort``. Never raise.

    ``resolve_cli_path`` is stubbed to raise so each call aborts just after the
    hermetic-handling block: a ``ProviderError`` proves execution reached that
    far, where an exception about the new setting would not.
    """

    @staticmethod
    def _stub_no_cli(monkeypatch: pytest.MonkeyPatch, module: str) -> None:
        from bmad_assist_lite.core.exceptions import ProviderError

        def _no_cli(name: str) -> str:
            raise ProviderError(f"stubbed: no {name} CLI")

        monkeypatch.setattr(f"{module}.resolve_cli_path", _no_cli)

    @pytest.mark.parametrize(
        ("provider_cls", "module"),
        NON_CLAUDE_PROVIDERS,
        ids=lambda p: getattr(p, "__name__", str(p)),
    )
    @pytest.mark.parametrize("hermetic", [True, False])
    def test_do_invoke_runs_past_hermetic_without_error(
        self,
        provider_cls: type,
        module: str,
        hermetic: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from bmad_assist_lite.core.exceptions import ProviderError
        from bmad_assist_lite.providers.result_collector import ResultCollector

        self._stub_no_cli(monkeypatch, module)
        monkeypatch.setattr("bmad_assist_lite.core.config.get_config", lambda: _cfg(hermetic))

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
        ("provider_cls", "module"),
        NON_CLAUDE_PROVIDERS,
        ids=lambda p: getattr(p, "__name__", str(p)),
    )
    def test_hermetic_is_logged_as_ignored(
        self,
        provider_cls: type,
        module: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Each non-Claude provider records that it discarded the setting."""
        from bmad_assist_lite.core.exceptions import ProviderError
        from bmad_assist_lite.providers.result_collector import ResultCollector

        self._stub_no_cli(monkeypatch, module)
        monkeypatch.setattr("bmad_assist_lite.core.config.get_config", lambda: _cfg(True))
        caplog.set_level(logging.DEBUG, logger=module)

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

        assert any("hermetic" in r.getMessage() for r in caplog.records), (
            f"{provider_cls.__name__} does not log that it ignores hermetic"
        )

    @pytest.mark.parametrize(
        ("provider_cls", "module"),
        NON_CLAUDE_PROVIDERS,
        ids=lambda p: getattr(p, "__name__", str(p)),
    )
    def test_hermetic_never_becomes_a_cli_argument(self, provider_cls: type, module: str) -> None:
        """G2 "must not break", never a target: no new non-Claude CLI behaviour."""
        import inspect

        source = inspect.getsource(provider_cls._do_invoke)
        for forbidden in ("--strict-mcp-config", "--hermetic", "strict_mcp_config"):
            assert forbidden not in source, (
                f"{provider_cls.__name__} must not translate hermetic into CLI behaviour"
            )


# ============================================================================
# Run-log visibility — an unrecorded condition is not reproducible
# ============================================================================


class TestHermeticIsRecordedInTheRunLog:
    """An operator must be able to tell from the record whether a run was hermetic."""

    @pytest.mark.parametrize("hermetic", [True, False])
    def test_run_conditions_logged(
        self, hermetic: bool, caplog: pytest.LogCaptureFixture, tmp_path: Path
    ) -> None:
        from bmad_assist_lite.loop.runner import log_run_conditions

        config = _write_config(tmp_path, _BASE_PROVIDERS + f"  hermetic: {str(hermetic).lower()}\n")
        with caplog.at_level(logging.INFO, logger="bmad_assist_lite.loop.runner"):
            log_run_conditions(config)

        assert "hermetic" in caplog.text.lower()
        assert f"hermetic={hermetic}" in caplog.text

    def test_log_line_is_emitted_by_run_loop(self) -> None:
        """The recording is wired into the loop entry point, not merely available."""
        import inspect

        from bmad_assist_lite.loop import runner

        assert "log_run_conditions(config)" in inspect.getsource(runner.run_loop)
