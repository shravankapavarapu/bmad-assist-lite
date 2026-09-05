"""goal-run11 auto-trigger for the AC-completeness audit lane.

The run10 audit lane (``ac_audit.enabled``) caught the dangerous run9-class
cross-boundary completeness failures, but at ~$2/story it is too expensive to
force-ON for every story, and per-epic opt-in proved a dead letter (the operator
will not make per-epic risk calls). This module lets the TOOL decide, per story,
whether the audit is worth its ~$2 — from worktree-local structural risk signals
gathered at ``code_review`` time.

Design mirrors the SP-A1 adaptive lean-dev precedent
(:func:`bmad_assist_lite.loop.handlers.dev_story.resolve_dev_lean_mode`): a single
decision point (``CodeReviewHandler._build_lanes``) resolving a per-story choice
from ``(config, state)`` plus the on-disk signals the handler can already reach.

Three states (see :class:`bmad_assist_lite.core.config.AcAuditConfig`):

* ``enabled: true``  — force-ON. :func:`resolve_ac_audit_enabled` short-circuits
  before gathering any signal.
* ``auto: true``     — gather signals and decide. **Bias: FIRE when uncertain** —
  a wasted audit costs ~$2; a silent completeness escape is the thing this whole
  program exists to prevent. Every decision is recorded (log line + best-effort
  durable JSONL) so trigger accuracy is measurable.
* both false         — OFF, byte-identical to the pre-lever review phase: the
  resolver returns before any file/git access and nothing is recorded.

Signal extraction (``_count_acs``, ``_doc_markers``, ``_diff_spread``) and the
decision (:func:`decide_from_signals`) are pure functions of already-gathered
inputs, so the goal-run11 Phase-2 offline calibration exercises the exact same
logic against the frozen Epic-5 harvest without a live run.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from bmad_assist_lite.core.config import Config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.providers._windows import get_subprocess_kwargs

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants — CALIBRATED OFFLINE (goal-run11 Phase 2) against the frozen
# Epic-5 harvest. The bar: fire on 5.2 (run9 cross-boundary) and 5.6 (the caught
# blocker), and stay quiet on at least one story (else auto == always-ON in
# disguise). Change these ONLY from an offline calibration run, never live.
# ---------------------------------------------------------------------------
FIRE_MIN_AC_COUNT = 5
"""A story tracing >= this many acceptance criteria has enough completeness
surface that a missed consuming-side is plausible. LOAD-BEARING discriminator:
on the frozen Epic-5 calibration this is what fires 5.6 (5 ACs, no cross-screen
AC) while leaving 5.1 (4 ACs, self-contained) quiet."""

FIRE_MIN_DOC_MARKERS = 4
"""The story doc naming >= this many distinct cross-boundary discovery terms.
Set HIGH because the signal proved noisy on studio: every Epic-5 story doc
carries producer/consumer/searchParams language (2-5 hits each), so a low bar
fires everything. At 4 it is a non-load-bearing 'heavily-flagged' backup (only
5.2 reaches it, and 5.2 already fires on its cross-screen ACs)."""

FIRE_MIN_CHANGED_FILES = 30
"""An unusually large change (>= this many files). Set high: Epic-5 stories are
uniformly broad (24-37 files each), so a low bar makes the diff signal fire
everything and discriminate nothing. At 30 it is a 'genuinely huge change'
backup that does not flip any Epic-5 verdict (the quiet story, 5.1, is 25)."""

FIRE_MIN_CHANGED_DIRS = 10
"""... spread across >= this many directories. High for the same reason (Epic-5
spread is 6-11 dirs); a redundant backup, not the discriminator."""

#: Cross-boundary handoff language in an acceptance criterion — the run9 class
#: (an AC that threads a value from one screen/route into another).
_AC_CROSS_VERBS = re.compile(
    r"(?i)\b(thread(?:s|ed|ing)?|hand-?off|navigat\w+|consum\w+|carr(?:y|ies|ying)"
    r"|producer|downstream|forwarded|re-typed|into the s-)\b"
)

#: Any screen reference, e.g. "S-03". Used to detect an AC that reaches a screen
#: other than the story's own.
_SCREEN_RE = re.compile(r"\bS-\d+\b", re.IGNORECASE)

#: An acceptance-criterion bullet line, e.g. "- AC-4: Given ...".
_AC_LINE_RE = re.compile(r"(?im)^\s*[-*]?\s*AC-\d+\b.*$")

#: Distinct cross-boundary discovery terms a story doc uses when it involves
#: cross-cutting dependencies (producer/consumer, threading, sweeps).
_DOC_MARKER_TERMS: tuple[str, ...] = (
    "dependency sweep",
    "files you'll touch",
    "consumer",
    "consuming",
    "producer",
    "cross-screen",
    "cross-boundary",
    "call site",
    "route param",
    "searchparams",
    "hand-off",
    "handoff",
    "downstream",
    "threads ",
    "thread the",
)

_TRIGGER_LOG_PREFIX = "[AC-AUDIT-TRIGGER]"
"""Grep anchor for the Phase-4 harvest — every auto/forced decision emits one
single-line JSON record under this prefix into the story's captured log."""

