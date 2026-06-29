"""Per-phase crash recovery cleanup.

Removes partial artifacts left behind by a crashed phase execution.
Called on resume before re-entering the loop.
"""

import logging
from pathlib import Path

from bmad_assist_lite.core.state import Phase

logger = logging.getLogger(__name__)

CACHE_DIR_NAME = "cache"
BMAD_DIR_NAME = ".bmad-assist-lite"
CURSOR_DENY_CONFIG_MARKER_NAME = "cursor-deny-config.marker"


def _cleanup_cursor_deny_config(cache_dir: Path, cleaned: list[str]) -> None:
    """Remove orphaned Cursor deny-config file and marker from a crashed invocation.

    Reads the marker file to find the deny-config path, removes both files,
    and appends cleaned paths to ``cleaned``. Never raises — crash recovery
    must not itself crash.

    Args:
        cache_dir: Path to ``.bmad-assist-lite/cache/`` directory.
        cleaned: Accumulator list for cleaned file paths.

    """
    marker_path = cache_dir / CURSOR_DENY_CONFIG_MARKER_NAME
    if not marker_path.exists():
        return

    try:
        deny_config_path_str = marker_path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as e:
        logger.warning("Failed to read cursor deny-config marker: %s", e)
        # Remove the unreadable marker itself
        try:
            marker_path.unlink(missing_ok=True)
            cleaned.append(str(marker_path))
        except OSError:
            pass
        return

    # Remove the deny-config file at the path recorded in the marker
    if deny_config_path_str:
        deny_config_path = Path(deny_config_path_str)
        # Validate the marker-referenced path before deleting:
        # 1. Must be an absolute path (relative paths are untrusted)
        # 2. Must end with .cursor/cli.json (expected deny-config location)
        if not deny_config_path.is_absolute() or deny_config_path.name != "cli.json":
            logger.warning(
                "Cursor deny-config marker contains unexpected path: %s — skipping deletion",
                deny_config_path,
            )
        else:
            try:
                if deny_config_path.exists():
                    deny_config_path.unlink()
                    cleaned.append(str(deny_config_path))
                    logger.info("Cleaned orphaned cursor deny-config: %s", deny_config_path)
            except OSError as e:
                logger.warning(
                    "Failed to clean cursor deny-config %s: %s", deny_config_path, e
                )

    # Remove the marker itself
    try:
        marker_path.unlink(missing_ok=True)
        cleaned.append(str(marker_path))
        logger.info("Cleaned cursor deny-config marker: %s", marker_path)
    except OSError as e:
        logger.warning("Failed to clean cursor deny-config marker: %s", e)


def cleanup_for_phase(phase: Phase, project_path: Path) -> list[str]:
    """Remove partial artifacts from a previous crashed phase.

    Args:
        phase: The phase that was interrupted.
        project_path: Path to the project root.

    Returns:
        List of cleaned file paths (as strings).

    """
    cleaned: list[str] = []

    # Clean *.tmp files from cache directory
    cache_dir = project_path / BMAD_DIR_NAME / CACHE_DIR_NAME
    if cache_dir.exists() and cache_dir.is_dir():
        for tmp_file in cache_dir.glob("*.tmp"):
            try:
                tmp_file.unlink()
                cleaned.append(str(tmp_file))
                logger.info("Cleaned temp file: %s", tmp_file)
            except OSError as e:
                logger.warning("Failed to clean %s: %s", tmp_file, e)

    # Cursor deny-config crash recovery: remove orphaned deny-config + marker
    _cleanup_cursor_deny_config(cache_dir, cleaned)

    # Phase-specific warnings
    if phase == Phase.DEV_STORY:
        logger.warning("Resuming from DEV_STORY phase — check for uncommitted git changes")

    return cleaned


# Files/directories that persist across stories (do NOT delete)
_KEEP_FILENAMES = {"story-queue.yaml", "epic-libs.yaml", CURSOR_DENY_CONFIG_MARKER_NAME}
_KEEP_DIRS = {"lib-docs"}


def clear_story_cache(project_path: Path) -> int:
    """Wipe story-scoped cache files when transitioning to a new story.

    Removes all files in ``.bmad-assist-lite/cache/`` except long-lived
    artifacts: ``story-queue.yaml``, ``epic-libs.yaml``, and the
    ``lib-docs/`` directory.

    Returns the number of files deleted.
    """
    cache_dir = project_path / BMAD_DIR_NAME / CACHE_DIR_NAME
    if not cache_dir.exists():
        return 0

    deleted = 0
    for item in cache_dir.iterdir():
        if item.name in _KEEP_FILENAMES:
            continue
        if item.is_dir():
            if item.name in _KEEP_DIRS:
                continue
            # Remove unexpected subdirectories entirely
            import shutil

            shutil.rmtree(item, ignore_errors=True)
            deleted += 1
            logger.info("Cleared cache directory: %s", item.name)
        else:
            try:
                item.unlink()
                deleted += 1
            except OSError as e:
                logger.warning("Failed to delete cache file %s: %s", item.name, e)

    if deleted:
        logger.info("Cleared %d story-scoped cache files", deleted)
    return deleted
