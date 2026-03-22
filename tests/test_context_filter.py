"""Tests for compiler.context_filter — epic-driven context filtering."""

import logging
from pathlib import Path

import pytest

from bmad_assist_lite.compiler.context_filter import (
    _build_filename_to_key_map,
    _extract_section_from_content,
    apply_context_filter,
    parse_context_requirements,
)
from bmad_assist_lite.compiler.types import CompilerContext
from bmad_assist_lite.core.exceptions import CompilerError

# ---------------------------------------------------------------------------
# parse_context_requirements
# ---------------------------------------------------------------------------


class TestParseContextRequirements:
    """Tests for parsing Context Requirements tables from epic markdown."""

    EPIC_WITH_TABLE = """\
# Epic 1: My Epic

Some intro text.

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| architecture.md | Tech Stack; Analytics Integration | Core tech |
| prd.md | Executive Summary; Functional Requirements | Reqs only |
| ux-design-specification.md | (skip) | Not needed |
| project-context.md | (full) | Always load |
"""

    def test_parses_basic_table(self):
        result = parse_context_requirements(self.EPIC_WITH_TABLE)
        assert result is not None
        assert len(result) == 4

    def test_sections_directive(self):
        result = parse_context_requirements(self.EPIC_WITH_TABLE)
        assert result is not None
        arch = result[0]
        assert arch.document == "architecture.md"
        assert arch.directive == "sections"
        assert arch.sections == ["Tech Stack", "Analytics Integration"]

    def test_skip_directive(self):
        result = parse_context_requirements(self.EPIC_WITH_TABLE)
        assert result is not None
        ux = result[2]
        assert ux.document == "ux-design-specification.md"
        assert ux.directive == "skip"
        assert ux.sections == []

    def test_full_directive(self):
        result = parse_context_requirements(self.EPIC_WITH_TABLE)
        assert result is not None
        ctx = result[3]
        assert ctx.document == "project-context.md"
        assert ctx.directive == "full"
        assert ctx.sections == []

    def test_returns_none_when_no_table(self):
        epic = "# Epic\n\nNo context requirements here.\n"
        assert parse_context_requirements(epic) is None

    def test_returns_none_for_empty_string(self):
        assert parse_context_requirements("") is None

    def test_case_insensitive_heading(self):
        epic = """\
## context requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| foo.md | (full) | All |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert len(result) == 1

    def test_h2_and_h4_headings(self):
        for hashes in ("##", "####"):
            epic = f"""\
{hashes} Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| bar.md | Section A | Reason |
"""
            result = parse_context_requirements(epic)
            assert result is not None, f"Failed for heading level {hashes}"

    def test_empty_sections_treated_as_full(self):
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| notes.md |  | Load everything |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert result[0].directive == "full"

    def test_backtick_wrapped_filenames_stripped(self):
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| `architecture.md` | Tech Stack | Core |
| `prd.md` | (skip) | Not needed |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert result[0].document == "architecture.md"
        assert result[1].document == "prd.md"

    def test_backtick_wrapped_directives_stripped(self):
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| ux.md | `(skip)` | Not needed |
| ctx.md | `(full)` | Always |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert result[0].directive == "skip"
        assert result[1].directive == "full"

    def test_backtick_wrapped_section_names_stripped(self):
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | `Tech Stack`; `Deployment` | Core |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert result[0].sections == ["Tech Stack", "Deployment"]

    def test_missing_required_columns_returns_none(self):
        epic = """\
### Context Requirements

| File | Notes |
|------|-------|
| a.md | stuff |
"""
        result = parse_context_requirements(epic)
        assert result is None

    def test_skip_directive_case_insensitive(self):
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| a.md | (SKIP) | Not needed |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert result[0].directive == "skip"

    def test_table_with_only_separator_returns_none(self):
        epic = """\
### Context Requirements

| Document | Sections |
|----------|----------|
"""
        assert parse_context_requirements(epic) is None

    def test_multiple_sections_parsing(self):
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| big.md | A; B; C; D | Many sections |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert result[0].sections == ["A", "B", "C", "D"]


# ---------------------------------------------------------------------------
# _extract_section_from_content
# ---------------------------------------------------------------------------


class TestExtractSectionFromContent:
    """Tests for in-memory markdown section extraction."""

    SAMPLE_MD = """\
# Top Level

Intro text.

## Tech Stack

We use Python and TypeScript.

### Sub Section

Details here.