_TRIGGER_RECORD_FILENAME = "ac-audit-trigger.jsonl"


# ---------------------------------------------------------------------------
# Signal container + decision
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AuditTriggerSignals:
    """Worktree-local structural signals gathered at review time."""

    story_ac_count: int
    cross_ac_markers: int
    doc_marker_hits: int
    diff_file_count: int
    diff_dir_count: int
    dev_attempt: int
    review_iteration: int
    signals_available: bool
    """False when the diff or the epic ACs could not be read — treated as
    'uncertain', which fires under the bias."""
    markers_found: tuple[str, ...] = ()
    surfaces_sample: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        """JSON-serialisable form (tuples -> lists) for the decision record."""
        d = asdict(self)
        d["markers_found"] = list(self.markers_found)
        d["surfaces_sample"] = list(self.surfaces_sample)
        return d


@dataclass(frozen=True)
class AuditDecision:
    """The resolved per-story audit decision, with its reasons for the record."""

    fire: bool
    mode: str  # "forced_on" | "off" | "auto" | "final_round"
    reason: str
    signals: AuditTriggerSignals | None = None

    def to_record(self, state: State) -> dict[str, object]:
        """The per-story decision record written to the log + durable JSONL."""
        return {
            "story": state.current_story,
            "epic": state.current_epic,
            "review_iteration": state.review_iteration,
            "dev_attempt": state.dev_attempt,
            "fired": self.fire,
            "mode": self.mode,
            "reason": self.reason,
            "signals": self.signals.as_dict() if self.signals is not None else None,
        }


def decide_from_signals(s: AuditTriggerSignals) -> tuple[bool, str]:
    """Pure decision core. FIRE if any risk signal trips; bias to fire.

    Returns ``(fire, reason)`` where ``reason`` names every signal that fired
    (or, when quiet, the signal values that kept it quiet — so a calibration run
    can see exactly why).
    """
    reasons: list[str] = []
    # Escalation: a retry or a re-review means the first pass was not clean —
    # always audit (this is the safety net for the fix loop and lean-dev retries).
    if s.dev_attempt > 0 or s.review_iteration >= 1:
        reasons.append(
            f"escalation(dev_attempt={s.dev_attempt},review_iteration={s.review_iteration})"
        )
    # Uncertain: could not read the diff or the epic ACs — fire rather than
    # silently skip the gate on a story we cannot size.
    if not s.signals_available:
        reasons.append("signals_unavailable->fire_when_uncertain")
    # The run9 class: an AC threads a value across a screen/route boundary.
    if s.cross_ac_markers >= 1:
        reasons.append(f"cross_ac_markers={s.cross_ac_markers}")
    # High completeness surface: many ACs to trace end-to-end.
    if s.story_ac_count >= FIRE_MIN_AC_COUNT:
        reasons.append(f"ac_count={s.story_ac_count}>={FIRE_MIN_AC_COUNT}")
    # create_story itself flagged cross-cutting dependencies.
    if s.doc_marker_hits >= FIRE_MIN_DOC_MARKERS:
        reasons.append(f"doc_markers={s.doc_marker_hits}>={FIRE_MIN_DOC_MARKERS}")
    # A broad change set that can hide a missing consuming side.
    if (
        s.diff_file_count >= FIRE_MIN_CHANGED_FILES
        or s.diff_dir_count >= FIRE_MIN_CHANGED_DIRS
    ):
        reasons.append(
            f"diff_spread(files={s.diff_file_count},dirs={s.diff_dir_count})"
        )

    if reasons:
        return True, "; ".join(reasons)
    return False, (
        f"quiet(ac_count={s.story_ac_count},cross_ac={s.cross_ac_markers},"
        f"doc_markers={s.doc_marker_hits},files={s.diff_file_count},"
        f"dirs={s.diff_dir_count})"
    )


# ---------------------------------------------------------------------------
# Pure signal extractors (shared by the live gatherer and the offline harness)
# ---------------------------------------------------------------------------
def extract_story_section(epic_text: str, story_id: str | None) -> str:
    """Return the ``### Story {id}:`` section body, or the whole text on a miss.

    A miss (empty text or no matching header) returns "" so the AC count is 0 —
    the caller decides whether an unreadable epic counts as 'uncertain'.
    """
    if not epic_text or not story_id:
        return ""
    header = re.compile(
        rf"(?im)^#{{2,4}}\s*Story\s+{re.escape(story_id)}\s*:", re.IGNORECASE
    )
    m = header.search(epic_text)
    if not m:
        return ""
    start = m.start()
    nxt = re.compile(r"(?im)^#{2,4}\s")
    tail = nxt.search(epic_text, m.end())
    return epic_text[start : tail.start()] if tail else epic_text[start:]


