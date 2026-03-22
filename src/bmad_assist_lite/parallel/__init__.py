"""Parallel story execution module for bmad-assist-lite."""

from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.dependency_graph import DependencyGraph
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.git_ops import get_current_branch, is_protected_branch
from bmad_assist_lite.parallel.logging import (
    log_run_complete,
    log_run_header,
    log_teardown_result,
    setup_parallel_log,
    teardown_parallel_log,
)
from bmad_assist_lite.parallel.merger import (
    ConflictResolutionResult,
    GateResult,
    MergeQueue,
    MergeResult,
    PostMergeQGResult,
    merge_story,
    resolve_conflicts,
    run_post_merge_fix,
    run_post_merge_qg,
    update_sprint_status_done,
)
from bmad_assist_lite.parallel.orchestrator import Orchestrator
from bmad_assist_lite.parallel.output import OutputMultiplexer
from bmad_assist_lite.parallel.recovery import recover_state
from bmad_assist_lite.parallel.report import (
    MergeOutcome,
    ReportData,
    build_report,
    render_report,
    write_report,
)
from bmad_assist_lite.parallel.state import (
    ParallelState,
    StoryState,
    StoryStatus,
    create_initial_state,
    get_parallel_state_path,
    load_state,
    save_state,
)
from bmad_assist_lite.parallel.worktree_manager import (
    WorktreeInfo,
    cleanup_worktree,
    create_worktree,
    list_worktrees,
    prune_worktrees,
)

__all__ = [
    "ConflictResolutionResult",
    "DependencyGraph",
    "GateResult",
    "MergeOutcome",
    "MergeQueue",
    "MergeResult",
    "Orchestrator",
    "OutputMultiplexer",
    "ParallelConfig",
    "ParallelError",
    "ParallelState",
    "PostMergeQGResult",
    "ReportData",
    "StoryState",
    "StoryStatus",
    "WorktreeInfo",
    "build_report",
    "cleanup_worktree",
    "create_initial_state",
    "create_worktree",
    "get_current_branch",
    "get_parallel_state_path",
    "is_protected_branch",
    "list_worktrees",
    "load_state",
    "log_run_complete",
    "log_run_header",
    "log_teardown_result",
    "merge_story",
    "prune_worktrees",
    "recover_state",
    "render_report",
    "resolve_conflicts",
    "run_post_merge_fix",
    "run_post_merge_qg",
    "save_state",
    "setup_parallel_log",
    "teardown_parallel_log",
    "update_sprint_status_done",
    "write_report",
]