## Analytics Integration

Analytics stuff.

## Another Section

More content.
"""

    def test_extracts_section_with_content(self):
        result = _extract_section_from_content(self.SAMPLE_MD, "Tech Stack")
        assert result is not None
        assert "## Tech Stack" in result
        assert "We use Python and TypeScript." in result
        assert "### Sub Section" in result

    def test_section_ends_at_same_level_heading(self):
        result = _extract_section_from_content(self.SAMPLE_MD, "Tech Stack")
        assert result is not None
        assert "Analytics Integration" not in result

    def test_extracts_last_section(self):
        result = _extract_section_from_content(self.SAMPLE_MD, "Another Section")
        assert result is not None
        assert "More content." in result

    def test_returns_none_for_missing_section(self):
        result = _extract_section_from_content(self.SAMPLE_MD, "Nonexistent")
        assert result is None

    def test_normalization_dash_underscore(self):
        content = "# Doc\n\n## Tech-Stack\n\nContent here.\n"
        result = _extract_section_from_content(content, "Tech Stack")
        assert result is not None
        assert "Content here." in result

    def test_normalization_dot(self):
        content = "# Doc\n\n## Tech.Stack\n\nContent here.\n"
        result = _extract_section_from_content(content, "Tech Stack")
        assert result is not None

    def test_case_insensitive_match(self):
        result = _extract_section_from_content(self.SAMPLE_MD, "tech stack")
        assert result is not None

    def test_section_with_parentheses(self):
        content = """\
# Doc

## Third-Party Integration (Cal.com)

Cal.com integration details.

## Other Section

Other content.
"""
        result = _extract_section_from_content(
            content, "Third-Party Integration (Cal.com)"
        )
        assert result is not None
        assert "Cal.com integration details." in result
        assert "Other content." not in result

    def test_empty_content(self):
        assert _extract_section_from_content("", "Anything") is None

    def test_extracts_subsection(self):
        result = _extract_section_from_content(self.SAMPLE_MD, "Sub Section")
        assert result is not None
        assert "Details here." in result
        # Should end before the next h2
        assert "Analytics" not in result


# ---------------------------------------------------------------------------
# _build_filename_to_key_map
# ---------------------------------------------------------------------------


class TestBuildFilenameToKeyMap:
    """Tests for filename-to-key mapping."""

    def test_maps_path_filenames(self):
        discovered = {
            "architecture_file": [Path("planning/architecture.md")],
            "prd_file": [Path("planning/prd.md")],
        }
        result = _build_filename_to_key_map(discovered)
        assert result["architecture.md"] == "architecture_file"
        assert result["prd.md"] == "prd_file"

    def test_lowercases_filenames(self):
        discovered = {"arch": [Path("planning/Architecture.MD")]}
        result = _build_filename_to_key_map(discovered)
        assert "architecture.md" in result

    def test_empty_discovered_files(self):
        assert _build_filename_to_key_map({}) == {}

    def test_multiple_files_per_key(self):
        discovered = {"docs": [Path("a/foo.md"), Path("b/bar.md")]}
        result = _build_filename_to_key_map(discovered)
        assert result["foo.md"] == "docs"
        assert result["bar.md"] == "docs"


# ---------------------------------------------------------------------------
# apply_context_filter (integration)
# ---------------------------------------------------------------------------


class TestApplyContextFilter:
    """Integration tests for apply_context_filter."""

    EPIC_CONTENT = """\
# Epic 1

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| architecture.md | Tech Stack | Core |
| prd.md | (skip) | Not needed |
| project-context.md | (full) | Always |
"""

    ARCHITECTURE_CONTENT = """\
# Architecture

## Overview

Overview text.

## Tech Stack

Python, TypeScript, React.

## Deployment

