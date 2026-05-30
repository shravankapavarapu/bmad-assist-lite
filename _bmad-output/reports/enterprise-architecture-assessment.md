# Enterprise Architecture Assessment: bmad-assist-lite
### Mapping Claude Certified Architect Patterns to Project Reality
**Date**: 2026-03-31
**Source**: Claude Certified Architect - Foundations Certification Exam Guide

---

## Executive Summary

The project already implements several enterprise-grade patterns that align with the certification's philosophy -- multi-LLM validation with consensus detection, Template Method provider abstraction, atomic state persistence, and a plugin architecture. However, there are **6 high-impact areas** where adopting certification-recommended patterns would materially improve reliability, cost efficiency, and developer experience.

| Priority | Pattern | Current State | Impact |
|----------|---------|---------------|--------|
| **P0** | MCP Tool Integration | None | Unlocks Claude Code native tool use for dev_story/fix_quality_gate |
| **P0** | Structured Output via JSON Schema | Regex parsing | Eliminates Evidence Score parsing failures |
| **P1** | Claude Code CI/CD Integration | None | Automated PR review pipeline |
| **P1** | Agent SDK Hooks for Enforcement | Prompt-only | Deterministic quality gate prerequisites |
| **P2** | Context Window Optimization | Basic filtering | Token savings via trimming + position-aware ordering |
| **P2** | Path-Specific .claude/rules/ | Monolithic CLAUDE.md | Per-module conventions for parallel worktrees |

---

## Domain 1: Agentic Architecture & Orchestration (27% of exam)

### 1.1 Agentic Loops -- Partially Applicable

**Certification Pattern**: Implement loops where Claude decides next actions via `stop_reason` ("tool_use" vs "end_turn"), with tool results appended to conversation history.

**Current State**: The 10-phase loop is a **fixed sequential pipeline** -- `create_story -> validate -> synthesize -> dev -> review -> synthesize -> quality_gate -> ...`. Phase transitions are deterministic via `transitions.py`, not model-driven.

**Assessment**: The pipeline approach is actually the **correct architecture** for this use case. The certification warns against "setting arbitrary iteration caps as the primary stopping mechanism" -- but the phases aren't arbitrary caps, they're a deliberate methodology (BMAD). Each phase has a clear purpose and the sequence matters.

**Where agentic loops WOULD help**: The `dev_story` and `fix_quality_gate` phases. Currently, the master LLM gets a single prompt and returns a single response. If instead these phases used an **agentic loop** where Claude could:
1. Read the story file
2. Implement code changes
3. Run tests via tool_use
4. See test failures
5. Fix and re-run

...the result would be Claude's native iterative development capability instead of a single-shot prompt. This is the difference between "here's everything, do it all at once" and "work on this task, using tools as needed."

**Recommendation**: **Consider migrating `dev_story` and `fix_quality_gate` to use Claude's native agentic loop** (via Claude Code's `-p` flag with tool access) instead of the current single-shot prompt pattern. This would leverage the certification's Task Statement 1.1 directly.

---

### 1.2 Multi-Agent Coordinator-Subagent -- Already Implemented (Partially)

**Certification Pattern**: Hub-and-spoke architecture where a coordinator manages subagents with isolated context, routing all communication through the coordinator.

**Current State**: This pattern already exists. The `code_review` handler spawns N validators in parallel (subagents), then `code_review_synthesis` acts as the coordinator that aggregates results. The Evidence Score system is the "result aggregation" layer.

**Gap**: Validators don't have isolated tool access (they're read-only for multi-LLM safety). The certification emphasizes **scoped tool access per subagent** (Task Statement 2.3) -- giving each agent only the tools needed for its role. The constraint of "no tool execution during multi-LLM phase" is actually a correct application of this principle.

