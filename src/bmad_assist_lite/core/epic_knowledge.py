"""L3 (goal-run5): a bounded, curated epic-knowledge brief across an epic's stories.

At each story's completion the master writes/updates a small
``.bmad-assist-lite/epic-knowledge-<epic>.md`` brief -- architectural decisions,
gotchas, file-map deltas, conventions established. Later stories in the epic load
it inside the stable, cached system-prompt region (see
``compiler/stable_context.py``), so they start "smart" without recompiling prior
story transcripts. This is the *artifact* form of epic accumulation; a naive
epic-scoped transcript resume is explicitly rejected (it re-carries every prior
story's history each turn -- the measured fix-phase regression at scale).

The brief is:
  * bounded -- hard-capped at ``epic_knowledge.max_chars`` (~2k tokens),
  * durable -- kept beside ``state.yaml`` under ``.bmad-assist-lite/`` so the
    story-transition cache sweep (which only clears ``cache/``) never deletes it,
  * inspectable -- plain markdown you can read to see what the system learned,
  * best-effort -- a failed write never fails an already-completed story.

Off by default (``epic_knowledge.enabled``).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from bmad_assist_lite.core.config import Config
from bmad_assist_lite.core.paths import get_paths
from bmad_assist_lite.core.state import State

logger = logging.getLogger(__name__)

_BRIEF_STEM = "epic-knowledge"

#: Phase name the writer's provider call is recorded under in phase-metrics.jsonl.
WRITE_EPIC_KNOWLEDGE_PHASE = "write_epic_knowledge"

_TRUNCATION_MARKER = "\n\n<!-- epic-knowledge truncated at cap -->\n"


def epic_knowledge_path(epic_num: str | int | None) -> Path | None:
    """Return the epic-scoped brief path, or None if unavailable.

    Kept directly under ``.bmad-assist-lite/`` (not ``cache/``) so it survives
    every story transition -- a brief that the next story's sweep deletes would
    be useless. Returns None when the epic is unknown or paths are not yet
    initialised (tests, early startup), so callers degrade to "no brief".
    """
    if epic_num is None:
        return None
    try:
        paths = get_paths()
    except RuntimeError:
        return None
    return paths.bmad_assist_dir / f"{_BRIEF_STEM}-{epic_num}.md"


def read_epic_knowledge(epic_num: str | int | None) -> str | None:
    """Return the current brief text for the epic, or None if absent/empty."""
    path = epic_knowledge_path(epic_num)
    if path is None or not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("epic_knowledge: could not read %s", path, exc_info=True)
        return None
    return text or None


def _build_writer_prompt(epic_num: str | int, story_id: str, existing: str | None) -> str:
    """Compose the bounded write/update prompt handed to the master.

    The master reads the just-completed story's artifacts itself (it is invoked
    with read-only tools in the project directory), then emits ONLY the updated
    brief markdown -- the caller writes the file, so the model needs no write
    tools. Merge, don't append blindly: the brief is bounded, so stale or
    duplicated entries must be revised out.
    """
    prior = (
        f"The current epic-knowledge brief (revise and extend it -- do NOT simply "
        f"append):\n\n{existing}\n"
        if existing
        else "There is no epic-knowledge brief yet; create the first one.\n"
    )
    return (
        f"You are curating the epic-knowledge brief for epic {epic_num}. "
        f"Story {story_id} has just completed.\n\n"
        f"{prior}\n"
        f"Read the artifacts for story {story_id} in this project (the story file, "
        f"the implemented changes / dev output, and the code-review findings) and "
        f"produce an UPDATED brief that a developer starting the NEXT story in this "
        f"epic would want to have already read. Capture only what carries forward:\n"
        f"  - Decisions: architectural/design choices made and why.\n"
        f"  - Gotchas: traps, surprises, non-obvious constraints hit during the story.\n"
        f"  - File map: where key modules/handlers live and any structural deltas.\n"
        f"  - Conventions: patterns/idioms this epic has settled on.\n\n"
        f"Rules:\n"
        f"  - Merge with the existing brief; revise or drop entries that no longer "
        f"help. Keep it curated, not cumulative.\n"
        f"  - Be specific and terse. No narration of THIS story's process -- only "
        f"durable, forward-looking knowledge.\n"
        f"  - Output ONLY the brief as GitHub-flavoured markdown, starting with a "
        f"'# Epic {epic_num} — knowledge brief' heading. No preamble, no code fences "
        f"around the whole thing.\n"
    )


def _normalize_brief(text: str) -> str:
    """Trim model chatter so the cached brief is clean markdown only.

    Smaller models sometimes emit a preamble ("I'll help you create the
    brief... Let me find the artifacts.") -- sometimes with no newline before
    the heading ("...artifacts.# Epic 3", observed with haiku) -- or wrap the
    whole thing in a ``` fence, despite the prompt asking for the brief alone.
    The brief must start with a heading, so everything before the FIRST markdown
    heading (``#``..``######`` + space, which skips ``#5``-style prose since it
    requires the trailing space) is dropped, wherever that heading falls. A
    surrounding code fence is stripped first. If no heading is found the text is
    returned stripped, and the caller's empty-brief guard decides what to do.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    match = re.search(r"#{1,6}[ \t]", text)
    if match:
        return text[match.start() :].strip()
    return text


def _bound(text: str, max_chars: int) -> str:
    """Hard-cap the brief so accumulation can never defeat context economy."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(_TRUNCATION_MARKER))
    return text[:keep].rstrip() + _TRUNCATION_MARKER


def write_epic_knowledge_after_story(config: Config, project_path: Path, state: State) -> None:
    """Write/update the epic-knowledge brief at story completion (L3 hook).

    Gated on ``epic_knowledge.enabled``; a no-op otherwise. Best-effort: any
    failure is logged and swallowed, because the story is already done and an
    experimental, default-OFF accumulation lever must never turn a passing story
    into a failed run. The master's write turn is recorded in phase-metrics.jsonl
    under ``write_epic_knowledge`` so its cost is measurable like any phase.
    """
    if not config.epic_knowledge.enabled:
        return

    epic_num = state.current_epic
    story_id = state.current_story
    if epic_num is None or story_id is None:
        logger.debug("epic_knowledge: no epic/story in state; skipping brief write")
        return

    path = epic_knowledge_path(epic_num)
    if path is None:
        logger.debug("epic_knowledge: paths unavailable; skipping brief write")
        return

    try:
        from bmad_assist_lite.core.config import get_phase_timeout
        from bmad_assist_lite.core.phase_metrics import phase_metrics_context
        from bmad_assist_lite.providers import get_provider
        from bmad_assist_lite.providers.base import READ_ONLY_TOOLS, write_progress

        existing = read_epic_knowledge(epic_num)
        prompt = _build_writer_prompt(epic_num, story_id, existing)

        provider = get_provider(config.providers.master.provider)
        # Master parity: the brief is written at the session's capability, never a
        # routed cheaper model (write_epic_knowledge is a NON_ROUTABLE_LLM phase).
        model = config.providers.master.model
        timeout = get_phase_timeout(config, WRITE_EPIC_KNOWLEDGE_PHASE)

        write_progress(f"  Updating epic-knowledge brief (epic {epic_num})...")
        with phase_metrics_context(story_id=story_id, phase=WRITE_EPIC_KNOWLEDGE_PHASE):
            result = provider.invoke(
                prompt,
                model=model,
                timeout=timeout,
                cwd=project_path,
                allowed_tools=list(READ_ONLY_TOOLS),
                effort=config.providers.master.effort,
            )
        brief = _normalize_brief(provider.parse_output(result))
        if not brief:
            logger.warning("epic_knowledge: master returned an empty brief; keeping prior")
            return

        brief = _bound(brief, config.epic_knowledge.max_chars)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(brief, encoding="utf-8")
        os.replace(tmp, path)
        logger.info(
            "epic_knowledge: wrote brief for epic %s after story %s (%d chars)",
            epic_num,
            story_id,
            len(brief),
        )
        write_progress(f"  Epic-knowledge brief updated ({len(brief)} chars)")
    except Exception:
        # Never let an experimental accumulation lever fail a completed story.
        logger.warning(
            "epic_knowledge: failed to write brief for epic %s after story %s",
            epic_num,
            story_id,
            exc_info=True,
        )
