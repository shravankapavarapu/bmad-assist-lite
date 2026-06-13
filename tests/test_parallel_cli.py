"""Test the parallel run CLI command and branch guard."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import Result
from typer.testing import CliRunner

from bmad_assist_lite.cli import app
from bmad_assist_lite.core.exceptions import ConfigError, ParserError, StateError
from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.exceptions import ParallelError

runner = CliRunner()

# ============================================================================
# Shared helpers
# ============================================================================

_BASE_PATCHES = {
    "branch": "bmad_assist_lite.parallel.git_ops.get_current_branch",
    "protected": "bmad_assist_lite.parallel.git_ops.is_protected_branch",
    "config": "bmad_assist_lite.core.config.load_config_with_project",
    "init_paths": "bmad_assist_lite.core.paths.init_paths",
    "find_epic": "bmad_assist_lite.cli._find_epic_file",
    "is_dedicated": "bmad_assist_lite.cli._is_dedicated_epic_file",
    "parse_epic": "bmad_assist_lite.bmad.parser.parse_epic_file",
    "dep_graph": "bmad_assist_lite.parallel.dependency_graph.DependencyGraph",
    "orchestrator": "bmad_assist_lite.parallel.orchestrator.Orchestrator",
    "lock": "bmad_assist_lite.loop.locking.running_lock",
    "asyncio_run": "bmad_assist_lite.parallel.cli.asyncio.run",
}


def _make_mock_graph(
    story_count: int = 3,
    ready_count: int = 2,
) -> MagicMock:
    """Create a mock DependencyGraph with the given story/ready counts."""
    graph = MagicMock()
    graph.story_count = story_count
    graph.get_ready_stories.return_value = [f"3.{i}" for i in range(1, ready_count + 1)]
    return graph


def _make_mock_paths(tmp_path: Path) -> MagicMock:
    """Create a mock ProjectPaths for testing."""
    paths = MagicMock()
    planning = tmp_path / "planning"
    planning.mkdir(exist_ok=True)
    paths.planning_artifacts = planning
    return paths


def _make_mock_epic_doc() -> MagicMock:
    """Create a mock EpicDocument with stories."""
    doc = MagicMock()
    doc.stories = [MagicMock(), MagicMock(), MagicMock()]
    return doc


def _make_mock_config(parallel_cfg: ParallelConfig | None = None) -> MagicMock:
    """Create a mock Config with optional parallel config."""
    config = MagicMock()
    config.parallel = parallel_cfg
    return config


def _invoke_run(tmp_path: Path, extra_args: list[str] | None = None) -> Result:
    """Invoke the parallel run command with defaults."""
    args = ["parallel", "run", "--project", str(tmp_path), "--epic", "3"]
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(app, args)


# ============================================================================
# TestBranchGuard
# ============================================================================


class TestBranchGuard:
    """Test branch guard enforcement."""

    def test_rejects_main(self, tmp_path: Path) -> None:
        """Branch guard rejects main with exit code 1 and correct message."""
        with (
            patch(_BASE_PATCHES["branch"], return_value="main"),
            patch(_BASE_PATCHES["protected"], return_value=True),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "Parallel mode cannot run on main/master" in result.output

    def test_rejects_master(self, tmp_path: Path) -> None:
        """Branch guard rejects master with exit code 1."""
        with (
            patch(_BASE_PATCHES["branch"], return_value="master"),
            patch(_BASE_PATCHES["protected"], return_value=True),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "Parallel mode cannot run on main/master" in result.output

    def test_allows_feature_branch(self, tmp_path: Path) -> None:
        """Branch guard allows feature branches without exit."""
        mock_graph = _make_mock_graph()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=_make_mock_config()),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(_BASE_PATCHES["lock"]),
            patch(_BASE_PATCHES["orchestrator"]),
            patch(_BASE_PATCHES["asyncio_run"]),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 0

    def test_rejects_detached_head(self, tmp_path: Path) -> None:
        """Branch guard rejects detached HEAD with exit code 1 and message."""
        with (
            patch(_BASE_PATCHES["branch"], return_value="HEAD"),
            patch(_BASE_PATCHES["protected"], return_value=False),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "detached HEAD state" in result.output

    def test_git_error_exits_with_1(self, tmp_path: Path) -> None:
        """ParallelError from get_current_branch exits with code 1."""
        with patch(
            _BASE_PATCHES["branch"],
            side_effect=ParallelError("git executable not found on PATH"),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "Git error" in result.output


# ============================================================================
# TestConfigLoading
# ============================================================================


class TestConfigLoading:
    """Test configuration loading and fallback defaults."""

    def test_default_config_values(self) -> None:
        """ParallelConfig() defaults to max_concurrency=3, stagger_delay=10.0."""
        config = ParallelConfig()
        assert config.max_concurrency == 3
        assert config.stagger_delay == 10.0

    def test_uses_defaults_when_parallel_missing(self, tmp_path: Path) -> None:
        """When app config has no parallel section, defaults are used."""
        mock_graph = _make_mock_graph()
        app_config = _make_mock_config(parallel_cfg=None)

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(_BASE_PATCHES["lock"]),
            patch(_BASE_PATCHES["orchestrator"]) as mock_orch_cls,
            patch(_BASE_PATCHES["asyncio_run"]),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 0
        # Verify the orchestrator was constructed with default ParallelConfig
        actual_config = mock_orch_cls.call_args.kwargs["config"]
        assert actual_config.max_concurrency == 3
        assert actual_config.stagger_delay == 10.0

    def test_config_error_exits(self, tmp_path: Path) -> None:
        """ConfigError from config loading produces exit code 1."""
        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(
                _BASE_PATCHES["config"],
                side_effect=ConfigError("bad config"),
            ),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "Config error" in result.output


# ============================================================================
# TestSettingsSummary
# ============================================================================


class TestSettingsSummary:
    """Test startup settings summary output."""

    def test_settings_summary_printed(self, tmp_path: Path) -> None:
        """All settings summary fields appear in output."""
        mock_graph = _make_mock_graph(story_count=5, ready_count=3)
        parallel_cfg = ParallelConfig(max_concurrency=2, stagger_delay=5.0)
        app_config = _make_mock_config(parallel_cfg=parallel_cfg)

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(_BASE_PATCHES["lock"]),
            patch(_BASE_PATCHES["orchestrator"]),
            patch(_BASE_PATCHES["asyncio_run"]),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 0
        output = result.output
        assert "Max concurrency: 2" in output
        assert "Stagger delay: 5.0s" in output
        assert "Base branch: epic/3" in output
        assert "Epic: 3" in output
        assert "Total stories: 5" in output
        assert "Ready stories: 3" in output


# ============================================================================
# TestOrchestratorStartup
# ============================================================================


class TestOrchestratorStartup:
    """Test orchestrator construction and execution."""

    def test_orchestrator_called_with_correct_params(self, tmp_path: Path) -> None:
        """Orchestrator is constructed with correct parameters."""
        mock_graph = _make_mock_graph()
        parallel_cfg = ParallelConfig(max_concurrency=2, stagger_delay=5.0)
        app_config = _make_mock_config(parallel_cfg=parallel_cfg)

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(_BASE_PATCHES["lock"]),
            patch(_BASE_PATCHES["orchestrator"]) as mock_orch_cls,
            patch(_BASE_PATCHES["asyncio_run"]),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 0
        mock_orch_cls.assert_called_once()
        call_kwargs = mock_orch_cls.call_args.kwargs
        assert call_kwargs["dependency_graph"] is mock_graph
        assert call_kwargs["config"] is parallel_cfg
        assert call_kwargs["project_root"] == tmp_path.resolve()
        assert call_kwargs["epic_num"] == 3
        assert call_kwargs["base_branch"] == "epic/3"

    def test_asyncio_run_called(self, tmp_path: Path) -> None:
        """asyncio.run() is called with orchestrator.run()."""
        mock_graph = _make_mock_graph()
        app_config = _make_mock_config()
        mock_orch_instance = MagicMock()
        mock_orch_instance.run = MagicMock()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(_BASE_PATCHES["lock"]),
            patch(_BASE_PATCHES["orchestrator"], return_value=mock_orch_instance),
            patch(_BASE_PATCHES["asyncio_run"]) as mock_arun,
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 0
        mock_arun.assert_called_once()
        # The argument to asyncio.run should be the coroutine from orchestrator.run()
        call_args = mock_arun.call_args[0]
        assert len(call_args) == 1

    def test_lock_acquisition(self, tmp_path: Path) -> None:
        """running_lock is called with the project path."""
        mock_graph = _make_mock_graph()
        app_config = _make_mock_config()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(_BASE_PATCHES["lock"]) as mock_lock,
            patch(_BASE_PATCHES["orchestrator"]),
            patch(_BASE_PATCHES["asyncio_run"]),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 0
        mock_lock.assert_called_once_with(tmp_path.resolve())


# ============================================================================
# TestErrorHandling
# ============================================================================


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_lock_conflict_exits_with_1(self, tmp_path: Path) -> None:
        """StateError from lock acquisition exits with code 1."""
        mock_graph = _make_mock_graph()
        app_config = _make_mock_config()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(
                _BASE_PATCHES["lock"],
                side_effect=StateError("lock conflict"),
            ),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "Another bmad-assist-lite instance" in result.output

    def test_missing_epic_file_exits_with_1(self, tmp_path: Path) -> None:
        """Missing epic file produces error and exit code 1."""
        app_config = _make_mock_config()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=None),
            patch(_BASE_PATCHES["is_dedicated"], return_value=False),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "No dedicated epic file" in result.output

    def test_parallel_error_from_orchestrator_exits_with_1(
        self, tmp_path: Path
    ) -> None:
        """ParallelError from orchestrator exits with code 1."""
        mock_graph = _make_mock_graph()
        app_config = _make_mock_config()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(_BASE_PATCHES["lock"]),
            patch(_BASE_PATCHES["orchestrator"]),
            patch(
                _BASE_PATCHES["asyncio_run"],
                side_effect=ParallelError("orchestrator boom"),
            ),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "Parallel run error" in result.output

    def test_keyboard_interrupt_exits_with_130(self, tmp_path: Path) -> None:
        """KeyboardInterrupt exits with code 130 and friendly message."""
        mock_graph = _make_mock_graph()
        app_config = _make_mock_config()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(_BASE_PATCHES["lock"]),
            patch(_BASE_PATCHES["orchestrator"]),
            patch(
                _BASE_PATCHES["asyncio_run"],
                side_effect=KeyboardInterrupt,
            ),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 130
        assert "Parallel run interrupted" in result.output

    def test_cancelled_error_exits_with_130(self, tmp_path: Path) -> None:
        """asyncio.CancelledError exits with code 130 and friendly message."""
        mock_graph = _make_mock_graph()
        app_config = _make_mock_config()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(_BASE_PATCHES["dep_graph"], return_value=mock_graph),
            patch(_BASE_PATCHES["lock"]),
            patch(_BASE_PATCHES["orchestrator"]),
            patch(
                _BASE_PATCHES["asyncio_run"],
                side_effect=asyncio.CancelledError,
            ),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 130
        assert "Parallel run interrupted" in result.output

    def test_epic_parse_error_exits_with_1(self, tmp_path: Path) -> None:
        """ParserError from parse_epic_file exits with code 1."""
        app_config = _make_mock_config()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(
                _BASE_PATCHES["parse_epic"],
                side_effect=ParserError("malformed epic file"),
            ),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "Epic parse error" in result.output

    def test_circular_dependency_exits_with_1(self, tmp_path: Path) -> None:
        """ParallelError from DependencyGraph cycle detection exits with 1."""
        app_config = _make_mock_config()

        with (
            patch(_BASE_PATCHES["branch"], return_value="epic/3"),
            patch(_BASE_PATCHES["protected"], return_value=False),
            patch(_BASE_PATCHES["config"], return_value=app_config),
            patch(_BASE_PATCHES["init_paths"], return_value=_make_mock_paths(tmp_path)),
            patch(_BASE_PATCHES["find_epic"], return_value=tmp_path / "epic-3.md"),
            patch(_BASE_PATCHES["is_dedicated"], return_value=True),
            patch(_BASE_PATCHES["parse_epic"], return_value=_make_mock_epic_doc()),
            patch(
                _BASE_PATCHES["dep_graph"],
                side_effect=ParallelError("Circular dependency: 3.1 -> 3.2 -> 3.1"),
            ),
        ):
            result = _invoke_run(tmp_path)

        assert result.exit_code == 1
        assert "Dependency graph error" in result.output
