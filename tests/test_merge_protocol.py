"""Merge protocol tests: rebase, one short critical section, no data loss.

Every test here drives **real temporary git repositories**. The behaviour
under test is git's — whether a rebase moved a ref, whether a commit is
reachable, whether a conflict is a conflict — and a mock cannot be wrong
about that in an interesting way.
"""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.exceptions import ParallelError
from bmad_assist_lite.parallel.git_ops import (
    _run_git,
    count_ahead_behind,
    count_unmerged_commits,
    rebase_branch,
    ref_exists,
    rev_parse,
    tree_sha,
)
from bmad_assist_lite.parallel.merge_guard import (
    REQUIRED_CLAUSES,
    DeletionDecision,
    assert_deletion_allowed,
    assert_merge_lock_not_held,
    branch_deletion_decision,
    enter_merge_lock,
    exit_merge_lock,
    merge_lock_held,
)
from bmad_assist_lite.parallel.merger import (
    MergeQueue,
    land_candidate,
    resolve_on_resolution_branch,
)
from bmad_assist_lite.parallel.parked import (
    get_parked_dir,
    list_parked_merges,
    record_parked_merge,
    unpark_merge,
)
from bmad_assist_lite.parallel.recovery import reconcile_merge_queue
from bmad_assist_lite.parallel.state import (
    GateObservation,
    MergeAttempt,
    MergeTier,
    StoryStatus,
    create_initial_state,
    gate_verdict,
)
from bmad_assist_lite.parallel.worktree_manager import (
    _branch_name,
    cleanup_worktree,
    create_worktree,
)

BASE_BRANCH = "integration"


# ============================================================================
# Real-git fixtures
# ============================================================================


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run_git(list(args), cwd=repo)


