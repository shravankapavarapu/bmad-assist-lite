"""Tests for the sequential merge queue and git merge module."""

import asyncio
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from pydantic import ValidationError

from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.merger import (
    MergeQueue,
    MergeResult,
    merge_story,
)


# ============================================================================
# MergeResult Model Tests (Task 6.1)
# ============================================================================


class TestMergeResultModel:
    """Test MergeResult frozen Pydantic model validation."""

    def test_success_result_creation(self) -> None:
        """Verify MergeResult can be created for a successful merge."""
        result = MergeResult(success=True, story_id="3.1")

        assert result.success is True
        assert result.story_id == "3.1"
        assert result.conflict_files == []
        assert result.error is None

    def test_conflict_result_creation(self) -> None:
        """Verify MergeResult can be created for a conflict merge."""
        result = MergeResult(
            success=False,
            story_id="3.2",
            conflict_files=["src/main.py", "src/utils.py"],
            error="Merge conflict in 2 file(s)",
        )

        assert result.success is False
        assert result.story_id == "3.2"
        assert result.conflict_files == ["src/main.py", "src/utils.py"]
        assert result.error == "Merge conflict in 2 file(s)"

    def test_frozen_model_is_immutable(self) -> None:
        """Verify MergeResult raises on attribute assignment (frozen)."""
        result = MergeResult(success=True, story_id="3.1")

        with pytest.raises(ValidationError):
            result.success = False  # type: ignore[misc]

    def test_missing_required_fields_raises(self) -> None:
        """Verify MergeResult requires success and story_id."""
        with pytest.raises(ValidationError):
            MergeResult()  # type: ignore[call-arg]

    def test_default_conflict_files_empty_list(self) -> None:
        """Verify conflict_files defaults to empty list."""
        result = MergeResult(success=True, story_id="3.1")
        assert result.conflict_files == []

    def test_default_error_is_none(self) -> None:
        """Verify error defaults to None."""
        result = MergeResult(success=True, story_id="3.1")
        assert result.error is None


# ============================================================================
# merge_story() — Clean Merge Path Tests (Task 6.2)
# ============================================================================


