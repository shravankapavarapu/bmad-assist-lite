"""Tests for the BMAD markdown parser.

Covers: frontmatter parsing, BmadDocument creation, epic/story extraction,
        epic number inference from filename.
"""

from pathlib import Path

import pytest

from bmad_assist_lite.bmad.parser import (
    BmadDocument,
    EpicDocument,
    EpicStory,
    _parse_frontmatter,
    parse_bmad_file,
    parse_epic_file,
)


# ---------------------------------------------------------------------------
# _parse_frontmatter
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    """Tests for YAML frontmatter extraction."""

    def test_parse_frontmatter_basic(self):
        """Parse text with valid YAML frontmatter."""
        text = "---\ntitle: Test\nversion: 2\n---\ncontent body here"
        fm, content = _parse_frontmatter(text)

        assert fm == {"title": "Test", "version": 2}
        assert content == "content body here"

    def test_parse_frontmatter_none(self):
        """Text without --- returns empty frontmatter and full text as content."""
        text = "Just some plain text\nwith multiple lines"
        fm, content = _parse_frontmatter(text)

        assert fm == {}
        assert content == text

    def test_parse_frontmatter_empty_block(self):
        """Frontmatter block with no keys returns empty dict."""
        text = "---\n---\nremaining content"
        fm, content = _parse_frontmatter(text)

        assert fm == {}
        assert content == "remaining content"

    def test_parse_frontmatter_no_closing(self):
        """Missing closing --- treats everything as plain text."""
        text = "---\ntitle: Test\nno closing delimiter"
        fm, content = _parse_frontmatter(text)

        assert fm == {}
        assert content == text


# ---------------------------------------------------------------------------
# parse_bmad_file
# ---------------------------------------------------------------------------


class TestParseBmadFile:
    """Tests for full BMAD file parsing from disk."""

    def test_parse_bmad_file(self, tmp_path: Path):
        """Parse a file with frontmatter + content."""
        md = tmp_path / "doc.md"
        md.write_text(
            "---\ntitle: My Doc\nauthor: BMAD\n---\n# Heading\n\nBody text here.",
            encoding="utf-8",
        )

        doc = parse_bmad_file(md)

        assert isinstance(doc, BmadDocument)
        assert doc.path == md
        assert doc.frontmatter == {"title": "My Doc", "author": "BMAD"}
        assert "# Heading" in doc.content
        assert "Body text here." in doc.content

    def test_parse_bmad_file_no_frontmatter(self, tmp_path: Path):
        """File without frontmatter has empty frontmatter dict."""
        md = tmp_path / "plain.md"
        md.write_text("# Just a heading\n\nSome content.", encoding="utf-8")

        doc = parse_bmad_file(md)

        assert doc.frontmatter == {}
        assert "# Just a heading" in doc.content


# ---------------------------------------------------------------------------
# parse_epic_file — with stories
# ---------------------------------------------------------------------------


class TestParseEpicFile:
    """Tests for epic file parsing and story extraction."""

    EPIC_CONTENT = """\
# Epic 1: Test Epic

## Story 1.1: First Story

**Status:** Draft
**Priority:** High

- [ ] AC 1
- [ ] AC 2

## Story 1.2: Second Story

**Status:** In Progress

- [ ] AC 3
"""

    def test_parse_epic_file_with_stories(self, tmp_path: Path):
        """Parse epic file containing story headers and metadata."""
        epic_file = tmp_path / "epic-1.md"
        epic_file.write_text(self.EPIC_CONTENT, encoding="utf-8")

        doc = parse_epic_file(epic_file, epic_number=1)

        assert isinstance(doc, EpicDocument)
        assert doc.epic_number == 1
        assert doc.title == "Epic 1: Test Epic"
        assert len(doc.stories) == 2

        # First story
        s1 = doc.stories[0]
        assert s1.number == "1.1"
        assert s1.title == "First Story"
        assert s1.ac_count == 2
        assert s1.status == "Draft"
        assert s1.priority == "High"

        # Second story
        s2 = doc.stories[1]
        assert s2.number == "1.2"
        assert s2.title == "Second Story"
        assert s2.ac_count == 1
        assert s2.status == "In Progress"

    def test_parse_epic_file_no_stories(self, tmp_path: Path):
        """File with no ## Story headers returns empty stories list."""
        epic_file = tmp_path / "epic-2.md"
        epic_file.write_text(
            "# Epic 2: Empty Epic\n\nJust description, no stories.",
            encoding="utf-8",
        )

        doc = parse_epic_file(epic_file, epic_number=2)

        assert doc.epic_number == 2
        assert doc.stories == []

    def test_epic_number_from_filename(self, tmp_path: Path):
        """Epic number is extracted from filename when not provided explicitly."""
        epic_file = tmp_path / "epic-3.md"
        epic_file.write_text("# Epic 3: Inferred Number\n", encoding="utf-8")

        doc = parse_epic_file(epic_file)  # No epic_number argument

        assert doc.epic_number == 3

    def test_epic_number_fallback_zero(self, tmp_path: Path):
        """Filename without digits yields epic_number=0."""
        epic_file = tmp_path / "overview.md"
        epic_file.write_text("# Overview\n", encoding="utf-8")

        doc = parse_epic_file(epic_file)

        assert doc.epic_number == 0

    def test_story_code_field(self, tmp_path: Path):
        """Parsed stories have the correct code field (STORY-X.Y)."""
        epic_file = tmp_path / "epic-1.md"
        epic_file.write_text(
            "# Epic 1\n\n## Story 1.1: Code Check\n\n- [ ] AC\n",
            encoding="utf-8",
        )

        doc = parse_epic_file(epic_file, epic_number=1)

        assert len(doc.stories) == 1
        assert doc.stories[0].code == "STORY-1.1"
