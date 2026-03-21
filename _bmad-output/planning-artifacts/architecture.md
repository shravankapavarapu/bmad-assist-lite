---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
lastStep: 8
status: 'complete'
completedAt: '2026-03-17'
inputDocuments:
  - 'prd.md'
  - 'project-context.md'
  - 'CLAUDE.md'
  - 'requirements-parallel-story-execution.md'
workflowType: 'architecture'
project_name: 'bmad-assist-lite-parallel-stories'
user_name: 'Shravan'
date: '2026-03-17'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**
54 FRs across 10 capability areas. The architecture must support three primary flows:
1. **Parallel execution flow** (FR1-FR13): Dependency graph → ready story selection → worktree creation → subprocess loop spawn → process monitoring
2. **Merge & integration flow** (FR14-FR20): Sequential merge queue → Claude CLI conflict resolution → post-merge QG → fix_quality_gate → commit
3. **Lifecycle management flow** (FR21-FR42): State persistence → crash recovery → sprint status → blocked story handling → graceful shutdown

**Non-Functional Requirements:**
18 NFRs across 4 categories that constrain the architecture:
- **Reliability:** Atomic state writes, crash resilience within 30 seconds, git operation atomicity, worktree isolation (NFR1-5)
- **Performance:** <1% orchestrator overhead, <30s worktree creation, <1s DAG computation (NFR6-10)
- **Platform:** Windows-primary with Unix support, isolated platform-specific modules, pathlib throughout (NFR11-14)
- **Integration:** Zero regression to existing loop, existing toolchain compliance (mypy strict, ruff, pytest) (NFR15-18)

**Scale & Complexity:**

- Primary domain: CLI tool / process orchestration
- Complexity level: Medium-High
- Estimated architectural components: 8 new modules + 3 minor existing code changes
- Max concurrent processes: 1 orchestrator + up to 5 worktree loops + merge operations

### Technical Constraints & Dependencies

- **Python 3.11+** with strict mypy, ruff, frozen Pydantic models
- **Git 2.5+** for worktree support — all git operations via subprocess
- **Claude CLI** available and authenticated for merger agent
- **Existing bmad-assist-lite codebase** (~60 source files) — must integrate without modifying loop behavior
- **Windows-native process management** — `taskkill` on Windows, `killpg` on Unix (existing `_windows.py` patterns)
- **Atomic file writes** — temp + `os.replace()` pattern for all state/config files
- **Singleton patterns** — `get_config()`, `get_paths()` with `_reset_*()` for testing

### Cross-Cutting Concerns Identified

1. **Platform safety** — Every git command, process spawn, file operation, and path must work on Windows and Unix. Isolated in dedicated modules.
2. **Crash recovery** — Orchestrator can be killed at any point. `parallel-state.yaml` + per-worktree `state.yaml` must be consistent after any failure mode.
3. **Observability** — Three-tier logging (orchestrator log, per-story logs, live console) spans all components. Output locking prevents interleaving.
4. **Git safety** — Worktree operations, branch management, and merges must never leave the repository in a broken state. Guard against main/master branch.
5. **Process lifecycle** — Spawned loop subprocesses must be tracked, monitored, and cleaned up — even on orchestrator crash (orphan detection on restart).

## Existing System Architecture

This section documents the current bmad-assist-lite architecture. The parallel enhancement sits above this system — the existing loop becomes the "unit of work" that parallel replicates in isolated worktrees.

### Package Layout

```
src/bmad_assist_lite/
├── cli.py              # Entry point: Typer app (run, init, compile, reset-lock, fetch-docs)
├── core/               # Config, paths, state machine, sprint status, toolchain, quality gates, exceptions
├── providers/           # BaseProvider ABC + Claude SDK + Gemini implementations + Windows process mgmt
├── compiler/            # Workflow compilation: parse workflow.yaml → resolve vars → discover files → XML prompt
├── loop/                # Main BMAD loop: 10 phase handlers, crash recovery, sprint sync, signals, locking
├── plugins/             # Plugin architecture: ProviderPlugin, PhasePlugin, WorkflowPlugin protocols
├── context_docs/        # Context7 library docs: detection, caching, resolution, epic table parsing
├── validation/          # Evidence Score: deterministic scoring, multi-validator aggregation
├── bmad/                # Epic/story markdown parser (extracts dependencies, status, acceptance criteria)
└── workflows/           # Bundled workflow templates (16 templates, package data)
```

### The 10-Phase Development Loop

The core of bmad-assist-lite is a sequential state machine that processes one story at a time through phases:

**Story Loop (7 phases):**

| Phase | Type | LLM | Description |
|-------|------|-----|-------------|
| `create_story` | LLM | Master | Reads epic, creates story file with AC, tasks, quality gates |
| `validate_story` | LLM | Multi (parallel) | N validators review story in parallel (read-only tools) |
| `validate_story_synthesis` | LLM | Master | Synthesizes validator reports, updates story |
| `dev_story` | LLM | Master | Implements code: TDD, negative tests, toolchain detection |
| `code_review` | LLM | Multi (parallel) | N reviewers review code in parallel (read-only tools) |
| `code_review_synthesis` | LLM | Master | Synthesizes reviews, runs toolchain verification |
| `quality_gate` | Non-LLM | — | Runs lint/typecheck/build/test deterministically |

**Quality Gate Detour:**

| Phase | Type | LLM | Description |
|-------|------|-----|-------------|
| `fix_quality_gate` | LLM | Master | Reads failure report, fixes code. Returns to `quality_gate`. NOT in phase list — reached via `next_phase` override only |

**Epic Teardown (2 phases, after all stories):**