Docker stuff.
"""

    PRD_CONTENT = "# PRD\n\nLots of PRD content.\n"
    PROJECT_CONTEXT_CONTENT = "# Project Context\n\nContext info.\n"

    def _make_context(self, tmp_path: Path) -> CompilerContext:
        ctx = CompilerContext(
            project_root=tmp_path,
            output_folder=tmp_path / "_output",
        )
        ctx.discovered_files = {
            "epic_file": [tmp_path / "planning" / "epic-1.md"],
            "architecture_file": [tmp_path / "planning" / "architecture.md"],
            "prd_file": [tmp_path / "planning" / "prd.md"],
            "project_context_file": [tmp_path / "planning" / "project-context.md"],
        }
        ctx.file_contents = {
            "epic_file": self.EPIC_CONTENT,
            "architecture_file": self.ARCHITECTURE_CONTENT,
            "prd_file": self.PRD_CONTENT,
            "project_context_file": self.PROJECT_CONTEXT_CONTENT,
        }
        return ctx

    def test_sections_directive_filters_content(self, tmp_path: Path):
        ctx = self._make_context(tmp_path)
        apply_context_filter(ctx)

        arch = ctx.file_contents["architecture_file"]
        assert "Tech Stack" in arch
        assert "Python, TypeScript, React." in arch
        assert "Overview text." not in arch
        assert "Docker stuff." not in arch

    def test_skip_directive_empties_content(self, tmp_path: Path):
        ctx = self._make_context(tmp_path)
        apply_context_filter(ctx)

        assert ctx.file_contents["prd_file"] == ""

    def test_full_directive_preserves_content(self, tmp_path: Path):
        ctx = self._make_context(tmp_path)
        apply_context_filter(ctx)

        assert ctx.file_contents["project_context_file"] == self.PROJECT_CONTEXT_CONTENT

    def test_noop_when_no_epic(self, tmp_path: Path):
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"some_file": "content"}
        apply_context_filter(ctx)
        assert ctx.file_contents["some_file"] == "content"

    def test_noop_when_no_table(self, tmp_path: Path):
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": "# Epic\n\nNo table here.\n", "other": "data"}
        apply_context_filter(ctx)
        assert ctx.file_contents["other"] == "data"

    def test_skip_directive_missing_document_no_error(self, tmp_path: Path):
        """Skip directive on a missing document is a silent pass-through (no error)."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| nonexistent.md | (skip) | N/A |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": epic}
        ctx.discovered_files = {}

        # Should NOT raise — skip on missing doc is already satisfied
        apply_context_filter(ctx)

    def test_document_not_in_table_unchanged(self, tmp_path: Path):
        """Files not mentioned in the table keep their full content."""
        ctx = self._make_context(tmp_path)
        ctx.file_contents["extra_file"] = "extra content"
        apply_context_filter(ctx)
        assert ctx.file_contents["extra_file"] == "extra content"

    def test_epic_content_itself_unchanged(self, tmp_path: Path):
        ctx = self._make_context(tmp_path)
        apply_context_filter(ctx)
        assert ctx.file_contents["epic_file"] == self.EPIC_CONTENT

    def test_multiple_sections_concatenated(self, tmp_path: Path):
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| doc.md | Section A; Section B | Both |
"""
        doc = """\
# Doc

## Section A

Content A.

## Section B

Content B.

## Section C

Content C.
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"doc_file": [tmp_path / "doc.md"]}
        ctx.file_contents = {"epic_file": epic, "doc_file": doc}

        apply_context_filter(ctx)

        result = ctx.file_contents["doc_file"]
        assert "Content A." in result
        assert "Content B." in result
        assert "Content C." not in result

    def test_real_epic_table_format(self, tmp_path: Path):
        """Test with exact table format from a real epic file."""
        epic = """\
# Epic 2: Booking & Conversion

### Context Requirements

<!-- Sections from planning docs needed for story creation in this epic.
     Uses exact H2/H3 header text from each document, semicolon-separated. -->

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Starter Template Evaluation; Third-Party Integration (Cal.com); Analytics Integration |
| `prd.md` | Executive Summary; Functional Requirements |
| `ux-design-specification.md` | `(skip)` |
| `project-context.md` | `(full)` |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert len(result) == 4

        # architecture.md — sections directive, backticks stripped
        assert result[0].document == "architecture.md"
        assert result[0].directive == "sections"
        assert result[0].sections == [
            "Starter Template Evaluation",
            "Third-Party Integration (Cal.com)",
            "Analytics Integration",
        ]

        # prd.md — sections directive
        assert result[1].document == "prd.md"
        assert result[1].directive == "sections"
        assert result[1].sections == ["Executive Summary", "Functional Requirements"]

        # ux-design-specification.md — skip directive, backticks stripped
        assert result[2].document == "ux-design-specification.md"
        assert result[2].directive == "skip"

        # project-context.md — full directive, backticks stripped
        assert result[3].document == "project-context.md"
        assert result[3].directive == "full"

    def test_real_epic_end_to_end(self, tmp_path: Path):
        """End-to-end test with real epic table and architecture content."""
        epic = """\
