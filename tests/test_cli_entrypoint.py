"""Tests for the ``python -m`` entry points.

``python -m bmad_assist_lite.cli`` must dispatch to the Typer app. Without a
``__main__`` guard the module is imported, nothing runs, and the process exits
0 with no output — a failure shape indistinguishable from "there was nothing
to do".
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def _run_module(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m <module>`` with the package importable."""
    import os

    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_ROOT}{os.pathsep}{existing}" if existing else str(SRC_ROOT)
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )


class TestModuleEntryPoints:
    """``-m`` invocation actually dispatches instead of silently no-opping."""

    def test_cli_module_dispatches(self) -> None:
        """LOAD-BEARING: ``python -m bmad_assist_lite.cli --version`` runs the app.

        Exits 0 AND prints. A bare exit 0 with empty output is the silent
        no-op this test exists to forbid.
        """
        proc = _run_module("bmad_assist_lite.cli", "--version")

        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        assert proc.stdout.strip(), "module form produced no output — it silently no-opped"
        assert "bmad-assist-lite" in proc.stdout

    def test_cli_module_reports_unknown_command(self) -> None:
        """An unknown command must fail loudly, not exit 0 in silence."""
        proc = _run_module("bmad_assist_lite.cli", "definitely-not-a-command")

        assert proc.returncode != 0
        assert (proc.stdout + proc.stderr).strip()

    def test_package_module_still_dispatches(self) -> None:
        """``python -m bmad_assist_lite`` keeps working (no regression)."""
        proc = _run_module("bmad_assist_lite", "--version")

        assert proc.returncode == 0, f"stderr: {proc.stderr}"
        assert "bmad-assist-lite" in proc.stdout
