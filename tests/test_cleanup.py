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


# ============================================================================
# REQ-06.1 — Forensic artifact retention across story transitions
# ============================================================================


def _make_cache(tmp_path: Path) -> Path:
    """Create and return the project cache directory."""
    cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
    cache_dir.mkdir(parents=True)
    return cache_dir


def _write_forensics(cache_dir: Path, story_id: str) -> dict[str, Path]:
    """Write the three forensic artifact types for ``story_id``."""
    paths = {
        "review": cache_dir / f"synthesis-diff-review-{story_id}.patch",
        "validate": cache_dir / f"synthesis-diff-validate-{story_id}.patch",
        "qa": cache_dir / f"qa-failures-{story_id}.md",
    }
    for key, path in paths.items():
        path.write_text(f"{key} content for {story_id}", encoding="utf-8")
    return paths


def _load_forensics_config(**overrides: object) -> None:
    """Load a config whose ``forensics`` section carries ``overrides``."""
    from bmad_assist_lite.core.config import load_config

    load_config(
        {
            "providers": {"master": {"provider": "claude", "model": "opus"}},
            "forensics": overrides,
        }
    )


class TestForensicRetention:
    """Forensic artifacts survive the story transition, bounded (REQ-06.1)."""

    def test_forensic_artifacts_survive_story_transition(self, tmp_path: Path) -> None:
        """LOAD-BEARING: all three forensic artifact types survive; others do not.

        REQ-06.1 AC1 + AC3 + AC4.
        """
        from bmad_assist_lite.loop.cleanup import (
            FORENSICS_DIR_NAME,
            clear_story_cache,
            find_forensic_artifacts,
        )

        cache_dir = _make_cache(tmp_path)
        written = _write_forensics(cache_dir, "1.2")
        unrelated = cache_dir / "validations.json"
        unrelated.write_text("ephemeral", encoding="utf-8")

        clear_story_cache(tmp_path)

        # The unrelated cache file MUST still be deleted — a transition that
        # keeps everything is a regression, not a fix.
        assert not unrelated.exists()

        # Every forensic artifact survives, is attributable to its story, and
        # keeps its exact content.
        story_dir = cache_dir / FORENSICS_DIR_NAME / "1.2"
        for key, original in written.items():
            assert not original.exists(), f"{original.name} left in the story-scoped cache"
            archived = story_dir / original.name
            assert archived.exists(), f"{original.name} did not survive the transition"
            assert archived.read_text(encoding="utf-8") == f"{key} content for 1.2"

        surviving = {p.name for p in find_forensic_artifacts(tmp_path)}
        assert surviving == {p.name for p in written.values()}

    def test_non_forensic_cache_files_still_deleted(self, tmp_path: Path) -> None:
        """The sweep is not turned into a no-op by retention (REQ-06.1 AC3)."""
        from bmad_assist_lite.loop.cleanup import clear_story_cache

        cache_dir = _make_cache(tmp_path)
        _write_forensics(cache_dir, "3.1")
        ephemeral = [
            cache_dir / "validations.json",
            cache_dir / "reviews.json",
            cache_dir / "synthesis-response-review-3.1.md",
            cache_dir / "toolchain.yaml",
        ]
        for path in ephemeral:
            path.write_text("x", encoding="utf-8")
        # Long-lived allowlist entries still survive.
        (cache_dir / "story-queue.yaml").write_text("queue", encoding="utf-8")

        deleted = clear_story_cache(tmp_path)

        for path in ephemeral:
            assert not path.exists(), f"{path.name} should have been swept"
        assert deleted == len(ephemeral)
        assert (cache_dir / "story-queue.yaml").exists()

    def test_dev_stream_artifact_archived(self, tmp_path: Path) -> None:
        """SP-D0: dev-stream-<story>.jsonl survives the transition; others don't."""
        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        cache_dir = _make_cache(tmp_path)
        dev_stream = cache_dir / "dev-stream-3.1.jsonl"
        dev_stream.write_text('{"seq":0,"kind":"text"}\n', encoding="utf-8")
        ephemeral = cache_dir / "validations.json"
        ephemeral.write_text("x", encoding="utf-8")

        clear_story_cache(tmp_path)

        archived = cache_dir / FORENSICS_DIR_NAME / "3.1" / "dev-stream-3.1.jsonl"
        assert archived.exists(), "dev-stream artifact did not survive the transition"
        assert archived.read_text(encoding="utf-8") == '{"seq":0,"kind":"text"}\n'
        assert not dev_stream.exists()
        # A non-forensic file is still swept — retention is not a blanket no-op.
        assert not ephemeral.exists()

    def test_phase_prefixed_stream_artifact_archived(self, tmp_path: Path) -> None:
        """SP-A2: <phase>-stream-<story>.jsonl (e.g. create_story) also survives."""
        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        cache_dir = _make_cache(tmp_path)
        cs_stream = cache_dir / "create_story-stream-4.7.jsonl"
        cs_stream.write_text('{"seq":0,"kind":"text"}\n', encoding="utf-8")

        clear_story_cache(tmp_path)

        archived = cache_dir / FORENSICS_DIR_NAME / "4.7" / "create_story-stream-4.7.jsonl"
        assert archived.exists(), "create_story-stream artifact did not survive"
        assert not cs_stream.exists()

    def test_multiple_stories_archived_separately(self, tmp_path: Path) -> None:
        """Artifacts are attributable per story across transitions (AC4)."""
        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        cache_dir = _make_cache(tmp_path)
        for story_id in ("1.1", "1.2", "2.10"):
            _write_forensics(cache_dir, story_id)
            clear_story_cache(tmp_path)

        forensics_root = cache_dir / FORENSICS_DIR_NAME
        assert {d.name for d in forensics_root.iterdir()} == {"1.1", "1.2", "2.10"}
        for story_id in ("1.1", "1.2", "2.10"):
            names = {p.name for p in (forensics_root / story_id).iterdir()}
            assert names == {
                f"synthesis-diff-review-{story_id}.patch",
                f"synthesis-diff-validate-{story_id}.patch",
                f"qa-failures-{story_id}.md",
            }

    def test_archive_is_atomic_and_leaves_no_temp_files(self, tmp_path: Path) -> None:
        """No partial/temp residue is left behind by the archive step."""
        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        cache_dir = _make_cache(tmp_path)
        _write_forensics(cache_dir, "4.4")

        clear_story_cache(tmp_path)

        forensics_root = cache_dir / FORENSICS_DIR_NAME
        assert list(forensics_root.rglob("*.tmp")) == []
        assert list(cache_dir.glob("*.tmp")) == []

    def test_rearchiving_same_story_overwrites_without_loss(self, tmp_path: Path) -> None:
        """A second transition for the same story keeps the newest artifact."""
        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        cache_dir = _make_cache(tmp_path)
        _write_forensics(cache_dir, "5.1")
        clear_story_cache(tmp_path)

        (cache_dir / "qa-failures-5.1.md").write_text("second attempt", encoding="utf-8")
        clear_story_cache(tmp_path)

        archived = cache_dir / FORENSICS_DIR_NAME / "5.1" / "qa-failures-5.1.md"
        assert archived.read_text(encoding="utf-8") == "second attempt"


