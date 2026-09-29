"""Review verdict as a durable artifact, and the three-witness "done" check.

This module exists because of the epic-11 runs (2026-09-04, incident named at
the 2026-09-05 roundtable): the parallel merger marked every merged story
``done`` the moment its branch landed, and re-marked earlier stories ``done``
as a "repair". Six stories whose last recorded review verdict was REJECT or
MAJOR REWORK read ``done``; one story that implemented nothing was re-seeded
``done`` from the tracker on resume, and five dependents built on it.

The operator-approved rule: **the merger never writes ``done``**. A story may
be promoted to ``done`` only when three independent witnesses agree:

1. The final full-pass review's verdict is APPROVE or better. A delta
   re-review scoped to a fix diff cannot promote — a story rejected for six
   missing criteria that fixes two must not come back approved on the delta.
2. The story file's own ``Status:`` line reads ``done``.
3. The acceptance-criteria audit lane ran on that final round and passed.

Any dissent parks the story in ``review``, which does not satisfy a
dependency edge and is the input to the operator's out-of-band review pass.

The witnesses need something durable to testify from. The review loop's
verdict lived only in phase outputs and worker state, which a merge discards;
so the synthesis handler records it here, in a per-story YAML under
``implementation-artifacts`` — committed content, so it survives the merge
and travels with the branch.

Posture (matching ``core.signoff``): the check functions return a **reason
string or None**; they do not raise and do not write. There are two checks
with deliberately different strictness:

* :func:`verdict_blocks_done` — the promotion gate. Absence of evidence is
  dissent: a story with no recorded verdict has not earned ``done``.
* :func:`seed_blocks_done` — the resume-seeding gate. Blocks only on positive
  disagreement (a story file that says it is not done, or a recorded verdict
  that failed). Absence of both artifacts leaves the tracker's claim
  standing, so projects with pre-feature ``done`` rows are not re-run on
  upgrade.
"""

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from bmad_assist_lite.core.exceptions import StateError

logger = logging.getLogger(__name__)

__all__ = [
    "APPROVING_VERDICTS",
    "VERDICTS_DIRNAME",
    "ReviewVerdictRecord",
    "load_review_verdict",
    "parse_audit_table",
    "read_story_status",
    "seed_blocks_done",
    "verdict_blocks_done",
    "verdict_path",
    "write_review_verdict",
]

VERDICTS_DIRNAME: str = "verdicts"
TEMP_FILE_SUFFIX: str = ".tmp"

#: Canonical Verdict values that count as an approval, plus their code-review
#: display forms — the record stores the canonical value, but a hand-written
#: or older artifact may carry the display form and means the same thing.
APPROVING_VERDICTS: frozenset[str] = frozenset({"PASS", "EXCELLENT", "APPROVE", "EXEMPLARY"})

#: Story-file statuses that witness two accepts as "done".
_DONE_STATUS_VALUES: frozenset[str] = frozenset({"done", "complete", "completed"})


def _utc_now() -> datetime:
    """Get current UTC datetime without timezone info."""
    return datetime.now(UTC).replace(tzinfo=None)


