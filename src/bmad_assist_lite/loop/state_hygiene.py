"""Stale-state detection, its declared policy table, and the operator boundary.

Persistence in this tool is strong; invalidation was thin.  A stale artifact
cannot make a run *fast* — it makes a run confidently wrong, which is the
success criterion this module is written against.  No timing claim is made
for anything here.

The operator boundary
---------------------

Detection is automatic.  Removal is not.  :func:`sweep_stale_state` defaults
to ``dry_run=True`` and inverting it takes an explicit argument, because the
precedent for getting this wrong is concrete: a cleanup script's own review
once caught a ``--dry-run`` that silently inverted into a live kill, next to
an unanchored match that could have killed a *different* worktree's live
processes.

Two rules follow from that, and both are enforced by tests rather than by
comment:

* **Nothing here terminates a process.**  Not a stale one, not an orphan.
  The sweep reads a lock file's recorded PID to decide whether the lock is
  stale, and stops there.  Process ownership is established by recorded PID
  plus the project root that recorded it — never by matching a process name,
  which is what makes another worktree's processes reachable.
* **Nothing that holds work is ever offered.**  A worktree is only offered
  for cleanup once
  :func:`~bmad_assist_lite.parallel.merge_guard.branch_deletion_decision`
  has cleared *both* clauses of its predicate.  An offer the operator accepts
  destroys its subject, so for a parked merge the *offer itself* is the thing
  that must not exist.

The stale-state surface
-----------------------

============================== ============= ================================ =====================
Item                           Owner         Invalidated by                   Policy
============================== ============= ================================ =====================
``*.tmp``                      any phase     the phase that wrote it finishes auto-reap (on resume)
``state.yaml``                 loop runner   the story it names completing    detect-and-warn
``sprint-status.yaml``         sprint sync   state.yaml advancing             detect-and-warn
``parallel-state.yaml``        orchestrator  the parallel run finishing       detect-and-warn
``story-queue.yaml``           ``cli.py``    a new run's story discovery      operator-only
``running.lock``               loop runner   the owning process exiting       operator-only
``resume-checkpoint``          loop runner   sprint-status marking it done    detect-and-warn
``abandoned-worktree``         parallel      the merge landing or parking     operator-only
``epic-libs.yaml``             context_docs  the epic changing                detect-and-warn
``cursor-deny-config.marker``  cursor        provider ``_cleanup()``          auto-reap (on resume)
``lib-docs/``                  context_docs  the epic changing                detect-and-warn
``forensics/``                 cleanup       the retention cap                operator-only
``parked-merges/``             merge ladder  the operator un-parking          operator-only
============================== ============= ================================ =====================

``forensics/`` and ``parked-merges/`` appear here as items this module must
**not** touch.  They are retained evidence and retained work respectively;
listing them with an ``operator-only`` policy is what stops a later reader
mistaking "not swept" for "not considered".

Cross-checking the table against the code is :func:`undeclared_cache_keeps`:
anything the cache sweep keeps must have a row above, so a new entry added to
``cleanup._KEEP_FILENAMES`` without a row fails the check.
"""

from __future__ import annotations

import logging
import os
import sys
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from bmad_assist_lite.loop import cleanup as cleanup_module

logger = logging.getLogger(__name__)

__all__ = [
    "STALE_STATE_SURFACE",
    "HygieneReport",
    "Policy",
    "StaleFinding",
    "StaleStateItem",
    "declared_names",
    "sweep_stale_state",
    "undeclared_cache_keeps",
]

BMAD_DIR_NAME = ".bmad-assist-lite"
CACHE_DIR_NAME = "cache"
LOCK_FILENAME = "running.lock"
STATE_FILENAME = "state.yaml"
PARALLEL_STATE_FILENAME = "parallel-state.yaml"
STORY_QUEUE_FILENAME = "story-queue.yaml"
SPRINT_STATUS_RELPATH = Path("_bmad-output") / "implementation-artifacts" / "sprint-status.yaml"

_DONE_STATUSES = frozenset({"done", "completed", "complete"})


class Policy(StrEnum):
    """What this tool is allowed to do about a stale item, without being asked."""

    AUTO_REAP = "auto-reap"
    DETECT_AND_WARN = "detect-and-warn"
    OPERATOR_ONLY = "operator-only"


class StaleStateItem(BaseModel):
    """One row of the stale-state surface table."""

    model_config = ConfigDict(frozen=True)

    name: str
    owner: str
    invalidated_by: str
    policy: Policy
    kept_by_cache_sweep: bool = False
    note: str = ""


