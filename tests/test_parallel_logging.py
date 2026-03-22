"""Tests for parallel/logging.py — orchestrator log file functionality."""

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bmad_assist_lite.parallel.logging import (
    _LOGGER_NAME,
    _LOG_FILENAME,
    _TRUNCATION_LIMIT,
    _TRUNCATION_MARKER,
    _truncate_output,
    log_dependency_unlocked,
    log_merge_queued,
    log_merge_result,
    log_qg_result,
    log_run_complete,
    log_run_header,
    log_story_blocked,
    log_story_completed,
    log_story_started,
    setup_parallel_log,
    teardown_parallel_log,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def log_dir(tmp_path: Path):
    """Provide a temporary directory for log file creation with cleanup."""
    yield tmp_path
    # Guarantee teardown even if tests fail
    teardown_parallel_log()


@pytest.fixture()
def setup_log(log_dir: Path):
    """Set up the parallel log in the temporary directory."""
    setup_parallel_log(log_dir)
    yield log_dir
    # teardown handled by log_dir fixture


def _read_log(project_root: Path) -> str:
    """Read the parallel-run.log file content."""
    log_path = project_root / _LOG_FILENAME
    if log_path.exists():
        return log_path.read_text(encoding="utf-8")
    return ""


# ============================================================================
# Test: stdlib shadow — import resolves to stdlib, not self
# ============================================================================


class TestStdlibShadow:
    def test_logging_import_resolves_to_stdlib(self) -> None:
        """Verify that import logging as _logging resolves to stdlib."""
        from bmad_assist_lite.parallel import logging as parallel_logging_module

        # The _logging attribute inside the module should be stdlib logging
        assert hasattr(parallel_logging_module, "_logging")
        # stdlib logging has getLogger, FileHandler, Formatter
        _logging_ref = parallel_logging_module._logging
        assert hasattr(_logging_ref, "getLogger")
        assert hasattr(_logging_ref, "FileHandler")
        assert hasattr(_logging_ref, "Formatter")
        # It should NOT be the parallel logging module itself
        assert _logging_ref is not parallel_logging_module


# ============================================================================
# Test: setup_parallel_log / teardown_parallel_log
# ============================================================================


class TestSetupTeardown:
    def test_setup_creates_file_handler(self, log_dir: Path) -> None:
        """Task 5.1: setup_parallel_log creates FileHandler writing to parallel-run.log."""
        setup_parallel_log(log_dir)

        logger = logging.getLogger(_LOGGER_NAME)
        file_handlers = [
            h for h in logger.handlers if isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) == 1

        fh = file_handlers[0]
        assert Path(fh.baseFilename) == log_dir / _LOG_FILENAME
        assert fh.mode == "a"
        assert fh.encoding == "utf-8"

    def test_setup_idempotent(self, log_dir: Path) -> None:
        """Task 5.8: calling setup twice doesn't add duplicate handlers."""
        setup_parallel_log(log_dir)
        setup_parallel_log(log_dir)

        logger = logging.getLogger(_LOGGER_NAME)
        file_handlers = [
            h for h in logger.handlers if isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) == 1

    def test_teardown_removes_handler(self, log_dir: Path) -> None:
        """Task 5.2: teardown_parallel_log removes FileHandler and closes file."""
        setup_parallel_log(log_dir)
        teardown_parallel_log()

        logger = logging.getLogger(_LOGGER_NAME)
        file_handlers = [
            h for h in logger.handlers if isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) == 0

    def test_teardown_without_setup_is_safe(self) -> None:
        """teardown_parallel_log is safe to call without prior setup."""
        # Should not raise
        teardown_parallel_log()

    def test_teardown_closes_file_handle(self, log_dir: Path) -> None:
        """After teardown, the file handle is closed (no ResourceWarning)."""
        setup_parallel_log(log_dir)

        from bmad_assist_lite.parallel.logging import _file_handler

        # The handler should exist before teardown
        assert _file_handler is not None
        fh = _file_handler
        # Capture the underlying stream before close() sets it to None
        stream = fh.stream

        teardown_parallel_log()

        # After teardown, the stream should be closed
        assert stream.closed

    def test_setup_after_teardown_works(self, log_dir: Path) -> None:
        """Can re-setup after teardown."""
        setup_parallel_log(log_dir)
        teardown_parallel_log()
        setup_parallel_log(log_dir)

        logger = logging.getLogger(_LOGGER_NAME)
        file_handlers = [
            h for h in logger.handlers if isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) == 1

    def test_log_file_is_append_mode(self, log_dir: Path) -> None:
        """Log file uses append mode so consecutive runs build a continuous log."""
        # Write initial content
        log_path = log_dir / _LOG_FILENAME
        log_path.write_text("EXISTING CONTENT\n", encoding="utf-8")

        setup_parallel_log(log_dir)
        log_run_header("main", 6, 3, 5)
        teardown_parallel_log()

        content = log_path.read_text(encoding="utf-8")
        assert content.startswith("EXISTING CONTENT\n")
        assert "[ORCHESTRATOR]" in content


# ============================================================================
# Test: log_run_header
# ============================================================================


class TestLogRunHeader:
    def test_header_contains_all_fields(self, setup_log: Path) -> None:
        """Task 5.3: log_run_header writes correct header fields."""
        log_run_header("main", 6, 3, 5)

        content = _read_log(setup_log)
        assert "base_branch=main" in content
        assert "epic=6" in content
        assert "max_concurrency=3" in content
        assert "stories=5" in content

    def test_header_contains_orchestrator_prefix(self, setup_log: Path) -> None:
        """Header messages use [ORCHESTRATOR] prefix."""
        log_run_header("develop", 4, 2, 8)

        content = _read_log(setup_log)
        assert "[ORCHESTRATOR]" in content

    def test_header_contains_separator(self, setup_log: Path) -> None:
        """Header is delimited by separator lines."""
        log_run_header("main", 6, 3, 5)

        content = _read_log(setup_log)
        assert "=" * 80 in content

    def test_header_contains_timestamp(self, setup_log: Path) -> None:
        """Header lines include timestamps from the formatter."""
        log_run_header("main", 6, 3, 5)

        content = _read_log(setup_log)
        # Format: [YYYY-MM-DD HH:MM:SS]
        import re

        assert re.search(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", content)


# ============================================================================
# Test: log_run_complete
# ============================================================================


class TestLogRunComplete:
    def test_run_complete_writes_counts(self, setup_log: Path) -> None:
        """Task 5.7: log_run_complete writes run-end delimiter with story counts."""
        log_run_complete(total_stories=10, completed=7, blocked=2, failed=1)

        content = _read_log(setup_log)
        assert "total=10" in content
        assert "completed=7" in content
        assert "blocked=2" in content
        assert "failed=1" in content

    def test_run_complete_contains_separator(self, setup_log: Path) -> None:
        """Run-end footer includes separator for readability."""
        log_run_complete(total_stories=5, completed=5, blocked=0, failed=0)

        content = _read_log(setup_log)
        assert "=" * 80 in content
        assert "Parallel run complete" in content

    def test_run_complete_visible_between_runs(self, setup_log: Path) -> None:
        """Run delimiters are visible between consecutive append-mode runs."""
        log_run_header("main", 6, 3, 5)
        log_run_complete(total_stories=5, completed=5, blocked=0, failed=0)
        log_run_header("main", 6, 3, 3)
        log_run_complete(total_stories=3, completed=3, blocked=0, failed=0)

        content = _read_log(setup_log)
        # Should contain two "Parallel run started" and two "Parallel run complete"
        assert content.count("Parallel run started") == 2
        assert content.count("Parallel run complete") == 2


# ============================================================================
# Test: Event helpers — level and content
# ============================================================================


class TestLogStoryStarted:
    def test_story_started_info_level(self, setup_log: Path) -> None:
        """Task 5.4: log_story_started writes INFO level."""
        log_story_started("3.2", Path("/worktrees/story-3.2"))

        content = _read_log(setup_log)
        assert "[INFO]" in content
        assert "Story 3.2 started" in content
        assert "story-3.2" in content


class TestLogStoryCompleted:
    def test_story_completed_success_info(self, setup_log: Path) -> None:
        """Task 5.4: successful completion at INFO level."""
        log_story_completed("3.2", 0)

        content = _read_log(setup_log)
        assert "[INFO]" in content
        assert "Story 3.2 completed successfully" in content

    def test_story_completed_failure_warning(self, setup_log: Path) -> None:
        """Task 5.4: non-zero exit code at WARNING level."""
        log_story_completed("3.2", 1)

        content = _read_log(setup_log)
        assert "[WARNING]" in content
        assert "Story 3.2 failed" in content
        assert "exit_code=1" in content

    def test_story_completed_killed_error(self, setup_log: Path) -> None:
        """Task 5.4: negative exit code (killed) at ERROR level."""
        log_story_completed("3.2", -1)

        content = _read_log(setup_log)
        assert "[ERROR]" in content
        assert "Story 3.2 terminated" in content
        assert "exit_code=-1" in content


class TestLogMergeQueued:
    def test_merge_queued_info(self, setup_log: Path) -> None:
        """Task 5.4: log_merge_queued at INFO level."""
        log_merge_queued("3.2")

        content = _read_log(setup_log)
        assert "[INFO]" in content
        assert "Story 3.2 queued for merge" in content


class TestLogMergeResult:
    def test_merge_result_success_info(self, setup_log: Path) -> None:
        """Task 5.4: successful merge at INFO level."""
        log_merge_result("3.2", success=True, error=None)

        content = _read_log(setup_log)
        assert "[INFO]" in content
        assert "[MERGE|3.2] Merge succeeded" in content

    def test_merge_result_failure_error(self, setup_log: Path) -> None:
        """Task 5.4: failed merge at ERROR level."""
        log_merge_result("3.2", success=False, error="Conflict in src/foo.py")

        content = _read_log(setup_log)
        assert "[ERROR]" in content
        assert "[MERGE|3.2] Merge failed" in content
        assert "Conflict in src/foo.py" in content

    def test_merge_result_failure_no_error_message(self, setup_log: Path) -> None:
        """Merge failure with None error shows 'unknown error'."""
        log_merge_result("3.2", success=False, error=None)

        content = _read_log(setup_log)
        assert "unknown error" in content


class TestLogStoryBlocked:
    def test_story_blocked_warning(self, setup_log: Path) -> None:
        """Task 5.4: log_story_blocked at WARNING level."""
        log_story_blocked("3.2", "Exit code 1")

        content = _read_log(setup_log)
        assert "[WARNING]" in content
        assert "Story 3.2 blocked" in content
        assert "Exit code 1" in content


class TestLogDependencyUnlocked:
    def test_dependency_unlocked_info(self, setup_log: Path) -> None:
        """Task 5.4: log_dependency_unlocked at INFO level."""
        log_dependency_unlocked("3.3", "3.2")

        content = _read_log(setup_log)
        assert "[INFO]" in content
        assert "Story 3.3 dependency unlocked by 3.2" in content


# ============================================================================
# Test: log_qg_result — pass and fail with per-gate detail
# ============================================================================


class TestLogQgResult:
    def test_qg_result_all_passed(self, setup_log: Path) -> None:
        """Task 5.4: all gates passed at INFO level."""
        gate1 = MagicMock(name="Lint", passed=True)
        gate1.name = "Lint"
        log_qg_result("3.2", all_passed=True, gate_results=[gate1])

        content = _read_log(setup_log)
        assert "[INFO]" in content
        assert "All quality gates passed" in content

    def test_qg_result_failure_with_detail(self, setup_log: Path) -> None:
        """Task 5.5: failed QG includes per-gate failure detail."""
        gate1 = MagicMock()
        gate1.name = "Lint"
        gate1.passed = True
        gate1.command = "ruff check ."
        gate1.exit_code = 0
        gate1.stdout = ""
        gate1.stderr = ""

        gate2 = MagicMock()
        gate2.name = "Tests"
        gate2.passed = False
        gate2.command = "pytest"
        gate2.exit_code = 1
        gate2.stdout = "FAILED test_foo.py::test_bar"
        gate2.stderr = "AssertionError: 1 != 2"

        log_qg_result("3.2", all_passed=False, gate_results=[gate1, gate2])

        content = _read_log(setup_log)
        assert "[ERROR]" in content
        assert "Quality gate FAILED" in content
        assert "Tests" in content
        assert "pytest" in content
        assert "exit_code=1" in content
        assert "FAILED test_foo.py::test_bar" in content
        assert "AssertionError: 1 != 2" in content
        # Lint should NOT appear in failed gates detail
        assert "Gate: Lint" not in content

    def test_qg_result_truncates_long_stdout(self, setup_log: Path) -> None:
        """Task 5.6: stdout exceeding 2000 chars is truncated with marker."""
        long_output = "x" * 3000
        gate = MagicMock()
        gate.name = "Tests"
        gate.passed = False
        gate.command = "pytest"
        gate.exit_code = 1
        gate.stdout = long_output
        gate.stderr = ""

        log_qg_result("3.2", all_passed=False, gate_results=[gate])

        content = _read_log(setup_log)
        assert "[truncated]" in content
        # The truncated output should contain the last 2000 chars
        assert "x" * 100 in content

    def test_qg_result_truncates_long_stderr(self, setup_log: Path) -> None:
        """Task 5.6: stderr exceeding 2000 chars is truncated with marker."""
        long_stderr = "e" * 3000
        gate = MagicMock()
        gate.name = "Tests"
        gate.passed = False
        gate.command = "pytest"
        gate.exit_code = 1
        gate.stdout = ""
        gate.stderr = long_stderr

        log_qg_result("3.2", all_passed=False, gate_results=[gate])

        content = _read_log(setup_log)
        assert "[truncated]" in content

    def test_qg_result_empty_gate_results(self, setup_log: Path) -> None:
        """Edge case: empty gate results list."""
        log_qg_result("3.2", all_passed=False, gate_results=[])

        content = _read_log(setup_log)
        assert "[ERROR]" in content
        assert "Quality gate FAILED" in content

    def test_qg_result_no_truncation_under_limit(self, setup_log: Path) -> None:
        """stdout/stderr under 2000 chars are not truncated."""
        gate = MagicMock()
        gate.name = "Lint"
        gate.passed = False
        gate.command = "ruff check ."
        gate.exit_code = 1
        gate.stdout = "short output"
        gate.stderr = ""

        log_qg_result("3.2", all_passed=False, gate_results=[gate])

        content = _read_log(setup_log)
        assert "[truncated]" not in content
        assert "short output" in content

    def test_qg_result_none_stdout_stderr(self, setup_log: Path) -> None:
        """Edge case: None stdout/stderr fields."""
        gate = MagicMock()
        gate.name = "Tests"
        gate.passed = False
        gate.command = "pytest"
        gate.exit_code = 1
        gate.stdout = None
        gate.stderr = None

        log_qg_result("3.2", all_passed=False, gate_results=[gate])

        content = _read_log(setup_log)
        assert "[ERROR]" in content
        assert "Gate: Tests" in content


# ============================================================================
# Test: _truncate_output helper
# ============================================================================


class TestTruncateOutput:
    def test_short_text_unchanged(self) -> None:
        """Text under the limit is returned unchanged."""
        text = "short"
        assert _truncate_output(text) == "short"

    def test_exact_limit_unchanged(self) -> None:
        """Text at exactly the limit is returned unchanged."""
        text = "x" * _TRUNCATION_LIMIT
        assert _truncate_output(text) == text

    def test_over_limit_truncated(self) -> None:
        """Text over the limit is truncated to last N chars with marker."""
        text = "A" * 500 + "B" * 2500
        result = _truncate_output(text)
        assert result.startswith(_TRUNCATION_MARKER)
        # The content after the marker should be the last 2000 chars
        content_after_marker = result[len(_TRUNCATION_MARKER):]
        assert len(content_after_marker) == _TRUNCATION_LIMIT
        assert content_after_marker == text[-_TRUNCATION_LIMIT:]

    def test_empty_string(self) -> None:
        """Empty string is returned unchanged."""
        assert _truncate_output("") == ""


# ============================================================================
# Test: Log format correctness
# ============================================================================


class TestLogFormat:
    def test_format_has_timestamp_and_level(self, setup_log: Path) -> None:
        """Log format includes [timestamp] [LEVEL] but not logger name."""
        log_story_started("3.2", Path("/w/s"))

        content = _read_log(setup_log)
        lines = [ln for ln in content.strip().splitlines() if "Story 3.2" in ln]
        assert len(lines) >= 1
        line = lines[0]
        # Should have [YYYY-MM-DD HH:MM:SS] [INFO]
        import re

        assert re.match(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[INFO\]", line)
        # Should NOT contain the Python logger namespace
        assert "bmad_assist_lite.parallel" not in line