**Gap**: No **iterative refinement loop** where the coordinator evaluates synthesis output for gaps and re-delegates. Currently, synthesis is a single pass. The certification (Task Statement 1.2) recommends: "evaluate synthesis output for gaps -> re-delegate -> re-synthesize until coverage is sufficient."

**Recommendation**: For code review, consider adding a **synthesis quality check** -- if the synthesis identifies unresolved contradictions between validators, it could trigger a targeted re-review of just those areas.

---

### 1.3 Subagent Context Passing -- Correctly Implemented

**Certification Pattern**: Subagent context must be explicitly provided in the prompt -- subagents don't inherit parent context.

**Current State**: Each phase handler explicitly compiles its context via the `compiler/` module. Validators get the same compiled prompt. The synthesizer gets the original context + validator outputs. Context is **always explicit**, never inherited.

The `compiler/context_filter.py` validates that referenced documents actually exist at compile time -- this is a production-hardened pattern the certification doesn't even mention. The project is ahead here.

---

### 1.4 Programmatic Enforcement vs Prompt-Based -- Key Opportunity

**Certification Pattern**: "When deterministic compliance is required, prompt instructions alone have a non-zero failure rate." Use programmatic hooks/prerequisites to enforce business rules.

**Current State**: Quality gates are **deterministic** (non-LLM, command-based) -- this is correct. But the flow from `dev_story` to quality gate relies on the LLM correctly completing implementation tasks in the right order. If the LLM skips test writing, it is only caught at the quality gate phase.

**Opportunity**: The certification's Hook pattern (Task Statement 1.5) could be applied here. An **Agent SDK PostToolUse hook** on the `dev_story` phase could:
- Intercept file writes and track which files have been modified
- Block progression to "story complete" until test files exist
- Enforce that `pytest` passes before the LLM declares completion

This would catch quality issues **during development** instead of at the separate quality gate phase, reducing the fix_quality_gate retry loop.

---

### 1.7 Session State & Resumption -- Applicable

**Certification Pattern**: Named session resumption, fork_session for parallel exploration, informing resumed sessions about changes.

**Current State**: `--resume` with state.yaml cross-validation exists. But each phase starts fresh -- there's no conversation continuity between phases.

**Where this matters**: When `fix_quality_gate` runs after a `quality_gate` failure, it doesn't have the context of the original `dev_story` conversation. It gets a fresh prompt with the failure report. The certification recommends: "Informing a resumed session about specific file changes for targeted re-analysis."

**Recommendation**: For `fix_quality_gate`, consider using Claude Code's `--resume` flag to continue the same session that did the original development, providing the quality gate failure report as new context. The model would have full memory of what it built and why, making fixes more targeted.

---

## Domain 2: Tool Design & MCP Integration (18% of exam)

### 2.1-2.4 MCP Integration -- Highest Impact Opportunity

**Certification Pattern**: MCP servers expose tools and resources to Claude. Project-scoped `.mcp.json` for shared tooling, environment variable expansion for credentials.

**Current State**: **No MCP integration at all.** Providers invoke Claude SDK and Gemini CLI as subprocess wrappers, passing compiled prompts. The LLMs don't have access to project tools during execution.

**This is the single highest-impact gap.** Here's why:

When the `dev_story` phase runs, it sends a single compiled prompt to Claude and gets back a single text response containing code. That response then gets written to files. But **Claude Code natively supports** Read, Write, Edit, Bash, Grep, Glob tools -- it can iteratively explore code, write changes, run tests, and fix issues in a loop.

By adding MCP tools, the project could expose:

1. **Project-specific tools** via `.mcp.json`:
   - `get_story_context` -- returns the compiled story context
   - `run_quality_gate` -- executes lint/typecheck/build/test
   - `get_sprint_status` -- returns current sprint state
   - `mark_story_done` -- updates sprint-status.yaml

2. **MCP Resources** (content catalogs):
   - Expose the epic/story structure as a resource so Claude can browse available stories
   - Expose architecture.md as a resource for reference during development

