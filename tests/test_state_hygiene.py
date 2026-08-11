"""Stale-state hygiene: detection, the operator boundary, and the guard (REQ-05).

Every guard here is proven by a negative test (G4). The two hard constraints
are the reason most of this file exists:

* Hygiene must never undo the no-data-loss guarantee (REQ-05.4 / REQ-03.6).
* Detection is automatic; removal is an explicit operator action (REQ-05.2),
  and nothing here may ever terminate a process (incident I-08).
"""

from __future__ import annotations

import inspect
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from bmad_assist_lite.loop import cleanup as cleanup_module
from bmad_assist_lite.loop.state_hygiene import (
    STALE_STATE_SURFACE,
    Policy,
    StaleStateItem,
    declared_names,
    sweep_stale_state,
    undeclared_cache_keeps,
)
from bmad_assist_lite.parallel.merge_guard import branch_deletion_decision
from bmad_assist_lite.parallel.parked import ParkedMerge, record_parked_merge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository with one commit on the integration branch."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    (root / ".bmad-assist-lite" / "cache").mkdir(parents=True)
    return root


def _add_worktree(repo_root: Path, branch: str, path: Path, *, commit: bool) -> None:
    """Attach a worktree on a new branch, optionally with an unmerged commit."""
    _git(repo_root, "worktree", "add", "-b", branch, str(path), "main")
    if commit:
        (path / "work.txt").write_text("unmerged work\n", encoding="utf-8")
        _git(path, "add", "-A")
        _git(path, "commit", "-m", "work that must not be lost")


def _finding_paths(report: object) -> set[str]:
    return {f.path for f in report.findings}  # type: ignore[attr-defined]


def _offered_paths(report: object) -> set[str]:
    return {f.path for f in report.offers}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# REQ-05.1 — the surface is enumerated and the keep-list is auditable
# ---------------------------------------------------------------------------


