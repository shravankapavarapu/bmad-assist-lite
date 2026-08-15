"""Sign-off is a recorded artifact bound to the tree it approved.

Incident **I-01** is the failure this guards: a story reached `done` with zero
implementing commits — one violation in thirteen stories, silent and
high-impact. A sign-off that is an implicit step in a prompt leaves no evidence
that anything was approved, and a sign-off recorded without a SHA silently
outlives the code it approved.

The artifact binds to the **tree** SHA (`git rev-parse HEAD^{tree}`), reusing
REQ-03.5's mechanism rather than inventing a second one: a commit SHA changes on
every rebase even when the content did not, so commit-SHA binding produces false
invalidations, while the tree SHA changes exactly when the content does.
"""

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from bmad_assist_lite.core.signoff import (
    SignoffRecord,
    load_signoff,
    signoff_blocks_done,
    story_commit_count,
    write_signoff,
)
from bmad_assist_lite.core.sprint_status import SprintStatus
from bmad_assist_lite.core.sprint_sync import sync_state_to_sprint
from bmad_assist_lite.core.state import State


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository with one commit, so tree SHAs are real."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "seed.txt").write_text("seed\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


def _sign(repo: Path, story_id: str = "1.1", **over: object) -> SignoffRecord:
    """Record an approval bound to the repository's current tree."""
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    fields: dict[str, object] = {
        "story_id": story_id,
        "tree_sha": tree,
        "commit_sha": _git(repo, "rev-parse", "HEAD"),
        "verdict": "approved",
        "reviewer": "architect",
        "timestamp": datetime(2026, 8, 11, 12, 0, 0),
    }
    fields.update(over)
    record = SignoffRecord(**fields)  # type: ignore[arg-type]
    write_signoff(record, repo)
    return record


def _implement(repo: Path, story_id: str = "1.1") -> None:
    """Make a commit that follows the tool's own auto-commit convention."""
    (repo / f"story_{story_id.replace('.', '_')}.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", f"feat(story-{story_id}): implement it")


# ============================================================================
# The artifact
# ============================================================================


class TestSignoffArtifact:
    """Criterion 1 and 4: what is recorded, and how it is written."""

    def test_records_sha_verdict_reviewer_and_timestamp(self, repo: Path) -> None:
        record = _sign(repo)
        loaded = load_signoff(repo, "1.1")
        assert loaded is not None
        assert loaded.tree_sha == record.tree_sha
        assert loaded.verdict == "approved"
        assert loaded.reviewer == "architect"
        assert loaded.timestamp == datetime(2026, 8, 11, 12, 0, 0)

    def test_is_frozen(self, repo: Path) -> None:
        record = _sign(repo)
        with pytest.raises(Exception):
            record.verdict = "rejected"  # type: ignore[misc]

    def test_write_leaves_no_temp_file(self, repo: Path) -> None:
        """Criterion 4: atomic write, temp + os.replace, nothing left behind."""
        _sign(repo)
        assert not list((repo / ".bmad-assist-lite" / "signoffs").glob("*.tmp"))

    def test_missing_signoff_reads_as_none(self, repo: Path) -> None:
        assert load_signoff(repo, "9.9") is None

    def test_a_rejected_verdict_is_not_an_approval(self, repo: Path) -> None:
        _implement(repo)
        _sign(repo, verdict="rejected")
        assert signoff_blocks_done(repo, "1.1") is not None


# ============================================================================
# The I-01 guard
# ============================================================================


class TestDoneRequiresSignoff:
    """Criteria 2 and 3 — the load-bearing refusals."""

    def test_a_matching_signoff_permits_done(self, repo: Path) -> None:
        """NEG control: the guard must let the correct case through."""
        _implement(repo)
        _sign(repo)
        assert signoff_blocks_done(repo, "1.1") is None

    def test_a_stale_signoff_does_not_permit_done(self, repo: Path) -> None:
        """LOAD-BEARING: a sign-off must not outlive the tree it approved."""
        _implement(repo)
        _sign(repo)
        (repo / "changed_after_signoff.py").write_text("y = 2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "feat(story-1.1): later change")

        reason = signoff_blocks_done(repo, "1.1")
        assert reason is not None
        assert "stale" in reason.lower()

    def test_no_signoff_at_all_does_not_permit_done(self, repo: Path) -> None:
        _implement(repo)
        reason = signoff_blocks_done(repo, "1.1")
        assert reason is not None
        assert "no sign-off" in reason.lower()

    def test_zero_implementing_commits_names_i01(self, repo: Path) -> None:
        """LOAD-BEARING: the exact I-01 shape, refused and named."""
        _sign(repo)
        reason = signoff_blocks_done(repo, "1.1")
        assert reason is not None
        assert "I-01" in reason
        assert "zero implementing commits" in reason.lower()

    def test_commit_counting_follows_the_auto_commit_convention(self, repo: Path) -> None:
        assert story_commit_count(repo, "1.1") == 0
        _implement(repo)
        assert story_commit_count(repo, "1.1") == 1

    def test_a_non_repo_does_not_manufacture_a_refusal(self, tmp_path: Path) -> None:
        """NEG — outside git the guard cannot judge, so it must not block."""
        assert signoff_blocks_done(tmp_path, "1.1") is None


# ============================================================================
# Wiring into the done flip
# ============================================================================


class TestSprintSyncGate:
    """The postcondition sits where `done` is actually written."""

    def test_off_by_default_preserves_todays_behaviour(self, repo: Path) -> None:
        """G8: the guard is additive and opt-in; nothing changes on upgrade."""
        status = SprintStatus(development_status={"story-1-1": "review"})
        sync_state_to_sprint(State(completed_stories=["1.1"]), status)
        assert status.get_story_status("1.1") == "done"

    def test_enabled_guard_refuses_the_unsigned_done(self, repo: Path) -> None:
        """LOAD-BEARING: with the guard on, an unsigned story stays un-done."""
        status = SprintStatus(development_status={"story-1-1": "review"})
        sync_state_to_sprint(
            State(completed_stories=["1.1"]),
            status,
            signoff_check=lambda story_id: "no sign-off artifact",
        )
        assert status.get_story_status("1.1") != "done"

    def test_enabled_guard_allows_a_signed_done(self, repo: Path) -> None:
        status = SprintStatus(development_status={"story-1-1": "review"})
        sync_state_to_sprint(
            State(completed_stories=["1.1"]),
            status,
            signoff_check=lambda story_id: None,
        )
        assert status.get_story_status("1.1") == "done"

    def test_the_guard_never_writes_back_into_state(self, repo: Path) -> None:
        """The artifact must not become a reverse sprint-status → state channel."""
        state = State(completed_stories=["1.1"])
        sync_state_to_sprint(
            state,
            SprintStatus(development_status={"story-1-1": "review"}),
            signoff_check=lambda story_id: "blocked",
        )
        assert state.completed_stories == ["1.1"]


class TestSignoffConfig:
    """The config surface is additive (G8)."""

    def test_defaults_to_not_required(self) -> None:
        from bmad_assist_lite.core.config import load_config

        config = load_config(
            {"providers": {"master": {"provider": "claude", "model": "opus"}}}
        )
        assert config.signoff.required is False

    def test_can_be_switched_on(self) -> None:
        from bmad_assist_lite.core.config import load_config

        config = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "signoff": {"required": True},
            }
        )
        assert config.signoff.required is True
