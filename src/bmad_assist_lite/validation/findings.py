"""The structured review-finding record, its severity axis, and the set hash.

Our providers are one-shot subprocesses, so loop convergence has to be
detected from *artifacts* rather than from a live conversation.  That makes a
hashable finding set load-bearing, and nothing upstream supplies one: BMAD's
review emits free-form markdown bullets with no severity field, its finding
``id`` is a per-pass sequential integer rather than a stable identity, and its
only convergence signal is per-bucket counts, which collide — two different
finding sets with the same counts are indistinguishable.  So this module is
ours.  What *is* harvested is the vocabulary: the ``low``/``medium``/``high``
severity ladder, the orthogonal root-cause ``bucket`` axis, and the
deterministic ``followup_review_recommended`` score.

This module deliberately depends on nothing in ``loop/``.  Hashing and
severity scoring are *inputs* to the review loop, not consumers of it, and a
test asserts the independence — the two were once recorded as depending on
each other, which made the group unbuildable.

The normalisation, stated concretely
------------------------------------

"Location-tolerant, whitespace-insensitive, order-insensitive" is a
description, not a specification.  What is actually implemented:

* **Order and duplicates** — the set hash is taken over the *sorted set* of
  per-finding digests, so neither the order the reviewer emitted findings in
  nor a repeated finding can change it.
* **Location** — ``line`` is excluded from the digest and ``anchor`` (the
  symbol or snippet the finding points at) is included.  Embedded ``:123``
  line references inside free text are rewritten to ``:#``.  This is what
  makes the hash survive a fix that shifted everything below it by one line.
* **Text** — NFKC normalised, markdown emphasis and backticks stripped,
  casefolded, whitespace runs collapsed, trailing sentence punctuation
  dropped.
* **Excluded entirely** — ``detail`` and ``source``.  Free-form prose that an
  LLM rewords on every pass would make the hash change while nothing was
  fixed, which is the exact failure the check exists to catch.

**The known consequence, accepted deliberately.** A hash blind to line
numbers is also blind to a fix that only *moved* the defect.  That is the
correct reading rather than a gap: relocating a defect is not fixing it, so
an unchanged ``anchor`` at a new line should read as "same finding".  A fix
that actually changes the flagged code changes the anchor, and the hash moves
with it.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from enum import IntEnum, StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "Bucket",
    "Finding",
    "FindingParseError",
    "FindingSet",
    "Severity",
    "finding_set_hash",
    "followup_review_recommended",
    "parse_findings",
    "render_findings_block",
]

#: Markers the review templates wrap the machine-readable finding block in.
FINDINGS_OPEN_MARKER = "<!-- BMAD-FINDINGS -->"
FINDINGS_CLOSE_MARKER = "<!-- /BMAD-FINDINGS -->"

_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL)
_EMPHASIS_RE = re.compile(r"[*_`~]+")
_LINE_REF_RE = re.compile(r":\d+\b")
_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[.,;:!?\s]+$")


class FindingParseError(ValueError):
    """Review output could not be parsed into findings.

    Raised rather than returning an empty set, because an empty finding set
    simultaneously means "clean review" and "the hash converged" — a swallowed
    parse error would assert both pieces of good news at once.
    """


class Severity(IntEnum):
    """How much a finding matters. Ordered, so a threshold can cull on it."""

    LOW = 0
    MEDIUM = 1
    HIGH = 2

    @classmethod
    def parse(cls, raw: object) -> Severity:
        """Parse a severity name, refusing anything outside the vocabulary."""
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            try:
                return cls[raw.strip().upper()]
            except KeyError:
                pass
        raise FindingParseError(
            f"unknown severity {raw!r}; expected one of "
            f"{[s.name.lower() for s in cls]}"
        )

    @property
    def label(self) -> str:
        """Lowercase name, as it appears on the wire."""
        return self.name.lower()


class Bucket(StrEnum):
    """Root cause, orthogonal to severity.

    Ordered by root cause rather than by severity, so that "how bad is it?"
    and "what kind of thing is it?" stay separable: a high-severity finding
    the intent puts out of scope must not drive a fix iteration.
    """

    INTENT_GAP = "intent_gap"
    BAD_SPEC = "bad_spec"
    PATCH = "patch"
    DEFER = "defer"
    REJECT = "reject"


#: Buckets that may ever trigger a fix iteration. ``defer`` and ``reject``
#: never do, whatever severity they carry.
ACTIONABLE_BUCKETS: frozenset[Bucket] = frozenset(
    {Bucket.INTENT_GAP, Bucket.BAD_SPEC, Bucket.PATCH}
)


def _normalise_path(raw: str) -> str:
    """Normalise a file path to one comparable form."""
    text = raw.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _normalise_text(raw: str) -> str:
    """Collapse a free-text field to its comparable core."""
    text = unicodedata.normalize("NFKC", raw)
    text = _EMPHASIS_RE.sub("", text)
    text = _LINE_REF_RE.sub(":#", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = _TRAILING_PUNCT_RE.sub("", text)
    return text.casefold()


class Finding(BaseModel):
    """One review finding, in the only representation the loop operates on."""

    model_config = ConfigDict(frozen=True)

    #: Fields that participate in the identity hash. Declared here, on the
    #: record, so the hash and the severity cull cannot drift apart.
    HASH_FIELDS: ClassVar[tuple[str, ...]] = (
        "file",
        "anchor",
        "severity",
        "bucket",
        "title",
    )
    #: Fields deliberately kept out of the hash, and why.
    EXCLUDED_FROM_HASH: ClassVar[tuple[str, ...]] = (
        "line",  # shifts when unrelated code above it changes
        "detail",  # free-form prose, reworded every pass
        "source",  # which reviewer saw it is not part of what it is
    )

    file: str
    title: str
    severity: Severity
    bucket: Bucket = Bucket.PATCH
    anchor: str = ""
    line: int | None = None
    detail: str = ""
    source: str = ""

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, value: object) -> object:
        if isinstance(value, str):
            return Severity.parse(value)
        return value

    @property
    def is_actionable(self) -> bool:
        """Whether this finding's bucket can drive a fix iteration at all."""
        return self.bucket in ACTIONABLE_BUCKETS

    def digest(self) -> str:
        """Return this finding's normalised identity digest."""
        parts = [
            _normalise_path(self.file),
            _normalise_text(self.anchor),
            self.severity.label,
            self.bucket.value,
            _normalise_text(self.title),
        ]
        joined = "\x1f".join(parts)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def to_wire(self) -> dict[str, Any]:
        """Render back to the JSON shape the review templates emit."""
        return {
            "file": self.file,
            "line": self.line,
            "severity": self.severity.label,
            "bucket": self.bucket.value,
            "anchor": self.anchor,
            "title": self.title,
            "detail": self.detail,
            "source": self.source,
        }


