"""Deterministic cross-reviewer finding merge for the structured-review path (SP-1).

The default review pipeline hands N reviewers' free-form prose to a synthesis
model that re-derives, de-duplicates and narrates the findings itself — most of
the synthesis phase's output tokens. SP-1 moves the mechanical half of that work
into code: reviewers emit the same machine ``<!-- BMAD-FINDINGS -->`` block the
synthesis already documents, this module unions and identity-de-duplicates them,
and one short adjudication call only *annotates* the merged set (root-cause
bucket + verdict) rather than regenerating it.

The load-bearing invariant, stated so a test can pin it: the merge is a pure
union keyed on the finding identity digest (``Finding.digest`` — file, anchor,
severity, bucket, title, all normalised). Two findings collapse **only** when
that digest is equal, i.e. they are the same finding. So the merge can never
drop a distinct finding of any severity — in particular no finding at or above
``high`` — which is exactly the SP-1 quality guard, checkable here without an
LLM (see :func:`high_severity_preserved`).

The shipped SP-1 path keeps ``code_review_synthesis`` as the round-1 fixer and
feeds it the merged candidate set (``render_adjudication_candidates``) so it need
not re-derive the cross-reviewer comparison; the synthesis then applies fixes and
emits its own remaining-findings block as before. The *by-reference adjudication*
helpers below (``parse_adjudication`` / ``apply_adjudication`` — a tool-free call
that only annotates buckets on stable ids ``F1``..``Fn``, never re-emitting the
set, so it cannot lose a finding) back the alternative "adjudication-only"
synthesis and are retained as tested infrastructure for it. If that call's output
cannot be parsed, the merged set is used unchanged — a safe, slightly
conservative fallback that can over-block but never silently under-block.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from bmad_assist_lite.validation.findings import (
    FINDINGS_CLOSE_MARKER,
    FINDINGS_OPEN_MARKER,
    Bucket,
    Finding,
    FindingParseError,
    FindingSet,
    Severity,
    parse_findings,
)

__all__ = [
    "ADJUDICATION_CLOSE_MARKER",
    "ADJUDICATION_OPEN_MARKER",
    "AdjudicationResult",
    "apply_adjudication",
    "high_severity_preserved",
    "merge_findings",
    "parse_adjudication",
    "parse_reviewer_findings",
    "render_adjudication_candidates",
    "reviewer_findings_addendum",
]


def reviewer_findings_addendum() -> str:
    """Extra reviewer instruction (SP-1): also emit the structured findings block.

    Appended to the reviewer prompt when ``speed.structured_review`` is on. The
    reviewers already narrate their review; this makes them additionally emit the
    same machine block the synthesis documents, so the synthesis can merge in code
    instead of re-deriving the finding set in prose. Field docs mirror
    ``code-review-synthesis`` step 9, reframed for a reviewer: report every finding
    you see, do not pre-filter to "remaining after a fix".
    """
    return (
        "<structured-findings-output>\n"
        "In ADDITION to your review above, you MUST end your response with the\n"
        "machine-readable finding block below. It is parsed and merged in code, so\n"
        "the exact markers and JSON shape are required. A response without this\n"
        "block is treated as a parse failure for your lane (your prose findings are\n"
        "then lost to the merge).\n\n"
        "Emit EVERY finding you found, one object each. If you found nothing, emit\n"
        "an empty list `[]` — that is how a clean review is expressed.\n\n"
        "Fields per finding:\n"
        "- `file`: repo-relative path.\n"
        "- `anchor`: the symbol/signature/expression the finding is about (e.g.\n"
        "  `def load_config`). This is the finding's identity across passes — be\n"
        "  specific and stable; do NOT put a line number in it.\n"
        "- `line`: integer, optional, advisory only.\n"
        "- `severity`: one of `low`, `medium`, `high`. Judge real reachability, not\n"
        "  the worst theoretical reading.\n"
        "- `bucket`: the ROOT CAUSE — `intent_gap` (code diverges from the story),\n"
        "  `bad_spec` (the story is wrong/ambiguous), `patch` (localized defect,\n"
        "  localized fix), `defer` (real but out of scope on the story's stated\n"
        "  intent), `reject` (not a real problem). Default to `patch` for a normal\n"
        "  defect. `defer`/`reject` never trigger a fix.\n"
        "- `title`: one line, specific. Avoid line numbers in the text.\n"
        "- `detail`: optional, free-form explanation.\n\n"
        f"{FINDINGS_OPEN_MARKER}\n"
        "```json\n"
        "[\n"
        '  {"file": "src/example/config.py", "anchor": "def load_config",\n'
        '   "line": 42, "severity": "high", "bucket": "patch",\n'
        '   "title": "Config path used before it is validated",\n'
        '   "detail": "A relative path from the caller reaches open() unchecked."}\n'
        "]\n"
        "```\n"
        f"{FINDINGS_CLOSE_MARKER}\n"
        "</structured-findings-output>"
    )

#: Markers the adjudication call wraps its decisions block in. Distinct from the
#: findings markers so a response may legitimately carry both.
ADJUDICATION_OPEN_MARKER = "<!-- BMAD-ADJUDICATION -->"
ADJUDICATION_CLOSE_MARKER = "<!-- /BMAD-ADJUDICATION -->"

_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL)


def _split_sources(source: str) -> list[str]:
    """Split a possibly comma-joined ``source`` back into lane labels."""
    return [part.strip() for part in source.split(",") if part.strip()]


def parse_reviewer_findings(
    reviews: Sequence[dict[str, Any]],
) -> tuple[list[Finding], list[str]]:
    """Parse each successful reviewer's structured findings block.

    A reviewer whose output lacks or breaks the block is recorded in ``notes``
    but never fails the merge — one lane's missing block must not lose another
    lane's findings. Each parsed finding is tagged with its reviewer label in
    ``source`` (excluded from the identity hash, so tagging is free).

    Returns:
        ``(findings, notes)`` where ``findings`` carries every parsed finding
        across all lanes (pre-merge) and ``notes`` explains any lane that
        produced no parseable block.

    """
    findings: list[Finding] = []
    notes: list[str] = []
    for review in reviews:
        if review.get("exit_code") != 0:
            continue
        label = review.get("reviewer") or review.get("validator") or "Unknown"
        text = review.get("response") or ""
        try:
            parsed = parse_findings(text)
        except FindingParseError as exc:
            notes.append(f"{label}: no parseable findings block ({exc})")
            continue
        for finding in parsed.findings:
            # The lane label is the authoritative source; a reviewer-set source
            # (rare) is replaced so merge can union lanes by a known vocabulary.
            findings.append(finding.model_copy(update={"source": label}))
    return findings, notes


def merge_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Union and identity-de-duplicate findings, preserving every distinct one.

    Dedup collapses two findings only when their identity digest is equal; the
    surviving record's ``source`` unions the collapsed lanes' labels. Ordering is
    deterministic: severity descending, then file, title, anchor — so the same
    inputs always yield the same ``F1``..``Fn`` id assignment downstream.
    """
    survivors: dict[str, Finding] = {}
    sources: dict[str, list[str]] = {}
    for finding in findings:
        digest = finding.digest()
        lane_sources = sources.setdefault(digest, [])
        for label in _split_sources(finding.source):
            if label not in lane_sources:
                lane_sources.append(label)
        survivors.setdefault(digest, finding)

    merged: list[Finding] = []
    for digest, finding in survivors.items():
        joined = ", ".join(sources[digest])
        merged.append(
            finding.model_copy(update={"source": joined}) if joined else finding
        )
    merged.sort(
        key=lambda f: (-int(f.severity), f.file, f.title, f.anchor)
    )
    return merged


