"""Tests for worktree bootstrap module (Story 9.1).

Covers BootstrapResult model, copy_files_to_worktree(), run_setup_commands(),
run_validation_command(), bootstrap_worktree(), and ParallelConfig bootstrap fields.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from bmad_assist_lite.parallel.bootstrap import (
    BootstrapResult,
    bootstrap_worktree,
    copy_files_to_worktree,
    run_setup_commands,
    run_validation_command,
)
from bmad_assist_lite.parallel.config import ParallelConfig


# ============================================================================
# ParallelConfig — Bootstrap Field Defaults (Task 8.1)
# ============================================================================


class TestParallelConfigBootstrapDefaults:
    """Verify all 5 new bootstrap fields have correct defaults."""

    def test_default_copy_to_worktree(self) -> None:
        config = ParallelConfig()
        assert config.copy_to_worktree == []

    def test_default_copy_strict(self) -> None:
        config = ParallelConfig()
        assert config.copy_strict is False

    def test_default_setup_commands(self) -> None:
        config = ParallelConfig()
        assert config.setup_commands == []

    def test_default_validation_command(self) -> None:
        config = ParallelConfig()
        assert config.validation_command is None

    def test_default_bootstrap_timeout(self) -> None:
        config = ParallelConfig()
        assert config.bootstrap_timeout == 120


class TestParallelConfigBootstrapValidation:
    """Verify bootstrap field validation rules."""

    def test_bootstrap_timeout_minimum_enforced(self) -> None:
        with pytest.raises(ValidationError, match="bootstrap_timeout"):
            ParallelConfig(bootstrap_timeout=0)

    def test_bootstrap_timeout_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="bootstrap_timeout"):
            ParallelConfig(bootstrap_timeout=-1)

    def test_bootstrap_timeout_one_accepted(self) -> None:
        config = ParallelConfig(bootstrap_timeout=1)
        assert config.bootstrap_timeout == 1

    def test_bootstrap_timeout_large_value_accepted(self) -> None:
        config = ParallelConfig(bootstrap_timeout=3600)
        assert config.bootstrap_timeout == 3600

    def test_custom_copy_to_worktree(self) -> None:
        config = ParallelConfig(copy_to_worktree=[".env", "config/"])
        assert config.copy_to_worktree == [".env", "config/"]

    def test_custom_setup_commands(self) -> None:
        config = ParallelConfig(setup_commands=["pip install -e .", "npm ci"])
        assert config.setup_commands == ["pip install -e .", "npm ci"]

    def test_custom_validation_command(self) -> None:
        config = ParallelConfig(validation_command="pytest -q -x")
        assert config.validation_command == "pytest -q -x"

    def test_copy_strict_true(self) -> None:
        config = ParallelConfig(copy_strict=True)
        assert config.copy_strict is True


# ============================================================================
# BootstrapResult — Model Tests (Task 8.1)
# ============================================================================


class TestBootstrapResultModel:
    """Verify BootstrapResult is a frozen Pydantic model with correct fields."""

    def test_success_result(self) -> None:
        result = BootstrapResult(success=True)
        assert result.success is True
        assert result.failed_phase is None
        assert result.error_message is None
        assert result.output == ""

    def test_failure_result(self) -> None:
        result = BootstrapResult(
            success=False,
            failed_phase="copy",
            error_message="File not found",
            output="some output",
        )
        assert result.success is False
        assert result.failed_phase == "copy"
        assert result.error_message == "File not found"
        assert result.output == "some output"

    def test_frozen_model(self) -> None:
        result = BootstrapResult(success=True)
        with pytest.raises(ValidationError):
            result.success = False  # type: ignore[misc]

    def test_failed_phase_literal_copy(self) -> None:
        result = BootstrapResult(success=False, failed_phase="copy")
        assert result.failed_phase == "copy"

    def test_failed_phase_literal_setup(self) -> None:
        result = BootstrapResult(success=False, failed_phase="setup")
        assert result.failed_phase == "setup"

    def test_failed_phase_literal_validation(self) -> None:
        result = BootstrapResult(success=False, failed_phase="validation")
        assert result.failed_phase == "validation"

    def test_failed_phase_invalid_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BootstrapResult(success=False, failed_phase="invalid")  # type: ignore[arg-type]


# ============================================================================
# copy_files_to_worktree — Happy Path (Task 8.2, AC #1)
# ============================================================================


class TestCopyFilesHappyPath:
    """Test copying files from project root to worktree."""

    def test_copy_single_file(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        env_file = project_root / ".env"
        env_file.write_text("SECRET=abc")

        result = copy_files_to_worktree(
            files=[".env"],
            project_root=project_root,
            worktree_path=worktree,
        )

        assert result.success is True
        assert (worktree / ".env").exists()
        assert (worktree / ".env").read_text() == "SECRET=abc"

    def test_copy_multiple_files(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        (project_root / ".env").write_text("KEY=val")
        (project_root / "local.settings.json").write_text("{}")

        result = copy_files_to_worktree(
            files=[".env", "local.settings.json"],
            project_root=project_root,
            worktree_path=worktree,
        )

        assert result.success is True
        assert (worktree / ".env").exists()
        assert (worktree / "local.settings.json").exists()

    def test_copy_file_in_subdirectory(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        (project_root / "config").mkdir()
        (project_root / "config" / "app.yaml").write_text("key: value")

        result = copy_files_to_worktree(
            files=["config/app.yaml"],
            project_root=project_root,
            worktree_path=worktree,
        )

        assert result.success is True
        assert (worktree / "config" / "app.yaml").exists()
        assert (worktree / "config" / "app.yaml").read_text() == "key: value"

    def test_copy_output_lists_files(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        (project_root / ".env").write_text("x=1")

        result = copy_files_to_worktree(
            files=[".env"],
            project_root=project_root,
            worktree_path=worktree,
        )

        assert "Copied file: .env" in result.output


# ============================================================================
# copy_files_to_worktree — Directory Copy (Task 8.2, AC #8)
# ============================================================================


class TestCopyFilesDirectory:
    """Test copying directories recursively."""

    def test_copy_directory_with_trailing_slash(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        config_dir = project_root / "config"
        config_dir.mkdir()
        (config_dir / "a.yaml").write_text("a: 1")
        (config_dir / "b.yaml").write_text("b: 2")
        sub = config_dir / "sub"
        sub.mkdir()
        (sub / "c.yaml").write_text("c: 3")

        result = copy_files_to_worktree(
            files=["config/"],
            project_root=project_root,
            worktree_path=worktree,
        )

        assert result.success is True
        assert (worktree / "config" / "a.yaml").read_text() == "a: 1"
        assert (worktree / "config" / "b.yaml").read_text() == "b: 2"
        assert (worktree / "config" / "sub" / "c.yaml").read_text() == "c: 3"

    def test_copy_directory_without_trailing_slash(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        config_dir = project_root / "config"
        config_dir.mkdir()
        (config_dir / "settings.json").write_text("{}")

        result = copy_files_to_worktree(
            files=["config"],
            project_root=project_root,
            worktree_path=worktree,
        )

        assert result.success is True
        assert (worktree / "config" / "settings.json").read_text() == "{}"

    def test_copy_directory_output_message(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        (project_root / "config").mkdir()
        (project_root / "config" / "x.txt").write_text("x")

        result = copy_files_to_worktree(
            files=["config/"],
            project_root=project_root,
            worktree_path=worktree,
        )

        assert "Copied directory: config/" in result.output

    def test_copy_directory_dirs_exist_ok(self, tmp_path: Path) -> None:
        """Verify dirs_exist_ok=True allows overwriting existing directories."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        config_dir = project_root / "config"
        config_dir.mkdir()
        (config_dir / "new.txt").write_text("new")

        # Pre-create destination directory with existing file
        dest_config = worktree / "config"
        dest_config.mkdir()
        (dest_config / "existing.txt").write_text("old")

        result = copy_files_to_worktree(
            files=["config"],
            project_root=project_root,
            worktree_path=worktree,
        )

        assert result.success is True
        assert (worktree / "config" / "new.txt").read_text() == "new"
        assert (worktree / "config" / "existing.txt").read_text() == "old"


