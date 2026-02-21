"""Parse and update Quality Gates table in story markdown files."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class QualityGateEntry:
    """A single row from the Quality Gates markdown table."""

    name: str
    command: str
    status: str  # PENDING, PASS, FAIL


# Matches: | Gate Name | `command` | **STATUS** |
_TABLE_ROW_RE = re.compile(
    r"^\|\s*(.+?)\s*\|\s*`(.+?)`\s*\|\s*\*\*(PENDING|PASS|FAIL).*?\*\*\s*\|",
    re.MULTILINE,
)


def parse_quality_gates_table(content: str) -> list[QualityGateEntry]:
    """Parse ## Quality Gates markdown table from story content.

    Expected format:
    | Gate | Command | Status |
    |------|---------|--------|
    | Lint | `ruff check src/` | **PENDING** |
    """
    entries: list[QualityGateEntry] = []
    for match in _TABLE_ROW_RE.finditer(content):
        name = match.group(1).strip()
        command = match.group(2).strip()
        status = match.group(3).strip()
        entries.append(QualityGateEntry(name=name, command=command, status=status))
    return entries


def update_quality_gate_status(story_path: Path, gate_name: str, new_status: str) -> None:
    """Update a gate's status in the story file (PENDING -> PASS/FAIL).

    Performs a surgical in-place replacement of the status field for the
    matching gate row.
    """
    content = story_path.read_text(encoding="utf-8")

    def _replace_status(match: re.Match[str]) -> str:
        row_name = match.group(1).strip()
        if row_name == gate_name:
            command = match.group(2)
            return f"| {row_name} | `{command}` | **{new_status}** |"
        return str(match.group(0))

    updated = _TABLE_ROW_RE.sub(_replace_status, content)
    if updated != content:
        story_path.write_text(updated, encoding="utf-8")
        logger.debug("Updated quality gate '%s' to %s in %s", gate_name, new_status, story_path)


def update_task_checkboxes(story_path: Path) -> None:
    """Mark quality gate task checkboxes as [x] when all gates pass.

    Finds lines like:
    - [ ] Task N: Validate quality gates
    - [ ] N.M Run lint/typecheck/build/test
    """
    content = story_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    changed = False

    # Patterns for quality gate task checkboxes
    task_re = re.compile(
        r"^(\s*-\s)\[ \](\s+Task \d+.*(?:quality.?gate|Quality.?Gate|lint|typecheck|build|test).*)"
    )
    subtask_re = re.compile(
        r"^(\s*-\s)\[ \](\s+\d+\.\d+[:\s].*(?:lint|typecheck|type.check|build|test).*)",
        re.IGNORECASE,
    )

    new_lines: list[str] = []
    for line in lines:
        m = task_re.match(line) or subtask_re.match(line)
        if m:
            line = f"{m.group(1)}[x]{m.group(2)}"
            if not line.endswith("\n") and content.endswith("\n"):
                line += "\n"
            changed = True
        new_lines.append(line)

    if changed:
        story_path.write_text("".join(new_lines), encoding="utf-8")
        logger.debug("Updated task checkboxes in %s", story_path)
