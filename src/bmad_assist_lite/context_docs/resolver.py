"""Orchestrator for library doc resolution: detection + HTTP + cache.

Ties together detector.py (library detection), cache.py (file cache),
and Context7 REST API (documentation fetching).
"""

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from bmad_assist_lite.context_docs.cache import LibDocsCache
from bmad_assist_lite.context_docs.detector import detect_libraries
from bmad_assist_lite.context_docs.epic_table import (
    EpicLibrarySpec,
    get_story_lib_mapping,
    parse_context7_table,
)

if TYPE_CHECKING:
    from bmad_assist_lite.compiler.types import CompilerContext

logger = logging.getLogger(__name__)

CONTEXT7_BASE_URL = "https://context7.com/api/v2"
CONTEXT7_SEARCH_URL = f"{CONTEXT7_BASE_URL}/libs/search"
CONTEXT7_DOCS_URL = f"{CONTEXT7_BASE_URL}/context"


def _get_httpx() -> "object":
    """Lazy-import httpx with a clear error if not installed."""
    try:
        import httpx

        return httpx
    except ImportError:
        raise ImportError(
            "httpx is required for Context7 library doc fetching. "
            "Install it with: pip install bmad-assist-lite[context7]"
        ) from None


def _get_api_key() -> str | None:
    """Get Context7 API key from environment."""
    return os.environ.get("CONTEXT7_API_KEY")


def _resolve_library_id(
    httpx_mod: object,
    library_name: str,
    api_key: str | None,
    timeout: float = 15.0,
) -> str | None:
    """Resolve a library name to a Context7 library ID.

    Returns the library ID (e.g. '/facebook/react') or None on failure.
    """
    import httpx as _httpx

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = _httpx.get(
            CONTEXT7_SEARCH_URL,
            params={"libraryName": library_name, "query": library_name},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

        # API returns {"results": [...]} or a bare list
        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list) and results:
            best = results[0]
            lib_id = best.get("id")
            if lib_id:
                logger.debug("Resolved %s -> %s", library_name, lib_id)
                return str(lib_id)

        logger.warning("Context7: no match found for '%s'", library_name)
        return None
    except Exception as e:
        logger.warning("Context7: search failed for '%s': %s", library_name, e)
        return None


