# CLAUDE.md

## Project Overview

**bmad-assist-lite** is a lightweight, Windows-native Python CLI tool that automates the BMAD (Breakthrough Method of Agile AI Driven Development) methodology with Multi-LLM orchestration. It coordinates Claude Code CLI + Gemini CLI + Codex CLI + Cursor CLI to run a 10-phase development loop: create story → validate → synthesize → implement → code-review → synthesize-review → quality-gate → (fix-quality-gate) → epic-quality-gate → retrospective.

Derived from bmad-assist, with ~60 source files, 13 test files, and 16 workflow templates. Plugin architecture for extensibility.

## Build & Development Commands

```bash
pip install -e .
pip install -e ".[dev]"
pytest -q --tb=line --no-header
pytest tests/test_config.py
mypy src/
ruff check src/
ruff format src/
```

## Architecture

### Package Layout

Source in `src/bmad_assist_lite/` with entry point `cli.py` (Typer app, 5 commands: run, init, compile, reset-lock, fetch-docs).

### Core Subsystems

- **`core/`** — Config (2-tier YAML: global + project), paths singleton, state machine (10 phases), sprint status tracking, resume validation, toolchain detection (`toolchain.py`), quality gates parser (`quality_gates.py`), command runner (`command_runner.py`), exceptions, async utilities
- **`providers/`** — BaseProvider ABC (Template Method pattern) with Claude SDK, Gemini, Codex, and Cursor implementations. `base.py` defines concrete `invoke()` that creates `ResultCollector`, delegates to `_do_invoke()`, catches `TimeoutError` → `_handle_timeout()` with grace period, and calls `_cleanup()` in `finally`. Shared constants: `COMMON_TOOL_NAMES` (tool restriction prompts), `resolve_cli_path()` (3-tier CLI binary resolution: config override → PATH → known platform install locations). `result_collector.py` provides thread-safe `ResultCollector` for streaming chunk accumulation and activity tracking. Windows-safe process management in `_windows.py`. `codex.py` implements `CodexProvider` using subprocess + NDJSON stream parsing with prompt via stdin (avoids Windows 32K command line limit), structured output via `--output-schema` and `--output-last-message` file output, and `parse_output()` conversion to Evidence Score format. `gemini.py` uses `-p "." --yolo` flags for headless non-interactive mode. `cursor.py` implements `CursorProvider` using subprocess + NDJSON stream parsing with `--output-format stream-json`, write-mode predicate (`allowed_tools is None`), deny-config lifecycle for read-only invocations (`.cursor/cli.json` + marker file), and result-event-based success determination
- **`compiler/`** — Workflow compilation: parse workflow.yaml → resolve variables → discover files → generate XML prompt.
  - **Context Requirements validation** — `context_filter.py` validates epic Context Requirements references at compilation time. Missing non-optional documents or sections raise `CompilerError` (all missing items collected and reported in a single error with actionable fix instructions). Documents referenced with a `(skip)` directive (exclude from context) that are missing are silently ignored.
  - **`(optional)` convention** — Epic Context Requirements entries can include an `(optional)` marker (document-level: `(full) (optional)`; per-section: `Section Name (optional)`) so missing optional refs produce warnings instead of errors.