def finding_set_hash(findings: Sequence[Finding]) -> str:
    """Hash a finding set so that equal sets hash equal, across passes.

    Order-insensitive and duplicate-insensitive by construction: the digest is
    taken over the sorted *set* of per-finding digests.
    """
    digests = sorted({finding.digest() for finding in findings})
    joined = "\n".join(digests)
    return hashlib.sha256(f"findings:{len(digests)}:{joined}".encode()).hexdigest()


def followup_review_recommended(
    findings: Sequence[Finding],
    *,
    medium_weight: int = 3,
    low_weight: int = 1,
    threshold: int = 5,
) -> bool:
    """Decide whether another review pass is worth paying for.

    Deterministic, no extra LLM call.  With a cap of one iteration this is
    what decides whether that iteration is spent **at all**, so the common
    case — a handful of minor findings — costs nothing.

    The formula is harvested from upstream's unattended review step; the
    constants are not, because they were tuned against a different finding
    distribution (four subagent layers at model parity, versus our N vendor
    models under a different prompt).  They are configuration for that reason,
    and the defaults are the upstream starting point, not a target.
    """
    actionable = [f for f in findings if f.is_actionable]
    if any(f.severity is Severity.HIGH for f in actionable):
        return True

    mediums = sum(1 for f in actionable if f.severity is Severity.MEDIUM)
    lows = sum(1 for f in actionable if f.severity is Severity.LOW)
    return (medium_weight * mediums + low_weight * lows) >= threshold


class FindingSet(BaseModel):
    """An immutable set of findings from one review pass."""

    model_config = ConfigDict(frozen=True)

    findings: tuple[Finding, ...] = Field(default=())

    @property
    def is_clean(self) -> bool:
        """Whether the pass produced no findings at all."""
        return not self.findings

    @property
    def hash(self) -> str:
        """The normalised set hash for this pass."""
        return finding_set_hash(self.findings)

    def blocking(self, threshold: Severity) -> tuple[Finding, ...]:
        """Findings at or above ``threshold`` in an actionable bucket.

        Everything below the threshold stays in ``findings`` — it is culled
        from the *loop*, not from the record.
        """
        return tuple(
            f for f in self.findings if f.is_actionable and f.severity >= threshold
        )

    def counts_by_severity(self) -> dict[str, int]:
        """Per-severity totals, for the human-readable triage log."""
        return {
            severity.label: sum(1 for f in self.findings if f.severity is severity)
            for severity in Severity
        }


def _extract_block(text: str) -> str:
    """Pull the machine-readable payload out of a review response."""
    start = text.find(FINDINGS_OPEN_MARKER)
    if start == -1:
        raise FindingParseError(
            "no finding block: the review response contains no "
            f"{FINDINGS_OPEN_MARKER} marker. An absent block is not a clean "
            "review — it means the reviewer did not report in the required form."
        )

    body = text[start + len(FINDINGS_OPEN_MARKER) :]
    end = body.find(FINDINGS_CLOSE_MARKER)
    if end != -1:
        body = body[:end]

    fence = _FENCE_RE.search(body)
    return fence.group("body") if fence else body


def parse_findings(text: str) -> FindingSet:
    """Parse review output into the one structured finding record shape.

    This is the **only** parser that produces :class:`Finding` records, so
    hashing and severity scoring can never disagree about what a finding is.

    Args:
        text: The raw review or synthesis response.

    Returns:
        The parsed :class:`FindingSet`.  An explicitly empty list is a clean
        review; anything unparseable is an error.

    Raises:
        FindingParseError: If no block is present or its payload is not a
            list of well-formed finding objects.

    """
    payload = _extract_block(text).strip()
    if not payload:
        raise FindingParseError("finding block is present but empty")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FindingParseError(f"finding block is not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise FindingParseError(
            f"finding block must be a JSON list, got {type(data).__name__}"
        )

    findings: list[Finding] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise FindingParseError(
                f"finding {index} is a {type(entry).__name__}, expected an object"
            )
        try:
            findings.append(Finding.model_validate(entry))
        except FindingParseError:
            raise
        except Exception as exc:
            raise FindingParseError(f"finding {index} is malformed: {exc}") from exc

    return FindingSet(findings=tuple(findings))


def render_findings_block(findings: Sequence[Finding]) -> str:
    """Render findings back into the wire format, for prompts and artifacts."""
    payload = json.dumps([f.to_wire() for f in findings], indent=2)
    return f"{FINDINGS_OPEN_MARKER}\n```json\n{payload}\n```\n{FINDINGS_CLOSE_MARKER}"
