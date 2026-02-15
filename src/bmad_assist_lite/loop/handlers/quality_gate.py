"""QUALITY_GATE phase handler — deterministic, non-LLM.

Runs lint/typecheck/build/test commands and updates the story file
Quality Gates table with PASS/FAIL status.
"""

import logging
from pathlib import Path

from bmad_assist_lite.core.command_runner import CommandResult, run_command
from bmad_assist_lite.core.config import Config
from bmad_assist_lite.core.paths import get_paths
from bmad_assist_lite.core.quality_gates import (
    QualityGateEntry,
    parse_quality_gates_table,
    update_quality_gate_status,
    update_task_checkboxes,
)
from bmad_assist_lite.core.state import Phase, State
from bmad_assist_lite.core.toolchain import detect_toolchain
from bmad_assist_lite.loop.types import PhaseResult

logger = logging.getLogger(__name__)


class QualityGateHandler:
    """Non-LLM handler: runs quality gate commands and reports results."""

    def __init__(self, config: Config, project_path: Path) -> None:
        self.config = config
        self.project_path = project_path

    def _resolve_story_path(self, state: State) -> Path | None:
        """Resolve story file path from epic/story numbers."""
        paths = get_paths()
        story_id = state.current_story
        if not story_id:
            return None

        parts = story_id.split(".")
        if len(parts) != 2:
            return None

        epic_num, story_num = parts
        story_file = paths.stories_dir / f"story-{epic_num}.{story_num}.md"
        if story_file.exists():
            return story_file
        return None

    def _get_commands(self, state: State) -> list[QualityGateEntry]:
        """Get quality gate commands from story file, config, or auto-detect."""
        # 1. Try story file Quality Gates table
        story_path = self._resolve_story_path(state)
        if story_path and story_path.exists():
            content = story_path.read_text(encoding="utf-8")
            entries = parse_quality_gates_table(content)
            if entries:
                return entries

        # 2. Try config quality_gate commands
        qg = self.config.quality_gate
        if qg:
            entries = []
            if qg.lint:
                entries.append(QualityGateEntry(name="Lint", command=qg.lint, status="PENDING"))
            if qg.typecheck:
                entries.append(
                    QualityGateEntry(name="Typecheck", command=qg.typecheck, status="PENDING")
                )
            if qg.build:
                entries.append(QualityGateEntry(name="Build", command=qg.build, status="PENDING"))
            if qg.test:
                entries.append(QualityGateEntry(name="Tests", command=qg.test, status="PENDING"))
            if entries:
                return entries

        # 3. Auto-detect from project root
        tc = detect_toolchain(self.project_path)
        entries = []
        if tc.lint:
            entries.append(QualityGateEntry(name="Lint", command=tc.lint, status="PENDING"))
        if tc.typecheck:
            entries.append(
                QualityGateEntry(name="Typecheck", command=tc.typecheck, status="PENDING")
            )
        if tc.build:
            entries.append(QualityGateEntry(name="Build", command=tc.build, status="PENDING"))
        if tc.test:
            entries.append(QualityGateEntry(name="Tests", command=tc.test, status="PENDING"))
        return entries

    def _get_command_timeout(self) -> int:
        """Get per-command timeout."""
        if self.config.quality_gate:
            return self.config.quality_gate.command_timeout
        return 120

    def _write_failure_report(
        self, state: State, failures: list[tuple[QualityGateEntry, CommandResult]]
    ) -> Path:
        """Write failure report to cache directory."""
        cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        story_id = state.current_story or "unknown"
        report_path = cache_dir / f"qa-failures-{story_id}.md"

        lines = [f"# Quality Gate Failures — Story {story_id}\n"]
        for entry, result in failures:
            lines.append(f"\n## Failed: {entry.name}\n")
            lines.append(f"**Command:** `{result.command}`\n")
            lines.append(f"**Exit Code:** {result.exit_code}\n")
            output = (result.stdout + "\n" + result.stderr).strip()
            lines.append(f"**Output:**\n```\n{output}\n```\n")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Wrote QA failure report to %s", report_path)
        return report_path

    def execute(self, state: State) -> PhaseResult:
        """Run quality gate commands and return action-based result."""
        commands = self._get_commands(state)
        if not commands:
            logger.info("No quality gate commands found — passing by default")
            return PhaseResult.ok({"quality_gate_action": "pass"})

        timeout = self._get_command_timeout()
        story_path = self._resolve_story_path(state)
        results: list[tuple[QualityGateEntry, CommandResult]] = []
        failures: list[tuple[QualityGateEntry, CommandResult]] = []

        for entry in commands:
            print(f"    Running: {entry.command}", flush=True)
            cmd_result = run_command(entry.command, self.project_path, timeout=timeout)
            results.append((entry, cmd_result))

            status = "PASS" if cmd_result.success else "FAIL"
            icon = "\u2714" if cmd_result.success else "\u2718"
            print(f"    {icon} {entry.name}: {status}", flush=True)

            if story_path:
                update_quality_gate_status(story_path, entry.name, status)

            if not cmd_result.success:
                failures.append((entry, cmd_result))

        all_passed = len(failures) == 0

        if all_passed:
            if story_path:
                update_task_checkboxes(story_path)
            return PhaseResult.ok({"quality_gate_action": "pass"})

        # Write failure report
        self._write_failure_report(state, failures)

        if state.qa_retry_count == 0:
            # First failure — try LLM fix
            return PhaseResult(
                success=True,
                next_phase=Phase.FIX_QUALITY_GATE,
                outputs={"quality_gate_action": "fix"},
            )

        # Retry already attempted — skip story
        return PhaseResult.ok({"quality_gate_action": "skip_story"})