| Phase | Type | LLM | Description |
|-------|------|-----|-------------|
| `epic_quality_gate` | Non-LLM | — | Runs full project test suite. Reports failed QA stories |
| `retrospective` | LLM | Master | Reviews epic execution, captures lessons learned |

**Phase execution flow:**
```
for each story in epic:
    for each phase in [create, validate, validate_synthesis, dev, code_review, code_review_synthesis, quality_gate]:
        result = execute_phase(state)
        save_state(state)           # atomic write
        trigger_sprint_sync(state)  # one-way, non-fatal
        if quality_gate fails:
            route to fix_quality_gate → re-run quality_gate
            if still fails: mark blocked, skip to next story
    advance_to_next_story()
run epic teardown phases
```

### Provider Subsystem

Two LLM providers with a common `BaseProvider` ABC:

- **Claude SDK Provider** (`providers/claude_sdk.py`) — Uses `claude-agent-sdk` async API. Models: opus, sonnet, haiku (or full claude-* IDs). Primary for master phases.
- **Gemini Provider** (`providers/gemini.py`) — Subprocess-based, runs Gemini CLI with JSON streaming. Any model string (validated by CLI). Primary for multi-LLM validator phases.

**Multi-LLM safety constraint:** `validate_story` and `code_review` run N providers in parallel using `asyncio.gather()` with `ThreadPoolExecutor`. These phases are restricted to **read-only tools** (Read, Glob, Grep) — no file writes during parallel execution. `code_review_synthesis` runs single master LLM — safe for command execution.

**Concurrency infrastructure:**
- `_OUTPUT_LOCK` (threading.Lock) in `providers/base.py` — prevents interleaved console output
- `run_async_in_thread()` — fresh event loop per thread, safe for ThreadPoolExecutor
- `start_stream_reader_threads()` — daemon threads for stdout/stderr per subprocess
- `_child_pgids` — thread-safe process group registry for signal handler cleanup

### State Machine & Persistence

- **`State`** — Frozen Pydantic model tracking `current_epic`, `current_story`, `current_phase`, `completed_stories`, `completed_epics`, `qa_retry_count`, `failed_qa_stories`
- **Immutable transitions** via `model_copy(update={...})` — `with_phase()`, `with_story()`, `complete_story()`
- **Atomic persistence** — `save_state()` writes to temp file, then `os.replace()`. Saved after every phase for crash recovery.
- **Resume** — On `--resume`, reads `state.yaml`, cross-checks against sprint-status, skips done stories/epics

### Sprint Status Tracking

- **`sprint-status.yaml`** — Single source of truth for story discovery and progress
- **One-way sync** — `state.yaml` → `sprint-status.yaml` after each phase (never reverse)
- **Non-fatal** — Sync errors logged as warnings, never propagated
- **Story discovery** — `find_next_backlog_story()` reads sprint-status at startup, caches queue

### Workflow Compilation Pipeline

```
workflow.yaml → parser → variable resolution → file discovery → XML prompt output
```

- Each phase has a workflow directory in `workflows/` (e.g., `create-story/`, `dev-story/`, `code-review/`)
- Compiler resolves `{variables}`, discovers referenced files, embeds them in XML prompt
- Toolchain auto-detection examines project root for build system indicators (package.json, pyproject.toml, etc.)

### Quality Gate Enforcement

Deterministic, non-LLM enforcement after code review synthesis:

- **Command sources (priority order):** Story file Quality Gates table → config `quality_gate` → auto-detected toolchain
- **On all-pass:** Auto-commit + mark story done
- **On fail (first try):** Write failure report to cache, route to `fix_quality_gate`
- **On fail (retry):** Auto-commit + mark blocked, skip to next story
- **Auto-commit timing:** After quality_gate outcomes (both pass and skip), NOT after code_review_synthesis

### Key Architectural Patterns

- **Plugin-first** — Providers, phases, and workflows are pluggable. Built-ins register first, plugins override
- **Windows-native** — `taskkill /F /T /PID` on Windows, `os.killpg()` on Unix. All in `providers/_windows.py`
- **2-tier config** — `~/.bmad-assist-lite/config.yaml` (global) merges under `bmad-assist-lite.yaml` (project). Project wins.
- **Singleton configs** — `get_config()`, `get_paths()` with `_reset_*()` for test isolation
- **Atomic writes** — All state and sprint-status files use temp + `os.replace()` pattern

### How Parallel Enhancement Relates

The parallel orchestrator treats the entire sequential loop as an opaque unit of work:

```
EXISTING (unchanged):
  Loop process: create → validate → synthesize → dev → review → synthesis → QG
  Inputs: epic file, sprint-status, config
  Outputs: implemented code (committed), story file, state.yaml

NEW (parallel orchestrator):
  Creates N worktrees, each running the existing loop for one story
  Coordinates: dependency resolution, merge queue, post-merge QG, state tracking
  The loop has ZERO awareness of parallel mode (except BMAD_PARALLEL_MODE env var disabling sprint sync)
```

## Starter Template Evaluation

### Primary Technology Domain

Python CLI tool — brownfield enhancement to existing `bmad-assist-lite` package.

### Starter Assessment

**Not applicable.** This is an enhancement to an existing codebase, not a new project. The technology stack is fully established:

- **Language & Runtime:** Python 3.11+ with strict type annotations
- **CLI Framework:** Typer (existing `cli.py` entry point)
- **Data Models:** Pydantic 2.0+ with `ConfigDict(frozen=True)`
- **Configuration:** 2-tier YAML (global + project) via PyYAML
- **Testing:** pytest with `asyncio_mode = "auto"`, autouse fixture reset pattern
- **Quality:** ruff (lint + format, 100-char lines), mypy strict with Pydantic plugin
- **Package:** `pyproject.toml` with `pip install -e .`