# Epic 2

### Context Requirements

| Document | Sections to Load |
|----------|-----------------|
| `architecture.md` | Third-Party Integration (Cal.com); Analytics Integration |
| `prd.md` | Executive Summary |
| `ux-design-specification.md` | `(skip)` |
| `project-context.md` | `(full)` |
"""
        architecture = """\
# Architecture

## Starter Template Evaluation

Starter stuff.

## Third-Party Integration (Cal.com)

Cal.com embed approach using vanilla JS.

## Analytics Integration

Google Analytics setup with consent.

## Performance Optimization

Core Web Vitals budget.

## Other Section

Unrelated content.
"""
        prd = """\
# PRD

## Executive Summary

MVP landing page for AI agency.

## Functional Requirements

FR10: Multiple CTA placements.
FR11: Embedded calendar widget.

## Non-Functional Requirements

NFR stuff not needed.
"""
        ux = "# UX Design\n\nFull UX document with lots of content.\n"
        project_ctx = "# Project Context\n\nAlways needed.\n"

        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {
            "epic_file": [tmp_path / "epic-2.md"],
            "architecture_file": [tmp_path / "architecture.md"],
            "prd_file": [tmp_path / "prd.md"],
            "ux_file": [tmp_path / "ux-design-specification.md"],
            "project_context_file": [tmp_path / "project-context.md"],
        }
        ctx.file_contents = {
            "epic_file": epic,
            "architecture_file": architecture,
            "prd_file": prd,
            "ux_file": ux,
            "project_context_file": project_ctx,
        }

        apply_context_filter(ctx)

        # architecture — only requested sections
        arch = ctx.file_contents["architecture_file"]
        assert "Cal.com embed approach" in arch
        assert "Google Analytics setup" in arch
        assert "Starter stuff." not in arch
        assert "Core Web Vitals budget." not in arch
        assert "Unrelated content." not in arch

        # prd — only Executive Summary
        prd_result = ctx.file_contents["prd_file"]
        assert "MVP landing page" in prd_result
        assert "NFR stuff not needed." not in prd_result

        # ux — skipped
        assert ctx.file_contents["ux_file"] == ""

        # project-context — full
        assert ctx.file_contents["project_context_file"] == project_ctx

        # epic itself — unchanged
        assert ctx.file_contents["epic_file"] == epic


# ---------------------------------------------------------------------------
# Missing Context Requirements error behavior (Story 8.1)
# ---------------------------------------------------------------------------


class TestMissingContextRequirementsError:
    """Tests for CompilerError raised on missing context references."""

    def test_single_missing_document_raises(self, tmp_path: Path):
        """Single document not in discovered files triggers CompilerError."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| missing-doc.md | (full) | Needed |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": epic}
        ctx.discovered_files = {}

        with pytest.raises(CompilerError) as exc_info:
            apply_context_filter(ctx)

        msg = str(exc_info.value)
        assert "missing-doc.md" in msg
        assert "missing documents" in msg
        assert "Discovered files missing" in msg

    def test_single_missing_section_raises(self, tmp_path: Path):
        """Section not found in existing document raises CompilerError."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Crash Recovery | Needed |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        ctx.file_contents = {
            "epic_file": epic,
            "arch_file": "# Architecture\n\n## Overview\n\nSome content.\n",
        }

        with pytest.raises(CompilerError) as exc_info:
            apply_context_filter(ctx)

        msg = str(exc_info.value)
        assert "Crash Recovery" in msg
        assert "arch.md" in msg

    def test_multiple_missing_sections_across_documents(self, tmp_path: Path):
        """Multiple missing sections across multiple docs in one error."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Crash Recovery; Blocked Story Handling | Core |
| prd.md | Missing Feature | Needed |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {
            "arch_file": [tmp_path / "arch.md"],
            "prd_file": [tmp_path / "prd.md"],
        }
        ctx.file_contents = {
            "epic_file": epic,
            "arch_file": "# Arch\n\n## Overview\n\nContent.\n",
            "prd_file": "# PRD\n\n## Summary\n\nContent.\n",
        }

        with pytest.raises(CompilerError) as exc_info:
            apply_context_filter(ctx)

        msg = str(exc_info.value)
        assert "Crash Recovery" in msg
        assert "Blocked Story Handling" in msg
        assert "Missing Feature" in msg
        assert "arch.md" in msg
        assert "prd.md" in msg

    def test_missing_document_and_missing_section_combined(self, tmp_path: Path):
        """Missing doc + missing section in different doc combined in one error."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| ghost.md | (full) | Needed |
| arch.md | Nonexistent Section | Needed |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        ctx.file_contents = {
            "epic_file": epic,
            "arch_file": "# Arch\n\n## Real Section\n\nContent.\n",
        }

        with pytest.raises(CompilerError) as exc_info:
            apply_context_filter(ctx)

        msg = str(exc_info.value)
        assert "ghost.md" in msg
        assert "Nonexistent Section" in msg
        assert "Discovered files missing" in msg

    def test_all_references_resolve_no_error(self, tmp_path: Path):
        """All references resolve — no error, existing filter behavior."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Tech Stack | Core |