def high_severity_preserved(
    inputs: Sequence[Finding], merged: Sequence[Finding]
) -> bool:
    """SP-1 quality guard, code-checkable: no ``>= high`` input finding was dropped.

    Every input finding at or above ``high`` must have an identity-equal survivor
    in ``merged``. True by construction for :func:`merge_findings`; asserted so a
    regression that starts dropping findings is caught without an A/B.
    """
    merged_digests = {f.digest() for f in merged}
    return all(
        f.digest() in merged_digests
        for f in inputs
        if f.severity >= Severity.HIGH
    )


def render_adjudication_candidates(
    merged: Sequence[Finding],
) -> tuple[str, dict[str, Finding]]:
    """Render merged findings as a compact, id-tagged candidate list.

    Returns ``(text, id_map)`` where ``id_map`` maps ``F1``..``Fn`` (assigned in
    merged order) back to the finding, so the adjudication decisions can be
    applied by reference.
    """
    id_map: dict[str, Finding] = {}
    lines: list[str] = []
    for index, finding in enumerate(merged, start=1):
        fid = f"F{index}"
        id_map[fid] = finding
        location = f"{finding.file}:{finding.line}" if finding.line else finding.file
        head = (
            f"- {fid} [{finding.severity.label}/{finding.bucket.value}] "
            f"{location} — {finding.title}"
        )
        meta: list[str] = []
        if finding.anchor:
            meta.append(f"anchor: {finding.anchor}")
        if finding.source:
            meta.append(f"reviewers: {finding.source}")
        if meta:
            head += "  (" + "; ".join(meta) + ")"
        lines.append(head)
        if finding.detail:
            lines.append(f"    {finding.detail}")
    return "\n".join(lines), id_map