### Architectural Foundation (Existing)

The new `parallel/` module will be added as a sibling to existing subsystem directories (`core/`, `loop/`, `providers/`, `compiler/`, etc.) within `src/bmad_assist_lite/`. It follows all established patterns from `project-context.md` (54 rules).

No initialization command or starter template is needed. The first implementation story will create the `parallel/` directory structure and register the `parallel` Typer sub-app.

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
1. Orchestrator concurrency model → asyncio event loop
2. Git operations interface → raw subprocess
3. Worktree loop spawning → Python subprocess (separate process per story)

**Important Decisions (Shape Architecture):**
4. Claude CLI invocation for merger agent → CLI subprocess with `--print`
5. Live console output multiplexing → asyncio stream reader

**Deferred Decisions (Post-MVP):**
- Shared cache strategy for worktree build artifacts (Phase 2)
- Web dashboard communication protocol (Phase 2)

### Process Architecture

**Decision:** asyncio event loop for orchestrator coordination

**Rationale:** Already proven in the codebase (code_review handler uses `asyncio.gather()` for multi-LLM phases). Event-based wakeup via `asyncio.Event` reduces latency vs. polling. Clean signal handling with `asyncio.get_event_loop().add_signal_handler()` on Unix; `signal.signal()` fallback on Windows.

**Pattern:**
- `asyncio.create_subprocess_exec()` for spawning worktree loops
- `asyncio.Event` for completion signaling (set from process monitor, awaited by main loop)
- `asyncio.wait_for()` with timeout for merge operations
- `asyncio.sleep()` for stagger delays between worktree starts

**Affects:** Orchestrator, worktree manager, merger agent, output multiplexer

### Git Operations

**Decision:** Raw subprocess calls via `subprocess.run()` / `subprocess.Popen()`

**Rationale:** Zero new dependencies. Consistent with existing codebase patterns (Gemini provider, command_runner). Full control over error handling and cross-platform kwargs. GitPython adds dependency risk and known Windows long-path issues.

**Commands used:**
- `git worktree add <path> -b <branch>` — Create worktree with new branch
- `git worktree remove <path>` — Clean up worktree
- `git worktree list --porcelain` — List worktrees for orphan detection
- `git worktree prune` — Clean stale references
- `git merge <branch>` — Merge story branch into base
- `git branch -d <branch>` — Delete merged branch
- `git rev-parse --abbrev-ref HEAD` — Detect current branch (main/master guard)
- `git diff --name-only --diff-filter=U` — Detect merge conflicts

**Platform safety:** All commands use `get_subprocess_kwargs()` from existing `_windows.py`. Paths use `pathlib.Path` resolved to strings at subprocess call boundary.

**Affects:** Worktree manager, merger agent

### Claude CLI Invocation (Merger Agent)

**Decision:** Claude Code CLI subprocess with `--print` flag

**Rationale:** Simpler than the full SDK agent loop. The merger agent needs a single prompt→response interaction (read conflicts, resolve them), not an ongoing agent session. Uses existing user authentication. `--print` flag gives non-interactive output suitable for subprocess capture.

**Invocation pattern:**
```
claude --print -p "You are a merge conflict resolver. Here are the conflicts: ..."
```

**Fallback:** If Claude CLI fails or times out, mark story as blocked. The user resolves manually.

**Affects:** Merger agent only

### Worktree Loop Spawning

**Decision:** Python subprocess — one OS process per story

**Rationale:** The existing loop uses module-level singletons (`get_config()`, `get_paths()`, handler registry) that aren't thread-safe. Separate processes get completely clean state. Each process runs `bmad-assist-lite run --epic N --story M --single-story` in the worktree directory.

**Spawning pattern:**
```python
proc = await asyncio.create_subprocess_exec(
    sys.executable, "-m", "bmad_assist_lite", "run",
    "--epic", str(epic), "--story", str(story), "--single-story",
    cwd=str(worktree_path),
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.STDOUT,
    env={**os.environ, "BMAD_PARALLEL_MODE": "1"},
)
```

**Exit code semantics:** 0 = story completed successfully. Non-zero = story failed (blocked or error).

**Affects:** Orchestrator, worktree manager, observability

### Live Console Output Multiplexing

**Decision:** asyncio stream reader integrated with orchestrator event loop

**Rationale:** Since the orchestrator uses asyncio, reading subprocess stdout as async streams keeps everything in one event loop. No separate threads needed for output reading. Each stream reader prefixes lines with `[story|phase]` and writes through the existing `write_progress()` lock.

**Pattern:**
```python
async def read_story_output(proc, story_id):
    async for line in proc.stdout:
        prefix = f"[{story_id}]"
        write_progress(f"{prefix} {line.decode().rstrip()}")
```

**Affects:** Orchestrator, observability

### Decision Impact Analysis

**Implementation Sequence:**
1. Git operations wrapper (foundation — needed by everything)
2. Worktree manager (uses git operations)
3. Orchestrator core with asyncio loop (uses worktree manager)
4. Loop spawning + output multiplexing (uses orchestrator)
5. Merger agent with Claude CLI (uses git operations)
6. State persistence + crash recovery (uses orchestrator)
7. CLI commands (uses orchestrator)
8. Observability (spans all components)

**Cross-Component Dependencies:**
- Git operations → used by worktree manager AND merger agent
- asyncio event loop → shared by orchestrator, output readers, and merge operations
- `write_progress()` lock → shared by all output-producing components
- `parallel-state.yaml` → written by orchestrator, read by CLI status command

## Implementation Patterns & Consistency Rules

### Existing Patterns (from project-context.md)

All 54 rules in `project-context.md` apply to the `parallel/` module. Key patterns that are especially relevant:

