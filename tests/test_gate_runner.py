"""Tests for the shared gate runner (`core/gate_runner.py`).

Every guard here was proven RED before it was made green: the mutations that break
each load-bearing assertion are named in the test docstrings.
"""

import logging
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.core.command_runner import CommandResult
from bmad_assist_lite.core.gate_runner import (
    DEFAULT_GATE_POOL_SIZE,
    ENV_BLOCKED_LABEL,
    GATE_DURATION_LOG_PREFIX,
    GateClassification,
    GateCommand,
    GateRunnerError,
    active_gate_commands,
    assert_tree_quiescent,
    classify_exit_code,
    make_base_bootstrap,
    reset_base_bootstrap_state,
    run_gates,
)
from bmad_assist_lite.parallel.bootstrap import BootstrapResult
from bmad_assist_lite.providers.base import WINDOWS_COMMAND_NOT_FOUND, ExitStatus

PY = sys.executable


def _sleep_cmd(seconds: float) -> str:
    return f'{PY} -c "import time; time.sleep({seconds})"'


def _exit_cmd(code: int, stdout: str = "", stderr: str = "") -> str:
    body = ""
    if stdout:
        body += f"import sys; sys.stdout.write({stdout!r}); "
    if stderr:
        body += f"import sys; sys.stderr.write({stderr!r}); "
    return f'{PY} -c "{body}raise SystemExit({code})"'


@pytest.fixture(autouse=True)
def _reset_bootstrap_state() -> None:
    reset_base_bootstrap_state()


# ============================================================================
# REQ-02.3 / REQ-02.6 — classification
# ============================================================================


