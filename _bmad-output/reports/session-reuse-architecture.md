# Session Reuse Architecture: Persistent Claude Sessions Across Phases
**Date**: 2026-03-31
**Status**: Research complete -- FEASIBLE

---

## Executive Summary

Instead of 7 separate Claude sessions per story (each recompiling ~12K tokens of context from scratch), maintain a single persistent Claude session for all master LLM phases. Multi-LLM review phases remain separate. Follow-up turns inject only the new information (validator reports, review feedback, QG results) into the ongoing conversation.

Estimated savings: **6-8 minutes per story (~35-45%)** from eliminating context recompilation, re-discovery overhead, and improving fix loop quality.

---

## The Problem

Current flow creates 7 separate sessions per story. Each one:
- Compiles ~12K tokens of context from disk files (~10-20 sec)
- Claude cold-starts and re-reads/re-discovers everything (~30-60 sec of LLM exploration)
- Has ZERO memory of previous phases

The worst impact: when `fix_quality_gate` runs, Claude starts completely fresh with only the failure report. It doesn't know what it built, why it made design decisions, or what approaches it already tried.

---

## The Solution: Persistent Session

```
Current (7 sessions, each fresh):
  Session A: "Here's 12K tokens of context. Create story 3.2."     -> done, discarded
  Session B: "Here's 12K tokens of context. Validate story 3.2."   -> done, discarded
  Session C: "Here's 12K tokens of context. Synthesize validation." -> done, discarded
  Session D: "Here's 12K tokens of context. Implement story 3.2."  -> done, discarded
  Session E: "Here's 12K tokens of context. Review the code."      -> done, discarded
  Session F: "Here's 12K tokens of context. Synthesize review."    -> done, discarded
  Session G: "Here's 12K tokens of context. Fix QG failures."      -> done, discarded

Proposed (1 persistent session + separate review sessions):
  Session M (master, persistent):
    Turn 1: "Create story 3.2 from epic 3." [12K context, one-time]
            -> Claude creates story (has full memory)

    [Multi-LLM validators run in separate sessions -- unchanged]

    Turn 2: "Validation feedback: <2K tokens of reports>. Update story if needed."
            -> Claude already knows the story it wrote, applies fixes instantly

    Turn 3: "Now implement the story."
            -> Claude already knows every detail of the story -- no re-reading

    [Multi-LLM reviewers run in separate sessions -- unchanged]

    Turn 4: "Review feedback: <3K tokens of reports>. Fix issues and run QG commands."
            -> Claude remembers exactly what it built and WHY
            -> Fixes are surgical, not exploratory
```

---

## Why This Is Transformative

| Aspect | Current (7 sessions) | Session Reuse (1 session) |
|--------|---------------------|--------------------------|
| Context compilation | 7 x 12K tokens compiled from disk | **Zero** after turn 1 |
| Claude's knowledge | Re-discovers everything each phase | **Remembers everything** it did |
| Fix quality | Guesses at implementation intent | **Knows** implementation intent |
| Prompt tokens sent | ~84K total (7 x 12K) | ~20K total (12K initial + small follow-ups) |
| Session startup | 7 cold starts (~5-10s each) | 1 cold start + 3 resumes (~2s each) |

Follow-up turns are tiny -- instead of 12K recompiled context, you send ~2-3K of new information (validator reports, review feedback, QG failure output).

---

## Time Impact Estimate

```
Current per story:                              ~18 min
  - 7 x context compilation overhead:           ~2 min
  - 7 x Claude re-reading/re-discovering:       ~3-4 min
  - Fix loop (Claude lacks context):            ~0-10 min

Session reuse per story:                        ~10-12 min
  - 1 x context compilation:                    ~0.3 min
  - 0 x re-reading (Claude remembers):          0 min
  - Fix loop (Claude has full context):         ~0-3 min
  - Follow-up turns (inject reports):           ~0.5 min
```

**Estimated savings: 6-8 minutes per story (~35-45%)**

---

## Technical Approach: Confirmed Feasible

### Option A: claude-agent-sdk with resume (RECOMMENDED)

The SDK (v0.1.48) natively supports session continuation. Three relevant fields on `ClaudeAgentOptions`:

| Field | Type | Purpose |
|-------|------|---------|
| `continue_conversation` | `bool` | Resume most recent session in current directory |
| `resume` | `str \| None` | Resume a specific session by UUID |
| `fork_session` | `bool` | Branch from a session (new ID, preserves history) |

**Usage pattern for bmad-assist-lite:**

```python
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

# Turn 1: Create story (fresh session)
session_id = None
options = ClaudeAgentOptions(
    model="opus",
    permission_mode="acceptEdits",
    cwd=worktree_path,
)
async for message in query(prompt=initial_prompt, options=options):
    if isinstance(message, ResultMessage):
        session_id = message.session_id  # Capture UUID for later

# ... multi-LLM validation runs in separate sessions ...

# Turn 2: Resume same session with validation feedback
options = ClaudeAgentOptions(
    resume=session_id,   # <-- THIS IS THE KEY CHANGE
    model="opus",
    permission_mode="acceptEdits",
    cwd=worktree_path,
)
async for message in query(prompt=validation_feedback, options=options):
    # Claude has FULL context from Turn 1
    ...

# Turn 3: Resume again for implementation
options = ClaudeAgentOptions(
    resume=session_id,
    model="opus",
    permission_mode="acceptEdits",
    cwd=worktree_path,
)
async for message in query(prompt="Now implement the story.", options=options):
    # Claude remembers both the story it created AND the validation feedback
    ...
```

