"""Tests for Claude CLI merge conflict resolution (Story 4.2)."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from pydantic import ValidationError

from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.merger import (
    ConflictResolutionResult,
    MergeResult,
    _build_resolution_prompt,
    _has_residual_markers,
    _parse_resolution_output,
    merge_story,
    resolve_conflicts,
)


# ============================================================================
# Helpers
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


def _make_claude_output(*file_pairs: tuple[str, str]) -> str:
    """Build Claude CLI output with --- FILE: ... --- / --- END FILE --- delimiters.

    Args:
        file_pairs: Tuples of (filepath, resolved_content).

    Returns:
        Formatted Claude CLI output string.

    """
    sections = []
    for filepath, content in file_pairs:
        sections.append(f"--- FILE: {filepath} ---\n{content}--- END FILE ---")
    return "\n".join(sections)


def _make_mock_popen(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> MagicMock:
    """Create a mock Popen instance with communicate returning (stdout, stderr)."""
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (stdout, stderr)
    mock_proc.returncode = returncode
    mock_proc.pid = 12345
    return mock_proc


# ============================================================================
# ConflictResolutionResult Model Tests (Task 5)
# ============================================================================


class TestConflictResolutionResultModel:
    """Test ConflictResolutionResult frozen Pydantic model validation."""

    def test_success_result_creation(self) -> None:
        """Verify ConflictResolutionResult can be created for a successful resolution."""
        result = ConflictResolutionResult(
            resolved=True,
            files_resolved=["src/main.py", "src/utils.py"],
        )

        assert result.resolved is True
        assert result.files_resolved == ["src/main.py", "src/utils.py"]
        assert result.files_with_residual_markers == []
        assert result.error is None

    def test_failure_result_creation(self) -> None:
        """Verify ConflictResolutionResult can be created for a failed resolution."""
        result = ConflictResolutionResult(
            resolved=False,
            files_with_residual_markers=["src/main.py"],
            error="Residual conflict markers in 1 file(s)",
        )

        assert result.resolved is False
        assert result.files_resolved == []
        assert result.files_with_residual_markers == ["src/main.py"]
        assert result.error is not None

    def test_frozen_model_is_immutable(self) -> None:
        """Verify ConflictResolutionResult raises on attribute assignment (frozen)."""
        result = ConflictResolutionResult(resolved=True)

        with pytest.raises(ValidationError):
            result.resolved = False  # type: ignore[misc]

    def test_missing_required_fields_raises(self) -> None:
        """Verify ConflictResolutionResult requires resolved field."""
        with pytest.raises(ValidationError):
            ConflictResolutionResult()  # type: ignore[call-arg]

    def test_default_fields(self) -> None:
        """Verify default values for optional fields."""
        result = ConflictResolutionResult(resolved=True)
        assert result.files_resolved == []
        assert result.files_with_residual_markers == []
        assert result.error is None


# ============================================================================
# Prompt Assembly Tests (Task 7.6)
# ============================================================================


class TestBuildResolutionPrompt:
    """Test _build_resolution_prompt() prompt construction."""

    def test_prompt_contains_story_context(self) -> None:
        """Verify the prompt contains story context."""
        prompt = _build_resolution_prompt(
            story_context="Story 4.2: Claude CLI resolution",
            conflict_files=["src/main.py"],
            file_contents={"src/main.py": "conflict content"},
        )

        assert "Story 4.2: Claude CLI resolution" in prompt

    def test_prompt_contains_file_list(self) -> None:
        """Verify the prompt contains all conflict file paths."""
        prompt = _build_resolution_prompt(
            story_context="Test story",
            conflict_files=["src/main.py", "src/utils.py"],
            file_contents={
                "src/main.py": "content1",
                "src/utils.py": "content2",
            },
        )

        assert "src/main.py" in prompt
        assert "src/utils.py" in prompt

    def test_prompt_contains_conflict_markers(self) -> None:
        """Verify the prompt contains the actual conflict content."""
        conflict_content = (
            "<<<<<<< HEAD\ndef old():\n=======\ndef new():\n>>>>>>> branch\n"
        )
        prompt = _build_resolution_prompt(
            story_context="Test",
            conflict_files=["src/main.py"],
            file_contents={"src/main.py": conflict_content},
        )

        assert "<<<<<<< HEAD" in prompt
        assert "def old():" in prompt
        assert "def new():" in prompt

    def test_prompt_contains_delimiter_instructions(self) -> None:
        """Verify the prompt instructs Claude to use FILE/END FILE delimiters."""
        prompt = _build_resolution_prompt(
            story_context="Test",
            conflict_files=["src/main.py"],
            file_contents={"src/main.py": "content"},
        )

        assert "--- FILE:" in prompt
        assert "--- END FILE ---" in prompt

    def test_prompt_contains_resolution_instructions(self) -> None:
        """Verify the prompt contains conflict resolution instructions."""
        prompt = _build_resolution_prompt(
            story_context="Test",
            conflict_files=["src/main.py"],
            file_contents={"src/main.py": "content"},
        )

        assert "Do NOT include conflict markers" in prompt


# ============================================================================
# Output Parser Tests (Task 7.13, 7.14)
# ============================================================================


class TestParseResolutionOutput:
    """Test _parse_resolution_output() Claude CLI output parsing."""

    def test_single_file_extraction(self) -> None:
        """Verify correct extraction of a single file's resolved content."""
        output = _make_claude_output(("src/main.py", "resolved content\n"))
        result = _parse_resolution_output(output, ["src/main.py"])

        assert "src/main.py" in result
        assert result["src/main.py"] == "resolved content\n"

    def test_multiple_file_extraction(self) -> None:
        """Verify correct extraction of multiple files' resolved content."""
        output = _make_claude_output(
            ("src/main.py", "content1\n"),
            ("src/utils.py", "content2\n"),
        )
        result = _parse_resolution_output(
            output, ["src/main.py", "src/utils.py"]
        )

        assert len(result) == 2
        assert result["src/main.py"] == "content1\n"
        assert result["src/utils.py"] == "content2\n"

    def test_output_with_conversational_filler(self) -> None:
        """Verify correct extraction when output includes filler text."""
        output = (
            "Sure, here are the resolved files:\n\n"
            + _make_claude_output(("src/main.py", "resolved\n"))
            + "\n\nLet me know if you need anything else!"
        )
        result = _parse_resolution_output(output, ["src/main.py"])

        assert result["src/main.py"] == "resolved\n"

    def test_missing_file_raises_parallel_error(self) -> None:
        """Verify ParallelError when Claude CLI returns fewer files than expected."""
        output = _make_claude_output(("src/main.py", "resolved\n"))

        with pytest.raises(ParallelError, match="missing resolution"):
            _parse_resolution_output(
                output, ["src/main.py", "src/utils.py"]
            )

    def test_empty_output_raises_parallel_error(self) -> None:
        """Verify ParallelError when Claude CLI returns no file delimiters."""
        with pytest.raises(ParallelError, match="missing resolution"):
            _parse_resolution_output("No delimiters here", ["src/main.py"])

    def test_output_with_markdown_code_fences_outside_delimiters(self) -> None:
        """Verify parsing ignores markdown code fences outside delimiters."""
        output = (
            "```\nsome fenced code\n```\n"
            + _make_claude_output(("src/main.py", "clean content\n"))
        )
        result = _parse_resolution_output(output, ["src/main.py"])

        assert result["src/main.py"] == "clean content\n"

    def test_crlf_line_endings_in_output(self) -> None:
        """Verify parser handles CRLF (\\r\\n) line endings from Windows Claude CLI."""
        output = (
            "--- FILE: src/main.py ---\r\n"
            "resolved content\r\n"
            "--- END FILE ---"
        )
        result = _parse_resolution_output(output, ["src/main.py"])

        assert "src/main.py" in result

    def test_path_normalization_backslash_to_forward(self) -> None:
        """Verify parser normalizes backslash paths from Claude output."""
        output = (
            "--- FILE: src\\main.py ---\n"
            "resolved content\n"
            "--- END FILE ---"
        )
        # conflict_files use forward slashes
        result = _parse_resolution_output(output, ["src/main.py"])

        assert "src/main.py" in result
        assert result["src/main.py"] == "resolved content\n"

    def test_conflict_files_with_backslashes_returns_original_keys(self) -> None:
        """Verify dict is keyed by original conflict_files (backslash preserved)."""
        output = _make_claude_output(("src/main.py", "resolved\n"))
        # conflict_files passed with backslashes (from Windows git)
        result = _parse_resolution_output(output, ["src\\main.py"])

        # Key should be the original backslash path for caller compatibility
        assert "src\\main.py" in result
        assert result["src\\main.py"] == "resolved\n"