class TestClassification:
    """The single classification vocabulary, and its platform awareness."""

    @pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
    def test_missing_command_is_env_on_every_platform(self, platform: str) -> None:
        """127 stays NOT_FOUND everywhere: run_command synthesises it itself."""
        assert classify_exit_code(127, platform=platform) is GateClassification.ENV
        assert classify_exit_code(126, platform=platform) is GateClassification.ENV

    def test_cmd_exe_command_not_found_is_env_on_windows(self) -> None:
        """9009 is cmd.exe's command-not-found; 126/127 are shell conventions."""
        assert (
            classify_exit_code(WINDOWS_COMMAND_NOT_FOUND, platform="win32")
            is GateClassification.ENV
        )

    def test_9009_is_not_env_on_posix(self) -> None:
        """A POSIX process exiting 9009 is not a missing command."""
        assert (
            classify_exit_code(WINDOWS_COMMAND_NOT_FOUND, platform="linux")
            is GateClassification.REAL
        )

    def test_exit_status_is_platform_aware(self) -> None:
        assert ExitStatus.from_code(9009, platform="win32") is ExitStatus.NOT_FOUND
        assert ExitStatus.from_code(9009, platform="linux") is ExitStatus.SIGNAL
        assert ExitStatus.from_code(137, platform="linux") is ExitStatus.SIGNAL
        assert ExitStatus.from_code(137, platform="win32") is ExitStatus.ERROR
        assert ExitStatus.get_signal_number(137, platform="win32") is None
        assert ExitStatus.get_signal_number(137, platform="linux") == 9

    def test_ordinary_failure_is_real(self) -> None:
        assert classify_exit_code(1) is GateClassification.REAL

    def test_classification_is_not_derived_from_stderr_text(self, tmp_path: Path) -> None:
        """A real failure whose stderr says "command not found" is still real."""
        run = run_gates(
            [
                GateCommand(
                    name="Tests",
                    command=_exit_cmd(1, stderr="E   AssertionError: command not found\n"),
                )
            ],
            tmp_path,
            report=False,
        )
        assert run.failures[0].classification is GateClassification.REAL

    def test_unmapped_exit_code_is_real_and_warned(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An unmapped code must not fall through to env (REQ-02.6 crit 2)."""
        with caplog.at_level(logging.WARNING):
            run = run_gates(
                [GateCommand(name="Tests", command=_exit_cmd(3))], tmp_path, report=False
            )
        assert run.failures[0].classification is GateClassification.REAL
        assert any("unmapped code 3" in r.getMessage() for r in caplog.records)

    def test_enum_has_exactly_two_members(self) -> None:
        """v1 ships real and env — no silent flaky/test_maintenance fallbacks."""
        assert {m.value for m in GateClassification} == {"real", "env"}

    def test_no_literal_exit_code_map_in_the_runner(self) -> None:
        """The runner must not define a second exit-code vocabulary."""
        import ast

        source = Path("src/bmad_assist_lite/core/gate_runner.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                literals = {
                    n.value
                    for n in [node.left, *node.comparators]
                    if isinstance(n, ast.Constant) and isinstance(n.value, int)
                }
                assert not (literals & {126, 127, 9009}), (
                    "gate_runner.py compares a raw exit code — classification must "
                    "come from ExitStatus.from_code()"
                )
        assert "ExitStatus.from_code" in source


# ============================================================================
# REQ-02.10 — the runner cannot fake green
# ============================================================================


class TestGateRunnerGoesRed:
    """The four negative tests REQ-02.10 names."""

    def test_planted_failing_command_reports_fail(self, tmp_path: Path) -> None:
        run = run_gates(
            [GateCommand(name="Tests", command=_exit_cmd(1))], tmp_path, report=False
        )
        assert run.all_passed is False
        assert run.failures[0].status_label == "FAIL"

    def test_planted_timeout_reports_fail(self, tmp_path: Path) -> None:
        run = run_gates(
            [GateCommand(name="Tests", command=_sleep_cmd(30))],
            tmp_path,
            timeout=1,
            report=False,
        )
        assert run.all_passed is False
        assert run.failures[0].classification is GateClassification.REAL

    def test_stderr_without_failure_is_pass(self, tmp_path: Path) -> None:
        """Stderr is not a failure signal (exit 0 wins)."""
        run = run_gates(
            [GateCommand(name="Lint", command=_exit_cmd(0, stderr="warning: noisy\n"))],
            tmp_path,
            report=False,
        )
        assert run.all_passed is True

    def test_planted_concurrent_failure_reports_fail_and_all_results(
        self, tmp_path: Path
    ) -> None:
        commands = [
            GateCommand(name="Lint", command=_exit_cmd(0)),
            GateCommand(name="Typecheck", command=_exit_cmd(0)),
            GateCommand(name="Build", command=_exit_cmd(1)),
            GateCommand(name="Tests", command=_exit_cmd(0)),
        ]
        run = run_gates(commands, tmp_path, report=False)
        assert run.all_passed is False
        assert len(run.outcomes) == 4
        assert [o.passed for o in run.outcomes] == [True, True, False, True]


# ============================================================================
# REQ-02.2 — bounded concurrency
# ============================================================================


class TestConcurrency:
    """Gates fan out into a bounded pool; writes serialise."""

    def test_parallel_is_faster_than_sequential_same_commands(
        self, tmp_path: Path
    ) -> None:
        """Self-calibrating: the same work, pool=4 vs the pool=1 kill switch.

        A fixed wall-clock threshold cannot distinguish a correct bounded pool from
        a broken one on a loaded host. Comparing the two pool sizes in the same
        process can, and it is exactly the comparison REQ-02.2 is about.
        """
        commands = [GateCommand(name=f"G{i}", command=_sleep_cmd(0.4)) for i in range(4)]

        serial = run_gates(commands, tmp_path, pool_size=1, report=False)
        parallel = run_gates(commands, tmp_path, pool_size=4, report=False)

        assert serial.all_passed and parallel.all_passed
        assert parallel.duration_ms < serial.duration_ms

    def test_peak_concurrency_equals_pool_size(self, tmp_path: Path) -> None:
        """Observed overlap, not elapsed time — immune to host load."""
        peak = _measure_peak_concurrency(tmp_path, count=4, pool_size=4)
        assert peak == 4

    def test_pool_is_bounded_not_unbounded_fanout(self, tmp_path: Path) -> None:
        """Eight commands with a pool of 4 must never show 5 in flight."""
        peak = _measure_peak_concurrency(tmp_path, count=8, pool_size=DEFAULT_GATE_POOL_SIZE)
        assert peak <= DEFAULT_GATE_POOL_SIZE

    def test_pool_size_one_is_the_kill_switch(self, tmp_path: Path) -> None:
        peak = _measure_peak_concurrency(tmp_path, count=4, pool_size=1)
        assert peak == 1

    def test_result_order_matches_declared_order(self, tmp_path: Path) -> None:
        """Deliberately inverted completion times must not reorder results."""
        commands = [
            GateCommand(name="Slowest", command=_sleep_cmd(0.45)),
            GateCommand(name="Slower", command=_sleep_cmd(0.30)),
            GateCommand(name="Fast", command=_sleep_cmd(0.15)),
            GateCommand(name="Fastest", command=_exit_cmd(0)),
        ]
        run = run_gates(commands, tmp_path, pool_size=4, report=False)
        assert [o.name for o in run.outcomes] == [
            "Slowest",
            "Slower",
            "Fast",
            "Fastest",
        ]

    def test_tree_mutating_commands_never_overlap(self, tmp_path: Path) -> None:
        """`serial=True` commands are writes — they must not overlap anything."""
        marker = tmp_path / "overlap.log"
        script = (
            f"import time, pathlib; p = pathlib.Path({str(marker)!r}); "
            "p.open('a').write('IN\\n'); time.sleep(0.3); p.open('a').write('OUT\\n')"
        )
        commands = [
            GateCommand(name="BuildA", command=f'{PY} -c "{script}"', serial=True),
            GateCommand(name="BuildB", command=f'{PY} -c "{script}"', serial=True),
            GateCommand(name="Lint", command=_sleep_cmd(0.2)),
        ]
        run = run_gates(commands, tmp_path, pool_size=4, report=False)
        assert run.all_passed

        entries = marker.read_text(encoding="utf-8").split()
        depth = 0
        for entry in entries:
            depth += 1 if entry == "IN" else -1
            assert depth <= 1, "two tree-mutating gate commands overlapped"

    def test_console_writes_are_not_interleaved(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        commands = [
            GateCommand(name=f"Gate{i}", command=_exit_cmd(0)) for i in range(4)
        ]
        run_gates(commands, tmp_path, pool_size=4)
        out = capsys.readouterr().out
        for line in out.splitlines():
            assert line.count("Gate summary") <= 1
            assert not (line.count("PASS") > 1)


def _measure_peak_concurrency(tmp_path: Path, count: int, pool_size: int) -> int:
    """Run `count` commands and return the highest observed simultaneous count."""
    lock = threading.Lock()
    active = 0
    peak = 0
    real_run = run_gates.__globals__["run_command"]

    def _tracked(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.25)
            return CommandResult(
                command=command, exit_code=0, stdout="", stderr="", duration_ms=250
            )
        finally:
            with lock:
                active -= 1

    assert real_run is not None
    with patch("bmad_assist_lite.core.gate_runner.run_command", _tracked):
        run_gates(
            [GateCommand(name=f"G{i}", command="noop") for i in range(count)],
            tmp_path,
            pool_size=pool_size,
            report=False,
        )
    return peak


# ============================================================================
# REQ-02.4 — an env failure never reaches the LLM fixer
# ============================================================================


class TestEnvRouting:
    """Classification downgrades routing, never visibility."""

    def test_env_failure_is_still_a_failure(self, tmp_path: Path) -> None:
        """For every classification, a failing command is FAIL."""
        for command, expected in (
            (_exit_cmd(1), GateClassification.REAL),
            ("definitely-not-a-real-binary-xyz", GateClassification.ENV),
        ):
            run = run_gates(
                [GateCommand(name="Gate", command=command)], tmp_path, report=False
            )
            assert run.all_passed is False
            assert run.failures[0].classification is expected
            assert run.failures[0].status_label == "FAIL"

    def test_mixed_run_is_real_overall(self, tmp_path: Path) -> None:
        """Conservative precedence: a real failure must still reach the fixer."""
        run = run_gates(
            [
                GateCommand(name="Lint", command="definitely-not-a-real-binary-xyz"),
                GateCommand(name="Tests", command=_exit_cmd(1)),
            ],
            tmp_path,
            report=False,
        )
        assert run.overall_classification is GateClassification.REAL

    def test_env_only_run_is_env_overall(self, tmp_path: Path) -> None:
        run = run_gates(
            [GateCommand(name="Lint", command="definitely-not-a-real-binary-xyz")],
            tmp_path,
            report=False,
        )
        assert run.overall_classification is GateClassification.ENV

    def test_bootstrap_and_retry_happens_exactly_once(self, tmp_path: Path) -> None:
        calls: list[bool] = []

        def _hook(force: bool) -> BootstrapResult:
            calls.append(force)
            return BootstrapResult(success=True)

        run = run_gates(
            [GateCommand(name="Lint", command="definitely-not-a-real-binary-xyz")],
            tmp_path,
            bootstrap=_hook,
            report=False,
        )
        assert calls == [True]
        assert run.env_blocked is True
        assert run.all_passed is False

    def test_env_blocked_is_a_named_terminal_state(self, tmp_path: Path) -> None:
        run = run_gates(
            [GateCommand(name="Lint", command="definitely-not-a-real-binary-xyz")],
            tmp_path,
            bootstrap=lambda force: BootstrapResult(success=True),
            report=False,
        )
        assert run.classification_label(run.failures[0]) == ENV_BLOCKED_LABEL

    def test_bootstrap_success_can_clear_the_env_failure(self, tmp_path: Path) -> None:
        state = {"bootstrapped": False}
        real_run = __import__(
            "bmad_assist_lite.core.gate_runner", fromlist=["run_command"]
        ).run_command

        def _fake(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
            if state["bootstrapped"]:
                return CommandResult(command, 0, "", "", 1)
            return CommandResult(command, 127, "", "not found", 1)

        def _hook(force: bool) -> BootstrapResult:
            state["bootstrapped"] = True
            return BootstrapResult(success=True)

        assert real_run is not None
        with patch("bmad_assist_lite.core.gate_runner.run_command", _fake):
            run = run_gates(
                [GateCommand(name="Lint", command="ruff check")],
                tmp_path,
                bootstrap=_hook,
                report=False,
            )
        assert run.all_passed is True
        assert run.env_blocked is False
        assert run.outcomes[0].retried_after_bootstrap is True

    def test_real_127_is_not_silently_retried_into_oblivion(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A test suite that exits 127 for a real reason ends in a visible failure.

        The exit code alone never authorises suppression: bootstrap-and-retry is
        bounded at one, and what follows is the named env-blocked terminal state,
        not another retry and not silence.
        """
        attempts: list[str] = []

        def _fake(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
            attempts.append(command)
            return CommandResult(command, 127, "collected 12 items", "", 5)

        hook_calls: list[bool] = []

        def _hook(force: bool) -> BootstrapResult:
            hook_calls.append(force)
            return BootstrapResult(success=True)

        with caplog.at_level(logging.WARNING), patch(
            "bmad_assist_lite.core.gate_runner.run_command", _fake
        ):
            run = run_gates(
                [GateCommand(name="Tests", command="pytest -q")],
                tmp_path,
                bootstrap=_hook,
                report=False,
            )

        assert len(attempts) == 2, "bootstrap-and-retry must be bounded at one"
        assert hook_calls == [True]
        assert run.all_passed is False
        assert run.env_blocked is True
        assert any(ENV_BLOCKED_LABEL in r.getMessage() for r in caplog.records)


# ============================================================================
# A13(a) — the classification the operator actually sees
# ============================================================================


class TestConsoleSummary:
    """The symptom that motivated T13 is a console line, so assert the console."""

    def test_summary_names_gate_classification_and_command(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        env_cmd = "definitely-not-a-real-binary-xyz"
        real_cmd = _exit_cmd(1)
        run_gates(
            [
                GateCommand(name="Lint", command=env_cmd),
                GateCommand(name="Tests", command=real_cmd),
            ],
            tmp_path,
        )
        out = capsys.readouterr().out

        assert "Lint" in out and "Tests" in out
        assert "[env]" in out or f"[{ENV_BLOCKED_LABEL}]" in out
        assert "[real]" in out
        assert env_cmd in out
        assert real_cmd in out

    def test_unknown_never_appears_as_a_gate_outcome(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The direct assertion that "failed gates: unknown" is gone.

        Covers every classification plus REQ-02.6 criterion 2's unmapped exit code.
        """
        run_gates(
            [
                GateCommand(name="Lint", command="definitely-not-a-real-binary-xyz"),
                GateCommand(name="Typecheck", command=_exit_cmd(3)),
                GateCommand(name="Build", command=_exit_cmd(1)),
                GateCommand(name="Tests", command=_exit_cmd(0)),
            ],
            tmp_path,
        )
        out = capsys.readouterr().out
        assert "unknown" not in out.lower()
        assert "Gate summary:" in out

    def test_passing_run_summary_has_no_unknown(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run_gates([GateCommand(name="Lint", command=_exit_cmd(0))], tmp_path)
        out = capsys.readouterr().out
        assert "unknown" not in out.lower()
        assert "Lint: PASS" in out


# ============================================================================
# REQ-02.5 — durations are recorded
# ============================================================================


class TestDurations:
    def test_each_command_carries_a_duration(self, tmp_path: Path) -> None:
        run = run_gates(
            [
                GateCommand(name="Lint", command=_exit_cmd(0)),
                GateCommand(name="Tests", command=_exit_cmd(0)),
            ],
            tmp_path,
            report=False,
        )
        assert all(o.duration_ms >= 0 for o in run.outcomes)
        assert run.duration_ms >= 0

    def test_durations_reach_a_durable_log_line(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="bmad_assist_lite.core.gate_runner"):
            run_gates(
                [
                    GateCommand(name="Lint", command=_exit_cmd(0)),
                    GateCommand(name="Tests", command=_exit_cmd(0)),
                ],
                tmp_path,
                label="story:1.2",
                report=False,
            )
        lines = [
            r.getMessage()
            for r in caplog.records
            if GATE_DURATION_LOG_PREFIX in r.getMessage()
        ]
        assert len(lines) == 2
        assert "gate=Lint" in lines[0]
        assert "label=story:1.2" in lines[0]
        assert "duration_ms=" in lines[0]
        assert "classification=" in lines[0]


# ============================================================================
# REQ-02.7 — the base repo is bootstrapped before a post-merge gate
# ============================================================================


class TestBaseBootstrap:
    def test_no_bootstrap_hook_without_configuration(self, tmp_path: Path) -> None:
        """A post-merge gate never becomes an implicit dependency install."""
        config = MagicMock()
        config.parallel.setup_commands = []
        config.parallel.copy_to_worktree = []
        assert make_base_bootstrap(tmp_path, config) is None

    def test_hook_runs_the_existing_pipeline_against_the_base_repo(
        self, tmp_path: Path
    ) -> None:
        config = MagicMock()
        config.parallel.setup_commands = ["pip install -e ."]
        config.parallel.copy_to_worktree = []
        config.parallel.model_copy.return_value = "PARALLEL_CONFIG"

        hook = make_base_bootstrap(tmp_path, config)
        assert hook is not None
        with patch(
            "bmad_assist_lite.parallel.bootstrap.bootstrap_worktree"
        ) as mock_bootstrap:
            mock_bootstrap.return_value = BootstrapResult(success=True)
            hook(False)

        mock_bootstrap.assert_called_once_with(
            project_root=tmp_path,
            worktree_path=tmp_path,
            config="PARALLEL_CONFIG",
            validate=False,
        )
        # Validation is skipped: quality_gate is the sole authoritative suite run.
        config.parallel.model_copy.assert_called_once_with(
            update={"validation_command": None}
        )

    def test_base_repo_is_not_bootstrapped_twice(self, tmp_path: Path) -> None:
        config = MagicMock()
        config.parallel.setup_commands = ["pip install -e ."]
        config.parallel.copy_to_worktree = []
        hook = make_base_bootstrap(tmp_path, config)
        assert hook is not None

        with patch(
            "bmad_assist_lite.parallel.bootstrap.bootstrap_worktree"
        ) as mock_bootstrap:
            mock_bootstrap.return_value = BootstrapResult(success=True)
            hook(False)
            hook(False)
        assert mock_bootstrap.call_count == 1

    def test_bootstrap_first_runs_before_any_gate_command(self, tmp_path: Path) -> None:
        order: list[str] = []

        def _hook(force: bool) -> BootstrapResult:
            order.append("bootstrap")
            return BootstrapResult(success=True)

        def _fake(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
            order.append("gate")
            return CommandResult(command, 0, "", "", 1)

        with patch("bmad_assist_lite.core.gate_runner.run_command", _fake):
            run_gates(
                [GateCommand(name="Tests", command="pytest")],
                tmp_path,
                bootstrap=_hook,
                bootstrap_first=True,
                report=False,
            )
        assert order == ["bootstrap", "gate"]

    def test_bootstrap_failure_is_surfaced_as_env(self, tmp_path: Path) -> None:
        def _hook(force: bool) -> BootstrapResult:
            return BootstrapResult(
                success=False,
                failed_phase="setup",
                error_message="pip install failed",
                classification="env",
            )

        run = run_gates(
            [GateCommand(name="Tests", command=_exit_cmd(0))],
            tmp_path,
            bootstrap=_hook,
            bootstrap_first=True,
            report=False,
        )
        assert run.all_passed is False
        assert run.failures[0].classification is GateClassification.ENV
        assert "Bootstrap" in run.failures[0].name

    def test_bootstrap_validation_failure_is_not_mislabelled_env(
        self, tmp_path: Path
    ) -> None:
        """A validation command runs project code; it can fail for a real reason."""
        def _hook(force: bool) -> BootstrapResult:
            return BootstrapResult(
                success=False,
                failed_phase="validation",
                error_message="pytest failed",
                classification="real",
            )

        run = run_gates(
            [GateCommand(name="Tests", command=_exit_cmd(0))],
            tmp_path,
            bootstrap=_hook,
            bootstrap_first=True,
            report=False,
        )
        assert run.failures[0].classification is GateClassification.REAL

    def test_runner_defines_no_bootstrap_pipeline_of_its_own(self) -> None:
        """REQ-02.7 crit 4: reuse, do not re-implement."""
        source = Path("src/bmad_assist_lite/core/gate_runner.py").read_text(
            encoding="utf-8"
        )
        assert "from bmad_assist_lite.parallel.bootstrap import" in source
        assert "subprocess" not in source


# ============================================================================
# G12 — the runtime invariant beside the static check
# ============================================================================


class TestTreeMutationGuard:
    """Bootstrap-and-retry is tree-mutating but is not a gate command."""

    def test_quiescent_when_nothing_is_running(self) -> None:
        assert active_gate_commands() == 0
        assert_tree_quiescent("base-repo bootstrap")

    def test_guard_raises_while_a_gate_command_is_in_flight(
        self, tmp_path: Path
    ) -> None:
        """Mutation proving RED: dropping the guard lets this run silently."""
        observed: list[Exception] = []

        def _fake(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
            try:
                assert_tree_quiescent("base-repo bootstrap")
            except GateRunnerError as exc:
                observed.append(exc)
            return CommandResult(command, 0, "", "", 1)

        with patch("bmad_assist_lite.core.gate_runner.run_command", _fake):
            run_gates(
                [GateCommand(name="Tests", command="pytest")], tmp_path, report=False
            )
        assert len(observed) == 1

    def test_bootstrap_retry_never_fires_while_gates_run(self, tmp_path: Path) -> None:
        seen: list[int] = []

        def _hook(force: bool) -> BootstrapResult:
            seen.append(active_gate_commands())
            return BootstrapResult(success=True)

        run_gates(
            [
                GateCommand(name="Lint", command="definitely-not-a-real-binary-xyz"),
                GateCommand(name="Typecheck", command=_sleep_cmd(0.3)),
            ],
            tmp_path,
            bootstrap=_hook,
            report=False,
        )
        assert seen == [0]


# ============================================================================
# REQ-02.11 — no flake retry
# ============================================================================


def test_real_failure_is_never_retried(tmp_path: Path) -> None:
    attempts: list[str] = []

    def _fake(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
        attempts.append(command)
        return CommandResult(command, 1, "", "", 1)

    with patch("bmad_assist_lite.core.gate_runner.run_command", _fake):
        run_gates(
            [GateCommand(name="Tests", command="pytest")],
            tmp_path,
            bootstrap=lambda force: BootstrapResult(success=True),
            report=False,
        )
    assert attempts == ["pytest"]


def test_empty_command_set_passes(tmp_path: Path) -> None:
    run = run_gates([], tmp_path, report=False)
    assert run.all_passed is True
    assert run.outcomes == []


# ============================================================================
# REQ-02.4 crit 1/6 — execution-driven, through the real handler
# ============================================================================


def _qg_config(test_cmd: str) -> MagicMock:
    config = MagicMock()
    config.quality_gate.lint = None
    config.quality_gate.typecheck = None
    config.quality_gate.build = None
    config.quality_gate.test = test_cmd
    config.quality_gate.test_unit = None
    config.quality_gate.command_timeout = 30
    config.quality_gate.max_retries = 2
    return config


class TestQualityGateRouting:
    """The classification decides routing — proven through the real handler."""

    def _run(self, tmp_path: Path, exit_code: int) -> object:
        from bmad_assist_lite.core.paths import init_paths
        from bmad_assist_lite.core.state import State
        from bmad_assist_lite.loop.handlers.quality_gate import QualityGateHandler

        init_paths(tmp_path)
        handler = QualityGateHandler(_qg_config("pytest -q"), tmp_path)
        state = State(current_epic="1", current_story="1.2", qa_retry_count=0)

        def _fake(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
            return CommandResult(command, exit_code, "", "boom", 5)

        with patch("bmad_assist_lite.core.gate_runner.run_command", _fake):
            return handler.execute(state)

    def test_env_failure_does_not_route_to_the_fixer(self, tmp_path: Path) -> None:
        """127 is an env failure: no next_phase, so qa_retry_count cannot change.

        `loop/runner.py` increments `qa_retry_count` only when `next_phase` is
        `FIX_QUALITY_GATE`, so `next_phase is None` is what makes the counter
        unchanged — asserted here on the real handler, not on a constructed branch.
        """
        result = self._run(tmp_path, exit_code=127)
        assert result.next_phase is None
        assert result.outputs["quality_gate_action"] == "env_blocked"
        assert result.outputs["quality_gate_classification"] == "env"

    def test_real_failure_does_route_to_the_fixer(self, tmp_path: Path) -> None:
        from bmad_assist_lite.core.state import Phase

        result = self._run(tmp_path, exit_code=1)
        assert result.next_phase == Phase.FIX_QUALITY_GATE
        assert result.outputs["quality_gate_action"] == "fix"
        assert result.outputs["quality_gate_classification"] == "real"

    def test_routing_an_env_run_to_the_fixer_is_a_runtime_error(self) -> None:
        """The recorded mutation: make the handler route env, and this fires.

        A static reachability check reports "unreachable" the moment the phase name
        is computed in a helper. This assertion holds at the transition point.
        """
        from bmad_assist_lite.core.gate_runner import GateOutcome, GateRunResult
        from bmad_assist_lite.loop.handlers.quality_gate import QualityGateHandler

        env_run = GateRunResult(
            all_passed=False,
            outcomes=[
                GateOutcome(
                    name="Lint",
                    command="ruff check",
                    passed=False,
                    exit_code=127,
                    stdout="",
                    stderr="",
                    duration_ms=1,
                    classification=GateClassification.ENV,
                )
            ],
            duration_ms=1,
            pool_size=4,
        )
        with pytest.raises(GateRunnerError, match="never route to fix_quality_gate"):
            QualityGateHandler._assert_routable_to_fixer(env_run)

    def test_env_blocked_is_visible_on_the_console(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._run(tmp_path, exit_code=127)
        out = capsys.readouterr().out
        assert "env-blocked" in out
        assert "pytest -q" in out
        assert "unknown" not in out.lower()


# ============================================================================
# REQ-02.7 crit 1 — post-merge gates bootstrap the merge target first
# ============================================================================


class TestPostMergeBaseBootstrap:
    def _config(self) -> MagicMock:
        config = _qg_config("pytest -q")
        config.parallel.setup_commands = ["pip install -e ."]
        config.parallel.copy_to_worktree = []
        return config

    def test_post_merge_gate_bootstraps_the_base_repo_before_gates(
        self, tmp_path: Path
    ) -> None:
        from bmad_assist_lite.parallel.merger import run_post_merge_qg

        order: list[str] = []

        def _fake(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
            order.append(f"gate:{command}")
            return CommandResult(command, 0, "", "", 1)

        def _fake_bootstrap(**kwargs: object) -> BootstrapResult:
            order.append(f"bootstrap:{kwargs['worktree_path']}")
            return BootstrapResult(success=True)

        with patch("bmad_assist_lite.core.gate_runner.run_command", _fake), patch(
            "bmad_assist_lite.parallel.bootstrap.bootstrap_worktree", _fake_bootstrap
        ):
            result = run_post_merge_qg("4.4", tmp_path, self._config())

        assert order[0] == f"bootstrap:{tmp_path}"
        assert any(o.startswith("gate:") for o in order[1:])
        assert result.all_passed is True

    def test_post_merge_gate_results_carry_a_classification(
        self, tmp_path: Path
    ) -> None:
        from bmad_assist_lite.parallel.merger import run_post_merge_qg

        def _fake(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
            return CommandResult(command, 127, "", "not found", 1)

        with patch("bmad_assist_lite.core.gate_runner.run_command", _fake), patch(
            "bmad_assist_lite.parallel.bootstrap.bootstrap_worktree",
            lambda **kw: BootstrapResult(success=True),
        ):
            result = run_post_merge_qg("4.4", tmp_path, self._config())

        assert result.all_passed is False
        assert result.env_blocked is True
        assert result.classification == "env"
        assert result.gate_results[0].classification == ENV_BLOCKED_LABEL
        assert result.gate_results[0].status_label == "FAIL"

    def test_env_blocked_post_merge_is_not_routed_to_the_fixer(self) -> None:
        from bmad_assist_lite.parallel.merger import (
            GateResult,
            PostMergeQGResult,
            _assert_fix_routable,
        )

        env_result = PostMergeQGResult(
            all_passed=False,
            story_id="4.4",
            classification="env",
            env_blocked=True,
            gate_results=[
                GateResult(
                    name="Tests",
                    command="pytest -q",
                    passed=False,
                    exit_code=127,
                    stdout="",
                    stderr="",
                    duration_ms=1,
                    classification=ENV_BLOCKED_LABEL,
                )
            ],
        )
        assert _assert_fix_routable(env_result) is False

        real_result = env_result.model_copy(
            update={"classification": "real", "env_blocked": False}
        )
        assert _assert_fix_routable(real_result) is True

    def test_failed_post_merge_run_always_has_a_reason(self) -> None:
        """No PostMergeQGResult may fail with nothing to report — that was `unknown`."""
        from bmad_assist_lite.parallel.merger import PostMergeQGResult

        result = PostMergeQGResult(
            all_passed=False, story_id="4.4", failure_reason="fix subprocess timed out"
        )
        assert result.failure_reason


def test_base_bootstrap_hook_refuses_to_run_while_a_gate_is_in_flight(
    tmp_path: Path,
) -> None:
    """Binds the guard's CALL SITE, not just the guard function.

    Recorded mutation: delete `assert_tree_quiescent(...)` from the hook and this
    test goes red — the bootstrap silently mutates the tree under a running gate.
    """
    config = MagicMock()
    config.parallel.setup_commands = ["pip install -e ."]
    config.parallel.copy_to_worktree = []
    hook = make_base_bootstrap(tmp_path, config)
    assert hook is not None

    raised: list[Exception] = []

    def _fake(command: str, cwd: Path, timeout: int = 120) -> CommandResult:
        try:
            hook(True)
        except GateRunnerError as exc:
            raised.append(exc)
        return CommandResult(command, 0, "", "", 1)

    with patch("bmad_assist_lite.core.gate_runner.run_command", _fake), patch(
        "bmad_assist_lite.parallel.bootstrap.bootstrap_worktree",
        lambda **kw: BootstrapResult(success=True),
    ):
        run_gates([GateCommand(name="Tests", command="pytest")], tmp_path, report=False)

    assert len(raised) == 1


def test_env_blocked_loop_branch_does_not_touch_qa_retry_count() -> None:
    """Static half of the G12 pairing for REQ-02.4 crit 1.

    Its execution-driven partner is
    `TestQualityGateRouting::test_env_failure_does_not_route_to_the_fixer`, which
    proves on the real handler that `next_phase is None` — and `next_phase ==
    FIX_QUALITY_GATE` is the sole site that increments the counter.
    """
    import ast

    source = Path("src/bmad_assist_lite/loop/runner.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    branches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and "env_blocked" in ast.unparse(node.test)
    ]
    assert branches, "the env_blocked action branch is missing from the loop"
    for branch in branches:
        body = ast.unparse(ast.Module(body=branch.body, type_ignores=[]))
        assert "qa_retry_count" not in body

    increments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AugAssign) and "qa_retry_count" in ast.unparse(node)
    ]
    assert len(increments) == 1, "qa_retry_count must have exactly one increment site"
