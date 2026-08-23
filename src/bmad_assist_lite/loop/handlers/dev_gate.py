"""DEV_GATE phase handler — real per-story dev gate (SP-A0), non-LLM.

Runs the configured real dev-gate commands (typecheck + the story's own tests)
in the story worktree immediately after ``dev_story`` and records an objective
pass/fail verdict in run state. This is the run7 offline-replay method moved
in-chain: the objective tripwire the lean-first adaptive fallback (SP-A1) hangs
on, and the honest in-run signal the trivial gate never gave.

The phase is only inserted into the story sequence when
``quality_gate.real_dev_gate`` is on (see ``loop.runner.effective_story_phases``),
so with the flag off the chain is byte-identical. The handler still guards the
flag defensively, so a hand-added phase without the flag is a clean no-op.

SP-A0 only *records* the verdict and advances; it never blocks and never
retries. The adaptive fallback (SP-A1) adds the retry branch on top.
"""

import json
import logging
from pathlib import Path

from bmad_assist_lite.core.config import Config
from bmad_assist_lite.core.gate_runner import GateCommand, run_gates
from bmad_assist_lite.core.git import list_changed_files
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.handlers.dev_story import resolve_dev_lean_mode
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.loop.worktree_snapshot import restore_worktree
from bmad_assist_lite.providers.base import write_progress

logger = logging.getLogger(__name__)

#: Default basename of the run's implementation-artifacts directory — the home of
#: the story docs, validations, code-reviews, and sprint-status.yaml. A story
#: whose entire diff sits under a directory with this name changed only the run's
#: own bookkeeping, not the product. Used as the fallback when the paths
#: singleton is not initialised (unit tests) or points at a different checkout
#: than the worktree the gate runs in.
_IMPL_ARTIFACTS_DIRNAME_DEFAULT = "implementation-artifacts"


def _implementation_artifacts_dirname() -> str:
    """Basename of the implementation-artifacts dir, or the default fallback.

    The paths singleton is the source of truth when it is initialised; the
    fallback keeps the hollow guard working before paths are wired and in a
    parallel worktree whose singleton still points at the main checkout.
    """
    try:
        from bmad_assist_lite.core.paths import get_paths

        return get_paths().implementation_artifacts.name
    except (RuntimeError, ImportError):
        return _IMPL_ARTIFACTS_DIRNAME_DEFAULT


def _is_bookkeeping_path(repo_rel_path: str, artifacts_dirname: str) -> bool:
    """True if a repo-relative path lives under the implementation-artifacts dir.

    Matches on the directory *name* as a path segment rather than a resolved
    prefix, so it holds regardless of where the repo root is (worktree vs main
    checkout) and of the depth the artifacts dir sits at.
    """
    return artifacts_dirname in Path(repo_rel_path).parts


def story_change_is_hollow(project_path: Path) -> bool | None:
    """Whether the story's working-tree changes are bookkeeping-only (hollow).

    Returns:
        True  — there ARE changes but every one is a run bookkeeping artifact
                (the story doc / sprint-status under the implementation-artifacts
                dir): dev_story implemented nothing. This is the goal-run11
                story-6.5 false-completion signature.
        False — at least one changed file is story-relevant (code, docs, or tests
                outside the bookkeeping dir).
        None  — undetermined: git is unavailable/errored, OR the tree is
                completely clean (ambiguous with an already-committed resume).
                A caller MUST NOT treat None as hollow — the guard blocks only on
                the positive evidence of True.

    """
    changed = list_changed_files(project_path)
    if not changed:  # None (git failure) or [] (clean tree) — both undetermined
        return None
    artifacts = _implementation_artifacts_dirname()
    real = [f for f in changed if not _is_bookkeeping_path(f, artifacts)]
    return len(real) == 0