class ReviewVerdictRecord(BaseModel):
    """The review loop's exit, recorded where a merge cannot lose it.

    Attributes:
        story_id: ``"{epic}.{story}"``.
        verdict: Canonical Evidence Score verdict of the promoting round
            (``PASS``, ``EXCELLENT``, ``MAJOR_REWORK``, ``REJECT``), or
            ``None`` when no aggregate could be computed — which is not an
            approval.
        outcome: The review loop's exit outcome (``clean``, ``cap-exhausted``,
            ...), for the record; promotion reads ``verdict``, not this.
        review_iteration: Fix rounds spent when the loop exited.
        full_pass: Whether the promoting round was a full review, not a
            delta scoped to a fix diff. Only a full pass can promote.
        audit_required: Whether the AC-completeness audit lane was active for
            this run (``ac_audit.enabled`` or ``ac_audit.auto``). When it was
            not, witness three has nothing to testify about and is vacuous.
        audit_ran: Whether the audit lane actually ran on the promoting round.
        audit_passed: The audit lane's verdict — ``True`` only when every
            acceptance criterion read COMPLETE. ``None`` means its output
            could not be parsed, which is dissent, not a pass.
        evidence_score: The aggregate score, for humans reading the artifact.
        commit_sha: HEAD at recording time, for traceability only.
        timestamp: Naive UTC, per the project's timestamp convention.

    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    story_id: str
    verdict: str | None
    outcome: str
    review_iteration: int
    full_pass: bool
    audit_required: bool
    audit_ran: bool
    audit_passed: bool | None
    evidence_score: float | None = None
    commit_sha: str = ""
    timestamp: datetime


# ============================================================================
# Persistence
# ============================================================================


def verdict_path(project_root: Path, story_id: str) -> Path:
    """Resolve the verdict artifact path for one story.

    Lives under ``implementation-artifacts`` — committed content, unlike the
    ``.bmad-assist-lite`` state dir — so the record survives the merge of a
    parallel worker's branch and is readable from the integration checkout.
    """
    safe = story_id.replace(".", "-").replace(os.sep, "-")
    return (
        project_root
        / "_bmad-output"
        / "implementation-artifacts"
        / VERDICTS_DIRNAME
        / f"story-{safe}.yaml"
    )


def write_review_verdict(record: ReviewVerdictRecord, project_root: Path) -> Path:
    """Write a verdict artifact atomically (temp + ``os.replace``).

    Args:
        record: The review loop exit to record.
        project_root: Project root.

    Returns:
        The path written.

    Raises:
        StateError: If the artifact cannot be written.

    """
    path = verdict_path(project_root, record.story_id)
    temp_path = path.with_suffix(path.suffix + TEMP_FILE_SUFFIX)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            yaml.dump(
                record.model_dump(mode="json"),
                default_flow_style=False,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    except (OSError, yaml.YAMLError) as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                logger.debug("Could not remove verdict temp file %s", temp_path)
        raise StateError(f"Failed to write verdict artifact to {path}: {e}") from e

    return path


def load_review_verdict(project_root: Path, story_id: str) -> ReviewVerdictRecord | None:
    """Read one story's verdict artifact.

    Returns:
        The record, or None if there is none. A corrupt artifact also reads
        as None — an unreadable verdict is not an approval — and is logged.

    """
    path = verdict_path(project_root, story_id)
    if not path.exists():
        return None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return ReviewVerdictRecord.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError, TypeError) as e:
        logger.warning("Ignoring unreadable verdict artifact at %s: %s", path, e)
        return None


# ============================================================================
# Witness two: the story file's own Status line
# ============================================================================

_STATUS_LINE_RE = re.compile(r"^\*{0,2}Status\*{0,2}:\s*(.+?)\s*$", re.IGNORECASE)

#: How far into the story file the Status line may sit. It is literally line 3
#: in the template; the margin absorbs a title tweak, not a rewrite.
_STATUS_SCAN_LINES = 20


def read_story_status(story_path: Path) -> str | None:
    """Read the ``Status:`` line from a story file's head, as text.

    Deliberately not a story parser: the line is the witness, and parsing the
    whole document would couple this check to every template change.

    Returns:
        The lowercased status value, or None when the file cannot be read or
        no Status line appears in the first few lines.

    """
    try:
        text = story_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.debug("Could not read story file %s: %s", story_path, e)
        return None

    for line in text.splitlines()[:_STATUS_SCAN_LINES]:
        m = _STATUS_LINE_RE.match(line.strip())
        if m:
            return m.group(1).strip().strip("*").strip().lower()
    return None


def _resolve_story_file(project_root: Path, story_id: str) -> Path | None:
    """Find the story file for ``story_id`` under implementation-artifacts.

    Uses the same two naming forms as the loop's own resolver
    (``loop.story_paths``), duplicated here rather than imported because that
    resolver reads the paths singleton and this module must work against any
    explicit root (a worktree, the integration checkout, a test tmp dir).
    """
    stories_dir = project_root / "_bmad-output" / "implementation-artifacts"
    parts = story_id.split(".")
    if len(parts) != 2 or not all(parts):
        return None
    epic_num, story_num = parts

    primary = stories_dir / f"story-{epic_num}.{story_num}.md"
    if primary.exists():
        return primary

    candidates = sorted(stories_dir.glob(f"{epic_num}-{story_num}-*.md"))
    return candidates[0] if candidates else None


# ============================================================================
# Witness three helper: reading the audit lane's verdict table
# ============================================================================

_AUDIT_VERDICT_RE = re.compile(r"\b(COMPLETE|PARTIAL|MISSING)\b", re.IGNORECASE)


def parse_audit_table(text: str) -> bool | None:
    """Read the AC-audit lane's ``| AC | verdict | evidence |`` table.

    Returns:
        True when every row's verdict is COMPLETE, False when any row reads
        PARTIAL or MISSING, and None when no verdict rows are found at all —
        the caller must treat None as dissent, not as a pass.

    """
    verdicts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        for cell in cells:
            m = _AUDIT_VERDICT_RE.fullmatch(cell.strip("*_ ").strip())
            if m:
                verdicts.append(m.group(1).upper())
                break

    if not verdicts:
        return None
    return all(v == "COMPLETE" for v in verdicts)


# ============================================================================
# The two gates
# ============================================================================


def _record_dissent(record: ReviewVerdictRecord, story_id: str) -> str | None:
    """Say why a verdict record withholds approval, or None if it approves."""
    if not record.full_pass:
        return (
            f"story {story_id}'s recorded verdict came from a delta re-review, "
            "not a full pass — a delta cannot promote"
        )
    if record.verdict is None or record.verdict.upper() not in APPROVING_VERDICTS:
        return (
            f"story {story_id}'s final review verdict is "
            f"'{record.verdict or 'unavailable'}', not an approval"
        )
    if record.audit_required:
        if not record.audit_ran:
            return f"story {story_id}'s acceptance-criteria audit did not run on the final round"
        if record.audit_passed is not True:
            state = "unparseable" if record.audit_passed is None else "failing"
            return f"story {story_id}'s acceptance-criteria audit is {state} on the final round"
    return None


def verdict_blocks_done(project_root: Path, story_id: str) -> str | None:
    """The promotion gate: say why ``story_id`` may not be ``done``, or None.

    All three witnesses must agree, and absence of evidence is dissent — a
    story that cannot show an approving full-pass verdict, a story file that
    says ``done``, and a passing final-round audit has not earned the claim.

    Args:
        project_root: Root of the checkout to testify from (the project root
            for sequential runs, the integration checkout after a merge).
        story_id: Story identifier like ``"4.2"``.

    Returns:
        A human-readable reason to withhold ``done``, or None to permit it.

    """
    record = load_review_verdict(project_root, story_id)
    if record is None:
        return (
            f"story {story_id} has no recorded review verdict "
            f"(expected {verdict_path(project_root, story_id)})"
        )
    reason = _record_dissent(record, story_id)
    if reason is not None:
        return reason

    story_file = _resolve_story_file(project_root, story_id)
    if story_file is None:
        return f"story {story_id} has no story file to confirm its own status"
    status = read_story_status(story_file)
    if status is None:
        return f"story {story_id}'s story file has no readable Status line"
    if status not in _DONE_STATUS_VALUES:
        return f"story {story_id}'s story file says 'Status: {status}', not done"

    return None


def seed_blocks_done(project_root: Path, story_id: str) -> str | None:
    """The seeding gate: block re-seeding ``done`` only on positive disagreement.

    Used when reconciling a run against the tracker on start/resume. Unlike
    the promotion gate, absence of evidence does not block: a tracker row
    marked ``done`` before this feature existed has no verdict artifact, and
    refusing to seed it would re-run finished work on upgrade. What it does
    catch is the incident shape — a tracker that says ``done`` while the
    story's own file says otherwise.

    The story file is the SENIOR witness here, and when it affirmatively says
    ``done`` the verdict record is not consulted: the file is the artifact the
    operator's out-of-band review pass edits when it promotes a parked story
    by hand, and a stale in-loop verdict must not overrule that promotion on
    every later resume. The record is consulted only when the story file
    cannot testify (missing, or no readable Status line).

    Returns:
        A human-readable reason not to seed ``done``, or None to permit it.

    """
    story_file = _resolve_story_file(project_root, story_id)
    if story_file is not None:
        status = read_story_status(story_file)
        if status is not None:
            if status in _DONE_STATUS_VALUES:
                return None
            return (
                f"story {story_id}'s story file says 'Status: {status}' — the "
                "tracker's 'done' is not corroborated"
            )

    record = load_review_verdict(project_root, story_id)
    if record is not None:
        reason = _record_dissent(record, story_id)
        if reason is not None:
            return reason

    return None