STALE_STATE_SURFACE: tuple[StaleStateItem, ...] = (
    StaleStateItem(
        name="*.tmp",
        owner="any phase",
        invalidated_by="the phase that wrote it finishing",
        policy=Policy.AUTO_REAP,
        note="An interrupted atomic write. Provably owned by a run that is over.",
    ),
    StaleStateItem(
        name=STATE_FILENAME,
        owner="loop runner",
        invalidated_by="the story it names completing",
        policy=Policy.DETECT_AND_WARN,
        note="Holds the resume position. Never offered for removal.",
    ),
    StaleStateItem(
        name="sprint-status.yaml",
        owner="sprint sync",
        invalidated_by="state.yaml advancing past it",
        policy=Policy.DETECT_AND_WARN,
        note="Single source of truth for story discovery. Never offered.",
    ),
    StaleStateItem(
        name=PARALLEL_STATE_FILENAME,
        owner="parallel orchestrator",
        invalidated_by="the parallel run finishing or being abandoned",
        policy=Policy.DETECT_AND_WARN,
        note="Names worktrees; those may still hold work.",
    ),
    StaleStateItem(
        name=STORY_QUEUE_FILENAME,
        owner="cli.py story discovery",
        invalidated_by="a new run's story discovery, or an epic file moving",
        policy=Policy.OPERATOR_ONLY,
        kept_by_cache_sweep=True,
        note="Derived cache — rebuildable, so it may be offered for removal.",
    ),
    StaleStateItem(
        name=LOCK_FILENAME,
        owner="loop runner",
        invalidated_by="the owning process exiting",
        policy=Policy.OPERATOR_ONLY,
        note="Staleness is decided by the recorded PID. Never by killing it.",
    ),
    StaleStateItem(
        name="resume-checkpoint",
        owner="loop runner",
        invalidated_by="sprint-status marking the checkpointed story done",
        policy=Policy.DETECT_AND_WARN,
        note="A checkpoint parked on finished work would redo it silently.",
    ),
    StaleStateItem(
        name="abandoned-worktree",
        owner="parallel orchestrator",
        invalidated_by="the merge landing, or the operator un-parking it",
        policy=Policy.OPERATOR_ONLY,
        note="Offered only when the two-clause no-data-loss guard clears it.",
    ),
    StaleStateItem(
        name="epic-libs.yaml",
        owner="context_docs",
        invalidated_by="the epic changing",
        policy=Policy.DETECT_AND_WARN,
        kept_by_cache_sweep=True,
    ),
    StaleStateItem(
        name=cleanup_module.CURSOR_DENY_CONFIG_MARKER_NAME,
        owner="cursor provider",
        invalidated_by="the provider's _cleanup() running",
        policy=Policy.AUTO_REAP,
        kept_by_cache_sweep=True,
        note="Reaped by cleanup_for_phase() on resume, which also removes the "
        "deny-config it points at.",
    ),
    StaleStateItem(
        name="lib-docs",
        owner="context_docs",
        invalidated_by="the epic changing",
        policy=Policy.DETECT_AND_WARN,
        kept_by_cache_sweep=True,
    ),
    StaleStateItem(
        name=cleanup_module.FORENSICS_DIR_NAME,
        owner="loop/cleanup.py",
        invalidated_by="the forensics retention cap",
        policy=Policy.OPERATOR_ONLY,
        kept_by_cache_sweep=True,
        note="Retained analysis evidence. This module never touches it.",
    ),
    StaleStateItem(
        name="parked-merges",
        owner="merge ladder",
        invalidated_by="the operator un-parking the merge",
        policy=Policy.OPERATOR_ONLY,
        note="Retained work. Removing a record is never a side effect of a sweep.",
    ),
)


def declared_names() -> frozenset[str]:
    """Return every item name declared in the stale-state surface table."""
    return frozenset(item.name for item in STALE_STATE_SURFACE)


def undeclared_cache_keeps() -> frozenset[str]:
    """Return cache-sweep keep entries that have no row in the surface table.

    The cache sweep's retention policy is two literals in
    :mod:`bmad_assist_lite.loop.cleanup`.  This is what makes them auditable:
    a name added to either literal without a corresponding table row shows up
    here, and the verification script fails on a non-empty result.
    """
    kept = set(cleanup_module._KEEP_FILENAMES) | set(cleanup_module._KEEP_DIRS)
    kept.add(cleanup_module.FORENSICS_DIR_NAME)
    return frozenset(kept - declared_names())


