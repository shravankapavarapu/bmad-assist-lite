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
    worktree_base_dir: Path | None = Field(
        default=None,
        description="Custom base directory for git worktrees (None = auto)",
    )

    @field_validator("worktree_base_dir", mode="before")
    @classmethod
    def _coerce_empty_string_to_none(cls, v: object) -> object:
        """Coerce empty string to None for worktree_base_dir."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v
