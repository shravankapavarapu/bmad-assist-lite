# CLAUDE.md

## Project Overview

**bmad-assist-lite** is a lightweight, Windows-native Python CLI tool that automates the BMAD (Breakthrough Method of Agile AI Driven Development) methodology with Multi-LLM orchestration. It coordinates Claude Code CLI + Gemini CLI to run a 7-phase development loop: create story → validate → synthesize → implement → code-review → synthesize-review → retrospective.

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

- **`core/`** — Config (2-tier YAML: global + project), paths singleton, state machine (7 phases), sprint status tracking, resume validation, exceptions, async utilities
- **`providers/`** — BaseProvider ABC with Claude SDK + Gemini implementations. Windows-safe process management in `_windows.py`
- **`compiler/`** — Workflow compilation: parse workflow.yaml → resolve variables → discover files → generate XML prompt
- **`loop/`** — Main BMAD loop orchestration with 7 phase handlers, crash recovery cleanup, sprint sync, Windows-safe signals/locking
- **`plugins/`** — Plugin architecture: ProviderPlugin, PhasePlugin, WorkflowPlugin protocols with entry point + local directory discovery
- **`context_docs/`** — Context7 library documentation: `detector.py` (dependency parsing + doc scanning), `cache.py` (flat file cache + epic tracking), `resolver.py` (orchestrator + compiler injection). Opt-in via `context_docs` config
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

- **`create-story/template.md`** — Includes `<!-- QUALITY-GATE: BLOCKING -->` sections: Testing Requirements (unit/negative/integration tests) and Quality Gates table (lint, typecheck, build, tests, runtime)
- **`dev-story/instructions.xml`** — 9 steps: Load Story → Load Context → Detect Toolchain → Detect Review Continuation → Implement Tasks (TDD + negative tests) → Run Validations → Quality Gate Validation (BLOCKING) → Story Completion → Completion Communication
- **`code-review/checklist.md`** — Includes Story Test Requirements (BLOCKING), Runtime Verification (deferred to synthesis), and BLOCKING ISSUES summary
- **`code-review/instructions.xml`** — Step 3 includes read-only Story Test Requirements Check (NO command execution — multi-LLM parallel safety)
- **`code-review-synthesis/instructions.xml`** — 8 steps: adds Detect Toolchain + Runtime Verification (BLOCKING) before report generation. Safe for command execution (single Master LLM)

**Multi-LLM safety constraint:** Code-review runs multiple LLMs in parallel → read-only checks only. Code-review-synthesis runs single Master LLM → safe for build/test/lint execution.

**Toolchain auto-detection:** Both dev-story (step 3) and code-review-synthesis (step 6) examine project root for build system indicators (package.json, pyproject.toml, pom.xml, build.gradle, Makefile, Cargo.toml) and determine lint/typecheck/build/test commands automatically.

### Key Patterns

- **Plugin-first** — Providers, phases, and workflows are all pluggable. Built-ins register first, plugins override
- **Windows-native** — All process management uses `taskkill` on Windows, `killpg` on Unix. No SIGKILL/SIGTERM on Windows
- **2-tier config** — `~/.bmad-assist-lite/config.yaml` (global) + `bmad-assist-lite.yaml` (project)
- **Singleton configs** — `get_config()`, `get_paths()` with `_reset_*()` for testing
- **Atomic writes** — State and sprint-status files use temp + `os.replace` pattern to prevent corruption

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
    provider: claude    # or: gemini
    model: opus         # Claude: opus, sonnet, haiku (or full ID like claude-sonnet-4-5-20250929)
  multi:
    - provider: gemini
      model: gemini-2.5-flash  # Any Gemini model string (validated by Gemini CLI)
    - provider: claude
      model: haiku
```

**Valid model values:**
- **Claude** (`providers/claude_sdk.py`): `opus`, `sonnet`, `haiku`, or any `claude-*` full model ID. Default: `opus`
- **Gemini** (`providers/gemini.py`): Any model string (e.g., `gemini-2.5-flash`, `gemini-2.5-pro`). Validated by Gemini CLI at runtime. Default: `gemini-2.5-flash`

**Config model definitions** are in `core/config.py`: `MasterProviderConfig` and `MultiProviderConfig` Pydantic models define the `provider` and `model` fields.

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
  multi:
    - provider: gemini
      model: gemini-2.5-flash
    - provider: claude
      model: sonnet

loop:
  story: [create_story, validate_story, validate_story_synthesis,
          dev_story, code_review, code_review_synthesis]
  epic_teardown: [retrospective]

timeouts:
  default: 300
  dev_story: 600

paths:
  output_folder: _bmad-output

# Opt-in: fetch library docs from Context7 for dev-story/code-review-synthesis
context_docs:
  enabled: true
  max_libs: 8
  max_tokens_per_lib: 5000
```