class StaleFinding(BaseModel):
    """One detected piece of stale state, and what may be done about it."""

    model_config = ConfigDict(frozen=True)

    item: str
    path: str
    detail: str
    policy: Policy
    removable: bool = False
    protected_reason: str = ""


class HygieneReport(BaseModel):
    """The outcome of one sweep."""

    model_config = ConfigDict(frozen=True)

    findings: tuple[StaleFinding, ...] = ()
    removed: tuple[str, ...] = ()
    dry_run: bool = True

    @property
    def offers(self) -> tuple[StaleFinding, ...]:
        """Findings an explicit ``--clean`` would act on."""
        return tuple(f for f in self.findings if f.removable)


# ---------------------------------------------------------------------------
# Detectors — each returns findings and removes nothing
# ---------------------------------------------------------------------------


def _read_yaml_mapping(path: Path) -> tuple[dict[str, object] | None, str]:
    """Load a YAML mapping, distinguishing "absent" from "corrupt".

    Returns a ``(mapping, error)`` pair.  A non-empty ``error`` means the file
    exists but could not be read as a mapping — which must never be reported
    as "empty", because an empty state file and a truncated one mean opposite
    things to a resuming run.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"corrupt: unreadable ({exc})"
    except UnicodeDecodeError:
        return None, "corrupt: not valid UTF-8"

    if not content.strip():
        return {}, ""

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else "invalid YAML"
        return None, f"corrupt: invalid YAML ({first_line})"

    if not isinstance(data, dict):
        return None, f"corrupt: expected a mapping, got {type(data).__name__}"
    return data, ""


def _detect_orphan_temp_files(project_path: Path) -> list[StaleFinding]:
    """Interrupted atomic writes, anywhere state is written."""
    findings: list[StaleFinding] = []
    bmad_dir = project_path / BMAD_DIR_NAME
    candidates: list[Path] = []

    cache_dir = bmad_dir / CACHE_DIR_NAME
    if cache_dir.is_dir():
        candidates.extend(sorted(cache_dir.glob("*.tmp")))
    if bmad_dir.is_dir():
        candidates.extend(sorted(bmad_dir.glob("*.tmp")))
    sprint_status = project_path / SPRINT_STATUS_RELPATH
    if sprint_status.parent.is_dir():
        candidates.extend(sorted(sprint_status.parent.glob("*.tmp")))

    for tmp in candidates:
        findings.append(
            StaleFinding(
                item="*.tmp",
                path=str(tmp),
                detail="orphaned temp file from an interrupted atomic write",
                policy=Policy.AUTO_REAP,
                removable=True,
            )
        )
    return findings


def _lock_owner_pid(lock_path: Path) -> int | None:
    """Read the PID a lock file recorded, or ``None`` when it is unreadable."""
    try:
        lines = lock_path.read_text(encoding="utf-8").strip().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines:
        return None
    try:
        return int(lines[0].strip())
    except ValueError:
        return None


def _pid_is_alive(pid: int) -> bool:
    """Report whether a PID is live, without signalling it in any lethal way.

    ``os.kill(pid, 0)`` sends no signal — it is a permission-and-existence
    probe.  It is the only process call this module makes.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
        from bmad_assist_lite.loop.locking import _is_pid_alive_windows

        return _is_pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _detect_stale_lock(project_path: Path) -> list[StaleFinding]:
    """A lock whose recorded owner is gone.

    Only this project root's lock file is ever examined.  Another worktree's
    lock lives under a different root and is never enumerated, which is what
    keeps the I-08 unanchored match structurally impossible rather than merely
    avoided.
    """
    lock_path = project_path / BMAD_DIR_NAME / LOCK_FILENAME
    if not lock_path.is_file():
        return []

    pid = _lock_owner_pid(lock_path)
    if pid is None:
        return [
            StaleFinding(
                item=LOCK_FILENAME,
                path=str(lock_path),
                detail="lock file records no readable PID",
                policy=Policy.OPERATOR_ONLY,
                removable=True,
            )
        ]

    if _pid_is_alive(pid):
        return [
            StaleFinding(
                item=LOCK_FILENAME,
                path=str(lock_path),
                detail=f"held by live process {pid} — a run is active",
                policy=Policy.OPERATOR_ONLY,
                removable=False,
                protected_reason=(
                    f"process {pid} is alive; this sweep never terminates a process"
                ),
            )
        ]

    return [
        StaleFinding(
            item=LOCK_FILENAME,
            path=str(lock_path),
            detail=f"owning process {pid} is gone",
            policy=Policy.OPERATOR_ONLY,
            removable=True,
        )
    ]