- **Frozen Pydantic models** with `model_copy(update={...})` for mutations — applies to `ParallelState`, `ParallelConfig`
- **Atomic file writes** via temp + `os.replace()` — applies to `parallel-state.yaml`
- **Exception hierarchy** — new `ParallelError` subclass of `BmadAssistError`
- **Singleton pattern** — `ParallelConfig` loaded via existing `get_config()`, not a separate singleton
- **`logger = logging.getLogger(__name__)`** at module top in every file
- **`pathlib.Path` throughout** — no `os.path`, no string path concatenation
- **Absolute imports only** — `from bmad_assist_lite.parallel.orchestrator import ...`

### New Patterns for Parallel Module

#### Git Subprocess Pattern

All git operations MUST follow this pattern:

```python
def _run_git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command with consistent error handling."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        **get_subprocess_kwargs(),
    )
    if check and result.returncode != 0:
        raise ParallelError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result
```

**Rules:**
- Always use `get_subprocess_kwargs()` for platform safety
- Always pass `cwd` explicitly (never rely on process working directory)
- Always capture stderr for error messages
- Use `check=False` only when the caller explicitly handles non-zero exit (e.g., `git merge` which returns 1 on conflicts)

#### Async Pattern Rules

```python
# CORRECT: Pure async in orchestrator (new code)
async def run_loop(self) -> None:
    proc = await asyncio.create_subprocess_exec(...)
    await self._wait_for_completion()

# CORRECT: Bridge pattern when calling from sync context (existing pattern)
from bmad_assist_lite.core.async_utils import run_async_in_thread
result = run_async_in_thread(orchestrator.run_loop())

# WRONG: Never nest asyncio.run() inside async code
# WRONG: Never use threading for subprocess management in orchestrator
```

**Rules:**
- Orchestrator internals are pure async (asyncio)
- CLI entry points use `asyncio.run()` or `run_async_in_thread()` to bridge
- Never mix threading with asyncio for process management — use `asyncio.create_subprocess_exec()`
- Use `asyncio.Event` for signaling, not `threading.Event`

#### State Mutation Pattern

```python
# ParallelState is a frozen Pydantic model
# CORRECT: Use model_copy for state transitions
new_state = state.model_copy(update={
    "stories": {**state.stories, story_id: story_state.model_copy(update={"status": "done"})}
})
save_parallel_state(new_state, state_path)  # atomic write

# WRONG: Never mutate state directly
# state.stories[story_id].status = "done"  # FORBIDDEN - frozen model
```

#### Log Prefix Convention

| Context | Prefix Format | Example |
|---------|--------------|---------|
| Orchestrator decisions | `[ORCHESTRATOR]` | `[ORCHESTRATOR] Story 3.1 complete, queuing merge` |
| Story output (from worktree) | `[{story}\|{phase}]` | `[3.1\|DEV_STORY] Running quality gate...` |
| Merge operations | `[MERGE\|{story}]` | `[MERGE\|3.1] Merging parallel/3-1 → epic/3` |
| Post-merge QG | `[QG\|post-merge\|{story}]` | `[QG\|post-merge\|3.1] lint ✓ typecheck ✓` |
| Fix quality gate | `[FIX\|post-merge\|{story}]` | `[FIX\|post-merge\|3.1] Attempting fix...` |

**Log levels:**
- `INFO` — State transitions, story start/complete, merge results (goes to orchestrator log)
- `WARNING` — Recoverable issues (stale worktree pruned, retry needed)
- `ERROR` — Failures that block stories (merge failed, QG failed after fix)
- `DEBUG` — Subprocess commands, state file contents, scheduling scores

#### Process Cleanup Pattern

Every code path that spawns a subprocess MUST guarantee cleanup:

```python
async def _run_story(self, story_id: str, worktree_path: Path) -> int:
    proc = await asyncio.create_subprocess_exec(...)
    try:
        return_code = await proc.wait()
        return return_code
    except (asyncio.CancelledError, Exception):
        terminate_process(proc.pid)
        raise
    finally:
        self._signal_story_complete(story_id)
```

**Rules:**
- Use `try/finally` for every subprocess
- Kill process tree (not just process) on cleanup — use existing `terminate_process()` from `providers/_windows.py`
- Signal completion in `finally` block so orchestrator loop wakes up even on errors

#### Testing Patterns for Parallel Module

```python
# Use tmp_path fixture for worktree operations
def test_worktree_create(tmp_path: Path):
    ...

# Mock git subprocess calls, not git itself
@patch("bmad_assist_lite.parallel.git_ops._run_git")
def test_merge_conflict_detection(mock_git):
    mock_git.return_value = CompletedProcess(args=[], returncode=1, stderr="CONFLICT")
    ...

# Use asyncio markers for orchestrator tests
async def test_orchestrator_spawns_ready_stories():
    ...
```

### Enforcement Guidelines

**All AI Agents MUST:**
1. Follow all 54 rules in `project-context.md` — no exceptions for the parallel module
2. Use `_run_git()` wrapper for every git command — never raw `subprocess.run(["git", ...])`
3. Use async patterns in orchestrator code — never threading
4. Use atomic writes for `parallel-state.yaml` — never direct file writes
5. Include process cleanup in `finally` blocks — never leave orphaned subprocesses
6. Use the log prefix convention — never unformatted `print()` in library code

## Project Structure & Boundaries

### Complete Project Directory Structure (New Files Only)