# ============================================================================
# copy_files_to_worktree — Missing File (Task 8.2, AC #2, #3)
# ============================================================================


class TestCopyFilesMissing:
    """Test behavior when source files are missing."""

    def test_missing_file_strict_false_continues(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        result = copy_files_to_worktree(
            files=[".env"],
            project_root=project_root,
            worktree_path=worktree,
            strict=False,
        )

        assert result.success is True
        assert "WARNING" in result.output

    def test_missing_file_strict_false_no_empty_dirs(self, tmp_path: Path) -> None:
        """Verify no empty parent directories created when source missing."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        result = copy_files_to_worktree(
            files=["deep/nested/file.txt"],
            project_root=project_root,
            worktree_path=worktree,
            strict=False,
        )

        assert result.success is True
        assert not (worktree / "deep").exists()

    def test_missing_file_strict_true_fails(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        result = copy_files_to_worktree(
            files=[".env"],
            project_root=project_root,
            worktree_path=worktree,
            strict=True,
        )

        assert result.success is False
        assert result.failed_phase == "copy"
        assert result.error_message is not None
        assert ".env" in result.error_message

    def test_partial_success_non_strict(self, tmp_path: Path) -> None:
        """Some files exist, some don't — strict=False returns success with warnings."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        (project_root / ".env").write_text("found=1")

        result = copy_files_to_worktree(
            files=[".env", "missing.txt"],
            project_root=project_root,
            worktree_path=worktree,
            strict=False,
        )

        assert result.success is True
        assert (worktree / ".env").exists()
        assert "WARNING" in result.output
        assert "Copied file: .env" in result.output

    def test_partial_strict_fails_on_first_missing(self, tmp_path: Path) -> None:
        """strict=True stops at the first missing file."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        result = copy_files_to_worktree(
            files=["missing1.txt", "missing2.txt"],
            project_root=project_root,
            worktree_path=worktree,
            strict=True,
        )

        assert result.success is False
        assert result.failed_phase == "copy"
        assert "missing1.txt" in (result.error_message or "")


# ============================================================================
# copy_files_to_worktree — Path Traversal / Security (Synthesis Fix)
# ============================================================================


class TestCopyFilesPathSecurity:
    """Test path traversal protection in copy_files_to_worktree."""

    def test_parent_traversal_strict_fails(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        result = copy_files_to_worktree(
            files=["../etc/passwd"],
            project_root=project_root,
            worktree_path=worktree,
            strict=True,
        )

        assert result.success is False
        assert result.failed_phase == "copy"
        assert "escapes" in (result.error_message or "").lower()

    def test_parent_traversal_non_strict_warns(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        result = copy_files_to_worktree(
            files=["../etc/passwd"],
            project_root=project_root,
            worktree_path=worktree,
            strict=False,
        )

        assert result.success is True
        assert "WARNING" in result.output
        assert "escapes" in result.output.lower()

    def test_empty_entry_strict_fails(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        result = copy_files_to_worktree(
            files=[""],
            project_root=project_root,
            worktree_path=worktree,
            strict=True,
        )

        assert result.success is False
        assert result.failed_phase == "copy"

    def test_dot_entry_strict_fails(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        result = copy_files_to_worktree(
            files=["."],
            project_root=project_root,
            worktree_path=worktree,
            strict=True,
        )

        assert result.success is False
        assert result.failed_phase == "copy"


# ============================================================================
# copy_files_to_worktree — OSError Handling (Synthesis Fix)
# ============================================================================


class TestCopyFilesOSError:
    """Test that shutil OSError is caught and returns BootstrapResult."""

    @patch("bmad_assist_lite.parallel.bootstrap.shutil.copy2")
    def test_copy_oserror_strict_returns_failure(
        self, mock_copy2: MagicMock, tmp_path: Path
    ) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (project_root / "file.txt").write_text("data")

        mock_copy2.side_effect = PermissionError("Permission denied")

        result = copy_files_to_worktree(
            files=["file.txt"],
            project_root=project_root,
            worktree_path=worktree,
            strict=True,
        )

        assert result.success is False
        assert result.failed_phase == "copy"
        assert "Permission denied" in (result.error_message or "")

    @patch("bmad_assist_lite.parallel.bootstrap.shutil.copy2")
    def test_copy_oserror_non_strict_warns(
        self, mock_copy2: MagicMock, tmp_path: Path
    ) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (project_root / "file.txt").write_text("data")

        mock_copy2.side_effect = OSError("Disk full")

        result = copy_files_to_worktree(
            files=["file.txt"],
            project_root=project_root,
            worktree_path=worktree,
            strict=False,
        )

        assert result.success is True
        assert "WARNING" in result.output
        assert "Disk full" in result.output

    @patch("bmad_assist_lite.parallel.bootstrap.shutil.copytree")
    def test_copytree_oserror_strict_returns_failure(
        self, mock_copytree: MagicMock, tmp_path: Path
    ) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (project_root / "config").mkdir()

        mock_copytree.side_effect = PermissionError("Access denied")

        result = copy_files_to_worktree(
            files=["config"],
            project_root=project_root,
            worktree_path=worktree,
            strict=True,
        )

        assert result.success is False
        assert result.failed_phase == "copy"
        assert "Access denied" in (result.error_message or "")


# ============================================================================
# copy_files_to_worktree — Nested Directory Parent Creation (Synthesis Fix)
# ============================================================================


class TestCopyFilesNestedDirectory:
    """Test that parent dirs are created for nested copytree destinations."""

    def test_nested_directory_copy(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        nested = project_root / "deep" / "nested" / "config"
        nested.mkdir(parents=True)
        (nested / "app.yaml").write_text("x: 1")

        result = copy_files_to_worktree(
            files=["deep/nested/config"],
            project_root=project_root,
            worktree_path=worktree,
        )

        assert result.success is True
        assert (worktree / "deep" / "nested" / "config" / "app.yaml").exists()


# ============================================================================
# run_setup_commands — Success (Task 8.3, AC #4)
# ============================================================================


class TestRunSetupCommandsSuccess:
    """Test setup command execution — all commands succeed."""

    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_all_commands_run_in_order(
        self, mock_popen: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        process_mock = MagicMock()
        process_mock.communicate.return_value = ("output\n", "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        result = run_setup_commands(
            commands=["cmd1", "cmd2", "cmd3"],
            worktree_path=Path("/worktree"),
            timeout=60,
        )

        assert result.success is True
        assert mock_popen.call_count == 3

    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_commands_run_with_correct_cwd(
        self, mock_popen: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        process_mock = MagicMock()
        process_mock.communicate.return_value = ("", "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        run_setup_commands(
            commands=["pip install -e ."],
            worktree_path=Path("/my/worktree"),
            timeout=60,
        )

        mock_popen.assert_called_once_with(
            "pip install -e .",
            cwd=str(Path("/my/worktree")),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            shell=True,
            start_new_session=True,
        )

    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_output_accumulated(
        self, mock_popen: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        process_mock = MagicMock()
        process_mock.communicate.return_value = ("installed packages\n", "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        result = run_setup_commands(
            commands=["pip install"],
            worktree_path=Path("/worktree"),
        )

        assert "installed packages" in result.output


# ============================================================================
# run_setup_commands — Failure (Task 8.3, AC #4)
# ============================================================================


class TestRunSetupCommandsFailure:
    """Test setup command failure skips remaining commands."""

    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_mid_sequence_failure_skips_rest(
        self, mock_popen: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        success_proc = MagicMock()
        success_proc.communicate.return_value = ("ok\n", "")
        success_proc.returncode = 0

        fail_proc = MagicMock()
        fail_proc.communicate.return_value = ("", "error: failed\n")
        fail_proc.returncode = 1

        mock_popen.side_effect = [success_proc, fail_proc]

        result = run_setup_commands(
            commands=["cmd1", "cmd2", "cmd3"],
            worktree_path=Path("/worktree"),
        )

        assert result.success is False
        assert result.failed_phase == "setup"
        assert mock_popen.call_count == 2  # third command never called
        assert "error: failed" in result.output

    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_first_command_failure(
        self, mock_popen: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        fail_proc = MagicMock()
        fail_proc.communicate.return_value = ("", "not found\n")
        fail_proc.returncode = 127

        mock_popen.return_value = fail_proc

        result = run_setup_commands(
            commands=["bad_cmd", "never_runs"],
            worktree_path=Path("/worktree"),
        )

        assert result.success is False
        assert result.failed_phase == "setup"
        assert mock_popen.call_count == 1


# ============================================================================
# run_setup_commands — Timeout (Task 8.3, AC #6)
# ============================================================================


class TestRunSetupCommandsTimeout:
    """Test setup command timeout kills process tree."""

    @patch("bmad_assist_lite.parallel.bootstrap.terminate_process")
    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_timeout_returns_failure(
        self,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        mock_terminate: MagicMock,
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        process_mock = MagicMock()
        process_mock.pid = 12345
        timeout_err = subprocess.TimeoutExpired(cmd="slow_cmd", timeout=5)
        # First communicate() raises TimeoutExpired
        # Second communicate() (after kill) returns drained output
        process_mock.communicate.side_effect = [
            timeout_err,
            ("partial output", "partial error"),
        ]
        mock_popen.return_value = process_mock

        result = run_setup_commands(
            commands=["slow_cmd"],
            worktree_path=Path("/worktree"),
            timeout=5,
        )

        assert result.success is False
        assert result.failed_phase == "setup"
        assert "timed out" in (result.error_message or "").lower()
        assert "partial output" in result.output
        assert "partial error" in result.output
        mock_terminate.assert_called_once_with(12345)

    @patch("bmad_assist_lite.parallel.bootstrap.terminate_process")
    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_timeout_skips_remaining_commands(
        self,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        mock_terminate: MagicMock,
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        process_mock = MagicMock()
        process_mock.pid = 99
        timeout_err = subprocess.TimeoutExpired(cmd="hang", timeout=10)
        # First communicate() raises, second returns drained output
        process_mock.communicate.side_effect = [timeout_err, ("", "")]
        mock_popen.return_value = process_mock

        result = run_setup_commands(
            commands=["hang", "never_runs"],
            worktree_path=Path("/worktree"),
            timeout=10,
        )

        assert result.success is False
        assert mock_popen.call_count == 1


# ============================================================================
# run_validation_command — Pass (Task 8.4, AC #5)
# ============================================================================


class TestRunValidationCommandPass:
    """Test validation command with successful exit."""

    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_success_result(
        self, mock_popen: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        process_mock = MagicMock()
        process_mock.communicate.return_value = ("5 passed\n", "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        result = run_validation_command(
            command="pytest -q -x",
            worktree_path=Path("/worktree"),
        )

        assert result.success is True
        assert "5 passed" in result.output

    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_correct_popen_kwargs(
        self, mock_popen: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        process_mock = MagicMock()
        process_mock.communicate.return_value = ("", "")
        process_mock.returncode = 0
        mock_popen.return_value = process_mock

        run_validation_command(
            command="pytest -q -x",
            worktree_path=Path("/test/worktree"),
            timeout=30,
        )

        mock_popen.assert_called_once_with(
            "pytest -q -x",
            cwd=str(Path("/test/worktree")),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            shell=True,
            start_new_session=True,
        )


# ============================================================================
# run_validation_command — Fail (Task 8.4, AC #5)
# ============================================================================


class TestRunValidationCommandFail:
    """Test validation command with non-zero exit."""

    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_failure_result(
        self, mock_popen: MagicMock, mock_kwargs: MagicMock
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        process_mock = MagicMock()
        process_mock.communicate.return_value = ("", "FAILED test_x.py\n")
        process_mock.returncode = 1
        mock_popen.return_value = process_mock

        result = run_validation_command(
            command="pytest -q -x",
            worktree_path=Path("/worktree"),
        )

        assert result.success is False
        assert result.failed_phase == "validation"
        assert "FAILED test_x.py" in result.output


# ============================================================================
# run_validation_command — Timeout (Task 8.4, AC #6)
# ============================================================================


class TestRunValidationCommandTimeout:
    """Test validation command timeout kills process tree."""

    @patch("bmad_assist_lite.parallel.bootstrap.terminate_process")
    @patch("bmad_assist_lite.parallel.bootstrap.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_timeout_returns_failure(
        self,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        mock_terminate: MagicMock,
    ) -> None:
        mock_kwargs.return_value = {"start_new_session": True}

        process_mock = MagicMock()
        process_mock.pid = 54321
        timeout_err = subprocess.TimeoutExpired(cmd="pytest", timeout=30)
        # First communicate() raises, second returns drained output
        process_mock.communicate.side_effect = [
            timeout_err,
            ("collecting...", ""),
        ]
        mock_popen.return_value = process_mock

        result = run_validation_command(
            command="pytest -q -x",
            worktree_path=Path("/worktree"),
            timeout=30,
        )

        assert result.success is False
        assert result.failed_phase == "validation"
        assert "timed out" in (result.error_message or "").lower()
        assert "collecting..." in result.output
        mock_terminate.assert_called_once_with(54321)


# ============================================================================
# bootstrap_worktree — No-op (Task 8.5, AC #7)
# ============================================================================


class TestBootstrapWorktreeNoop:
    """Test bootstrap with default config does nothing."""

    @patch("bmad_assist_lite.parallel.bootstrap.subprocess.Popen")
    def test_noop_returns_success_immediately(
        self, mock_popen: MagicMock
    ) -> None:
        config = ParallelConfig()

        result = bootstrap_worktree(
            project_root=Path("/project"),
            worktree_path=Path("/worktree"),
            config=config,
        )

        assert result.success is True
        mock_popen.assert_not_called()

    def test_noop_with_empty_lists(self) -> None:
        config = ParallelConfig(
            copy_to_worktree=[],
            setup_commands=[],
            validation_command=None,
        )

        result = bootstrap_worktree(
            project_root=Path("/project"),
            worktree_path=Path("/worktree"),
            config=config,
        )

        assert result.success is True


# ============================================================================
# bootstrap_worktree — Full Pipeline (Task 8.5)
# ============================================================================


class TestBootstrapWorktreeFullPipeline:
    """Test full bootstrap pipeline execution."""

    def test_full_pipeline_success(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        (project_root / ".env").write_text("KEY=val")

        config = ParallelConfig(
            copy_to_worktree=[".env"],
        )

        result = bootstrap_worktree(
            project_root=project_root,
            worktree_path=worktree,
            config=config,
        )

        assert result.success is True
        assert (worktree / ".env").exists()

    @patch("bmad_assist_lite.parallel.bootstrap.run_validation_command")
    @patch("bmad_assist_lite.parallel.bootstrap.run_setup_commands")
    def test_full_pipeline_all_phases(
        self,
        mock_setup: MagicMock,
        mock_validation: MagicMock,
        tmp_path: Path,
    ) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        (project_root / ".env").write_text("X=1")

        mock_setup.return_value = BootstrapResult(success=True, output="setup ok")
        mock_validation.return_value = BootstrapResult(
            success=True, output="validation ok"
        )

        config = ParallelConfig(
            copy_to_worktree=[".env"],
            setup_commands=["pip install"],
            validation_command="pytest",
            bootstrap_timeout=60,
        )

        result = bootstrap_worktree(
            project_root=project_root,
            worktree_path=worktree,
            config=config,
        )

        assert result.success is True
        mock_setup.assert_called_once_with(
            commands=["pip install"],
            worktree_path=worktree,
            timeout=60,
        )
        mock_validation.assert_called_once_with(
            command="pytest",
            worktree_path=worktree,
            timeout=60,
        )
        assert "setup ok" in result.output
        assert "validation ok" in result.output


# ============================================================================
# bootstrap_worktree — validate=False (Task 8.5)
# ============================================================================


class TestBootstrapWorktreeValidateFalse:
    """Test validate=False skips validation phase."""

    @patch("bmad_assist_lite.parallel.bootstrap.run_validation_command")
    @patch("bmad_assist_lite.parallel.bootstrap.run_setup_commands")
    def test_validation_skipped_when_false(
        self,
        mock_setup: MagicMock,
        mock_validation: MagicMock,
    ) -> None:
        mock_setup.return_value = BootstrapResult(success=True, output="done")

        config = ParallelConfig(
            setup_commands=["pip install"],
            validation_command="pytest",
        )

        result = bootstrap_worktree(
            project_root=Path("/project"),
            worktree_path=Path("/worktree"),
            config=config,
            validate=False,
        )

        assert result.success is True
        mock_setup.assert_called_once()
        mock_validation.assert_not_called()


# ============================================================================
# bootstrap_worktree — Phase Failure Short-circuits (Task 8.5)
# ============================================================================


class TestBootstrapWorktreeFailureShortCircuit:
    """Test that phase failure stops subsequent phases."""

    @patch("bmad_assist_lite.parallel.bootstrap.run_setup_commands")
    def test_copy_failure_skips_setup(
        self,
        mock_setup: MagicMock,
        tmp_path: Path,
    ) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        config = ParallelConfig(
            copy_to_worktree=[".env"],
            copy_strict=True,
            setup_commands=["pip install"],
        )

        result = bootstrap_worktree(
            project_root=project_root,
            worktree_path=worktree,
            config=config,
        )

        assert result.success is False
        assert result.failed_phase == "copy"
        mock_setup.assert_not_called()

    @patch("bmad_assist_lite.parallel.bootstrap.run_validation_command")
    @patch("bmad_assist_lite.parallel.bootstrap.run_setup_commands")
    def test_setup_failure_skips_validation(
        self,
        mock_setup: MagicMock,
        mock_validation: MagicMock,
    ) -> None:
        mock_setup.return_value = BootstrapResult(
            success=False,
            failed_phase="setup",
            error_message="pip failed",
        )

        config = ParallelConfig(
            setup_commands=["pip install"],
            validation_command="pytest",
        )

        result = bootstrap_worktree(
            project_root=Path("/project"),
            worktree_path=Path("/worktree"),
            config=config,
        )

        assert result.success is False
        assert result.failed_phase == "setup"
        mock_validation.assert_not_called()

    @patch("bmad_assist_lite.parallel.bootstrap.run_validation_command")
    @patch("bmad_assist_lite.parallel.bootstrap.run_setup_commands")
    def test_validation_failure_reported(
        self,
        mock_setup: MagicMock,
        mock_validation: MagicMock,
    ) -> None:
        mock_setup.return_value = BootstrapResult(success=True, output="ok")
        mock_validation.return_value = BootstrapResult(
            success=False,
            failed_phase="validation",
            error_message="tests failed",
            output="1 failed",
        )

        config = ParallelConfig(
            setup_commands=["pip install"],
            validation_command="pytest",
        )

        result = bootstrap_worktree(
            project_root=Path("/project"),
            worktree_path=Path("/worktree"),
            config=config,
        )

        assert result.success is False
        assert result.failed_phase == "validation"
