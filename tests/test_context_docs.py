"""Tests for context_docs module: cache, detector, resolver, and config."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from bmad_assist_lite.context_docs.cache import LibDocsCache, _sanitize_name
from bmad_assist_lite.context_docs.detector import (
    _detect_from_cargo,
    _detect_from_package_json,
    _detect_from_pyproject,
    _detect_from_requirements,
    _scan_doc_for_frameworks,
    detect_libraries,
)
from bmad_assist_lite.core.config import _reset_config, load_config


# ============================================================================
# Cache Tests
# ============================================================================


class TestSanitizeName:
    def test_basic_name(self) -> None:
        assert _sanitize_name("react") == "react"

    def test_scoped_package(self) -> None:
        assert _sanitize_name("@types/node") == "_types_node"

    def test_spaces_and_caps(self) -> None:
        assert _sanitize_name("  React  ") == "react"

    def test_special_chars(self) -> None:
        assert _sanitize_name("my/lib@2.0") == "my_lib_2.0"


class TestLibDocsCache:
    def test_write_and_read(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        cache.write_library("react", "# React Docs\nSome content")

        assert cache.has_library("react")
        assert cache.read_library("react") == "# React Docs\nSome content"

    def test_read_missing(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        assert not cache.has_library("nonexistent")
        assert cache.read_library("nonexistent") is None

    def test_write_creates_dirs(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "deep" / "nested"
        cache = LibDocsCache(cache_dir)
        cache.write_library("vue", "vue docs")
        assert cache.read_library("vue") == "vue docs"

    def test_overwrite(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        cache.write_library("lib", "v1")
        cache.write_library("lib", "v2")
        assert cache.read_library("lib") == "v2"


class TestEpicLibsTracking:
    def test_set_and_get(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        cache.set_epic_libs("epic-1", ["react", "next.js"])
        assert cache.get_epic_libs("epic-1") == ["react", "next.js"]

    def test_get_unresolved(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        assert cache.get_epic_libs("epic-99") is None

    def test_get_libs_for_epic(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        cache.write_library("react", "react docs")
        cache.write_library("vue", "vue docs")
        cache.set_epic_libs("epic-1", ["react", "vue", "missing"])

        docs = cache.get_libs_for_epic("epic-1")
        assert docs == {"react": "react docs", "vue": "vue docs"}

    def test_get_libs_for_unresolved_epic(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        assert cache.get_libs_for_epic("epic-99") == {}

    def test_multiple_epics(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        cache.set_epic_libs("epic-1", ["react"])
        cache.set_epic_libs("epic-2", ["vue"])
        assert cache.get_epic_libs("epic-1") == ["react"]
        assert cache.get_epic_libs("epic-2") == ["vue"]


# ============================================================================
# Detector Tests
# ============================================================================


class TestDetectFromPackageJson:
    def test_basic_deps(self, tmp_path: Path) -> None:
        pkg = {
            "dependencies": {"react": "^18.0.0", "next": "^14.0.0"},
            "devDependencies": {"jest": "^29.0.0", "webpack": "^5.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = _detect_from_package_json(tmp_path)
        assert "react" in result
        assert "next" in result
        assert "webpack" in result
        # jest is in skip list
        assert "jest" not in result

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _detect_from_package_json(tmp_path) == []

    def test_malformed_json(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{bad json")
        assert _detect_from_package_json(tmp_path) == []


class TestDetectFromPyproject:
    def test_basic_deps(self, tmp_path: Path) -> None:
        content = """\
[project]
dependencies = [
    "fastapi>=0.100.0",
    "pydantic>=2.0.0",
    "uvicorn>=0.20.0",
]
"""
        (tmp_path / "pyproject.toml").write_text(content)
        result = _detect_from_pyproject(tmp_path)
        assert "fastapi" in result
        assert "uvicorn" in result
        # pydantic is not in skip list
        assert "pydantic" in result

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _detect_from_pyproject(tmp_path) == []


class TestDetectFromRequirements:
    def test_basic(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text(
            "flask>=2.0\nredis>=4.0\n# comment\n-r other.txt\n"
        )
        result = _detect_from_requirements(tmp_path)
        assert "flask" in result
        assert "redis" in result

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _detect_from_requirements(tmp_path) == []


class TestDetectFromCargo:
    def test_basic(self, tmp_path: Path) -> None:
        content = """\
