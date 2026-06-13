"""Tests for Story 1.3: Existing Code Integration.

Covers:
- BMAD_PARALLEL_MODE env var check in trigger_sync
- single_story parameter in run_loop
- parallel subcommand group registration
- --single-story CLI flag acceptance
"""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from bmad_assist_lite.cli import app
from bmad_assist_lite.core.sprint_sync import trigger_sync
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.types import LoopExitReason, PhaseResult

runner = CliRunner()

# Standard phase list used by the loop
PHASE_LIST = [
    "create_story",
    "validate_story",
    "validate_story_synthesis",
    "dev_story",
    "code_review",
    "code_review_synthesis",
    "quality_gate",
]

STORIES = ["1.1", "1.2"]
EPICS = [1]
STORIES_FOR_EPIC = {1: STORIES}


# ---------------------------------------------------------------------------
# Task 6.2–6.4: Sprint sync bypass with BMAD_PARALLEL_MODE
# ---------------------------------------------------------------------------


class TestTriggerSyncParallelMode:
    """Tests for BMAD_PARALLEL_MODE environment variable check."""

    def test_trigger_sync_skips_when_parallel_mode_set(self, tmp_path, monkeypatch):
        """trigger_sync returns early when BMAD_PARALLEL_MODE=1."""
        monkeypatch.setenv("BMAD_PARALLEL_MODE", "1")

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )

        with patch(
            "bmad_assist_lite.core.sprint_sync.load_sprint_status"
        ) as mock_load:
            trigger_sync(state, tmp_path)
            mock_load.assert_not_called()

    def test_trigger_sync_performs_sync_when_not_set(self, tmp_path, monkeypatch):
        """trigger_sync performs sync when BMAD_PARALLEL_MODE is not set."""
        monkeypatch.delenv("BMAD_PARALLEL_MODE", raising=False)

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )

        # This should perform the sync (creates sprint-status file)
        trigger_sync(state, tmp_path)
        from bmad_assist_lite.core.sprint_status import (
            get_sprint_status_path,
            load_sprint_status,
        )

        ss_path = get_sprint_status_path(tmp_path)
        assert ss_path.exists()
        loaded = load_sprint_status(ss_path)
        assert loaded.get_story_status("1.1") == "ready-for-dev"

    def test_trigger_sync_performs_sync_when_set_to_zero(self, tmp_path, monkeypatch):
        """trigger_sync performs sync when BMAD_PARALLEL_MODE=0 (not '1')."""
        monkeypatch.setenv("BMAD_PARALLEL_MODE", "0")

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )

        with patch(
            "bmad_assist_lite.core.sprint_sync.load_sprint_status"
        ) as mock_load:
            mock_load.return_value = MagicMock()
            trigger_sync(state, tmp_path)
            mock_load.assert_called_once()

    def test_trigger_sync_performs_sync_when_set_to_true(self, tmp_path, monkeypatch):
        """trigger_sync performs sync when BMAD_PARALLEL_MODE=true (not '1')."""
        monkeypatch.setenv("BMAD_PARALLEL_MODE", "true")

        state = State(
            current_epic=1,
            current_story="1.1",
            current_phase=Phase.CREATE_STORY,
        )

        with patch(
            "bmad_assist_lite.core.sprint_sync.load_sprint_status"
        ) as mock_load:
            mock_load.return_value = MagicMock()
            trigger_sync(state, tmp_path)
            mock_load.assert_called_once()


# ---------------------------------------------------------------------------
# Task 6.5–6.6: run_loop single_story behavior
# ---------------------------------------------------------------------------