```
src/bmad_assist_lite/
├── parallel/                          # NEW: Parallel story execution module
│   ├── __init__.py                    # Module exports
│   ├── cli.py                         # Typer sub-app: parallel run/status/unblock
│   ├── orchestrator.py                # Main asyncio coordination loop
│   ├── dependency_resolver.py         # Kahn's algorithm, scheduling scores, cycle detection
│   ├── worktree_manager.py            # Git worktree create/cleanup/prune
│   ├── merger.py                      # Claude CLI merge + conflict resolution + post-merge QG
│   ├── git_ops.py                     # Low-level git subprocess wrapper (_run_git)
│   ├── state.py                       # ParallelState Pydantic model + YAML I/O
│   ├── sprint_status_manager.py       # Orchestrator-owned sprint-status updates
│   ├── config.py                      # ParallelConfig Pydantic model
│   ├── logging.py                     # Orchestrator log setup, prefix formatting, summary report
│   └── exceptions.py                  # ParallelError and subclasses
│
├── cli.py                             # MODIFIED: Register parallel sub-app, add --epic/--story/--single-story
├── core/
│   └── sprint_sync.py                 # MODIFIED: Check BMAD_PARALLEL_MODE env var (~3 lines)
└── loop/
    └── runner.py                      # MODIFIED: --single-story exit behavior (~5 lines)

tests/
├── test_dependency_resolver.py        # NEW: Kahn's algorithm, scheduling scores, cycle detection
├── test_worktree_manager.py           # NEW: Git worktree operations (mocked git)
├── test_merger.py                     # NEW: Merge + conflict detection + post-merge QG
├── test_git_ops.py                    # NEW: Git subprocess wrapper
├── test_parallel_state.py             # NEW: ParallelState model + YAML I/O
├── test_parallel_config.py            # NEW: ParallelConfig validation
├── test_parallel_cli.py               # NEW: CLI commands (parallel run/status/unblock)
├── test_orchestrator.py               # NEW: Orchestrator coordination loop (async)
└── test_sprint_status_manager.py      # NEW: Orchestrator sprint-status updates
```

### Architectural Boundaries

**Module Boundary: `parallel/` ↔ existing codebase**

The `parallel/` module communicates with the existing codebase through exactly 3 touch points:

| Touch Point | Direction | Mechanism |
|-------------|-----------|-----------|
| `cli.py` → `parallel/cli.py` | Inbound | Typer sub-app registration (`app.add_typer(parallel_app)`) |
| `parallel/orchestrator.py` → `bmad-assist-lite run` | Outbound | Subprocess spawn (OS process boundary) |
| `parallel/sprint_status_manager.py` → `core/sprint_status.py` | Reuse | Import existing `SprintStatus` model and YAML I/O |

The existing loop running inside a worktree has **zero awareness** of parallel mode. It sees only the `BMAD_PARALLEL_MODE=1` env var (which disables sprint sync) and the `--epic`/`--story`/`--single-story` CLI flags.

**Internal Module Boundaries:**

```
parallel/cli.py
  ↓ calls
parallel/orchestrator.py (main loop)
  ├── uses → parallel/dependency_resolver.py (pure functions, no state)
  ├── uses → parallel/worktree_manager.py (git worktree operations)
  │              └── uses → parallel/git_ops.py (low-level git wrapper)
  ├── uses → parallel/merger.py (merge + QG + fix)
  │              └── uses → parallel/git_ops.py
  ├── uses → parallel/state.py (parallel-state.yaml read/write)
  ├── uses → parallel/sprint_status_manager.py (sprint-status.yaml updates)
  │              └── reuses → core/sprint_status.py (existing model)
  └── uses → parallel/logging.py (orchestrator log, prefixes, summary)
```

**Data Boundaries:**

| Data | Owner | Location | Access Pattern |
|------|-------|----------|---------------|
| `parallel-state.yaml` | Orchestrator | Project root | Read on startup, atomic write after each state change |
| `sprint-status.yaml` | Orchestrator (in parallel mode) | `_bmad-output/implementation-artifacts/` | Write after merge+QG, never read by orchestrator |
| Per-worktree `state.yaml` | Loop process | `{worktree}/.bmad-assist-lite/` | Owned by subprocess, orchestrator peeks read-only for status display |
| `parallel-run.log` | Orchestrator | Project root | Append-only during run, read by user post-run |
| Epic file | Read-only | `_bmad-output/planning-artifacts/` | Read once at startup for dependency parsing |

### Requirements to Structure Mapping

| FR Group | Module | Key Files |
|----------|--------|-----------|
| FR1-FR6 (Dependency Resolution) | `dependency_resolver.py` | Pure functions: `resolve_dependencies()`, `get_ready_stories()`, `compute_scheduling_scores()` |
| FR7, FR24-25, FR35 (Worktree Ops) | `worktree_manager.py` | `create_worktree()`, `cleanup_worktree()`, `prune_orphaned()` |
| FR8-FR13 (Parallel Execution) | `orchestrator.py` | `run_loop()`, `_spawn_story()`, `_monitor_processes()` |
| FR14-FR20 (Merge & Integration) | `merger.py` | `merge_story()`, `_resolve_conflicts()`, `_run_post_merge_qg()` |
| FR21-FR25 (State Management) | `state.py` | `ParallelState` model, `load_parallel_state()`, `save_parallel_state()` |
| FR26-FR29 (Sprint Status) | `sprint_status_manager.py` | `update_story_status()`, `update_epic_status()` |
| FR30-FR35 (Failure Handling) | `orchestrator.py` + `state.py` | Blocked story logic in orchestrator, state transitions in model |
| FR36-FR39 (CLI Commands) | `cli.py` | `parallel_run()`, `parallel_status()`, `parallel_unblock()` |
| FR40-FR42 (Graceful Shutdown) | `orchestrator.py` | Signal handling, drain mode, state persistence |
| FR43-FR47 (Observability) | `logging.py` + `orchestrator.py` | Log setup, prefix formatting, summary report generation |
| FR48-FR51 (Configuration) | `config.py` | `ParallelConfig` Pydantic model |
| FR52-FR54 (Existing Code) | `cli.py`, `runner.py`, `sprint_sync.py` | ~18 lines of changes |

