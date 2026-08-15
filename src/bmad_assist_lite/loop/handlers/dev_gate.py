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
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.loop.handlers.dev_story import resolve_dev_lean_mode
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.loop.worktree_snapshot import restore_worktree
from bmad_assist_lite.providers.base import write_progress

logger = logging.getLogger(__name__)


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
        """Per-command timeout, shared with the quality-gate fallback config."""
        return self.config.quality_gate.command_timeout if self.config.quality_gate else 120

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
        # re-runs the story lean-off from the pre-dev state, exactly once.
        do_retry = (
            not passed
            and self.config.speed.lean_dev_adaptive
            and state.dev_attempt == 0
            and not state.adaptive_retry_fired
        )
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

        write_progress(
            f"  Dev gate FAILED — story {state.current_story} (failed: {', '.join(failed)})"
        )
        logger.warning(
            "Dev gate failed for story %s: %s [%s]",
            state.current_story,
            ", ".join(failed),
            classification,
        )
        return PhaseResult.ok({"dev_gate_action": "fail", "dev_gate_passed": False})
