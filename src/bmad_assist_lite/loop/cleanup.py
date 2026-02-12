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

    # Phase-specific warnings
    if phase == Phase.DEV_STORY:
        logger.warning("Resuming from DEV_STORY phase — check for uncommitted git changes")

    return cleaned
