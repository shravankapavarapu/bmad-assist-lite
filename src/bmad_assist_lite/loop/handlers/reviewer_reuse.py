"""L2 (goal-run5): reviewer-lane self-resume across review rounds.

A reviewer or synthesis lane keeps its own round-1 Claude session id keyed by
``story#phase#index#provider#model``. A round-2 re-review then resumes THAT
session (re-reading only the fix diff) instead of recompiling the full story
context from scratch.

The story id is baked into the key, so a session can never be resumed across
stories, across phases, or across reviewer lanes. That makes the F-13
reviewer-independence rule *structural*: a lane can only ever resume a session
it wrote itself, and the dev/master session id is never written here -- so no
reviewer can inherit the author's reasoning. Claude-only (resume is a Claude CLI
capability); behind ``session_reuse.reviewer_self_resume`` (default OFF), so an
unset flag is a no-op on every path below.
"""

from __future__ import annotations

import logging
from typing import Any

from bmad_assist_lite.core.config import Config
from bmad_assist_lite.core.state import State

logger = logging.getLogger(__name__)

_CLAUDE = "claude"


def lane_key(
    story_id: str | None,
    phase: str,
    index: int,
    provider: str,
    model: str | None,
) -> str:
    """Return the stable holder key for one reviewer lane within one story."""
    return f"{story_id}#{phase}#{index}#{provider}#{model or 'default'}"


def resume_id_for(
    state: State,
    config: Config,
    *,
    provider: str,
    key: str,
) -> str | None:
    """Session id this lane should resume, or None to start cold.

    None on round 1 (nothing stored for the key yet), the stored round-1 id on a
    later round. Gated on the flag and Claude-only.
    """
    if not config.session_reuse.reviewer_self_resume:
        return None
    if provider != _CLAUDE:
        return None
    return state.reviewer_session_ids.get(key)


def capture_session(
    state: State,
    config: Config,
    *,
    story_id: str | None,
    provider: str,
    key: str,
    result: Any,
) -> None:
    """Store this lane's returned session id for a later round to resume.

    No-op unless the flag is on, the provider is Claude, and the call returned a
    session id. Stale-story entries are pruned so the holder stays bounded to the
    current story's lanes (a past story's session is never resumed again).
    """
    if not config.session_reuse.reviewer_self_resume:
        return
    if provider != _CLAUDE:
        return
    session_id = getattr(result, "provider_session_id", None)
    if not session_id:
        return

    prefix = f"{story_id}#"
    stale = [k for k in state.reviewer_session_ids if not k.startswith(prefix)]
    for k in stale:
        del state.reviewer_session_ids[k]

    state.reviewer_session_ids[key] = session_id
    logger.debug(
        "reviewer self-resume: stored session %s for lane %s",
        session_id,
        key,
    )
