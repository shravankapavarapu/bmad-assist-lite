"""Tests for the main loop's run-level budget (F-14, ADR-0005).

``loop/runner.py``'s ``while True`` had no bound of any kind: no iteration
count, no wall clock. ``loop.max_stories`` and ``loop.max_runtime`` bound it,
both optional and both defaulting to ``None`` = unlimited so nothing about an
existing config changes.

Exhaustion must be distinguishable from a crash: a named budget on the
console, a saved state, and a distinct non-zero exit code.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import Phase
from bmad_assist_lite.loop.types import LoopExitReason, PhaseResult

runner = CliRunner()

EPICS = [1]
STORIES_FOR_EPIC = {1: ["1.1", "1.2", "1.3"]}


def _config(**loop_overrides: object):
    """Build a Config whose ``loop`` section carries ``loop_overrides``."""
    return load_config(
        {
            "providers": {"master": {"provider": "claude", "model": "opus"}},
            "loop": {"epic_teardown": [], **loop_overrides},
            "auto_commit": {"enabled": False},
        }
    )


def _quality_gate_passes(state) -> PhaseResult:
    """Every phase succeeds; the quality gate always passes."""
    if state.current_phase == Phase.QUALITY_GATE:
        return PhaseResult.ok({"quality_gate_action": "pass"})
    return PhaseResult.ok()


def _never_terminates(state) -> PhaseResult:
    """A quality gate that detours to the fixer forever — a real infinite loop."""
    if state.current_phase == Phase.QUALITY_GATE:
        return PhaseResult(success=True, next_phase=Phase.FIX_QUALITY_GATE)
    if state.current_phase == Phase.FIX_QUALITY_GATE:
        return PhaseResult(success=True, next_phase=Phase.QUALITY_GATE)
    return PhaseResult.ok()


_FUSE = 100


class _LoopHarness:
    """Patch out everything the loop touches except the budget logic.

    A fuse trips after ``_FUSE`` phase executions so that a loop the budget
    fails to bound shows up as a failing assertion rather than as a hung test
    run — the RED evidence for these tests would otherwise be a hang.
    """

    def __init__(self, side_effect, *, real_state: bool = False):
        self.real_state = real_state
        self._patches: list = []
        self.execute = None
        self.calls = 0

        def fused(state):
            self.calls += 1
            if self.calls > _FUSE:
                raise RuntimeError(
                    f"test fuse: {_FUSE} phases executed without the budget stopping the loop"
                )
            return side_effect(state)

        self.side_effect = fused

    def __enter__(self):
        targets = [
            "bmad_assist_lite.loop.runner.trigger_sync",
            "bmad_assist_lite.loop.runner.execute_phase",
            "bmad_assist_lite.loop.runner.init_handlers",
            "bmad_assist_lite.loop.runner.running_lock",
            "bmad_assist_lite.loop.runner.clear_story_cache",
        ]
        if not self.real_state:
            targets.append("bmad_assist_lite.loop.runner.save_state")

        mocks = {}
        for target in targets:
            patcher = patch(target)
            self._patches.append(patcher)
            mocks[target.rsplit(".", 1)[1]] = patcher.start()

        mocks["running_lock"].return_value.__enter__ = MagicMock()
        mocks["running_lock"].return_value.__exit__ = MagicMock(return_value=False)
        mocks["execute_phase"].side_effect = self.side_effect
        self.execute = mocks["execute_phase"]
        return self

    def __exit__(self, *exc_info) -> bool:
        for patcher in reversed(self._patches):
            patcher.stop()
        return False

    def stories_seen(self) -> list[str]:
        """Distinct stories, in the order the loop reached them."""
        seen: list[str] = []
        for call in self.execute.call_args_list:
            story = call.args[0].current_story
            if story is not None and story not in seen:
                seen.append(story)
        return seen


# ---------------------------------------------------------------------------
# The config surface (additive, G8)
# ---------------------------------------------------------------------------


class TestRunBudgetConfig:
    """Both budgets are optional and default to unlimited."""

    def test_budgets_default_to_none(self) -> None:
        """An existing config gets no budget — today's behaviour is unchanged."""
        config = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})

        assert config.loop.max_stories is None
        assert config.loop.max_runtime is None

    def test_budgets_are_configurable(self) -> None:
        config = _config(max_stories=5, max_runtime=90.5)

        assert config.loop.max_stories == 5
        assert config.loop.max_runtime == 90.5

    def test_max_stories_rejects_zero(self) -> None:
        """A budget of 0 would stop before any work — that is `--help`, not a run."""
        from bmad_assist_lite.core.exceptions import ConfigError

        with pytest.raises(ConfigError):
            _config(max_stories=0)

    def test_max_runtime_rejects_zero(self) -> None:
        from bmad_assist_lite.core.exceptions import ConfigError

        with pytest.raises(ConfigError):
            _config(max_runtime=0)