- **`loop/`** — Main BMAD loop orchestration with 10 phase handlers (7 LLM + 3 non-LLM quality gate), crash recovery cleanup, sprint sync, Windows-safe signals/locking
- **`plugins/`** — Plugin architecture: ProviderPlugin, PhasePlugin, WorkflowPlugin protocols with entry point + local directory discovery
- **`context_docs/`** — Context7 library documentation: `detector.py` (dependency parsing + doc scanning), `cache.py` (flat file cache + epic tracking with story-level filtering), `resolver.py` (orchestrator + compiler injection), `epic_table.py` (parses `### Context7 Library Documentation` markdown tables from epic files for explicit library-to-story mapping, skipping auto-detection and `_resolve_library_id()` calls). Opt-in via `context_docs` config
- **`parallel/`** — Parallel story execution via git worktrees. `cli.py` (Typer subcommand), `config.py` (`ParallelConfig` frozen model with concurrency, stagger, and bootstrap fields), `orchestrator.py` (async orchestrator with canary bootstrap, semaphore-based concurrency, drain mode), `bootstrap.py` (worktree bootstrap pipeline: copy files → run setup commands → run validation; returns `BootstrapResult` frozen model — not exceptions — as the primary error communication mechanism), `state.py` (`ParallelState`/`StoryState` frozen models with YAML persistence), `git_ops.py` (branch operations), `worktree_manager.py` (create/cleanup worktrees), `dependency_graph.py` (story dependency resolution), `merger.py` (merge queue with post-merge quality gates), `output.py` (`OutputMultiplexer` for concurrent console output), `report.py` (run summary generation), `recovery.py` (crash recovery), `logging.py` (parallel-specific log setup), `exceptions.py` (`ParallelError`)
  - **Canary bootstrap pattern** — When bootstrap config is set, the first worktree acts as a canary: runs full bootstrap with validation (`bootstrap_worktree(validate=True)`). If the canary fails, the entire run aborts immediately (no other worktrees created). If the canary passes, remaining worktrees run copy + setup only (`validate=False`), skipping the redundant validation. Resume (`--resume`) skips canary entirely (worktrees already bootstrapped). `[BOOTSTRAP]` log prefix for all bootstrap messages.
- **`validation/`** — Evidence Score system: deterministic scoring, parsing from LLM output, multi-validator aggregation, synthesis prompt injection
- **`bmad/`** — Epic/story markdown parser
- **`workflows/`** — Bundled workflow templates (package data). Includes battle-hardened patterns: quality gates, toolchain auto-detection, review continuation, runtime verification

### Sprint Status Tracking

Sprint-status.yaml is the **single source of truth** for story discovery and progress:

- **`core/sprint_status.py`** — `SprintStatus` Pydantic model with YAML I/O. Tracks story and epic statuses in `_bmad-output/implementation-artifacts/sprint-status.yaml`. Provides `find_backlog_stories()` and `find_next_backlog_story()` for story queue discovery
- **`core/sprint_sync.py`** — One-way sync from `state.yaml` → `sprint-status.yaml` after each phase execution. Non-fatal (errors logged as warnings, never propagated). CREATE_STORY maps to `ready-for-dev`
- **`core/resume_validation.py`** — On `--resume`, cross-checks state against sprint-status to skip done stories/epics. Safety: never advances past RETROSPECTIVE phase
- **`loop/cleanup.py`** — Crash recovery: cleans `*.tmp` files from cache on resume, warns about uncommitted git changes for DEV_STORY
- **Story discovery** — At load time (in `cli.py`), reads sprint-status.yaml for backlog stories, validates epic files exist in `planning-artifacts/`, caches resolved queue to `.bmad-assist-lite/cache/story-queue.yaml`

### Workflow Enhancements (Battle-Hardened Patterns)

Workflow templates include tech-stack agnostic patterns ported from production bmad-assist usage:

- **`create-story/template.md`** — Includes a `## Testing Requirements` section (unit/negative/integration tests) and a `## Quality Gates` table (lint, typecheck, build, tests, runtime) whose rows are parsed by `core/quality_gates.py::parse_quality_gates_table`.
  *(Corrected 2026-08-11: this previously described `<!-- QUALITY-GATE: BLOCKING -->` markers. **No such marker exists** in the template or anywhere in `src/`. The claim had propagated into ADR-0008 §1(c) and REQ-04.1, where it would have made the create-story skip predicate reject every story the tool itself generates.)*
- **`dev-story/instructions.xml`** — 9 steps: Load Story → Load Context → Detect Toolchain → Detect Review Continuation → Implement Tasks (TDD + negative tests) → Run Validations → Quality Gate Validation (BLOCKING) → Story Completion → Completion Communication
- **`code-review/checklist.md`** — Includes Story Test Requirements (BLOCKING), Runtime Verification (deferred to synthesis), and BLOCKING ISSUES summary
- **`code-review/instructions.xml`** — Step 3 includes read-only Story Test Requirements Check (NO command execution — multi-LLM parallel safety)
- **`code-review-synthesis/instructions.xml`** — 8 steps: adds Detect Toolchain + Runtime Verification (BLOCKING) before report generation. Safe for command execution (single Master LLM)

