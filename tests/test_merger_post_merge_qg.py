"""Tests for post-merge quality gate functionality in merger.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from bmad_assist_lite.core.command_runner import CommandResult
from bmad_assist_lite.core.config import Config
from bmad_assist_lite.core.quality_gates import QualityGateEntry
from bmad_assist_lite.core.toolchain import ToolchainCommands
from bmad_assist_lite.parallel.merger import (
    GateResult,
    MergeQueue,
    MergeResult,
    PostMergeQGResult,
    _resolve_qg_commands,
    _write_post_merge_failure_report,
    run_post_merge_qg,
)

# Minimal config data for constructing Config objects in tests
MINIMAL_CONFIG_DATA = {
    "providers": {
        "master": {
            "provider": "claude",
            "model": "sonnet",
        },
    },
}


def _make_config(
    *,
    lint: str | None = None,
    typecheck: str | None = None,
    build: str | None = None,
    test: str | None = None,
    test_unit: str | None = None,
    command_timeout: int = 120,
) -> Config:
    """Build a Config with the given quality_gate fields."""
    qg_data: dict[str, object] = {"command_timeout": command_timeout}
    if lint is not None:
        qg_data["lint"] = lint
    if typecheck is not None:
        qg_data["typecheck"] = typecheck
    if build is not None:
        qg_data["build"] = build
    if test is not None:
        qg_data["test"] = test
    if test_unit is not None:
        qg_data["test_unit"] = test_unit

    data = {**MINIMAL_CONFIG_DATA, "quality_gate": qg_data}
    return Config.model_validate(data)


def _make_cmd_result(
    command: str = "echo ok",
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    duration_ms: int = 100,
) -> CommandResult:
    """Build a CommandResult for mocking run_command."""
    return CommandResult(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
    )


# ============================================================================
# GateResult Model Tests (Task 6.1)
# ============================================================================


class TestGateResultModel:
    """Test GateResult frozen Pydantic model validation."""

    def test_gate_result_creation(self) -> None:
        """Verify GateResult can be created with all fields."""
        gate = GateResult(
            name="Lint",
            command="ruff check src/",
            passed=True,
            exit_code=0,
            stdout="All good",
            stderr="",
            duration_ms=150,
        )

        assert gate.name == "Lint"
        assert gate.command == "ruff check src/"
        assert gate.passed is True
        assert gate.exit_code == 0
        assert gate.stdout == "All good"
        assert gate.stderr == ""
        assert gate.duration_ms == 150

    def test_gate_result_frozen(self) -> None:
        """Verify GateResult is immutable."""
        gate = GateResult(
            name="Lint",
            command="ruff check",
            passed=True,
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=100,
        )

        with pytest.raises(ValidationError):
            gate.passed = False  # type: ignore[misc]

    def test_gate_result_missing_fields_raises(self) -> None:
        """Verify GateResult requires all fields."""
        with pytest.raises(ValidationError):
            GateResult(name="Lint")  # type: ignore[call-arg]


# ============================================================================
# PostMergeQGResult Model Tests (Task 6.1)
# ============================================================================


class TestPostMergeQGResultModel:
    """Test PostMergeQGResult frozen Pydantic model validation."""

    def test_success_result_creation(self) -> None:
        """Verify PostMergeQGResult can be created for all-pass."""
        result = PostMergeQGResult(
            all_passed=True,
            story_id="3.1",
            gate_results=[],
            duration_ms=0,
        )

        assert result.all_passed is True
        assert result.story_id == "3.1"
        assert result.gate_results == []
        assert result.duration_ms == 0

    def test_failure_result_with_gates(self) -> None:
        """Verify PostMergeQGResult with gate details."""
        gate = GateResult(
            name="Lint",
            command="ruff check",
            passed=False,
            exit_code=1,
            stdout="Error found",
            stderr="",
            duration_ms=200,
        )
        result = PostMergeQGResult(
            all_passed=False,
            story_id="3.2",
            gate_results=[gate],
            duration_ms=200,
        )

        assert result.all_passed is False
        assert len(result.gate_results) == 1
        assert result.gate_results[0].name == "Lint"

    def test_frozen_model_is_immutable(self) -> None:
        """Verify PostMergeQGResult is immutable."""
        result = PostMergeQGResult(
            all_passed=True,
            story_id="3.1",
        )

        with pytest.raises(ValidationError):
            result.all_passed = False  # type: ignore[misc]

    def test_default_gate_results_empty(self) -> None:
        """Verify gate_results defaults to empty list."""
        result = PostMergeQGResult(all_passed=True, story_id="3.1")
        assert result.gate_results == []

    def test_default_duration_ms_zero(self) -> None:
        """Verify duration_ms defaults to 0."""
        result = PostMergeQGResult(all_passed=True, story_id="3.1")
        assert result.duration_ms == 0

    def test_missing_required_fields_raises(self) -> None:
        """Verify PostMergeQGResult requires all_passed and story_id."""
        with pytest.raises(ValidationError):
            PostMergeQGResult()  # type: ignore[call-arg]


# ============================================================================
# _resolve_qg_commands() Tests (Task 6.2-6.4)
# ============================================================================


class TestResolveQGCommands:
    """Test _resolve_qg_commands() helper."""

    def test_config_quality_gate_used(self) -> None:
        """Verify entries built from config quality_gate section."""
        config = _make_config(
            lint="ruff check src/",
            typecheck="mypy src/",
            build="python -m build",
            test="pytest",
        )

        entries = _resolve_qg_commands(Path("/repo"), config)

        assert len(entries) == 4
        assert entries[0].name == "Lint"
        assert entries[0].command == "ruff check src/"
        assert entries[1].name == "Typecheck"
        assert entries[2].name == "Build"
        assert entries[3].name == "Tests"
        assert entries[3].command == "pytest"

    def test_config_test_preferred_over_test_unit(self) -> None:
        """Verify post-merge QG prefers 'test' over 'test_unit'."""
        config = _make_config(
            lint="ruff check",
            test="pytest --all",
            test_unit="pytest tests/unit",
        )

        entries = _resolve_qg_commands(Path("/repo"), config)

        test_entry = [e for e in entries if e.name == "Tests"][0]
        assert test_entry.command == "pytest --all"

    def test_config_test_unit_fallback(self) -> None:
        """Verify test_unit used when test is not set."""
        config = _make_config(
            lint="ruff check",
            test_unit="pytest tests/unit",
        )

        entries = _resolve_qg_commands(Path("/repo"), config)

        test_entry = [e for e in entries if e.name == "Tests"][0]
        assert test_entry.command == "pytest tests/unit"

    @patch("bmad_assist_lite.parallel.merger.detect_toolchain")
    def test_no_config_falls_back_to_toolchain(
        self,
        mock_detect: MagicMock,
    ) -> None:
        """Verify fallback to detect_toolchain() when no config."""
        mock_detect.return_value = ToolchainCommands(
            lint="ruff check src/",
            typecheck="mypy src/",
            test="pytest -q --tb=short --no-header",
        )

        entries = _resolve_qg_commands(Path("/repo"), config=None)

        assert len(entries) == 3
        assert entries[0].name == "Lint"
        assert entries[1].name == "Typecheck"
        assert entries[2].name == "Tests"
        mock_detect.assert_called_once_with(Path("/repo"))

    @patch("bmad_assist_lite.parallel.merger.detect_toolchain")
    def test_no_config_no_toolchain_returns_empty(
        self,
        mock_detect: MagicMock,
    ) -> None:
        """Verify empty list when no config and no toolchain detected."""
        mock_detect.return_value = ToolchainCommands()

        entries = _resolve_qg_commands(Path("/repo"), config=None)

        assert entries == []

    @patch("bmad_assist_lite.parallel.merger.detect_toolchain")
    def test_empty_config_qg_falls_back_to_toolchain(
        self,
        mock_detect: MagicMock,
    ) -> None:
        """Verify config with empty quality_gate falls back to toolchain."""
        config = _make_config()  # No commands set
        mock_detect.return_value = ToolchainCommands(
            lint="ruff check src/",
        )

        entries = _resolve_qg_commands(Path("/repo"), config)

        assert len(entries) == 1
        assert entries[0].name == "Lint"

    def test_config_partial_commands(self) -> None:
        """Verify only set commands produce entries."""
        config = _make_config(lint="ruff check src/", build="python -m build")

        entries = _resolve_qg_commands(Path("/repo"), config)

        assert len(entries) == 2
        names = [e.name for e in entries]
        assert "Lint" in names
        assert "Build" in names
        assert "Typecheck" not in names
        assert "Tests" not in names


# ============================================================================
# run_post_merge_qg() Tests (Task 6.5-6.8)
# ============================================================================


class TestRunPostMergeQG:
    """Test run_post_merge_qg() function."""

    @patch("bmad_assist_lite.core.gate_runner.run_command")
    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_all_gates_pass(
        self,
        mock_resolve: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify all_passed=True when every gate passes."""
        mock_resolve.return_value = [
            QualityGateEntry(name="Lint", command="ruff check", status="PENDING"),
            QualityGateEntry(name="Tests", command="pytest", status="PENDING"),
        ]
        mock_run.side_effect = [
            _make_cmd_result(command="ruff check", exit_code=0, duration_ms=100),
            _make_cmd_result(command="pytest", exit_code=0, duration_ms=200),
        ]

        result = run_post_merge_qg("3.1", Path("/repo"))

        assert result.all_passed is True
        assert result.story_id == "3.1"
        assert len(result.gate_results) == 2
        assert result.gate_results[0].passed is True
        assert result.gate_results[1].passed is True
        assert sum(g.duration_ms for g in result.gate_results) == 300
        assert result.duration_ms >= 0

    @patch("bmad_assist_lite.parallel.merger._write_post_merge_failure_report")
    @patch("bmad_assist_lite.core.gate_runner.run_command")
    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_some_gates_fail(
        self,
        mock_resolve: MagicMock,
        mock_run: MagicMock,
        mock_report: MagicMock,
    ) -> None:
        """Verify all_passed=False with correct gate details on failure."""
        mock_resolve.return_value = [
            QualityGateEntry(name="Lint", command="ruff check", status="PENDING"),
            QualityGateEntry(name="Tests", command="pytest", status="PENDING"),
        ]
        mock_run.side_effect = [
            _make_cmd_result(command="ruff check", exit_code=0, duration_ms=100),
            _make_cmd_result(
                command="pytest",
                exit_code=1,
                stdout="FAILED test_foo.py",
                stderr="1 failed",
                duration_ms=500,
            ),
        ]
        mock_report.return_value = Path("/repo/.bmad-assist-lite/cache/report.md")

        result = run_post_merge_qg("3.1", Path("/repo"))

        assert result.all_passed is False
        assert result.gate_results[0].passed is True
        assert result.gate_results[1].passed is False
        assert result.gate_results[1].exit_code == 1
        assert result.gate_results[1].stdout == "FAILED test_foo.py"
        assert result.gate_results[1].stderr == "1 failed"
        assert sum(g.duration_ms for g in result.gate_results) == 600
        assert result.duration_ms >= 0
        mock_report.assert_called_once()

    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_no_commands_pass_by_default(
        self,
        mock_resolve: MagicMock,
    ) -> None:
        """Verify pass-by-default when no commands found."""
        mock_resolve.return_value = []

        result = run_post_merge_qg("3.1", Path("/repo"))

        assert result.all_passed is True
        assert result.gate_results == []
        assert result.duration_ms == 0

    @patch("bmad_assist_lite.core.gate_runner.run_command")
    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_uses_config_command_timeout(
        self,
        mock_resolve: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify command_timeout sourced from config when available."""
        config = _make_config(lint="ruff check", command_timeout=60)
        mock_resolve.return_value = [
            QualityGateEntry(name="Lint", command="ruff check", status="PENDING"),
        ]
        mock_run.return_value = _make_cmd_result(command="ruff check", duration_ms=50)

        run_post_merge_qg("3.1", Path("/repo"), config=config)

        mock_run.assert_called_once_with("ruff check", Path("/repo"), timeout=60)

    @patch("bmad_assist_lite.core.gate_runner.run_command")
    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_uses_default_command_timeout(
        self,
        mock_resolve: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify default 120s timeout when no config."""
        mock_resolve.return_value = [
            QualityGateEntry(name="Lint", command="ruff check", status="PENDING"),
        ]
        mock_run.return_value = _make_cmd_result(command="ruff check", duration_ms=50)

        run_post_merge_qg("3.1", Path("/repo"), config=None)

        mock_run.assert_called_once_with("ruff check", Path("/repo"), timeout=120)

    @patch("bmad_assist_lite.parallel.merger._write_post_merge_failure_report")
    @patch("bmad_assist_lite.core.gate_runner.run_command")
    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_command_not_found_captured_as_fail(
        self,
        mock_resolve: MagicMock,
        mock_run: MagicMock,
        mock_report: MagicMock,
    ) -> None:
        """Verify run_command exit_code 127 captured as FAIL gate result."""
        mock_resolve.return_value = [
            QualityGateEntry(name="Lint", command="missing_tool", status="PENDING"),
        ]
        mock_run.return_value = _make_cmd_result(
            command="missing_tool",
            exit_code=127,
            stderr="Command not found",
            duration_ms=10,
        )
        mock_report.return_value = Path("/repo/.bmad-assist-lite/cache/report.md")

        result = run_post_merge_qg("3.1", Path("/repo"))

        assert result.all_passed is False
        assert result.gate_results[0].exit_code == 127
        assert result.gate_results[0].passed is False

    @patch("bmad_assist_lite.parallel.merger._write_post_merge_failure_report")
    @patch("bmad_assist_lite.core.gate_runner.run_command")
    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_command_timeout_captured_as_fail(
        self,
        mock_resolve: MagicMock,
        mock_run: MagicMock,
        mock_report: MagicMock,
    ) -> None:
        """Verify run_command exit_code 124 (timeout) captured as FAIL gate result."""
        mock_resolve.return_value = [
            QualityGateEntry(name="Tests", command="pytest", status="PENDING"),
        ]
        mock_run.return_value = _make_cmd_result(
            command="pytest",
            exit_code=124,
            stderr="Command timed out after 120s",
            duration_ms=120000,
        )
        mock_report.return_value = Path("/repo/.bmad-assist-lite/cache/report.md")

        result = run_post_merge_qg("3.1", Path("/repo"))

        assert result.all_passed is False
        assert result.gate_results[0].exit_code == 124
        assert result.gate_results[0].passed is False

    @patch("bmad_assist_lite.parallel.merger._write_post_merge_failure_report")
    @patch("bmad_assist_lite.core.gate_runner.run_command")
    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_failure_report_write_error_is_nonfatal(
        self,
        mock_resolve: MagicMock,
        mock_run: MagicMock,
        mock_report: MagicMock,
    ) -> None:
        """Verify OSError from failure report write does not crash QG."""
        mock_resolve.return_value = [
            QualityGateEntry(name="Lint", command="ruff check", status="PENDING"),
        ]
        mock_run.return_value = _make_cmd_result(
            command="ruff check", exit_code=1, duration_ms=100
        )
        mock_report.side_effect = OSError("disk full")

        result = run_post_merge_qg("3.1", Path("/repo"))

        assert result.all_passed is False
        assert len(result.gate_results) == 1

    @patch("bmad_assist_lite.core.gate_runner.run_command")
    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_log_output_uses_prefix(
        self,
        mock_resolve: MagicMock,
        mock_run: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify log output uses [QG|post-merge|{story_id}] prefix."""
        mock_resolve.return_value = [
            QualityGateEntry(name="Lint", command="ruff check", status="PENDING"),
        ]
        mock_run.return_value = _make_cmd_result(command="ruff check", duration_ms=50)

        with caplog.at_level("INFO"):
            run_post_merge_qg("3.1", Path("/repo"))

        assert any("[QG|post-merge|3.1]" in r.message for r in caplog.records)

    @patch("bmad_assist_lite.parallel.merger._resolve_qg_commands")
    def test_no_commands_log_uses_prefix(
        self,
        mock_resolve: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify no-commands log uses [QG|post-merge|{story_id}] prefix."""
        mock_resolve.return_value = []

        with caplog.at_level("INFO"):
            run_post_merge_qg("4.2", Path("/repo"))

        assert any("[QG|post-merge|4.2]" in r.message for r in caplog.records)


# ============================================================================
# _write_post_merge_failure_report() Tests (Task 6.11)
# ============================================================================


class TestWritePostMergeFailureReport:
    """Test failure report writing."""

    def test_report_written_on_failure(self, tmp_path: Path) -> None:
        """Verify report file is created at correct path with per-gate details."""
        gate = GateResult(
            name="Lint",
            command="ruff check src/",
            passed=False,
            exit_code=1,
            stdout="Error: line too long",
            stderr="",
            duration_ms=200,
        )
        qg_result = PostMergeQGResult(
            all_passed=False,
            story_id="3.1",
            gate_results=[gate],
            duration_ms=200,
        )

        report_path = _write_post_merge_failure_report("3.1", tmp_path, qg_result)

        assert report_path.exists()
        assert report_path.name == "post-merge-qg-failures-3.1.md"
        content = report_path.read_text(encoding="utf-8")
        assert "Post-Merge Quality Gate Failures" in content
        assert "Story 3.1" in content
        assert "Failed: Lint" in content
        assert "`ruff check src/`" in content
        assert "Exit Code:** 1" in content
        assert "Error: line too long" in content

    def test_report_only_includes_failed_gates(self, tmp_path: Path) -> None:
        """Verify report only includes details for failed gates."""
        pass_gate = GateResult(
            name="Lint",
            command="ruff check",
            passed=True,
            exit_code=0,
            stdout="OK",
            stderr="",
            duration_ms=100,
        )
        fail_gate = GateResult(
            name="Tests",
            command="pytest",
            passed=False,
            exit_code=1,
            stdout="1 failed",
            stderr="",
            duration_ms=500,
        )
        qg_result = PostMergeQGResult(
            all_passed=False,
            story_id="3.2",
            gate_results=[pass_gate, fail_gate],
            duration_ms=600,
        )

        report_path = _write_post_merge_failure_report("3.2", tmp_path, qg_result)

        content = report_path.read_text(encoding="utf-8")
        assert "Failed: Tests" in content
        assert "Failed: Lint" not in content

    def test_report_creates_cache_directory(self, tmp_path: Path) -> None:
        """Verify cache directory is created if it doesn't exist."""
        gate = GateResult(
            name="Build",
            command="python -m build",
            passed=False,
            exit_code=1,
            stdout="",
            stderr="build error",
            duration_ms=100,
        )
        qg_result = PostMergeQGResult(
            all_passed=False,
            story_id="4.1",
            gate_results=[gate],
            duration_ms=100,
        )

        report_path = _write_post_merge_failure_report("4.1", tmp_path, qg_result)

        assert (tmp_path / ".bmad-assist-lite" / "cache").is_dir()
        assert report_path.exists()

    def test_report_log_uses_prefix(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify report writing logs with correct prefix."""
        gate = GateResult(
            name="Lint",
            command="ruff check",
            passed=False,
            exit_code=1,
            stdout="",
            stderr="",
            duration_ms=100,
        )
        qg_result = PostMergeQGResult(
            all_passed=False,
            story_id="3.1",
            gate_results=[gate],
            duration_ms=100,
        )

        with caplog.at_level("INFO"):
            _write_post_merge_failure_report("3.1", tmp_path, qg_result)

        assert any("[QG|post-merge|3.1]" in r.message for r in caplog.records)


# ============================================================================
# MergeQueue.process_next() Post-Merge QG Integration (Task 6.9-6.10)
# ============================================================================


class TestMergeQueuePostMergeQG:
    """Test MergeQueue.process_next() runs post-merge QG after merge."""

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_qg_runs_after_successful_merge(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
    ) -> None:
        """Verify QG runs after successful merge and result is attached."""
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = PostMergeQGResult(
            all_passed=True,
            story_id="3.1",
            gate_results=[],
            duration_ms=0,
        )

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")

        result = await queue.process_next()

        assert result is not None
        assert result.success is True
        assert result.qg_result is not None
        assert result.qg_result.all_passed is True
        mock_qg.assert_called_once_with("3.1", Path("/repo"), None)

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_qg_skipped_on_merge_failure(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
    ) -> None:
        """Verify QG is NOT run on merge failure."""
        mock_merge.return_value = MergeResult(
            success=False,
            story_id="3.2",
            conflict_files=["src/main.py"],
            error="Merge conflict",
        )

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.2")

        result = await queue.process_next()

        assert result is not None
        assert result.success is False
        assert result.qg_result is None
        mock_qg.assert_not_called()

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_merge_success_qg_pass(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
    ) -> None:
        """Verify merge success + QG all pass returns correct result."""
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = PostMergeQGResult(
            all_passed=True,
            story_id="3.1",
            gate_results=[],
            duration_ms=500,
        )

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")

        result = await queue.process_next()

        assert result is not None
        assert result.success is True
        assert result.qg_result is not None
        assert result.qg_result.all_passed is True

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_merge_success_qg_fail(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
    ) -> None:
        """Verify merge success + QG failure returns correct result."""
        fail_gate = GateResult(
            name="Lint",
            command="ruff check",
            passed=False,
            exit_code=1,
            stdout="Error",
            stderr="",
            duration_ms=100,
        )
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = PostMergeQGResult(
            all_passed=False,
            story_id="3.1",
            gate_results=[fail_gate],
            duration_ms=100,
        )

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")

        result = await queue.process_next()

        assert result is not None
        assert result.success is True
        assert result.qg_result is not None
        assert result.qg_result.all_passed is False

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_config_passed_through_to_qg(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
    ) -> None:
        """Verify config is passed through to run_post_merge_qg."""
        config = _make_config(lint="ruff check")
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = PostMergeQGResult(
            all_passed=True,
            story_id="3.1",
        )

        queue = MergeQueue(project_root=Path("/repo"), config=config)
        await queue.enqueue("3.1")

        await queue.process_next()

        mock_qg.assert_called_once_with("3.1", Path("/repo"), config)

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_qg_exception_preserves_merge_result(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
    ) -> None:
        """Verify QG infrastructure exception does not swallow merge result."""
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.side_effect = OSError("disk full")

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")

        result = await queue.process_next()

        assert result is not None
        assert result.success is True
        assert result.qg_result is None


# ============================================================================
# MergeResult.qg_result field Tests
# ============================================================================


class TestMergeResultQGField:
    """Test MergeResult.qg_result field."""

    def test_qg_result_default_none(self) -> None:
        """Verify qg_result defaults to None."""
        result = MergeResult(success=True, story_id="3.1")
        assert result.qg_result is None

    def test_qg_result_set(self) -> None:
        """Verify qg_result can be set on creation."""
        qg = PostMergeQGResult(all_passed=True, story_id="3.1")
        result = MergeResult(success=True, story_id="3.1", qg_result=qg)
        assert result.qg_result is not None
        assert result.qg_result.all_passed is True

    def test_qg_result_model_copy(self) -> None:
        """Verify qg_result can be set via model_copy."""
        result = MergeResult(success=True, story_id="3.1")
        qg = PostMergeQGResult(all_passed=True, story_id="3.1")
        updated = result.model_copy(update={"qg_result": qg})
        assert updated.qg_result is not None
        assert updated.qg_result.all_passed is True
        assert result.qg_result is None  # Original unchanged
