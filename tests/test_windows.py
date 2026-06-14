"""Tests for SIGTERM->SIGKILL escalation in Unix process termination (Story 11.1).

Covers all acceptance criteria:
- AC1: Grace-period exit — process exits within grace period after SIGTERM,
       returns True, SIGKILL never sent
- AC2: SIGKILL escalation — process ignores SIGTERM, SIGKILL sent after
       SIGTERM_GRACE_SECONDS, returns True
- AC3: Already-dead PID — ProcessLookupError on initial SIGTERM, returns False
- AC4: Mid-escalation death — process dies between SIGTERM and SIGKILL check,
       ProcessLookupError caught, returns True
- AC5: Windows unchanged — taskkill /F /T /PID path behaves identically,
       no escalation logic

Test numbering per story tasks:
- Task 1: SIGTERM_GRACE_SECONDS constant
- Task 2: SIGKILL escalation in Unix branch
- Task 3: Preserve existing behaviors
- Task 4: Comprehensive escalation and regression tests

All tests patch IS_WINDOWS and mock relevant OS/time functions as needed
so they run on any platform including Windows.
"""

import signal
from unittest.mock import MagicMock, call, patch

from bmad_assist_lite.providers._windows import (
    SIGTERM_GRACE_SECONDS,
    _SIGKILL,
    is_pid_alive,
    terminate_process,
)

# Base path for patching module internals
_MOD = "bmad_assist_lite.providers._windows"


# ============================================================================
# Task 1: SIGTERM_GRACE_SECONDS constant (AC: #1, #2)
# ============================================================================


class TestSigtermGraceSecondsConstant:
    """Verify the SIGTERM_GRACE_SECONDS constant exists and has the correct value."""

    def test_constant_value_is_five(self) -> None:
        """SIGTERM_GRACE_SECONDS must be 5 per story spec."""
        assert SIGTERM_GRACE_SECONDS == 5

    def test_constant_is_int(self) -> None:
        """SIGTERM_GRACE_SECONDS must be an integer type."""
        assert isinstance(SIGTERM_GRACE_SECONDS, int)


# ============================================================================
# Task 2: SIGKILL escalation in Unix branch (AC: #1, #2, #4)
# ============================================================================


class TestGracePeriodExit:
    """AC1: Process exits within grace period after SIGTERM — returns True,
    SIGKILL never sent.
    """

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.os")
    def test_sigterm_sufficient_immediate_death(
        self, mock_os: MagicMock, mock_time: MagicMock
    ) -> None:
        """Process dies on first is_pid_alive poll — no SIGKILL, returns True."""
        mock_os.getpgid.return_value = 1000
        mock_os.killpg.return_value = None
        # is_pid_alive uses os.kill internally, but we patch at module level
        # So we need to patch is_pid_alive directly
        mock_os.kill.side_effect = ProcessLookupError  # pid not alive

        # monotonic: start=0.0, then first check=0.1
        mock_time.monotonic.side_effect = [0.0, 0.1]
        mock_time.sleep.return_value = None

        result = terminate_process(42)

        assert result is True
        # SIGTERM was sent
        mock_os.getpgid.assert_called_once_with(42)
        mock_os.killpg.assert_called_once_with(1000, signal.SIGTERM)

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.os")
    def test_sigterm_sufficient_dies_mid_polling(
        self, mock_os: MagicMock, mock_time: MagicMock
    ) -> None:
        """Process dies during the polling loop (not first poll) — no SIGKILL."""
        mock_os.getpgid.return_value = 1000
        mock_os.killpg.return_value = None
        # First 2 polls: alive (os.kill returns None), 3rd poll: dead
        mock_os.kill.side_effect = [None, None, ProcessLookupError]

        # monotonic: start=0.0, then 0.1, 0.2, 0.3 (all within grace)
        mock_time.monotonic.side_effect = [0.0, 0.1, 0.2, 0.3]
        mock_time.sleep.return_value = None

        result = terminate_process(42)

        assert result is True
        # Only SIGTERM was sent, no SIGKILL
        mock_os.killpg.assert_called_once_with(1000, signal.SIGTERM)


