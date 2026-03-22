"""QUALITY_GATE phase handler — deterministic, non-LLM.

Runs lint/typecheck/build/test commands and updates the story file
Quality Gates table with PASS/FAIL status.
"""

import logging
import re
from pathlib import Path

from bmad_assist_lite.core.command_runner import CommandResult, clean_test_output, run_command
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
from bmad_assist_lite.providers.base import write_progress

logger = logging.getLogger(__name__)

_SEPARATOR_RE = re.compile(r"^_{3,}\s+(.+?)\s+_{3,}$")


def _deduplicate_test_output(output: str) -> str:
    """Deduplicate identical pytest failures for concise reporting.

    Groups failures by error signature (the E-prefixed lines), shows one
    sample stack trace per unique root cause plus a list of affected tests.
    Returns the original output unchanged if there is nothing to deduplicate.
    """
    if "FAILURES" not in output:
        return output

    lines = output.split("\n")

    # Find FAILURES and short-test-summary boundaries
    failures_idx: int | None = None
    summary_idx: int | None = None
    for i, line in enumerate(lines):
        if failures_idx is None and line.strip().strip("=").strip() == "FAILURES":
            failures_idx = i
        if "short test summary info" in line:
            summary_idx = i

    if failures_idx is None:
        return output

    end_idx = summary_idx if summary_idx is not None else len(lines)

    # Parse individual failure blocks separated by ___ TestName ___
    blocks: list[tuple[str, str]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    for line in lines[failures_idx + 1 : end_idx]:
        m = _SEPARATOR_RE.match(line.strip())
        if m:
            if current_name is not None:
                blocks.append((current_name, "\n".join(current_lines)))
            current_name = m.group(1)
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        blocks.append((current_name, "\n".join(current_lines)))

    if len(blocks) <= 1:
        return output

    # Group by error signature (lines starting with "E ")
    groups: dict[str, list[str]] = {}
    first_trace: dict[str, tuple[str, str]] = {}

    for test_name, trace in blocks:
        error_lines = [
            ln.strip() for ln in trace.split("\n") if ln.strip().startswith("E ")
        ]
        sig = "\n".join(error_lines) if error_lines else trace[-200:]
        if sig not in groups:
            groups[sig] = []
            first_trace[sig] = (test_name, trace)
        groups[sig].append(test_name)

    # If every failure is already unique, no dedup benefit
    if len(groups) == len(blocks):
        return output

    # Build deduplicated output
    total = len(blocks)
    unique = len(groups)

    parts: list[str] = []
    parts.extend(lines[: failures_idx + 1])
    parts.append("")
    parts.append(f"[{total} total failures, {unique} unique root cause(s)]")
    parts.append("")

    for i, (sig, test_names) in enumerate(groups.items(), 1):
        sample_name, sample_trace = first_trace[sig]
        count = len(test_names)

        parts.append(
            f"--- Root Cause {i} ({count} occurrence{'s' if count > 1 else ''}) ---"
        )
        parts.append(f"Sample test: {sample_name}")
        parts.append(sample_trace.strip())

        if count > 1:
            parts.append("")
            parts.append(f"All affected tests ({count}):")
            for name in test_names:
                parts.append(f"  - {name}")
        parts.append("")

    # Keep post-failures section (summary + final stats line)
    if summary_idx is not None:
        parts.extend(lines[summary_idx:])

    return "\n".join(parts)


class QualityGateHandler:
    """Non-LLM handler: runs quality gate commands and reports results."""

    def __init__(self, config: Config, project_path: Path) -> None:
        """Initialize the handler with config and project path."""
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
            test_cmd = qg.test_unit or qg.test
            if test_cmd:
                entries.append(QualityGateEntry(name="Tests", command=test_cmd, status="PENDING"))
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
        test_cmd = tc.test_unit or tc.test
        if test_cmd:
            entries.append(QualityGateEntry(name="Tests", command=test_cmd, status="PENDING"))
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
            raw_output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
            output = clean_test_output(raw_output)
            output = _deduplicate_test_output(output)
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
            write_progress(f"    Running: {entry.command}")
            cmd_result = run_command(entry.command, self.project_path, timeout=timeout)
            results.append((entry, cmd_result))

            status = "PASS" if cmd_result.success else "FAIL"
            icon = "\u2714" if cmd_result.success else "\u2718"
            write_progress(f"    {icon} {entry.name}: {status}")

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

        max_retries = self.config.quality_gate.max_retries if self.config.quality_gate else 2
        if state.qa_retry_count < max_retries:
            # Still have retries left — try LLM fix
            return PhaseResult(
                success=True,
                next_phase=Phase.FIX_QUALITY_GATE,
                outputs={"quality_gate_action": "fix"},
            )

        # All retries exhausted — skip story
        return PhaseResult.ok({"quality_gate_action": "skip_story"})