# ============================================================================
# Residual Marker Detection Tests
# ============================================================================


class TestHasResidualMarkers:
    """Test _has_residual_markers() conflict detection."""

    def test_no_markers_returns_false(self) -> None:
        """Verify clean content has no residual markers."""
        assert _has_residual_markers("def hello():\n    pass\n") is False

    def test_both_markers_returns_true(self) -> None:
        """Verify content with both <<<<<<< and >>>>>>> is detected."""
        content = "<<<<<<< HEAD\ndef old():\n=======\ndef new():\n>>>>>>> branch\n"
        assert _has_residual_markers(content) is True

    def test_only_open_marker_returns_false(self) -> None:
        """Verify single <<<<<<< without >>>>>>> is not detected."""
        assert _has_residual_markers("<<<<<<< HEAD\nsome text\n") is False

    def test_only_close_marker_returns_false(self) -> None:
        """Verify single >>>>>>> without <<<<<<< is not detected."""
        assert _has_residual_markers("some text\n>>>>>>> branch\n") is False

    def test_equals_alone_not_detected(self) -> None:
        """Verify ======= alone (common in markdown) is not detected."""
        assert _has_residual_markers("Title\n=======\nContent\n") is False


# ============================================================================
# resolve_conflicts() Happy Path Tests (Task 7.1)
# ============================================================================