**Concrete Impact**: Instead of a single-shot prompt -> response -> quality gate -> fix cycle, the dev_story phase could be: "Claude, implement this story using your tools. Run tests when you're done." Claude would iteratively develop, test, and fix -- all in one phase invocation.

**Recommendation**: Create a `.mcp.json` with project-specific tools that expose quality gate commands and sprint status to Claude during development phases.

---

### 2.2 Structured Error Responses -- Applicable

**Certification Pattern**: Return structured error metadata: `errorCategory` (transient/validation/permission), `isRetryable` boolean, human-readable descriptions.

**Current State**: `ExitStatus` enum (SUCCESS, ERROR, MISUSE, etc.) and `ProviderExitCodeError` with stderr capture exist. But error responses to the LLM are unstructured text.

**Opportunity**: When quality gate commands fail, the failure report could use structured error categories:
- `transient` (network flake during npm install) -> auto-retry
- `validation` (type error in code) -> route to fix_quality_gate
- `permission` (missing API key) -> abort with actionable message

The `_deduplicate_test_output()` in quality_gate.py already groups errors by signature -- this is a good foundation for structured error categorization.

---

## Domain 3: Claude Code Configuration & Workflows (20% of exam)

### 3.1 CLAUDE.md Hierarchy + .claude/rules/ -- Quick Win

**Certification Pattern**: Use `.claude/rules/` with YAML frontmatter glob patterns for path-specific conventions. Split monolithic CLAUDE.md into focused files.

**Current State**: Single monolithic CLAUDE.md (~300 lines) covering everything.

**Opportunity**: With the parallel worktree architecture, different parts of the codebase have different conventions:
- `src/bmad_assist_lite/parallel/` -- async patterns, frozen dataclasses
- `src/bmad_assist_lite/providers/` -- Template Method pattern, process management
- `src/bmad_assist_lite/compiler/` -- protocol-based compilers, file discovery
- `tests/` -- pytest conventions, autouse fixtures, specific markers

Create `.claude/rules/` files:

```yaml
# .claude/rules/parallel-module.md
---
paths: ["src/bmad_assist_lite/parallel/**"]
---
- Use frozen=True for all Pydantic models (immutable state)
- Use asyncio patterns with proper cancellation
- All subprocess spawning must use start_new_session=True
```

```yaml
# .claude/rules/testing.md
---
paths: ["tests/**"]
---
- Use autouse fixtures from conftest.py (reset_paths_singleton, etc.)
- Mark slow tests with @pytest.mark.slow
- Opt out of auto config with @pytest.mark.no_auto_config
```

**Impact**: When Claude Code works on files in these directories (especially in parallel worktrees), it automatically gets the right conventions without loading the full CLAUDE.md.

---

### 3.2 Skills with context:fork -- Applicable

**Certification Pattern**: Skills with `context: fork` run in isolated sub-agent context, preventing verbose output from polluting the main conversation.

**Current State**: 30+ BMAD skills exist but they don't use `context: fork`.

**Opportunity**: Skills like `bmad-code-review` and `bmad-qa-generate-e2e-tests` produce large outputs. Adding `context: fork` would keep the main conversation clean when using these interactively.

---

### 3.6 CI/CD Integration -- High Value

**Certification Pattern**: Run Claude Code in CI with `-p` flag, `--output-format json`, `--json-schema` for structured findings.

**Current State**: No CI/CD integration. All runs are manual via `bmad-assist-lite run`.

**Opportunity**: This is directly applicable to the workflow:

1. **PR Review Pipeline**: When a story branch is ready for merge, a GitHub Action could run:
   ```bash
   claude -p "Review this PR for the changes in story ${STORY_ID}" \
     --output-format json \
     --json-schema review-schema.json
   ```
   This would produce structured review findings that could be posted as PR comments.

2. **Post-Merge Quality Gate in CI**: Instead of running quality gates locally in the merge queue, run them in CI where the environment is clean and reproducible.

