"""Tests for the worktree manager module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.worktree_manager import (
    WorktreeInfo,
    _branch_name,
    _normalize_story_id,
    _worktree_path,
    cleanup_worktree,
    create_worktree,
    list_worktrees,
    prune_worktrees,
)


# ============================================================================
# Story ID Normalization Tests
# ============================================================================


class TestNormalizeStoryId:
    """Test _normalize_story_id helper."""

    def test_dots_replaced_with_dashes(self) -> None:
        """Verify dots are converted to dashes."""
        assert _normalize_story_id("3.1") == "3-1"

    def test_multiple_dots_all_replaced(self) -> None:
        """Verify multiple dots are all converted."""
        assert _normalize_story_id("3.1.1") == "3-1-1"

    def test_already_dashed_unchanged(self) -> None:
        """Verify already-dashed IDs pass through unchanged."""
        assert _normalize_story_id("3-1") == "3-1"

    def test_no_dots_no_dashes_unchanged(self) -> None:
        """Verify IDs without dots or dashes pass through unchanged."""
        assert _normalize_story_id("31") == "31"

    def test_empty_string(self) -> None:
        """Verify empty string passes through."""
        assert _normalize_story_id("") == ""


class TestWorktreePath:
    """Test _worktree_path helper."""

    def test_returns_correct_path(self, tmp_path: Path) -> None:
        """Verify worktree path follows the {repo}-parallel-{id} convention."""
        result = _worktree_path("3.1", tmp_path, "myrepo")
        expected = (tmp_path / "myrepo-parallel-3-1").resolve()
        assert result == expected

    def test_uses_normalized_story_id(self, tmp_path: Path) -> None:
        """Verify dots in story ID are normalized in the path."""
        result = _worktree_path("3.1.1", tmp_path, "myrepo")
        assert "myrepo-parallel-3-1-1" in str(result)

    def test_returns_resolved_path(self, tmp_path: Path) -> None:
        """Verify the returned path is resolved (absolute)."""
        result = _worktree_path("3.1", tmp_path, "myrepo")
        assert result.is_absolute()

    def test_uses_pathlib_path(self, tmp_path: Path) -> None:
        """Verify the return type is pathlib.Path."""
        result = _worktree_path("3.1", tmp_path, "myrepo")
        assert isinstance(result, Path)


class TestBranchName:
    """Test _branch_name helper."""

    def test_returns_correct_branch(self) -> None:
        """Verify branch name follows the parallel/{id} convention."""
        assert _branch_name("3.1") == "parallel/3-1"

    def test_uses_normalized_story_id(self) -> None:
        """Verify dots in story ID are normalized in branch name."""
        assert _branch_name("3.1.1") == "parallel/3-1-1"

    def test_already_dashed_id(self) -> None:
        """Verify already-dashed IDs produce correct branch names."""
        assert _branch_name("3-1") == "parallel/3-1"

    def test_no_dots_id(self) -> None:
        """Verify IDs without dots produce correct branch names."""
        assert _branch_name("31") == "parallel/31"


# ============================================================================
# create_worktree Tests
# ============================================================================


class TestCreateWorktree:
    """Test create_worktree function."""

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_calls_git_worktree_add(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify correct git worktree add command is issued."""
        project_root = tmp_path / "repo"
        project_root.mkdir()
        base_dir = tmp_path / "worktrees"

        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "add"],
            returncode=0,
            stdout="",
            stderr="",
        )

        create_worktree("3.1", project_root, base_dir)

        expected_path = (base_dir / "repo-parallel-3-1").resolve()
        mock_run_git.assert_called_once_with(
            ["worktree", "add", "-b", "parallel/3-1", str(expected_path)],
            cwd=project_root,
        )

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_returns_worktree_path(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify the returned path matches the expected worktree location."""
        project_root = tmp_path / "repo"
        project_root.mkdir()
        base_dir = tmp_path / "worktrees"

        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "add"],
            returncode=0,
            stdout="",
            stderr="",
        )

        result = create_worktree("3.1", project_root, base_dir)

        expected = (base_dir / "repo-parallel-3-1").resolve()
        assert result == expected
        assert isinstance(result, Path)

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_none_base_dir_defaults_to_parent(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify base_dir=None defaults to project_root.parent."""
        project_root = tmp_path / "repo"
        project_root.mkdir()

        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "add"],
            returncode=0,
            stdout="",
            stderr="",
        )

        result = create_worktree("3.1", project_root)

        expected = (project_root.parent / "repo-parallel-3-1").resolve()
        assert result == expected

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_propagates_parallel_error(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify ParallelError from _run_git is propagated."""
        project_root = tmp_path / "repo"
        project_root.mkdir()

        mock_run_git.side_effect = ParallelError(
            "git worktree failed: already exists"
        )

        with pytest.raises(ParallelError, match="already exists"):
            create_worktree("3.1", project_root)

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_normalizes_story_id_with_dots(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify story ID dots are normalized in git args."""
        project_root = tmp_path / "repo"
        project_root.mkdir()

        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "add"],
            returncode=0,
            stdout="",
            stderr="",
        )

        create_worktree("3.1.1", project_root, tmp_path)

        call_args = mock_run_git.call_args[0][0]
        assert call_args[3] == "parallel/3-1-1"
        assert "parallel-3-1-1" in call_args[4]