def _detect_corrupt_state_files(project_path: Path) -> list[StaleFinding]:
    """Corruption is reported as corruption, never as "empty"."""
    findings: list[StaleFinding] = []
    targets = (
        (STATE_FILENAME, project_path / BMAD_DIR_NAME / STATE_FILENAME),
        (PARALLEL_STATE_FILENAME, project_path / BMAD_DIR_NAME / PARALLEL_STATE_FILENAME),
        ("sprint-status.yaml", project_path / SPRINT_STATUS_RELPATH),
    )
    for item, path in targets:
        if not path.is_file():
            continue
        _, error = _read_yaml_mapping(path)
        if error:
            findings.append(
                StaleFinding(
                    item=item,
                    path=str(path),
                    detail=error,
                    policy=Policy.DETECT_AND_WARN,
                    removable=False,
                    protected_reason=(
                        "this file records progress; a sweep repairs nothing and "
                        "deletes nothing here"
                    ),
                )
            )
    return findings


def _detect_stale_story_queue(project_path: Path) -> list[StaleFinding]:
    """A cached queue whose epic files have moved is a queue that will mislead."""
    queue_path = project_path / BMAD_DIR_NAME / CACHE_DIR_NAME / STORY_QUEUE_FILENAME
    if not queue_path.is_file():
        return []

    data, error = _read_yaml_mapping(queue_path)
    if error:
        return [
            StaleFinding(
                item=STORY_QUEUE_FILENAME,
                path=str(queue_path),
                detail=error,
                policy=Policy.OPERATOR_ONLY,
                removable=True,
            )
        ]

    missing: list[str] = []
    epic_files = (data or {}).get("epic_files")
    if isinstance(epic_files, dict):
        for epic, raw in epic_files.items():
            if isinstance(raw, str) and not Path(raw).is_file():
                missing.append(f"epic {epic} -> {raw}")

    if not missing:
        return []
    return [
        StaleFinding(
            item=STORY_QUEUE_FILENAME,
            path=str(queue_path),
            detail="cached queue names epic files that no longer exist: "
            + "; ".join(missing),
            policy=Policy.OPERATOR_ONLY,
            removable=True,
        )
    ]


def _detect_stale_resume_checkpoint(project_path: Path) -> list[StaleFinding]:
    """A checkpoint parked on a story sprint-status already calls done."""
    state_path = project_path / BMAD_DIR_NAME / STATE_FILENAME
    status_path = project_path / SPRINT_STATUS_RELPATH
    if not state_path.is_file() or not status_path.is_file():
        return []

    state_data, state_error = _read_yaml_mapping(state_path)
    status_data, status_error = _read_yaml_mapping(status_path)
    if state_error or status_error or state_data is None or status_data is None:
        return []

    story = state_data.get("current_story")
    if not isinstance(story, str) or not story:
        return []

    development_status = status_data.get("development_status")
    if not isinstance(development_status, dict):
        return []

    recorded = development_status.get(story)
    if not isinstance(recorded, str) or recorded.strip().lower() not in _DONE_STATUSES:
        return []

    return [
        StaleFinding(
            item="resume-checkpoint",
            path=str(state_path),
            detail=(
                f"checkpoint is parked on story {story}, which sprint-status records "
                f"as '{recorded}'; a resume would redo finished work"
            ),
            policy=Policy.DETECT_AND_WARN,
            removable=False,
            protected_reason=(
                "state.yaml is the resume position — resolve with `run --resume`, "
                "which reconciles it against sprint-status, not by deleting it"
            ),
        )
    ]