3. **Automated Story Validation**: On push to a story branch, validate the story implementation against acceptance criteria.

**Impact**: Moves quality enforcement from local-only to shared CI infrastructure, which is especially important for the parallel execution workflow where multiple worktrees are producing changes simultaneously.

---

## Domain 4: Prompt Engineering & Structured Output (20% of exam)

### 4.3 JSON Schema Enforcement -- High Impact

**Certification Pattern**: Use `tool_use` with JSON schemas for guaranteed schema-compliant output. "Strict JSON schemas eliminate syntax errors but don't prevent semantic errors."

**Current State**: Evidence Score parsing relies on **regex patterns** in `validation/evidence_score.py`:
```python
# Parses: "**Total Score**: 5.3"
# Parses: "**Verdict**: MAJOR_REWORK"
# Parses: "- CRITICAL: 1 finding(s)"
```

This is fragile. If the LLM formats the score slightly differently, parsing fails.

**Recommendation**: Define Evidence Score as a JSON schema tool:

```json
{
  "name": "submit_evidence_score",
  "input_schema": {
    "type": "object",
    "properties": {
      "findings": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "severity": {"enum": ["CRITICAL", "IMPORTANT", "MINOR", "CLEAN_PASS"]},
            "description": {"type": "string"},
            "file_path": {"type": "string"},
            "line_number": {"type": "integer"}
          },
          "required": ["severity", "description"]
        }
      },
      "total_score": {"type": "number"},
      "verdict": {"enum": ["REJECT", "MAJOR_REWORK", "PASS", "EXCELLENT"]}
    },
    "required": ["findings", "total_score", "verdict"]
  }
}
```

With `tool_choice: {"type": "tool", "name": "submit_evidence_score"}`, the model **must** call this tool, guaranteeing structured output every time.

**Impact**: Eliminates all Evidence Score parsing failures. The `_parse_evidence_score()` regex becomes unnecessary.

**Caveat**: This requires moving from claude-agent-sdk to the Anthropic Python SDK's direct API for validation phases, or adding tool definitions to the SDK invocation.

---

### 4.6 Multi-Instance Review -- Already Implemented

**Certification Pattern**: "Independent review instances without prior reasoning context are more effective at catching subtle issues than self-review."

**Current State**: This is exactly the multi-LLM validation pattern. N independent validators (Gemini + Claude models) run without shared context, then synthesize. The certification validates this architecture choice.

**Enhancement**: The certification also recommends "splitting large reviews into per-file local analysis passes plus cross-file integration passes." For large stories touching many files:
1. First pass: per-file review (parallel, focused)
2. Second pass: cross-file integration review (sequential, broad)

This would address the "lost in the middle" effect (Task Statement 5.1) where models miss issues in the middle of large prompts.

---

## Domain 5: Context Management & Reliability (15% of exam)

### 5.1 Context Window Optimization -- Applicable

**Certification Pattern**: Trim verbose tool outputs, extract structured facts, position-aware input ordering (key findings at beginning/end, details in middle).

**Current State**: Context filtering already exists:
- Strip synthesis reports from prior phases
- Strip Quality Gates section from story files in LLM prompts
- Context7 library doc injection with token limits

**Additional Optimizations**:

1. **Position-aware ordering**: Place the story's acceptance criteria and task list at the **beginning** of the prompt (highest attention), architecture details in the **middle** (lower attention), and the "what to do now" instructions at the **end** (high attention). The certification explicitly warns about the "lost in the middle" effect.

2. **Trimming verbose context**: Epic files can be large. For `dev_story`, the full epic is loaded but only the current story's section is needed. The `context_filter.py` already does section-level extraction -- ensure this is applied to all phases.

3. **Structured fact extraction**: Instead of passing the full sprint-status.yaml to synthesis phases, extract just the relevant facts: "Story 3.2 is IN_FLIGHT, dependencies [3.1] are DONE, blocked stories: none."

