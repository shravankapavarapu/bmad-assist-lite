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

        if isinstance(data, list) and data:
            # Return the first (best) match
            best = data[0]
            lib_id = best.get("id")
            if lib_id:
                logger.debug("Resolved %s -> %s", library_name, lib_id)
                return str(lib_id)

        logger.debug("No Context7 match for library: %s", library_name)
        return None
    except Exception as e:
        logger.warning("Context7 library search failed for %s: %s", library_name, e)
        return None


def _fetch_library_docs(
    httpx_mod: object,
    library_id: str,
    library_name: str,
    api_key: str | None,
    max_tokens: int = 5000,
    timeout: float = 30.0,
) -> str | None:
    """Fetch documentation for a library from Context7.

    Returns markdown documentation text or None on failure.
    """
    import httpx as _httpx

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        params: dict[str, str | int] = {
            "libraryId": library_id,
            "query": f"{library_name} API usage examples",
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

        logger.debug("Empty docs response for %s", library_name)
        return None
    except Exception as e:
        logger.warning("Context7 doc fetch failed for %s (%s): %s", library_name, library_id, e)
        return None


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

    # 2. Detect libraries
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

    for lib_name in libraries:
        # Check cache first
        if cache.has_library(lib_name):
            fetched_libs.append(lib_name)
            continue

        # Resolve library ID
        lib_id = _resolve_library_id(httpx_mod, lib_name, api_key)
        if not lib_id:
            continue

        # Fetch docs
        docs = _fetch_library_docs(
            httpx_mod, lib_id, lib_name, api_key, max_tokens=max_tokens_per_lib
        )
        if docs:
            cache.write_library(lib_name, docs)
            fetched_libs.append(lib_name)

    # 4. Record epic mapping and return
    cache.set_epic_libs(epic_key, fetched_libs)
    return cache.get_libs_for_epic(epic_key)


def inject_library_docs(context: "CompilerContext") -> None:
    """Inject cached library documentation into compiler context.

    Reads the epic number from context.resolved_variables and loads any
    cached library docs into context.file_contents with a 'library-docs/' prefix.

    This is a no-op if context_docs is not configured or no docs are cached.
    """
    epic_num = context.resolved_variables.get("epic_num")
    if epic_num is None:
        return

    # Determine cache_dir from project paths
    cache_dir = context.project_root / ".bmad-assist-lite" / "cache"
    cache = LibDocsCache(cache_dir)
    epic_key = f"epic-{epic_num}"

    docs = cache.get_libs_for_epic(epic_key)
    if not docs:
        return

    for lib_name, content in docs.items():
        key = f"library-docs/{lib_name}"
        context.file_contents[key] = content

    logger.info("Injected %d library doc(s) into compiler context", len(docs))