**What this means for the provider layer:**
- Current `ClaudeSDKProvider._do_invoke()` creates fresh options each time
- Change: add optional `session_id` parameter
- Capture `session_id` from `ResultMessage` at end of each invocation
- Pass `resume=session_id` in subsequent invocations when session reuse is active

### Option B: Claude Code CLI with --resume (ALSO WORKS)

```bash
# Turn 1: fresh session with a name
claude -p "Create story 3.2..." --name "story-3.2"

# Turn 2: resume by name
claude -p "Validation says: ... Fix the story." --resume "story-3.2"

# Turn 3: resume again
claude -p "Now implement it." --resume "story-3.2"
```

CLI flags confirmed:
- `--continue` / `-c` -- resume most recent session in cwd
- `--resume` / `-r` -- resume by name or session ID
- `--session-id` -- force specific UUID
- `--fork-session` -- branch session (new ID, preserves history)
- `--name` / `-n` -- name session for later resumption
- `--no-session-persistence` -- disable persistence (for ephemeral runs)

### Option C: Anthropic API direct (NOT RECOMMENDED)

Loses Claude Code's built-in tools (Read, Write, Edit, Bash, Grep, Glob). Not worth the tradeoff.

---

## Research Findings (Complete)

- [x] **claude-agent-sdk supports session continuation** -- via `ClaudeAgentOptions(resume=session_id)` and `continue_conversation=True`
- [x] **Session ID captured from `ResultMessage.session_id`** -- returned at end of every query
- [x] **`claude -p --resume` works for programmatic usage** -- confirmed with named sessions and UUIDs
- [x] **Context window: automatic compaction on resume** -- Claude auto-clears older tool outputs, then summarizes if needed. Persistent rules should go in CLAUDE.md (not conversation) to survive compaction
- [x] **Session state in git worktrees: WORKS** -- sessions stored per-cwd at `~/.claude/projects/<encoded-cwd>/`. Each worktree has a unique cwd, so sessions are naturally isolated per story
- [x] **Gemini: NO session resumption** -- Gemini CLI does not support session continuation. Session reuse is Claude-only. Gemini would need the existing fresh-session pattern
- [x] **fork_session works in print mode** -- `--fork-session` can be combined with `--resume` for branched exploration

### SDK Session Management Utilities

```python
from claude_agent_sdk import list_sessions, get_session_messages

# List sessions in a worktree directory
sessions = list_sessions(directory=worktree_path, limit=10)

# Read full conversation history from a session
messages = get_session_messages(session_id, directory=worktree_path)
```

### Key Constraints

1. **Session files are per-cwd** -- `~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Worktree isolation is natural (each worktree = different cwd = separate session store).
2. **Compaction risk** -- Long sessions may auto-compact, losing early details. Mitigate by putting persistent rules in CLAUDE.md and keeping sessions focused.
3. **Claude-only** -- Gemini master provider cannot use session reuse. This optimization only applies when Claude is the master provider.
4. **`--session-id` not in SDK** -- The `--session-id` CLI flag (force UUID) is not a first-class `ClaudeAgentOptions` field but can be passed via `extra_args`.

---

## Relationship to Other Optimizations

Session reuse **supersedes** several previously planned optimizations:

| Previous Plan | Status with Session Reuse |
|--------------|--------------------------|
| Prompt pre-compilation | **Unnecessary** -- no prompts to compile after turn 1 |
| Agentic combined phases (`agentic_dev` handler) | **Partially replaced** -- session continuity solves the context problem more elegantly |
| Implementation summary protocol | **Unnecessary** -- Claude already remembers what it built |
| fix_quality_gate context carrying | **Solved automatically** -- Claude has full memory |

Multi-LLM review phases and parallel QG commands remain independent optimizations that stack with session reuse.

---

## Parallel Optimizations (Stack with Session Reuse)

These are independent improvements that combine with session reuse:

1. **QG commands in parallel** (~1 min saved) -- lint, typecheck, build, test are independent
2. **Code review parallel with QG** (~1-2 min saved) -- both read-only after dev
3. **Eliminate validation phases** (~4-6 min saved) -- methodology decision, needs data

Combined with session reuse:
```
Current:                      ~18 min
+ Session reuse:              ~10-12 min  (-35-45%)
+ Parallel QG + review||QG:   ~8-10 min  (-45-55%)
+ Drop validation (if safe):  ~5-7 min   (-60-70%)
```

---

## Implementation Impact

### Changes to `ClaudeSDKProvider` (`providers/claude_sdk.py`)

Minimal changes needed:

1. Add `session_id: str | None = None` parameter to `_do_invoke()`
2. When `session_id` is provided, set `options.resume = session_id`
3. Capture `ResultMessage.session_id` from the response stream
4. Return session_id alongside `ProviderResult` (new field or separate return)

### Changes to `BaseHandler` (`loop/handlers/base.py`)

1. Add session tracking: `self._session_id: str | None = None`
2. After `invoke_provider()`, store the returned session_id
3. On next `invoke_provider()`, pass the stored session_id if session reuse is enabled

### New: Session-Aware Handler Base

A `SessionAwareHandler` subclass that manages session lifecycle across multiple internal invocations (create → validate synthesis → dev → review synthesis → QG fix loop).

### Config: Opt-in

```yaml
# bmad-assist-lite.yaml
session_reuse:
  enabled: true  # default false for backward compatibility
```

### No Changes Needed

- `parallel/orchestrator.py` -- subprocess spawning unchanged
- `providers/gemini.py` -- Gemini doesn't support sessions, works as-is
- `loop/runner.py` -- phase iteration unchanged
- Multi-LLM handlers -- always use fresh sessions (different models)
