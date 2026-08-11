"""Parallel story execution module for bmad-assist-lite."""

from bmad_assist_lite.parallel.bootstrap import BootstrapResult, bootstrap_worktree
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
from bmad_assist_lite.parallel.merge_guard import (
    DeletionDecision,
    branch_deletion_decision,
)
from bmad_assist_lite.parallel.merger import (
    ConflictResolutionResult,
    GateResult,
    MergeQueue,
    MergeResult,
    PostMergeQGResult,
    land_candidate,
    merge_story,
    resolve_conflicts,
    resolve_on_resolution_branch,
    run_post_merge_fix,
    run_post_merge_qg,
    update_sprint_status_done,
)
from bmad_assist_lite.parallel.orchestrator import Orchestrator
from bmad_assist_lite.parallel.output import OutputMultiplexer
from bmad_assist_lite.parallel.parked import (
    ParkedMerge,
    list_parked_merges,
    record_parked_merge,
    unpark_merge,
)
from bmad_assist_lite.parallel.recovery import reconcile_merge_queue, recover_state
from bmad_assist_lite.parallel.report import (
    MergeOutcome,
    ReportData,
    build_report,
    render_report,
    write_report,
)
from bmad_assist_lite.parallel.state import (
    GateObservation,
    MergeAttempt,
    MergeTier,
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
    "BootstrapResult",
    "ConflictResolutionResult",
    "DeletionDecision",
    "DependencyGraph",
    "GateObservation",
    "GateResult",
    "MergeAttempt",
    "MergeOutcome",
    "MergeQueue",
    "MergeResult",
    "MergeTier",
    "Orchestrator",
    "OutputMultiplexer",
    "ParallelConfig",
    "ParallelError",
    "ParallelState",
    "ParkedMerge",
    "PostMergeQGResult",
    "ReportData",
    "StoryState",
    "StoryStatus",
    "WorktreeInfo",
    "bootstrap_worktree",
    "branch_deletion_decision",
    "build_report",
    "cleanup_worktree",
    "create_initial_state",
    "create_worktree",
    "get_current_branch",
    "get_parallel_state_path",
    "is_protected_branch",
    "land_candidate",
    "list_parked_merges",
    "list_worktrees",
    "load_state",
    "log_run_complete",
    "log_run_header",
    "log_teardown_result",
    "merge_story",
    "prune_worktrees",
    "reconcile_merge_queue",
    "record_parked_merge",
    "recover_state",
    "render_report",
    "resolve_conflicts",
    "resolve_on_resolution_branch",
    "run_post_merge_fix",
    "run_post_merge_qg",
    "save_state",
    "setup_parallel_log",
    "teardown_parallel_log",
    "unpark_merge",
    "update_sprint_status_done",
    "write_report",
]