class TestForensicRetentionCap:
    """The retention cap is bounded and evicts oldest-first (REQ-06.1 AC2)."""

    def test_cap_evicts_oldest_first(self, tmp_path: Path) -> None:
        """Oldest story directories are evicted once the cap is exceeded."""
        import os

        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        _load_forensics_config(max_stories=2)
        cache_dir = _make_cache(tmp_path)
        forensics_root = cache_dir / FORENSICS_DIR_NAME

        for index, story_id in enumerate(("1.1", "1.2", "1.3")):
            _write_forensics(cache_dir, story_id)
            clear_story_cache(tmp_path)
            # Deterministic age ordering: 1.1 oldest, 1.3 newest.
            stamp = 1_000_000 + index * 100
            os.utime(forensics_root / story_id, (stamp, stamp))

        _write_forensics(cache_dir, "1.4")
        clear_story_cache(tmp_path)

        remaining = {d.name for d in forensics_root.iterdir()}
        assert remaining == {"1.3", "1.4"}

    def test_cap_never_evicts_the_current_story(self, tmp_path: Path) -> None:
        """The story just archived is never the one evicted."""
        import os

        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        _load_forensics_config(max_stories=1)
        cache_dir = _make_cache(tmp_path)
        forensics_root = cache_dir / FORENSICS_DIR_NAME

        _write_forensics(cache_dir, "9.1")
        clear_story_cache(tmp_path)
        # Make the previous story look NEWER than the one about to be archived,
        # so a naive mtime sort would evict the current story.
        os.utime(forensics_root / "9.1", (9_000_000, 9_000_000))

        _write_forensics(cache_dir, "9.2")
        clear_story_cache(tmp_path)

        assert (forensics_root / "9.2").is_dir()
        assert {p.name for p in (forensics_root / "9.2").iterdir()} == {
            "synthesis-diff-review-9.2.patch",
            "synthesis-diff-validate-9.2.patch",
            "qa-failures-9.2.md",
        }

    def test_cap_default_is_bounded(self) -> None:
        """The default retention cap is a finite positive number."""
        from bmad_assist_lite.core.config import ForensicsConfig

        default = ForensicsConfig()
        assert default.enabled is True
        assert default.max_stories >= 1

    def test_cap_rejects_zero(self) -> None:
        """A cap of 0 is invalid — it would defeat the requirement."""
        import pytest as _pytest
        from pydantic import ValidationError

        from bmad_assist_lite.core.config import ForensicsConfig

        with _pytest.raises(ValidationError):
            ForensicsConfig(max_stories=0)


