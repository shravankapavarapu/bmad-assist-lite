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
extension:
  name: 'cursor-provider-linux'
  status: 'complete'
  date: '2026-06-12'
  completedAt: '2026-06-12'
  stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
  lastStep: 8
  inputDocuments:
    - 'requirements-cursor-provider.md'
    - 'project-context.md'
    - 'reference_cursor_cli_research.md (session memory)'
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
│   ├── bootstrap.py                   # Worktree bootstrap: file copy, setup, validation (Epic 9)
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
├── test_sprint_status_manager.py      # NEW: Orchestrator sprint-status updates
└── test_parallel_bootstrap.py         # NEW: Bootstrap pipeline: copy, setup, validation, canary
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
  ├── uses → parallel/bootstrap.py (worktree bootstrap: copy, setup, validate)
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
  bootstrap.py             # Worktree bootstrap: file copy, setup commands, validation (Epic 9)
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

### Worktree Bootstrap

**Scope:** Epic 9 — configurable worktree setup with file copying, dependency installation, and build validation before LLM invocation.

**Problem:** Git worktrees only contain tracked files. Untracked files (`.env`, `local.settings.json`) and installed dependencies (`node_modules/`, `.venv/`) are absent. Without bootstrap, worktree subprocesses fail at quality gates, wasting LLM tokens on a broken environment.

**Three-phase bootstrap pipeline:**

1. **Copy files** — Copy listed untracked files/directories from project root to worktree using `shutil.copy2` (files) / `shutil.copytree` (directories). Configurable strict mode: missing file = warning (default) or error.
2. **Setup commands** — Run ordered list of shell commands sequentially in worktree cwd (e.g., `pip install -e .`, `npm ci`). Each worktree needs its own installed dependencies since worktrees don't share working directory files. Non-zero exit = failure.
3. **Validation command** — Single smoke test command (e.g., `pytest -q -x`, `npm run build`). Non-zero exit = failure.

**Canary pattern:**

All worktrees fork from the same base branch HEAD. If bootstrap fails for one worktree, it will fail for all. The orchestrator uses a canary pattern to fail fast:

```
1. First story in batch = canary
2. Create canary worktree
3. Run full bootstrap (copy + setup + validation)
4. If FAIL → abort entire parallel run, clean up, exit with error
5. If PASS → proceed with remaining worktrees:
   - Each runs copy + setup (needs own dependencies)
   - Skip validation (canary proved the recipe works)
   - If setup fails for a non-canary → block that story, continue others
```

**Configuration (`ParallelConfig` additions):**
```python
copy_to_worktree: list[str] = []       # Files/dirs to copy (e.g., [".env", "secrets/"])
copy_strict: bool = False               # True = error on missing, False = warn
setup_commands: list[str] = []          # Sequential commands (e.g., ["pip install -e ."])
validation_command: str | None = None   # Smoke test (e.g., "pytest -q -x")
bootstrap_timeout: int = 120            # Per-command timeout in seconds
```

**Zero-overhead default:** When no bootstrap fields are configured, `bootstrap_worktree()` returns immediately with `success=True`. Existing users experience no change.

**Result model:**
```python
class BootstrapResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    success: bool
    failed_phase: str | None       # "copy" | "setup" | "validation" | None
    error_message: str | None
    output: str                     # Captured stdout/stderr for diagnostics
```

**Integration point:** `orchestrator.py` calls `bootstrap_worktree()` via `asyncio.to_thread()` between `create_worktree()` and subprocess spawn. The canary gate blocks all other story spawns until bootstrap completes.

**Error handling:**
- Canary bootstrap failure → abort entire run, clean up worktree, log full diagnostic output
- Non-canary setup failure → mark story as blocked (`block_reason="Bootstrap setup failed"`), clean up worktree, continue other stories
- All subprocess calls use `timeout` parameter — no hanging processes
- Log prefix: `[BOOTSTRAP]` for all bootstrap-related messages

**Affects:** `config.py` (new fields), `bootstrap.py` (new module), `orchestrator.py` (canary integration), `__init__.py` (exports)

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

**Nice-to-Have Gaps: 3**
- Performance baseline measurements — will be measured during implementation
- Integration test fixture strategy for git worktree operations — addressed in story acceptance criteria
- Worktree bootstrap for untracked files and dependency installation — addressed in Epic 9 (Worktree Bootstrap & Validation)

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

