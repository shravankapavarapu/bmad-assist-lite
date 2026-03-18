"""Tests for bmad_assist_lite.core.toolchain."""

import json
import sys

from bmad_assist_lite.core.toolchain import ToolchainCommands, detect_toolchain


class TestDetectToolchain:
    """Tests for detect_toolchain."""

    def test_detect_pnpm(self, tmp_path):
        """pnpm-lock.yaml selects pnpm as package manager."""
        (tmp_path / "pnpm-lock.yaml").write_text("")
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"lint": "eslint .", "test": "vitest"}})
        )
        result = detect_toolchain(tmp_path)
        assert result.lint == "pnpm run lint"
        assert result.test == "pnpm run test"

    def test_detect_yarn(self, tmp_path):
        """yarn.lock selects yarn as package manager."""
        (tmp_path / "yarn.lock").write_text("")
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"lint": "eslint .", "build": "tsc"}})
        )
        result = detect_toolchain(tmp_path)
        assert result.lint == "yarn run lint"
        assert result.build == "yarn run build"

    def test_detect_npm_default(self, tmp_path):
        """No lock file defaults to npm."""
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}})
        )
        result = detect_toolchain(tmp_path)
        assert result.test == "npm run test"

    def test_detect_node_scripts(self, tmp_path):
        """Detects lint/typecheck/build/test from package.json scripts."""
        (tmp_path / "package.json").write_text(
            json.dumps({
                "scripts": {
                    "lint": "eslint .",
                    "typecheck": "tsc --noEmit",
                    "build": "vite build",
                    "test": "vitest",
                }
            })
        )
        result = detect_toolchain(tmp_path)
        assert result.lint is not None
        assert result.typecheck is not None
        assert result.build is not None
        assert result.test is not None

    def test_detect_python(self, tmp_path):
        """pyproject.toml triggers Python toolchain detection."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        result = detect_toolchain(tmp_path)
        assert result.lint == "ruff check src/"
        assert result.typecheck == "mypy src/"
        assert result.test == "pytest -q --tb=short --no-header"
        assert result.build is None

    def test_detect_rust(self, tmp_path):
        """Cargo.toml triggers Rust toolchain detection."""
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
        result = detect_toolchain(tmp_path)
        assert result.lint == "cargo clippy -- -D warnings"
        assert result.build == "cargo build"
        assert result.test == "cargo test"

    def test_empty_project(self, tmp_path):
        """Empty project returns empty ToolchainCommands."""
        result = detect_toolchain(tmp_path)
        assert result == ToolchainCommands()
        assert result.lint is None
        assert result.typecheck is None
        assert result.build is None
        assert result.test is None

    def test_node_preferred_over_python(self, tmp_path):
        """Node.js is preferred when both package.json and pyproject.toml exist."""
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}})
        )
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        result = detect_toolchain(tmp_path)
        assert result.test == "npm run test"

    def test_detect_test_unit_script(self, tmp_path):
        """test:unit script in package.json sets test_unit field."""
        (tmp_path / "pnpm-lock.yaml").write_text("")
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest", "test:unit": "vitest --project unit"}})
        )
        result = detect_toolchain(tmp_path)
        assert result.test == "pnpm run test"
        assert result.test_unit == "pnpm run test:unit"

    def test_no_test_unit_script(self, tmp_path):
        """Without test:unit script, test_unit stays None."""
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest"}})
        )
        result = detect_toolchain(tmp_path)
        assert result.test == "npm run test"
        assert result.test_unit is None

    def test_no_scripts_key(self, tmp_path):
        """package.json without scripts key falls through."""
        (tmp_path / "package.json").write_text(json.dumps({"name": "foo"}))
        result = detect_toolchain(tmp_path)
        assert result == ToolchainCommands()

    def test_python_with_venv(self, tmp_path):
        """Python project with .venv prefixes commands with venv python."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        venv_dir = tmp_path / ".venv"
        if sys.platform == "win32":
            scripts_dir = venv_dir / "Scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / "python.exe").write_text("")
            expected_prefix = f"{scripts_dir / 'python.exe'} -m "
        else:
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "python").write_text("")
            expected_prefix = f"{bin_dir / 'python'} -m "
        result = detect_toolchain(tmp_path)
        assert result.lint == f"{expected_prefix}ruff check src/"
        assert result.typecheck == f"{expected_prefix}mypy src/"
        assert result.test == f"{expected_prefix}pytest -q --tb=short --no-header"

    def test_python_without_venv(self, tmp_path):
        """Python project without .venv uses bare commands."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        result = detect_toolchain(tmp_path)
        assert result.lint == "ruff check src/"
        assert result.typecheck == "mypy src/"

    def test_python_with_empty_venv_dir(self, tmp_path):
        """Python project with .venv dir but no python binary uses bare commands."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        (tmp_path / ".venv").mkdir()
        result = detect_toolchain(tmp_path)
        assert result.lint == "ruff check src/"