### Integration Points

**Internal Communication:**
- Orchestrator → worktree loops: subprocess spawn with CLI args + env vars
- Worktree loops → orchestrator: process exit code (0=success, non-zero=failure)
- Orchestrator → merger agent: direct function call (same process)
- Merger agent → Claude CLI: subprocess (`claude --print -p "..."`)
- Orchestrator → git: subprocess via `_run_git()` wrapper

**External Integrations:**
- Git CLI (2.5+) — worktree operations, merge, branch management
- Claude Code CLI — merge conflict resolution (merger agent)
- Existing bmad-assist-lite CLI — loop execution in worktrees

**Data Flow:**
```
Epic file → dependency_resolver → ready stories → orchestrator
  → worktree_manager (create) → subprocess spawn → loop executes 7 phases
  → process exits → orchestrator detects completion
  → merger (merge + QG + fix) → state update → sprint_status update
  → re-evaluate ready stories → next cycle
```

## Feature Architecture

This section provides feature-scoped architectural decisions for subsystems introduced by later epics. Each H3 subsection is referenced by epic Context Requirements tables — header names are a public API (renaming requires updating all referencing epics).

### Crash Recovery

**Scope:** Epic 5 — recovering orchestrator and in-flight story state after unexpected termination.

**Recovery strategy:** On orchestrator restart, recovery runs before any new work begins:

1. **Load `parallel-state.yaml`** — last atomically-written orchestrator state. Stories marked `running` were in-flight at crash time.
2. **Scan worktrees** via `git worktree list --porcelain` — detect which worktrees still exist on disk.
3. **Cross-reference** worktrees against parallel state:
   - Worktree exists + state `running` → Check per-worktree `state.yaml` for last completed phase. Mark story as `resumable` or `failed` based on phase progress.
   - Worktree exists + state unknown → Orphaned worktree. Queue for cleanup.
   - No worktree + state `running` → Process died before worktree cleanup. Mark story as `failed`, reset to `ready`.
4. **Clean up** — Remove orphaned worktrees, delete stale `.lock` files, prune temp files (`*.tmp` in cache directories).
5. **Resume** — Re-evaluate dependency graph with recovered state. Ready stories enter the normal scheduling flow.

**Invariants:**
- Recovery must complete in <30 seconds (NFR2)
- No story data loss — per-worktree commits are preserved in git even if worktree is removed
- Atomic state writes guarantee `parallel-state.yaml` is never corrupted

**Affects:** `orchestrator.py` (startup recovery flow), `state.py` (status transitions: `running` → `resumable`/`failed`), `worktree_manager.py` (orphan detection)

### Blocked Story Handling

**Scope:** Epic 5 — handling stories that cannot proceed due to failures, and cascading blocks through the dependency graph.

**Block triggers:**
- Quality gate fails after max retries (existing pattern from sequential loop)
- Merge conflicts that Claude CLI cannot resolve
- Post-merge quality gate fails after fix attempt
- Dependency on a blocked story

**Cascade algorithm:**
1. When story S is marked `blocked`, traverse the dependency DAG forward (all stories that transitively depend on S)
2. Mark all downstream stories as `blocked_by: [S]` — they cannot be scheduled until S is unblocked
3. Stories already `running` that depend on S are NOT interrupted — they continue but their merge will be deferred
4. Re-evaluate ready stories after cascade — some stories may become unschedulable

**Unblock flow:**
1. User runs `parallel unblock <story-id>` CLI command
2. Validates the block reason has been manually resolved (user confirms)
3. Resets story status from `blocked` to `ready`
4. Removes story from all `blocked_by` lists in downstream stories
5. Re-evaluates dependency graph — newly unblocked stories enter scheduling

**State model:**
- `StoryStatus` enum: `ready`, `running`, `merging`, `done`, `failed`, `blocked`
- `blocked_by: list[str]` field on each story state — tracks which upstream stories caused the block
- `block_reason: str | None` — human-readable reason (e.g., "QG failed after 2 retries", "merge conflict unresolvable")

**Affects:** `orchestrator.py` (scheduling exclusion), `state.py` (blocked status + cascade fields), `dependency_resolver.py` (exclude blocked from ready evaluation), `cli.py` (unblock command)

### State Persistence

**Scope:** Epics 5-6 — parallel orchestrator state model and persistence guarantees.

**State file:** `parallel-state.yaml` in project root (sibling to `bmad-assist-lite.yaml`).

**State model (`ParallelState`):**
```
epic_id: str
base_branch: str
started_at: datetime
status: "running" | "completed" | "failed" | "recovered"
stories:
  {story_id}:
    status: "ready" | "running" | "merging" | "done" | "failed" | "blocked"
    worktree_path: str | None
    branch: str | None
    pid: int | None
    started_at: datetime | None
    completed_at: datetime | None
    blocked_by: list[str]
    block_reason: str | None
    last_phase: str | None
merge_queue: list[str]
completed_merges: list[str]
failed_qa_stories: list[str]
```

**Write protocol:**
- Atomic writes only: write to `parallel-state.yaml.tmp`, then `os.replace()`
- Write triggers: story status change, merge queue update, recovery completion
- Frequency: after every state transition (not periodic) — ensures crash consistency

**Read protocol:**
- Read once at startup (or recovery)
- CLI `status` command reads independently (no lock needed — atomic writes guarantee consistent reads)
- Per-worktree loop processes never read `parallel-state.yaml` — isolation boundary