def _extract_adjudication_block(text: str) -> str | None:
    """Pull the decisions payload out of an adjudication response, or None."""
    start = text.find(ADJUDICATION_OPEN_MARKER)
    if start == -1:
        # Tolerate a bare fenced block if the model dropped the markers.
        fence = _FENCE_RE.search(text)
        return fence.group("body") if fence else None
    body = text[start + len(ADJUDICATION_OPEN_MARKER) :]
    end = body.find(ADJUDICATION_CLOSE_MARKER)
    if end != -1:
        body = body[:end]
    fence = _FENCE_RE.search(body)
    return fence.group("body") if fence else body


class AdjudicationResult:
    """The parsed outcome of the adjudication call.

    ``verdict`` is advisory prose for the operator log; the loop-driving state is
    ``decisions`` (per-id bucket overrides). Kept a plain object rather than a
    pydantic model so a partial/loose LLM payload degrades gracefully.
    """

    def __init__(self, verdict: str, decisions: dict[str, Bucket]) -> None:
        """Store the advisory verdict prose and the per-id bucket overrides."""
        self.verdict = verdict
        self.decisions = decisions


def parse_adjudication(text: str) -> AdjudicationResult | None:
    """Parse the adjudication decisions block, returning None on any failure.

    Expected payload (inside the markers or a fenced block)::

        {"verdict": "...", "decisions": [{"id": "F1", "bucket": "patch"}, ...]}

    A missing/invalid bucket for an entry is skipped (that finding keeps its
    reviewer-assigned bucket); a wholly unparseable payload returns None so the
    caller falls back to the un-adjudicated merged set.
    """
    payload = _extract_adjudication_block(text)
    if not payload or not payload.strip():
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    verdict = str(data.get("verdict", "")).strip()
    decisions: dict[str, Bucket] = {}
    raw_decisions = data.get("decisions")
    if isinstance(raw_decisions, list):
        for entry in raw_decisions:
            if not isinstance(entry, dict):
                continue
            fid = entry.get("id")
            bucket_raw = entry.get("bucket")
            if not isinstance(fid, str) or not isinstance(bucket_raw, str):
                continue
            try:
                decisions[fid] = Bucket(bucket_raw.strip().lower())
            except ValueError:
                continue
    return AdjudicationResult(verdict=verdict, decisions=decisions)


def apply_adjudication(
    id_map: dict[str, Finding], result: AdjudicationResult | None
) -> FindingSet:
    """Apply per-id bucket overrides to the merged findings.

    Findings with no decision (or when ``result`` is None) keep their
    reviewer-assigned bucket. The set membership is fixed by ``id_map`` — the
    adjudicator cannot add or remove findings — so ``>= high`` preservation is
    guaranteed regardless of what the model returned.
    """
    decisions = result.decisions if result is not None else {}
    out: list[Finding] = []
    for fid, finding in id_map.items():
        bucket = decisions.get(fid)
        if bucket is not None and bucket is not finding.bucket:
            finding = finding.model_copy(update={"bucket": bucket})
        out.append(finding)
    return FindingSet(findings=tuple(out))
