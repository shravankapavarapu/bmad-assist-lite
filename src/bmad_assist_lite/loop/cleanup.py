"""Per-phase crash recovery cleanup and story-transition cache hygiene.

Removes partial artifacts left behind by a crashed phase execution.
Called on resume before re-entering the loop.

Cache retention policy
----------------------

Everything in ``.bmad-assist-lite/cache/`` is story-scoped and swept by
:func:`clear_story_cache` on every story transition, except the items below.
Anything the sweep keeps must have a row here.

============================== ============= ================================ =====================
Item                           Owner         Invalidated by                   Policy
============================== ============= ================================ =====================
``*.tmp``                      any phase     the phase that wrote it finishes auto-reap (on resume)
``story-queue.yaml``           ``cli.py``    a new run's story discovery      keep in place
``epic-libs.yaml``             context_docs  the epic changing                keep in place
``cursor-deny-config.marker``  cursor        provider ``_cleanup()``          keep in place
``lib-docs/``                  context_docs  the epic changing                keep in place
``forensics/``                 this module   the retention cap                keep in place
``synthesis-diff-review-*``    review synth  never (analysis evidence)        archive + cap
``synthesis-diff-validate-*``  valid. synth  never (analysis evidence)        archive + cap
``qa-failures-*``              quality_gate  never (analysis evidence)        archive + cap
============================== ============= ================================ =====================

The three ``archive + cap`` families are story-id-suffixed, so an exact-name
allowlist can never retain them. They are moved into
``cache/forensics/<story_id>/`` on the transition, which keeps the
"cache is story-scoped" invariant intact while making the evidence durable and
greppable. Growth is bounded by ``forensics.max_stories``.

``forensics.enabled: false`` stops archiving: from that point on the three
families are swept with the rest of the story-scoped cache, exactly as they
were before retention existed. It does **not** delete an archive that already
exists — ``forensics/`` stays on the keep list whatever the flag says, and no
setting in this module removes it. Prune it by hand if you want the space back.
"""

import logging
import os
import re
import shutil
from pathlib import Path

from bmad_assist_lite.core.config import ForensicsConfig, get_config
from bmad_assist_lite.core.exceptions import ConfigError
from bmad_assist_lite.core.state import Phase

logger = logging.getLogger(__name__)

CACHE_DIR_NAME = "cache"
BMAD_DIR_NAME = ".bmad-assist-lite"
CURSOR_DENY_CONFIG_MARKER_NAME = "cursor-deny-config.marker"
FORENSICS_DIR_NAME = "forensics"


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

# Story-id-suffixed forensic artifacts, archived rather than deleted.
# Each pattern must capture the story id so the evidence stays attributable.
_FORENSIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^synthesis-diff-(?:review|validate)-(?P<story_id>.+)\.patch$"),
    re.compile(r"^qa-failures-(?P<story_id>.+)\.md$"),
    # Same evidence class as qa-failures, written by ``parallel/merger.py:776``. It does NOT
    # match the pattern above, which is anchored at the start of the name, so it was still
    # being swept — post-merge gate failures are precisely the env-vs-real classification data
    # WS6 needs, so losing them would have defeated half the point of retention.
    re.compile(r"^post-merge-qg-failures-(?P<story_id>.+)\.md$"),
)

_UNATTRIBUTED_STORY_ID = "_unattributed"
_UNSAFE_STORY_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _forensic_story_id(filename: str) -> str | None:
    """Return the archive subdirectory for a forensic artifact, else ``None``.

    The story id is taken from the filename and sanitised to a single safe path
    segment — it reaches this module from state and must never be able to
    escape the forensics directory.
    """
    for pattern in _FORENSIC_PATTERNS:
        match = pattern.match(filename)
        if match is None:
            continue
        safe = _UNSAFE_STORY_ID_CHARS.sub("_", match.group("story_id")).strip(".")
        return safe or _UNATTRIBUTED_STORY_ID
    return None


def _resolve_forensics_config() -> ForensicsConfig:
    """Return the forensics retention policy, defaulting when no config is loaded."""
    try:
        return get_config().forensics
    except ConfigError:
        return ForensicsConfig()