def _make_completed(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Helper to build a CompletedProcess for mocking _run_git."""
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestMergeStoryCleanMerge:
    """Test merge_story() when the merge succeeds (no conflicts)."""

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_clean_merge_returns_success(
        self,
        mock_run_git: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Verify merge_story returns success=True on clean merge."""
        mock_run_git.side_effect = [
            # git rev-parse --abbrev-ref HEAD
            _make_completed(stdout="feature/epic-3\n"),
            # git merge --no-edit parallel/3-1
            _make_completed(stdout="Merge made by the 'ort' strategy.\n"),
            # git branch -d parallel/3-1
            _make_completed(stdout="Deleted branch parallel/3-1\n"),
        ]

        result = merge_story("3.1", Path("/repo"))

        assert result.success is True
        assert result.story_id == "3.1"
        assert result.conflict_files == []
        assert result.error is None

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_branch_deletion_called_after_merge(
        self,
        mock_run_git: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Verify git branch -d is called after successful merge."""
        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(),
            _make_completed(),
        ]

        merge_story("3.1", Path("/repo"))

        # Third call should be branch deletion
        branch_delete_call = mock_run_git.call_args_list[2]
        assert branch_delete_call == call(
            ["branch", "-d", "parallel/3-1"], cwd=Path("/repo"), check=False
        )

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_merge_called_with_no_edit(
        self,
        mock_run_git: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Verify git merge is called with --no-edit and check=False."""
        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(),
            _make_completed(),
        ]

        merge_story("3.1", Path("/repo"))

        merge_call = mock_run_git.call_args_list[1]
        assert merge_call == call(
            ["merge", "--no-edit", "parallel/3-1"],
            cwd=Path("/repo"),
            check=False,
        )

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_story_id_normalization(
        self,
        mock_run_git: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Verify dots in story_id are normalized to dashes in branch name."""
        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(),
            _make_completed(),
        ]

        merge_story("3.2", Path("/repo"))

        merge_call = mock_run_git.call_args_list[1]
        assert merge_call == call(
            ["merge", "--no-edit", "parallel/3-2"],
            cwd=Path("/repo"),
            check=False,
        )


# ============================================================================
# merge_story() — Conflict Path Tests (Task 6.3)
# ============================================================================


class TestMergeStoryConflict:
    """Test merge_story() when a merge conflict occurs."""

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_conflict_returns_failure_with_files(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify conflict path returns success=False with conflict file list."""
        # Create .git/MERGE_HEAD to signal conflict state
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            # git rev-parse --abbrev-ref HEAD
            _make_completed(stdout="feature/epic-3\n"),
            # git merge --no-edit parallel/3-1 (conflict)
            _make_completed(
                returncode=1,
                stdout="CONFLICT (content): Merge conflict in src/main.py\n",
            ),
            # git diff --name-only --diff-filter=U
            _make_completed(stdout="src/main.py\nsrc/utils.py\n"),
            # git merge --abort
            _make_completed(),
        ]

        result = merge_story("3.1", tmp_path)

        assert result.success is False
        assert result.story_id == "3.1"
        assert result.conflict_files == ["src/main.py", "src/utils.py"]
        assert result.error is not None

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_merge_abort_called_after_conflict(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify git merge --abort is called to restore clean state."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(returncode=1, stdout="CONFLICT (content)\n"),
            _make_completed(stdout="src/main.py\n"),
            _make_completed(),
        ]

        merge_story("3.1", tmp_path)

        # Fourth call should be merge --abort
        abort_call = mock_run_git.call_args_list[3]
        assert abort_call == call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_merge_abort_guaranteed_via_finally(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify git merge --abort runs even if diff command fails."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(returncode=1, stdout="CONFLICT (content)\n"),
            # git diff fails with exception
            ParallelError("git diff failed"),
            # git merge --abort should still run
            _make_completed(),
        ]

        with pytest.raises(ParallelError, match="git diff failed"):
            merge_story("3.1", tmp_path)

        # merge --abort must have been called despite the exception
        abort_call = mock_run_git.call_args_list[3]
        assert abort_call == call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_conflict_detected_via_merge_head_file(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify conflict detected by .git/MERGE_HEAD existence."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            # No CONFLICT in stdout, but MERGE_HEAD exists
            _make_completed(returncode=1, stdout="", stderr=""),
            _make_completed(stdout=""),
            _make_completed(),
        ]

        result = merge_story("3.1", tmp_path)

        assert result.success is False

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_conflict_detected_via_stdout_keyword(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify conflict detected by CONFLICT keyword in stdout."""
        # No .git/MERGE_HEAD file — but CONFLICT in output
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(
                returncode=1,
                stdout="CONFLICT (content): Merge conflict in foo.py\n",
            ),
            _make_completed(stdout="foo.py\n"),
            _make_completed(),
        ]

        result = merge_story("3.1", tmp_path)

        assert result.success is False
        assert result.conflict_files == ["foo.py"]

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_empty_conflict_file_list(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify empty diff output results in empty conflict_files list."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(returncode=1, stdout="CONFLICT\n"),
            _make_completed(stdout=""),
            _make_completed(),
        ]

        result = merge_story("3.1", tmp_path)

        assert result.conflict_files == []


# ============================================================================
# merge_story() — Base Branch Verification Tests (Task 6.11)
# ============================================================================


class TestMergeStoryBaseBranchVerification:
    """Test base branch verification before merge."""

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_detached_head_raises_parallel_error(
        self,
        mock_run_git: MagicMock,
    ) -> None:
        """Verify ParallelError when HEAD is detached."""
        mock_run_git.return_value = _make_completed(stdout="HEAD\n")

        with pytest.raises(ParallelError, match="HEAD is detached"):
            merge_story("3.1", Path("/repo"))

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_wrong_branch_raises_parallel_error(
        self,
        mock_run_git: MagicMock,
    ) -> None:
        """Verify ParallelError when HEAD is on the wrong branch."""
        mock_run_git.return_value = _make_completed(stdout="feature/other\n")

        with pytest.raises(ParallelError, match="expected 'main'"):
            merge_story("3.1", Path("/repo"), expected_branch="main")

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_correct_expected_branch_proceeds(
        self,
        mock_run_git: MagicMock,
    ) -> None:
        """Verify merge proceeds when on the correct expected branch."""
        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(),
            _make_completed(),
        ]

        with patch("bmad_assist_lite.parallel.merger.cleanup_worktree"):
            result = merge_story("3.1", Path("/repo"), expected_branch="main")

        assert result.success is True

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_no_expected_branch_skips_branch_check(
        self,
        mock_run_git: MagicMock,
    ) -> None:
        """Verify merge proceeds on any branch when expected_branch is None."""
        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(),
            _make_completed(),
        ]

        with patch("bmad_assist_lite.parallel.merger.cleanup_worktree"):
            result = merge_story("3.1", Path("/repo"))

        assert result.success is True


# ============================================================================
# merge_story() — Fatal Git Error Tests (Task 6.10, 6.12)
# ============================================================================


class TestMergeStoryFatalError:
    """Test merge_story() with fatal git errors (non-conflict)."""

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_nonexistent_branch_raises_parallel_error(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify ParallelError when branch doesn't exist (not a conflict)."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        # No MERGE_HEAD → not a conflict

        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(
                returncode=1,
                stderr="merge: parallel/9-9 - not something we can merge\n",
            ),
        ]

        with pytest.raises(ParallelError, match="not a conflict"):
            merge_story("9.9", tmp_path)

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_fatal_git_error_raises_parallel_error(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify fatal git errors raise ParallelError instead of treating as conflict."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(
                returncode=128,
                stderr="fatal: not a git repository\n",
            ),
        ]

        with pytest.raises(ParallelError, match="not a conflict"):
            merge_story("3.1", tmp_path)


# ============================================================================
# Worktree Cleanup Tests (Task 6.8, 6.9)
# ============================================================================


class TestMergeStoryWorktreeCleanup:
    """Test worktree cleanup after successful merge."""

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_cleanup_worktree_called_after_success(
        self,
        mock_run_git: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Verify cleanup_worktree is called after successful merge."""
        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(),
            _make_completed(),
        ]

        merge_story("3.1", Path("/repo"))

        mock_cleanup.assert_called_once_with("3.1", Path("/repo"))

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_cleanup_failure_is_nonfatal(
        self,
        mock_run_git: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Verify cleanup failure is logged as warning, not raised."""
        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(),
            _make_completed(),
        ]
        mock_cleanup.side_effect = ParallelError("cleanup failed")

        # Should not raise
        result = merge_story("3.1", Path("/repo"))

        assert result.success is True
        mock_cleanup.assert_called_once()

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_cleanup_failure_logged_as_warning(
        self,
        mock_run_git: MagicMock,
        mock_cleanup: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify cleanup failure produces a warning log message."""
        mock_run_git.side_effect = [
            _make_completed(stdout="feature/epic-3\n"),
            _make_completed(),
            _make_completed(),
        ]
        mock_cleanup.side_effect = RuntimeError("disk error")

        with caplog.at_level("WARNING"):
            merge_story("3.1", Path("/repo"))

        assert any("cleanup failed" in r.message.lower() or "non-fatal" in r.message.lower()
                    for r in caplog.records)


# ============================================================================
# Branch Deletion Resilience Tests
# ============================================================================


class TestMergeStoryBranchDeletion:
    """Test branch deletion resilience on clean merge path."""

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_branch_deletion_failure_is_nonfatal(
        self,
        mock_run_git: MagicMock,
        mock_cleanup: MagicMock,
    ) -> None:
        """Verify branch deletion failure does not crash the merge pipeline."""
        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(),  # merge succeeds
            _make_completed(returncode=1, stderr="error: branch not found\n"),
        ]

        result = merge_story("3.1", Path("/repo"))

        assert result.success is True
        mock_cleanup.assert_called_once()

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_branch_deletion_failure_logged_as_warning(
        self,
        mock_run_git: MagicMock,
        mock_cleanup: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify branch deletion failure produces a warning log."""
        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(),
            _make_completed(returncode=1, stderr="error: branch not found\n"),
        ]

        with caplog.at_level("WARNING"):
            merge_story("3.1", Path("/repo"))

        assert any(
            "branch deletion failed" in r.message.lower()
            for r in caplog.records
        )


# ============================================================================
# Merge Abort Failure Logging Tests
# ============================================================================


class TestMergeAbortFailure:
    """Test that merge --abort failure is logged as warning."""

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_abort_failure_logged_as_warning(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify merge --abort failure produces a warning about dirty state."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(returncode=1, stdout="CONFLICT\n"),
            _make_completed(stdout="src/main.py\n"),
            _make_completed(returncode=1, stderr="error: abort failed\n"),
        ]

        with caplog.at_level("WARNING"):
            result = merge_story("3.1", tmp_path)

        assert result.success is False
        assert any(
            "dirty state" in r.message.lower()
            for r in caplog.records
        )


# ============================================================================
# Empty Conflict Files Error Message Test
# ============================================================================


class TestMergeConflictErrorMessage:
    """Test conflict error messages."""

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_empty_conflict_files_has_descriptive_error(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify empty conflict file list produces descriptive error, not 0 file(s)."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(returncode=1, stdout="CONFLICT\n"),
            _make_completed(stdout=""),
            _make_completed(),
        ]

        result = merge_story("3.1", tmp_path)

        assert result.error is not None
        assert "could not be determined" in result.error


# ============================================================================
# MergeQueue — Enqueue Tests (Task 6.4)
# ============================================================================


class TestMergeQueueEnqueue:
    """Test MergeQueue.enqueue() adds stories to the queue."""

    async def test_enqueue_adds_story(self) -> None:
        """Verify enqueue puts a story_id into the internal queue."""
        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")

        assert queue._queue.qsize() == 1

    async def test_enqueue_multiple_stories(self) -> None:
        """Verify multiple stories can be enqueued in FIFO order."""
        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")
        await queue.enqueue("3.2")
        await queue.enqueue("3.3")

        assert queue._queue.qsize() == 3

    async def test_enqueue_preserves_order(self) -> None:
        """Verify stories are dequeued in FIFO order."""
        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")
        await queue.enqueue("3.2")

        first = queue._queue.get_nowait()
        second = queue._queue.get_nowait()

        assert first == "3.1"
        assert second == "3.2"


# ============================================================================
# MergeQueue — process_next() Tests (Task 6.5, 6.7)
# ============================================================================


class TestMergeQueueProcessNext:
    """Test MergeQueue.process_next() merge execution."""

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_process_next_calls_merge_story(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
    ) -> None:
        """Verify process_next calls merge_story with correct arguments."""
        from bmad_assist_lite.parallel.merger import PostMergeQGResult

        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = PostMergeQGResult(
            all_passed=True, story_id="3.1",
        )

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")

        result = await queue.process_next()

        assert result is not None
        assert result.success is True
        assert result.story_id == "3.1"
        mock_merge.assert_called_once_with("3.1", Path("/repo"))

    async def test_process_next_returns_none_on_empty_queue(self) -> None:
        """Verify process_next returns None when queue is empty."""
        queue = MergeQueue(project_root=Path("/repo"))

        result = await queue.process_next()

        assert result is None

    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_process_next_returns_conflict_result(
        self,
        mock_merge: MagicMock,
    ) -> None:
        """Verify process_next returns conflict MergeResult."""
        mock_merge.return_value = MergeResult(
            success=False,
            story_id="3.2",
            conflict_files=["src/main.py"],
            error="Merge conflict in 1 file(s)",
        )

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.2")

        result = await queue.process_next()

        assert result is not None
        assert result.success is False
        assert result.conflict_files == ["src/main.py"]


# ============================================================================
# MergeQueue — Sequential Execution Tests (Task 6.6)
# ============================================================================


class TestMergeQueueSequentialExecution:
    """Test that MergeQueue enforces one-at-a-time merge execution."""

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_concurrent_process_next_executes_serially(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
    ) -> None:
        """Verify two concurrent process_next calls execute serially (never overlap)."""
        from bmad_assist_lite.parallel.merger import PostMergeQGResult

        execution_log: list[str] = []

        def slow_merge(story_id: str, project_root: Path) -> MergeResult:
            execution_log.append(f"start-{story_id}")
            time.sleep(0.05)
            execution_log.append(f"end-{story_id}")
            return MergeResult(success=True, story_id=story_id)

        mock_merge.side_effect = slow_merge
        mock_qg.side_effect = lambda sid, root, cfg=None: PostMergeQGResult(
            all_passed=True, story_id=sid,
        )

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")
        await queue.enqueue("3.2")

        # Launch two process_next calls concurrently
        results = await asyncio.gather(
            queue.process_next(),
            queue.process_next(),
        )

        # Both should return results (not None)
        assert all(r is not None for r in results)

        # The execution log must show serial execution:
        # start-X, end-X, start-Y, end-Y (never interleaved)
        assert len(execution_log) == 4
        assert execution_log[0].startswith("start-")
        assert execution_log[1].startswith("end-")
        assert execution_log[2].startswith("start-")
        assert execution_log[3].startswith("end-")

        # Verify both start/end pairs match the same story
        first_id = execution_log[0].split("-", 1)[1]
        assert execution_log[1] == f"end-{first_id}"
        second_id = execution_log[2].split("-", 1)[1]
        assert execution_log[3] == f"end-{second_id}"
        assert first_id != second_id

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_fifo_merge_ordering(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
    ) -> None:
        """Verify stories are merged in FIFO order."""
        from bmad_assist_lite.parallel.merger import PostMergeQGResult

        merged_order: list[str] = []

        def record_merge(story_id: str, project_root: Path) -> MergeResult:
            merged_order.append(story_id)
            return MergeResult(success=True, story_id=story_id)

        mock_merge.side_effect = record_merge
        mock_qg.side_effect = lambda sid, root, cfg=None: PostMergeQGResult(
            all_passed=True, story_id=sid,
        )

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")
        await queue.enqueue("3.2")
        await queue.enqueue("3.3")

        await queue.process_next()
        await queue.process_next()
        await queue.process_next()

        assert merged_order == ["3.1", "3.2", "3.3"]
