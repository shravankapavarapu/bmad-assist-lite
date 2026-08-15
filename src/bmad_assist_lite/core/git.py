"""Git commit helper for auto-committing story changes."""

import logging
import subprocess
from pathlib import Path

from bmad_assist_lite.providers._windows import get_subprocess_kwargs

logger = logging.getLogger(__name__)


#: Heading for the untracked-files listing appended to :func:`git_diff` output.
#: New files a dev/fixer created are invisible to ``git diff``; the listing keeps
#: them inside the review scope the diff is now load-bearing for.
UNTRACKED_HEADING = "# Untracked new files (not in the diff above; read them directly):"


def _run_git(args: list[str], project_path: Path, timeout: int) -> str | None:
    """Run a git command, returning stdout or None on any failure.

    Output is decoded with ``errors="replace"`` so invalid UTF-8 in a diff can
    never raise out of here — callers treat None as "no diff available" and a
    replaced character is strictly better than losing the whole diff.
    """
    try:
        result = subprocess.run(
            args,
            cwd=project_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **get_subprocess_kwargs(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        logger.warning("git %s failed: %s", " ".join(args[1:3]), exc)
        return None
    if result.returncode != 0:
        logger.warning("git %s failed: %s", " ".join(args[1:3]), result.stderr.strip())
        return None
    output: str = result.stdout
    return output


def git_diff(project_path: Path, *, stat: bool = False, timeout: int = 15) -> str | None:
    """Return the uncommitted diff vs HEAD, or its ``--stat``, or None on error.

    Staged and unstaged changes are both included (``git diff HEAD``), and
    untracked files are appended as a listing under ``UNTRACKED_HEADING`` —
    a fixer that created or ``git add``-ed a file must not silently shrink the
    review scope. After ``code_review_synthesis`` auto-commits the dev +
    synthesis work, this is exactly the ``fix_review`` changes a round-2 delta
    review (SP-2) needs to scope to — reviewers are read-only and cannot run git
    themselves, so the handler inlines this into the prompt. Falls back to the
    index diff on an unborn HEAD (no commits yet).
    """
    head_args = ["git", "diff", "--stat", "HEAD"] if stat else ["git", "diff", "HEAD"]
    output = _run_git(head_args, project_path, timeout)
    if output is None:
        fallback = ["git", "diff", "--stat"] if stat else ["git", "diff"]
        output = _run_git(fallback, project_path, timeout)
        if output is None:
            return None
    untracked = _run_git(
        ["git", "ls-files", "--others", "--exclude-standard"], project_path, timeout
    )
    if untracked and untracked.strip():
        listing = "\n".join(f"  {name}" for name in untracked.strip().splitlines())
        if output and not output.endswith("\n"):
            output += "\n"
        output += f"{UNTRACKED_HEADING}\n{listing}\n"
    return output.strip() if stat else output


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