| prd.md | (full) | All |
| ux.md | (skip) | Not needed |
"""
        arch = "# Arch\n\n## Tech Stack\n\nPython.\n\n## Other\n\nStuff.\n"
        prd = "# PRD\n\nAll content.\n"
        ux = "# UX\n\nFull UX content.\n"

        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {
            "arch_file": [tmp_path / "arch.md"],
            "prd_file": [tmp_path / "prd.md"],
            "ux_file": [tmp_path / "ux.md"],
        }
        ctx.file_contents = {
            "epic_file": epic,
            "arch_file": arch,
            "prd_file": prd,
            "ux_file": ux,
        }

        # Should NOT raise
        apply_context_filter(ctx)

        # Verify filters applied correctly
        assert "Tech Stack" in ctx.file_contents["arch_file"]
        assert "Other" not in ctx.file_contents["arch_file"]
        assert ctx.file_contents["prd_file"] == prd
        assert ctx.file_contents["ux_file"] == ""

    def test_document_key_no_content_raises(self, tmp_path: Path):
        """Document key in discovered_files but not in file_contents raises."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | (full) | Needed |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        # Key exists in discovered_files but NOT in file_contents
        ctx.file_contents = {"epic_file": epic}

        with pytest.raises(CompilerError, match="arch.md"):
            apply_context_filter(ctx)

    def test_document_key_no_content_sections_directive_raises(self, tmp_path: Path):
        """Sections directive on discovered but unloaded document raises."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Tech Stack | Needed |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        # Key exists in discovered_files but NOT in file_contents
        ctx.file_contents = {"epic_file": epic}

        with pytest.raises(CompilerError, match="arch.md"):
            apply_context_filter(ctx)

    def test_error_message_structure(self, tmp_path: Path):
        """Verify error message contains grouped output and fix instructions."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Missing A; Missing B | Core |
