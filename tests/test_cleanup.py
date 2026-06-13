"""Tests for crash recovery cleanup."""

import logging
from pathlib import Path

import pytest

from bmad_assist_lite.core.state import Phase
from bmad_assist_lite.loop.cleanup import (
    CURSOR_DENY_CONFIG_MARKER_NAME,
    cleanup_for_phase,
)


class TestCleanupForPhase:
    """Tests for cleanup_for_phase."""

    def test_cleans_tmp_files(self, tmp_path):
        """Removes *.tmp files from cache directory."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        (cache_dir / "partial.tmp").write_text("data")
        (cache_dir / "output.yaml").write_text("keep")

        cleaned = cleanup_for_phase(Phase.CREATE_STORY, tmp_path)

        assert len(cleaned) == 1
        assert "partial.tmp" in cleaned[0]
        assert not (cache_dir / "partial.tmp").exists()
        assert (cache_dir / "output.yaml").exists()

    def test_no_cache_dir(self, tmp_path):
        """No crash if cache dir doesn't exist."""
        cleaned = cleanup_for_phase(Phase.CREATE_STORY, tmp_path)
        assert cleaned == []

    def test_dev_story_warning(self, tmp_path, caplog):
        """DEV_STORY phase logs a warning about uncommitted git changes."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        with caplog.at_level(logging.WARNING):
            cleanup_for_phase(Phase.DEV_STORY, tmp_path)

        assert any("uncommitted git changes" in r.message for r in caplog.records)

    def test_returns_cleaned_paths(self, tmp_path):
        """Returns list of cleaned file paths."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "a.tmp").write_text("x")
        (cache_dir / "b.tmp").write_text("y")

        cleaned = cleanup_for_phase(Phase.VALIDATE_STORY, tmp_path)
        assert len(cleaned) == 2

    def test_multiple_tmp_files(self, tmp_path: Path) -> None:
        """Multiple tmp files are all cleaned."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)
        for i in range(5):
            (cache_dir / f"file{i}.tmp").write_text(f"data{i}")

        cleaned = cleanup_for_phase(Phase.CODE_REVIEW, tmp_path)
        assert len(cleaned) == 5
        assert list(cache_dir.glob("*.tmp")) == []


# ============================================================================
# Story 11.4: Cursor Deny-Config Crash Recovery Tests (AC #4)
# ============================================================================


class TestCursorDenyConfigCrashRecovery:
    """Test crash recovery sweep removes orphaned deny-config files."""

    def test_marker_exists_with_valid_path(self, tmp_path: Path) -> None:
        """Marker file exists with valid deny-config path -> both removed."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        # Create the orphaned deny-config
        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        deny_config = cursor_dir / "cli.json"
        deny_config.write_text('{"permissions": {"deny": ["Write(**)", "Shell(**)"]}}')

        # Create the marker pointing to it (must be absolute path)
        marker = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        marker.write_text(str(deny_config.resolve()))

        cleaned = cleanup_for_phase(Phase.CREATE_STORY, tmp_path)

        assert not deny_config.exists()
        assert not marker.exists()
        # Both paths should be in cleaned list
        assert str(deny_config.resolve()) in cleaned
        assert str(marker) in cleaned

    def test_marker_exists_deny_file_already_gone(self, tmp_path: Path) -> None:
        """Marker exists but referenced deny-config already deleted -> marker removed, no error."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        # Create marker pointing to non-existent file (must be absolute path)
        deny_config_path = (tmp_path / ".cursor" / "cli.json").resolve()
        marker = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        marker.write_text(str(deny_config_path))

        cleaned = cleanup_for_phase(Phase.VALIDATE_STORY, tmp_path)

        assert not marker.exists()
        # Marker should be in cleaned (deny-config wasn't there to clean)
        assert str(marker) in cleaned

    def test_no_marker_file(self, tmp_path: Path) -> None:
        """No marker file -> no action, no error."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        cleaned = cleanup_for_phase(Phase.DEV_STORY, tmp_path)

        # Only phase-specific warning, no crash recovery action for deny-config
        # cleaned may contain tmp files or nothing
        marker = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        assert not marker.exists()

    def test_marker_with_unreadable_content(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Marker file with unreadable content -> handled gracefully, marker removed."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        # Create marker with empty content
        marker = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        marker.write_text("")

        with caplog.at_level(logging.INFO):
            cleaned = cleanup_for_phase(Phase.CODE_REVIEW, tmp_path)

        # Marker should be cleaned up (empty path string is falsy, skips deny-config removal)
        assert not marker.exists()
        assert str(marker) in cleaned

    def test_crash_recovery_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Orphaned deny-config cleanup is logged at INFO."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        deny_config = cursor_dir / "cli.json"
        deny_config.write_text('{"permissions": {"deny": []}}')

        marker = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        marker.write_text(str(deny_config.resolve()))

        with caplog.at_level(logging.INFO):
            cleanup_for_phase(Phase.CREATE_STORY, tmp_path)

        assert any("orphaned cursor deny-config" in r.message.lower() for r in caplog.records)

    def test_marker_with_relative_path_rejected(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Marker containing a relative path is rejected (not deleted)."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        # Create marker with relative path (untrusted)
        marker = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        marker.write_text(".cursor/cli.json")

        with caplog.at_level(logging.WARNING):
            cleaned = cleanup_for_phase(Phase.CODE_REVIEW, tmp_path)

        # Marker itself is still cleaned, but deny-config deletion is skipped
        assert not marker.exists()
        assert any("unexpected path" in r.message for r in caplog.records)

    def test_marker_with_non_cli_json_path_rejected(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Marker pointing to a file that is not cli.json is rejected."""
        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        # Create a file that should NOT be deleted
        safe_file = tmp_path / "important.txt"
        safe_file.write_text("do not delete")

        # Create marker pointing to it (absolute but wrong name)
        marker = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        marker.write_text(str(safe_file.resolve()))

        with caplog.at_level(logging.WARNING):
            cleanup_for_phase(Phase.CODE_REVIEW, tmp_path)

        # The safe file must NOT be deleted
        assert safe_file.exists()
        assert safe_file.read_text() == "do not delete"
        assert any("unexpected path" in r.message for r in caplog.records)

    def test_marker_with_oserror_on_read(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """OSError on marker read -> marker removed, no crash."""
        from unittest.mock import patch

        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        marker = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        marker.write_text("some content")

        # Patch at the module level to only affect the read_text call in cleanup
        original_read_text = Path.read_text

        def failing_read_text(self_path: Path, *args: object, **kwargs: object) -> str:
            if self_path == marker:
                raise OSError("permission denied")
            return original_read_text(self_path, *args, **kwargs)  # type: ignore[arg-type]

        with (
            caplog.at_level(logging.WARNING),
            patch.object(Path, "read_text", failing_read_text),
        ):
            cleaned = cleanup_for_phase(Phase.CODE_REVIEW, tmp_path)

        assert any("Failed to read" in r.message for r in caplog.records)


class TestClearStoryCacheMarkerPreservation:
    """Test that clear_story_cache preserves the deny-config marker."""

    def test_marker_preserved_during_story_transition(self, tmp_path: Path) -> None:
        """clear_story_cache does NOT delete the deny-config marker file."""
        from bmad_assist_lite.loop.cleanup import clear_story_cache

        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True)

        marker = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
        marker.write_text("/some/path/.cursor/cli.json")

        # Also create a file that should be deleted
        ephemeral = cache_dir / "story-output.yaml"
        ephemeral.write_text("ephemeral data")

        deleted_count = clear_story_cache(tmp_path)

        # Marker must survive
        assert marker.exists()
        # Ephemeral file should be deleted
        assert not ephemeral.exists()
        assert deleted_count == 1