def _count_acs(section: str) -> tuple[int, int]:
    """Return ``(ac_count, cross_ac_markers)`` for a story section.

    ``ac_count`` counts ``AC-N:`` bullets (the shard's format; the parser's
    checkbox-only ``ac_count`` is structurally 0 here — verified on Epic-5). An
    acceptance criterion often WRAPS across physical lines, so each AC's text is
    the slice from its bullet to the next AC bullet; this is done inside the
    isolated ``**Acceptance Criteria:**`` block so ranges cited elsewhere (e.g.
    "proves: AC-1..AC-6") never inflate the count. ``cross_ac_markers`` counts
    ACs that use cross-boundary handoff language or reach a screen other than the
    story's own.
    """
    if not section:
        return 0, 0
    # Isolate the acceptance-criteria block: from its heading to the next
    # non-blockquoted bold subsection (**Validation:**/**Dependencies:**/...) or
    # the next markdown heading, whichever comes first.
    block_match = re.search(
        r"(?is)\*\*Acceptance Criteria:\*\*(.*?)"
        r"(?:\n[ \t]*\*\*[A-Z][^\n]*:\*\*|\n#{2,4}\s|\Z)",
        section,
    )
    ac_block = block_match.group(1) if block_match else section
    # The story's own screens, from the "screens:" Covers line if present.
    own = set()
    screens_line = re.search(r"(?im)^\s*[-*]?\s*screens:\s*(.+)$", section)
    if screens_line:
        own = {s.upper() for s in _SCREEN_RE.findall(screens_line.group(1))}
    ac_starts = [m.start() for m in _AC_LINE_RE.finditer(ac_block)]
    ac_count = len(ac_starts)
    cross = 0
    for i, start in enumerate(ac_starts):
        end = ac_starts[i + 1] if i + 1 < len(ac_starts) else len(ac_block)
        text = ac_block[start:end]
        refs = {s.upper() for s in _SCREEN_RE.findall(text)}
        reaches_other = bool(refs - own) if own else bool(refs)
        if _AC_CROSS_VERBS.search(text) or reaches_other:
            cross += 1
    return ac_count, cross


def _doc_markers(doc_text: str) -> tuple[int, tuple[str, ...]]:
    """Return ``(distinct_marker_count, markers_found)`` for a story doc."""
    if not doc_text:
        return 0, ()
    low = doc_text.lower()
    found = tuple(term for term in _DOC_MARKER_TERMS if term in low)
    return len(found), found


def _diff_spread(changed_files: list[str]) -> tuple[int, int, tuple[str, ...]]:
    """Return ``(file_count, distinct_dir_count, sample_of_dirs)``."""
    files = [f for f in changed_files if f]
    dirs = {os.path.dirname(f) or "." for f in files}
    sample = tuple(sorted(dirs))[:12]
    return len(files), len(dirs), sample


