"""Shared test fixtures for bmad-assist-lite.

Autouse fixtures ensure clean singleton state between tests.
Markers control which fixtures apply and which tests run.
"""

from unittest.mock import patch

import pytest

from bmad_assist_lite.core.config import _reset_config, load_config
from bmad_assist_lite.core.paths import _reset_paths
from bmad_assist_lite.loop.dispatch import reset_handlers
from bmad_assist_lite.providers.cursor import _reset_cursor_cli_version

# ============================================================================
# Markers
# ============================================================================

# Registered in pyproject.toml [tool.pytest.ini_options]:
#   slow        — tests taking >5s, skipped by default
#   integration — integration tests
#   no_auto_config — skip the auto config loading fixture


# ============================================================================
# Autouse Fixtures
# ============================================================================


MINIMAL_CONFIG_DATA: dict = {
    "providers": {
        "master": {
            "provider": "claude",
            "model": "opus",
        },
    },
}


@pytest.fixture(autouse=True)
def reset_paths_singleton():
    """Reset the paths singleton before each test."""
    _reset_paths()
    yield
    _reset_paths()


@pytest.fixture(autouse=True)
def reset_config_singleton(request):
    """Load minimal valid config before each test.

    Opt out by marking a test with ``@pytest.mark.no_auto_config``.
    """
    _reset_config()
    if "no_auto_config" not in {m.name for m in request.node.iter_markers()}:
        load_config(MINIMAL_CONFIG_DATA)
    yield
    _reset_config()


@pytest.fixture(autouse=True)
def reset_loop_dispatch():
    """Reset loop dispatch handler registry between tests."""
    reset_handlers()
    yield
    reset_handlers()


@pytest.fixture(autouse=True)
def reset_cursor_cli_version_singleton():
    """Reset the Cursor CLI version cache before each test."""
    _reset_cursor_cli_version()
    yield
    _reset_cursor_cli_version()


@pytest.fixture(autouse=True)
def _mock_parallel_log_setup():
    """Prevent orchestrator tests from creating real log files.

    Patches ``setup_parallel_log`` and ``teardown_parallel_log`` as imported
    in the orchestrator module so ``Orchestrator.run()`` never opens a
    FileHandler on a fake project-root path.  Tests in
    ``test_parallel_logging.py`` that import the functions directly from
    ``bmad_assist_lite.parallel.logging`` are unaffected.
    """
    with patch(
        "bmad_assist_lite.parallel.orchestrator.setup_parallel_log",
    ), patch(
        "bmad_assist_lite.parallel.orchestrator.teardown_parallel_log",
    ), patch(
        "bmad_assist_lite.parallel.orchestrator.log_run_header",
    ), patch(
        "bmad_assist_lite.parallel.orchestrator.log_run_complete",
    ), patch(
        "bmad_assist_lite.parallel.orchestrator.write_report",
    ):
        yield
