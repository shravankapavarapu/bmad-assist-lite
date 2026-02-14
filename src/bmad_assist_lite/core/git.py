"""Git commit helper for auto-committing story changes."""

import logging
import subprocess
from pathlib import Path

from bmad_assist_lite.providers._windows import get_subprocess_kwargs

logger = logging.getLogger(__name__)


def _title_from_story_key(story_key: str) -> str:
    """Extract human-readable title from a story key.

    Strips the leading ``N-N-`` prefix and replaces hyphens with spaces.

    Example:
        ``"6-2-blog-ui-component-unit-tests"`` → ``"blog ui component unit tests"``

    """
    parts = story_key.split("-", 2)
    if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
        return parts[2].replace("-", " ")
    return story_key.replace("-", " ")


def auto_commit_story(
    project_path: Path,
    story_id: str,
    story_key: str | None,
) -> bool:
    """Stage and commit all changes for a completed story.

    Non-fatal: logs warnings on failure and returns False instead of raising.

    Args:
        project_path: Project root (must be inside a git repo).
        story_id: Story identifier like ``"6.2"``.
        story_key: Full sprint-status key like ``"6-2-blog-ui-component-unit-tests"``,
            or None if unavailable.

    Returns:
        True if commit succeeded or there was nothing to commit.
        False if an error occurred (logged as warning).

    """
    sp_kwargs = get_subprocess_kwargs()

    # 1. Check for uncommitted changes
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
            **sp_kwargs,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("auto-commit: git status failed: %s", e)
        return False

    if result.returncode != 0:
        logger.warning("auto-commit: git status failed: %s", result.stderr.strip())
        return False

    if not result.stdout.strip():
        logger.info("auto-commit: no changes to commit for story %s", story_id)
        return True

    # 2. Stage all changes (respects .gitignore)
    try:
        add_result = subprocess.run(
            ["git", "add", "."],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
            **sp_kwargs,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("auto-commit: git add failed: %s", e)
        return False

    if add_result.returncode != 0:
        logger.warning("auto-commit: git add failed: %s", add_result.stderr.strip())
        return False

    # 3. Build commit message
    if story_key:
        title = _title_from_story_key(story_key)
        subject = f"feat(story-{story_id}): {title}"
    else:
        subject = f"feat(story-{story_id}): completed"

    message = f"{subject}\n\nAuto-committed by bmad-assist-lite after code_review_synthesis"

    # 4. Commit
    try:
        commit_result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
            **sp_kwargs,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("auto-commit: git commit failed: %s", e)
        return False

    if commit_result.returncode != 0:
        logger.warning("auto-commit: git commit failed: %s", commit_result.stderr.strip())
        return False

    logger.info("auto-commit: committed story %s", story_id)
    return True
