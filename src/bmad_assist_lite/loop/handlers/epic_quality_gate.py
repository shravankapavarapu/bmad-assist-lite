"""EPIC_QUALITY_GATE phase handler — deterministic, non-LLM.

Runs full project test suite before retrospective. If any stories
failed QA during the sprint, reports them and exits.
"""

import logging
from pathlib import Path

from bmad_assist_lite.core.command_runner import CommandResult, clean_test_output, run_command
from bmad_assist_lite.core.config import Config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.core.toolchain import ToolchainCommands, detect_toolchain
from bmad_assist_lite.loop.types import PhaseResult
from bmad_assist_lite.providers.base import write_progress

logger = logging.getLogger(__name__)


class EpicQualityGateHandler:
    """Non-LLM handler: runs project-wide quality gate checks."""

    def __init__(self, config: Config, project_path: Path) -> None:
        """Initialize the handler with config and project path."""
        self.config = config
        self.project_path = project_path

    def _get_commands(self) -> ToolchainCommands:
        """Get commands from config or auto-detect."""
        qg = self.config.quality_gate
        if qg and any([qg.lint, qg.typecheck, qg.build, qg.test]):
            return ToolchainCommands(
                lint=qg.lint,
                typecheck=qg.typecheck,
                build=qg.build,
                test=qg.test,
            )
        return detect_toolchain(self.project_path)

    def _get_command_timeout(self) -> int:
        """Get per-command timeout."""
        if self.config.quality_gate:
            return self.config.quality_gate.command_timeout
        return 120

    def _write_report(
        self,
        state: State,
        results: list[tuple[str, CommandResult]],
    ) -> Path:
        """Write epic QA report to cache directory."""
        cache_dir = self.project_path / ".bmad-assist-lite" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        epic_num = state.current_epic or "unknown"
        report_path = cache_dir / f"epic-{epic_num}-qa-report.md"

        lines = [f"# Epic {epic_num} Quality Gate Report\n"]

        for name, result in results:
            status = "PASS" if result.success else "FAIL"
            lines.append(f"\n## {name}: {status}\n")
            lines.append(f"**Command:** `{result.command}`\n")
            lines.append(f"**Exit Code:** {result.exit_code}\n")
            if not result.success:
                raw_output = (result.stdout + "\n" + result.stderr).strip()
                output = clean_test_output(raw_output)
                lines.append(f"**Output:**\n```\n{output}\n```\n")

        if state.failed_qa_stories:
            lines.append("\n## Stories with Failed Quality Gates\n")
            for story_id in state.failed_qa_stories:
                lines.append(f"- {story_id}\n")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path

    def execute(self, state: State) -> PhaseResult:
        """Run project-wide quality checks."""
        tc = self._get_commands()
        timeout = self._get_command_timeout()

        command_list: list[tuple[str, str]] = []
        if tc.lint:
            command_list.append(("Lint", tc.lint))
        if tc.typecheck:
            command_list.append(("Typecheck", tc.typecheck))
        if tc.build:
            command_list.append(("Build", tc.build))
        if tc.test:
            command_list.append(("Tests", tc.test))

        results: list[tuple[str, CommandResult]] = []
        failures: list[str] = []

        for name, cmd in command_list:
            write_progress(f"    Running: {cmd}")
            cmd_result = run_command(cmd, self.project_path, timeout=timeout)
            results.append((name, cmd_result))

            icon = "\u2714" if cmd_result.success else "\u2718"
            status = "PASS" if cmd_result.success else "FAIL"
            write_progress(f"    {icon} {name}: {status}")

            if not cmd_result.success:
                failures.append(name)

        # Write report
        report_path = self._write_report(state, results)

        # Check for failed QA stories
        if state.failed_qa_stories:
            failed_list = ", ".join(state.failed_qa_stories)
            msg = (
                f"Stories failed QA: [{failed_list}]. "
                f"Report: {report_path}. "
                f"Fix manually and --resume."
            )
            write_progress(f"\n  {msg}")
            return PhaseResult.fail(msg)

        if failures:
            failed_gates = ", ".join(failures)
            msg = (
                f"Epic quality gates failed: [{failed_gates}]. "
                f"Report: {report_path}. "
                f"Fix manually and --resume."
            )
            write_progress(f"\n  {msg}")
            return PhaseResult.fail(msg)

        return PhaseResult.ok()