def _detect_abandoned_worktrees(
    project_path: Path, integration_ref: str
) -> list[StaleFinding]:
    """Candidate worktrees, each put through the two-clause no-data-loss guard.

    A worktree is offered only when the guard clears *both* clauses.  A branch
    holding unmerged commits is never offered; neither is a worktree belonging
    to a live parked merge, whose branch may well have zero unmerged commits.
    """
    from bmad_assist_lite.parallel.exceptions import ParallelError
    from bmad_assist_lite.parallel.merge_guard import branch_deletion_decision
    from bmad_assist_lite.parallel.worktree_manager import list_worktrees

    try:
        worktrees = list_worktrees(project_path)
    except (ParallelError, OSError) as exc:
        logger.debug("Cannot enumerate worktrees under %s: %s", project_path, exc)
        return []

    main_path = project_path.resolve()
    findings: list[StaleFinding] = []

    for info in worktrees:
        wt_path = info.path.resolve()
        if wt_path == main_path or info.branch is None:
            continue

        decision = branch_deletion_decision(
            project_path, info.branch, integration_ref, worktree_path=wt_path
        ).require_full_predicate()

        if decision.safe:
            findings.append(
                StaleFinding(
                    item="abandoned-worktree",
                    path=str(wt_path),
                    detail=(
                        f"worktree on branch {info.branch} holds no unmerged work "
                        f"({decision.reason})"
                    ),
                    policy=Policy.OPERATOR_ONLY,
                    removable=True,
                )
            )
            continue

        findings.append(
            StaleFinding(
                item="abandoned-worktree",
                path=str(wt_path),
                detail=f"worktree on branch {info.branch} is NOT safe to remove",
                policy=Policy.OPERATOR_ONLY,
                removable=False,
                protected_reason=decision.reason,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def detect_stale_state(
    project_path: Path, integration_ref: str = "HEAD"
) -> tuple[StaleFinding, ...]:
    """Enumerate every stale-state finding for a project. Removes nothing."""
    findings: list[StaleFinding] = []
    findings.extend(_detect_orphan_temp_files(project_path))
    findings.extend(_detect_stale_lock(project_path))
    findings.extend(_detect_corrupt_state_files(project_path))
    findings.extend(_detect_stale_story_queue(project_path))
    findings.extend(_detect_stale_resume_checkpoint(project_path))
    findings.extend(_detect_abandoned_worktrees(project_path, integration_ref))
    return tuple(findings)


def _remove_offer(project_path: Path, finding: StaleFinding, integration_ref: str) -> bool:
    """Act on one cleared offer. Returns whether anything was removed."""
    path = Path(finding.path)

    if finding.item == "abandoned-worktree":
        from bmad_assist_lite.parallel.exceptions import ParallelError
        from bmad_assist_lite.parallel.merge_guard import (
            assert_deletion_allowed,
            branch_deletion_decision,
        )
        from bmad_assist_lite.parallel.worktree_manager import (
            _delete_branch,
            _remove_worktree,
            _remove_worktree_dir,
            list_worktrees,
        )

        branch = next(
            (
                wt.branch
                for wt in list_worktrees(project_path)
                if wt.path.resolve() == path and wt.branch
            ),
            None,
        )
        if branch is None:
            return False

        # Re-decide at the moment of deletion rather than trusting the offer:
        # the operator may have committed into the worktree between the report
        # and the confirmation.
        try:
            decision = assert_deletion_allowed(
                branch_deletion_decision(
                    project_path, branch, integration_ref, worktree_path=path
                ),
                f"worktree {path}",
            )
        except ParallelError as exc:
            logger.warning("Declining to remove %s: %s", path, exc)
            return False

        _remove_worktree(project_path, path, decision)
        _delete_branch(project_path, branch, decision)
        if path.exists():
            _remove_worktree_dir(path, decision)
        return not path.exists()

    try:
        path.unlink()
    except OSError as exc:
        logger.warning("Failed to remove %s: %s", path, exc)
        return False
    return True


def sweep_stale_state(
    project_path: Path,
    dry_run: bool = True,
    integration_ref: str = "HEAD",
) -> HygieneReport:
    """Detect stale state and, only when explicitly told to, remove what is safe.

    Args:
        project_path: Path to the project root.
        dry_run: Detection only.  **Defaults to non-destructive**, and
            inverting it requires passing the argument.
        integration_ref: Reference a worktree's commits must be reachable from
            before the worktree can be offered for cleanup.

    Returns:
        A :class:`HygieneReport`.  ``removed`` is always empty on a dry run.

    """
    findings = detect_stale_state(project_path, integration_ref)

    for finding in findings:
        if finding.removable:
            logger.info("Stale %s: %s (%s)", finding.item, finding.path, finding.detail)
        else:
            logger.warning(
                "Stale %s: %s (%s) — NOT offered for cleanup: %s",
                finding.item,
                finding.path,
                finding.detail,
                finding.protected_reason or "protected",
            )

    if dry_run:
        return HygieneReport(findings=findings, removed=(), dry_run=True)

    removed: list[str] = []
    for finding in findings:
        if not finding.removable:
            continue
        if _remove_offer(project_path, finding, integration_ref):
            removed.append(finding.path)
            logger.info("Removed stale %s: %s", finding.item, finding.path)

    return HygieneReport(findings=findings, removed=tuple(removed), dry_run=False)