---

# Extension: Cursor Provider (Composer 2.5) + Linux Migration

_Appended 2026-06-12. Extends the completed parallel-story-execution architecture with a new feature scope. Input: `requirements-cursor-provider.md`._

## Project Context Analysis (Cursor Provider + Linux)

### Requirements Overview

**Functional Requirements:**
15 FRs in three clusters, each mapping to a distinct architectural surface:

1. **Provider implementation** (FR1–FR8): `BaseProvider` contract compliance, stream-json NDJSON parsing, dual write/read-only invocation modes, and the model-verification cost guard. The risk here is external — the Cursor CLI's behavior — not internal design.
2. **Integration** (FR9–FR13): CLI binary resolution (`agent`/`cursor-agent`), config schema extension (`provider: cursor` in master and multi), Evidence Score compatibility, inherited timeout/grace machinery, >32K prompt delivery.
3. **Platform** (FR14–FR15): the one real code fix (SIGTERM→SIGKILL escalation in `terminate_process()`) and Linux deployment documentation.

**Non-Functional Requirements:**
5 NFRs that shape the design: zero Windows regression (NFR1), strict toolchain compliance with mocked-subprocess tests only (NFR2), cost safety — never silently run `composer-2.5-fast` (NFR3), no orphaned `agent` processes on either platform (NFR4), non-fatal validator failures (NFR5).

**Scale & Complexity:**

- Primary domain: CLI tool / provider subsystem extension (brownfield)
- Complexity level: Medium — one new module following an established pattern; the complexity is concentrated in defensive handling of a volatile external CLI, not in internal structure
- Estimated architectural components: 1 new provider module + 3 minor existing-code changes (`resolve_cli_path()`, config models, `_windows.py`) + deployment documentation

### Technical Constraints & Dependencies

- **Cursor CLI (`agent`)** — external dependency, auto-updates by default with no opt-out, exclusive access path to Composer 2.5 (no public API). Version pinning only via direct tarball.
- **NDJSON output contract** — success signal is the terminal `{"type":"result"}` event, *not* exit code; errors surface only on stderr; stream may end without a terminal event.
- **Auth** — `CURSOR_API_KEY` (Pro plan) via package-root `.env`, consistent with existing key handling.
- **Existing `BaseProvider` invariants** — 6-kwarg `invoke()` signature, `ResultCollector` streaming, grace-period timeout machinery: all inherited unchanged.
- **Known upstream bugs** (June 2026): silent switch to `composer-2.5-fast` (6× cost), historical `-p` hangs, exit-code-1-after-success reports.
- **Document drift note:** the Provider Subsystem section above predates `CodexProvider` (May 2026). Current reality: three providers (claude, gemini, codex); cursor becomes the fourth.

### Cross-Cutting Concerns Identified

1. **Cost containment** — the model-verification guard (system-init event check) protects every phase that uses this provider; a silent fast-variant run turns a $2 dev_story into $12.
2. **Process lifecycle** — the CLI's hang history combined with SIGTERM-only Unix kill = orphan risk on the very platform we're moving to. FR14 is load-bearing for NFR4.
3. **Platform duality** — code stays cross-platform (Windows untouched for existing providers) while validation shifts to Linux. No code may assume Linux-only.
4. **External CLI volatility** — auto-update can change CLI behavior mid-epic-run; parsing must be defensive and version logged at invocation.
5. **Phase-mode duality** — one provider, two safety postures: write-mode master (`--force --trust`) vs read-only validator (deny-config). The mode must be selected by phase context, never defaulted to write.

## Starter Template Evaluation (Cursor Provider Extension)

### Primary Technology Domain

Brownfield extension of an existing Python 3.11+ CLI tool. No starter template applies — the architectural foundation is the existing bmad-assist-lite codebase and its established provider pattern.

### Architectural Foundation (Existing)