class TestResolveConflictsHappyPath:
    """Test resolve_conflicts() successful resolution."""

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_happy_path_resolves_and_commits(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify files are written, staged, and committed on success."""
        # Create conflicted file
        conflict_file = tmp_path / "src" / "main.py"
        conflict_file.parent.mkdir(parents=True)
        conflict_file.write_text(
            "<<<<<<< HEAD\ndef old():\n=======\ndef new():\n>>>>>>> branch\n",
            encoding="utf-8",
        )

        # Mock Claude CLI Popen + communicate
        resolved_content = "def combined():\n    pass\n"
        mock_proc = _make_mock_popen(
            stdout=_make_claude_output(("src/main.py", resolved_content)),
        )
        mock_popen.return_value = mock_proc

        # Mock git commands (add, commit)
        mock_run_git.return_value = _make_completed()

        result = resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Story 4.2: Test",
        )

        assert result.resolved is True
        assert result.files_resolved == ["src/main.py"]
        assert result.error is None

        # Verify file was written
        written = conflict_file.read_text(encoding="utf-8")
        assert written == resolved_content

        # Verify git add was called
        mock_run_git.assert_any_call(["add", "src/main.py"], cwd=tmp_path)

        # Verify git commit --no-edit was called
        mock_run_git.assert_any_call(["commit", "--no-edit"], cwd=tmp_path)

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_multiple_conflict_files_all_resolved(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify all files are read, resolved, and validated individually."""
        # Create conflicted files
        for name in ["src/main.py", "src/utils.py"]:
            fpath = tmp_path / name
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(
                "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> branch\n",
                encoding="utf-8",
            )

        mock_proc = _make_mock_popen(
            stdout=_make_claude_output(
                ("src/main.py", "resolved1\n"),
                ("src/utils.py", "resolved2\n"),
            ),
        )
        mock_popen.return_value = mock_proc

        mock_run_git.return_value = _make_completed()

        result = resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py", "src/utils.py"],
            story_context="Test",
        )

        assert result.resolved is True
        assert set(result.files_resolved) == {"src/main.py", "src/utils.py"}

        # Verify both files were written
        assert (tmp_path / "src/main.py").read_text(encoding="utf-8") == "resolved1\n"
        assert (tmp_path / "src/utils.py").read_text(encoding="utf-8") == "resolved2\n"

        # Verify git add called for each file
        mock_run_git.assert_any_call(["add", "src/main.py"], cwd=tmp_path)
        mock_run_git.assert_any_call(["add", "src/utils.py"], cwd=tmp_path)


