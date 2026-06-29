"""Tests for XML-1.0 illegal-character sanitization in the compiler output.

Regression coverage for the fix_quality_gate failure where a synthesis report
containing a control character (e.g. an ANSI escape code) was written into the
story file, then embedded in a CDATA section. CDATA does NOT legalize control
characters in XML 1.0, so ElementTree rejected the document as
"not well-formed (invalid token)" and the phase crashed.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bmad_assist_lite.compiler.output import (
    _escape_xml_attr,
    _escape_xml_text,
    _sanitize_xml_chars,
    _wrap_cdata,
    generate_output,
)
from bmad_assist_lite.compiler.types import CompiledWorkflow

# Characters illegal in XML 1.0 that commonly appear in captured tool output.
ILLEGAL = "\x00\x07\x08\x0b\x0c\x0e\x1b\x1f"
# Characters that are legal and MUST be preserved.
LEGAL = "tab\tnl\ncr\rspace café — 漢字 \U0001f600"


def _make_compiled(context: str = "", instructions: str = "") -> CompiledWorkflow:
    return CompiledWorkflow(
        workflow_name="fix-quality-gate",
        mission="fix the gate",
        context=context,
        variables={},
        instructions=instructions,
        output_template="",
    )


class TestSanitizeXmlChars:
    """Unit tests for the _sanitize_xml_chars primitive."""

    def test_strips_all_illegal_control_chars(self) -> None:
        out = _sanitize_xml_chars(f"a{ILLEGAL}b")
        assert out == "ab"

    def test_preserves_legal_whitespace_and_unicode(self) -> None:
        assert _sanitize_xml_chars(LEGAL) == LEGAL

    def test_preserves_astral_plane_characters(self) -> None:
        # Emoji (U+1F600) is in the legal [#x10000-#x10FFFF] range.
        assert _sanitize_xml_chars("ok\U0001f600") == "ok\U0001f600"

    def test_empty_string(self) -> None:
        assert _sanitize_xml_chars("") == ""


class TestEmbeddingHelpersSanitize:
    """The escape/CDATA helpers must drop illegal chars before embedding."""

    def test_cdata_strips_illegal(self) -> None:
        wrapped = _wrap_cdata("out\x1b[31mput\x00")
        assert "\x1b" not in wrapped and "\x00" not in wrapped
        # Still a valid CDATA section.
        ET.fromstring(f"<r>{wrapped}</r>")

    def test_escape_text_strips_illegal(self) -> None:
        assert "\x1b" not in _escape_xml_text("a\x1bb")

    def test_escape_attr_strips_illegal(self) -> None:
        assert "\x00" not in _escape_xml_attr("a\x00b")


class TestGenerateOutputRegression:
    """End-to-end: generate_output must not raise on control-char content."""

    def test_context_file_with_ansi_escape_does_not_crash(self) -> None:
        # Mirrors the 8.5 failure: a synthesis report with an ANSI escape code.
        poisoned = (
            "## Review Synthesis\n"
            "Test output:\n\x1b[31mFAIL\x1b[0m tests/foo.test.ts\n"
            "Killed process \x00 abruptly.\n"
        )
        context_files = {
            "C:/proj/_bmad-output/8-5-multi-variant-generation.md": poisoned,
        }
        out = generate_output(
            _make_compiled(),
            project_root=Path("C:/proj"),
            context_files=context_files,
        )
        # Parses cleanly and the control chars are gone.
        ET.fromstring(out.xml)
        assert "\x1b" not in out.xml and "\x00" not in out.xml
        # Legitimate content survives.
        assert "FAIL" in out.xml and "tests/foo.test.ts" in out.xml

    def test_xml_instructions_with_control_char_does_not_crash(self) -> None:
        # The raw-instructions path (XML-structured instructions) is also guarded.
        instr = "<steps>\n<step>do\x0c the thing</step>\n</steps>"
        out = generate_output(_make_compiled(instructions=instr))
        ET.fromstring(out.xml)
        assert "\x0c" not in out.xml

    def test_clean_content_unaffected(self) -> None:
        out = generate_output(
            _make_compiled(),
            project_root=Path("C:/proj"),
            context_files={"C:/proj/story.md": "# Story\nAll good. café 😀\n"},
        )
        root = ET.fromstring(out.xml)
        assert root.tag == "compiled-workflow"
        assert "😀" in out.xml


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