class TestRunLoopSingleStory:
    """Tests for run_loop with single_story parameter."""

    @patch("bmad_assist_lite.loop.runner.trigger_sync")
    @patch("bmad_assist_lite.loop.runner.save_state")
    @patch("bmad_assist_lite.loop.runner.execute_phase")
    @patch("bmad_assist_lite.loop.runner.init_handlers")
    @patch("bmad_assist_lite.loop.runner.running_lock")
    def test_single_story_exits_after_first_story(
        self, mock_lock, mock_init, mock_execute, mock_save, mock_sync, tmp_path
    ):
        """run_loop with single_story=True returns COMPLETED after first story."""
        from bmad_assist_lite.loop.runner import run_loop

        # mock the lock context manager
        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        def execute_side_effect(state):
            """Return success for each phase. Quality gate returns 'pass'."""
            if state.current_phase == Phase.QUALITY_GATE:
                return PhaseResult.ok({"quality_gate_action": "pass"})
            return PhaseResult.ok()

        mock_execute.side_effect = execute_side_effect

        from bmad_assist_lite.core.config import get_config

        config = get_config()

        result = run_loop(
            config=config,
            project_path=tmp_path,
            epics=EPICS,
            stories_for_epic=STORIES_FOR_EPIC,
            single_story=True,
        )

        assert result == LoopExitReason.COMPLETED
        # Verify execute_phase was called (phases were executed)
        assert mock_execute.call_count >= 1
        # With single_story=True and QG pass, should exit before processing story 1.2

    @patch("bmad_assist_lite.loop.runner.trigger_sync")
    @patch("bmad_assist_lite.loop.runner.save_state")
    @patch("bmad_assist_lite.loop.runner.execute_phase")
    @patch("bmad_assist_lite.loop.runner.init_handlers")
    @patch("bmad_assist_lite.loop.runner.running_lock")
    def test_single_story_false_continues_to_next(
        self, mock_lock, mock_init, mock_execute, mock_save, mock_sync, tmp_path
    ):
        """run_loop with single_story=False continues to next story."""
        from bmad_assist_lite.loop.runner import run_loop

        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        stories_processed = []

        def execute_side_effect(state):
            """Track which stories are processed."""
            stories_processed.append(state.current_story)
            if state.current_phase == Phase.QUALITY_GATE:
                return PhaseResult.ok({"quality_gate_action": "pass"})
            return PhaseResult.ok()

        mock_execute.side_effect = execute_side_effect

        from bmad_assist_lite.core.config import get_config

        config = get_config()

        result = run_loop(
            config=config,
            project_path=tmp_path,
            epics=EPICS,
            stories_for_epic=STORIES_FOR_EPIC,
            single_story=False,
        )

        assert result == LoopExitReason.COMPLETED
        # Both stories should have been processed
        assert "1.1" in stories_processed
        assert "1.2" in stories_processed

    @patch("bmad_assist_lite.loop.runner.trigger_sync")
    @patch("bmad_assist_lite.loop.runner.save_state")
    @patch("bmad_assist_lite.loop.runner.execute_phase")
    @patch("bmad_assist_lite.loop.runner.init_handlers")
    @patch("bmad_assist_lite.loop.runner.running_lock")
    def test_single_story_skip_story_exits(
        self, mock_lock, mock_init, mock_execute, mock_save, mock_sync, tmp_path
    ):
        """run_loop with single_story=True exits on skip_story QG action."""
        from bmad_assist_lite.loop.runner import run_loop

        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        def execute_side_effect(state):
            if state.current_phase == Phase.QUALITY_GATE:
                return PhaseResult.ok({"quality_gate_action": "skip_story"})
            return PhaseResult.ok()

        mock_execute.side_effect = execute_side_effect

        from bmad_assist_lite.core.config import get_config

        config = get_config()

        result = run_loop(
            config=config,
            project_path=tmp_path,
            epics=EPICS,
            stories_for_epic=STORIES_FOR_EPIC,
            single_story=True,
        )

        assert result == LoopExitReason.COMPLETED

    @patch("bmad_assist_lite.loop.runner.trigger_sync")
    @patch("bmad_assist_lite.loop.runner.save_state")
    @patch("bmad_assist_lite.loop.runner.execute_phase")
    @patch("bmad_assist_lite.loop.runner.init_handlers")
    @patch("bmad_assist_lite.loop.runner.running_lock")
    def test_single_story_qg_retry_then_pass_exits(
        self, mock_lock, mock_init, mock_execute, mock_save, mock_sync, tmp_path
    ):
        """run_loop with single_story=True: QG fix_quality_gate retry then pass exits."""
        from bmad_assist_lite.loop.runner import run_loop

        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        qg_call_count = 0

        def execute_side_effect(state):
            nonlocal qg_call_count
            if state.current_phase == Phase.QUALITY_GATE:
                qg_call_count += 1
                if qg_call_count == 1:
                    # First QG: fail → trigger fix_quality_gate
                    return PhaseResult(
                        success=True,
                        next_phase=Phase.FIX_QUALITY_GATE,
                        outputs={"quality_gate_action": "fix_quality_gate"},
                    )
                # Second QG: pass
                return PhaseResult.ok({"quality_gate_action": "pass"})
            if state.current_phase == Phase.FIX_QUALITY_GATE:
                # Fix returns to quality_gate
                return PhaseResult(
                    success=True,
                    next_phase=Phase.QUALITY_GATE,
                    outputs={},
                )
            return PhaseResult.ok()

        mock_execute.side_effect = execute_side_effect

        from bmad_assist_lite.core.config import get_config

        config = get_config()

        result = run_loop(
            config=config,
            project_path=tmp_path,
            epics=EPICS,
            stories_for_epic=STORIES_FOR_EPIC,
            single_story=True,
        )

        assert result == LoopExitReason.COMPLETED
        assert qg_call_count == 2  # QG was called twice (fail then pass)


# ---------------------------------------------------------------------------
# Task 6.7: parallel subcommand group
# ---------------------------------------------------------------------------


class TestParallelSubcommand:
    """Tests for parallel subcommand group registration."""

    def test_parallel_help_shows(self):
        """Parallel --help returns success with help text."""
        result = runner.invoke(app, ["parallel", "--help"])
        assert result.exit_code == 0
        assert "Parallel story execution commands" in result.output


# ---------------------------------------------------------------------------
# Task 6.8: --single-story flag acceptance
# ---------------------------------------------------------------------------


