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

import logging
from pathlib import Path

from bmad_assist_lite.core.config import Config
from bmad_assist_lite.core.gate_runner import GateCommand, run_gates
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.types import PhaseResult
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

    def execute(self, state: State) -> PhaseResult:
        """Run the real dev gate, record the verdict, and advance.

        Returns ``dev_gate_action`` of ``pass``/``fail`` (or ``skipped`` /
        ``no_commands`` when inapplicable). SP-A0 never sets ``next_phase``: the
        story advances to review regardless, with the verdict recorded so the
        adaptive fallback and the offline analysis can key off it.
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
            state.dev_gate_records.append(
                {"passed": None, "failed": [], "reason": "no_commands"}
            )
            return PhaseResult.ok({"dev_gate_action": "no_commands"})

        run = run_gates(
            commands,
            self.project_path,
            timeout=self._timeout(),
            label=f"dev-gate:{state.current_story or '?'}",
        )
        failed = [o.name for o in run.failures]
        if run.all_passed:
            classification = "pass"
        else:
            cls = run.overall_classification
            classification = cls.value if cls is not None else "real"
        record: dict[str, object] = {
            "passed": run.all_passed,
            "failed": failed,
            "classification": classification,
        }
        state.dev_gate_records.append(record)

        if run.all_passed:
            write_progress(f"  Dev gate PASSED — story {state.current_story}")
            logger.info("Dev gate passed for story %s", state.current_story)
            return PhaseResult.ok({"dev_gate_action": "pass", "dev_gate_passed": True})

        write_progress(
            f"  Dev gate FAILED — story {state.current_story} (failed: {', '.join(failed)})"
        )
        logger.warning(
            "Dev gate failed for story %s: %s [%s]",
            state.current_story,
            ", ".join(failed),
            record["classification"],
        )
        return PhaseResult.ok({"dev_gate_action": "fail", "dev_gate_passed": False})