# ============================================================================
# resolve_conflicts() Empty Conflict Files Test (Edge Case)
# ============================================================================


class TestResolveConflictsEmptyFiles:
    """Test resolve_conflicts() with empty conflict file list."""

    def test_empty_conflict_files_returns_failure(self, tmp_path: Path) -> None:
        """Verify empty conflict_files returns failure without invoking Claude CLI."""
        result = resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=[],
            story_context="Test",
        )

        assert result.resolved is False
        assert "No conflict files" in (result.error or "")


# ============================================================================
# resolve_conflicts() Residual Markers Tests (Task 7.2)
# ============================================================================


class TestResolveConflictsResidualMarkers:
    """Test resolve_conflicts() with residual conflict markers in output."""

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_residual_markers_aborts_merge(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify merge abort and blocked result when residual markers detected."""
        conflict_file = tmp_path / "src" / "main.py"
        conflict_file.parent.mkdir(parents=True)
        conflict_file.write_text("original conflict", encoding="utf-8")

        # Claude returns content that still has conflict markers
        bad_content = "<<<<<<< HEAD\ndef old():\n=======\ndef new():\n>>>>>>> branch\n"
        mock_proc = _make_mock_popen(
            stdout=_make_claude_output(("src/main.py", bad_content)),
        )
        mock_popen.return_value = mock_proc

        mock_run_git.return_value = _make_completed()

        result = resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Test",
        )

        assert result.resolved is False
        assert "src/main.py" in result.files_with_residual_markers
        assert result.error is not None
        assert "Residual" in result.error

        # Verify merge abort was called (via try...finally)
        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )


# ============================================================================
# resolve_conflicts() Claude CLI Timeout Tests (Task 7.3)
# ============================================================================


class TestResolveConflictsTimeout:
    """Test resolve_conflicts() when Claude CLI times out."""

    @patch("bmad_assist_lite.parallel.merger.kill_process")
    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_timeout_aborts_merge_and_kills_process_tree(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        mock_kill: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify merge abort, process tree cleanup, and blocked result on timeout."""
        conflict_file = tmp_path / "src" / "main.py"
        conflict_file.parent.mkdir(parents=True)
        conflict_file.write_text("conflict content", encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
            cmd=["claude", "--print"], timeout=120
        )
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        mock_run_git.return_value = _make_completed()

        result = resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Test",
            timeout=120,
        )

        assert result.resolved is False
        assert "timed out" in (result.error or "")

        # Verify process tree was killed via kill_process()
        mock_kill.assert_called_once_with(mock_proc)
        mock_proc.wait.assert_called_once()

        # Verify merge abort was called (via try...finally)
        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )


# ============================================================================
# resolve_conflicts() Claude CLI Non-Zero Exit Tests (Task 7.4)
# ============================================================================