class TestForensicRetentionReversal:
    """``forensics.enabled: false`` stops archiving — it never deletes an archive."""

    def test_disabled_flag_deletes_forensic_artifacts(self, tmp_path: Path) -> None:
        """With retention off, un-archived artifacts are swept as before."""
        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        _load_forensics_config(enabled=False)
        cache_dir = _make_cache(tmp_path)
        written = _write_forensics(cache_dir, "1.2")
        unrelated = cache_dir / "validations.json"
        unrelated.write_text("ephemeral", encoding="utf-8")
        (cache_dir / "story-queue.yaml").write_text("queue", encoding="utf-8")

        deleted = clear_story_cache(tmp_path)

        for path in written.values():
            assert not path.exists()
        assert not unrelated.exists()
        assert not (cache_dir / FORENSICS_DIR_NAME).exists()
        assert deleted == 4
        assert (cache_dir / "story-queue.yaml").exists()

    def test_disabled_flag_preserves_an_existing_archive(self, tmp_path: Path) -> None:
        """LOAD-BEARING: turning archiving OFF must not destroy what was archived ON.

        Disabling a retention flag is a request to stop collecting evidence,
        never a request to delete the evidence already collected.
        """
        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        _load_forensics_config(enabled=True)
        cache_dir = _make_cache(tmp_path)
        forensics_root = cache_dir / FORENSICS_DIR_NAME
        _write_forensics(cache_dir, "1.1")
        clear_story_cache(tmp_path)
        archived_before = sorted(p.name for p in (forensics_root / "1.1").iterdir())
        assert archived_before, "precondition: story 1.1 was archived"

        _load_forensics_config(enabled=False)
        new_artifacts = _write_forensics(cache_dir, "1.2")
        clear_story_cache(tmp_path)

        # The existing archive survives, byte for byte.
        assert forensics_root.is_dir(), "the archive was destroyed by disabling the flag"
        assert sorted(p.name for p in (forensics_root / "1.1").iterdir()) == archived_before
        assert (forensics_root / "1.1" / "qa-failures-1.1.md").read_text(
            encoding="utf-8"
        ) == "qa content for 1.1"

        # ...and archiving genuinely stopped: nothing new was added.
        assert {d.name for d in forensics_root.iterdir()} == {"1.1"}
        for path in new_artifacts.values():
            assert not path.exists()

    def test_config_without_forensics_section_still_loads(self) -> None:
        """The field is additive — a config with no ``forensics`` key is valid (G8)."""
        from bmad_assist_lite.core.config import load_config

        config = load_config({"providers": {"master": {"provider": "claude", "model": "opus"}}})
        assert config.forensics.enabled is True


