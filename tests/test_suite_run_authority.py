"""Guards for the suite-run authority boundary fixed by ADR-0006.

`quality_gate` is the sole authoritative full-suite run: deterministic, non-LLM,
and the only one whose verdict the tool's own code enforces. `dev_story` keeps its
in-turn self-check. `code_review_synthesis` verifies the surface it changed rather
than re-running the whole suite inside a master-LLM turn.

The last test here is the one that matters most. Incident I-02 — six "green"
stories that shipped with five failing tests because every reviewer *deferred*
execution — is what happens when the last real run disappears. A future edit that
quietly removes the authoritative run must turn this file red.
"""

import sys
from pathlib import Path

import pytest

from bmad_assist_lite.core.gate_runner import GateCommand, run_gates

WORKFLOWS = Path(__file__).resolve().parents[1] / "src" / "bmad_assist_lite" / "workflows"
SYNTHESIS_INSTRUCTIONS = WORKFLOWS / "code-review-synthesis" / "instructions.xml"
DEV_STORY_INSTRUCTIONS = WORKFLOWS / "dev-story" / "instructions.xml"

# The language that means "run everything", mirroring the vocabulary
# verify_redundant_suite_runs.py counts on.
FULL_SUITE_PHRASES = (
    "full test suite",
    "entire test suite",
    "all tests must pass",
    "run all tests",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


class TestCodeReviewSynthesisScope:
    """T29 — the synthesis phase narrows its run; it does not drop it."""

    def test_synthesis_does_not_request_a_full_suite_run(self) -> None:
        text = _text(SYNTHESIS_INSTRUCTIONS)
        offenders = [phrase for phrase in FULL_SUITE_PHRASES if phrase in text]
        assert offenders == [], (
            f"code-review-synthesis still instructs a whole-suite run: {offenders}. "
            "ADR-0006 keeps quality_gate as the sole authoritative full-suite run."
        )

    def test_synthesis_still_requires_verification_of_the_changed_surface(self) -> None:
        """REQ-06.3 criterion 5 — narrowed, never deleted.

        A workflow whose text implies "someone else will run it" is exactly
        incident I-02's precondition, so the instruction to execute must survive
        the narrowing.
        """
        text = _text(SYNTHESIS_INSTRUCTIONS)
        assert "runtime verification" in text
        assert "you touched" in text or "you changed" in text, (
            "the narrowed instruction must still name the changed surface to verify"
        )
        assert "tests for this task" in text, (
            "synthesis must still be told to execute tests, only a narrower set"
        )

    def test_synthesis_forbids_deferring_execution(self) -> None:
        """The I-02 lesson, written into the instruction it applies to."""
        text = _text(SYNTHESIS_INSTRUCTIONS)
        assert "requires manual run" in text, (
            "the instruction must explicitly reject 'requires manual run' as verification"
        )


class TestDevStorySelfCheckKept:
    """ADR-0006 — dev_story's self-check is how the implementer knows it is done."""

    def test_dev_story_still_requests_a_full_suite_run(self) -> None:
        text = _text(DEV_STORY_INSTRUCTIONS)
        assert any(phrase in text for phrase in FULL_SUITE_PHRASES), (
            "dev-story lost its in-turn self-check; ADR-0006 keeps it, because removing "
            "it pushes the first execution of new code to a phase the implementer never sees"
        )


class TestAuthoritativeRunSurvives:
    """REQ-06.3 criterion 4 — the deterministic run is NOT the one removed.

    G12: a static check is a smoke alarm. This drives the real gate runner and
    asserts a command actually executed, so moving the call into another helper
    cannot fake a pass.
    """

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell quoting")
    def test_quality_gate_runner_actually_executes_its_commands(self, tmp_path: Path) -> None:
        marker = tmp_path / "gate-ran.txt"
        command = f'{sys.executable} -c "open({str(marker)!r}, \'w\').write(\'ran\')"'

        result = run_gates(
            [GateCommand(name="test", command=command)],
            tmp_path,
            timeout=60,
            report=False,
        )

        assert marker.exists(), (
            "the deterministic gate did not execute its command — the authoritative "
            "full-suite run is gone, which is incident I-02 waiting to happen"
        )
        assert result.all_passed is True

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell quoting")
    def test_quality_gate_phase_reaches_real_execution(self, tmp_path: Path) -> None:
        """The end-to-end link, driven through the handler the loop dispatches.

        A static check of the handler for a ``run_command`` call is exactly the
        smoke alarm G12 warns about — and it has already been defeated once here:
        the shared gate runner moved that call into a helper, and the check that
        counts deterministic runs went blind without going red. Executing the
        phase and looking for the side effect on disk cannot be defeated that way.
        """
        from bmad_assist_lite.core.config import load_config
        from bmad_assist_lite.core.paths import init_paths
        from bmad_assist_lite.core.state import Phase, State
        from bmad_assist_lite.loop.handlers.quality_gate import QualityGateHandler

        init_paths(tmp_path)
        marker = tmp_path / "quality-gate-ran.txt"
        config = load_config(
            {
                "providers": {"master": {"provider": "claude", "model": "opus"}},
                "quality_gate": {
                    "test": f'{sys.executable} -c "open({str(marker)!r}, \'w\').write(\'ran\')"',
                    "command_timeout": 60,
                },
            }
        )
        handler = QualityGateHandler(config, tmp_path)
        state = State(current_epic=1, current_story="1.2", current_phase=Phase.QUALITY_GATE)

        result = handler.execute(state)

        assert marker.exists(), (
            "the quality_gate phase did not execute its test command — the sole "
            "authoritative full-suite run is gone (ADR-0006), and nothing downstream "
            "would notice until an I-02 repeat ships"
        )
        assert result.outputs["quality_gate_action"] == "pass"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell quoting")
    def test_quality_gate_runner_fails_the_gate_on_a_failing_command(
        self, tmp_path: Path
    ) -> None:
        """A gate that cannot go red is not a gate."""
        result = run_gates(
            [GateCommand(name="test", command=f"{sys.executable} -c \"raise SystemExit(1)\"")],
            tmp_path,
            timeout=60,
            report=False,
        )

        assert result.all_passed is False
        assert [o.name for o in result.failures] == ["test"]
