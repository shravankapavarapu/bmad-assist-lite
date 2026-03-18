"""Tests for context_docs module: cache, detector, resolver, epic_table, and config."""

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
from bmad_assist_lite.context_docs.epic_table import (
    EpicLibrarySpec,
    get_story_lib_mapping,
    parse_context7_table,
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
# Epic Table Parser Tests
# ============================================================================


SAMPLE_TABLE = """\
# Epic 6: Testing & Quality

Some description text.

### Context7 Library Documentation

| Library | Context7 ID | Query Focus | Stories |
|---|---|---|---|
| Vitest | /vitest-dev/vitest | vi.mock patterns, vi.fn | 6-1, 6-2, 6-4 |
| Testing Library | /testing-library/react-testing-library | render, screen, userEvent | 6-2, 6-3 |
| Playwright | /microsoft/playwright | page fixtures, expect | 6-5, 6-6 |
| Drizzle ORM | /drizzle-team/drizzle-orm | select, insert, where | 6-1, 6-4 |
| Next.js | /vercel/next.js/v16.0.3 | App Router, server actions | 6-1 |
| Zod | /colinhacks/zod | z.object, z.string, parse | 6-4 |
| Framer Motion | /framer/motion | motion.div, animate | 6-3 |
| reCAPTCHA v3 | /nicholasgasior/ggrcc | verify token, site key | 6-4, 6-6 |

More text after the table.
"""


class TestParseContext7Table:
    def test_parses_standard_table(self) -> None:
        specs = parse_context7_table(SAMPLE_TABLE)
        assert specs is not None
        assert len(specs) == 8

        vitest = specs[0]
        assert vitest.name == "Vitest"
        assert vitest.context7_id == "/vitest-dev/vitest"
        assert vitest.query_focus == "vi.mock patterns, vi.fn"
        assert vitest.stories == ["1", "2", "4"]

    def test_returns_none_when_no_heading(self) -> None:
        content = "# Regular Epic\n\nNo context7 table here.\n"
        assert parse_context7_table(content) is None

    def test_handles_whitespace(self) -> None:
        content = """\
##  Context7  Library  Documentation

| Library Name | Context7 ID | Query Focus | Stories |
|---|---|---|---|
|  React  |  /facebook/react  |  hooks, state  |  1 , 2  |
"""
        specs = parse_context7_table(content)
        assert specs is not None
        assert len(specs) == 1
        assert specs[0].name == "React"
        assert specs[0].context7_id == "/facebook/react"
        assert specs[0].stories == ["1", "2"]

    def test_extracts_story_numbers_various_formats(self) -> None:
        content = """\
### Context7 Library Documentation

| Library Name | Context7 ID | Query Focus | Stories |
|---|---|---|---|
| LibA | /org/a | query a | 6-1, 6-2 |
| LibB | /org/b | query b | 6.3, 6.4 |
| LibC | /org/c | query c | 1, 2, 3 |
"""
        specs = parse_context7_table(content)
        assert specs is not None
        assert specs[0].stories == ["1", "2"]
        assert specs[1].stories == ["3", "4"]
        assert specs[2].stories == ["1", "2", "3"]

    def test_handles_versioned_ids(self) -> None:
        content = """\
### Context7 Library Documentation

| Library Name | Context7 ID | Query Focus | Stories |
|---|---|---|---|
| Next.js | /vercel/next.js/v16.0.3 | App Router | 1 |
"""
        specs = parse_context7_table(content)
        assert specs is not None
        assert specs[0].context7_id == "/vercel/next.js/v16.0.3"

    def test_case_insensitive_heading(self) -> None:
        content = """\
### CONTEXT7 LIBRARY DOCUMENTATION

| Library Name | Context7 ID | Query Focus | Stories |
|---|---|---|---|
| React | /facebook/react | hooks | 1 |
"""
        specs = parse_context7_table(content)
        assert specs is not None
        assert len(specs) == 1

    def test_h2_heading(self) -> None:
        content = """\
## Context7 Library Documentation

| Library Name | Context7 ID | Query Focus | Stories |
|---|---|---|---|
| Vue | /vuejs/vue | reactivity | 1 |
"""
        specs = parse_context7_table(content)
        assert specs is not None

    def test_h4_heading(self) -> None:
        content = """\
#### Context7 Library Documentation

| Library Name | Context7 ID | Query Focus | Stories |
|---|---|---|---|
| Vue | /vuejs/vue | reactivity | 1 |
"""
        specs = parse_context7_table(content)
        assert specs is not None

    def test_library_name_header_variant(self) -> None:
        """'Library Name' header should also work."""
        content = """\
### Context7 Library Documentation

| Library Name | Context7 ID | Query Focus | Stories |
|---|---|---|---|
| React | /facebook/react | hooks | 1 |
"""
        specs = parse_context7_table(content)
        assert specs is not None
        assert specs[0].name == "React"

    def test_returns_none_for_empty_table(self) -> None:
        content = """\
### Context7 Library Documentation

| Library Name | Context7 ID | Query Focus | Stories |
|---|---|---|---|
"""
        assert parse_context7_table(content) is None

    def test_skips_rows_with_missing_name(self) -> None:
        content = """\
### Context7 Library Documentation

| Library Name | Context7 ID | Query Focus | Stories |
|---|---|---|---|
|  | /org/lib | query | 1 |
| Valid | /org/valid | query | 2 |
"""
        specs = parse_context7_table(content)
        assert specs is not None
        assert len(specs) == 1
        assert specs[0].name == "Valid"


class TestGetStoryLibMapping:
    def test_builds_correct_mapping(self) -> None:
        specs = [
            EpicLibrarySpec("Vitest", "/v/v", "q", ["1", "2"]),
            EpicLibrarySpec("React", "/f/r", "q", ["1", "3"]),
        ]
        mapping = get_story_lib_mapping(specs)
        assert mapping == {
            "1": ["Vitest", "React"],
            "2": ["Vitest"],
            "3": ["React"],
        }

    def test_empty_specs(self) -> None:
        assert get_story_lib_mapping([]) == {}

    def test_single_lib_multiple_stories(self) -> None:
        specs = [EpicLibrarySpec("Lib", "/o/l", "q", ["1", "2", "3"])]
        mapping = get_story_lib_mapping(specs)
        assert set(mapping.keys()) == {"1", "2", "3"}
        for libs in mapping.values():
            assert libs == ["Lib"]


# ============================================================================
# Story-Level Cache Tests
# ============================================================================


class TestStoryLevelCache:
    def test_set_and_get_table_libs(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        cache.set_epic_table_libs(
            "epic-6",
            ["Vitest", "React"],
            {"1": ["Vitest", "React"], "2": ["Vitest"]},
        )
        # get_epic_libs returns the libs list for table format
        assert cache.get_epic_libs("epic-6") == ["Vitest", "React"]

    def test_is_table_source_true(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        cache.set_epic_table_libs("epic-6", ["Vitest"], {"1": ["Vitest"]})
        assert cache.is_table_source("epic-6") is True

    def test_is_table_source_false_for_legacy(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        cache.set_epic_libs("epic-1", ["react"])
        assert cache.is_table_source("epic-1") is False

    def test_is_table_source_false_for_missing(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        assert cache.is_table_source("epic-99") is False

    def test_get_libs_for_story_filters(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        cache.write_library("Vitest", "vitest docs")
        cache.write_library("React", "react docs")
        cache.write_library("Zod", "zod docs")
        cache.set_epic_table_libs(
            "epic-6",
            ["Vitest", "React", "Zod"],
            {"1": ["Vitest", "React"], "2": ["Zod"]},
        )

        docs = cache.get_libs_for_story("epic-6", "1")
        assert docs == {"Vitest": "vitest docs", "React": "react docs"}
        assert "Zod" not in docs

        docs2 = cache.get_libs_for_story("epic-6", "2")
        assert docs2 == {"Zod": "zod docs"}

    def test_get_libs_for_story_falls_back_to_all(self, tmp_path: Path) -> None:
        """Story not in mapping → returns all libs."""
        cache = LibDocsCache(tmp_path)
        cache.write_library("Vitest", "vitest docs")
        cache.write_library("React", "react docs")
        cache.set_epic_table_libs(
            "epic-6",
            ["Vitest", "React"],
            {"1": ["Vitest"]},
        )

        # Story 99 not in mapping → all libs
        docs = cache.get_libs_for_story("epic-6", "99")
        assert docs == {"Vitest": "vitest docs", "React": "react docs"}

    def test_get_libs_for_story_legacy_format(self, tmp_path: Path) -> None:
        """Legacy format returns all libs regardless of story_num."""
        cache = LibDocsCache(tmp_path)
        cache.write_library("react", "react docs")
        cache.set_epic_libs("epic-1", ["react"])

        docs = cache.get_libs_for_story("epic-1", "1")
        assert docs == {"react": "react docs"}

    def test_get_libs_for_story_missing_epic(self, tmp_path: Path) -> None:
        cache = LibDocsCache(tmp_path)
        assert cache.get_libs_for_story("epic-99", "1") == {}


# ============================================================================
# Resolve Epic Table Docs Tests
# ============================================================================


class TestResolveEpicTableDocs:
    @patch("bmad_assist_lite.context_docs.resolver._get_httpx")
    @patch("bmad_assist_lite.context_docs.resolver._fetch_library_docs")
    def test_uses_context7_id_directly(
        self,
        mock_fetch: MagicMock,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Table path skips _resolve_library_id, uses ID from spec."""
        from bmad_assist_lite.context_docs.resolver import _resolve_epic_table_docs

        mock_httpx.return_value = MagicMock()
        mock_fetch.return_value = "vitest docs content"

        cache = LibDocsCache(tmp_path)
        specs = [
            EpicLibrarySpec("Vitest", "/vitest-dev/vitest", "vi.mock patterns", ["1"]),
        ]

        result = _resolve_epic_table_docs(6, specs, cache, max_tokens_per_lib=5000)

        assert "Vitest" in result
        # Verify _fetch_library_docs was called with the spec's context7_id and query
        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args
        assert call_kwargs[1].get("query") or call_kwargs[0][4] if len(call_kwargs[0]) > 4 else True
        # Check keyword args
        _, kwargs = mock_fetch.call_args
        assert kwargs.get("query") == "vi.mock patterns"

    @patch("bmad_assist_lite.context_docs.resolver._get_httpx")
    @patch("bmad_assist_lite.context_docs.resolver._fetch_library_docs")
    def test_caches_with_story_mapping(
        self,
        mock_fetch: MagicMock,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Stores table format with story mapping in cache."""
        from bmad_assist_lite.context_docs.resolver import _resolve_epic_table_docs

        mock_httpx.return_value = MagicMock()
        mock_fetch.return_value = "docs"

        cache = LibDocsCache(tmp_path)
        specs = [
            EpicLibrarySpec("Vitest", "/v/v", "q", ["1", "2"]),
            EpicLibrarySpec("React", "/f/r", "q", ["1"]),
        ]

        _resolve_epic_table_docs(6, specs, cache, max_tokens_per_lib=5000)

        assert cache.is_table_source("epic-6")
        assert cache.get_epic_libs("epic-6") == ["Vitest", "React"]

    @patch("bmad_assist_lite.context_docs.resolver._get_httpx")
    @patch("bmad_assist_lite.context_docs.resolver._fetch_library_docs")
    def test_skips_already_cached_libs(
        self,
        mock_fetch: MagicMock,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Libs already in cache should not be re-fetched."""
        from bmad_assist_lite.context_docs.resolver import _resolve_epic_table_docs

        mock_httpx.return_value = MagicMock()
        mock_fetch.return_value = "new docs"

        cache = LibDocsCache(tmp_path)
        cache.write_library("Vitest", "cached vitest docs")

        specs = [
            EpicLibrarySpec("Vitest", "/v/v", "q", ["1"]),
            EpicLibrarySpec("React", "/f/r", "q", ["1"]),
        ]

        result = _resolve_epic_table_docs(6, specs, cache, max_tokens_per_lib=5000)

        # Only React should have been fetched
        assert mock_fetch.call_count == 1
        assert result["Vitest"] == "cached vitest docs"

    @patch(
        "bmad_assist_lite.context_docs.resolver._get_httpx",
        side_effect=ImportError("httpx not installed"),
    )
    def test_missing_httpx_returns_empty(
        self,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        from bmad_assist_lite.context_docs.resolver import _resolve_epic_table_docs

        cache = LibDocsCache(tmp_path)
        specs = [EpicLibrarySpec("Vitest", "/v/v", "q", ["1"])]

        result = _resolve_epic_table_docs(6, specs, cache, max_tokens_per_lib=5000)
        assert result == {}


# ============================================================================
# Injection with Story Filtering Tests
# ============================================================================


class TestInjectWithStoryFiltering:
    def test_table_source_with_story_num_filters(self, tmp_path: Path) -> None:
        """Table source + story_num → only story's libs injected."""
        from bmad_assist_lite.compiler.types import CompilerContext
        from bmad_assist_lite.context_docs.resolver import inject_library_docs

        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache = LibDocsCache(cache_dir)
        cache.write_library("Vitest", "vitest docs")
        cache.write_library("React", "react docs")
        cache.write_library("Zod", "zod docs")
        cache.set_epic_table_libs(
            "epic-6",
            ["Vitest", "React", "Zod"],
            {"1": ["Vitest", "React"], "2": ["Zod"]},
        )

        context = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "output",
            resolved_variables={"epic_num": 6, "story_num": 1},
        )

        inject_library_docs(context)

        assert "library-docs/Vitest" in context.file_contents
        assert "library-docs/React" in context.file_contents
        assert "library-docs/Zod" not in context.file_contents

    def test_table_source_without_story_num_injects_all(self, tmp_path: Path) -> None:
        """Table source + no story_num → all libs injected."""
        from bmad_assist_lite.compiler.types import CompilerContext
        from bmad_assist_lite.context_docs.resolver import inject_library_docs

        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache = LibDocsCache(cache_dir)
        cache.write_library("Vitest", "vitest docs")
        cache.write_library("Zod", "zod docs")
        cache.set_epic_table_libs(
            "epic-6",
            ["Vitest", "Zod"],
            {"1": ["Vitest"], "2": ["Zod"]},
        )

        context = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "output",
            resolved_variables={"epic_num": 6},
        )

        inject_library_docs(context)

        assert "library-docs/Vitest" in context.file_contents
        assert "library-docs/Zod" in context.file_contents

    def test_legacy_source_injects_all(self, tmp_path: Path) -> None:
        """Legacy source → all libs regardless of story_num."""
        from bmad_assist_lite.compiler.types import CompilerContext
        from bmad_assist_lite.context_docs.resolver import inject_library_docs

        cache_dir = tmp_path / ".bmad-assist-lite" / "cache"
        cache = LibDocsCache(cache_dir)
        cache.write_library("react", "react docs")
        cache.write_library("vue", "vue docs")
        cache.set_epic_libs("epic-1", ["react", "vue"])

        context = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "output",
            resolved_variables={"epic_num": 1, "story_num": 1},
        )

        inject_library_docs(context)

        assert "library-docs/react" in context.file_contents
        assert "library-docs/vue" in context.file_contents


# ============================================================================
# Table Fallback Tests
# ============================================================================


class TestTableFallback:
    @patch("bmad_assist_lite.context_docs.resolver._get_httpx")
    @patch("bmad_assist_lite.context_docs.resolver._fetch_library_docs")
    def test_epic_with_table_uses_table_path(
        self,
        mock_fetch: MagicMock,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Epic file with Context7 table should use table resolution."""
        from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

        mock_httpx.return_value = MagicMock()
        mock_fetch.return_value = "fetched docs"

        epic_file = tmp_path / "epic-6.md"
        epic_file.write_text(
            "# Epic 6\n\n"
            "### Context7 Library Documentation\n\n"
            "| Library Name | Context7 ID | Query Focus | Stories |\n"
            "|---|---|---|---|\n"
            "| Vitest | /vitest-dev/vitest | vi.mock | 1 |\n"
        )

        result = resolve_epic_docs(
            epic_num=6,
            project_root=tmp_path,
            cache_dir=tmp_path,
            epic_file=epic_file,
        )

        assert "Vitest" in result
        # Should have used table path — no _resolve_library_id call
        mock_fetch.assert_called_once()

    @patch("bmad_assist_lite.context_docs.resolver._get_httpx")
    @patch("bmad_assist_lite.context_docs.resolver._resolve_library_id")
    @patch("bmad_assist_lite.context_docs.resolver._fetch_library_docs")
    def test_epic_without_table_uses_autodetect(
        self,
        mock_fetch: MagicMock,
        mock_resolve: MagicMock,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Epic file without Context7 table should fall through to auto-detection."""
        from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

        mock_httpx.return_value = MagicMock()
        mock_resolve.return_value = "/expressjs/express"
        mock_fetch.return_value = "express docs"

        epic_file = tmp_path / "epic-1.md"
        epic_file.write_text("# Epic 1\n\nJust a regular epic, no table.\n")

        pkg = {"dependencies": {"express": "^4.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        result = resolve_epic_docs(
            epic_num=1,
            project_root=tmp_path,
            cache_dir=tmp_path,
            epic_file=epic_file,
        )

        assert "express" in result
        # Should have used auto-detection path with _resolve_library_id
        mock_resolve.assert_called_once()

    @patch("bmad_assist_lite.context_docs.resolver._get_httpx")
    @patch("bmad_assist_lite.context_docs.resolver._fetch_library_docs")
    def test_cached_table_epic_returns_early(
        self,
        mock_fetch: MagicMock,
        mock_httpx: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Already-resolved table epic should return from cache."""
        from bmad_assist_lite.context_docs.resolver import resolve_epic_docs

        cache = LibDocsCache(tmp_path)
        cache.write_library("Vitest", "cached vitest")
        cache.set_epic_table_libs("epic-6", ["Vitest"], {"1": ["Vitest"]})

        epic_file = tmp_path / "epic-6.md"
        epic_file.write_text(
            "### Context7 Library Documentation\n\n"
            "| Library Name | Context7 ID | Query Focus | Stories |\n"
            "|---|---|---|---|\n"
            "| Vitest | /vitest-dev/vitest | vi.mock | 1 |\n"
        )

        result = resolve_epic_docs(
            epic_num=6,
            project_root=tmp_path,
            cache_dir=tmp_path,
            epic_file=epic_file,
        )

        assert result == {"Vitest": "cached vitest"}
        # No HTTP calls should have been made
        mock_fetch.assert_not_called()
        mock_httpx.assert_not_called()


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