# ============================================================================
# cleanup_worktree Tests
# ============================================================================


class TestCleanupWorktree:
    """Test cleanup_worktree function."""

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_calls_worktree_remove_then_branch_delete(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify git worktree remove and git branch -D are called in order."""
        project_root = tmp_path / "repo"
        project_root.mkdir()
        base_dir = tmp_path / "worktrees"

        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="",
            stderr="",
        )

        cleanup_worktree("3.1", project_root, base_dir)

        expected_path = (base_dir / "repo-parallel-3-1").resolve()
        assert mock_run_git.call_count == 2
        mock_run_git.assert_has_calls([
            call(
                ["worktree", "remove", "--force", str(expected_path)],
                cwd=project_root,
                check=False,
            ),
            call(
                ["branch", "-D", "parallel/3-1"],
                cwd=project_root,
                check=False,
            ),
        ])

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_none_base_dir_defaults_to_parent(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify base_dir=None defaults to project_root.parent."""
        project_root = tmp_path / "repo"
        project_root.mkdir()

        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="",
            stderr="",
        )

        cleanup_worktree("3.1", project_root)

        expected_path = (project_root.parent / "repo-parallel-3-1").resolve()
        first_call_args = mock_run_git.call_args_list[0][0][0]
        assert str(expected_path) in first_call_args

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_handles_already_deleted_branch_gracefully(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify cleanup succeeds when branch is already deleted."""
        project_root = tmp_path / "repo"
        project_root.mkdir()

        def side_effect(
            args: list[str], cwd: Path, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            if args[0] == "branch":
                return subprocess.CompletedProcess(
                    args=["git", "branch", "-D"],
                    returncode=1,
                    stdout="",
                    stderr="error: branch 'parallel/3-1' not found.",
                )
            return subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout="",
                stderr="",
            )

        mock_run_git.side_effect = side_effect

        # Should not raise
        cleanup_worktree("3.1", project_root)

        # Verify branch deletion was still attempted
        assert mock_run_git.call_count == 2
        branch_call = mock_run_git.call_args_list[1]
        assert branch_call[0][0] == ["branch", "-D", "parallel/3-1"]
        assert branch_call[1]["check"] is False

    @patch("bmad_assist_lite.parallel.worktree_manager.shutil.rmtree")
    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_removes_persistent_directory_via_shutil(
        self,
        mock_run_git: MagicMock,
        mock_rmtree: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify shutil.rmtree is called when worktree directory persists."""
        project_root = tmp_path / "repo"
        project_root.mkdir()

        # Create the worktree directory so .exists() returns True
        wt_path = (project_root.parent / "repo-parallel-3-1").resolve()
        wt_path.mkdir(parents=True, exist_ok=True)

        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="",
            stderr="",
        )

        cleanup_worktree("3.1", project_root)

        mock_rmtree.assert_called_once_with(wt_path, ignore_errors=True)

    @patch("bmad_assist_lite.parallel.worktree_manager.shutil.rmtree")
    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_shutil_fallback_reached_when_git_remove_fails(
        self,
        mock_run_git: MagicMock,
        mock_rmtree: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify shutil.rmtree fallback is reached when git worktree remove fails."""
        project_root = tmp_path / "repo"
        project_root.mkdir()

        # Create the worktree directory so .exists() returns True
        wt_path = (project_root.parent / "repo-parallel-3-1").resolve()
        wt_path.mkdir(parents=True, exist_ok=True)

        def side_effect(
            args: list[str], cwd: Path, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            if args[0] == "worktree":
                return subprocess.CompletedProcess(
                    args=["git", "worktree", "remove"],
                    returncode=1,
                    stdout="",
                    stderr="fatal: cannot remove: locked",
                )
            return subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout="",
                stderr="",
            )

        mock_run_git.side_effect = side_effect

        cleanup_worktree("3.1", project_root)

        # shutil.rmtree should be called since git remove failed and dir persists
        mock_rmtree.assert_called_once_with(wt_path, ignore_errors=True)

    @patch("bmad_assist_lite.parallel.worktree_manager.shutil.rmtree")
    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_no_shutil_when_directory_gone(
        self,
        mock_run_git: MagicMock,
        mock_rmtree: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify shutil.rmtree is NOT called when directory is already gone."""
        project_root = tmp_path / "repo"
        project_root.mkdir()

        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="",
            stderr="",
        )

        cleanup_worktree("3.1", project_root)

        mock_rmtree.assert_not_called()

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_idempotent_when_worktree_already_removed(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify cleanup succeeds when worktree is already removed."""
        project_root = tmp_path / "repo"
        project_root.mkdir()

        def side_effect(
            args: list[str], cwd: Path, check: bool = True
        ) -> subprocess.CompletedProcess[str]:
            if args[0] == "worktree":
                return subprocess.CompletedProcess(
                    args=["git", "worktree", "remove"],
                    returncode=1,
                    stdout="",
                    stderr="fatal: '/path/to/parallel-3-1' is not a working tree",
                )
            return subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout="",
                stderr="",
            )

        mock_run_git.side_effect = side_effect

        # Should not raise — branch deletion and shutil still execute
        cleanup_worktree("3.1", project_root)

        # Verify branch deletion was still attempted after worktree remove failed
        branch_call = mock_run_git.call_args_list[1]
        assert branch_call[0][0] == ["branch", "-D", "parallel/3-1"]


# ============================================================================
# list_worktrees Tests
# ============================================================================


class TestListWorktrees:
    """Test list_worktrees function."""

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_parses_single_worktree(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify single worktree stanza is parsed correctly."""
        porcelain_output = (
            "worktree /path/to/main\n"
            "HEAD abc1234567890\n"
            "branch refs/heads/main\n"
        )
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout=porcelain_output,
            stderr="",
        )

        result = list_worktrees(tmp_path)

        assert len(result) == 1
        assert result[0].path == Path("/path/to/main")
        assert result[0].branch == "main"
        assert result[0].commit == "abc1234567890"

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_parses_multiple_worktrees(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify multiple worktree stanzas are parsed correctly."""
        porcelain_output = (
            "worktree /path/to/main\n"
            "HEAD abc1234567890\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /path/to/parallel-3-1\n"
            "HEAD def4567890123\n"
            "branch refs/heads/parallel/3-1\n"
        )
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout=porcelain_output,
            stderr="",
        )

        result = list_worktrees(tmp_path)

        assert len(result) == 2
        assert result[0].path == Path("/path/to/main")
        assert result[0].branch == "main"
        assert result[0].commit == "abc1234567890"
        assert result[1].path == Path("/path/to/parallel-3-1")
        assert result[1].branch == "parallel/3-1"
        assert result[1].commit == "def4567890123"

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_empty_output_returns_empty_list(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify empty porcelain output returns empty list."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout="",
            stderr="",
        )

        result = list_worktrees(tmp_path)

        assert result == []

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_detached_head_has_none_branch(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify detached HEAD worktree has branch=None."""
        porcelain_output = (
            "worktree /path/to/detached\n"
            "HEAD abc1234567890\n"
            "detached\n"
        )
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout=porcelain_output,
            stderr="",
        )

        result = list_worktrees(tmp_path)

        assert len(result) == 1
        assert result[0].branch is None
        assert result[0].commit == "abc1234567890"

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_bare_repo_entry_has_none_branch(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify bare repo entry is handled with branch=None."""
        porcelain_output = (
            "worktree /path/to/bare\n"
            "HEAD abc1234567890\n"
            "bare\n"
        )
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout=porcelain_output,
            stderr="",
        )

        result = list_worktrees(tmp_path)

        assert len(result) == 1
        assert result[0].branch is None

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_ignores_locked_and_prunable_metadata_lines(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify locked/prunable metadata lines are gracefully ignored."""
        porcelain_output = (
            "worktree /path/to/main\n"
            "HEAD abc1234567890\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /path/to/parallel-3-1\n"
            "HEAD def4567890123\n"
            "branch refs/heads/parallel/3-1\n"
            "locked\n"
            "\n"
            "worktree /path/to/parallel-3-2\n"
            "HEAD ghi7890123456\n"
            "branch refs/heads/parallel/3-2\n"
            "prunable gitdir file /path/to/.git/worktrees/parallel-3-2 does not exist\n"
        )
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout=porcelain_output,
            stderr="",
        )

        result = list_worktrees(tmp_path)

        assert len(result) == 3
        assert result[1].branch == "parallel/3-1"
        assert result[1].commit == "def4567890123"
        assert result[2].branch == "parallel/3-2"
        assert result[2].commit == "ghi7890123456"

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_calls_correct_git_command(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify the correct git command is called."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout="",
            stderr="",
        )

        list_worktrees(tmp_path)

        mock_run_git.assert_called_once_with(
            ["worktree", "list", "--porcelain"],
            cwd=tmp_path,
        )

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_propagates_parallel_error(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify ParallelError from _run_git is propagated."""
        mock_run_git.side_effect = ParallelError("git worktree failed")

        with pytest.raises(ParallelError, match="git worktree failed"):
            list_worktrees(tmp_path)

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_worktree_info_is_frozen(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify WorktreeInfo instances are immutable."""
        porcelain_output = (
            "worktree /path/to/main\n"
            "HEAD abc1234567890\n"
            "branch refs/heads/main\n"
        )
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout=porcelain_output,
            stderr="",
        )

        result = list_worktrees(tmp_path)

        with pytest.raises(Exception):  # noqa: B017
            result[0].branch = "other"  # type: ignore[misc]

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_whitespace_only_output_returns_empty_list(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify whitespace-only output returns empty list."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "list", "--porcelain"],
            returncode=0,
            stdout="   \n\n  ",
            stderr="",
        )

        result = list_worktrees(tmp_path)

        assert result == []


