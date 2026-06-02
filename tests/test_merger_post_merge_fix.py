"""Tests for post-merge fix quality gate and sprint status update in merger.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.merger import (
    MergeQueue,
    MergeResult,
    PostMergeQGResult,
    run_post_merge_fix,
    update_sprint_status_done,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    """Build a mock subprocess.CompletedProcess-like object."""
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _make_popen_mock(
    returncode: int = 0,
    stdout: str = "fix applied",
    stderr: str = "",
    timeout: bool = False,
) -> MagicMock:
    """Build a mock subprocess.Popen instance."""
    import subprocess

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode

    if timeout:
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=300)
    else:
        proc.communicate.return_value = (stdout, stderr)

    proc.poll.return_value = returncode
    return proc


# ============================================================================
# run_post_merge_fix() Tests (Task 5.1-5.7, 5.16, 5.18)
# ============================================================================


class TestRunPostMergeFix:
    """Test run_post_merge_fix() function."""

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_copies_failure_report_to_handler_path(
        self,
        mock_popen: MagicMock,
        mock_git: MagicMock,
        mock_qg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify failure report is copied from post-merge path to handler path."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)
        report = cache_dir / "post-merge-qg-failures-3.1.md"
        report.write_text("# Failures\n## Lint failed", encoding="utf-8")

        mock_popen.return_value = _make_popen_mock()
        mock_git.return_value = _make_completed_process(stdout="1 file changed")
        mock_qg.return_value = PostMergeQGResult(all_passed=True, story_id="3.1")

        run_post_merge_fix("3.1", tmp_path)

        handler_report = cache_dir / "qa-failures-3.1.md"
        assert handler_report.exists()
        assert "Lint failed" in handler_report.read_text(encoding="utf-8")

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_spawns_fix_subprocess_with_correct_args(
        self,
        mock_popen: MagicMock,
        mock_git: MagicMock,
        mock_qg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify fix subprocess is spawned with --fix-post-merge and correct args."""
        mock_popen.return_value = _make_popen_mock()
        mock_git.return_value = _make_completed_process(stdout="1 file changed")
        mock_qg.return_value = PostMergeQGResult(all_passed=True, story_id="3.1")

        run_post_merge_fix("3.1", tmp_path)

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "--fix-post-merge" in cmd
        assert "--epic" in cmd
        assert "3" in cmd
        assert "--story" in cmd
        assert "1" in cmd
        assert "--attempt" in cmd

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_commits_with_correct_tagged_message(
        self,
        mock_popen: MagicMock,
        mock_git: MagicMock,
        mock_qg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test 5.3: Verify commit message uses the correct tagged format."""
        mock_popen.return_value = _make_popen_mock()
        # diff --stat returns changes, then add + commit succeed
        mock_git.return_value = _make_completed_process(stdout="1 file changed")
        mock_qg.return_value = PostMergeQGResult(all_passed=True, story_id="3.1")

        run_post_merge_fix("3.1", tmp_path)

        # Find the commit call
        commit_calls = [
            c for c in mock_git.call_args_list
            if c[0][0] == ["commit", "-m", "fix: post-merge integration fix for story 3.1"]
        ]
        assert len(commit_calls) == 1

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_reruns_qg_after_fix_and_returns_result(
        self,
        mock_popen: MagicMock,
        mock_git: MagicMock,
        mock_qg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test 5.4: Verify QG is re-run after fix and its result is returned."""
        mock_popen.return_value = _make_popen_mock()
        mock_git.return_value = _make_completed_process(stdout="1 file changed")
        expected_qg = PostMergeQGResult(all_passed=True, story_id="3.1", duration_ms=500)
        mock_qg.return_value = expected_qg

        result = run_post_merge_fix("3.1", tmp_path)

        assert result is expected_qg
        mock_qg.assert_called_once_with("3.1", tmp_path, None)

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_handles_missing_failure_report_gracefully(
        self,
        mock_popen: MagicMock,
        mock_git: MagicMock,
        mock_qg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test 5.5: Verify missing failure report doesn't crash; still attempts fix."""
        # No failure report file created
        mock_popen.return_value = _make_popen_mock()
        mock_git.return_value = _make_completed_process(stdout="1 file changed")
        mock_qg.return_value = PostMergeQGResult(all_passed=True, story_id="3.1")

        result = run_post_merge_fix("3.1", tmp_path)

        # Fix was still attempted
        mock_popen.assert_called_once()
        assert result.all_passed is True

    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_handles_claude_cli_timeout(
        self,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test 5.6: Verify Claude CLI timeout returns all_passed=False."""
        mock_popen.return_value = _make_popen_mock(timeout=True)

        result = run_post_merge_fix("3.1", tmp_path)

        assert result.all_passed is False
        assert result.story_id == "3.1"

    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_handles_claude_cli_failure(
        self,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test 5.7: Verify Claude CLI non-zero exit returns all_passed=False."""
        mock_popen.return_value = _make_popen_mock(returncode=1, stderr="CLI error")

        result = run_post_merge_fix("3.1", tmp_path)

        assert result.all_passed is False
        assert result.story_id == "3.1"

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_attempt_number_passed_to_subprocess(
        self,
        mock_popen: MagicMock,
        mock_git: MagicMock,
        mock_qg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify attempt number is passed as --attempt flag to subprocess."""
        mock_popen.return_value = _make_popen_mock()
        mock_git.return_value = _make_completed_process(stdout="1 file changed")
        mock_qg.return_value = PostMergeQGResult(all_passed=True, story_id="3.1")

        run_post_merge_fix("3.1", tmp_path, attempt=2)

        cmd = mock_popen.call_args[0][0]
        attempt_idx = cmd.index("--attempt")
        assert cmd[attempt_idx + 1] == "2"

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_no_retry_context_on_first_attempt(
        self,
        mock_popen: MagicMock,
        mock_git: MagicMock,
        mock_qg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify retry context is NOT included when attempt == 1."""
        mock_popen.return_value = _make_popen_mock()
        mock_git.return_value = _make_completed_process(stdout="1 file changed")
        mock_qg.return_value = PostMergeQGResult(all_passed=True, story_id="3.1")

        run_post_merge_fix("3.1", tmp_path, attempt=1)

        call_args = mock_popen.return_value.communicate.call_args
        prompt = call_args[1].get("input", call_args[0][0] if call_args[0] else "")
        assert "<retry-context>" not in prompt

    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_empty_status_treated_as_failed_attempt(
        self,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test 5.18: Verify no changes (empty git status) is treated as failed attempt."""
        mock_popen.return_value = _make_popen_mock()

        with patch("bmad_assist_lite.parallel.merger._run_git") as mock_git:
            # git status --porcelain returns empty output (no changes)
            mock_git.return_value = _make_completed_process(stdout="")

            result = run_post_merge_fix("3.1", tmp_path)

        assert result.all_passed is False
        assert result.story_id == "3.1"

    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_subprocess_not_found_raises_parallel_error(
        self,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify FileNotFoundError for subprocess raises ParallelError."""
        from bmad_assist_lite.parallel.exceptions import ParallelError

        mock_popen.side_effect = FileNotFoundError("python not found")

        with pytest.raises(ParallelError, match="Python executable"):
            run_post_merge_fix("3.1", tmp_path)

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_log_uses_fix_qg_prefix(
        self,
        mock_popen: MagicMock,
        mock_git: MagicMock,
        mock_qg: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test 1.8: Verify logging uses [FIX-QG|post-merge|{story_id}] prefix."""
        mock_popen.return_value = _make_popen_mock()
        mock_git.return_value = _make_completed_process(stdout="1 file changed")
        mock_qg.return_value = PostMergeQGResult(all_passed=True, story_id="3.1")

        with caplog.at_level("INFO"):
            run_post_merge_fix("3.1", tmp_path)

        assert any("[FIX-QG|post-merge|3.1]" in r.message for r in caplog.records)

    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_subprocess_receives_parallel_mode_env(
        self,
        mock_popen: MagicMock,
        mock_git: MagicMock,
        mock_qg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify subprocess receives BMAD_PARALLEL_MODE=1 in environment."""
        mock_popen.return_value = _make_popen_mock()
        mock_git.return_value = _make_completed_process(stdout="M file.py")
        mock_qg.return_value = PostMergeQGResult(all_passed=True, story_id="3.1")

        run_post_merge_fix("3.1", tmp_path)

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["env"]["BMAD_PARALLEL_MODE"] == "1"

    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    def test_git_commit_failure_returns_all_passed_false(
        self,
        mock_popen: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify git commit failure returns PostMergeQGResult(all_passed=False)."""
        from bmad_assist_lite.parallel.exceptions import ParallelError

        mock_popen.return_value = _make_popen_mock()

        with patch("bmad_assist_lite.parallel.merger._run_git") as mock_git:
            # First call: git status --porcelain returns changes
            # Second call: git add -A succeeds
            # Third call: git commit fails
            mock_git.side_effect = [
                _make_completed_process(stdout="M file.py"),  # status --porcelain
                _make_completed_process(),  # add -A
                ParallelError("commit failed"),  # commit
            ]

            result = run_post_merge_fix("3.1", tmp_path)

        assert result.all_passed is False
        assert result.story_id == "3.1"


# ============================================================================
# process_merge_with_fix() Tests (Task 5.8-5.12, 5.17)
# ============================================================================


class TestProcessMergeWithFix:
    """Test MergeQueue.process_merge_with_fix() orchestration method."""

    @patch("bmad_assist_lite.parallel.merger.update_sprint_status_done")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_fix")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_returns_immediately_on_qg_pass(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
        mock_fix: MagicMock,
        mock_sprint: MagicMock,
    ) -> None:
        """Test 5.8: Verify immediate return when QG passes (no fix needed)."""
        qg_result = PostMergeQGResult(all_passed=True, story_id="3.1")
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = qg_result

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")

        result = await queue.process_merge_with_fix()

        assert result is not None
        assert result.success is True
        assert result.qg_result is not None
        assert result.qg_result.all_passed is True
        mock_fix.assert_not_called()
        # Sprint status should be updated on success
        mock_sprint.assert_called_once()
        assert mock_sprint.call_args[0][:2] == ("3.1", Path("/repo"))

    @patch("bmad_assist_lite.parallel.merger.update_sprint_status_done")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_fix")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_invokes_fix_when_qg_fails(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
        mock_fix: MagicMock,
        mock_sprint: MagicMock,
    ) -> None:
        """Test 5.9: Verify fix is invoked when QG fails, then re-runs QG."""
        qg_fail = PostMergeQGResult(all_passed=False, story_id="3.1")
        qg_pass = PostMergeQGResult(all_passed=True, story_id="3.1")
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = qg_fail
        mock_fix.return_value = qg_pass

        config = ParallelConfig(post_merge_fix_retries=1)
        queue = MergeQueue(
            project_root=Path("/repo"),
            parallel_config=config,
        )
        await queue.enqueue("3.1")

        result = await queue.process_merge_with_fix()

        assert result is not None
        assert result.qg_result is not None
        assert result.qg_result.all_passed is True
        mock_fix.assert_called_once()
        mock_sprint.assert_called_once()
        assert mock_sprint.call_args[0][:2] == ("3.1", Path("/repo"))

    @patch("bmad_assist_lite.parallel.merger.update_sprint_status_done")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_fix")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_respects_post_merge_fix_retries_limit(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
        mock_fix: MagicMock,
        mock_sprint: MagicMock,
    ) -> None:
        """Test 5.10: Verify retry count respects post_merge_fix_retries."""
        qg_fail = PostMergeQGResult(all_passed=False, story_id="3.1")
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = qg_fail
        mock_fix.return_value = qg_fail  # Fix never succeeds

        config = ParallelConfig(post_merge_fix_retries=3)
        queue = MergeQueue(
            project_root=Path("/repo"),
            parallel_config=config,
        )
        await queue.enqueue("3.1")

        result = await queue.process_merge_with_fix()

        assert result is not None
        assert result.qg_result is not None
        assert result.qg_result.all_passed is False
        assert mock_fix.call_count == 3
        mock_sprint.assert_not_called()

    @patch("bmad_assist_lite.parallel.merger.update_sprint_status_done")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_fix")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_returns_blocked_ready_result_when_retries_exhausted(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
        mock_fix: MagicMock,
        mock_sprint: MagicMock,
    ) -> None:
        """Test 5.11: Verify blocked-ready result when all retries exhausted."""
        qg_fail = PostMergeQGResult(all_passed=False, story_id="3.1")
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = qg_fail
        mock_fix.return_value = qg_fail

        config = ParallelConfig(post_merge_fix_retries=1)
        queue = MergeQueue(
            project_root=Path("/repo"),
            parallel_config=config,
        )
        await queue.enqueue("3.1")

        result = await queue.process_merge_with_fix()

        assert result is not None
        assert result.success is True  # Merge succeeded
        assert result.qg_result is not None
        assert result.qg_result.all_passed is False  # QG still failing
        mock_sprint.assert_not_called()

    @patch("bmad_assist_lite.parallel.merger.update_sprint_status_done")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_fix")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_skips_fix_when_merge_fails(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
        mock_fix: MagicMock,
        mock_sprint: MagicMock,
    ) -> None:
        """Test 5.12: Verify fix is skipped when merge itself fails."""
        mock_merge.return_value = MergeResult(
            success=False,
            story_id="3.1",
            error="Merge conflict",
        )

        config = ParallelConfig(post_merge_fix_retries=1)
        queue = MergeQueue(
            project_root=Path("/repo"),
            parallel_config=config,
        )
        await queue.enqueue("3.1")

        result = await queue.process_merge_with_fix()

        assert result is not None
        assert result.success is False
        mock_fix.assert_not_called()
        mock_qg.assert_not_called()

    @patch("bmad_assist_lite.parallel.merger.update_sprint_status_done")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_fix")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_retries_zero_skips_fix_loop(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
        mock_fix: MagicMock,
        mock_sprint: MagicMock,
    ) -> None:
        """Test 5.17: Verify post_merge_fix_retries=0 skips fix loop entirely."""
        qg_fail = PostMergeQGResult(all_passed=False, story_id="3.1")
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = qg_fail

        config = ParallelConfig(post_merge_fix_retries=0)
        queue = MergeQueue(
            project_root=Path("/repo"),
            parallel_config=config,
        )
        await queue.enqueue("3.1")

        result = await queue.process_merge_with_fix()

        assert result is not None
        assert result.qg_result is not None
        assert result.qg_result.all_passed is False
        mock_fix.assert_not_called()
        mock_sprint.assert_not_called()

    @patch("bmad_assist_lite.parallel.merger.update_sprint_status_done")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_returns_none_when_queue_empty(
        self,
        mock_merge: MagicMock,
        mock_sprint: MagicMock,
    ) -> None:
        """Verify None is returned when queue is empty."""
        queue = MergeQueue(project_root=Path("/repo"))

        result = await queue.process_merge_with_fix()

        assert result is None

    @patch("bmad_assist_lite.parallel.merger.update_sprint_status_done")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_fix")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_default_retries_is_one(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
        mock_fix: MagicMock,
        mock_sprint: MagicMock,
    ) -> None:
        """Verify default post_merge_fix_retries is 1 when no parallel_config."""
        qg_fail = PostMergeQGResult(all_passed=False, story_id="3.1")
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = qg_fail
        mock_fix.return_value = qg_fail  # Fix fails

        queue = MergeQueue(project_root=Path("/repo"))
        await queue.enqueue("3.1")

        result = await queue.process_merge_with_fix()

        assert result is not None
        assert mock_fix.call_count == 1  # Default of 1 retry

    @patch("bmad_assist_lite.parallel.merger.update_sprint_status_done")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_fix")
    @patch("bmad_assist_lite.parallel.merger.run_post_merge_qg")
    @patch("bmad_assist_lite.parallel.merger.merge_story")
    async def test_fix_succeeds_on_second_attempt(
        self,
        mock_merge: MagicMock,
        mock_qg: MagicMock,
        mock_fix: MagicMock,
        mock_sprint: MagicMock,
    ) -> None:
        """Verify fix loop stops after successful attempt."""
        qg_fail = PostMergeQGResult(all_passed=False, story_id="3.1")
        qg_pass = PostMergeQGResult(all_passed=True, story_id="3.1")
        mock_merge.return_value = MergeResult(success=True, story_id="3.1")
        mock_qg.return_value = qg_fail
        mock_fix.side_effect = [qg_fail, qg_pass]  # Fails first, passes second

        config = ParallelConfig(post_merge_fix_retries=3)
        queue = MergeQueue(
            project_root=Path("/repo"),
            parallel_config=config,
        )
        await queue.enqueue("3.1")

        result = await queue.process_merge_with_fix()

        assert result is not None
        assert result.qg_result is not None
        assert result.qg_result.all_passed is True
        assert mock_fix.call_count == 2  # Stopped after success
        mock_sprint.assert_called_once()
        assert mock_sprint.call_args[0][:2] == ("3.1", Path("/repo"))


# ============================================================================
# update_sprint_status_done() Tests (Task 5.13-5.15)
# ============================================================================


class TestUpdateSprintStatusDone:
    """Test update_sprint_status_done() helper function."""

    @patch("bmad_assist_lite.core.sprint_status.save_sprint_status")
    @patch("bmad_assist_lite.core.sprint_status.load_sprint_status")
    @patch("bmad_assist_lite.core.sprint_status.get_sprint_status_path")
    def test_loads_updates_and_saves(
        self,
        mock_path: MagicMock,
        mock_load: MagicMock,
        mock_save: MagicMock,
    ) -> None:
        """Test 5.13: Verify load, update, and save flow."""
        from bmad_assist_lite.core.sprint_status import SprintStatus

        mock_path.return_value = Path("/repo/_bmad-output/sprint-status.yaml")
        sprint = SprintStatus(development_status={"story-3-1": "in-progress"})
        mock_load.return_value = sprint

        update_sprint_status_done("3.1", Path("/repo"))

        mock_path.assert_called_once_with(Path("/repo"))
        mock_load.assert_called_once_with(Path("/repo/_bmad-output/sprint-status.yaml"))
        mock_save.assert_called_once()
        # Verify the status was set to "done"
        assert sprint.get_story_status("3.1") == "done"

    @patch("bmad_assist_lite.core.sprint_status.load_sprint_status")
    @patch("bmad_assist_lite.core.sprint_status.get_sprint_status_path")
    def test_handles_missing_sprint_status_file(
        self,
        mock_path: MagicMock,
        mock_load: MagicMock,
    ) -> None:
        """Test 5.14: Verify graceful handling of missing sprint-status file."""
        from bmad_assist_lite.core.sprint_status import SprintStatus

        mock_path.return_value = Path("/repo/_bmad-output/sprint-status.yaml")
        mock_load.return_value = SprintStatus()  # Empty status

        # Should not raise
        update_sprint_status_done("3.1", Path("/repo"))

    @patch("bmad_assist_lite.core.sprint_status.get_sprint_status_path")
    def test_catches_and_logs_errors_without_raising(
        self,
        mock_path: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test 5.15: Verify errors are caught and logged, not raised."""
        mock_path.side_effect = OSError("disk error")

        with caplog.at_level("WARNING"):
            # Should NOT raise
            update_sprint_status_done("3.1", Path("/repo"))

        assert any("[SPRINT|3.1]" in r.message for r in caplog.records)
        assert any("non-fatal" in r.message for r in caplog.records)

    @patch("bmad_assist_lite.core.sprint_status.save_sprint_status")
    @patch("bmad_assist_lite.core.sprint_status.load_sprint_status")
    @patch("bmad_assist_lite.core.sprint_status.get_sprint_status_path")
    def test_log_uses_sprint_prefix(
        self,
        mock_path: MagicMock,
        mock_load: MagicMock,
        mock_save: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify logging uses [SPRINT|{story_id}] prefix."""
        from bmad_assist_lite.core.sprint_status import SprintStatus

        mock_path.return_value = Path("/repo/_bmad-output/sprint-status.yaml")
        mock_load.return_value = SprintStatus()

        with caplog.at_level("INFO"):
            update_sprint_status_done("3.1", Path("/repo"))

        assert any("[SPRINT|3.1]" in r.message for r in caplog.records)

    @patch("bmad_assist_lite.core.sprint_status.save_sprint_status")
    @patch("bmad_assist_lite.core.sprint_status.load_sprint_status")
    @patch("bmad_assist_lite.core.sprint_status.get_sprint_status_path")
    def test_save_failure_is_nonfatal(
        self,
        mock_path: MagicMock,
        mock_load: MagicMock,
        mock_save: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify save_sprint_status failure is non-fatal."""
        from bmad_assist_lite.core.sprint_status import SprintStatus

        mock_path.return_value = Path("/repo/_bmad-output/sprint-status.yaml")
        mock_load.return_value = SprintStatus()
        mock_save.side_effect = OSError("write failed")

        with caplog.at_level("WARNING"):
            update_sprint_status_done("3.1", Path("/repo"))

        assert any("non-fatal" in r.message for r in caplog.records)
