"""Tests for compiler.context_filter — epic-driven context filtering."""

from pathlib import Path

import pytest

from bmad_assist_lite.compiler.context_filter import (
    ContextRequirement,
    _build_filename_to_key_map,
    _extract_section_from_content,
    apply_context_filter,
    parse_context_requirements,
)
from bmad_assist_lite.compiler.types import CompilerContext


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

    def test_unmatched_document_warns(self, tmp_path: Path, caplog):
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

        with caplog.at_level("WARNING"):
            apply_context_filter(ctx)

        assert "nonexistent.md" in caplog.text

    def test_unmatched_section_warns(self, tmp_path: Path, caplog):
        epic = """\
# Epic

### Context Requirements

| Document | Sections | Rationale |
|----------|----------|-----------|
| arch.md | Missing Section Name | Oops |
"""
        ctx = CompilerContext(project_root=tmp_path, output_folder=tmp_path / "_output")
        ctx.discovered_files = {"arch_file": [tmp_path / "arch.md"]}
        ctx.file_contents = {
            "epic_file": epic,
            "arch_file": "# Arch\n\n## Real Section\n\nContent.\n",
        }

        with caplog.at_level("WARNING"):
            apply_context_filter(ctx)

        assert "Missing Section Name" in caplog.text

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