**Consistency guarantees:**
- Last-writer-wins is safe because only the orchestrator process writes
- If orchestrator crashes mid-write, the `.tmp` file is discarded on recovery, and the last complete state is used

**Affects:** `state.py` (model + I/O), `orchestrator.py` (write triggers), `cli.py` (status read)

### Parallel Module Layout

**Scope:** All parallel epics — directory structure and module boundaries.

**Directory structure:**
```
src/bmad_assist_lite/parallel/
  __init__.py              # Module exports
  cli.py                   # Typer sub-app: parallel run/status/unblock
  orchestrator.py          # Main asyncio coordination loop
  dependency_resolver.py   # Kahn's algorithm, scheduling scores, cycle detection
  worktree_manager.py      # Git worktree create/cleanup/prune
  merger.py                # Claude CLI merge + conflict resolution + post-merge QG
  git_ops.py               # Low-level git subprocess wrapper (_run_git)
  state.py                 # ParallelState Pydantic model + YAML I/O
  sprint_status_manager.py # Orchestrator-owned sprint-status updates
  config.py                # ParallelConfig Pydantic model
  logging.py               # Orchestrator log setup, prefix formatting, summary report
  recovery.py              # Crash recovery and orphan detection (Epic 5)
  exceptions.py            # ParallelError and subclasses
```

**Module boundary rule:** The `parallel/` package communicates with existing code through exactly 3 touch points (see Architectural Boundaries section). New files for resilience (Epic 5) and observability (Epic 6) are added within `parallel/`, never in `core/` or `loop/`.

**Import rules:**
- `parallel/` modules may import from `core/` (config, paths, exceptions, sprint_status)
- `parallel/` modules must NOT import from `loop/` or `providers/`
- `core/` and `loop/` must NOT import from `parallel/`
- Within `parallel/`, follow the internal dependency graph (see Architectural Boundaries)

### Observability

**Scope:** Epic 6 — logging, status display, and summary reporting for parallel execution.

**Three-tier logging architecture:**

| Tier | Destination | Content | Owner |
|------|-------------|---------|-------|
| Orchestrator log | `parallel-run.log` (file) | All orchestrator decisions, state transitions, merge results, errors | `logging.py` |
| Per-story output | Console (multiplexed) | Prefixed stdout from each worktree subprocess | `orchestrator.py` stream readers |
| Summary report | Console + log file | Post-run summary with story outcomes, timings, failures | `logging.py` |

**Orchestrator log (`parallel-run.log`):**
- Append-only file in project root, created at run start
- Rotated per run (timestamp in filename or truncate on new run)
- Includes all INFO+ messages from orchestrator, merger, worktree manager
- Machine-parseable: each line prefixed with `[TIMESTAMP] [LEVEL] [COMPONENT]`

**Enhanced status display:**
- `parallel status` CLI command reads `parallel-state.yaml` and renders a table
- Columns: Story ID, Status, Phase, Duration, Worktree, Branch
- Color-coded status: green=done, yellow=running, red=blocked/failed
- Includes phase info by peeking at per-worktree `state.yaml` (read-only)

**Summary report generation:**
- Generated after all stories complete (or on graceful shutdown)
- Content: total duration, per-story timing breakdown, pass/fail/blocked counts, QG failure details, merge conflict summary
- Written to orchestrator log and echoed to console

**Affects:** `logging.py` (all three tiers), `cli.py` (status command), `orchestrator.py` (stream readers, summary trigger)

### Epic Teardown

**Scope:** Epic 6 — cleanup and finalization after all stories in an epic complete in parallel mode.

**Teardown sequence:**
1. **Wait for completion** — All stories must be in terminal state (`done`, `blocked`, or `failed`)
2. **Final merge verification** — Confirm all `done` stories have been merged to the base branch
3. **Run epic quality gate** — Full project test suite on the base branch (reuses existing `epic_quality_gate` phase logic)
4. **Generate summary report** — Aggregate results from all stories (see Observability)
5. **Update sprint status** — Mark epic as `done` (if all stories done) or `blocked` (if any stories blocked/failed)
6. **Clean up worktrees** — Remove all remaining worktrees for this epic
7. **Clean up branches** — Delete all merged story branches (`parallel/{story-id}`)
8. **Run retrospective** — Optional, reuses existing `retrospective` phase

**Partial completion handling:**
- If some stories are `blocked`/`failed`, epic teardown still runs for completed stories
- Blocked stories are reported in the summary with block reasons
- Epic status becomes `blocked` (not `done`) — requires manual intervention before marking complete

**Integration with existing phases:**
- `epic_quality_gate` and `retrospective` are existing phase handlers in `loop/handlers/`
- In parallel mode, the orchestrator calls these directly (not via subprocess loop) after all merges complete
- They run on the base branch with all merged code

**Affects:** `orchestrator.py` (teardown sequence), `worktree_manager.py` (bulk cleanup), `sprint_status_manager.py` (epic status update), `logging.py` (summary report)

## Architecture Validation Results

### Coherence Validation

**Decision Compatibility:**
- asyncio orchestrator + `asyncio.create_subprocess_exec()` + asyncio stream readers — all in one event loop, no threading conflicts
- Raw git subprocess + `get_subprocess_kwargs()` — consistent with existing Gemini provider pattern
- Claude CLI subprocess for merger agent — independent of orchestrator's asyncio
- Frozen Pydantic state models + atomic YAML writes — consistent with existing `State` and `SprintStatus` patterns
- No version conflicts — all using existing Python 3.11+ toolchain