class TestResolveConflictsNonZeroExit:
    """Test resolve_conflicts() when Claude CLI returns non-zero exit code."""

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_nonzero_exit_aborts_merge(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify merge abort and error message on non-zero exit."""
        conflict_file = tmp_path / "src" / "main.py"
        conflict_file.parent.mkdir(parents=True)
        conflict_file.write_text("conflict content", encoding="utf-8")

        mock_proc = _make_mock_popen(
            returncode=1,
            stderr="Authentication error",
        )
        mock_popen.return_value = mock_proc

        mock_run_git.return_value = _make_completed()

        result = resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Test",
        )

        assert result.resolved is False
        assert "rc=1" in (result.error or "")
        assert "Authentication error" in (result.error or "")

        # Verify merge abort was called (via try...finally)
        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )


# ============================================================================
# resolve_conflicts() Claude CLI Not Found Tests (Task 7.5)
# ============================================================================


class TestResolveConflictsNotFound:
    """Test resolve_conflicts() when Claude CLI is not on PATH."""

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_file_not_found_raises_parallel_error_and_aborts(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify ParallelError raised and merge aborted when claude CLI not found."""
        conflict_file = tmp_path / "src" / "main.py"
        conflict_file.parent.mkdir(parents=True)
        conflict_file.write_text("conflict content", encoding="utf-8")

        mock_popen.side_effect = FileNotFoundError(
            "No such file or directory: 'claude'"
        )
        mock_run_git.return_value = _make_completed()

        with pytest.raises(ParallelError, match="not found on PATH"):
            resolve_conflicts(
                story_id="4.2",
                project_root=tmp_path,
                conflict_files=["src/main.py"],
                story_context="Test",
            )

        # Verify merge abort was called via try...finally
        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )


# ============================================================================
# resolve_conflicts() UnicodeDecodeError Test (Edge Case)
# ============================================================================