def _fetch_library_docs(
    httpx_mod: object,
    library_id: str,
    library_name: str,
    api_key: str | None,
    max_tokens: int = 5000,
    timeout: float = 30.0,
    query: str | None = None,
) -> str | None:
    """Fetch documentation for a library from Context7.

    Args:
        query: Custom query string. Defaults to ``"{library_name} API usage examples"``.

    Returns markdown documentation text or None on failure.

    """
    import httpx as _httpx

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        params: dict[str, str | int] = {
            "libraryId": library_id,
            "query": query or f"{library_name} API usage examples",
            "tokens": max_tokens,
            "type": "txt",
        }
        response = _httpx.get(
            CONTEXT7_DOCS_URL,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        text = response.text.strip()

        if text:
            logger.debug("Fetched %d chars of docs for %s", len(text), library_name)
            return text

        logger.warning("Context7: empty docs response for '%s'", library_name)
        return None
    except Exception as e:
        logger.warning("Context7: doc fetch failed for '%s' (%s): %s", library_name, library_id, e)
        return None


def _resolve_epic_table_docs(
    epic_num: int,
    specs: list[EpicLibrarySpec],
    cache: LibDocsCache,
    max_tokens_per_lib: int,
) -> dict[str, str]:
    """Resolve library docs using explicit Context7 table specs.

    Skips ``_resolve_library_id()`` — the Context7 ID is provided directly.
    Uses the spec's ``query_focus`` instead of a generic query.
    """
    try:
        httpx_mod = _get_httpx()
    except ImportError as e:
        logger.warning("%s", e)
        return {}

    api_key = _get_api_key()
    epic_key = f"epic-{epic_num}"
    fetched_libs: list[str] = []
    skipped: list[str] = []

    for spec in specs:
        if cache.has_library(spec.name):
            fetched_libs.append(spec.name)
            continue

        docs = _fetch_library_docs(
            httpx_mod,
            spec.context7_id,
            spec.name,
            api_key,
            max_tokens=max_tokens_per_lib,
            query=spec.query_focus,
        )
        if docs:
            cache.write_library(spec.name, docs)
            fetched_libs.append(spec.name)
        else:
            skipped.append(spec.name)

    if skipped:
        logger.warning(
            "Context7 table: could not fetch docs for %d libraries: %s",
            len(skipped),
            skipped,
        )

    if fetched_libs:
        logger.info(
            "Context7 table: resolved %d libraries for epic %d: %s",
            len(fetched_libs),
            epic_num,
            fetched_libs,
        )
    else:
        logger.warning("Context7 table: no library docs fetched for epic %d", epic_num)

    # Build story mapping and store in table format
    story_libs = get_story_lib_mapping(specs)
    cache.set_epic_table_libs(epic_key, fetched_libs, story_libs)
    return cache.get_libs_for_epic(epic_key)


def resolve_epic_docs(
    epic_num: int,
    project_root: Path,
    cache_dir: Path,
    epic_file: Path | None = None,
    architecture_file: Path | None = None,
    max_libs: int = 8,
    max_tokens_per_lib: int = 5000,
) -> dict[str, str]:
    """Resolve library documentation for an epic.

    Flow:
        1. Check if epic already resolved in cache → return cached docs
        2. Detect libraries from project deps + docs
        3. For each library not in cache, fetch from Context7
        4. Cache results and return

    Args:
        epic_num: Epic number.
        project_root: Path to project root.
        cache_dir: Path to .bmad-assist-lite/cache directory.
        epic_file: Optional epic markdown file path.
        architecture_file: Optional architecture doc path.
        max_libs: Maximum libraries to resolve.
        max_tokens_per_lib: Token limit per library for Context7 API.

    Returns:
        Dict of {library_name: documentation_text}. Empty dict on failure.

    """
    cache = LibDocsCache(cache_dir)
    epic_key = f"epic-{epic_num}"

    # 1. Check if already resolved for this epic
    existing = cache.get_epic_libs(epic_key)
    if existing is not None:
        logger.info("Epic %d already resolved (%d libraries)", epic_num, len(existing))
        return cache.get_libs_for_epic(epic_key)

    # 2. Check for Context7 table in epic file
    if epic_file and epic_file.exists():
        try:
            epic_content = epic_file.read_text(encoding="utf-8")
            specs = parse_context7_table(epic_content)
            if specs is not None:
                logger.info(
                    "Found Context7 table in epic %d with %d libraries",
                    epic_num,
                    len(specs),
                )
                return _resolve_epic_table_docs(
                    epic_num, specs, cache, max_tokens_per_lib
                )
        except OSError as e:
            logger.warning("Failed to read epic file for table check: %s", e)

    # 3. Detect libraries
    libraries = detect_libraries(
        project_root,
        epic_file=epic_file,
        architecture_file=architecture_file,
        max_libs=max_libs,
    )

    if not libraries:
        logger.info("No libraries detected for epic %d", epic_num)
        cache.set_epic_libs(epic_key, [])
        return {}

    logger.info("Detected %d libraries for epic %d: %s", len(libraries), epic_num, libraries)

    # 3. Fetch missing docs from Context7
    try:
        httpx_mod = _get_httpx()
    except ImportError as e:
        logger.warning("%s", e)
        return {}

    api_key = _get_api_key()
    fetched_libs: list[str] = []
    skipped: list[str] = []

    for lib_name in libraries:
        # Check cache first
        if cache.has_library(lib_name):
            fetched_libs.append(lib_name)
            continue

        # Resolve library ID
        lib_id = _resolve_library_id(httpx_mod, lib_name, api_key)
        if not lib_id:
            skipped.append(lib_name)
            continue

        # Fetch docs
        docs = _fetch_library_docs(
            httpx_mod, lib_id, lib_name, api_key, max_tokens=max_tokens_per_lib
        )
        if docs:
            cache.write_library(lib_name, docs)
            fetched_libs.append(lib_name)
        else:
            skipped.append(lib_name)

    if skipped:
        logger.warning(
            "Context7: could not fetch docs for %d libraries: %s", len(skipped), skipped
        )

    if fetched_libs:
        logger.info("Context7: fetched docs for %d libraries: %s", len(fetched_libs), fetched_libs)
    else:
        logger.warning("Context7: no library docs fetched for epic %d", epic_num)

    # 4. Record epic mapping and return
    cache.set_epic_libs(epic_key, fetched_libs)
    return cache.get_libs_for_epic(epic_key)


def inject_library_docs(context: "CompilerContext") -> None:
    """Inject cached library documentation into compiler context.

    Reads the epic number from context.resolved_variables and loads any
    cached library docs into context.file_contents with a 'library-docs/' prefix.

    For table-sourced epics with a story_num in context, only injects
    the libraries mapped to that specific story.

    This is a no-op if context_docs is not configured or no docs are cached.
    """
    epic_num = context.resolved_variables.get("epic_num")
    if epic_num is None:
        return

    # Determine cache_dir from project paths
    cache_dir = context.project_root / ".bmad-assist-lite" / "cache"
    cache = LibDocsCache(cache_dir)
    epic_key = f"epic-{epic_num}"

    story_num = context.resolved_variables.get("story_num")
    if story_num is not None and cache.is_table_source(epic_key):
        docs = cache.get_libs_for_story(epic_key, str(story_num))
    else:
        docs = cache.get_libs_for_epic(epic_key)

    if not docs:
        return

    for lib_name, content in docs.items():
        key = f"library-docs/{lib_name}"
        context.file_contents[key] = content

    logger.info("Injected %d library doc(s) into compiler context", len(docs))