# ---------------------------------------------------------------------------
# Live gathering (reads git + epic + story doc)
# ---------------------------------------------------------------------------
def _git_changed_files(project_path: Path, timeout: int = 15) -> list[str] | None:
    """``git diff --name-only HEAD`` + untracked, or None on any git failure."""

    def _run(args: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                args,
                cwd=project_path,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                **get_subprocess_kwargs(),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            logger.warning("git %s failed: %s", " ".join(args[1:3]), exc)
            return None

    tracked = _run(["git", "diff", "--name-only", "HEAD"])
    if tracked is None or tracked.returncode != 0:
        # Unborn HEAD (no commits yet): fall back to the index diff.
        tracked = _run(["git", "diff", "--name-only"])
        if tracked is None or tracked.returncode != 0:
            return None
    files = [ln.strip() for ln in tracked.stdout.splitlines() if ln.strip()]
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard"])
    if untracked is not None and untracked.returncode == 0:
        files.extend(ln.strip() for ln in untracked.stdout.splitlines() if ln.strip())
    return files


def _read_epic_text(epic: int | str | None) -> str | None:
    """Read the epic shard text via the paths singleton, or None if unresolvable.

    Imports are lazy to avoid a handler->cli import cycle at module load.
    """
    if epic is None:
        return None
    try:
        epic_num = int(str(epic).split(".", 1)[0])
    except (ValueError, TypeError):
        return None
    try:
        from bmad_assist_lite.cli import _find_epic_file
        from bmad_assist_lite.core.paths import get_paths

        epics_dir = get_paths().epics_dir
    except (RuntimeError, ImportError):
        return None
    path = _find_epic_file(epics_dir, epic_num)
    if path is None or not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_story_doc(story_id: str | None) -> str:
    """Read the story doc text, or "" if none resolves (missing != uncertain)."""
    if not story_id:
        return ""
    try:
        from bmad_assist_lite.loop.story_paths import resolve_story_path

        path = resolve_story_path(story_id)
    except (RuntimeError, ImportError):
        return ""
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def gather_audit_signals(
    config: Config,
    state: State,
    project_path: Path,
    *,
    epic_text: str | None = None,
    story_doc_text: str | None = None,
    changed_files: list[str] | None = None,
) -> AuditTriggerSignals:
    """Gather the structural signals for the current story.

    The three keyword overrides let the Phase-2 offline calibration inject frozen
    inputs (a story's reconstructed diff, its committed story doc, the frozen
    epic shard) so the exact live extraction + decision run against past data.
    """
    available = True

    if changed_files is None:
        changed_files = _git_changed_files(project_path)
        if changed_files is None:
            available = False
            changed_files = []
    file_count, dir_count, surfaces = _diff_spread(changed_files)

    if epic_text is None:
        epic_text = _read_epic_text(state.current_epic)
        if epic_text is None:
            available = False
            epic_text = ""
    section = extract_story_section(epic_text, state.current_story)
    ac_count, cross = _count_acs(section)

    if story_doc_text is None:
        story_doc_text = _read_story_doc(state.current_story)
    doc_hits, markers = _doc_markers(story_doc_text)

    return AuditTriggerSignals(
        story_ac_count=ac_count,
        cross_ac_markers=cross,
        doc_marker_hits=doc_hits,
        diff_file_count=file_count,
        diff_dir_count=dir_count,
        dev_attempt=state.dev_attempt,
        review_iteration=state.review_iteration,
        signals_available=available,
        markers_found=markers,
        surfaces_sample=surfaces,
    )


# ---------------------------------------------------------------------------
# The resolver + the decision record
# ---------------------------------------------------------------------------
def resolve_ac_audit_enabled(
    config: Config,
    state: State,
    project_path: Path | None = None,
) -> AuditDecision:
    """Whether the AC-completeness audit lane runs for this story.

    ``enabled`` (force-ON) wins and short-circuits before any signal gathering.
    With both flags off the function returns immediately with ``fire=False`` and
    no side effects — the byte-identical-off guarantee. In ``auto`` mode the
    FINAL review round fires unconditionally, before any signal gathering: the
    final round's verdict is the one that can promote a story to done, and the
    audit's pass is one of the three witnesses that promotion requires — a
    quiet signal set must not skip the gate on exactly the round that counts.
    Other rounds gather worktree-local signals and apply
    :func:`decide_from_signals`.
    """
    ac = config.ac_audit
    if ac.enabled:
        return AuditDecision(
            fire=True, mode="forced_on", reason="ac_audit.enabled=true (force-ON)"
        )
    if not ac.auto:
        return AuditDecision(
            fire=False, mode="off", reason="ac_audit.auto=false, enabled=false"
        )
    cap = config.loop.review_max_iterations
    if state.review_iteration >= cap:
        return AuditDecision(
            fire=True,
            mode="final_round",
            reason=(
                f"final review round (review_iteration={state.review_iteration} "
                f">= cap={cap}) — the promoting round always carries the audit"
            ),
        )
    signals = gather_audit_signals(config, state, project_path or Path("."))
    fire, reason = decide_from_signals(signals)
    return AuditDecision(fire=fire, mode="auto", reason=reason, signals=signals)


def _atomic_append_jsonl(path: Path, line: str) -> None:
    """Append one line to a JSONL file atomically (read + append + os.replace)."""
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(existing + line + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record_audit_trigger(decision: AuditDecision, state: State) -> None:
    """Record a trigger decision for the Phase-4 harvest.

    Emits a single-line JSON log record (the durable, parallel-safe harvest
    source) plus a best-effort local JSONL beside ``state.yaml``. No-op when the
    lever is off, so the off path stays inert.
    """
    if decision.mode == "off":
        return
    record = decision.to_record(state)
    line = json.dumps(record, sort_keys=True)
    logger.info("%s %s", _TRIGGER_LOG_PREFIX, line)
    try:
        from bmad_assist_lite.providers.base import write_progress

        verb = "FIRED" if decision.fire else "quiet"
        write_progress(
            f"  {_TRIGGER_LOG_PREFIX} audit {verb} for story "
            f"{record.get('story')}: {decision.reason}"
        )
    except (OSError, ValueError, ImportError):
        pass
    try:
        from bmad_assist_lite.core.paths import get_paths

        dst = get_paths().bmad_assist_dir / _TRIGGER_RECORD_FILENAME
        _atomic_append_jsonl(dst, line)
    except (RuntimeError, OSError):
        pass