- **Implementation template:** `providers/codex.py` (`CodexProvider`) — the proven subprocess + NDJSON streaming provider. CursorProvider is the third instantiation of this shape (after Gemini's subprocess streaming and Codex's NDJSON parsing), satisfying the Rule of Three; shared helpers may be extracted *after* duplication is observed, not before.
- **Contract:** `BaseProvider` ABC — Template Method `invoke()` with `_do_invoke()` / `_cleanup()` / `parse_output()` / `supports_model()` extension points. All timeout, grace-period, and collector machinery inherited unchanged.
- **Toolchain:** existing pyproject.toml, strict mypy + Pydantic plugin, ruff (line 100), pytest with autouse singleton-reset fixtures. Zero new Python dependencies.

### Environment Initialization (Linux Box)

The only "initialization command" in this feature is the deployment bootstrap, not a project scaffold:

```bash
# Cursor CLI (installs to ~/.local/bin/agent)
curl https://cursor.com/install -fsS | bash
agent --version && agent --list-models   # spike S5: verify composer-2.5 visible

# Project
git clone <repo> && cd bmad-assist-lite
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# CURSOR_API_KEY goes in the package-root .env (existing convention)
```

**Note:** Running spike S5 (`agent --list-models` on the Pro key) should be the first action on the Linux box — it validates the feature's premise before any code is written.

## Core Architectural Decisions (Cursor Provider Extension)

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
- Mode selection via `allowed_tools` (D2), read-only enforcement layering (D3), terminal-event-based success detection (D5), stderr-tail error surfacing (D7)

**Important Decisions (Shape Architecture):**
- Cost guard behavior (D6), model scope (D9), binary resolution order (D10), SIGKILL escalation (D12)

**Deferred Decisions (Post-MVP):**
- `--stream-partial-output` incremental deltas (complete-message streaming suffices initially); CLI version pinning (accept auto-update until S4 proves otherwise); Linux CI runner (manual validation gate first); shared NDJSON helper extraction across Codex/Cursor (wait for observed duplication)

### Provider Invocation & Mode Selection

**D1 — Canonical invocation.**
`agent -p --output-format stream-json --model <model> [--force] [--trust] <prompt>` with `cwd` set to the project/worktree root. Prompt passed as argv on Linux (~2MB limit makes the Windows 32K concern moot); stdin delivery adopted only if spike S1 confirms semantics. `--trust` included in headless invocations.

**D2 — Mode selection via `allowed_tools` (no signature change).**
`allowed_tools=None` (master phases) → write mode: `--force --trust`. `allowed_tools` = restricted list (multi-LLM phases) → read-only mode. This reuses the exact channel Claude SDK (hard, SDK `tools`) and Codex (soft, prompt warning) already interpret. The 6-kwarg `invoke()` contract is untouched.