# ============================================================================
# prune_worktrees Tests
# ============================================================================


class TestPruneWorktrees:
    """Test prune_worktrees function."""

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_calls_git_worktree_prune(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify correct git command is called."""
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "prune"],
            returncode=0,
            stdout="",
            stderr="",
        )

        prune_worktrees(tmp_path)

        mock_run_git.assert_called_once_with(
            ["worktree", "prune"],
            cwd=tmp_path,
        )

    @patch("bmad_assist_lite.parallel.worktree_manager._run_git")
    def test_propagates_parallel_error(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify ParallelError from _run_git is propagated."""
        mock_run_git.side_effect = ParallelError("git worktree prune failed")

        with pytest.raises(ParallelError, match="git worktree prune failed"):
            prune_worktrees(tmp_path)


# ============================================================================
# WorktreeInfo Model Tests
# ============================================================================


class TestWorktreeInfoModel:
    """Test WorktreeInfo Pydantic model."""

    def test_create_with_branch(self) -> None:
        """Verify WorktreeInfo can be created with a branch."""
        info = WorktreeInfo(
            path=Path("/repo"),
            branch="main",
            commit="abc123",
        )
        assert info.path == Path("/repo")
        assert info.branch == "main"
        assert info.commit == "abc123"

    def test_create_with_none_branch(self) -> None:
        """Verify WorktreeInfo can be created with branch=None."""
        info = WorktreeInfo(
            path=Path("/repo"),
            branch=None,
            commit="abc123",
        )
        assert info.branch is None

    def test_frozen_model(self) -> None:
        """Verify WorktreeInfo is immutable."""
        info = WorktreeInfo(
            path=Path("/repo"),
            branch="main",
            commit="abc123",
        )
        with pytest.raises(Exception):  # noqa: B017
            info.commit = "def456"  # type: ignore[misc]

    def test_path_is_pathlib(self) -> None:
        """Verify path field is a pathlib.Path instance."""
        info = WorktreeInfo(
            path=Path("/repo"),
            branch="main",
            commit="abc123",
        )
        assert isinstance(info.path, Path)
