"""Tests for git subprocess wrapper module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.git_ops import (
    _run_git,
    get_current_branch,
    is_protected_branch,
)

# ============================================================================
# _run_git Tests
# ============================================================================


class TestRunGitSuccess:
    """Test _run_git with successful git commands."""

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_successful_command_returns_completed_process(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify _run_git returns CompletedProcess on success."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="On branch main\n",
            stderr="",
        )

        result = _run_git(["status"], cwd=Path("/repo"))

        assert result.returncode == 0
        assert result.stdout == "On branch main\n"

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_args_include_git_prefix(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify subprocess.run is called with ["git", *args]."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )

        _run_git(["status"], cwd=Path("/repo"))

        call_args = mock_run.call_args
        assert call_args[0][0] == ["git", "status"]

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_capture_output_true(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify capture_output=True is passed to subprocess.run."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )

        _run_git(["status"], cwd=Path("/repo"))

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["capture_output"] is True

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_text_true(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify text=True is passed to subprocess.run."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )

        _run_git(["status"], cwd=Path("/repo"))

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["text"] is True

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_encoding_utf8(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify encoding='utf-8' is passed to subprocess.run."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )

        _run_git(["status"], cwd=Path("/repo"))

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["encoding"] == "utf-8"

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_subprocess_kwargs_passed(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify get_subprocess_kwargs() kwargs are unpacked into subprocess.run."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )

        _run_git(["status"], cwd=Path("/repo"))

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["start_new_session"] is True

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_cwd_passed_as_string(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify cwd Path is converted to string for subprocess.run."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=0,
            stdout="",
            stderr="",
        )

        cwd = Path("/my/repo")
        _run_git(["status"], cwd=cwd)

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == str(cwd)
        assert isinstance(call_kwargs["cwd"], str)

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_multi_word_git_command(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify multi-word git commands are passed correctly."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--abbrev-ref", "HEAD"],
            returncode=0,
            stdout="main\n",
            stderr="",
        )

        _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=Path("/repo"))

        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "rev-parse", "--abbrev-ref", "HEAD"]


class TestRunGitCheckTrue:
    """Test _run_git error handling with check=True (default)."""

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_nonzero_exit_raises_parallel_error(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify non-zero exit with check=True raises ParallelError."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository\n",
        )

        with pytest.raises(ParallelError, match="git status failed"):
            _run_git(["status"], cwd=Path("/repo"))

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_error_message_includes_stderr(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify error message includes stderr content."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=1,
            stdout="",
            stderr="error: specific problem\n",
        )

        with pytest.raises(ParallelError, match="error: specific problem"):
            _run_git(["status"], cwd=Path("/repo"))

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_error_message_includes_subcommand_name(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify error message format includes the git subcommand name."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "merge"],
            returncode=1,
            stdout="",
            stderr="merge conflict\n",
        )

        with pytest.raises(ParallelError, match="git merge failed"):
            _run_git(["merge"], cwd=Path("/repo"))

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_empty_stderr_clean_error_message(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify error message is clean when stderr is empty."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=1,
            stdout="",
            stderr="",
        )

        with pytest.raises(ParallelError, match="git status failed:"):
            _run_git(["status"], cwd=Path("/repo"))


class TestRunGitCheckFalse:
    """Test _run_git passthrough with check=False."""

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    def test_nonzero_exit_returns_completed_process(
        self,
        mock_kwargs: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """Verify non-zero exit with check=False returns CompletedProcess."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "merge"],
            returncode=1,
            stdout="",
            stderr="CONFLICT (content): merge conflict\n",
        )

        result = _run_git(["merge"], cwd=Path("/repo"), check=False)

        assert result.returncode == 1
        assert "CONFLICT" in result.stderr