**Multi-LLM safety constraint:** Code-review runs multiple LLMs in parallel → read-only checks only. Code-review-synthesis runs single Master LLM → safe for build/test/lint execution.

**Toolchain auto-detection:** Both dev-story (step 3) and code-review-synthesis (step 6) examine project root for build system indicators (package.json, pyproject.toml, pom.xml, build.gradle, Makefile, Cargo.toml) and determine lint/typecheck/build/test commands automatically.

### Quality Gate Phases

Deterministic, non-LLM quality gate enforcement after code review synthesis:

- **`quality_gate`** (non-LLM) — Runs lint/typecheck/build/test commands. Sources commands from: story file Quality Gates table → config `quality_gate` → auto-detected toolchain. Updates story file with PASS/FAIL. On all-pass: auto-commit + mark done. On fail (first try): writes failure report to cache, routes to `fix_quality_gate`. On fail (retry): auto-commit + mark blocked, skip to next story.
- **`fix_quality_gate`** (LLM) — Reads failure report from cache, renders `fix-quality-gate` workflow, asks master LLM to fix. Always returns to `quality_gate` for re-check. NOT in the story phase list — only reached via `next_phase` override.
- **`epic_quality_gate`** (non-LLM, epic teardown) — Runs full project test suite before retrospective. If `failed_qa_stories` exist, reports them and exits with error for manual fix.

**State fields:** `failed_qa_stories` tracks stories that failed QA after retry. `qa_retry_count` tracks retry attempts per story (reset on pass or skip).

**Auto-commit timing:** Moved from after code_review_synthesis to after quality_gate outcomes (both pass and skip_story).

### Key Patterns

- **Plugin-first** — Providers, phases, and workflows are all pluggable. Built-ins register first, plugins override
- **Cross-platform process management** — All process management uses `taskkill` on Windows, `killpg` on Unix. No SIGKILL/SIGTERM on Windows. On Unix, `terminate_process()` uses SIGTERM→SIGKILL escalation: sends SIGTERM → polls `is_pid_alive()` for up to `SIGTERM_GRACE_SECONDS` (5s) → sends SIGKILL if still alive. Constant defined in `providers/_windows.py`
- **CLI binary resolution** — `resolve_cli_path()` in `base.py` resolves CLI binaries in order: config `providers.cli_paths.<name>` → `shutil.which()` → known platform install paths (Windows: `%LOCALAPPDATA%`, `%APPDATA%\npm`; Linux: `~/.local/bin`, `/usr/local/bin`, `~/.npm-global/bin`). Checks `.cmd`, `.exe`, and bare names on Windows
- **2-tier config** — `~/.bmad-assist-lite/config.yaml` (global) + `bmad-assist-lite.yaml` (project)
- **Singleton configs** — `get_config()`, `get_paths()` with `_reset_*()` for testing
- **Atomic writes** — State and sprint-status files use temp + `os.replace` pattern to prevent corruption
- **Graceful timeout** — Providers use a grace period pattern: on timeout, if `ResultCollector.is_active()` detects recent streaming activity (within `ACTIVE_STREAM_THRESHOLD=30s`), a grace period of `max(MIN_GRACE_PERIOD_SECONDS=60, timeout * GRACE_PERIOD_RATIO=0.25)` seconds is granted. After grace, partial text >= `MIN_USEFUL_RESPONSE_CHARS=200` chars → `ProviderResult(timed_out=True)`; < 200 chars → `ProviderTimeoutError`. Constants defined in `base.py`: `DEFAULT_TIMEOUT=300`, `MIN_GRACE_PERIOD_SECONDS=60`, `GRACE_PERIOD_RATIO=0.25`, `ACTIVE_STREAM_THRESHOLD=30.0`, `MIN_USEFUL_RESPONSE_CHARS=200`

