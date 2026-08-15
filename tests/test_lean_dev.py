"""Unit tests for SP-D1 lean_dev (goal-run7): the dev_story output-economy addendum.

Pins the WIRING (flag off -> base prompt byte-identical; flag on -> addendum
appended once) and the CONTENT CONTRACT (economy language only; never an effort
notch -- it must not tell dev to think less, skip tests, or write less code).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from bmad_assist_lite.core.config import LeanDev, load_config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.dev_story import DevStoryHandler

_BASE = "bmad_assist_lite.loop.handlers.base.BaseHandler.render_prompt"


def _handler(tmp_path: Path, *, lean_dev: bool | str) -> DevStoryHandler:
    config = load_config(
        {
            "providers": {"master": {"provider": "claude", "model": "opus"}},
            "speed": {"lean_dev": lean_dev},
        }
    )
    return DevStoryHandler(config, tmp_path)


class TestLeanDevWiring:
    """The flag gates a single appended addendum; off is byte-identical."""

    def test_defaults_off(self) -> None:
        config = load_config(
            {"providers": {"master": {"provider": "claude", "model": "opus"}}}
        )
        assert config.speed.lean_dev is LeanDev.OFF

    def test_off_leaves_prompt_unchanged(self, tmp_path: Path) -> None:
        handler = _handler(tmp_path, lean_dev=False)
        state = State(current_epic=3, current_story="3.1")
        with patch(_BASE, return_value="BASE PROMPT"):
            assert handler.render_prompt(state) == "BASE PROMPT"

    def test_on_appends_addendum_once(self, tmp_path: Path) -> None:
        handler = _handler(tmp_path, lean_dev=True)
        state = State(current_epic=3, current_story="3.1")
        with patch(_BASE, return_value="BASE PROMPT"):
            out = handler.render_prompt(state)
        assert out.startswith("BASE PROMPT\n\n")
        assert out.count("<lean-dev>") == 1 and out.count("</lean-dev>") == 1
        # the base prompt is preserved verbatim ahead of the addendum
        assert out.split("\n\n<lean-dev>")[0] == "BASE PROMPT"


class TestLeanDevContract:
    """The addendum trims description, never the work — no effort notch."""

    def test_is_economy_not_an_effort_notch(self) -> None:
        text = DevStoryHandler._lean_dev_addendum().lower()
        for forbidden in (
            "think less",
            "less thinking",
            "skip test",
            "skip the test",
            "fewer test",
            "don't test",
            "do not test",
            "write less code",
            "less code",
            "lower effort",
            "reduce effort",
            "reduce delivered",
        ):
            assert forbidden not in text, f"lean_dev addendum must not say {forbidden!r}"

    def test_keeps_the_real_work_explicit(self) -> None:
        text = DevStoryHandler._lean_dev_addendum().lower()
        # acceptance criteria + tests + checks are still required, in full
        assert "acceptance criterion" in text or "acceptance criteria" in text
        assert "test" in text
        # the Write-over-Edit + minimal-Edit-context economy guidance is present
        assert "write" in text and "edit" in text
        # the final-report economy (no code re-print) is present
        assert "final report" in text and "re-print" in text


class TestLeanDevEnum:
    """SP-D1b: lean_dev is a three-state mode; the legacy bool still coerces."""

    def _load(self, value: object):
        return load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "speed": {"lean_dev": value},
            }
        )

    def test_legacy_true_is_full(self) -> None:
        assert self._load(True).speed.lean_dev is LeanDev.FULL

    def test_legacy_false_is_off(self) -> None:
        assert self._load(False).speed.lean_dev is LeanDev.OFF

    def test_string_values_accepted(self) -> None:
        for value, expected in (
            ("off", LeanDev.OFF),
            ("full", LeanDev.FULL),
            ("report_only", LeanDev.REPORT_ONLY),
            ("FULL", LeanDev.FULL),  # case-insensitive
        ):
            assert self._load(value).speed.lean_dev is expected

    def test_full_is_byte_identical_to_legacy_true(self, tmp_path: Path) -> None:
        state = State(current_epic=3, current_story="3.1")
        with patch(_BASE, return_value="BASE PROMPT"):
            full = _handler(tmp_path, lean_dev="full").render_prompt(state)
            legacy = _handler(tmp_path, lean_dev=True).render_prompt(state)
        assert full == legacy
        assert "<lean-dev>" in full

    def test_off_appends_nothing(self, tmp_path: Path) -> None:
        handler = _handler(tmp_path, lean_dev="off")
        state = State(current_epic=3, current_story="3.1")
        with patch(_BASE, return_value="BASE PROMPT"):
            assert handler.render_prompt(state) == "BASE PROMPT"

    def test_report_only_appends_report_addendum(self, tmp_path: Path) -> None:
        handler = _handler(tmp_path, lean_dev="report_only")
        state = State(current_epic=3, current_story="3.1")
        with patch(_BASE, return_value="BASE PROMPT"):
            out = handler.render_prompt(state)
        assert out.startswith("BASE PROMPT\n\n")
        assert out.count("<lean-dev-report>") == 1 and out.count("</lean-dev-report>") == 1
        # It is NOT the full whole-phase addendum.
        assert "<lean-dev>" not in out


class TestLeanDevReportAddendum:
    """The report-scoped variant trims only the report, not the working phase."""

    def test_report_only_scopes_to_the_report(self) -> None:
        text = DevStoryHandler._lean_dev_report_addendum().lower()
        assert "final report" in text and "re-print" in text
        # The working-phase economy rules of the full addendum must be ABSENT —
        # leaving the working phase untouched is the whole point of the probe.
        assert "do not narrate" not in text
        assert "incremental edit" not in text

    def test_report_only_is_not_an_effort_notch(self) -> None:
        text = DevStoryHandler._lean_dev_report_addendum().lower()
        for forbidden in (
            "think less",
            "less thinking",
            "skip test",
            "write less code",
            "lower effort",
        ):
            assert forbidden not in text, f"report addendum must not say {forbidden!r}"