class TestSigkillEscalation:
    """AC2: Process ignores SIGTERM — SIGKILL sent after grace period."""

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.os")
    def test_sigkill_sent_after_grace_period(
        self, mock_os: MagicMock, mock_time: MagicMock
    ) -> None:
        """Process survives entire grace period — SIGKILL is sent, returns True."""
        mock_os.getpgid.return_value = 1000
        mock_os.killpg.return_value = None
        # Process always alive (os.kill succeeds = process exists)
        mock_os.kill.return_value = None

        # monotonic: start=0.0, then polls at 0.1, 0.2, ... until > 5.0
        # We need enough values to exceed SIGTERM_GRACE_SECONDS
        monotonic_values = [0.0]  # start
        t = 0.1
        while t <= SIGTERM_GRACE_SECONDS:
            monotonic_values.append(t)
            t += 0.1
        # Final value exceeds grace period
        monotonic_values.append(SIGTERM_GRACE_SECONDS + 0.1)
        mock_time.monotonic.side_effect = monotonic_values
        mock_time.sleep.return_value = None

        result = terminate_process(42)

        assert result is True
        # Both SIGTERM and SIGKILL were sent
        killpg_calls = mock_os.killpg.call_args_list
        assert len(killpg_calls) == 2
        assert killpg_calls[0] == call(1000, signal.SIGTERM)
        assert killpg_calls[1] == call(1000, _SIGKILL)

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.os")
    def test_sleep_called_with_point_one(
        self, mock_os: MagicMock, mock_time: MagicMock
    ) -> None:
        """Polling loop uses 0.1s sleep intervals, not busy-waiting."""
        mock_os.getpgid.return_value = 1000
        mock_os.killpg.return_value = None
        # Die on second poll
        mock_os.kill.side_effect = [None, ProcessLookupError]

        mock_time.monotonic.side_effect = [0.0, 0.1, 0.2]
        mock_time.sleep.return_value = None

        terminate_process(42)

        # sleep(0.1) was called at least once
        mock_time.sleep.assert_called_with(0.1)


class TestMidEscalationDeath:
    """AC4: Process dies between SIGTERM and SIGKILL — treated as success."""

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.os")
    def test_process_lookup_error_on_sigkill(
        self, mock_os: MagicMock, mock_time: MagicMock
    ) -> None:
        """ProcessLookupError on SIGKILL call — returns True (success)."""
        mock_os.getpgid.return_value = 1000
        # First killpg (SIGTERM) succeeds, second (SIGKILL) raises ProcessLookupError
        mock_os.killpg.side_effect = [None, ProcessLookupError]
        # Process alive through entire grace period
        mock_os.kill.return_value = None

        # Fast-forward through grace period
        mock_time.monotonic.side_effect = [0.0, SIGTERM_GRACE_SECONDS + 0.1]
        mock_time.sleep.return_value = None

        result = terminate_process(42)

        assert result is True

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.os")
    def test_process_dies_during_polling_is_success(
        self, mock_os: MagicMock, mock_time: MagicMock
    ) -> None:
        """Process dies mid-poll (ProcessLookupError on second is_pid_alive check)."""
        mock_os.getpgid.return_value = 1000
        mock_os.killpg.return_value = None
        # First poll: alive, second poll: dead
        mock_os.kill.side_effect = [None, ProcessLookupError]

        mock_time.monotonic.side_effect = [0.0, 0.1, 0.2]
        mock_time.sleep.return_value = None

        result = terminate_process(42)

        assert result is True
        # Only SIGTERM sent, no SIGKILL
        mock_os.killpg.assert_called_once_with(1000, signal.SIGTERM)


# ============================================================================
# Task 3: Preserve existing behaviors (AC: #3, #5)
# ============================================================================


class TestAlreadyDeadPid:
    """AC3: PID does not exist before signaling — returns False."""

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.os")
    def test_process_lookup_error_on_getpgid(self, mock_os: MagicMock) -> None:
        """ProcessLookupError on os.getpgid — returns False (unchanged behavior)."""
        mock_os.getpgid.side_effect = ProcessLookupError

        result = terminate_process(42)

        assert result is False

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.os")
    def test_process_lookup_error_on_initial_sigterm(
        self, mock_os: MagicMock
    ) -> None:
        """ProcessLookupError on initial SIGTERM killpg — returns False."""
        mock_os.getpgid.return_value = 1000
        mock_os.killpg.side_effect = ProcessLookupError

        result = terminate_process(42)

        assert result is False