class TestSurfaceEnumeration:
    def test_every_stale_state_class_has_a_declared_policy(self) -> None:
        """REQ-05.1 crit 1-2: goal.md WS2's surface is enumerated, each with a policy."""
        names = declared_names()
        for required in (
            "*.tmp",
            "state.yaml",
            "sprint-status.yaml",
            "parallel-state.yaml",
            "story-queue.yaml",
            "running.lock",
            "resume-checkpoint",
            "abandoned-worktree",
        ):
            assert required in names, f"{required} is missing from STALE_STATE_SURFACE"
        for item in STALE_STATE_SURFACE:
            assert isinstance(item.policy, Policy)
            assert item.owner and item.invalidated_by

    def test_cache_keep_list_is_fully_declared(self) -> None:
        """REQ-05.1 crit 3: everything the sweep keeps has a table row."""
        assert undeclared_cache_keeps() == frozenset()

    def test_neg_a_new_keep_entry_without_a_table_row_fails_the_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REQ-05.1 crit 3 (NEG) — this is what makes the two-literal policy auditable."""
        monkeypatch.setattr(
            cleanup_module,
            "_KEEP_FILENAMES",
            {*cleanup_module._KEEP_FILENAMES, "smuggled-in.yaml"},
        )
        assert undeclared_cache_keeps() == frozenset({"smuggled-in.yaml"})

    def test_surface_items_are_frozen(self) -> None:
        item = STALE_STATE_SURFACE[0]
        with pytest.raises(Exception):
            item.name = "mutated"  # type: ignore[misc]
        assert isinstance(item, StaleStateItem)


# ---------------------------------------------------------------------------
# REQ-05.2 — detect automatically, act only on explicit operator action
# ---------------------------------------------------------------------------


class TestOperatorBoundary:
    def test_dry_run_is_the_default(self) -> None:
        """REQ-05.2 crit 3 — I-08's `--dry-run` silently inverted into a live kill."""
        signature = inspect.signature(sweep_stale_state)
        assert signature.parameters["dry_run"].default is True

    def test_neg_default_sweep_deletes_nothing(self, repo: Path) -> None:
        """REQ-05.2 crit 3 (NEG): the default is non-destructive, proven by behaviour."""
        stale_tmp = repo / ".bmad-assist-lite" / "cache" / "partial.tmp"
        stale_tmp.write_text("x", encoding="utf-8")

        report = sweep_stale_state(repo)

        assert report.dry_run is True
        assert report.removed == ()
        assert stale_tmp.exists(), "a default sweep must never delete"
        assert str(stale_tmp) in _finding_paths(report)

    def test_explicit_clean_removes_only_offered_items(self, repo: Path) -> None:
        stale_tmp = repo / ".bmad-assist-lite" / "cache" / "partial.tmp"
        stale_tmp.write_text("x", encoding="utf-8")

        report = sweep_stale_state(repo, dry_run=False)

        assert report.dry_run is False
        assert str(stale_tmp) in report.removed
        assert not stale_tmp.exists()

    def test_neg_sweep_never_terminates_a_process(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REQ-05.2 crit 1 (NEG, load-bearing) — nothing here may kill a process.

        The invariant is precise: signal 0 sends nothing and is the documented
        liveness probe, so banning ``os.kill`` outright would ban the detection
        this module is *supposed* to do. What must never happen is a lethal
        signal, or any of the platform kill utilities.
        """
        killed: list[object] = []
        real_kill = os.kill

        def _guarded_kill(pid: int, sig: int, *args: object, **kwargs: object) -> None:
            if sig != 0:
                killed.append((pid, sig))
                raise AssertionError(
                    f"the hygiene sweep signalled pid {pid} with signal {sig}"
                )
            real_kill(pid, sig, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "kill", _guarded_kill)
        real_run = subprocess.run

        def _guarded_run(cmd: object, *args: object, **kwargs: object) -> object:
            argv = cmd if isinstance(cmd, list) else [str(cmd)]
            joined = " ".join(str(a) for a in argv)
            assert "taskkill" not in joined and "pkill" not in joined
            assert not (argv and str(argv[0]) == "kill")
            return real_run(cmd, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(subprocess, "run", _guarded_run)

        lock = repo / ".bmad-assist-lite" / "running.lock"
        lock.write_text(f"{os.getpid()}\n{datetime.now().isoformat()}\n", encoding="utf-8")

        sweep_stale_state(repo, dry_run=False)
        assert killed == []

    def test_neg_a_live_lock_from_another_worktree_is_not_matched(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """REQ-05.2 crit 2 (NEG) — I-08's unanchored match, which could kill a
        different worktree's live processes. Ownership is per project root plus
        recorded PID, never a name match."""
        other_root = tmp_path / "other-worktree"
        (other_root / ".bmad-assist-lite").mkdir(parents=True)
        other_lock = other_root / ".bmad-assist-lite" / "running.lock"
        other_lock.write_text(
            f"{os.getpid()}\n{datetime.now().isoformat()}\n", encoding="utf-8"
        )

        report = sweep_stale_state(repo, dry_run=False)

        assert other_lock.exists()
        assert str(other_lock) not in _finding_paths(report)
        assert str(other_lock) not in report.removed

    def test_a_lock_held_by_a_live_process_is_never_offered(self, repo: Path) -> None:
        lock = repo / ".bmad-assist-lite" / "running.lock"
        lock.write_text(f"{os.getpid()}\n{datetime.now().isoformat()}\n", encoding="utf-8")

        report = sweep_stale_state(repo, dry_run=False)

        assert lock.exists(), "a lock whose owner is alive is not stale"
        assert str(lock) not in _offered_paths(report)

    def test_a_lock_from_a_dead_process_is_detected_and_offered(self, repo: Path) -> None:
        lock = repo / ".bmad-assist-lite" / "running.lock"
        # PID 0 is never a live user process on any supported platform.
        lock.write_text(f"0\n{datetime.now().isoformat()}\n", encoding="utf-8")

        report = sweep_stale_state(repo)

        assert str(lock) in _offered_paths(report)
        assert lock.exists(), "detection alone must not remove it"


# ---------------------------------------------------------------------------
# REQ-05.3 — tolerant loading, and corruption is not silence
# ---------------------------------------------------------------------------


class TestToleranceAndCorruption:
    def test_unknown_fields_are_accepted_at_runtime(self) -> None:
        """REQ-05.3 crit 1, with the T41 correction: `init_forbid_extra` is a
        mypy-plugin setting; Pydantic's runtime default is `extra='ignore'`."""
        from bmad_assist_lite.core.state import State
        from bmad_assist_lite.core.sprint_status import SprintStatus

        assert State.model_validate({"qa_retry_count": 2, "from_the_future": "x"})
        assert SprintStatus.model_validate({"from_the_future": "x"})

    def test_ignored_fields_are_logged_once_not_per_read(
        self, repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """REQ-05.3 crit 3."""
        from bmad_assist_lite.core.state import load_state, save_state, State

        path = repo / ".bmad-assist-lite" / "state.yaml"
        save_state(State(current_story="1.1"), path)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["invented_by_a_newer_version"] = True
        path.write_text(yaml.dump(data), encoding="utf-8")

        with caplog.at_level("WARNING"):
            load_state(path)
            load_state(path)

        warnings = [
            r for r in caplog.records if "invented_by_a_newer_version" in r.getMessage()
        ]
        assert len(warnings) == 1, "ignored fields are logged once, not per read"

    def test_neg_a_truncated_state_file_is_reported_not_treated_as_empty(
        self, repo: Path
    ) -> None:
        """REQ-05.3 crit 2 (NEG)."""
        path = repo / ".bmad-assist-lite" / "state.yaml"
        path.write_text("current_story: '1.1'\ncompleted_stories: [\n", encoding="utf-8")

        report = sweep_stale_state(repo)

        corrupt = [f for f in report.findings if f.path == str(path)]
        assert corrupt, "a corrupt state file must be detected"
        assert "corrupt" in corrupt[0].detail.lower()
        assert not corrupt[0].removable, "state may hold work — never offered for deletion"


# ---------------------------------------------------------------------------
# REQ-05.4 — hygiene MUST NOT undo the no-data-loss guarantee
# ---------------------------------------------------------------------------


class TestNoDataLossIsNotUndone:
    def test_guard_consults_both_clauses_on_every_call(self, repo: Path) -> None:
        """REQ-05.4 crit 1 (A16): one predicate, two clauses, neither optional."""
        _add_worktree(repo, "story/1.1", repo.parent / "wt-1.1", commit=False)

        decision = branch_deletion_decision(repo, "story/1.1")

        assert decision.clauses_consulted == frozenset({"unmerged-commits", "parked-merge"})

    def test_neg_a_caller_cannot_get_a_verdict_that_consulted_one_clause(
        self, repo: Path
    ) -> None:
        """REQ-05.4 crit 1 (NEG) — no call site may hold half the rule."""
        _add_worktree(repo, "story/1.2", repo.parent / "wt-1.2", commit=False)
        decision = branch_deletion_decision(repo, "story/1.2")
        assert "parked-merge" in decision.clauses_consulted

        with pytest.raises(ValueError, match="both clauses"):
            decision.model_copy(
                update={"clauses_consulted": frozenset({"unmerged-commits"})}
            ).require_full_predicate()

    def test_neg_a_branch_with_unmerged_commits_is_never_reaped(self, repo: Path) -> None:
        """REQ-05.4 crit 2 (NEG, load-bearing)."""
        wt = repo.parent / "wt-abandoned"
        _add_worktree(repo, "story/2.1", wt, commit=True)

        report = sweep_stale_state(repo, dry_run=False)

        assert wt.exists(), "a worktree holding unmerged work must survive the sweep"
        assert str(wt) not in _offered_paths(report)
        assert str(wt) not in report.removed
        protected = [f for f in report.findings if f.path == str(wt)]
        assert protected and "not reachable" in protected[0].protected_reason
        branches = _git(repo, "branch", "--list").stdout
        assert "story/2.1" in branches

    def test_a_branch_with_zero_unmerged_commits_is_still_cleaned(self, repo: Path) -> None:
        """REQ-05.4 crit 3 (A16) — the guard must not leak worktrees forever."""
        wt = repo.parent / "wt-merged"
        _add_worktree(repo, "story/3.1", wt, commit=False)

        report = sweep_stale_state(repo, dry_run=False)

        assert str(wt) in _offered_paths(report)
        assert str(wt) in report.removed
        assert not wt.exists()

    def test_neg_a_parked_merges_worktree_is_never_even_offered(self, repo: Path) -> None:
        """REQ-05.4 crit 4 (NEG, A16) — the reciprocal of REQ-03.6 crit 5e.

        Zero unmerged commits AND a live parked-merge record: the case
        criterion 2 structurally cannot reach. An offer the operator accepts
        destroys the record's subject, so the *offer* is what must not exist.
        """
        wt = repo.parent / "wt-parked"
        _add_worktree(repo, "story/4.1", wt, commit=False)
        record_parked_merge(
            repo,
            ParkedMerge(
                story_id="4.1",
                branch="story/4.1",
                worktree_path=str(wt),
                reason="ladder exhausted",
            ),
        )

        report = sweep_stale_state(repo, dry_run=False)

        assert str(wt) not in _offered_paths(report), "a parked merge must not be offered"
        assert str(wt) not in report.removed
        assert wt.exists()
        parked_finding = [f for f in report.findings if f.path == str(wt)]
        assert parked_finding, "the sweep must still say why it declined"
        detail = parked_finding[0].protected_reason
        assert "parked" in detail.lower() and "4.1" in detail
        assert "list-parked" in detail, "point the operator at REQ-03.6.5c's listing surface"

    def test_neg_a_full_sweep_never_removes_a_parked_merge_record(self, repo: Path) -> None:
        """REQ-05.4 crit 5 (NEG) — never a side effect of the sweep."""
        wt = repo.parent / "wt-parked-record"
        _add_worktree(repo, "story/5.1", wt, commit=False)
        path = record_parked_merge(
            repo,
            ParkedMerge(story_id="5.1", branch="story/5.1", worktree_path=str(wt)),
        )
        before = path.read_bytes()

        sweep_stale_state(repo, dry_run=False)

        assert path.exists()
        assert path.read_bytes() == before, "the record must be byte-identical"

    def test_forensic_archive_is_never_offered_for_cleanup(self, repo: Path) -> None:
        """T28 must not be regressed: retained evidence is not stale state."""
        forensics = repo / ".bmad-assist-lite" / "cache" / "forensics" / "1.1"
        forensics.mkdir(parents=True)
        evidence = forensics / "qa-failures-1.1.md"
        evidence.write_text("gate failure evidence", encoding="utf-8")

        report = sweep_stale_state(repo, dry_run=False)

        assert evidence.exists()
        assert str(evidence) not in report.removed
        assert str(forensics) not in _offered_paths(report)


# ---------------------------------------------------------------------------
# Story-queue and resume-checkpoint staleness
# ---------------------------------------------------------------------------


class TestQueueAndCheckpoint:
    def test_story_queue_referencing_a_missing_epic_is_detected(self, repo: Path) -> None:
        queue = repo / ".bmad-assist-lite" / "cache" / "story-queue.yaml"
        queue.write_text(
            yaml.dump({"epic_files": {"1": str(repo / "_bmad-output" / "epic-1.md")}}),
            encoding="utf-8",
        )

        report = sweep_stale_state(repo)

        stale = [f for f in report.findings if f.path == str(queue)]
        assert stale, "a queue naming an epic file that no longer exists is stale"
        assert stale[0].removable, "the queue is a derived cache — safe to offer"

    def test_resume_checkpoint_for_a_done_story_is_detected(self, repo: Path) -> None:
        from bmad_assist_lite.core.state import State, save_state

        save_state(
            State(current_story="1.1", current_epic=1),
            repo / ".bmad-assist-lite" / "state.yaml",
        )
        artifacts = repo / "_bmad-output" / "implementation-artifacts"
        artifacts.mkdir(parents=True)
        (artifacts / "sprint-status.yaml").write_text(
            yaml.dump({"development_status": {"1.1": "done"}}), encoding="utf-8"
        )

        report = sweep_stale_state(repo)

        checkpoints = [f for f in report.findings if f.item == "resume-checkpoint"]
        assert checkpoints, "a checkpoint parked on a done story is stale"
        assert not checkpoints[0].removable, "state.yaml is never auto-offered"