class TestResolveConflictsUnicodeError:
    """Test resolve_conflicts() with non-UTF-8 conflict files."""

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_unicode_decode_error_returns_failure(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify UnicodeDecodeError from binary files is handled gracefully."""
        # Create a binary file that will fail read_text(encoding="utf-8")
        binary_file = tmp_path / "src" / "image.bin"
        binary_file.parent.mkdir(parents=True)
        binary_file.write_bytes(b"\x80\x81\x82\xff\xfe")

        mock_run_git.return_value = _make_completed()

        result = resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/image.bin"],
            story_context="Test",
        )

        assert result.resolved is False
        assert "Failed to read conflict file" in (result.error or "")

        # Verify merge abort was called via try...finally
        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )


# ============================================================================
# resolve_conflicts() Merge Abort Guarantee Tests (Task 7.10)
# ============================================================================


class TestResolveConflictsMergeAbortGuarantee:
    """Test git merge --abort is called on any resolution failure via try...finally."""

    @patch("bmad_assist_lite.parallel.merger.kill_process")
    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_abort_on_timeout(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        mock_kill: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify merge --abort called on timeout."""
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("conflict", encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=120
        )
        mock_popen.return_value = mock_proc
        mock_run_git.return_value = _make_completed()

        resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Test",
        )

        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_abort_on_nonzero_exit(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify merge --abort called on non-zero exit."""
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("conflict", encoding="utf-8")

        mock_proc = _make_mock_popen(returncode=1, stderr="error")
        mock_popen.return_value = mock_proc
        mock_run_git.return_value = _make_completed()

        resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Test",
        )

        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_abort_on_residual_markers(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify merge --abort called when residual markers remain."""
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("conflict", encoding="utf-8")

        bad = "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> b\n"
        mock_proc = _make_mock_popen(
            stdout=_make_claude_output(("src/main.py", bad)),
        )
        mock_popen.return_value = mock_proc
        mock_run_git.return_value = _make_completed()

        resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Test",
        )

        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_abort_on_parse_failure(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify merge --abort called when output parsing fails."""
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("conflict", encoding="utf-8")

        # Output with no delimiters at all
        mock_proc = _make_mock_popen(
            stdout="I couldn't resolve the conflicts.",
        )
        mock_popen.return_value = mock_proc
        mock_run_git.return_value = _make_completed()

        resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Test",
        )

        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_abort_on_commit_failure(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify merge --abort called when git commit --no-edit fails."""
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("conflict", encoding="utf-8")

        mock_proc = _make_mock_popen(
            stdout=_make_claude_output(("src/main.py", "resolved\n")),
        )
        mock_popen.return_value = mock_proc

        # git add succeeds, but git commit raises ParallelError
        def git_side_effect(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if args == ["commit", "--no-edit"]:
                raise ParallelError("git commit failed: nothing to commit")
            return _make_completed()

        mock_run_git.side_effect = git_side_effect

        with pytest.raises(ParallelError, match="nothing to commit"):
            resolve_conflicts(
                story_id="4.2",
                project_root=tmp_path,
                conflict_files=["src/main.py"],
                story_context="Test",
            )

        # Verify merge abort was called via try...finally
        mock_run_git.assert_any_call(
            ["merge", "--abort"], cwd=tmp_path, check=False
        )


# ============================================================================
# merge_story() with Resolution Enabled Tests (Task 7.7, 7.8, 7.9)
# ============================================================================


class TestMergeStoryWithResolution:
    """Test merge_story() with resolve=True (end-to-end resolution path)."""

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger.resolve_conflicts")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_resolution_success_returns_merge_success(
        self,
        mock_run_git: MagicMock,
        mock_resolve: MagicMock,
        mock_cleanup: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify successful resolution returns MergeResult(success=True)."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),  # rev-parse
            _make_completed(stdout=""),  # status --porcelain
            _make_completed(
                returncode=1,
                stdout="CONFLICT (content): Merge conflict in src/main.py\n",
            ),
            _make_completed(stdout="src/main.py\n"),  # diff
            _make_completed(),  # branch -d
        ]

        def _resolve_side_effect(**kwargs: object) -> ConflictResolutionResult:
            merge_head = tmp_path / ".git" / "MERGE_HEAD"
            if merge_head.exists():
                merge_head.unlink()
            return ConflictResolutionResult(
                resolved=True,
                files_resolved=["src/main.py"],
            )

        mock_resolve.side_effect = _resolve_side_effect

        result = merge_story(
            "4.2",
            tmp_path,
            resolve=True,
            story_context="Story 4.2 context",
            conflict_resolution_timeout=60,
        )

        assert result.success is True
        assert result.story_id == "4.2"

        mock_resolve.assert_called_once_with(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Story 4.2 context",
            timeout=60,
        )

    @patch("bmad_assist_lite.parallel.merger.resolve_conflicts")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_resolution_failure_returns_merge_failure(
        self,
        mock_run_git: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify failed resolution returns MergeResult(success=False)."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(stdout=""),  # status --porcelain
            _make_completed(
                returncode=1, stdout="CONFLICT (content)\n"
            ),
            _make_completed(stdout="src/main.py\n"),
        ]

        def _resolve_side_effect(**kwargs: object) -> ConflictResolutionResult:
            merge_head = tmp_path / ".git" / "MERGE_HEAD"
            if merge_head.exists():
                merge_head.unlink()
            return ConflictResolutionResult(
                resolved=False,
                error="Claude CLI timed out after 120s",
            )

        mock_resolve.side_effect = _resolve_side_effect

        result = merge_story(
            "4.2",
            tmp_path,
            resolve=True,
            story_context="Test",
        )

        assert result.success is False
        assert result.story_id == "4.2"
        assert "timed out" in (result.error or "")
        assert result.conflict_files == ["src/main.py"]


class TestMergeStoryResolutionDisabled:
    """Test merge_story() with resolve=False (backward compat with 4.1)."""

    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_default_no_resolution_returns_failure(
        self,
        mock_run_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify conflicts return MergeResult(success=False) without resolution."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(stdout=""),  # status --porcelain
            _make_completed(
                returncode=1, stdout="CONFLICT (content)\n"
            ),
            _make_completed(stdout="src/main.py\n"),
            _make_completed(),  # merge --abort
        ]

        result = merge_story("4.2", tmp_path)

        assert result.success is False
        assert result.conflict_files == ["src/main.py"]

    @patch("bmad_assist_lite.parallel.merger.resolve_conflicts")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_resolve_false_does_not_call_resolve_conflicts(
        self,
        mock_run_git: MagicMock,
        mock_resolve: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify resolve_conflicts is NOT called when resolve=False."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(stdout=""),  # status --porcelain
            _make_completed(
                returncode=1, stdout="CONFLICT (content)\n"
            ),
            _make_completed(stdout="src/main.py\n"),
            _make_completed(),
        ]

        merge_story("4.2", tmp_path, resolve=False)

        mock_resolve.assert_not_called()


# ============================================================================
# merge_story() Resolution with Cleanup Tests
# ============================================================================


class TestMergeStoryResolutionCleanup:
    """Test merge_story() cleanup after successful resolution."""

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger.resolve_conflicts")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_worktree_cleanup_after_resolution(
        self,
        mock_run_git: MagicMock,
        mock_resolve: MagicMock,
        mock_cleanup: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify worktree cleanup runs after successful resolution."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(stdout=""),  # status --porcelain
            _make_completed(returncode=1, stdout="CONFLICT\n"),
            _make_completed(stdout="src/main.py\n"),
            _make_completed(),  # branch delete
        ]

        def _resolve_side_effect(**kwargs: object) -> ConflictResolutionResult:
            merge_head = tmp_path / ".git" / "MERGE_HEAD"
            if merge_head.exists():
                merge_head.unlink()
            return ConflictResolutionResult(
                resolved=True, files_resolved=["src/main.py"]
            )

        mock_resolve.side_effect = _resolve_side_effect

        merge_story("4.2", tmp_path, resolve=True, story_context="Test")

        mock_cleanup.assert_called_once_with("4.2", tmp_path)

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger.resolve_conflicts")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_branch_deletion_after_resolution(
        self,
        mock_run_git: MagicMock,
        mock_resolve: MagicMock,
        mock_cleanup: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify branch deletion runs after successful resolution."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(stdout=""),  # status --porcelain
            _make_completed(returncode=1, stdout="CONFLICT\n"),
            _make_completed(stdout="src/main.py\n"),
            _make_completed(),  # branch delete
        ]

        def _resolve_side_effect(**kwargs: object) -> ConflictResolutionResult:
            merge_head = tmp_path / ".git" / "MERGE_HEAD"
            if merge_head.exists():
                merge_head.unlink()
            return ConflictResolutionResult(
                resolved=True, files_resolved=["src/main.py"]
            )

        mock_resolve.side_effect = _resolve_side_effect

        merge_story("4.2", tmp_path, resolve=True, story_context="Test")

        branch_call = mock_run_git.call_args_list[4]
        assert branch_call == call(
            ["branch", "-d", "parallel/4-2"], cwd=tmp_path, check=False
        )