class TestFixQualityGateReadsReportWithinStory:
    """Archiving on transition must not race the same-story fix_quality_gate read."""

    def test_report_readable_after_quality_gate_failure(self, tmp_path: Path) -> None:
        """``quality_gate`` -> ``fix_quality_gate`` happens with no transition between."""
        from bmad_assist_lite.loop.cleanup import clear_story_cache  # noqa: F401

        cache_dir = _make_cache(tmp_path)
        report = cache_dir / "qa-failures-7.3.md"
        report.write_text("# Quality Gate Failures — Story 7.3\n", encoding="utf-8")

        # No clear_story_cache runs on the detour; the handler's read must succeed.
        assert report.exists()
        assert "Story 7.3" in report.read_text(encoding="utf-8")

    def test_next_phase_detour_does_not_clear_the_cache(self) -> None:
        """The ``next_phase`` override branch in the runner performs no cache sweep.

        Static half of the G12 pairing: the detour into ``fix_quality_gate``
        must not reach ``clear_story_cache``.
        """
        import ast
        import inspect

        from bmad_assist_lite.loop import runner

        tree = ast.parse(inspect.getsource(runner))
        detours: list[ast.If] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test_src = ast.dump(node.test)
            if "next_phase" in test_src and "FIX_QUALITY_GATE" not in test_src:
                detours.append(node)
        assert detours, "no next_phase override branch found in runner.py"
        for branch in detours:
            calls = {
                child.func.id
                for child in ast.walk(branch)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
            }
            assert "clear_story_cache" not in calls

    def test_fix_quality_gate_handler_reads_archived_story_report(
        self, tmp_path: Path
    ) -> None:
        """Runtime half: after a transition, the report is retrievable per story."""
        from bmad_assist_lite.loop.cleanup import clear_story_cache, find_forensic_artifacts

        cache_dir = _make_cache(tmp_path)
        (cache_dir / "qa-failures-7.3.md").write_text("failures", encoding="utf-8")

        clear_story_cache(tmp_path)

        matches = [
            p for p in find_forensic_artifacts(tmp_path, story_id="7.3")
            if p.name == "qa-failures-7.3.md"
        ]
        assert len(matches) == 1
        assert matches[0].read_text(encoding="utf-8") == "failures"

class TestPostMergeQgFailuresRetained:
    """`post-merge-qg-failures-*.md` is the same evidence class as `qa-failures-*.md`.

    It is written by ``parallel/merger.py`` and does not match the ``qa-failures-`` pattern,
    which is anchored at the start of the filename. Post-merge gate failures are exactly the
    env-vs-real classification data WS6 needs, so sweeping them would have defeated half the
    point of forensic retention.
    """

    def test_post_merge_qg_failures_survives_transition(self, tmp_path: Path) -> None:
        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        cache = tmp_path / ".bmad-assist-lite" / "cache"
        cache.mkdir(parents=True)
        (cache / "post-merge-qg-failures-3.4.md").write_text("failed gates: unknown")
        (cache / "validations.json").write_text("{}")

        clear_story_cache(tmp_path)

        archived = cache / FORENSICS_DIR_NAME / "3.4" / "post-merge-qg-failures-3.4.md"
        assert archived.exists(), "post-merge QG failure report was swept, not archived"
        assert archived.read_text() == "failed gates: unknown"
        assert not (cache / "validations.json").exists(), (
            "non-forensic cache files must still be swept — retention must not become a no-op"
        )

    def test_story_id_is_extracted_not_swallowed_by_the_prefix(self, tmp_path: Path) -> None:
        """The greedy `.+` must not fold `qg-failures-` into the story id."""
        from bmad_assist_lite.loop.cleanup import FORENSICS_DIR_NAME, clear_story_cache

        cache = tmp_path / ".bmad-assist-lite" / "cache"
        cache.mkdir(parents=True)
        (cache / "post-merge-qg-failures-12.7.md").write_text("x")

        clear_story_cache(tmp_path)

        assert (cache / FORENSICS_DIR_NAME / "12.7" / "post-merge-qg-failures-12.7.md").exists()