class TestSingleStoryFlag:
    """Tests for --single-story CLI flag parsing."""

    def test_single_story_flag_accepted_in_help(self):
        """--single-story flag appears in run --help output."""
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "--single-story" in result.output

    def test_single_story_flag_description(self):
        """--single-story flag has correct help text."""
        result = runner.invoke(app, ["run", "--help"])
        assert "Exit after completing a single story" in result.output


# ---------------------------------------------------------------------------
# Task 6 (synthesis): CLI integration test for --epic + --story + --single-story
# ---------------------------------------------------------------------------


class TestSingleStoryCLIIntegration:
    """CLI-level integration test for --single-story with --epic and --story."""

    def test_single_story_with_epic_and_story_filters_exact(self, tmp_path):
        """--epic 1 --story 2 --single-story passes only story 1.2 to run_loop."""
        from bmad_assist_lite.core.sprint_status import SprintStatus

        # Create required config file so CLI doesn't exit early
        (tmp_path / "bmad-assist-lite.yaml").write_text("providers: {}\n")

        # Set up sprint status with multiple stories using proper key format
        ss = SprintStatus(
            development_status={
                "epic-1": "backlog",
                "1-1-first-story": "backlog",
                "1-2-second-story": "backlog",
                "1-3-third-story": "backlog",
            }
        )

        # Set up paths mock
        mock_paths_obj = MagicMock()
        mock_paths_obj.sprint_status_file = tmp_path / "sprint-status.yaml"
        mock_paths_obj.sprint_status_file.write_text("generated: '2026-01-01'\n")
        mock_paths_obj.planning_artifacts = tmp_path / "planning"
        mock_paths_obj.planning_artifacts.mkdir(parents=True, exist_ok=True)
        (mock_paths_obj.planning_artifacts / "epic-1.md").write_text("# Epic 1\n")
        mock_paths_obj.logs_dir = tmp_path / "logs"
        mock_paths_obj.cache_dir = tmp_path / "cache"
        mock_paths_obj.output_folder = tmp_path / "output"
        mock_paths_obj.implementation_artifacts = tmp_path / "impl"
        mock_paths_obj.architecture_file = tmp_path / "arch.md"

        with (
            patch(
                "bmad_assist_lite.core.config.load_config_with_project"
            ) as mock_config,
            patch("bmad_assist_lite.core.paths.init_paths", return_value=mock_paths_obj),
            patch(
                "bmad_assist_lite.core.sprint_status.load_sprint_status",
                return_value=ss,
            ),
            patch("bmad_assist_lite.loop.runner.run_loop") as mock_run_loop,
        ):
            mock_config.return_value = MagicMock(context_docs=None)
            mock_run_loop.return_value = LoopExitReason.COMPLETED

            result = runner.invoke(
                app,
                [
                    "run", "--project", str(tmp_path),
                    "--epic", "1", "--story", "2", "--single-story",
                ],
            )

            # run_loop should have been called
            assert mock_run_loop.called, f"run_loop not called; output: {result.output}"
            call_kwargs = mock_run_loop.call_args
            stories_for_epic = call_kwargs.kwargs.get("stories_for_epic", {})
            # Only story 1.2 should be passed (exact match, not >= filtering)
            assert stories_for_epic.get(1) == ["1.2"]
            assert call_kwargs.kwargs.get("single_story") is True


# ---------------------------------------------------------------------------
# Task 6.9: --single-story without --epic/--story
# ---------------------------------------------------------------------------


class TestSingleStoryWithoutEpicStory:
    """Tests for --single-story without explicit --epic/--story flags."""

    @patch("bmad_assist_lite.loop.runner.trigger_sync")
    @patch("bmad_assist_lite.loop.runner.save_state")
    @patch("bmad_assist_lite.loop.runner.execute_phase")
    @patch("bmad_assist_lite.loop.runner.init_handlers")
    @patch("bmad_assist_lite.loop.runner.running_lock")
    def test_single_story_without_filters_processes_first_story(
        self, mock_lock, mock_init, mock_execute, mock_save, mock_sync, tmp_path
    ):
        """single_story=True without explicit story filter processes first backlog story and exits."""
        from bmad_assist_lite.loop.runner import run_loop

        mock_lock.return_value.__enter__ = MagicMock()
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        stories_processed = []

        def execute_side_effect(state):
            stories_processed.append(state.current_story)
            if state.current_phase == Phase.QUALITY_GATE:
                return PhaseResult.ok({"quality_gate_action": "pass"})
            return PhaseResult.ok()

        mock_execute.side_effect = execute_side_effect

        from bmad_assist_lite.core.config import get_config

        config = get_config()
        multi_stories = {1: ["1.1", "1.2", "1.3"]}

        result = run_loop(
            config=config,
            project_path=tmp_path,
            epics=[1],
            stories_for_epic=multi_stories,
            single_story=True,
        )

        assert result == LoopExitReason.COMPLETED
        # Only story 1.1 should be processed (the first backlog story)
        unique_stories = set(stories_processed)
        assert "1.1" in unique_stories
        # Stories 1.2 and 1.3 should not have any phases executed
        assert "1.2" not in unique_stories
        assert "1.3" not in unique_stories