class TestWindowsUnchanged:
    """AC5: Windows taskkill path behaves identically — no escalation logic."""

    @patch(f"{_MOD}.IS_WINDOWS", True)
    @patch(f"{_MOD}.subprocess")
    def test_windows_taskkill_success(self, mock_subprocess: MagicMock) -> None:
        """Windows path: taskkill succeeds — returns True."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_subprocess.run.return_value = mock_result

        result = terminate_process(42)

        assert result is True
        mock_subprocess.run.assert_called_once_with(
            ["taskkill", "/F", "/T", "/PID", "42"],
            capture_output=True,
            text=True,
            timeout=10,
        )

    @patch(f"{_MOD}.IS_WINDOWS", True)
    @patch(f"{_MOD}.subprocess")
    def test_windows_taskkill_failure(self, mock_subprocess: MagicMock) -> None:
        """Windows path: taskkill fails — returns False."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = MagicMock()
        mock_result.stderr.strip.return_value = "Access denied"
        mock_subprocess.run.return_value = mock_result

        result = terminate_process(42)

        assert result is False

    @patch(f"{_MOD}.IS_WINDOWS", True)
    @patch(f"{_MOD}.subprocess")
    def test_windows_no_sigkill_logic(self, mock_subprocess: MagicMock) -> None:
        """Windows path does not use os.killpg or signal escalation."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_subprocess.run.return_value = mock_result

        # Patch os to ensure it's never called for kill operations
        with patch(f"{_MOD}.os") as mock_os:
            terminate_process(42)
            mock_os.getpgid.assert_not_called()
            mock_os.killpg.assert_not_called()


# ============================================================================
# Edge cases and general exception handling
# ============================================================================


class TestEdgeCases:
    """Additional edge cases per Testing Requirements."""

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.os")
    def test_immediate_death_after_sigterm_no_sleep(
        self, mock_os: MagicMock, mock_time: MagicMock
    ) -> None:
        """First is_pid_alive poll returns False — loop exits immediately, no sleep called."""
        mock_os.getpgid.return_value = 1000
        mock_os.killpg.return_value = None
        # Process not alive on first check
        mock_os.kill.side_effect = ProcessLookupError

        mock_time.monotonic.side_effect = [0.0, 0.1]
        mock_time.sleep.return_value = None

        result = terminate_process(42)

        assert result is True
        # SIGKILL never sent
        mock_os.killpg.assert_called_once_with(1000, signal.SIGTERM)
        # Key differentiator from test_sigterm_sufficient_immediate_death:
        # verify sleep was never called since process died on first poll
        mock_time.sleep.assert_not_called()

    @patch(f"{_MOD}.logger")
    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.os")
    def test_general_exception_returns_false_with_warning(
        self, mock_os: MagicMock, mock_logger: MagicMock
    ) -> None:
        """General Exception in the outer try/except returns False with warning log."""
        mock_os.getpgid.side_effect = RuntimeError("unexpected error")

        result = terminate_process(42)

        assert result is False
        mock_logger.warning.assert_called_once()
        # Verify warning message includes the PID
        warning_args = mock_logger.warning.call_args
        assert 42 in warning_args[0]

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.os")
    def test_permission_error_on_sigkill_returns_false(
        self, mock_os: MagicMock, mock_time: MagicMock
    ) -> None:
        """PermissionError on SIGKILL falls to outer except — returns False."""
        mock_os.getpgid.return_value = 1000
        # SIGTERM succeeds, SIGKILL raises PermissionError
        mock_os.killpg.side_effect = [None, PermissionError("Operation not permitted")]
        # Process alive through entire grace period
        mock_os.kill.return_value = None

        mock_time.monotonic.side_effect = [0.0, SIGTERM_GRACE_SECONDS + 0.1]
        mock_time.sleep.return_value = None

        result = terminate_process(42)

        assert result is False

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.time")
    @patch(f"{_MOD}.os")
    def test_uses_monotonic_not_iteration_count(
        self, mock_os: MagicMock, mock_time: MagicMock
    ) -> None:
        """Grace period uses wall-clock time (time.monotonic), not iteration count."""
        mock_os.getpgid.return_value = 1000
        mock_os.killpg.return_value = None
        # Process always alive
        mock_os.kill.return_value = None

        # Simulate time jumping past grace period in two monotonic calls
        # (e.g., sleep overshooting)
        mock_time.monotonic.side_effect = [0.0, SIGTERM_GRACE_SECONDS + 1.0]
        mock_time.sleep.return_value = None

        result = terminate_process(42)

        assert result is True
        # SIGKILL should have been sent even with only one iteration
        killpg_calls = mock_os.killpg.call_args_list
        assert len(killpg_calls) == 2
        assert killpg_calls[1] == call(1000, _SIGKILL)


class TestIsPidAlive:
    """Regression tests for is_pid_alive (existing functionality)."""

    def test_negative_pid_returns_false(self) -> None:
        """PID <= 0 always returns False."""
        assert is_pid_alive(0) is False
        assert is_pid_alive(-1) is False

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.os")
    def test_alive_process_returns_true(self, mock_os: MagicMock) -> None:
        """Unix: os.kill(pid, 0) succeeds — process alive."""
        mock_os.kill.return_value = None

        result = is_pid_alive(42)

        assert result is True
        mock_os.kill.assert_called_once_with(42, 0)

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.os")
    def test_dead_process_returns_false(self, mock_os: MagicMock) -> None:
        """Unix: ProcessLookupError — process dead."""
        mock_os.kill.side_effect = ProcessLookupError

        result = is_pid_alive(42)

        assert result is False

    @patch(f"{_MOD}.IS_WINDOWS", False)
    @patch(f"{_MOD}.os")
    def test_permission_error_returns_true(self, mock_os: MagicMock) -> None:
        """Unix: PermissionError — process exists but can't signal."""
        mock_os.kill.side_effect = PermissionError

        result = is_pid_alive(42)

        assert result is True