[package]
name = "my-app"

[dependencies]
tokio = { version = "1", features = ["full"] }
serde = "1.0"

[dev-dependencies]
criterion = "0.5"
"""
        (tmp_path / "Cargo.toml").write_text(content)
        result = _detect_from_cargo(tmp_path)
        assert "tokio" in result
        assert "serde" in result
        assert "criterion" in result

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _detect_from_cargo(tmp_path) == []


class TestScanDocForFrameworks:
    def test_finds_react(self) -> None:
        text = "We use React with Next.js for the frontend."
        result = _scan_doc_for_frameworks(text)
        assert "react" in result
        assert "next.js" in result

    def test_finds_python_frameworks(self) -> None:
        text = "Backend built with FastAPI and SQLAlchemy on PostgreSQL."
        result = _scan_doc_for_frameworks(text)
        assert "fastapi" in result
        assert "sqlalchemy" in result
        assert "postgresql" in result

    def test_empty_text(self) -> None:
        assert _scan_doc_for_frameworks("") == []


class TestDetectLibraries:
    def test_combines_sources(self, tmp_path: Path) -> None:
        # package.json with react
        pkg = {"dependencies": {"react": "^18.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        # Architecture doc mentioning FastAPI
        arch_file = tmp_path / "architecture.md"
        arch_file.write_text("Backend uses FastAPI with Redis caching.")

        result = detect_libraries(tmp_path, architecture_file=arch_file, max_libs=10)
        assert "react" in result
        assert "fastapi" in result
        assert "redis" in result

    def test_deduplication(self, tmp_path: Path) -> None:
        pkg = {"dependencies": {"react": "^18.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        # Also mention react in docs
        doc = tmp_path / "doc.md"
        doc.write_text("We use React for the UI.")

        result = detect_libraries(tmp_path, epic_file=doc)
        assert result.count("react") == 1

    def test_max_libs_cap(self, tmp_path: Path) -> None:
        pkg = {"dependencies": {f"lib-{i}": "1.0" for i in range(20)}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = detect_libraries(tmp_path, max_libs=5)
        assert len(result) == 5

    def test_empty_project(self, tmp_path: Path) -> None:
        assert detect_libraries(tmp_path) == []


# ============================================================================
# Resolver Tests
# ============================================================================


class TestResolveEpicDocs:
    def test_returns_cached_docs(self, tmp_path: Path) -> None:
        """If epic already resolved, return cached docs without HTTP calls."""
        from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

        cache = LibDocsCache(tmp_path)
        cache.write_library("react", "cached react docs")
        cache.set_epic_libs("epic-1", ["react"])

        result = resolve_epic_docs(
            epic_num=1,
            project_root=tmp_path,
            cache_dir=tmp_path,
        )
        assert result == {"react": "cached react docs"}

    def test_empty_project_no_http(self, tmp_path: Path) -> None:
        """Empty project (no deps) should return {} without HTTP calls."""
        from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

        result = resolve_epic_docs(
            epic_num=1,
            project_root=tmp_path,
            cache_dir=tmp_path,
        )
        assert result == {}

    @patch("bmad_assist_lite.context_docs.resolver._get_httpx")
    @patch("bmad_assist_lite.context_docs.resolver._resolve_library_id")
    @patch("bmad_assist_lite.context_docs.resolver._fetch_library_docs")
    def test_fetches_and_caches(
        self,
        mock_fetch: MagicMock,
        mock_resolve: MagicMock,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should call Context7 API for detected libraries and cache results."""
        from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

        # Set up project with package.json
        pkg = {"dependencies": {"express": "^4.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        mock_httpx.return_value = MagicMock()
        mock_resolve.return_value = "/expressjs/express"
        mock_fetch.return_value = "# Express.js Docs\nRouting..."

        result = resolve_epic_docs(
            epic_num=1,
            project_root=tmp_path,
            cache_dir=tmp_path,
        )

        assert "express" in result
        assert "Express.js Docs" in result["express"]
        mock_resolve.assert_called_once()
        mock_fetch.assert_called_once()

        # Verify it was cached
        cache = LibDocsCache(tmp_path)
        assert cache.has_library("express")
        assert cache.get_epic_libs("epic-1") == ["express"]

    @patch("bmad_assist_lite.context_docs.resolver._get_httpx")
    @patch("bmad_assist_lite.context_docs.resolver._resolve_library_id")
    def test_skips_unresolvable_libraries(
        self,
        mock_resolve: MagicMock,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Libraries that can't be resolved should be skipped gracefully."""
        from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

        pkg = {"dependencies": {"obscure-lib": "1.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        mock_httpx.return_value = MagicMock()
        mock_resolve.return_value = None  # Can't resolve

        result = resolve_epic_docs(
            epic_num=1,
            project_root=tmp_path,
            cache_dir=tmp_path,
        )
        assert result == {}

    @patch(
        "bmad_assist_lite.context_docs.resolver._get_httpx",
        side_effect=ImportError("httpx not installed"),
    )
    def test_missing_httpx_returns_empty(
        self,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Missing httpx should log warning and return empty dict."""
        from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

        pkg = {"dependencies": {"react": "^18.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = resolve_epic_docs(
            epic_num=1,
            project_root=tmp_path,
            cache_dir=tmp_path,
        )
        assert result == {}


# ============================================================================
# Injection Tests
# ============================================================================


class TestInjectLibraryDocs:
    def test_injects_into_context(self, tmp_path: Path) -> None:
        from bmad_assist_lite.compiler.types import CompilerContext
        from bmad_assist_lite.context_docs.resolver import inject_library_docs

        # Pre-populate cache
        cache = LibDocsCache(tmp_path / ".bmad-assist-lite" / "cache")
        cache.write_library("react", "react docs content")
        cache.set_epic_libs("epic-1", ["react"])

        context = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "output",
            resolved_variables={"epic_num": 1},
        )

        inject_library_docs(context)

        assert "library-docs/react" in context.file_contents
        assert context.file_contents["library-docs/react"] == "react docs content"

    def test_noop_without_epic_num(self, tmp_path: Path) -> None:
        from bmad_assist_lite.compiler.types import CompilerContext
        from bmad_assist_lite.context_docs.resolver import inject_library_docs

        context = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "output",
        )

        inject_library_docs(context)
        assert not context.file_contents

    def test_noop_without_cached_docs(self, tmp_path: Path) -> None:
        from bmad_assist_lite.compiler.types import CompilerContext
        from bmad_assist_lite.context_docs.resolver import inject_library_docs

        context = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "output",
            resolved_variables={"epic_num": 99},
        )

        inject_library_docs(context)
        assert not context.file_contents


# ============================================================================
# Config Model Tests
# ============================================================================


class TestContextDocsConfig:
    def test_config_with_context_docs(self) -> None:
        _reset_config()
        cfg = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "context_docs": {"enabled": True, "max_libs": 5, "max_tokens_per_lib": 3000},
            }
        )
        assert cfg.context_docs is not None
        assert cfg.context_docs.enabled is True
        assert cfg.context_docs.max_libs == 5
        assert cfg.context_docs.max_tokens_per_lib == 3000

    def test_config_without_context_docs(self) -> None:
        _reset_config()
        cfg = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
            }
        )
        assert cfg.context_docs is None

    def test_config_defaults(self) -> None:
        _reset_config()
        cfg = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "context_docs": {},
            }
        )
        assert cfg.context_docs is not None
        assert cfg.context_docs.enabled is True
        assert cfg.context_docs.max_libs == 8
        assert cfg.context_docs.max_tokens_per_lib == 5000