# ============================================================================
# Config Integration Tests (Task 7.15)
# ============================================================================


class TestConflictResolutionTimeoutConfig:
    """Test conflict_resolution_timeout config field."""

    def test_default_timeout_value(self) -> None:
        """Verify default conflict_resolution_timeout is 120."""
        config = ParallelConfig()
        assert config.conflict_resolution_timeout == 120

    def test_custom_timeout_value(self) -> None:
        """Verify custom timeout value is accepted."""
        config = ParallelConfig(conflict_resolution_timeout=300)
        assert config.conflict_resolution_timeout == 300

    def test_minimum_timeout_validation(self) -> None:
        """Verify timeout below minimum raises ValidationError."""
        with pytest.raises(ValidationError):
            ParallelConfig(conflict_resolution_timeout=5)

    def test_timeout_frozen(self) -> None:
        """Verify timeout field is frozen."""
        config = ParallelConfig()
        with pytest.raises(ValidationError):
            config.conflict_resolution_timeout = 200  # type: ignore[misc]

    @patch("bmad_assist_lite.parallel.merger.cleanup_worktree")
    @patch("bmad_assist_lite.parallel.merger.resolve_conflicts")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_timeout_flows_to_resolve_conflicts(
        self,
        mock_run_git: MagicMock,
        mock_resolve: MagicMock,
        mock_cleanup: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify config timeout is passed through to resolve_conflicts."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        mock_run_git.side_effect = [
            _make_completed(stdout="main\n"),
            _make_completed(stdout=""),  # status --porcelain
            _make_completed(returncode=1, stdout="CONFLICT\n"),
            _make_completed(stdout="src/main.py\n"),
            _make_completed(),
        ]

        def _resolve_side_effect(**kwargs: object) -> ConflictResolutionResult:
            merge_head = tmp_path / ".git" / "MERGE_HEAD"
            if merge_head.exists():
                merge_head.unlink()
            return ConflictResolutionResult(
                resolved=True, files_resolved=["src/main.py"]
            )

        mock_resolve.side_effect = _resolve_side_effect

        config = ParallelConfig(conflict_resolution_timeout=300)

        merge_story(
            "4.2",
            tmp_path,
            resolve=True,
            story_context="Test",
            conflict_resolution_timeout=config.conflict_resolution_timeout,
        )

        mock_resolve.assert_called_once()
        call_kwargs = mock_resolve.call_args
        assert call_kwargs.kwargs["timeout"] == 300