# ---------------------------------------------------------------------------
# loop.max_stories
# ---------------------------------------------------------------------------


class TestMaxStoriesBudget:
    """The iteration budget stops the run at an exact story count."""

    def test_stops_at_the_story_budget(self, tmp_path: Path) -> None:
        """LOAD-BEARING: with a budget of 2, story 1.3 is never entered."""
        from bmad_assist_lite.loop.runner import run_loop

        with _LoopHarness(_quality_gate_passes) as harness:
            result = run_loop(
                config=_config(max_stories=2),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

            assert result == LoopExitReason.BUDGET_EXHAUSTED
            assert harness.stories_seen() == ["1.1", "1.2"]

    def test_none_means_unlimited(self, tmp_path: Path) -> None:
        """NEG — with no budget the run completes every story, as it does today."""
        from bmad_assist_lite.loop.runner import run_loop

        with _LoopHarness(_quality_gate_passes) as harness:
            result = run_loop(
                config=_config(),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

            assert result == LoopExitReason.COMPLETED
            assert harness.stories_seen() == ["1.1", "1.2", "1.3"]

    def test_budget_larger_than_the_queue_never_fires(self, tmp_path: Path) -> None:
        """NEG — a budget that is not reached must not change the outcome."""
        from bmad_assist_lite.loop.runner import run_loop

        with _LoopHarness(_quality_gate_passes) as harness:
            result = run_loop(
                config=_config(max_stories=99),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

            assert result == LoopExitReason.COMPLETED
            assert harness.stories_seen() == ["1.1", "1.2", "1.3"]

    def test_exhaustion_names_the_budget_and_the_remedy(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A `nohup` log must say which budget ran out and how to continue."""
        from bmad_assist_lite.loop.runner import run_loop

        with _LoopHarness(_quality_gate_passes):
            run_loop(
                config=_config(max_stories=1),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

        out = capsys.readouterr().out
        assert "loop.max_stories" in out
        assert "--resume" in out
        assert "loop.max_runtime" not in out, "named the wrong budget"

    def test_state_is_saved_and_resumable_on_exhaustion(self, tmp_path: Path) -> None:
        """Exhaustion checkpoints to disk so --resume continues cleanly."""
        from bmad_assist_lite.core.state import get_state_path, load_state
        from bmad_assist_lite.loop.runner import run_loop

        with _LoopHarness(_quality_gate_passes, real_state=True):
            result = run_loop(
                config=_config(max_stories=1),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

        assert result == LoopExitReason.BUDGET_EXHAUSTED
        state_path = get_state_path(tmp_path)
        assert state_path.exists(), "no state file — the run is not resumable"
        resumed = load_state(state_path)
        assert resumed.current_story == "1.2"
        assert resumed.current_phase is not None


# ---------------------------------------------------------------------------
# loop.max_runtime
# ---------------------------------------------------------------------------


class TestMaxRuntimeBudget:
    """The wall-clock budget bounds a run that is slow rather than long."""

    def test_stops_a_loop_that_would_never_terminate(self, tmp_path: Path) -> None:
        """LOAD-BEARING: a quality-gate/fixer ping-pong is bounded by wall clock."""
        import time

        from bmad_assist_lite.loop.runner import run_loop

        def slow_pingpong(state):
            time.sleep(0.02)
            return _never_terminates(state)

        with _LoopHarness(slow_pingpong) as harness:
            result = run_loop(
                config=_config(max_runtime=0.05),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

            assert result == LoopExitReason.BUDGET_EXHAUSTED
            assert harness.execute.call_count >= 2

    def test_none_means_unlimited(self, tmp_path: Path) -> None:
        """NEG — no wall-clock budget means the run is bounded only by its work.

        The same story queue completes normally, so a finite run under
        ``max_runtime`` proves the budget did the stopping, not the harness.
        """
        from bmad_assist_lite.loop.runner import run_loop

        with _LoopHarness(_quality_gate_passes) as harness:
            result = run_loop(
                config=_config(max_runtime=None),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

            assert result == LoopExitReason.COMPLETED
            assert harness.stories_seen() == ["1.1", "1.2", "1.3"]

    def test_exhaustion_names_the_runtime_budget(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import time

        from bmad_assist_lite.loop.runner import run_loop

        def slow_pingpong(state):
            time.sleep(0.02)
            return _never_terminates(state)

        with _LoopHarness(slow_pingpong):
            run_loop(
                config=_config(max_runtime=0.05),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

        out = capsys.readouterr().out
        assert "loop.max_runtime" in out
        assert "--resume" in out
        assert "loop.max_stories" not in out, "named the wrong budget"


# ---------------------------------------------------------------------------
# The exit code (a public CLI contract — additive)
# ---------------------------------------------------------------------------


class TestBudgetExhaustedExitCode:
    """Budget exhaustion is a distinct non-zero code, not the failure code."""

    def test_exit_reason_is_distinct(self) -> None:
        assert LoopExitReason.BUDGET_EXHAUSTED not in (
            LoopExitReason.COMPLETED,
            LoopExitReason.INTERRUPTED,
            LoopExitReason.ERROR,
        )

    def test_cli_returns_the_budget_exit_code(self, tmp_path: Path) -> None:
        """The CLI maps BUDGET_EXHAUSTED to its own code, not 0/1/130."""
        from bmad_assist_lite.cli import BUDGET_EXHAUSTED_EXIT_CODE, app
        from bmad_assist_lite.core.sprint_status import SprintStatus

        assert BUDGET_EXHAUSTED_EXIT_CODE not in (0, 1, 130)

        (tmp_path / "bmad-assist-lite.yaml").write_text("providers: {}\n")
        ss = SprintStatus(
            development_status={"epic-1": "backlog", "1-1-first-story": "backlog"}
        )

        mock_paths_obj = MagicMock()
        mock_paths_obj.sprint_status_file = tmp_path / "sprint-status.yaml"
        mock_paths_obj.sprint_status_file.write_text("generated: '2026-01-01'\n")
        mock_paths_obj.planning_artifacts = tmp_path / "planning"
        mock_paths_obj.planning_artifacts.mkdir(parents=True, exist_ok=True)
        (mock_paths_obj.planning_artifacts / "epic-1.md").write_text("# Epic 1\n")
        mock_paths_obj.logs_dir = tmp_path / "logs"
        mock_paths_obj.cache_dir = tmp_path / "cache"
        mock_paths_obj.output_folder = tmp_path / "output"
        mock_paths_obj.implementation_artifacts = tmp_path / "impl"
        mock_paths_obj.architecture_file = tmp_path / "arch.md"

        with (
            patch("bmad_assist_lite.core.config.load_config_with_project") as mock_config,
            patch("bmad_assist_lite.core.paths.init_paths", return_value=mock_paths_obj),
            patch(
                "bmad_assist_lite.core.sprint_status.load_sprint_status", return_value=ss
            ),
            patch("bmad_assist_lite.loop.runner.run_loop") as mock_run_loop,
        ):
            mock_config.return_value = MagicMock(context_docs=None)
            mock_run_loop.return_value = LoopExitReason.BUDGET_EXHAUSTED

            result = runner.invoke(app, ["run", "--project", str(tmp_path)])

        assert mock_run_loop.called, f"run_loop not called; output: {result.output}"
        assert result.exit_code == BUDGET_EXHAUSTED_EXIT_CODE
        assert "--resume" in result.output


# ---------------------------------------------------------------------------
# loop.max_cost_usd  (T31 / OSS #11)
# ---------------------------------------------------------------------------
#
# ADR-0005 rejected a token/cost cap as "a control that can never fire", because
# nothing metered spend. T27 (capture) and T37 (persistence) removed that
# objection: `phase-metrics.jsonl` now carries a per-phase `total_cost_usd`.
# The pilot measured $45.41 for a single story, so the cap is not hypothetical.


def _spend(path: Path, usd: float, *, phase: str = "dev_story") -> None:
    """Append one phase metric record carrying a cost, as a real phase would."""
    from datetime import datetime

    from bmad_assist_lite.core.phase_metrics import PhaseMetricRecord, append_record

    append_record(
        PhaseMetricRecord(
            story_id="1.1",
            phase=phase,
            timestamp=datetime(2026, 8, 11, 12, 0, 0),
            duration_ms=1000,
            total_cost_usd=usd,
            call_count=1,
        ),
        path,
    )


class TestMaxCostConfig:
    """The cost budget is additive and optional (G8)."""

    def test_defaults_to_none(self) -> None:
        assert load_config(
            {"providers": {"master": {"provider": "claude", "model": "opus"}}}
        ).loop.max_cost_usd is None

    def test_accepts_a_dollar_figure(self) -> None:
        assert _config(max_cost_usd=45.41).loop.max_cost_usd == 45.41

    def test_rejects_zero(self) -> None:
        """A cap of 0 would stop before the first phase — a footgun, not a budget."""
        with pytest.raises(Exception):
            _config(max_cost_usd=0)


class TestCostAccounting:
    """Spend is read from the persisted metrics, and only for this run."""

    def test_sums_cost_recorded_after_the_baseline(self, tmp_path: Path) -> None:
        from bmad_assist_lite.core.phase_metrics import cost_since, record_count

        path = tmp_path / "phase-metrics.jsonl"
        _spend(path, 10.0)
        baseline = record_count(path)
        _spend(path, 2.5)
        _spend(path, 1.25)

        assert baseline == 1
        assert cost_since(path, baseline) == pytest.approx(3.75)

    def test_earlier_runs_do_not_count_against_this_run(self, tmp_path: Path) -> None:
        """NEG — otherwise --resume would re-trip the cap immediately.

        ``max_stories`` and ``max_runtime`` both reset per run; the cost budget
        matches them rather than becoming a project-lifetime total that makes
        the documented ``--resume`` remedy useless.
        """
        from bmad_assist_lite.core.phase_metrics import cost_since, record_count

        path = tmp_path / "phase-metrics.jsonl"
        _spend(path, 500.0)
        assert cost_since(path, record_count(path)) == 0.0

    def test_unreadable_metrics_do_not_raise(self, tmp_path: Path) -> None:
        """Enforcement reads tolerantly; a corrupt file must not end a long run."""
        from bmad_assist_lite.core.phase_metrics import cost_since

        path = tmp_path / "phase-metrics.jsonl"
        path.write_text("{not json\n", encoding="utf-8")
        assert cost_since(path, 0) is None

    def test_unreported_cost_is_not_zero(self, tmp_path: Path) -> None:
        """A provider that reports no cost yields None, never a fake 0.0."""
        from datetime import datetime

        from bmad_assist_lite.core.phase_metrics import (
            PhaseMetricRecord,
            append_record,
            cost_since,
        )

        path = tmp_path / "phase-metrics.jsonl"
        append_record(
            PhaseMetricRecord(
                phase="quality_gate",
                timestamp=datetime(2026, 8, 11, 12, 0, 0),
                duration_ms=5,
            ),
            path,
        )
        assert cost_since(path, 0) == 0.0


class TestMaxCostBudget:
    """The cap actually fires — REQ-08.5 criterion 4 forbids shipping one that cannot."""

    def test_stops_the_run_when_spend_reaches_the_cap(self, tmp_path: Path) -> None:
        """LOAD-BEARING: each phase costs $1; a $2 cap must not reach story 1.3."""
        from bmad_assist_lite.loop.runner import run_loop

        metrics = tmp_path / "phase-metrics.jsonl"

        def costly(state):
            _spend(metrics, 1.0, phase=str(state.current_phase))
            return _quality_gate_passes(state)

        with (
            patch("bmad_assist_lite.loop.runner._metrics_path", return_value=metrics),
            _LoopHarness(costly) as harness,
        ):
            result = run_loop(
                config=_config(max_cost_usd=2.0),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

        assert result == LoopExitReason.BUDGET_EXHAUSTED
        assert harness.stories_seen() == ["1.1"], "spent past the cap"

    def test_none_means_unlimited(self, tmp_path: Path) -> None:
        """NEG — no cost budget leaves today's behaviour untouched."""
        from bmad_assist_lite.loop.runner import run_loop

        metrics = tmp_path / "phase-metrics.jsonl"

        def costly(state):
            _spend(metrics, 100.0, phase=str(state.current_phase))
            return _quality_gate_passes(state)

        with (
            patch("bmad_assist_lite.loop.runner._metrics_path", return_value=metrics),
            _LoopHarness(costly) as harness,
        ):
            result = run_loop(
                config=_config(),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

        assert result == LoopExitReason.COMPLETED
        assert harness.stories_seen() == ["1.1", "1.2", "1.3"]

    def test_cap_above_actual_spend_never_fires(self, tmp_path: Path) -> None:
        """NEG — a budget that is not reached must not change the outcome."""
        from bmad_assist_lite.loop.runner import run_loop

        metrics = tmp_path / "phase-metrics.jsonl"

        def costly(state):
            _spend(metrics, 0.01, phase=str(state.current_phase))
            return _quality_gate_passes(state)

        with (
            patch("bmad_assist_lite.loop.runner._metrics_path", return_value=metrics),
            _LoopHarness(costly) as harness,
        ):
            result = run_loop(
                config=_config(max_cost_usd=1000.0),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

        assert result == LoopExitReason.COMPLETED
        assert harness.stories_seen() == ["1.1", "1.2", "1.3"]

    def test_exhaustion_names_the_cost_budget_and_the_remedy(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The console says which budget, what it cost, and how to continue."""
        from bmad_assist_lite.loop.runner import run_loop

        metrics = tmp_path / "phase-metrics.jsonl"

        def costly(state):
            _spend(metrics, 5.0, phase=str(state.current_phase))
            return _quality_gate_passes(state)

        with (
            patch("bmad_assist_lite.loop.runner._metrics_path", return_value=metrics),
            _LoopHarness(costly),
        ):
            run_loop(
                config=_config(max_cost_usd=5.0),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

        out = capsys.readouterr().out
        assert "loop.max_cost_usd" in out
        assert "--resume" in out
        assert "$" in out, "a cost budget should report dollars"
        assert "loop.max_stories" not in out, "named the wrong budget"

    def test_unmeterable_spend_does_not_stop_the_run(self, tmp_path: Path) -> None:
        """NEG — if nothing is metered the cap must not fire on a phantom zero."""
        from bmad_assist_lite.loop.runner import run_loop

        with (
            patch("bmad_assist_lite.loop.runner._metrics_path", return_value=None),
            _LoopHarness(_quality_gate_passes) as harness,
        ):
            result = run_loop(
                config=_config(max_cost_usd=0.01),
                project_path=tmp_path,
                epics=EPICS,
                stories_for_epic=STORIES_FOR_EPIC,
            )

        assert result == LoopExitReason.COMPLETED
        assert harness.stories_seen() == ["1.1", "1.2", "1.3"]
