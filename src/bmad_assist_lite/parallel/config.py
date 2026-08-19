"""Configuration model for parallel story execution."""

import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


class ParallelConfig(BaseModel):
    """Parallel execution configuration.

    Controls concurrency, timing, and worktree settings for parallel
    story execution via git worktrees.
    """

    model_config = ConfigDict(frozen=True)

    max_concurrency: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Maximum number of concurrent story executions (1-5)",
    )
    stagger_delay: float = Field(
        default=10.0,
        ge=0,
        description="Delay in seconds between spawning parallel stories",
    )
    post_merge_fix_retries: int = Field(
        default=1,
        ge=0,
        description="Number of retry attempts for post-merge quality gate fixes",
    )
    conflict_resolution_timeout: int = Field(
        default=600,
        ge=10,
        description=(
            "Timeout in seconds for AI conflict resolution. The default is 600, "
            "not the 120 at which resolution was measured to time out. Resolution "
            "runs outside the merge lock, so a long budget costs latency for that "
            "story alone, never for the queue."
        ),
    )
    max_rebase_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        description=(
            "How many times a candidate may be rebased and re-attempted before the "
            "merge ladder ends in park"
        ),
    )
    integration_branch: str | None = Field(
        default=None,
        description=(
            "Reference stories land on. None means the branch currently checked out "
            "in the project root — nothing hard-codes main"
        ),
    )
    worktree_base_dir: Path | None = Field(
        default=None,
        description="Custom base directory for git worktrees (None = auto)",
    )
    copy_to_worktree: list[str] = Field(
        default_factory=list,
        description="Files/directories to copy from project root to worktree",
    )
    copy_strict: bool = Field(
        default=False,
        description="If True, fail bootstrap when a copy source is missing",
    )
    setup_commands: list[str] = Field(
        default_factory=list,
        description="Shell commands to run sequentially in worktree after copy",
    )
    validation_command: str | None = Field(
        default=None,
        description="Shell command to validate worktree setup (e.g. pytest -q -x)",
    )
    bootstrap_timeout: int = Field(
        default=120,
        ge=1,
        description="Timeout in seconds for each setup/validation command",
    )
    numbered_file_globs: list[str] = Field(
        default_factory=list,
        description=(
            "Globs, relative to the project root, of files whose leading numeric "
            "prefix is an ordering contract (e.g. 'db/migrations/*.sql'). Two "
            "parallel branches can each ADD a file with the same number — git "
            "shows no conflict, lint/typecheck/build all pass, and the ordering "
            "is silently broken. When set, the post-merge quality gate fails "
            "loud on any shared prefix before running the command gates."
        ),
    )
    retry_parked_on_resume: bool = Field(
        default=True,
        description=(
            "On --resume, automatically un-park parked merges whose branch "
            "still exists and send them back through the merge ladder at the "
            "clean tier. A retry that fails simply re-parks — the branch and "
            "worktree are never deleted, so the retry is safe and bounded to "
            "one attempt per resume."
        ),
    )

    @field_validator("worktree_base_dir", mode="before")
    @classmethod
    def _coerce_empty_string_to_none(cls, v: object) -> object:
        """Coerce empty string to None for worktree_base_dir."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v