class TestRunGitEmptyArgs:
    """Test _run_git with empty args list."""

    def test_empty_args_raises_parallel_error(self) -> None:
        """Verify _run_git([]) raises ParallelError without calling subprocess.run."""
        with pytest.raises(ParallelError, match="_run_git requires at least one argument"):
            _run_git([], cwd=Path("/repo"))

    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    def test_empty_args_does_not_call_subprocess(
        self,
        mock_run: MagicMock,
    ) -> None:
        """Verify subprocess.run is not called when args is empty."""
        with pytest.raises(ParallelError):
            _run_git([], cwd=Path("/repo"))

        mock_run.assert_not_called()


class TestRunGitSubprocessExceptions:
    """Test _run_git handling of subprocess.run exceptions."""

    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    def test_file_not_found_raises_parallel_error(
        self,
        mock_run: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Verify FileNotFoundError is wrapped in ParallelError."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'git'")

        with pytest.raises(ParallelError, match="git executable not found on PATH"):
            _run_git(["status"], cwd=Path("/repo"))

    @patch("bmad_assist_lite.parallel.git_ops.get_subprocess_kwargs")
    @patch("bmad_assist_lite.parallel.git_ops.subprocess.run")
    def test_os_error_raises_parallel_error(
        self,
        mock_run: MagicMock,
        mock_kwargs: MagicMock,
    ) -> None:
        """Verify OSError is wrapped in ParallelError."""
        mock_kwargs.return_value = {"start_new_session": True}
        mock_run.side_effect = OSError("Permission denied")

        with pytest.raises(ParallelError, match="failed to execute git"):
            _run_git(["status"], cwd=Path("/repo"))


# ============================================================================
# get_current_branch Tests
# ============================================================================


class TestGetCurrentBranch:
    """Test get_current_branch() helper."""

    @patch("bmad_assist_lite.parallel.git_ops._run_git")
    def test_returns_stripped_branch_name(
        self,
        mock_run_git: MagicMock,
    ) -> None:
        """Verify get_current_branch returns stripped stdout."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--abbrev-ref", "HEAD"],
            returncode=0,
            stdout="feature/parallel\n",
            stderr="",
        )

        result = get_current_branch(cwd=Path("/repo"))

        assert result == "feature/parallel"
        mock_run_git.assert_called_once_with(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=Path("/repo")
        )

    @patch("bmad_assist_lite.parallel.git_ops._run_git")
    def test_detached_head_returns_head_string(
        self,
        mock_run_git: MagicMock,
    ) -> None:
        """Verify get_current_branch returns 'HEAD' in detached HEAD state."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "rev-parse", "--abbrev-ref", "HEAD"],
            returncode=0,
            stdout="HEAD\n",
            stderr="",
        )

        result = get_current_branch(cwd=Path("/repo"))

        assert result == "HEAD"

    @patch("bmad_assist_lite.parallel.git_ops._run_git")
    def test_propagates_parallel_error(
        self,
        mock_run_git: MagicMock,
    ) -> None:
        """Verify ParallelError propagates when git command fails."""
        mock_run_git.side_effect = ParallelError(
            "git rev-parse failed: fatal: not a git repository"
        )

        with pytest.raises(ParallelError, match="git rev-parse failed"):
            get_current_branch(cwd=Path("/not-a-repo"))


# ============================================================================
# is_protected_branch Tests
# ============================================================================


class TestIsProtectedBranch:
    """Test is_protected_branch() helper."""

    def test_main_is_protected(self) -> None:
        """Verify 'main' is a protected branch."""
        assert is_protected_branch("main") is True

    def test_master_is_protected(self) -> None:
        """Verify 'master' is a protected branch."""
        assert is_protected_branch("master") is True

    def test_feature_branch_not_protected(self) -> None:
        """Verify feature branches are not protected."""
        assert is_protected_branch("feature/parallel") is False

    def test_epic_branch_not_protected(self) -> None:
        """Verify epic branches are not protected."""
        assert is_protected_branch("epic/1") is False

    def test_develop_not_protected(self) -> None:
        """Verify 'develop' is not a protected branch."""
        assert is_protected_branch("develop") is False

    def test_empty_string_not_protected(self) -> None:
        """Verify empty string is not a protected branch."""
        assert is_protected_branch("") is False
