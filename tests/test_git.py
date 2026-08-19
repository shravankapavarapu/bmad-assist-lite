"""Tests for core/git.py auto-commit helper."""

import subprocess

from bmad_assist_lite.core.git import (
    _title_from_story_key,
    auto_commit_story,
    list_changed_files,
)


def _init_repo(path):
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    # Initial commit so HEAD exists
    (path / "README.md").write_text("init")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        capture_output=True,
        check=True,
    )


class TestTitleFromStoryKey:
    """Tests for _title_from_story_key helper."""

    def test_standard_key(self):
        """Standard N-N-slug key extracts slug as spaced title."""
        assert _title_from_story_key("6-2-blog-ui-component-unit-tests") == (
            "blog ui component unit tests"
        )

    def test_single_word_title(self):
        """Single word after prefix."""
        assert _title_from_story_key("1-1-setup") == "setup"

    def test_no_numeric_prefix(self):
        """Key without numeric prefix is fully hyphen-to-space converted."""
        assert _title_from_story_key("some-random-key") == "some random key"


class TestAutoCommitStory:
    """Tests for auto_commit_story using real git repos."""

    def test_no_changes(self, tmp_path):
        """No dirty files -> returns True, no commit created."""
        _init_repo(tmp_path)
        result = auto_commit_story(tmp_path, "1.1", "1-1-setup")
        assert result is True

        # Only the initial commit should exist
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert log.stdout.strip().count("\n") == 0  # single line = 1 commit

    def test_with_changes(self, tmp_path):
        """Dirty repo -> stages, commits with correct message format."""
        _init_repo(tmp_path)
        (tmp_path / "app.py").write_text("print('hello')")

        result = auto_commit_story(tmp_path, "6.2", "6-2-blog-ui-component-unit-tests")
        assert result is True

        log = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert log.stdout.strip() == "feat(story-6.2): blog ui component unit tests"

    def test_with_story_key(self, tmp_path):
        """Commit message includes human-readable title from story key."""
        _init_repo(tmp_path)
        (tmp_path / "feature.py").write_text("x = 1")

        auto_commit_story(tmp_path, "3.1", "3-1-user-auth-setup")

        log = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert log.stdout.strip() == "feat(story-3.1): user auth setup"

    def test_without_story_key(self, tmp_path):
        """Falls back to story_id only when no key provided."""
        _init_repo(tmp_path)
        (tmp_path / "module.py").write_text("y = 2")

        auto_commit_story(tmp_path, "5.3", None)

        log = subprocess.run(
            ["git", "log", "-1", "--format=%s"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert log.stdout.strip() == "feat(story-5.3): completed"

    def test_commit_body(self, tmp_path):
        """Commit body includes auto-commit attribution."""
        _init_repo(tmp_path)
        (tmp_path / "file.txt").write_text("data")

        auto_commit_story(tmp_path, "1.1", "1-1-test")

        log = subprocess.run(
            ["git", "log", "-1", "--format=%b"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert "Auto-committed by bmad-assist-lite" in log.stdout

    def test_failure_returns_false(self, tmp_path):
        """Git error -> returns False, doesn't raise."""
        # tmp_path is not a git repo
        (tmp_path / "file.txt").write_text("data")
        result = auto_commit_story(tmp_path, "1.1", None)
        assert result is False

    def test_not_a_repo(self, tmp_path):
        """Not in a git repo -> returns False gracefully."""
        result = auto_commit_story(tmp_path, "2.1", "2-1-some-feature")
        assert result is False

    def test_gitignore_respected(self, tmp_path):
        """Files in .gitignore should not be staged."""
        _init_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("*.log\n")
        (tmp_path / "debug.log").write_text("log data")
        (tmp_path / "app.py").write_text("code")

        auto_commit_story(tmp_path, "1.1", "1-1-test")

        # Check that debug.log is not tracked
        ls_files = subprocess.run(
            ["git", "ls-files"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert "debug.log" not in ls_files.stdout
        assert "app.py" in ls_files.stdout


class TestListChangedFiles:
    """list_changed_files: [] (clean) and None (undeterminable) are distinct."""

    def test_non_repo_returns_none(self, tmp_path):
        """Not a git repo → None (could not determine), never []."""
        assert list_changed_files(tmp_path) is None

    def test_clean_tree_returns_empty_list(self, tmp_path):
        """A committed, clean tree → [] (positively determined clean)."""
        _init_repo(tmp_path)
        assert list_changed_files(tmp_path) == []

    def test_untracked_file_is_listed(self, tmp_path):
        """An untracked new file is included (not just tracked diffs)."""
        _init_repo(tmp_path)
        (tmp_path / "new.py").write_text("x = 1\n")
        assert "new.py" in list_changed_files(tmp_path)

    def test_tracked_modification_is_listed(self, tmp_path):
        """A modification to a tracked file appears in the list."""
        _init_repo(tmp_path)
        (tmp_path / "README.md").write_text("changed")
        assert "README.md" in list_changed_files(tmp_path)