| ghost.md | (full) | Needed |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        ctx.file_contents = {
            "epic_file": epic,
            "arch_file": "# Arch\n\n## Real Section\n\nContent.\n",
        }

        with pytest.raises(CompilerError) as exc_info:
            apply_context_filter(ctx)

        msg = str(exc_info.value)
        # Dynamic header for combined missing sections + docs
        assert "unresolved references" in msg
        # Sections grouped under document
        assert "arch.md:" in msg
        assert "Missing A" in msg
        assert "Missing B" in msg
        # Missing docs grouped separately
        assert "Discovered files missing:" in msg
        assert "ghost.md" in msg
        # Fix instructions present
        assert "Fix:" in msg
        assert "(optional)" in msg

    def test_mixed_scenario_resolved_refs_applied_before_error(self, tmp_path: Path):
        """Resolved refs are filtered before error is raised for missing ones."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Tech Stack | Core |
| ghost.md | (full) | Needed |
"""
        arch = "# Arch\n\n## Tech Stack\n\nPython.\n\n## Other\n\nStuff.\n"

        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        ctx.file_contents = {"epic_file": epic, "arch_file": arch}

        with pytest.raises(CompilerError, match="ghost.md"):
            apply_context_filter(ctx)

        # Even though error was raised, resolved ref should have been applied
        assert "Tech Stack" in ctx.file_contents["arch_file"]
        assert "Other" not in ctx.file_contents["arch_file"]

    def test_sections_directive_missing_document_raises(self, tmp_path: Path):
        """Sections directive on missing document raises CompilerError."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| ghost.md | Section A; Section B | Needed |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": epic}
        ctx.discovered_files = {}

        with pytest.raises(CompilerError, match="ghost.md"):
            apply_context_filter(ctx)


# ---------------------------------------------------------------------------
# Optional reference parsing (Story 8.2)
# ---------------------------------------------------------------------------


class TestParseOptionalReferences:
    """Tests for (optional) marker detection in parse_context_requirements."""

    def test_full_optional_directive(self):
        """Task 4.1: (full) (optional) → directive='full', optional=True."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | (full) (optional) | Nice to have |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert len(result) == 1
        req = result[0]
        assert req.directive == "full"
        assert req.optional is True
        assert req.sections == []

    def test_per_section_optional_indices(self):
        """Task 4.2: Per-section optional produces correct optional_sections."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Crash Recovery; State Persistence (optional); Blocked Story Handling | Core |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        req = result[0]
        assert req.directive == "sections"
        assert req.sections == [
            "Crash Recovery",
            "State Persistence",
            "Blocked Story Handling",
        ]
        # "State Persistence" is index 1
        assert req.optional_sections == frozenset({1})
        # Document-level optional is False (not all sections optional)
        assert req.optional is False

    def test_optional_text_stripped_before_section_matching(self):
        """Task 4.7: (optional) stripped so 'Foo (optional)' matches '## Foo'."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| doc.md | Foo (optional) | Nice |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        req = result[0]
        assert req.sections == ["Foo"]
        assert req.optional_sections == frozenset({0})

    def test_case_insensitive_optional_uppercase(self):
        """Task 4.8: (OPTIONAL) variant recognized."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | (full) (OPTIONAL) | Nice |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert result[0].optional is True
        assert result[0].directive == "full"

    def test_case_insensitive_optional_mixed_case(self):
        """Task 4.8: (Optional) variant recognized."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Section A (Optional) | Nice |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert result[0].optional_sections == frozenset({0})
        assert result[0].sections == ["Section A"]

    def test_skip_optional_directive(self):
        """Task 4.9: (skip) (optional) parsed correctly."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| ux.md | (skip) (optional) | Not needed |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        req = result[0]
        assert req.directive == "skip"
        assert req.optional is True

    def test_no_optional_marker_backward_compat(self):
        """Task 4.6: No (optional) → optional=False, empty optional_sections."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Tech Stack; Deployment | Core |
| prd.md | (full) | All |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        assert result[0].optional is False
        assert result[0].optional_sections == frozenset()
        assert result[1].optional is False
        assert result[1].optional_sections == frozenset()

    def test_backtick_wrapped_optional_section(self):
        """Task 2.4: Backtick-wrapped section with (optional) detected."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| doc.md | `Section A (optional)`; Section B | Partial |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        req = result[0]
        assert req.sections == ["Section A", "Section B"]
        assert req.optional_sections == frozenset({0})

    def test_multiple_optional_sections(self):
        """Multiple sections marked optional in one row."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| doc.md | A; B (optional); C; D (optional) | Mixed |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        req = result[0]
        assert req.sections == ["A", "B", "C", "D"]
        assert req.optional_sections == frozenset({1, 3})
        assert req.optional is False

    def test_all_sections_optional_sets_doc_optional(self):
        """When all sections in a list are optional, doc-level optional is True."""
        epic = """\
### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| doc.md | A (optional); B (optional) | All optional |
"""
        result = parse_context_requirements(epic)
        assert result is not None
        req = result[0]
        assert req.optional is True
        assert req.optional_sections == frozenset({0, 1})


# ---------------------------------------------------------------------------
# Optional reference behavior in apply_context_filter (Story 8.2)
# ---------------------------------------------------------------------------


class TestOptionalReferenceFiltering:
    """Integration tests for optional references in apply_context_filter."""

    def test_optional_missing_document_warning_no_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """Task 4.3: Optional missing document → WARNING, no error."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| missing-doc.md | (full) (optional) | Nice to have |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": epic}
        ctx.discovered_files = {}

        with caplog.at_level(logging.WARNING):
            apply_context_filter(ctx)  # Should NOT raise

        assert "missing-doc.md" in caplog.text
        assert "Optional document" in caplog.text

    def test_optional_missing_section_among_required(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """Task 4.4: Optional missing section → WARNING, required present → ok."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Crash Recovery; State Persistence (optional); Blocked Story Handling | Core |
"""
        arch_content = """\
# Architecture

## Crash Recovery

Recovery details.

## Blocked Story Handling

