"""Auto-detect project build toolchain commands."""

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolchainCommands:
    """Detected build/lint/test commands for a project."""

    lint: str | None = None
    typecheck: str | None = None
    build: str | None = None
    test: str | None = None
    test_unit: str | None = None


def _detect_package_manager(project_root: Path) -> str:
    """Detect JS/TS package manager from lock files."""
    if (project_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _detect_node(project_root: Path) -> ToolchainCommands | None:
    """Detect commands from package.json scripts."""
    pkg_json = project_root / "package.json"
    if not pkg_json.exists():
        return None

    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse package.json: %s", e)
        return None

    scripts = data.get("scripts", {})
    if not isinstance(scripts, dict):
        return None

    pm = _detect_package_manager(project_root)
    run_prefix = f"{pm} run" if pm != "npm" else "npm run"

    lint = f"{run_prefix} lint" if "lint" in scripts else None
    typecheck = f"{run_prefix} typecheck" if "typecheck" in scripts else None
    build = f"{run_prefix} build" if "build" in scripts else None
    test = f"{run_prefix} test" if "test" in scripts else None
    test_unit = f"{run_prefix} test:unit" if "test:unit" in scripts else None

    if any([lint, typecheck, build, test]):
        return ToolchainCommands(
            lint=lint, typecheck=typecheck, build=build, test=test, test_unit=test_unit
        )
    return None


def _detect_python(project_root: Path) -> ToolchainCommands | None:
    """Detect Python toolchain from pyproject.toml."""
    if not (project_root / "pyproject.toml").exists():
        return None

    prefix = _detect_venv_prefix(project_root)
    return ToolchainCommands(
        lint=f"{prefix}ruff check src/",
        typecheck=f"{prefix}mypy src/",
        test=f"{prefix}pytest -q --tb=short --no-header",
    )


def _detect_venv_prefix(project_root: Path) -> str:
    """Return a command prefix for the project's .venv, or empty string."""
    venv_dir = project_root / ".venv"
    if not venv_dir.is_dir():
        return ""
    if sys.platform == "win32":
        python = venv_dir / "Scripts" / "python.exe"
    else:
        python = venv_dir / "bin" / "python"
    if not python.exists():
        return ""
    logger.debug("Detected .venv at %s", venv_dir)
    return f"{python} -m "


def _detect_rust(project_root: Path) -> ToolchainCommands | None:
    """Detect Rust toolchain from Cargo.toml."""
    if not (project_root / "Cargo.toml").exists():
        return None

    return ToolchainCommands(
        lint="cargo clippy -- -D warnings",
        build="cargo build",
        test="cargo test",
    )


def detect_toolchain(project_root: Path) -> ToolchainCommands:
    """Auto-detect project build commands from project root.

    Detection order: Node.js > Python > Rust.
    Returns empty ToolchainCommands if nothing detected.
    """
    for detector in (_detect_node, _detect_python, _detect_rust):
        result = detector(project_root)
        if result is not None:
            logger.info("Detected toolchain: %s", result)
            return result

    logger.info("No toolchain detected for %s", project_root)
    return ToolchainCommands()