### Provider Implementor Reference

New providers must extend `BaseProvider` and implement:

- **`_do_invoke()`** — Provider-specific invocation. Feed `collector.add(chunk)` as streaming text arrives. Raise `TimeoutError` when the provider's internal timeout fires. The base class `invoke()` catches it and handles grace period logic
- **`_cleanup()`** — Kill process / close connection. Called in `finally` by the base class, guaranteed to run on success, timeout, and exceptions
- **`parse_output(result)`** — Extract response text from `ProviderResult`. Multi-LLM handlers (`validate_story`, `code_review`) call this to get the scored text, so providers with structured output (e.g., Codex JSON) must convert to evidence score format here
- **`supports_model(model)`** — Return `True` if the provider supports the given model string
- **`provider_name`** property — Return the provider identifier string (e.g., `"claude"`, `"gemini"`, `"codex"`, `"cursor"`)

### Init Command

`bmad-assist-lite init` creates a minimal project scaffold:
- `bmad-assist-lite.yaml` — Default config (Claude master + Gemini multi)
- `_bmad-output/planning-artifacts/` — Place epic files, PRD, architecture docs
- `_bmad-output/implementation-artifacts/` — Generated stories, sprint status

The `.bmad-assist-lite/` directory (with `cache/`, `state.yaml`) is created automatically on first `run` via `paths.ensure_directories()`. The init command does **not** create it.

Note: Unlike bmad-assist, there is no interactive config wizard. Config is a static template — edit `bmad-assist-lite.yaml` manually to change providers/models.

## Changing Models

To change which LLM models are used, edit `bmad-assist-lite.yaml` in your project root:

```yaml
providers:
  master:
    provider: claude    # or: gemini, codex, cursor
    model: opus         # Claude: opus, sonnet, haiku (or full ID like claude-sonnet-4-5-20250929)
    effort: max         # Opus 4.7 thinking effort: low|medium|high|xhigh|max. Omit to use Claude Code's default (xhigh).
  multi:
    - provider: gemini
      model: gemini-2.5-flash  # Any Gemini model string (validated by Gemini CLI)
    - provider: codex
      model: gpt-5.3-codex    # Any gpt-*/codex-* model
    - provider: claude
      model: haiku
```

**Valid model values:**
- **Claude** (`providers/claude_sdk.py`): `opus`, `sonnet`, `haiku`, or any `claude-*` full model ID. Default: `opus`
- **Gemini** (`providers/gemini.py`): Any model string (e.g., `gemini-2.5-flash`, `gemini-2.5-pro`). Validated by Gemini CLI at runtime. Default: `gemini-2.5-flash`
- **Codex** (`providers/codex.py`): `gpt-5.3-codex`, `gpt-5.1-codex-mini`, `o4-mini`, or any `gpt-`/`codex-`/`o1-`/`o3-`/`o4-` prefixed model. Default: `gpt-5.3-codex`. Requires OpenAI API key auth (`codex login --with-api-key`); ChatGPT auth does not support model selection
- **Cursor** (`providers/cursor.py`): `composer-2.5`, `composer-2.5-fast`, or any `composer-*` prefixed model. Default: `composer-2.5`. Requires `CURSOR_API_KEY` (Pro plan) in `.env`. See `docs/linux-deployment.md` for setup

**Valid effort values (Claude Opus 4.7 only):** `low`, `medium`, `high`, `xhigh`, `max`. Forwarded to the CLI as `--effort <value>` via the SDK's `extra_args`. Ignored by Gemini, Codex, Cursor, and pre-Opus-4.7 Claude models. Default for this project: `max`.

**Config model definitions** are in `core/config.py`: `MasterProviderConfig` and `MultiProviderConfig` Pydantic models define the `provider`, `model`, and `effort` fields.

## Code Style

- Python 3.11+ target
- Line length: 100 characters
- Strict mypy with Pydantic plugin
- Ruff for linting and formatting
- `asyncio_mode = "auto"` for pytest-asyncio