**Pattern Consistency:**
- `_run_git()` wrapper enforces consistent git error handling across worktree_manager and merger
- Log prefix convention covers all output contexts consistently
- Async pattern rules prevent mixing threading/asyncio
- State mutation pattern (frozen model + `model_copy`) prevents direct mutation everywhere
- Process cleanup pattern (try/finally with `terminate_process`) prevents orphaned processes

**Structure Alignment:**
- `parallel/` module boundary is clean — 3 touch points with existing code
- Internal dependency graph is acyclic: `cli → orchestrator → {dependency_resolver, worktree_manager, merger, state, sprint_status_manager, logging}` → `git_ops`
- No circular imports possible with this structure

### Requirements Coverage

**Functional Requirements: 54/54 (100%)**

| FR Group | Architectural Support | Status |
|----------|-----------------------|--------|
| FR1-FR6 (Dependency Resolution) | `dependency_resolver.py` — pure functions | Covered |
| FR7-FR13 (Parallel Execution) | `orchestrator.py` + `worktree_manager.py` — asyncio subprocess | Covered |
| FR14-FR20 (Merge & Integration) | `merger.py` + `git_ops.py` — Claude CLI + post-merge QG | Covered |
| FR21-FR25 (State Management) | `state.py` — frozen Pydantic + atomic YAML | Covered |
| FR26-FR29 (Sprint Status) | `sprint_status_manager.py` — reuses existing model | Covered |
| FR30-FR35 (Failure Handling) | `orchestrator.py` + `state.py` — blocked transitions | Covered |
| FR36-FR39 (CLI Commands) | `cli.py` — Typer sub-app | Covered |
| FR40-FR42 (Graceful Shutdown) | `orchestrator.py` — signal handling + drain | Covered |
| FR43-FR47 (Observability) | `logging.py` + `orchestrator.py` — tiered logging | Covered |
| FR48-FR51 (Configuration) | `config.py` — Pydantic model in existing config | Covered |
| FR52-FR54 (Existing Code) | 3 files modified (~18 lines) | Covered |

**Non-Functional Requirements: 18/18 (100%)**

| NFR Group | Architectural Support | Status |
|-----------|-----------------------|--------|
| NFR1-5 (Reliability) | Atomic writes, process isolation, orphan detection | Covered |
| NFR6-10 (Performance) | asyncio (no polling overhead), pure-function DAG | Covered |
| NFR11-14 (Platform) | `get_subprocess_kwargs()`, `pathlib.Path`, `_run_git()` | Covered |
| NFR15-18 (Integration) | Subprocess isolation, existing toolchain | Covered |

### Gap Analysis

**Critical Gaps: 0**

**Important Gaps: 1**
- Merger agent prompt template — specifies invocation but not the prompt content. Deferred to epic/story creation.

**Nice-to-Have Gaps: 2**
- Performance baseline measurements — will be measured during implementation
- Integration test fixture strategy for git worktree operations — addressed in story acceptance criteria

### Architecture Completeness Checklist

**Requirements Analysis**
- [x] Project context thoroughly analyzed (54 rules internalized)
- [x] Scale and complexity assessed (medium-high, 8 components)
- [x] Technical constraints identified (Python 3.11+, git 2.5+, Windows-primary)
- [x] Cross-cutting concerns mapped (platform safety, crash recovery, observability, git safety, process lifecycle)

**Architectural Decisions**
- [x] Critical decisions documented (asyncio, raw subprocess, process-per-story)
- [x] Technology stack fully specified (existing + no new dependencies)
- [x] Integration patterns defined (subprocess boundaries, 3 touch points)
- [x] Performance considerations addressed (event-based, <1% overhead target)

**Implementation Patterns**
- [x] Naming conventions established (existing 54 rules + log prefixes)
- [x] Structure patterns defined (git subprocess, async rules, state mutation)
- [x] Communication patterns specified (subprocess exit codes, file-based state)
- [x] Process patterns documented (cleanup in finally, terminate process tree)

**Project Structure**
- [x] Complete directory structure defined (12 new files, 9 test files)
- [x] Component boundaries established (3 touch points, acyclic internal deps)
- [x] Integration points mapped (orchestrator → loop subprocess → git → Claude CLI)
- [x] Requirements to structure mapping complete (54 FRs → specific files)

### Architecture Readiness Assessment

**Overall Status: READY FOR IMPLEMENTATION**

**Confidence Level:** High — all critical decisions made, all FRs/NFRs architecturally supported, patterns and structure aligned with existing codebase conventions.

**Key Strengths:**
- Zero new dependencies — uses existing toolchain throughout
- Clean module boundary with only 3 touch points to existing code
- Proven patterns (asyncio, subprocess, atomic writes) reused from existing codebase
- ~18 lines of existing code changes — minimal regression risk

**Areas for Future Enhancement:**
- Merger agent prompt template (design task for story creation)
- Integration test fixtures for git worktree operations
- Performance baseline measurements
- Shared cache strategy for worktree cold-start (Phase 2)

### Implementation Handoff

**AI Agent Guidelines:**
- Follow all architectural decisions exactly as documented
- Use implementation patterns consistently across all components
- Respect project structure and boundaries
- Follow the 54 rules in `project-context.md` plus the 6 new enforcement guidelines
- Refer to this document for all architectural questions

**Implementation Sequence:**
1. Git operations wrapper (`git_ops.py`) — foundation
2. Worktree manager (`worktree_manager.py`) — uses git_ops
3. Orchestrator core (`orchestrator.py`) — asyncio loop, uses worktree_manager
4. Loop spawning + output multiplexing — uses orchestrator
5. Merger agent (`merger.py`) — Claude CLI, uses git_ops
6. State persistence + crash recovery (`state.py`) — uses orchestrator
7. CLI commands (`cli.py`) — uses orchestrator
8. Observability (`logging.py`) — spans all components
