# Multi-Provider Session Architecture: Supporting Session Reuse Across All Providers
**Date**: 2026-04-01
**Status**: Research complete

---

## Executive Summary

Session reuse is NOT Claude-only. All three major coding CLIs (Claude Code, OpenAI Codex CLI, Gemini CLI) support session resume natively. Additionally, the newer APIs from both OpenAI and Google provide server-side session state. This means the session reuse optimization can be provider-agnostic with the right abstraction layer.

For providers that DON'T support native sessions (Ollama, older APIs), we can simulate session continuity by maintaining and replaying summarized conversation history. The handler layer shouldn't need to know which strategy is being used.

---

## Research Findings: Provider Session Capabilities

### CLI Tools (Used by bmad-assist-lite)

| CLI Tool | Session Resume | Non-Interactive Mode | Resume Command |
|----------|---------------|---------------------|----------------|
| **Claude Code** | YES | `claude -p "prompt"` | `claude -p "prompt" --resume <session-id>` |
| **OpenAI Codex CLI** | YES | `codex exec "prompt"` | `codex exec resume --last "prompt"` or `codex exec resume <id>` |
| **Gemini CLI** | YES | `gemini -p "prompt"` | `gemini --resume <id>` or `gemini -r latest` |
| **OpenCode** | YES | `opencode run "prompt"` | `opencode --continue` or `opencode --session <id>` |

All four CLIs store sessions locally (Claude: JSONL in `~/.claude/projects/`, Codex: SQLite in `~/.codex/`, Gemini: per-project auto-save, OpenCode: local DB with export/import) and support resuming by ID or "most recent."

### APIs (For SDK-Based Providers)

| Provider | API | Native Server-Side State | Mechanism |
|----------|-----|-------------------------|-----------|
| **OpenAI** | Responses API | **YES** | `previous_response_id` chaining (30-day TTL) |
| **OpenAI** | Conversations API | **YES** | Durable `conversation_id` (no TTL) |
| **OpenAI** | Chat Completions | **NO** | Caller manages messages array |
| **OpenAI** | Assistants API | **YES** (deprecated Aug 2026) | Thread IDs |
| **Google** | Interactions API (beta) | **YES** | `previous_interaction_id` |
| **Google** | generateContent | **NO** | Caller manages contents array |
| **Mistral** | Conversations (beta) | **YES** | `conversation_id` |
| **Mistral** | Chat Completions | **NO** | Caller manages messages array |
| **Cohere** | V1 Chat | **YES** | `conversation_id` |
| **Cohere** | V2 Chat | **NO** | Removed in V2 |
| **Ollama** | /api/chat | **NO** | Caller manages messages array |
| **Z.AI (GLM-5)** | Chat Completion | **NO** | Caller manages messages array |
| **Z.AI (GLM-5)** | Agent Conversation | **LIMITED** | `conversation_id` (agents only, not general chat) |
| **Z.AI (GLM-5)** | via Claude Code CLI | **YES (inherited)** | Claude Code manages sessions; GLM is the backend model |

### Key Insight

The industry is converging on server-side session state. OpenAI's Responses API (`previous_response_id`), Google's Interactions API (`previous_interaction_id`), and Mistral's Conversations API all follow the same pattern. The older Chat Completions pattern (caller manages history) is being superseded.

---

## OpenAI Codex CLI: Provider Analysis

Codex CLI is OpenAI's direct competitor to Claude Code. Written in Rust.

**Install:** `npm install -g @openai/codex` or `brew install --cask codex`

### Feature Comparison