## Testing Conventions

### Autouse Fixtures (in `tests/conftest.py`)

- `reset_paths_singleton` — Resets path config between tests
- `reset_config_singleton` — Loads minimal valid config. Opt out with `@pytest.mark.no_auto_config`
- `reset_loop_dispatch` — Resets handler registry between tests

### Test Markers

- `@pytest.mark.slow` — Tests >5s, skipped by default
- `@pytest.mark.integration` — Integration tests
- `@pytest.mark.no_auto_config` — Skip auto config loading

## Configuration

```yaml
# bmad-assist-lite.yaml
providers:
  master:
    provider: claude
    model: opus
    effort: max          # Opus 4.7 thinking effort: low|medium|high|xhigh|max
  multi:
    - provider: gemini
      model: gemini-2.5-flash
    - provider: codex
      model: gpt-5.3-codex
    - provider: claude
      model: sonnet
  cli_paths:  # Override CLI binary paths (useful when venv strips PATH)
    claude: "C:/Users/you/.local/bin/claude.exe"  # system CLI (avoids SDK's bundled binary)
    codex: "C:/path/to/codex.exe"
    cursor: "/home/user/.local/bin/agent"  # cursor-agent or agent binary
    gemini: "C:/path/to/gemini.cmd"

loop:
  story: [create_story, validate_story, validate_story_synthesis,
          dev_story, code_review, code_review_synthesis, quality_gate]
  epic_teardown: [epic_quality_gate, retrospective]
  # Run-level budget — both optional, both default to null (unlimited).
  # On exhaustion the loop saves state, prints which budget ran out and how to
  # continue, and exits with code 3 (distinct from 1 = failed, 130 = interrupted).
  max_stories: null   # stop after N stories; resume with `run --resume`
  max_runtime: null   # stop after N wall-clock seconds

timeouts:
  default: 300
  dev_story: 1200

paths:
  output_folder: _bmad-output

# Opt-in: fetch library docs from Context7 for dev-story/code-review-synthesis
context_docs:
  enabled: true
  max_libs: 8
  max_tokens_per_lib: 5000

# Fallback quality gate commands (auto-detected if omitted)
quality_gate:
  lint: "ruff check src/"
  typecheck: "mypy src/"
  test: "pytest -q --tb=short --no-header"
  command_timeout: 120  # per-command timeout in seconds

# Parallel story execution via git worktrees
parallel:
  max_concurrency: 3          # max concurrent stories (1-5)
  stagger_delay: 10.0         # seconds between spawns
  post_merge_fix_retries: 1   # retry attempts for post-merge quality gate fixes
  conflict_resolution_timeout: 120  # seconds for Claude CLI conflict resolution
  worktree_base_dir: null      # custom base dir for worktrees (null = auto)
  copy_to_worktree: []        # files/dirs to copy (e.g., [".env", "secrets/"])
  setup_commands: []           # sequential shell commands (e.g., ["pip install -e ."])
  validation_command: null     # smoke test command (e.g., "pytest -q -x")
  copy_strict: false           # true = error on missing copy source, false = warn
  bootstrap_timeout: 120       # per-command timeout in seconds for setup/validation

# Bounded review -> fix -> re-review loop
loop:
  review_max_iterations: 1  # fix rounds per story; 0 disables the loop entirely

review:
  blocking_severity: medium   # low|medium|high — below this, findings are recorded only
  followup_medium_weight: 3   # follow-up score: any(high) or 3*medium + 1*low >= threshold
  followup_low_weight: 1
  followup_threshold: 5

# Auto-commit story changes after quality gate pass/fail
auto_commit:
  enabled: true  # default

# Retain forensic artifacts (synthesis-diff-*, qa-failures-*) across story
# transitions by archiving them into cache/forensics/<story_id>/
forensics:
  enabled: true      # false = pre-retention behaviour (artifacts swept with the cache)
  max_stories: 20    # retention cap; oldest story archives evicted first
```