def _commit(repo: Path, relpath: str, content: str, message: str) -> str:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", relpath)
    _git(repo, "commit", "-m", message)
    return rev_parse(repo, "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository on an integration branch with one commit."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", BASE_BRANCH)
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    _commit(root, "README.md", "base\n", "chore: base")
    return root


def _story_branch(repo_root: Path, story_id: str, relpath: str, content: str) -> str:
    """Create a story worktree, commit one file on it, return the branch SHA."""
    wt = create_worktree(story_id, repo_root)
    _git(wt, "config", "user.email", "test@example.com")
    _git(wt, "config", "user.name", "Test")
    _git(wt, "config", "commit.gpgsign", "false")
    _commit(wt, relpath, content, f"feat: story {story_id}")
    return rev_parse(repo_root, _branch_name(story_id))


def _claude_stub(stdout: str, returncode: int = 0):
    """Patch ``subprocess.Popen`` for ``claude`` only, delegating everything else.

    ``merger`` and ``git_ops`` share one ``subprocess`` module, so a blanket
    patch would break the real git calls the rest of the path depends on.
    """
    real_popen = subprocess.Popen

    def stub(args, *rest, **kwargs):
        if not (isinstance(args, (list, tuple)) and args and args[0] == "claude"):
            return real_popen(args, *rest, **kwargs)
        proc = MagicMock()
        proc.communicate.return_value = (stdout, "")
        proc.returncode = returncode
        return proc

    return stub


def _config_with_test(command: str) -> object:
    return load_config(
        {
            "providers": {"master": {"provider": "claude", "model": "opus"}},
            "quality_gate": {"test": command, "command_timeout": 60},
        }
    )


# ============================================================================
# REQ-03.1 — rebase-before-merge exists
# ============================================================================


class TestRebaseBeforeMerge:
    """Rebase-before-merge, on real repositories."""

    def test_two_branches_touching_different_files_land_linearly(
        self, repo: Path
    ) -> None:
        """AC 1: the second lands on top of the first, linear, both present."""
        _story_branch(repo, "1.1", "a.txt", "alpha\n")
        _story_branch(repo, "1.2", "b.txt", "beta\n")

        first = land_candidate("1.1", repo, _branch_name("1.1"), None,
                               integration_ref=BASE_BRANCH)
        assert first.status == "landed"

        second = land_candidate("1.2", repo, _branch_name("1.2"), None,
                                integration_ref=BASE_BRANCH)
        assert second.status == "landed"
        assert second.rebased is True

        assert (repo / "a.txt").read_text() == "alpha\n"
        assert (repo / "b.txt").read_text() == "beta\n"

        # Linear: every commit has at most one parent.
        parents = _git(repo, "log", "--pretty=%p", BASE_BRANCH).stdout.split("\n")
        assert all(len(line.split()) <= 1 for line in parents if line.strip())

    def test_same_file_conflict_goes_to_the_ladder_not_a_silent_overwrite(
        self, repo: Path
    ) -> None:
        """AC 2: a same-file conflict is detected, not silently overwritten."""
        _story_branch(repo, "2.1", "shared.txt", "from story 2.1\n")
        _story_branch(repo, "2.2", "shared.txt", "from story 2.2\n")

        assert land_candidate("2.1", repo, _branch_name("2.1"), None,
                              integration_ref=BASE_BRANCH).status == "landed"

        second = land_candidate("2.2", repo, _branch_name("2.2"), None,
                                integration_ref=BASE_BRANCH)
        assert second.status == "conflict"
        assert second.conflict_files == ["shared.txt"]
        # Not overwritten: story 2.1's content is still what landed.
        assert (repo / "shared.txt").read_text() == "from story 2.1\n"

    def test_failed_rebase_leaves_the_branch_sha_unchanged(self, repo: Path) -> None:
        """AC 3 (NEG): a failed rebase aborts and restores the branch tip."""
        _story_branch(repo, "3.1", "shared.txt", "story side\n")
        _commit(repo, "shared.txt", "base side\n", "chore: base moves")

        branch = _branch_name("3.1")
        before = rev_parse(repo, branch)

        outcome = rebase_branch(repo, branch, BASE_BRANCH)

        assert outcome.status == "conflict"
        assert rev_parse(repo, branch) == before
        assert outcome.sha_after == before
        # And no rebase is left in progress in the story worktree.
        wt = repo.parent / "proj-parallel-3-1"
        assert _run_git(["status", "--porcelain"], cwd=wt).stdout.strip() == ""

    def test_no_rebase_is_attempted_when_already_up_to_date(self, repo: Path) -> None:
        """AC 4: behind == 0 skips the rebase entirely."""
        _story_branch(repo, "4.1", "a.txt", "alpha\n")
        branch = _branch_name("4.1")
        before = rev_parse(repo, branch)

        ahead, behind = count_ahead_behind(repo, branch, BASE_BRANCH)
        assert (ahead, behind) == (1, 0)

        outcome = rebase_branch(repo, branch, BASE_BRANCH)

        assert outcome.status == "skipped"
        assert outcome.behind == 0
        assert rev_parse(repo, branch) == before

    def test_rebase_works_when_the_branch_is_not_checked_out(self, repo: Path) -> None:
        """A branch with no worktree still rebases, via a detached worktree."""
        _git(repo, "branch", "loose", BASE_BRANCH)
        _git(repo, "checkout", "-q", "loose")
        loose_sha = _commit(repo, "loose.txt", "loose\n", "feat: loose")
        _git(repo, "checkout", "-q", BASE_BRANCH)
        _commit(repo, "base2.txt", "b2\n", "chore: base moves")

        outcome = rebase_branch(repo, "loose", BASE_BRANCH)

        assert outcome.status == "rebased"
        assert outcome.sha_after != loose_sha
        assert count_ahead_behind(repo, "loose", BASE_BRANCH) == (1, 0)


# ============================================================================
# REQ-03.2 — one short critical section, containing no LLM call
# ============================================================================


class TestCriticalSection:
    """The lock is short, it serialises, and no provider runs inside it."""

    async def test_second_merge_attempt_waits(self, repo: Path) -> None:
        """AC 2 (NEG): a concurrent second attempt waits, it does not overlap."""
        _story_branch(repo, "5.1", "a.txt", "alpha\n")
        _story_branch(repo, "5.2", "b.txt", "beta\n")

        queue = MergeQueue(repo, integration_ref=BASE_BRANCH)
        await queue.enqueue("5.1")
        await queue.enqueue("5.2")

        overlap: list[str] = []
        depth = 0
        real_land = land_candidate

        def tracking_land(*args: object, **kwargs: object) -> object:
            nonlocal depth
            depth += 1
            if depth > 1:
                overlap.append("concurrent")
            try:
                return real_land(*args, **kwargs)  # type: ignore[arg-type]
            finally:
                depth -= 1

        with patch("bmad_assist_lite.parallel.merger.land_candidate", tracking_land):
            results = await asyncio.gather(queue.process_next(), queue.process_next())

        assert overlap == []
        assert all(r is not None and r.landed for r in results)

    async def test_lock_is_not_held_at_provider_invocation(self, repo: Path) -> None:
        """AC 5b (NEG): the runtime invariant, on the real merge path.

        A genuine same-file conflict is driven through the real
        ``MergeQueue`` with a stubbed ``claude --print``. The stub records
        whether the merge lock was held at the moment of invocation.
        """
        _story_branch(repo, "6.1", "shared.txt", "from 6.1\n")
        _story_branch(repo, "6.2", "shared.txt", "from 6.2\n")

        queue = MergeQueue(repo, integration_ref=BASE_BRANCH)
        await queue.enqueue("6.1")
        await queue.enqueue("6.2")

        held_at_invocation: list[bool] = []
        resolver = _claude_stub(
            "--- FILE: shared.txt ---\nfrom 6.1\nfrom 6.2\n--- END FILE ---\n"
        )

        def recording_stub(args, *rest, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(args, (list, tuple)) and args and args[0] == "claude":
                held_at_invocation.append(merge_lock_held())
            return resolver(args, *rest, **kwargs)

        with patch("subprocess.Popen", recording_stub):
            first = await queue.process_next()
            second = await queue.process_next()

        assert first is not None and first.landed
        assert second is not None and second.landed
        assert held_at_invocation, "the resolver was never reached — test proves nothing"
        assert all(held is False for held in held_at_invocation)

    async def test_mutation_invoking_the_provider_inside_the_lock_is_caught(
        self, repo: Path
    ) -> None:
        """AC 5c (NEG): the invariant is proven to fire.

        The mutation moves the resolver back inside the critical section.
        The runtime assertion must reject it — an assertion never seen red
        is untested code that can fake green.
        """
        _story_branch(repo, "7.1", "shared.txt", "from 7.1\n")
        _story_branch(repo, "7.2", "shared.txt", "from 7.2\n")

        queue = MergeQueue(repo, integration_ref=BASE_BRANCH)
        await queue.enqueue("7.1")
        await queue.enqueue("7.2")

        real_land = land_candidate

        def land_then_resolve_inside_the_lock(*args: object, **kwargs: object) -> object:
            outcome = real_land(*args, **kwargs)  # type: ignore[arg-type]
            if getattr(outcome, "status", "") == "conflict":
                # THE MUTATION: the resolver is invoked from inside the
                # critical section instead of outside it.
                assert_merge_lock_not_held("conflict resolver (claude --print)")
            return outcome

        with patch(
            "bmad_assist_lite.parallel.merger.land_candidate",
            land_then_resolve_inside_the_lock,
        ):
            first = await queue.process_next()
            second = await queue.process_next()

        assert first is not None and first.landed
        assert second is not None
        assert second.landed is False
        assert second.parked is True
        assert "merge-protocol invariant violated" in (second.error or "")

    async def test_lock_hold_time_is_logged_per_merge(
        self, repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """AC 4: the cost of holding the section is measurable, not inferred."""
        _story_branch(repo, "8.1", "a.txt", "alpha\n")
        queue = MergeQueue(repo, integration_ref=BASE_BRANCH)
        await queue.enqueue("8.1")

        with caplog.at_level("INFO"):
            await queue.process_next()

        assert any("Critical section held" in r.message for r in caplog.records)

    def test_assert_merge_lock_not_held_raises_when_held(self) -> None:
        """The sentinel itself goes red when the section is entered."""
        token = enter_merge_lock()
        try:
            assert merge_lock_held() is True
            with pytest.raises(ParallelError, match="invariant violated"):
                assert_merge_lock_not_held("test call site")
        finally:
            exit_merge_lock(token)
        assert merge_lock_held() is False


# ============================================================================
# REQ-03.3 — the re-gate runs on the rebased tree and is binding
# ============================================================================


class TestReGate:
    """The re-gate decides the land, and it is never skipped."""

    def test_semantic_break_blocks_the_merge(self, repo: Path) -> None:
        """AC 2 (NEG): A alone passes, B alone passes, A+B fails → blocked.

        This is the whole point of re-gating on the rebased tree: neither
        branch is broken, their combination is, and only a gate that runs
        after the rebase can see it.
        """
        gate = "python3 check.py"
        config = _config_with_test(gate)

        # The checker fails only when both files are present together.
        _commit(
            repo,
            "check.py",
            "import pathlib, sys\n"
            "a = pathlib.Path('a.txt').exists()\n"
            "b = pathlib.Path('b.txt').exists()\n"
            "sys.exit(1 if (a and b) else 0)\n",
            "chore: integration checker",
        )

        _story_branch(repo, "9.1", "a.txt", "alpha\n")
        _story_branch(repo, "9.2", "b.txt", "beta\n")

        # Each branch passes the gate on its own.
        for story in ("9.1", "9.2"):
            wt = repo.parent / f"proj-parallel-{story.replace('.', '-')}"
            assert subprocess.run(
                ["python3", "check.py"], cwd=wt, capture_output=True
            ).returncode == 0

        first = land_candidate("9.1", repo, _branch_name("9.1"), config,
                               integration_ref=BASE_BRANCH)
        assert first.status == "landed"
        head_after_first = rev_parse(repo, BASE_BRANCH)

        second = land_candidate("9.2", repo, _branch_name("9.2"), config,
                                integration_ref=BASE_BRANCH)

        assert second.status == "gate_failed"
        # The merge is BLOCKED: the integration head is untouched...
        assert rev_parse(repo, BASE_BRANCH) == head_after_first
        assert not (repo / "b.txt").exists()
        # ...and the work is not lost.
        assert ref_exists(repo, _branch_name("9.2"))
        assert count_unmerged_commits(repo, _branch_name("9.2"), BASE_BRANCH) >= 1

    def test_gate_observation_binds_to_the_post_rebase_tree(self, repo: Path) -> None:
        """AC 1: the observation names the tree the gate actually ran on."""
        _story_branch(repo, "10.1", "a.txt", "alpha\n")
        outcome = land_candidate("10.1", repo, _branch_name("10.1"), None,
                                 integration_ref=BASE_BRANCH)

        assert outcome.observation is not None
        assert outcome.observation.tree_sha == tree_sha(repo, BASE_BRANCH)
        assert outcome.observation.directory == str(repo)

    def test_re_gate_is_not_skipped_when_behind_is_zero(self, repo: Path) -> None:
        """AC 4 (NEG): behind == 0 skips the rebase, never the gate."""
        _story_branch(repo, "11.1", "a.txt", "alpha\n")
        gate_calls: list[str] = []
        real_gate = None

        from bmad_assist_lite.parallel import merger as merger_mod

        real_gate = merger_mod.run_post_merge_qg

        def counting_gate(*args: object, **kwargs: object) -> object:
            gate_calls.append("ran")
            return real_gate(*args, **kwargs)  # type: ignore[misc]

        assert count_ahead_behind(repo, _branch_name("11.1"), BASE_BRANCH)[1] == 0
        with patch.object(merger_mod, "run_post_merge_qg", counting_gate):
            outcome = land_candidate("11.1", repo, _branch_name("11.1"), None,
                                     integration_ref=BASE_BRANCH)

        assert outcome.rebased is False
        assert gate_calls == ["ran"]


# ============================================================================
# REQ-03.4 — tiered, persisted escalation
# ============================================================================


class TestLadder:
    """clean → auto → ai-resolve → park, recorded, tree clean at every rung."""

    def test_tiers_are_a_closed_enum(self) -> None:
        """AC 1: the ladder is closed, and park is on it."""
        assert [t.value for t in MergeTier] == ["clean", "auto", "ai-resolve", "park"]

    def test_failed_resolution_leaves_the_story_branch_untouched(
        self, repo: Path
    ) -> None:
        """AC 2 + 3 (NEG): clean tree, unchanged SHA, fresh branch only."""
        _story_branch(repo, "12.1", "shared.txt", "from 12.1\n")
        _story_branch(repo, "12.2", "shared.txt", "from 12.2\n")
        assert land_candidate("12.1", repo, _branch_name("12.1"), None,
                              integration_ref=BASE_BRANCH).status == "landed"

        branch = _branch_name("12.2")
        before = rev_parse(repo, branch)
        wt = repo.parent / "proj-parallel-12-2"

        with patch("subprocess.Popen", _claude_stub("nothing useful")):
            outcome = resolve_on_resolution_branch(
                "12.2", repo, branch, BASE_BRANCH, attempt=1, timeout=5,
            )

        assert outcome.ok is False
        assert rev_parse(repo, branch) == before
        assert _run_git(["status", "--porcelain"], cwd=wt).stdout.strip() == ""
        # The resolution branch is gone; the story branch is not.
        assert not ref_exists(repo, "parallel-resolve/12-2-1")
        assert ref_exists(repo, branch)

    def test_resolution_happens_on_a_fresh_branch(self, repo: Path) -> None:
        """AC 3: the resolver never works on the story branch."""
        _story_branch(repo, "13.1", "shared.txt", "from 13.1\n")
        _story_branch(repo, "13.2", "shared.txt", "from 13.2\n")
        land_candidate("13.1", repo, _branch_name("13.1"), None,
                       integration_ref=BASE_BRANCH)

        branch = _branch_name("13.2")
        before = rev_parse(repo, branch)

        with patch(
            "subprocess.Popen",
            _claude_stub("--- FILE: shared.txt ---\nmerged\n--- END FILE ---\n"),
        ):
            outcome = resolve_on_resolution_branch(
                "13.2", repo, branch, BASE_BRANCH, attempt=1, timeout=5,
            )

        assert outcome.ok is True
        assert outcome.resolution_branch == "parallel-resolve/13-2-1"
        assert rev_parse(repo, branch) == before

    def test_resolution_timeout_default_is_600_not_the_measured_120(self) -> None:
        """AC 4: 600s, explicitly not the value at which resolution timed out."""
        assert ParallelConfig().conflict_resolution_timeout == 600

    async def test_another_story_lands_while_a_resolution_is_in_flight(
        self, repo: Path
    ) -> None:
        """AC 4b (NEG): the whole point of moving resolution out of the lock."""
        _story_branch(repo, "14.1", "shared.txt", "from 14.1\n")
        _story_branch(repo, "14.2", "shared.txt", "from 14.2\n")
        _story_branch(repo, "14.3", "independent.txt", "independent\n")

        queue = MergeQueue(repo, integration_ref=BASE_BRANCH)
        await queue.enqueue("14.1")
        await queue.enqueue("14.2")
        await queue.enqueue("14.3")

        # 14.1 lands cleanly; 14.2 then conflicts and goes to resolution.
        first = await queue.process_next()
        assert first is not None and first.landed

        resolution_started = asyncio.Event()
        release_resolution = asyncio.Event()
        landed_during_resolution: list[str] = []
        loop = asyncio.get_running_loop()

        def slow_resolution(*args: object, **kwargs: object) -> object:
            loop.call_soon_threadsafe(resolution_started.set)
            asyncio.run_coroutine_threadsafe(
                _wait(release_resolution), loop
            ).result(timeout=10)
            from bmad_assist_lite.parallel.merger import ResolutionOutcome

            return ResolutionOutcome(ok=False, error="resolver gave up")

        async def _wait(event: asyncio.Event) -> None:
            await event.wait()

        with patch(
            "bmad_assist_lite.parallel.merger.resolve_on_resolution_branch",
            slow_resolution,
        ):
            conflicted = asyncio.create_task(queue.process_next())
            await asyncio.wait_for(resolution_started.wait(), timeout=10)

            # The resolution is in flight and holds no lock, so 14.3 lands.
            independent = await asyncio.wait_for(queue.process_next(), timeout=10)
            assert independent is not None
            assert independent.landed is True
            landed_during_resolution.append(independent.story_id)

            release_resolution.set()
            parked = await asyncio.wait_for(conflicted, timeout=10)

        assert landed_during_resolution == ["14.3"]
        assert parked is not None and parked.parked is True

    async def test_candidate_whose_base_moved_is_discarded_and_retried(
        self, repo: Path
    ) -> None:
        """AC 4c: the optimistic-retry race, driven explicitly."""
        _story_branch(repo, "15.1", "a.txt", "alpha\n")
        branch = _branch_name("15.1")
        stale_base = rev_parse(repo, BASE_BRANCH)
        _commit(repo, "moved.txt", "moved\n", "chore: base moved under us")

        outcome = land_candidate(
            "15.1", repo, branch, None,
            integration_ref=BASE_BRANCH, expected_base_sha=stale_base,
        )

        assert outcome.status == "base_moved"
        # Nothing landed and nothing was rewritten.
        assert rev_parse(repo, BASE_BRANCH) != stale_base
        assert not (repo / "a.txt").exists()

    async def test_ladder_ends_in_park_never_in_a_delete(self, repo: Path) -> None:
        """AC 6: exhausting the ladder parks; the branch and worktree survive."""
        _story_branch(repo, "16.1", "shared.txt", "from 16.1\n")
        _story_branch(repo, "16.2", "shared.txt", "from 16.2\n")

        queue = MergeQueue(repo, integration_ref=BASE_BRANCH)
        await queue.enqueue("16.1")
        await queue.enqueue("16.2")
        await queue.process_next()

        with patch("subprocess.Popen", _claude_stub("no", returncode=1)):
            result = await queue.process_next()

        assert result is not None
        assert result.parked is True
        assert result.tier == MergeTier.PARK
        assert ref_exists(repo, _branch_name("16.2"))
        assert (repo.parent / "proj-parallel-16-2").exists()
        assert [a.tier for a in result.attempts][-1] == MergeTier.PARK

    def test_codex_is_never_used_for_conflict_resolution(self) -> None:
        """AC 5 (NEG): no Codex path is reachable from the resolver."""
        source = (
            Path(__file__).resolve().parents[1]
            / "src/bmad_assist_lite/parallel/merger.py"
        ).read_text(encoding="utf-8")
        assert "codex" not in source.lower()


# ============================================================================
# REQ-03.5 — verdicts bind to tree SHAs
# ============================================================================


class TestTreeBoundVerdicts:
    """Observations are persisted; verdicts are computed at read time."""

    def test_a_pass_on_tree_a_is_not_a_pass_on_tree_b(self, repo: Path) -> None:
        """AC 2 (the load-bearing test): the verdict auto-invalidates."""
        tree_a = tree_sha(repo, "HEAD")
        observations = [GateObservation(tree_sha=tree_a, result="pass")]
        assert gate_verdict(observations, tree_a) == "pass"

        _commit(repo, "new.txt", "new\n", "chore: change the tree")
        tree_b = tree_sha(repo, "HEAD")

        assert tree_b != tree_a
        assert gate_verdict(observations, tree_b) != "pass"
        assert gate_verdict(observations, tree_b) == "unknown"

    def test_a_rebase_changes_the_tree_so_the_verdict_does_not_survive(
        self, repo: Path
    ) -> None:
        """A rebase with behind > 0 always changes the tree — hence no skip."""
        _story_branch(repo, "17.1", "a.txt", "alpha\n")
        branch = _branch_name("17.1")
        before_tree = tree_sha(repo, branch)
        _commit(repo, "base2.txt", "b2\n", "chore: base moves")

        outcome = rebase_branch(repo, branch, BASE_BRANCH)

        assert outcome.status == "rebased"
        assert tree_sha(repo, branch) != before_tree

    def test_records_carry_both_tree_and_commit_sha(self, repo: Path) -> None:
        """AC 1: tree SHA for invalidation, commit SHA for humans."""
        _story_branch(repo, "18.1", "a.txt", "alpha\n")
        outcome = land_candidate("18.1", repo, _branch_name("18.1"), None,
                                 integration_ref=BASE_BRANCH)
        assert outcome.observation is not None
        assert outcome.observation.tree_sha
        assert outcome.observation.commit_sha
        assert outcome.observation.tree_sha != outcome.observation.commit_sha


# ============================================================================
# REQ-03.6 — never delete a branch or worktree that holds commits
# ============================================================================


class TestNoDataLoss:
    """The guard sits at the deletion site, and it is proven both ways."""

    def test_branch_with_unmerged_commits_is_not_deleted(self, repo: Path) -> None:
        """AC 2 (the load-bearing test): branch, worktree and commit survive."""
        sha = _story_branch(repo, "19.1", "a.txt", "alpha\n")
        branch = _branch_name("19.1")
        wt = repo.parent / "proj-parallel-19-1"

        cleanup_worktree("19.1", repo, integration_ref=BASE_BRANCH)

        assert ref_exists(repo, branch)
        assert wt.exists()
        assert rev_parse(repo, branch) == sha
        assert _git(repo, "cat-file", "-t", sha).stdout.strip() == "commit"

    def test_branch_with_zero_unmerged_commits_is_still_cleaned_up(
        self, repo: Path
    ) -> None:
        """AC 3 (NEG): the guard must not leak worktrees forever."""
        _story_branch(repo, "20.1", "a.txt", "alpha\n")
        branch = _branch_name("20.1")
        assert land_candidate("20.1", repo, branch, None,
                              integration_ref=BASE_BRANCH).status == "landed"

        # land_candidate cleans up on success; the branch and worktree are gone.
        assert not ref_exists(repo, branch)
        assert not (repo.parent / "proj-parallel-20-1").exists()

    def test_guard_at_the_deletion_site_survives_a_post_merge_qg_failure(
        self, repo: Path
    ) -> None:
        """AC 5b (NEG): the placement, not just the words.

        The deletion is upstream of the post-merge gate, on the success
        path. A guard in the gate's failure handler would run after the
        branch was already gone, so this drives a real gate failure through
        the real queue and asserts the branch is still there.
        """
        _commit(
            repo,
            "check.py",
            "import pathlib, sys\nsys.exit(1 if pathlib.Path('a.txt').exists() else 0)\n",
            "chore: checker",
        )
        _story_branch(repo, "21.1", "a.txt", "alpha\n")
        branch = _branch_name("21.1")
        sha = rev_parse(repo, branch)

        outcome = land_candidate(
            "21.1", repo, branch, _config_with_test("python3 check.py"),
            integration_ref=BASE_BRANCH,
        )

        assert outcome.status == "gate_failed"
        assert ref_exists(repo, branch)
        assert rev_parse(repo, branch) == sha
        assert (repo.parent / "proj-parallel-21-1").exists()

    def test_deletion_primitives_refuse_without_a_cleared_decision(self) -> None:
        """AC 4b (NEG): the runtime invariant at the deletion site."""
        with pytest.raises(ParallelError, match="without consulting"):
            assert_deletion_allowed(None, "branch parallel/1-1")
        with pytest.raises(ParallelError, match="without consulting"):
            assert_deletion_allowed("safe", "branch parallel/1-1")

        refused = DeletionDecision(
            branch="parallel/1-1", integration_ref="HEAD", safe=False,
            unmerged_commits=3, reason="3 commits would be lost",
            clauses_consulted=REQUIRED_CLAUSES,
        )
        with pytest.raises(ParallelError, match="refusing to delete"):
            assert_deletion_allowed(refused, "branch parallel/1-1")

        # REQ-05.4 crit 1 (A16): a verdict that consulted only one clause is
        # not a verdict. Hygiene and merge share this guard, so a half-checked
        # decision must be unusable at the deletion site, not merely discouraged.
        half_checked = DeletionDecision(
            branch="parallel/1-1", integration_ref="HEAD", safe=True,
            clauses_consulted=frozenset({"unmerged-commits"}),
        )
        with pytest.raises(ParallelError, match="both clauses"):
            assert_deletion_allowed(half_checked, "branch parallel/1-1")

    def test_guard_treats_an_unreadable_repository_as_unsafe(self, tmp_path: Path) -> None:
        """Indeterminate is unsafe: losing work is not recoverable."""
        decision = branch_deletion_decision(tmp_path / "nope", "parallel/1-1")
        assert decision.safe is False
        assert "refusing to delete" in decision.reason

    def test_guard_allows_deletion_of_a_branch_that_does_not_exist(
        self, repo: Path
    ) -> None:
        """Cleanup stays idempotent: there is nothing left to lose."""
        decision = branch_deletion_decision(repo, "parallel/does-not-exist")
        assert decision.safe is True

    def test_parked_merge_record_round_trips_at_the_documented_path(
        self, repo: Path
    ) -> None:
        """AC 5b: one record per parked merge, atomic, at a documented path."""
        from bmad_assist_lite.parallel.parked import ParkedMerge

        record = ParkedMerge(
            story_id="22.1",
            branch="parallel/22-1",
            worktree_path=str(repo.parent / "proj-parallel-22-1"),
            integration_head=rev_parse(repo, BASE_BRANCH),
            reason="conflict resolution failed",
            attempts=[
                MergeAttempt(
                    tier=MergeTier.AI_RESOLVE, branch="parallel/22-1",
                    commit_sha="a" * 40, tree_sha="b" * 40, outcome="conflict",
                )
            ],
        )
        path = record_parked_merge(repo, record)

        assert path == get_parked_dir(repo) / "22.1.yaml"
        assert path.exists()
        loaded = list_parked_merges(repo)
        assert [r.story_id for r in loaded] == ["22.1"]
        assert loaded[0].branch == "parallel/22-1"
        assert loaded[0].attempts[0].tier == MergeTier.AI_RESOLVE

    def test_operator_can_list_and_unpark_a_parked_merge(self, repo: Path) -> None:
        """AC 5c + 5d: discovery and the documented recovery step."""
        from bmad_assist_lite.parallel.parked import ParkedMerge

        record_parked_merge(repo, ParkedMerge(story_id="23.1", branch="parallel/23-1"))
        assert len(list_parked_merges(repo)) == 1

        assert unpark_merge(repo, "23.1") is True
        assert list_parked_merges(repo) == []
        assert unpark_merge(repo, "23.1") is False

    async def test_following_the_recovery_procedure_re_attempts_the_merge(
        self, repo: Path
    ) -> None:
        """AC 5d: the documented un-park steps actually get the work back."""
        _story_branch(repo, "27.1", "shared.txt", "from 27.1\n")
        _story_branch(repo, "27.2", "shared.txt", "from 27.2\n")

        queue = MergeQueue(repo, integration_ref=BASE_BRANCH)
        await queue.enqueue("27.1")
        await queue.enqueue("27.2")
        await queue.process_next()

        with patch("subprocess.Popen", _claude_stub("no", returncode=1)):
            parked = await queue.process_next()
        assert parked is not None and parked.parked is True
        assert len(list_parked_merges(repo)) == 1

        # Step 2 of the procedure: fix the work on the branch itself.
        wt = repo.parent / "proj-parallel-27-2"
        _commit(wt, "shared.txt", "from 27.1\nfrom 27.2\n", "fix: reconcile by hand")

        # Steps 3-5: clear the record, re-queue, re-run.
        assert unpark_merge(repo, "27.2") is True
        await queue.enqueue("27.2")
        relanded = await queue.process_next()

        assert relanded is not None
        assert relanded.landed is True
        assert (repo / "shared.txt").read_text() == "from 27.1\nfrom 27.2\n"
        assert list_parked_merges(repo) == []


# ============================================================================
# REQ-03.7 — the merge queue survives a crash
# ============================================================================


class TestReconcile:
    """Git, not a persisted flag, is the authority for what landed."""

    def test_unlanded_merges_are_rederived_after_a_crash(self, repo: Path) -> None:
        """AC 1: a merge interrupted mid-queue comes back."""
        _story_branch(repo, "24.1", "a.txt", "alpha\n")
        state = create_initial_state(BASE_BRANCH, 24, ["24.1"])
        state = state.with_story_status("24.1", StoryStatus.MERGING)

        outcome = reconcile_merge_queue(state, repo, BASE_BRANCH)

        assert outcome.requeue == ["24.1"]
        assert outcome.landed == []

    def test_an_already_landed_merge_is_not_reattempted(self, repo: Path) -> None:
        """AC 2 (NEG): idempotence — the integration head is unchanged."""
        _story_branch(repo, "25.1", "a.txt", "alpha\n")
        outcome = land_candidate("25.1", repo, _branch_name("25.1"), None,
                                 integration_ref=BASE_BRANCH)
        head_before = rev_parse(repo, BASE_BRANCH)

        # The crash lands mid-queue: the branch was cleaned up, the state
        # still says "merging". Git is asked, and git says it landed.
        state = create_initial_state(BASE_BRANCH, 25, ["25.1"])
        state = state.with_story_status(
            "25.1", StoryStatus.MERGING, landed_commit_sha=outcome.commit_sha,
        )

        outcome = reconcile_merge_queue(state, repo, BASE_BRANCH)

        assert outcome.landed == ["25.1"]
        assert outcome.requeue == []
        assert rev_parse(repo, BASE_BRANCH) == head_before

    def test_reconcile_reads_git_not_the_persisted_status(self, repo: Path) -> None:
        """AC 3: a state that claims done is corrected by git."""
        _story_branch(repo, "26.1", "a.txt", "alpha\n")
        state = create_initial_state(BASE_BRANCH, 26, ["26.1"])
        state = state.with_story_status("26.1", StoryStatus.DONE)

        outcome = reconcile_merge_queue(state, repo, BASE_BRANCH)

        # The record says done; git says the commits are not on the head.
        assert outcome.requeue == ["26.1"]

    def test_reconcile_is_one_named_function(self) -> None:
        """AC 4: not logic scattered across startup paths."""
        from bmad_assist_lite.parallel import recovery

        assert callable(recovery.reconcile_merge_queue)
        assert "reconcile_merge_queue" in recovery.__all__


# ============================================================================
# REQ-03.8 — two concurrent orchestrators are detected
# ============================================================================


class TestRunExclusion:
    """On-disk lock with PID liveness, beside — never replacing — the async lock."""

    def test_second_run_against_a_locked_project_is_refused(self, tmp_path: Path) -> None:
        """AC 1: a clear refusal naming the remedy."""
        from bmad_assist_lite.core.exceptions import StateError
        from bmad_assist_lite.loop.locking import running_lock

        with running_lock(tmp_path):
            with pytest.raises(StateError, match="already active"):
                with running_lock(tmp_path):
                    pass

    def test_a_stale_lock_from_a_dead_pid_does_not_block_forever(
        self, tmp_path: Path
    ) -> None:
        """AC 2 (NEG): liveness is PID-based, not timeout-based."""
        from bmad_assist_lite.loop.locking import running_lock

        lock_dir = tmp_path / ".bmad-assist-lite"
        lock_dir.mkdir(parents=True)
        # PID 0 is never a live user process on any supported platform.
        (lock_dir / "running.lock").write_text("0\n2020-01-01T00:00:00\n")

        with running_lock(tmp_path) as lock_path:
            assert lock_path.exists()

    def test_the_intra_run_asyncio_lock_is_not_replaced(self) -> None:
        """AC 3 (NEG): the in-process lock is sufficient and must remain."""
        source = (
            Path(__file__).resolve().parents[1]
            / "src/bmad_assist_lite/parallel/merger.py"
        ).read_text(encoding="utf-8")
        assert "asyncio.Lock()" in source
        assert "async with self._lock" in source

    def test_nothing_hard_codes_main_as_the_integration_target(self) -> None:
        """REQ-03.9 AC 3: the integration target is configuration."""
        assert ParallelConfig().integration_branch is None
        queue = MergeQueue(Path("/nonexistent"))
        assert queue.integration_ref == "HEAD"
        queue = MergeQueue(
            Path("/nonexistent"),
            parallel_config=ParallelConfig(integration_branch="develop"),
        )
        assert queue.integration_ref == "develop"