| Feature | Codex CLI | Claude Code | Gemini CLI |
|---------|-----------|-------------|------------|
| Non-interactive mode | `codex exec "prompt"` | `claude -p "prompt"` | `gemini -p "prompt"` |
| Session resume | `codex exec resume --last` | `claude --resume <id>` | `gemini --resume <id>` |
| Full-auto approval | `--full-auto` | `permission_mode="acceptEdits"` | N/A (auto by default) |
| JSONL output | `--json` | `--output-format json` | `--output-format stream-json` |
| Config file | `AGENTS.md` | `CLAUDE.md` | `GEMINI.md` |
| Sandbox | Apple Seatbelt / Docker | Permission modes | None |
| Multi-provider | YES (openai, gemini, ollama, mistral, etc.) | NO (Claude only) | NO (Gemini only) |
| Tool access | File read/write, bash | Read, Write, Edit, Bash, Grep, Glob | File read/write, bash |
| Session storage | SQLite (`~/.codex/`) | JSONL (`~/.claude/projects/`) | Per-project auto-save |

### Adding a Codex Provider

A `CodexProvider` would follow the exact same subprocess pattern as the existing `GeminiProvider`:
- Spawn `codex exec` as subprocess with `--full-auto --json` flags
- Parse JSONL event stream from stdout (same approach as Gemini's `stream-json`)
- Capture session ID from session event in the stream
- For resume: `codex exec resume <session-id>` with optional follow-up prompt

The implementation effort is comparable to `GeminiProvider` since the patterns are nearly identical.

---

## Z.AI GLM-5: Provider Analysis

### Overview

GLM-5 is Z.AI's (Zhipu AI) large language model family. Models include GLM-5, GLM-5-Turbo, GLM-5.1, GLM-4.7, GLM-4.5 variants, and GLM-4-32B. Z.AI also offers vision models (GLM-5V-Turbo).

**API Base URL:** `https://api.z.ai/api/coding/paas/v4`

### Session Management: Three Access Paths

**Path 1: Direct Chat Completion API -- NO session state**

The Chat Completion endpoint (`POST /paas/v4/chat/completions`) follows the same stateless pattern as OpenAI's Chat Completions:
- Client sends full `messages` array each request
- No `session_id`, `conversation_id`, or `previous_response_id` parameter
- Only `request_id` for request deduplication (not session management)
- Multi-turn conversations require client-managed message history

This places GLM-5's direct API in the **stateless** category alongside Ollama and older Chat Completions APIs.

**Path 2: Agent Conversation API -- LIMITED session state**

Z.AI has a separate Agent Conversation API (`POST /api/v1/agents/conversation`) that supports `conversation_id` for querying and resuming conversation history. However, this is limited to specific agent types (documented for "slides_glm_agent") and not a general-purpose session management mechanism for the chat API.

**Path 3: Via Claude Code CLI -- INHERITS session resume**

This is the most relevant path for bmad-assist-lite. GLM-5 can be used as a **backend model substitute** for Claude Code by setting:
- `ANTHROPIC_AUTH_TOKEN` = Z.AI API key
- `ANTHROPIC_BASE_URL` = `https://api.z.ai/api/anthropic`

With this configuration, Claude Code's interface shows Claude model names but GLM models execute underneath. The key implication: **Claude Code manages sessions locally**, so session resume works regardless of the backend model. The `--resume` flag continues the local session, and the conversation history is replayed to GLM-5 via the messages array.

This means GLM-5 gets session resume "for free" when accessed through Claude Code CLI -- no API-level session support needed.

### GLM-5 Capabilities Relevant to bmad-assist-lite

| Feature | Status |
|---------|--------|
| Chat Completion API | OpenAI-compatible format (messages array) |
| Tool/Function Calling | YES -- function calls, web search, retrieval |
| Streaming | YES -- SSE with `stream: true` |
| Structured Output | YES -- `response_format: {type: "json_object"}` |
| Vision/Multimodal | YES -- via GLM-5V-Turbo (images, video, audio, files) |
| Thinking/Reasoning | YES -- `thinking` parameter with `clear_thinking` option |
| Max Context | 131,072 tokens |
| Native Session State | NO (direct API) / YES (via Claude Code CLI) |

### Integration Strategy for bmad-assist-lite

Two approaches:

1. **Via Claude Code CLI (recommended)**: Configure GLM-5 as the backend model. Session resume works through Claude Code's local session management. No new provider needed -- uses existing `ClaudeSDKProvider` with GLM-5 behind the proxy. Minimal effort.

2. **Direct API provider**: Create a `GLMProvider` following the OpenAI Chat Completions pattern. Since the API is stateless, would use the **replay strategy** from SessionManager. Medium effort. Would allow using GLM-5 independently of Claude Code.

### Open Source Status

GLM-4 was released as open source. GLM-5's licensing is not explicitly documented on docs.z.ai. The DevPack/coding plan is a commercial subscription service that integrates with multiple coding tools (Claude Code, Roo Code, Kilo Code, Cline, OpenCode, and others).

---

## OpenCode: Provider Analysis

### Overview

OpenCode is an open-source (MIT license) AI coding agent CLI by Anomaly (github.com/anomalyco/opencode, ~135k stars). Available as a terminal TUI, desktop app (beta), and IDE extension. Supports 75+ LLM providers through Models.dev, including local models. Provider-agnostic by design.

### Session Management -- YES, Full Support

OpenCode has comprehensive session management:

| Feature | Command |
|---------|---------|
| Resume last session | `opencode --continue` or `-c` |
| Resume specific session | `opencode --session <id>` or `-s <id>` |
| Fork a session | `opencode --fork` (branch from current, new ID) |
| Non-interactive mode | `opencode run "prompt"` (headless, single prompt) |
| Headless server | `opencode serve` (API server, no TUI) |
| Web interface | `opencode web` (headless + browser UI) |
| List sessions | `opencode session list` |
| Export/import sessions | `opencode export` / `opencode import` |
| Share sessions | `/share` (generates shareable link) |

### Feature Comparison with Other CLIs

| Feature | OpenCode | Claude Code | Codex CLI | Gemini CLI |
|---------|----------|-------------|-----------|------------|
| Non-interactive | `opencode run "prompt"` | `claude -p "prompt"` | `codex exec "prompt"` | `gemini -p "prompt"` |
| Session resume | `--continue` / `--session <id>` | `--resume <id>` | `exec resume --last` | `--resume <id>` |
| Session fork | `--fork` | `--fork-session` | N/A | N/A |
| Headless server | `opencode serve` | N/A | N/A | N/A |
| Session export | `opencode export/import` | N/A | N/A | N/A |
| Multi-provider | YES (75+ via Models.dev) | NO (Claude only) | YES (openai, gemini, ollama, etc.) | NO (Gemini only) |
| Config file | `AGENTS.md` | `CLAUDE.md` | `AGENTS.md` | `GEMINI.md` |
| MCP support | YES (`opencode mcp`) | YES (`.mcp.json`) | N/A | N/A |
| LSP integration | YES (auto-loaded) | NO | NO | NO |
| License | MIT | Proprietary | MIT | Proprietary |

### Unique Capabilities

- **Headless server mode** (`opencode serve`): Runs as an API server that can be attached to remotely via `opencode attach <url>`. Useful for CI/CD or remote development.
- **Session export/import**: Portable session data transfer between machines.
- **Custom agents**: `opencode agent create/list` for creating specialized agent configurations.
- **Plan mode**: Toggle via Tab key -- suggests implementations without making changes.

### Integration Strategy for bmad-assist-lite

OpenCode could be added as a provider using the same subprocess pattern as Gemini/Codex:
- Non-interactive mode: `opencode run "prompt"`
- Session resume: `opencode --continue` or `--session <id>`
- Multi-provider support means OpenCode could route to GLM-5, Claude, OpenAI, or local models

The session management is on par with Claude Code, making it a strong candidate for the native session resume strategy.

---

## Architectural Design: Provider-Agnostic Session Management

### The Problem

Three categories of providers:

1. **CLI-based with native session resume** (Claude, Codex, Gemini CLI)
2. **API-based with server-side state** (OpenAI Responses API, Gemini Interactions API)
3. **Stateless** (Chat Completions, Ollama, older APIs)

The handler layer should say "continue this conversation" without knowing which category the provider falls into.

### Design: Two-Tier Abstraction

```
Handler Layer (session-agnostic)
  |
  |  "invoke with session context"
  v
SessionManager (strategy selection)
  |
  |-- NativeSessionStrategy (CLI resume / API chaining)
  |     - Claude: pass resume=session_id to ClaudeAgentOptions
  |     - Codex: codex exec resume <id>
  |     - Gemini CLI: gemini --resume <id>
  |     - OpenAI Responses API: previous_response_id=response_id
  |     - Gemini Interactions API: previous_interaction_id=id
  |
  |-- ReplaySessionStrategy (caller-managed history)
  |     - Maintains conversation summary across turns
  |     - Injects summary as context prefix on each invocation
  |     - Trims/summarizes old turns when approaching token limit
  |     - Works with: Chat Completions, Ollama, any stateless API
  |
  v
Provider Layer (unchanged for existing invoke() calls)
```

### SessionCapable Protocol

A `SessionCapable` protocol that providers opt into. Providers that implement it expose:
- `invoke_with_session(prompt, session_id=None)` -- invoke with optional resume
- `last_session_id` property -- returns the session ID captured from the most recent invocation

Providers that DON'T implement this protocol continue working through the standard `invoke()` method. The SessionManager detects the capability at runtime and selects the appropriate strategy.

### SessionManager Responsibilities

The SessionManager sits between the handler and provider layers:

- **For native providers**: Captures session ID after each invocation, passes it as `session_id` on the next call. Zero overhead.
- **For stateless providers**: Records each turn's prompt and a rule-based summary of the response. On subsequent turns, injects a `<conversation-history>` block into the prompt containing summaries of prior turns. Manages a token budget for history to prevent prompt bloat.
- **For both**: Provides `reset()` to start a fresh session (new story), tracks turn count, handles crash recovery by persisting session ID to state.yaml.

### Replay Strategy: How Stateless Providers Maintain Context

Rather than replaying full conversation history (which grows unbounded), the SessionManager generates compact summaries after each turn using rule-based extraction:

- **Files modified**: regex extraction of file paths from response
- **Key decisions**: extraction of "chose X because Y" patterns
- **Completion signals**: "all tests pass", "implemented", "created" etc.

These summaries are injected as a `<conversation-history>` prefix in subsequent prompts, giving the stateless provider enough context to continue meaningfully without re-reading everything.

**Token budget management**: Oldest turn summaries are trimmed first when approaching the configured `max_history_tokens` limit. Rule-based (no extra LLM calls).

---

## Gemini CLI: Already Supports Session Resume

Important finding: The existing `GeminiProvider` uses the Gemini CLI, which ALREADY supports `--resume`. This means session reuse works for BOTH existing providers (Claude AND Gemini) with minimal changes -- just adding the `--resume <id>` flag to the subprocess command and capturing the session ID from the output stream.

---

## Provider Capability Matrix

| Provider | Type | Session Resume | Strategy | Effort |
|----------|------|---------------|----------|--------|
| **Claude** (existing) | CLI + SDK | YES | Native: `ClaudeAgentOptions(resume=id)` | Low |
| **Gemini** (existing) | CLI | YES | Native: `gemini --resume <id>` | Low |
| **Codex** (new) | CLI | YES | Native: `codex exec resume <id>` | Medium |
| **OpenAI API** (new) | SDK | YES | Native: `previous_response_id` | Medium |
| **Ollama** (new) | API | NO | Replay: summarized history injection | Medium-High |
| **Mistral** (new) | API | YES (beta) | Native: `conversation_id` | Medium |
| **GLM-5 via Claude Code** (new) | CLI proxy | YES (inherited) | Native: Claude Code manages sessions, GLM is backend | Low -- config only |
| **GLM-5 direct API** (new) | API | NO | Replay: summarized history injection | Medium -- new provider class |
| **OpenCode** (new) | CLI | YES | Native: `opencode --continue` / `--session <id>` | Medium -- new provider class |

---

## Architectural Decisions

### Decision 1: Session management lives in a new SessionManager layer

Not in the provider itself. The provider knows HOW to resume (native) or doesn't. The SessionManager knows WHEN to resume and handles the fallback strategy.

### Decision 2: SessionCapable is opt-in via protocol

Providers don't need to change to support the old behavior. Only providers that implement `SessionCapable` get native session resume. Others get the replay strategy automatically.

### Decision 3: Replay strategy uses rule-based summarization (not LLM)

Avoids extra API calls. Extracts file paths, decisions, and outcomes from responses using regex/heuristics. Good enough for maintaining context at ~67% more tokens than native resume.

### Decision 4: Session reuse is opt-in via config

```yaml
session_reuse:
  enabled: true            # default false
  strategy: auto           # auto | native | replay
  max_history_tokens: 8000 # token budget for replay summaries
```

`auto` strategy: use native if provider supports it, fall back to replay.

### Decision 5: Multi-LLM phases NEVER use session reuse

Validation and code review use multiple different LLMs in parallel. These are always fresh sessions. Session reuse only applies to the master LLM's sequential phases.

### Decision 6: Codex provider follows existing subprocess pattern

Same architecture as GeminiProvider -- subprocess with JSON stream parsing. Codex's `--json` flag outputs JSONL events, similar to Gemini's `--output-format stream-json`.

---

## Performance Comparison: All Strategies

### Per-Story Token Usage

| Strategy | Turn 1 | Turn 2 | Turn 3 | Turn 4 | Total Input |
|----------|--------|--------|--------|--------|-------------|
| **Current** (7 fresh sessions) | 12K | 12K | 12K | 12K | **~84K** (7 phases) |
| **Native resume** | 12K | 2K | 1K | 3K | **~18K** |
| **Replay with summary** | 12K | 4K | 6K | 8K | **~30K** |
| **Replay with full history** | 12K | 17K | 30K | 45K | **~104K** (worse!) |

Native resume is 4.7x more token-efficient than current. Replay with summarization is 2.8x more efficient. Full history replay (without summarization) would be WORSE than current -- summarization is essential.

### Per-Story Time Savings

| Scenario | Current | Native Resume | Replay (Summarized) |
|----------|---------|--------------|-------------------|
| Happy path (no QG failures) | ~18 min | ~10-12 min | ~13-15 min |
| One QG fix needed | ~23 min | ~12-14 min | ~16-18 min |
| Two QG fixes needed | ~28 min | ~14-16 min | ~19-21 min |

Native resume saves ~35-45%. Replay saves ~15-25%.

### Cost Comparison (Approximate, Claude Opus Pricing)

| Strategy | Input Tokens | Savings vs Current |
|----------|-------------|-------------------|
| Current (84K input) | 84,000 | -- |
| Native resume (18K) | 18,000 | **79% reduction** |
| Replay summary (30K) | 30,000 | **64% reduction** |

Output tokens are roughly the same across strategies (Claude does the same work).

---

## Error Handling and Edge Cases

### Session Resume Failure
If a native provider fails to resume (session expired, corrupted, or deleted), the SessionManager falls back to a fresh session for that turn. Logged as a warning, not an error. The story continues with a context gap but doesn't fail.

### Context Window Exhaustion (Native Sessions)
Long sessions may trigger Claude Code's auto-compaction, losing early details. Mitigation: put persistent rules in CLAUDE.md (survives compaction), keep turns focused, and monitor for response quality degradation.

### Worktree Session Isolation
Each worktree has a unique `cwd`, so sessions are naturally isolated per story. No cross-story session contamination possible. Session files for worktree `/project/.worktrees/parallel-3-1/` are stored separately from `/project/.worktrees/parallel-3-2/`.

### Crash Recovery with Sessions
Session IDs can be persisted in `state.yaml`. On resume after crash, the SessionManager passes the stored session ID. Claude picks up with full memory of prior work. This is dramatically better than current crash recovery, which restarts the entire phase from scratch with no context.

### Provider Switchover Mid-Story
If the master provider changes between turns (config reload, fallback), the SessionManager detects the change, resets the session, and switches strategy as needed. Logged as a warning.

---

## Implementation Priority

### Phase 1: Native Session Resume for Existing Providers
1. Add `session_id` support to `ClaudeSDKProvider` (capture from ResultMessage, pass as resume)
2. Add `--resume` flag to `GeminiProvider` (capture session ID from output, pass on next invoke)
3. Create `SessionManager` with native strategy
4. Wire into handler layer

### Phase 2: Codex Provider
5. Create `CodexProvider` following GeminiProvider subprocess pattern
6. Add `codex exec resume` support
7. Register in provider registry

### Phase 3: Replay Strategy for Stateless Providers
8. Implement replay strategy with rule-based summarization
9. Test with Ollama as the stateless reference provider
10. Token budget management and history trimming

### Phase 4: API-Based Providers (Future)
11. OpenAI Responses API provider with `previous_response_id`
12. Gemini Interactions API provider with `previous_interaction_id`
13. Alternatives to CLI-based providers for teams that prefer API-level control

---

## Impact on Existing Architecture

### Files to Create
- `providers/session.py` -- SessionManager, SessionCapable protocol, strategies
- `providers/codex.py` -- CodexProvider
- Corresponding test files

### Files to Modify
- `providers/claude_sdk.py` -- implement SessionCapable
- `providers/gemini.py` -- implement SessionCapable
- `providers/base.py` -- add SessionCapable protocol
- `providers/__init__.py` -- register codex provider
- `core/config.py` -- add session_reuse config, codex model support

### Files NOT Changed
- `parallel/orchestrator.py` -- subprocess spawning unchanged
- `loop/runner.py` -- phase iteration unchanged
- `loop/transitions.py` -- unchanged
- Existing handler files -- unchanged (SessionManager wraps provider)

### Backward Compatibility
- Session reuse defaults to OFF (`session_reuse.enabled: false`)
- All existing providers work identically without session reuse
- Codex provider is additive (new registration, doesn't affect existing)
- No breaking changes to any existing interface

---

## Migration Path

1. **Add SessionCapable to existing providers** -- non-breaking, existing `invoke()` still works
2. **Create SessionManager** -- new module, no dependencies on existing code
3. **Wire into new handler** -- the `agentic_dev` handler from the earlier plan uses SessionManager; existing handlers are unchanged
4. **Add Codex provider** -- additive, no impact on existing providers
5. **Enable via config** -- until `session_reuse.enabled: true` is set, nothing changes

---

## Sources

- OpenAI Conversation State Guide (developers.openai.com)
- OpenAI Responses API -- `previous_response_id` (platform.openai.com)
- OpenAI Conversations API -- thread replacement (platform.openai.com)
- OpenAI Codex CLI -- non-interactive mode (developers.openai.com/codex)
- OpenAI Codex CLI -- session resumption (deepwiki.com/openai/codex)
- Gemini Interactions API (ai.google.dev)
- Gemini CLI session management (geminicli.com, developers.googleblog.com)
- Mistral Conversations Beta (docs.mistral.ai)
- Cohere Chat API V1/V2 (docs.cohere.com)
- Ollama API (github.com/ollama/ollama)
- claude-agent-sdk v0.1.48 source (installed package)
