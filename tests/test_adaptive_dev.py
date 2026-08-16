"""Tests for SP-A1 — lean-first adaptive dev (speed.lean_dev_adaptive).

Covers the config guard (adaptive requires the real gate), the per-attempt lean
mode, the dev-gate retry branch, and — the contract-pinned invariant — that the
retry restarts from the pre-dev worktree state with no half-lean residue, while
gitignored build output survives.
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from bmad_assist_lite.core.config import LeanDev, load_config
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.handlers.dev_gate import DevGateHandler
from bmad_assist_lite.loop.handlers.dev_story import resolve_dev_lean_mode
from bmad_assist_lite.loop.worktree_snapshot import restore_worktree, snapshot_worktree

_PROVIDERS = {"providers": {"master": {"provider": "claude", "model": "opus"}}}


def _adaptive_cfg(**overrides):
    """A valid adaptive config (adaptive on + real gate on with one command)."""
    data = {
        **_PROVIDERS,
        "speed": {"lean_dev_adaptive": True},
        "quality_gate": {
            "real_dev_gate": True,
            "real_dev_gate_commands": [{"name": "Tests", "command": "true"}],
        },
    }
    data.update(overrides)
    return load_config(data)


def _run_result(all_passed, failed_names=(), classification="real"):
    return SimpleNamespace(
        all_passed=all_passed,
        failures=[SimpleNamespace(name=n) for n in failed_names],
        overall_classification=SimpleNamespace(value=classification),
    )


class TestAdaptiveConfigSafety:
    """Adaptive is ON by default; with no real gate it degrades to full dev (not rejected)."""

    def test_adaptive_without_gate_loads_and_degrades_to_full(self) -> None:
        # No quality_gate group: the gate resolves no commands, so lean-first does
        # NOT engage — the story runs full dev (never backstop-less blanket lean).
        cfg = load_config({**_PROVIDERS, "speed": {"lean_dev_adaptive": True}})
        assert cfg.speed.lean_dev_adaptive is True
        assert resolve_dev_lean_mode(cfg, State(dev_attempt=0)) is LeanDev.OFF

    def test_adaptive_with_gate_group_but_gate_off_degrades_to_full(self) -> None:
        # real_dev_gate explicitly off => no gate to back the fallback => full dev.
        cfg = load_config(
            {
                **_PROVIDERS,
                "speed": {"lean_dev_adaptive": True},
                "quality_gate": {"typecheck": "true", "real_dev_gate": False},
            }
        )
        assert resolve_dev_lean_mode(cfg, State(dev_attempt=0)) is LeanDev.OFF

    def test_adaptive_with_gate_is_valid(self) -> None:
        cfg = _adaptive_cfg()
        assert cfg.speed.lean_dev_adaptive is True
        assert cfg.quality_gate.real_dev_gate is True

    def test_lean_dev_adaptive_defaults_on(self) -> None:
        assert load_config(_PROVIDERS).speed.lean_dev_adaptive is True

    def test_real_dev_gate_defaults_on_when_group_present(self) -> None:
        cfg = load_config({**_PROVIDERS, "quality_gate": {"typecheck": "true"}})
        assert cfg.quality_gate.real_dev_gate is True


class TestResolveLeanMode:
    """Adaptive forces lean-first then lean-off; otherwise the static mode holds."""

    def test_adaptive_attempt0_is_full(self) -> None:
        cfg = _adaptive_cfg()
        assert resolve_dev_lean_mode(cfg, State(dev_attempt=0)) is LeanDev.FULL

    def test_adaptive_retry_is_off(self) -> None:
        cfg = _adaptive_cfg()
        assert resolve_dev_lean_mode(cfg, State(dev_attempt=1)) is LeanDev.OFF

    def test_non_adaptive_uses_static_mode(self) -> None:
        cfg = load_config({**_PROVIDERS, "speed": {"lean_dev": "report_only"}})
        # dev_attempt is irrelevant when adaptive is off.
        assert resolve_dev_lean_mode(cfg, State(dev_attempt=1)) is LeanDev.REPORT_ONLY


class TestDevGateRetryBranch:
    """The gate trips exactly one lean-off retry on a failed lean-first attempt."""

    def test_lean_first_fail_triggers_retry(self, tmp_path) -> None:
        handler = DevGateHandler(_adaptive_cfg(), tmp_path)
        state = State(current_epic=4, current_story="4.7", dev_attempt=0)
        state.pre_dev_snapshot = "deadbeef"
        with (
            patch(
                "bmad_assist_lite.loop.handlers.dev_gate.run_gates",
                return_value=_run_result(False, ("Tests",)),
            ),
            patch(
                "bmad_assist_lite.loop.handlers.dev_gate.restore_worktree",
                return_value=True,
            ) as restore,
        ):
            result = handler.execute(state)
        assert result.next_phase is Phase.DEV_STORY
        assert result.outputs["dev_gate_action"] == "retry"
        assert state.adaptive_retry_fired is True
        restore.assert_called_once_with(tmp_path, "deadbeef")
        rec = state.dev_gate_records[0]
        assert rec["retry_fired"] is True and rec["retry_restored"] is True
        assert rec["lean_mode"] == "full" and rec["attempt"] == 0

    def test_retry_still_fires_when_snapshot_missing(self, tmp_path) -> None:
        handler = DevGateHandler(_adaptive_cfg(), tmp_path)
        state = State(current_epic=4, current_story="4.7", dev_attempt=0)
        # No pre_dev_snapshot → retry still fires but flags restore unavailable.
        with patch(
            "bmad_assist_lite.loop.handlers.dev_gate.run_gates",
            return_value=_run_result(False, ("Tests",)),
        ):
            result = handler.execute(state)
        assert result.next_phase is Phase.DEV_STORY
        assert state.dev_gate_records[0]["retry_restored"] is False

    def test_env_classified_failure_skips_retry(self, tmp_path) -> None:
        # An env-blocked gate (command not executable: missing deps, no shell)
        # would fail the retry's gate identically, so the lean-off re-run is a
        # pure waste — record the verdict and advance instead.
        handler = DevGateHandler(_adaptive_cfg(), tmp_path)
        state = State(current_epic=4, current_story="4.7", dev_attempt=0)
        state.pre_dev_snapshot = "deadbeef"
        with (
            patch(
                "bmad_assist_lite.loop.handlers.dev_gate.run_gates",
                return_value=_run_result(False, ("Tests",), classification="env"),
            ),
            patch(
                "bmad_assist_lite.loop.handlers.dev_gate.restore_worktree",
            ) as restore,
        ):
            result = handler.execute(state)
        assert result.next_phase is None
        assert result.outputs["dev_gate_action"] == "fail"
        assert state.adaptive_retry_fired is False
        restore.assert_not_called()
        rec = state.dev_gate_records[0]
        assert rec["retry_fired"] is False
        assert rec["retry_skipped"] == "env"

    def test_retry_attempt_does_not_retry_again(self, tmp_path) -> None:
        handler = DevGateHandler(_adaptive_cfg(), tmp_path)
        # Second attempt (lean-off) still fails → the retry's result stands.
        state = State(current_epic=4, current_story="4.7", dev_attempt=1)
        with patch(
            "bmad_assist_lite.loop.handlers.dev_gate.run_gates",
            return_value=_run_result(False, ("Tests",)),
        ):
            result = handler.execute(state)
        assert result.next_phase is None
        assert result.outputs["dev_gate_action"] == "fail"
        assert state.dev_gate_records[0]["lean_mode"] == "off"

    def test_lean_first_pass_advances_without_retry(self, tmp_path) -> None:
        handler = DevGateHandler(_adaptive_cfg(), tmp_path)
        state = State(current_epic=4, current_story="4.7", dev_attempt=0)
        with patch(
            "bmad_assist_lite.loop.handlers.dev_gate.run_gates",
            return_value=_run_result(True),
        ):
            result = handler.execute(state)
        assert result.next_phase is None
        assert result.outputs["dev_gate_action"] == "pass"
        assert state.adaptive_retry_fired is False

    def test_durable_record_written(self, tmp_path) -> None:
        handler = DevGateHandler(_adaptive_cfg(), tmp_path)
        state = State(current_epic=4, current_story="4.7", dev_attempt=0)
        with patch(
            "bmad_assist_lite.loop.handlers.dev_gate.run_gates",
            return_value=_run_result(True),
        ):
            handler.execute(state)
        record_file = tmp_path / ".bmad-assist-lite" / "cache" / "dev-adaptive-4.7.jsonl"
        assert record_file.exists()
        assert '"attempt": 0' in record_file.read_text(encoding="utf-8")


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _init_repo(path):
    _git(["init", "-q"], path)
    _git(["config", "user.email", "t@t.t"], path)
    _git(["config", "user.name", "t"], path)
    (path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["commit", "-qm", "base"], path)


class TestWorktreeSnapshotRestore:
    """The contract invariant: retry restarts from the exact pre-dev state."""

    def test_restore_reverts_dev_and_keeps_prior_artifacts_and_ignored(self, tmp_path) -> None:
        _init_repo(tmp_path)
        # Prior-phase untracked artifact (the story doc) and gitignored build output.
        (tmp_path / "story.md").write_text("story\n", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "dep.txt").write_text("dep\n", encoding="utf-8")

        snapshot = snapshot_worktree(tmp_path)
        assert snapshot is not None

        # Simulate a lean dev attempt: edit a tracked file, delete another,
        # create a new one, and touch gitignored output.
        (tmp_path / "tracked.txt").write_text("dev-edited\n", encoding="utf-8")
        (tmp_path / "story.md").write_text("dev-touched\n", encoding="utf-8")
        (tmp_path / "newcode.py").write_text("print('x')\n", encoding="utf-8")
        (tmp_path / "node_modules" / "dep.txt").write_text("rebuilt\n", encoding="utf-8")

        assert restore_worktree(tmp_path, snapshot) is True

        # Tracked file reverted; untracked prior artifact restored; dev's new file
        # gone; gitignored build output left untouched (not reverted, not deleted).
        assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "base\n"
        assert (tmp_path / "story.md").read_text(encoding="utf-8") == "story\n"
        assert not (tmp_path / "newcode.py").exists()
        assert (tmp_path / "node_modules" / "dep.txt").read_text(encoding="utf-8") == "rebuilt\n"

    def test_restore_recreates_a_dev_deleted_tracked_file(self, tmp_path) -> None:
        _init_repo(tmp_path)
        snapshot = snapshot_worktree(tmp_path)
        assert snapshot is not None
        (tmp_path / "tracked.txt").unlink()  # dev deleted a tracked file
        assert restore_worktree(tmp_path, snapshot) is True
        assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "base\n"

    def test_snapshot_on_non_git_dir_returns_none(self, tmp_path) -> None:
        assert snapshot_worktree(tmp_path) is None