---

### 5.3 Error Propagation -- Applicable Enhancement

**Certification Pattern**: Structured error context including failure type, what was attempted, partial results, and potential alternatives.

**Current State**: The `fix_quality_gate` handler includes retry context:
- Previous failure report from cache
- Retry count and alternative strategies
- Story file with test requirements

**Enhancement**: The certification recommends having subagents "implement local recovery for transient failures and only propagate errors they cannot resolve." Applied to this architecture:

- If `quality_gate` lint fails with a formatting issue -> auto-fix with `ruff format` before marking as failed
- If a single test fails due to a flaky assertion -> re-run once before escalating to `fix_quality_gate`
- Only escalate to the LLM when deterministic fixes fail

This would reduce unnecessary LLM invocations for issues that have deterministic solutions.

---

### 5.4 Crash Recovery with Structured State -- Already Strong

**Certification Pattern**: "Structured state persistence for crash recovery: each agent exports state to a known location, and the coordinator loads a manifest on resume."

**Current State**: The `state.yaml` + `parallel-state.yaml` + `sprint-status.yaml` triple is essentially this pattern. The `recovery.py` module recovers worktree state on resume. This is well-implemented.

---

## What NOT to Adopt

Some certification patterns would be **over-engineering** for this project:

1. **Batch Processing API** (Task Statement 4.5): The workflow is interactive/sequential. The 50% cost savings of batch API requires 24-hour latency tolerance -- inappropriate for the feedback loop.

2. **Fork Session** (Task Statement 1.7): Phases are independent compilations, not conversation continuations. Forking adds complexity without clear benefit for the pipeline model.

3. **Human Review Routing with Confidence Calibration** (Task Statement 5.5): The Evidence Score system already routes to human review (REJECT/MAJOR_REWORK verdicts). Adding field-level confidence scores would over-complicate what's already working.

4. **MCP Resources for Content Catalogs** (Task Statement 2.4): Context compilation handles document discovery. MCP resources would duplicate this capability.

---

## Recommended Implementation Roadmap

### Phase 1: Quick Wins (1-2 days each)
1. **`.claude/rules/` path-specific conventions** -- Split CLAUDE.md, add glob-scoped rules for parallel/, providers/, tests/
2. **Position-aware prompt ordering** -- Restructure compiled prompts to place key info at beginning/end
3. **Auto-fix before escalation** -- Run `ruff format` on lint failures before routing to fix_quality_gate

### Phase 2: Structural Improvements (3-5 days each)
4. **JSON Schema for Evidence Score** -- Define tool_use schema, eliminate regex parsing
5. **CI/CD pipeline for PR review** -- GitHub Action with `claude -p --output-format json`
6. **Agentic loop for dev_story** -- Use Claude Code's `-p` flag with tool access instead of single-shot prompt

### Phase 3: Architecture Evolution (1-2 weeks)
7. **MCP tool integration** -- `.mcp.json` with project-specific tools for quality gates and sprint status
8. **Agent SDK hooks** -- PostToolUse hooks for enforcement during development phases

---

## Certification Domain Coverage Summary

| Domain | Weight | Current Alignment | Key Gap |
|--------|--------|-------------------|---------|
| 1. Agentic Architecture | 27% | Partial (pipeline is valid, but dev_story is single-shot) | Agentic loop for dev phases |
| 2. Tool Design & MCP | 18% | Minimal (no MCP, no tool_use) | MCP integration |
| 3. Claude Code Config | 20% | Moderate (CLAUDE.md exists, skills exist) | .claude/rules/, CI/CD |
| 4. Prompt Engineering | 20% | Strong (structured templates, multi-LLM review) | JSON schema enforcement |
| 5. Context & Reliability | 15% | Strong (context filtering, crash recovery, Evidence Score) | Position-aware ordering |
