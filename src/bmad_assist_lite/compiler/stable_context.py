"""Build the epic's stable, unfiltered context as a cacheable system-prompt block.

The block is composed of the artifacts that do NOT change across the phases or
stories of an epic — project context, PRD, UX, architecture, and the *full*
epic — loaded UNFILTERED and in a deterministic order, with no timestamps or
absolute paths in the text. That makes it byte-identical every time it is
compiled within an epic, so when it rides as an appended system prompt (see
``providers/claude_sdk.py``) its bytes form a warm prompt-cache prefix shared
across every phase and story. Story files are deliberately excluded — they are
the per-story volatile content and belong in the user message.
"""

from __future__ import annotations

import logging

from bmad_assist_lite.compiler.discovery import discover_files, load_file_contents
from bmad_assist_lite.compiler.types import CompilerContext, WorkflowIR

logger = logging.getLogger(__name__)

#: Stable, epic-invariant inputs, loaded FULL (never SELECTIVE/filtered). The
#: epic is loaded whole — NOT trimmed to the current story — so it is identical
#: across the epic's stories. Keys are sorted at emit time for a stable order.
STABLE_SUPERSET_PATTERNS: dict[str, dict[str, str]] = {
    "project_context": {
        "pattern": "{project-root}/**/project-context.md",
        "strategy": "FULL_LOAD",
    },
    "prd_file": {"pattern": "{planning_artifacts}/*prd*.md", "strategy": "FULL_LOAD"},
    "ux_file": {"pattern": "{project-root}/**/ux*.md", "strategy": "FULL_LOAD"},
    "architecture_file": {
        "pattern": "{project-root}/**/architecture*.md",
        "strategy": "FULL_LOAD",
    },
    "epic_file": {"sharded": "{project-root}/**/epic-{epic_num}.md", "strategy": "FULL_LOAD"},
}


def build_stable_system_prompt(context: CompilerContext) -> str | None:
    """Return the epic's stable context as one deterministic text block, or None.

    None is returned when the flag path finds nothing to load or discovery fails;
    the caller then invokes with the CLI's default system prompt unchanged.
    """
    ir = WorkflowIR(
        name="_stable_superset",
        config_path=context.project_root,
        instructions_path=context.project_root,
        template_path=None,
        validation_path=None,
        raw_config={"input_file_patterns": STABLE_SUPERSET_PATTERNS},
        raw_instructions="",
    )
    stable_ctx = CompilerContext(
        project_root=context.project_root,
        output_folder=context.output_folder,
        project_knowledge=context.project_knowledge,
        resolved_variables=dict(context.resolved_variables),
        workflow_ir=ir,
    )

    try:
        discover_files(stable_ctx)
        load_file_contents(stable_ctx)
    except Exception:
        logger.warning("stable_prefix: failed to load stable superset", exc_info=True)
        return None

    parts: list[str] = []
    for key in sorted(stable_ctx.file_contents):
        content = stable_ctx.file_contents[key]
        if content and content.strip():
            parts.append(f'<stable-artifact name="{key}">\n{content}\n</stable-artifact>')

    # L3: the epic-knowledge brief rides the same cached region, but goes LAST.
    # It is the one part that changes between stories (it accumulates), so placing
    # it after the epic-invariant artifacts confines the cross-story cache
    # invalidation to the tail -- the invariant bulk before it stays
    # byte-identical across the epic's stories and keeps its prefix-cache hits.
    brief = _epic_knowledge_block(context)
    if brief:
        parts.append(brief)

    if not parts:
        return None

    return "<stable-context>\n" + "\n".join(parts) + "\n</stable-context>\n"


def _epic_knowledge_block(context: CompilerContext) -> str | None:
    """Return the epic-knowledge brief as a stable-artifact block, or None.

    Gated on ``epic_knowledge.enabled`` (read defensively -- the compiler runs in
    tests and probes where no config is loaded, which means "off"). Returns None
    when disabled, the epic is unknown, or no brief has been written yet.
    """
    try:
        from bmad_assist_lite.core.config import get_config

        if not get_config().epic_knowledge.enabled:
            return None
    except Exception:
        return None

    epic_num = context.resolved_variables.get("epic_num")
    if epic_num is None:
        return None

    from bmad_assist_lite.core.epic_knowledge import read_epic_knowledge

    brief = read_epic_knowledge(epic_num)
    if not brief:
        return None
    return f'<stable-artifact name="epic_knowledge">\n{brief}\n</stable-artifact>'