Blocked handling details.
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        ctx.file_contents = {"epic_file": epic, "arch_file": arch_content}

        with caplog.at_level(logging.WARNING):
            apply_context_filter(ctx)  # Should NOT raise

        # Optional missing section logged as warning
        assert "State Persistence" in caplog.text
        assert "Optional section" in caplog.text

        # Required sections extracted correctly
        result = ctx.file_contents["arch_file"]
        assert "Crash Recovery" in result
        assert "Blocked Story Handling" in result

    def test_optional_missing_section_required_missing_raises(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """Required section missing raises even with optional also missing."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Crash Recovery; State Persistence (optional); Blocked Story Handling | Core |
"""
        arch_content = """\
# Architecture

## Overview

Overview only, no matching sections.
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        ctx.file_contents = {"epic_file": epic, "arch_file": arch_content}

        with caplog.at_level(logging.WARNING):
            with pytest.raises(CompilerError) as exc_info:
                apply_context_filter(ctx)

        msg = str(exc_info.value)
        # Required sections raise errors
        assert "Crash Recovery" in msg
        assert "Blocked Story Handling" in msg
        # Optional section NOT in error message
        assert "State Persistence" not in msg
        # But optional section IS in warning log
        assert "State Persistence" in caplog.text

    def test_all_non_optional_present_optional_missing_no_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """Task 4.5: All non-optional present, optional missing → no error."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Tech Stack | Core |
| optional-doc.md | (full) (optional) | Nice to have |
"""
        arch_content = "# Arch\n\n## Tech Stack\n\nPython.\n"

        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        ctx.file_contents = {"epic_file": epic, "arch_file": arch_content}

        with caplog.at_level(logging.WARNING):
            apply_context_filter(ctx)  # Should NOT raise

        assert "optional-doc.md" in caplog.text
        assert "Tech Stack" in ctx.file_contents["arch_file"]

    def test_no_optional_markers_existing_error_behavior(self, tmp_path: Path):
        """Task 4.6: No (optional) markers → CompilerError as before."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| ghost.md | (full) | Needed |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": epic}
        ctx.discovered_files = {}

        with pytest.raises(CompilerError, match="ghost.md"):
            apply_context_filter(ctx)

    def test_optional_section_name_matches_heading(self, tmp_path: Path):
        """Task 4.7: Section 'Foo (optional)' matches heading '## Foo'."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| doc.md | Foo (optional); Bar | Both |
"""
        doc_content = """\
# Doc

## Foo

Foo content.

## Bar

Bar content.
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"doc_file": [tmp_path / "doc.md"]}
        ctx.file_contents = {"epic_file": epic, "doc_file": doc_content}

        apply_context_filter(ctx)

        result = ctx.file_contents["doc_file"]
        assert "Foo content." in result
        assert "Bar content." in result

    def test_skip_optional_missing_document_no_error(self, tmp_path: Path):
        """Task 4.9: (skip) (optional) on missing document → no error."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| nonexistent.md | (skip) (optional) | N/A |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": epic}
        ctx.discovered_files = {}

        # Should NOT raise — skip on missing doc is already a no-op
        apply_context_filter(ctx)

    def test_case_insensitive_optional_in_filter(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """Task 4.8: Case-insensitive (OPTIONAL) works in apply_context_filter."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| missing.md | (full) (OPTIONAL) | Nice |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": epic}
        ctx.discovered_files = {}

        with caplog.at_level(logging.WARNING):
            apply_context_filter(ctx)  # Should NOT raise

        assert "missing.md" in caplog.text

    def test_mixed_optional_required_documents(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """Mix of optional and required missing documents."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| required.md | (full) | Must have |
| optional.md | (full) (optional) | Nice to have |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": epic}
        ctx.discovered_files = {}

        with caplog.at_level(logging.WARNING):
            with pytest.raises(CompilerError) as exc_info:
                apply_context_filter(ctx)

        msg = str(exc_info.value)
        # Required doc in error
        assert "required.md" in msg
        # Optional doc NOT in error
        assert "optional.md" not in msg
        # Optional doc in warning log
        assert "optional.md" in caplog.text

    def test_sections_directive_missing_doc_with_mixed_optional_raises(
        self, tmp_path: Path,
    ):
        """Entire document missing with mixed optional/required sections raises."""
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Crash Recovery; State Persistence (optional); Blocked Story Handling | Core |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.file_contents = {"epic_file": epic}
        ctx.discovered_files = {}

        # Document is missing and has required sections → must raise CompilerError
        with pytest.raises(CompilerError, match="arch.md"):
            apply_context_filter(ctx)