# ============================================================================
# Prompt via stdin Tests (Task 1.4)
# ============================================================================


class TestClaudeCliInvocation:
    """Test Claude CLI is invoked correctly with prompt via stdin."""

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_prompt_passed_via_stdin(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify prompt is passed via communicate(input=) (stdin), not -p arg."""
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("conflict", encoding="utf-8")

        mock_proc = _make_mock_popen(
            stdout=_make_claude_output(("src/main.py", "resolved\n")),
        )
        mock_popen.return_value = mock_proc
        mock_run_git.return_value = _make_completed()

        resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Test story context",
        )

        # Verify Popen was called with stdin=PIPE
        popen_kwargs = mock_popen.call_args
        assert popen_kwargs.kwargs.get("stdin") == subprocess.PIPE

        # Verify communicate was called with the prompt via input=
        comm_call = mock_proc.communicate.call_args
        assert comm_call.kwargs.get("input") is not None
        assert "Test story context" in comm_call.kwargs["input"]

        # Verify command is ["claude", "--print"]
        assert popen_kwargs.args[0] == ["claude", "--print"]

    @patch("bmad_assist_lite.parallel.merger.get_subprocess_kwargs", return_value={})
    @patch("bmad_assist_lite.parallel.merger.subprocess.Popen")
    @patch("bmad_assist_lite.parallel.merger._run_git")
    def test_subprocess_uses_get_subprocess_kwargs(
        self,
        mock_run_git: MagicMock,
        mock_popen: MagicMock,
        mock_kwargs: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Verify get_subprocess_kwargs() is used for Claude CLI subprocess call."""
        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("conflict", encoding="utf-8")

        mock_proc = _make_mock_popen(
            stdout=_make_claude_output(("src/main.py", "resolved\n")),
        )
        mock_popen.return_value = mock_proc
        mock_run_git.return_value = _make_completed()

        resolve_conflicts(
            story_id="4.2",
            project_root=tmp_path,
            conflict_files=["src/main.py"],
            story_context="Test",
        )

        # Verify get_subprocess_kwargs was called
        mock_kwargs.assert_called_once()