class DevGateHandler:
    """Non-LLM handler: run the real dev gate and record the objective verdict."""

    def __init__(self, config: Config, project_path: Path) -> None:
        """Initialize the handler with config and project path."""
        self.config = config
        self.project_path = project_path

    def _commands(self) -> list[GateCommand]:
        """Resolve the ordered gate commands: explicit list, else typecheck+test."""
        qg = self.config.quality_gate
        if qg is None:
            return []
        if qg.real_dev_gate_commands:
            return [
                GateCommand(name=c.name, command=c.command)
                for c in qg.real_dev_gate_commands
            ]
        commands: list[GateCommand] = []
        if qg.typecheck:
            commands.append(GateCommand(name="Typecheck", command=qg.typecheck))
        test_cmd = qg.test_unit or qg.test
        if test_cmd:
            commands.append(GateCommand(name="Tests", command=test_cmd))
        return commands

    def _timeout(self) -> int:
        """Per-command timeout — the dev gate's own, sized for a full test suite."""
        qg = self.config.quality_gate
        return qg.real_dev_gate_command_timeout if qg else 900

    def _write_adaptive_record(self, state: State, record: dict[str, object]) -> None:
        """Append the per-attempt record to a durable per-story JSONL.

        run state carries only the CURRENT story's records (they reset at the
        next story), so the epic harvest reads this cache file instead — archived
        across story transitions by the forensics sweep.
        """
        cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            path = cache_dir / f"dev-adaptive-{state.current_story or 'unknown'}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as e:
            logger.warning("dev-adaptive record write failed: %s", e)

    def execute(self, state: State) -> PhaseResult:
        """Run the real dev gate, record the verdict, and advance — or trip the retry.

        SP-A0: records an objective verdict and advances (``dev_gate_action`` of
        ``pass``/``fail``). SP-A1: when ``speed.lean_dev_adaptive`` is on and the
        first (lean-first) attempt fails the gate, restore the pre-dev worktree
        and route back to ``dev_story`` for one lean-off retry
        (``dev_gate_action`` ``retry``); the retry's result then stands.
        """
        qg = self.config.quality_gate
        if qg is None or not qg.real_dev_gate:
            # Defensive: the phase should not be in the sequence with the flag off.
            return PhaseResult.ok({"dev_gate_action": "skipped"})

        commands = self._commands()
        if not commands:
            logger.warning(
                "real_dev_gate on but no commands resolved (no real_dev_gate_commands "
                "and no typecheck/test) — recording a no-op verdict"
            )
            record: dict[str, object] = {
                "attempt": state.dev_attempt,
                "passed": None,
                "failed": [],
                "reason": "no_commands",
            }
            state.dev_gate_records.append(record)
            self._write_adaptive_record(state, record)
            return PhaseResult.ok({"dev_gate_action": "no_commands"})

        # Hollow-story guard: a story whose only working-tree changes are the
        # run's own bookkeeping artifacts implemented nothing, and an empty diff
        # passes typecheck+tests trivially — so short-circuit the gate to a fail
        # instead of running (and trivially passing) the commands. Positive
        # evidence only (see story_change_is_hollow): git failure or a clean tree
        # is None and proceeds to the real gate unchanged.
        hollow = (
            story_change_is_hollow(self.project_path)
            if qg.real_dev_gate_hollow_guard
            else None
        )
        if hollow:
            passed = False
            failed = ["hollow-story: no story-relevant changes (bookkeeping only)"]
            classification = "hollow"
        else:
            run = run_gates(
                commands,
                self.project_path,
                timeout=self._timeout(),
                label=f"dev-gate:{state.current_story or '?'}",
            )
            passed = run.all_passed
            failed = [o.name for o in run.failures]
            if passed:
                classification = "pass"
            else:
                cls = run.overall_classification
                classification = cls.value if cls is not None else "real"

        record = {
            "attempt": state.dev_attempt,
            "lean_mode": resolve_dev_lean_mode(self.config, state).value,
            "passed": passed,
            "failed": failed,
            "classification": classification,
            "retry_fired": False,
        }

        # SP-A1 adaptive fallback: the first (lean-first) attempt failing the gate
        # re-runs the story lean-off from the pre-dev state, exactly once. An
        # env-classified failure (command not executable: missing deps, no shell)
        # would fail the retry's gate identically, so the redo would be a pure
        # waste — record the verdict and advance instead.
        env_failure = classification == "env"
        do_retry = (
            not passed
            and not env_failure
            and self.config.speed.lean_dev_adaptive
            and state.dev_attempt == 0
            and not state.adaptive_retry_fired
        )
        if not passed and env_failure and state.dev_attempt == 0:
            record["retry_skipped"] = "env"
        if do_retry:
            restored = (
                restore_worktree(self.project_path, state.pre_dev_snapshot)
                if state.pre_dev_snapshot
                else False
            )
            record["retry_fired"] = True
            record["retry_restored"] = restored

        state.dev_gate_records.append(record)
        self._write_adaptive_record(state, record)

        if passed:
            write_progress(f"  Dev gate PASSED — story {state.current_story}")
            logger.info("Dev gate passed for story %s", state.current_story)
            return PhaseResult.ok({"dev_gate_action": "pass", "dev_gate_passed": True})

        if do_retry:
            state.adaptive_retry_fired = True
            note = "" if record.get("retry_restored") else " (WARNING: snapshot restore unavailable)"
            write_progress(
                f"  Dev gate FAILED — lean-first fallback: retrying story "
                f"{state.current_story} lean-OFF from pre-dev state{note}"
            )
            logger.warning(
                "Adaptive fallback: retrying story %s lean-OFF (restored=%s, failed: %s)",
                state.current_story,
                record.get("retry_restored"),
                ", ".join(failed),
            )
            return PhaseResult(
                success=True,
                next_phase=Phase.DEV_STORY,
                outputs={"dev_gate_action": "retry", "dev_gate_passed": False},
            )

        # Confirmed-hollow story (guard tripped, and the one lean-off retry — if
        # enabled — did not produce real work): fail the PHASE, not just record a
        # verdict. SP-A0's normal fail advances to the quality gate, which also
        # passes trivially on an empty diff; only failing the phase stops the run
        # from certifying and committing an empty implementation. Nothing is
        # committed at dev-gate time, so failing here leaves no hollow commit.
        if classification == "hollow":
            write_progress(
                f"  Dev gate FAILED — story {state.current_story} implemented "
                "nothing (only bookkeeping artifacts changed); halting rather "
                "than certifying a hollow story"
            )
            logger.error(
                "Dev gate hollow-guard: story %s produced no story-relevant "
                "changes (bookkeeping only) after %d attempt(s) — failing the "
                "phase so the run parks it instead of committing empty work",
                state.current_story,
                state.dev_attempt + 1,
            )
            return PhaseResult(
                success=False,
                error=(
                    f"Hollow story {state.current_story}: dev_story produced no "
                    "story-relevant changes (only run bookkeeping artifacts "
                    "changed). Refusing to certify or commit an empty "
                    "implementation."
                ),
                outputs={"dev_gate_action": "hollow", "dev_gate_passed": False},
            )

        env_note = (
            " — env-classified, lean-off retry skipped (fix the environment, not the code)"
            if record.get("retry_skipped") == "env"
            else ""
        )
        write_progress(
            f"  Dev gate FAILED — story {state.current_story} "
            f"(failed: {', '.join(failed)}){env_note}"
        )
        logger.warning(
            "Dev gate failed for story %s: %s [%s]",
            state.current_story,
            ", ".join(failed),
            classification,
        )
        return PhaseResult.ok({"dev_gate_action": "fail", "dev_gate_passed": False})