def _enforce_forensics_cap(
    forensics_root: Path, max_stories: int, protected: set[str]
) -> None:
    """Evict the oldest archived stories until at most ``max_stories`` remain.

    Stories in ``protected`` (the ones just archived) are never evicted, so a
    clock skew or a hand-edited mtime can never destroy the current story's
    evidence.
    """
    if not forensics_root.is_dir():
        return

    story_dirs = [d for d in forensics_root.iterdir() if d.is_dir()]
    excess = len(story_dirs) - max_stories
    if excess <= 0:
        return

    evictable = sorted(
        (d for d in story_dirs if d.name not in protected),
        key=lambda d: (d.stat().st_mtime, d.name),
    )
    for stale in evictable[:excess]:
        shutil.rmtree(stale, ignore_errors=True)
        logger.info(
            "Evicted forensics for story %s (retention cap: %d stories)",
            stale.name,
            max_stories,
        )


def _archive_forensic_artifacts(cache_dir: Path, max_stories: int) -> set[str]:
    """Move forensic artifacts out of the story-scoped cache into ``forensics/``.

    Returns the set of story ids archived in this pass.
    """
    forensics_root = cache_dir / FORENSICS_DIR_NAME
    archived: set[str] = set()

    for item in sorted(cache_dir.iterdir()):
        if item.is_dir():
            continue
        story_id = _forensic_story_id(item.name)
        if story_id is None:
            continue

        destination = forensics_root / story_id / item.name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # os.replace IS the atomic primitive for a move within one
            # filesystem (source and destination are both under cache/): after
            # a crash the artifact is at exactly one of the two paths, whole.
            # Staging through a temp name would only widen that window.
            os.replace(item, destination)
        except OSError as e:
            logger.warning("Failed to archive forensic artifact %s: %s", item.name, e)
            continue

        archived.add(story_id)
        logger.info("Archived forensic artifact %s to %s", item.name, destination.parent)

    _enforce_forensics_cap(forensics_root, max_stories, archived)
    return archived


def find_forensic_artifacts(project_path: Path, story_id: str | None = None) -> list[Path]:
    """List archived forensic artifacts, optionally for a single story.

    Args:
        project_path: Path to the project root.
        story_id: Restrict the result to one story's archive when given.

    Returns:
        Sorted list of archived artifact paths (empty when none exist).

    """
    forensics_root = project_path / BMAD_DIR_NAME / CACHE_DIR_NAME / FORENSICS_DIR_NAME
    if not forensics_root.is_dir():
        return []

    if story_id is not None:
        safe = _UNSAFE_STORY_ID_CHARS.sub("_", story_id).strip(".") or _UNATTRIBUTED_STORY_ID
        search_root = forensics_root / safe
        if not search_root.is_dir():
            return []
    else:
        search_root = forensics_root

    return sorted(p for p in search_root.rglob("*") if p.is_file())


def clear_story_cache(project_path: Path) -> int:
    """Wipe story-scoped cache files when transitioning to a new story.

    Removes all files in ``.bmad-assist-lite/cache/`` except long-lived
    artifacts: ``story-queue.yaml``, ``epic-libs.yaml``, and the
    ``lib-docs/`` directory.

    Forensic artifacts (``synthesis-diff-*``, ``qa-failures-*``) are archived
    into ``cache/forensics/<story_id>/`` rather than deleted, under the
    ``forensics`` retention cap. With ``forensics.enabled: false`` they are
    swept like any other story-scoped file, but the existing
    ``cache/forensics/`` archive is left untouched either way.
    See the module docstring's policy table.

    Returns the number of files deleted (archived artifacts are not deletions).
    """
    cache_dir = project_path / BMAD_DIR_NAME / CACHE_DIR_NAME
    if not cache_dir.exists():
        return 0

    forensics = _resolve_forensics_config()
    # ``forensics/`` is kept unconditionally: the flag gates COLLECTION, not
    # retention. Gating the keep on the flag would make disabling archiving
    # rmtree the archive already on disk — a destructive reading of a switch
    # nobody flips to delete data.
    keep_dirs = {*_KEEP_DIRS, FORENSICS_DIR_NAME}
    if forensics.enabled:
        _archive_forensic_artifacts(cache_dir, forensics.max_stories)

    deleted = 0
    for item in cache_dir.iterdir():
        if item.name in _KEEP_FILENAMES:
            continue
        if item.is_dir():
            if item.name in keep_dirs:
                continue
            # Remove unexpected subdirectories entirely
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
