"""Tests for bmad_assist_lite.core.quality_gates."""

from bmad_assist_lite.core.quality_gates import (
    parse_quality_gates_table,
    update_quality_gate_status,
    update_task_checkboxes,
)

SAMPLE_STORY = """\
# Story 1.1

## Tasks
- [ ] Task 1: Implement feature
  - [ ] 1.1 Write code
- [ ] Task 2: Validate quality gates
  - [ ] 2.1 Run lint
  - [ ] 2.2 Run tests

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/` | **PENDING** |
| Typecheck | `mypy src/` | **PENDING** |
| Tests | `pytest -q` | **PENDING** |

## Notes
Some trailing content.
"""


class TestParseQualityGatesTable:
    """Tests for parse_quality_gates_table."""

    def test_parse_standard_table(self):
        """Parses a standard Quality Gates table."""
        entries = parse_quality_gates_table(SAMPLE_STORY)
        assert len(entries) == 3
        assert entries[0].name == "Lint"
        assert entries[0].command == "ruff check src/"
        assert entries[0].status == "PENDING"
        assert entries[1].name == "Typecheck"
        assert entries[2].name == "Tests"

    def test_parse_extra_whitespace(self):
        """Handles extra whitespace in table rows."""
        content = "|  Lint  |  `ruff check src/`  |  **PENDING**  |"
        entries = parse_quality_gates_table(content)
        assert len(entries) == 1
        assert entries[0].name == "Lint"
        assert entries[0].command == "ruff check src/"

    def test_no_table_returns_empty(self):
        """Returns empty list when no Quality Gates table exists."""
        content = "# Story 1.1\n\nJust some text.\n"
        entries = parse_quality_gates_table(content)
        assert entries == []

    def test_parse_pass_fail_statuses(self):
        """Parses PASS and FAIL statuses correctly."""
        content = (
            "| Lint | `ruff check` | **PASS** |\n"
            "| Tests | `pytest` | **FAIL** |\n"
        )
        entries = parse_quality_gates_table(content)
        assert len(entries) == 2
        assert entries[0].status == "PASS"
        assert entries[1].status == "FAIL"


class TestUpdateQualityGateStatus:
    """Tests for update_quality_gate_status."""

    def test_update_pending_to_pass(self, tmp_path):
        """Updates PENDING to PASS for a specific gate."""
        story_file = tmp_path / "story.md"
        story_file.write_text(SAMPLE_STORY, encoding="utf-8")

        update_quality_gate_status(story_file, "Lint", "PASS")

        content = story_file.read_text(encoding="utf-8")
        entries = parse_quality_gates_table(content)
        assert entries[0].status == "PASS"
        assert entries[1].status == "PENDING"  # unchanged
        assert entries[2].status == "PENDING"  # unchanged

    def test_update_pending_to_fail(self, tmp_path):
        """Updates PENDING to FAIL for a specific gate."""
        story_file = tmp_path / "story.md"
        story_file.write_text(SAMPLE_STORY, encoding="utf-8")

        update_quality_gate_status(story_file, "Tests", "FAIL")

        content = story_file.read_text(encoding="utf-8")
        entries = parse_quality_gates_table(content)
        assert entries[0].status == "PENDING"
        assert entries[2].status == "FAIL"

    def test_preserves_rest_of_content(self, tmp_path):
        """Updating a gate status preserves the rest of the file."""
        story_file = tmp_path / "story.md"
        story_file.write_text(SAMPLE_STORY, encoding="utf-8")

        update_quality_gate_status(story_file, "Lint", "PASS")

        content = story_file.read_text(encoding="utf-8")
        assert "# Story 1.1" in content
        assert "Some trailing content." in content
        assert "Task 1: Implement feature" in content


class TestUpdateTaskCheckboxes:
    """Tests for update_task_checkboxes."""

    def test_marks_quality_gate_checkboxes(self, tmp_path):
        """Marks quality gate task checkboxes as [x]."""
        story_file = tmp_path / "story.md"
        story_file.write_text(SAMPLE_STORY, encoding="utf-8")

        update_task_checkboxes(story_file)

        content = story_file.read_text(encoding="utf-8")
        assert "- [x] Task 2: Validate quality gates" in content
        assert "- [x] 2.1 Run lint" in content
        assert "- [x] 2.2 Run tests" in content
        # Non-quality-gate task unchanged
        assert "- [ ] Task 1: Implement feature" in content

    def test_no_checkboxes_to_update(self, tmp_path):
        """No changes when no quality gate checkboxes exist."""
        story_file = tmp_path / "story.md"
        content = "# Story\n\n- [ ] Task 1: Do stuff\n"
        story_file.write_text(content, encoding="utf-8")

        update_task_checkboxes(story_file)

        assert story_file.read_text(encoding="utf-8") == content