**D3 — Read-only enforcement: layered.**
1. Omit `--force` — file writes physically impossible (proposals only).
2. Temp-create `<cwd>/.cursor/cli.json` with `{"permissions": {"deny": ["Write(**)", "Shell(**)"]}}` — covers shell execution, which omitting `--force` does not confirm. Create **only if absent** (never clobber a user's file; if pre-existing, leave untouched and log at DEBUG). Remove in `_cleanup()` (guaranteed via `finally`). Crash-leak protection: `loop/cleanup.py` resume sweep also removes a bmad-created marker variant. Concurrent read-only validators in the same cwd write identical content via atomic temp + `os.replace` — idempotent.
3. Codex-style prompt restriction warning appended — parity with existing multi-LLM convention.

**D4 — Activity tracking includes tool events.**
Assistant-message text feeds `collector.add()`. Tool-call started/completed events also mark collector activity (without adding text) — otherwise a long quality-gate command inside `code_review_synthesis` would exceed `ACTIVE_STREAM_THRESHOLD=30s` between assistant messages and wrongly deny the grace period on timeout.

### Output Parsing & Error Handling

**D5 — Success = terminal result event, not exit code.**
Final text = `result` field of the `{"type":"result"}` event; `session_id` captured into the result. A non-zero exit *after* a received result event is logged and ignored (documented upstream quirk).

**D6 — Cost guard: warn + continue.**
Parse the system-init event's `model` field. On mismatch with the requested model: WARNING log naming both models, actual model recorded in the `ProviderResult`. No abort — tokens already spent are sunk; visibility is the goal.

**D7 — Failure mapping.**
Missing terminal event (stream ended early) or non-zero exit without result event → `ProviderError` carrying the **tail** of stderr (existing Codex convention). Timeout path unchanged: internal `TimeoutError` → base-class grace machinery.

### Process Lifecycle & Platform

**D8 — Subprocess management.**
`subprocess.Popen` + `get_subprocess_kwargs()` (CREATE_NO_WINDOW / `start_new_session=True`), stdout/stderr daemon reader threads via existing `start_stream_reader_threads()`, kill path through `terminate_process()`. Identical shape to Codex.

**D12 — SIGKILL escalation (FR14).**
`terminate_process()` Unix branch: `killpg(SIGTERM)` → poll process group up to 5s → `killpg(SIGKILL)`. Synchronous block ≤5s is acceptable (the Windows taskkill path already blocks up to 10s). Brings the implementation up to its own docstring contract.

### Configuration & Integration

**D9 — Model scope: `composer-*` only.** `supports_model()` accepts `composer-` prefix; no `auto`, no pass-through of other vendors' models. Default model: `composer-2.5`.

**D10 — Binary resolution: `cursor-agent` before `agent`.**
3-tier order preserved (config `providers.cli_paths.cursor` → PATH → known locations), but within each tier try `cursor-agent` first — `agent` is dangerously generic as a PATH name. Linux known path `~/.local/bin` already present.

**D11 — Auth & environment.** `CURSOR_API_KEY` flows from package-root `.env` into the subprocess environment (inherited; no explicit flag). CLI version logged once per process at first invocation (`agent --version`, cached) so auto-update drift is diagnosable from logs.

### Linux Migration & Deployment

**D13 — Validate, don't rewrite.** One code fix (D12). Validation gate before first real epic on the box: full `pytest` green → spikes S1/S2/S5 → one complete story loop on a sample project. Windows-only tests get `sys.platform` skip markers as discovered, not preemptively.

**D14 — Deployment documentation** at `docs/linux-deployment.md`: CLI install, API key placement, project bootstrap, spike checklist (S1–S5 from requirements).

### Decision Impact Analysis

**Implementation sequence:** D12 (platform fix, independent) → D10 (resolution) → D1/D2/D5/D7 (provider core) → D3/D4 (mode + activity refinements) → D6/D11 (guards + logging) → D13/D14 (Linux validation).

**Cross-component dependencies:** D3's cleanup hooks into `loop/cleanup.py` (crash recovery); D6 requires a `ProviderResult` field check for recording actual model; D2 depends on handlers continuing to pass `allowed_tools` for multi phases (no handler changes needed).

## Implementation Patterns & Consistency Rules (Cursor Provider Extension)

### Pattern Categories Defined

Existing foundation: all 56 rules in `project-context.md` apply unchanged (frozen Pydantic models, absolute imports, atomic writes, singleton resets, etc.). The rules below cover only what is *new* variance introduced by this feature — 8 conflict points where an implementing agent could plausibly diverge.

### NDJSON Parsing Patterns

- **Tolerant line parsing** — decode stdout as UTF-8 with `errors="replace"`; each line parsed independently; malformed JSON lines logged at DEBUG and **skipped, never raised**; unknown event `type` values ignored silently. The stream is an external contract that auto-updates — parsing must degrade, not crash.
- **Event dispatch by `type` field** — a single dispatch point (dict or if/elif chain in one function), not type-checks scattered across the reader loop. Event handling order: system init (capture `model`, verify against requested) → assistant messages (text → `collector.add()`) → tool events (mark activity only) → result (capture final text + `session_id`, set completion flag).
- **Completion flag over exit code** — a `result_received` boolean decides success. Exit code is logged but only consulted when no result event arrived.

### Naming & Placement Patterns

- Module: `src/bmad_assist_lite/providers/cursor.py`; class `CursorProvider`; `provider_name` returns `"cursor"`; config value `provider: cursor`; `providers.cli_paths.cursor` for binary override.
- Constants at module top, UPPER_SNAKE: `DEFAULT_CURSOR_MODEL = "composer-2.5"`, `CURSOR_BINARY_NAMES = ("cursor-agent", "agent")`, deny-config content as a frozen constant.
- Tests: mirror the Codex provider's test file naming and class-grouping style (`tests/test_cursor*.py`, classes like `TestCursorParseOutput`).
- SIGKILL escalation constant lives in `_windows.py` next to its user: `SIGTERM_GRACE_SECONDS = 5`.

### Mode Selection Pattern

- **One predicate, stated once:** write mode ⟺ `allowed_tools is None`. An empty or restricted list → read-only. No tool-name inspection (no "contains Write" logic) — the predicate stays dumb and greppable.
- **One command builder:** a single `_build_command(model, write_mode)` helper returns the full argv list. Mode-dependent flags (`--force`, deny-config creation) are decided only there and in `_do_invoke()`'s setup — never scattered.

### Deny-Config Lifecycle Pattern

- Path: `<cwd>/.cursor/cli.json`. Created **only if absent**, written atomically (temp + `os.replace`).
- **Ownership marker:** when the provider creates the file, it records the created path in `.bmad-assist-lite/cache/cursor-deny-config.marker` (in the project, not inside `.cursor/` — the CLI may rewrite its own dir). `_cleanup()` deletes the deny file only if the marker says we created it, then deletes the marker. `loop/cleanup.py`'s resume sweep applies the same marker check (crash recovery).
- Never modify a pre-existing `.cursor/cli.json` — log at DEBUG and rely on the remaining layers (no `--force`, prompt warning).

### Error & Logging Patterns

- **Error format parity with Codex:** `ProviderError` messages carry the **tail** of stderr using the same truncation constant/convention as `codex.py` — do not invent a new length or format.
- **Prompt restriction warning:** reuse the exact `COMMON_TOOL_NAMES` construction from `codex.py` (shared constant from `base.py`) — same wording, not a paraphrase.
- **Cost-guard warning format:** `logger.warning("Cursor model mismatch: requested %s, got %s", requested, actual)` — one line, both models named, greppable.
- User-visible streaming via `write_progress()` with `color_index`; diagnostics via module `logger`. Never `print()`.

### Subprocess Patterns

- argv list invocation (never `shell=True` for the agent binary); kwargs from `get_subprocess_kwargs()`; reader threads from `start_stream_reader_threads()`; kill via `terminate_process()`. Environment: inherited `os.environ` (carries `CURSOR_API_KEY`) — no env surgery.
- CLI version: `agent --version` run lazily once per process, cached in a module-level variable, logged at INFO.

### Testing Patterns

- All provider tests mock `subprocess.Popen` — no live CLI invocation anywhere in the suite (NFR2). Fixture NDJSON streams as multi-line strings, including the three failure shapes: missing result event, malformed lines, model-mismatch init event.
- Platform-specific tests use `@pytest.mark.skipif(sys.platform == "win32", ...)` (or the inverse) — added when a test actually fails cross-platform, not preemptively.

### Enforcement Guidelines (Cursor Extension)

**All AI agents MUST:**
- Treat the terminal result event as the only success signal (never exit code alone)
- Keep the write-mode predicate `allowed_tools is None` — exactly one place
- Never crash on unparseable NDJSON lines or unknown event types
- Never modify a user's pre-existing `.cursor/cli.json`

**Anti-patterns:**
- ❌ Raising on the first malformed NDJSON line (auto-updating CLI will eventually emit something new)
- ❌ Writing deny rules into the *global* `~/.cursor/cli-config.json` (would cripple write-mode runs)
- ❌ Spawning `agent --version` on every invocation (one subprocess per call is enough)
- ❌ Copy-pasting Codex's restriction prompt text instead of importing the shared constant

## Project Structure & Boundaries (Cursor Provider Extension)

### Directory Structure (New and Touched Files Only)

```
bmad-assist-lite/
├── src/bmad_assist_lite/
│   ├── providers/
│   │   ├── cursor.py                 [NEW]   CursorProvider — command builder, NDJSON event
│   │   │                                     dispatch, deny-config lifecycle, cost guard,
│   │   │                                     parse_output, supports_model
│   │   ├── __init__.py               [TOUCH] lazy-import map + built-ins dict gain "cursor"
│   │   ├── base.py                   [TOUCH] resolve_cli_path(): known binary names for
│   │   │                                     "cursor" → ("cursor-agent", "agent")
│   │   └── _windows.py               [TOUCH] terminate_process(): SIGTERM→SIGKILL escalation;
│   │                                         new constant SIGTERM_GRACE_SECONDS = 5
│   ├── core/
│   │   └── config.py                 [TOUCH] provider name validation accepts "cursor"
│   │                                         (Master + Multi provider configs)
│   └── loop/
│       └── cleanup.py                [TOUCH] resume sweep: remove orphaned cursor deny-config
│                                             via marker file check
├── tests/
│   ├── test_cursor_provider.py       [NEW]   NDJSON fixtures (success, missing-result,
│   │                                         malformed-lines, model-mismatch), mode predicate,
│   │                                         deny-config lifecycle, stderr-tail errors
│   └── (existing _windows tests)     [TOUCH] escalation behavior, Unix-marked
├── docs/
│   └── linux-deployment.md           [NEW]   install, auth, bootstrap, spike checklist S1–S5
├── CLAUDE.md                         [TOUCH] "Changing Models" section gains cursor/composer-2.5
└── bmad-assist-lite.yaml             [TOUCH] example: cursor master config (user-edited)
```

Five existing files touched, two new source-adjacent files, one new test file. No handler, compiler, state, or parallel-module changes.

### Architectural Boundaries

- **Provider boundary:** `CursorProvider` ↔ rest of system exclusively through the `BaseProvider` contract. Handlers, workflow compilation, Evidence Score parsing — all unaware a new provider exists.
- **Mode boundary:** phase handlers keep passing `allowed_tools` exactly as today; the write/read-only interpretation lives entirely inside `cursor.py`.
- **Platform boundary:** all platform-conditional code remains in `_windows.py`. `cursor.py` contains zero `sys.platform` checks.
- **Deny-config ownership:** `cursor.py` owns create/remove within an invocation (`_cleanup()`); `loop/cleanup.py` owns crash-recovery sweep. Both gate on the marker file — neither ever touches a user-authored `.cursor/cli.json`.
- **Config boundary:** `config.py` validates the provider name only; model validity is the provider's concern (`supports_model`), consistent with gemini/codex.

### Requirements to Structure Mapping

| FRs | Location |
|-----|----------|
| FR1–FR8, FR11–FR13 (provider core, parsing, guards, prompt delivery) | `providers/cursor.py` |
| FR9 (binary resolution) | `providers/base.py` |
| FR10 (config schema) | `core/config.py` |
| FR14 (SIGKILL escalation) | `providers/_windows.py` |
| FR15 (deployment docs) | `docs/linux-deployment.md` |
| D3 crash sweep | `loop/cleanup.py` |
| NFR2 (test conventions) | `tests/test_cursor_provider.py` |

### Integration Points & Data Flow

```
Phase handler (unchanged)
  → BaseProvider.invoke(prompt, model, timeout, cwd, allowed_tools, ...)
    → CursorProvider._do_invoke()
        1. derive mode (allowed_tools is None → write)
        2. [read-only] create deny-config if absent + marker
        3. Popen(["cursor-agent", "-p", "--output-format", "stream-json", ...])
        4. reader threads → NDJSON dispatch:
             init → cost guard │ assistant → collector.add() │ tool → activity │ result → final text
        5. result event → ProviderResult(text, session_id, actual model)
    → _cleanup(): kill process if alive, remove deny-config via marker
  → parse_output() → handler consumes text (Evidence Score parsing for multi phases)
```

External integration: the `agent` CLI itself (auth via inherited `CURSOR_API_KEY`; workspace = `cwd`, same per-worktree isolation as other providers in parallel mode — each worktree gets its own deny-config when reviewing).

### Development Workflow Integration

- **On Windows (today):** all code paths compile, type-check, and test (mocked subprocess) — development of the provider does not require the Linux box.
- **On the Linux box:** validation gate order — `pytest` full suite → spikes S5/S1/S2 → one story loop with `master: cursor` on a sample project → first real epic.

## Architecture Validation Results (Cursor Provider Extension)

### Coherence Validation ✅

**Decision Compatibility:**
All 14 decisions verified against the live codebase, not just each other. Three assumptions were checked directly during validation: (1) `ProviderResult` already carries `model: str | None` and `provider_session_id: str | None` (base.py:304–314) — D5/D6 require no schema changes; (2) `ResultCollector.add("")` is documented to accept empty strings and update the activity timestamp (result_collector.py:33–44) — D4's tool-event activity marking works with the existing API; (3) `allowed_tools` flows through `invoke()` → `_do_invoke()` unchanged (base.py:336–432) — D2's mode predicate has its channel.

**Pattern Consistency:**
Patterns reuse existing machinery at every opportunity: `get_subprocess_kwargs()`, `start_stream_reader_threads()`, `terminate_process()`, `COMMON_TOOL_NAMES`, `write_progress()`, atomic temp + `os.replace`. The only new lifecycle (deny-config + marker) follows the established crash-recovery pattern in `loop/cleanup.py`.

**Structure Alignment:**
One new module, five touched files, zero changes to handlers/compiler/state/parallel. The provider boundary holds: nothing outside `cursor.py` knows Cursor exists except registration, config validation, and binary resolution.

### Requirements Coverage Validation ✅

**Functional Requirements:** FR1–FR15 all mapped to decisions and files (traceability table in the Structure section). FR13 (>32K prompts) is covered by the argv default on Linux; spike S1 may *improve* it (stdin) but cannot block it.

**Non-Functional Requirements:**
- NFR1 (Windows zero regression): the only existing-behavior change (`_windows.py` escalation) is in the Unix branch; Windows `taskkill` path untouched.
- NFR2: all tests mock `Popen`; no live CLI in the suite.
- NFR3: D6 cost guard, recorded in `ProviderResult.model`.
- NFR4: D12 escalation + D8 kill path; `--trust`/`--force` never leave a prompt waiting.
- NFR5: `ProviderError` flows into existing multi-validator aggregation (already non-fatal per established behavior).

### Implementation Readiness Validation ✅

Decisions carry exact flags, file paths, constants, and log formats. The patterns section pins all eight identified variance points. An implementing agent has no open design choices — only the spikes, which are deployment-gates, not design-gates.

### Gap Analysis Results

**Critical Gaps:** None.

**Important Gaps:**
- Spikes S1–S5 are open *by design* (require the Linux box); defaults are decided so none block implementation. S5 can invalidate the premise (composer-2.5 not visible to the Pro key) — run it first.
- Upstream volatility: the CLI auto-updates; the NDJSON contract could drift. Mitigated by tolerant parsing (patterns) and version logging (D11), not eliminated.

**Nice-to-Have Gaps:**
- The pre-extension Provider Subsystem section still says "Two LLM providers" — historical record, flagged by the drift note in this extension's context analysis.
- Linux CI runner deferred; manual validation gate stands in.

### Architecture Completeness Checklist (Cursor Extension)

**Requirements Analysis**
- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed
- [x] Technical constraints identified
- [x] Cross-cutting concerns mapped

**Architectural Decisions**
- [x] Critical decisions documented with versions
- [x] Technology stack fully specified
- [x] Integration patterns defined
- [x] Performance considerations addressed

**Implementation Patterns**
- [x] Naming conventions established
- [x] Structure patterns defined
- [x] Communication patterns specified
- [x] Process patterns documented

**Project Structure**
- [x] Complete directory structure defined
- [x] Component boundaries established
- [x] Integration points mapped
- [x] Requirements to structure mapping complete

### Architecture Readiness Assessment (Cursor Extension)

**Overall Status: READY FOR IMPLEMENTATION**

**Confidence Level:** High for the provider implementation (verified against live code); Medium for runtime behavior on the Linux box until spikes S1/S2/S5 run — by construction, those can only refine, not invalidate, the design (except S5, which gates the premise).

**Key Strengths:**
- Zero new dependencies, zero schema changes, zero handler changes
- Three load-bearing assumptions verified against source during validation, not assumed
- Defensive posture matched to a volatile external CLI (tolerant parsing, terminal-event success, cost guard)

**Areas for Future Enhancement:**
- `--stream-partial-output` for finer-grained streaming
- Shared NDJSON helper extraction if Codex/Cursor duplication proves real
- Linux CI runner; CLI version pinning if S4 finds auto-update breakage

### Implementation Handoff (Cursor Extension)

**AI Agent Guidelines:**
- Follow all extension decisions (D1–D14) exactly as documented
- Apply the Cursor extension patterns alongside the 56 project-context rules
- Respect the provider boundary — no changes outside the seven listed files
- Refer to this extension section for all Cursor/Linux architectural questions

**First Implementation Priority:** D12 (`_windows.py` SIGKILL escalation) — independent, testable today on Windows, and required by everything that follows. Then `cursor.py` per the D-sequence. Spike S5 runs the moment the Linux box exists.
