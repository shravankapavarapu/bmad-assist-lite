# bmad-assist-lite

Lightweight, Windows-native Python CLI that automates the **BMAD** (Breakthrough Method of Agile AI Driven Development) methodology with Multi-LLM orchestration.

Coordinates **Claude Code CLI** and **Gemini CLI** to run a 7-phase development loop:

```
create story → validate → synthesize → implement → code review → synthesize review → retrospective
```

Multiple LLMs validate and review in parallel, then a single Master LLM synthesizes findings. Only the Master modifies files.

## Quick Start

### Prerequisites

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- [Gemini CLI](https://github.com/google-gemini/gemini-cli) installed and authenticated

### Install

```bash
# Clone and install
git clone https://github.com/webdozo/bmad-assist-lite.git
cd bmad-assist-lite
python -m venv .venv
#activate virtual env
.venv\Scripts\activate.bat
pip install -e .

# Or install with dev tools
pip install -e ".[dev]"
```

### Initialize a Project

```bash
cd your-project
bmad-assist-lite init
```

This creates:
- `bmad-assist-lite.yaml` — project configuration (default: Claude master + Gemini multi)
- `_bmad-output/planning-artifacts/` — place your epic files, PRD, architecture docs here
- `_bmad-output/implementation-artifacts/` — generated stories, code reviews, sprint status

The `.bmad-assist-lite/` directory (state, cache) is created automatically on first `run`.

> **Note:** The init command creates a static config template. There is no interactive wizard — edit `bmad-assist-lite.yaml` to customize providers and models (see [Changing Models](#changing-models)).

### Add Your BMAD Documents

Place planning documents in `_bmad-output/planning-artifacts/`:

```
_bmad-output/
  planning-artifacts/
    epic-1.md           # Epic definition (or epics.md for all-in-one)
    epic-2.md           # Additional epics
    prd.md              # Product Requirements Document
    architecture.md     # Architecture/technical design
    ux.md               # UX specifications (optional)
  implementation-artifacts/
    sprint-status.yaml  # Story queue (you create this)
```

### Create Your Sprint Status

**Sprint-status.yaml is the source of truth** for what stories to work on. Create it in `_bmad-output/implementation-artifacts/`:

```yaml
# _bmad-output/implementation-artifacts/sprint-status.yaml
generated: "2026-02-12"
development_status:
  epic-1: backlog
  1-1-project-setup: backlog
  1-2-user-auth: backlog
  1-3-api-endpoints: backlog
  epic-1-retrospective: optional
  epic-2: backlog
  2-1-dashboard: backlog
```

Story keys use the format `{epic}-{story}-{title}` (e.g., `1-2-user-auth`). The loop processes backlog stories in the order they appear in the file.

Each epic referenced in the sprint status **must have a matching epic file** in `planning-artifacts/`. The tool searches for:
1. `epic-{N}.md` or `epic{N}.md` (specific file)
2. `epics.md` or `*epic*.md` (master file containing all epics)

If no epic file is found, the run stops immediately with a clear error.

### Run the Loop

```bash
# Run all epics and stories
bmad-assist-lite run

# Run a specific epic
bmad-assist-lite run --epic 1

# Run starting from a specific story
bmad-assist-lite run --epic 1 --story 2

# Resume after interrupt (Ctrl+C saves state)
bmad-assist-lite run --resume

# Verbose output
bmad-assist-lite run -vv
```

### Other Commands

```bash
# Compile and inspect a workflow prompt (for debugging)
bmad-assist-lite compile create-story --epic 1 --story 1

# Pre-fetch library docs from Context7 (requires context_docs enabled)
bmad-assist-lite fetch-docs --epic 1

# Remove a stale lock file from a crashed run
bmad-assist-lite reset-lock

# Show version
bmad-assist-lite --version
```

## Configuration

### Project Config (`bmad-assist-lite.yaml`)

```yaml
providers:
  master:
    provider: claude        # Master LLM: synthesizes and implements
    model: opus
  multi:                    # Validators/reviewers: run in parallel
    - provider: gemini
      model: gemini-2.5-flash
    - provider: claude
      model: sonnet

loop:
  story:                    # Phase order per story
    - create_story
    - validate_story
    - validate_story_synthesis
    - dev_story
    - code_review
    - code_review_synthesis
  epic_teardown:            # Phases after all stories in an epic
    - retrospective

timeouts:
  default: 300              # 5 min default
  dev_story: 1200           # 20 min for implementation

paths:
  output_folder: _bmad-output

# Library documentation fetching (opt-in)
context_docs:
  enabled: true
  max_libs: 8               # max libraries to fetch docs for
  max_tokens_per_lib: 5000  # max tokens per library from Context7
```

### Global Config (`~/.bmad-assist-lite/config.yaml`)

Optional global defaults stored in your **home directory** (e.g., `C:\Users\<you>\.bmad-assist-lite\config.yaml` on Windows, `~/.bmad-assist-lite/config.yaml` on macOS/Linux). Project config merges on top (project values win).

```yaml
providers:
  master:
    provider: claude
    model: opus
```

### Changing Models

Edit the `providers` section in `bmad-assist-lite.yaml` in your **project root** (the file created by `bmad-assist-lite init`):

```yaml
providers:
  master:
    provider: claude          # Options: claude, gemini
    model: opus               # See supported values below
  multi:
    - provider: gemini
      model: gemini-2.5-pro   # Any Gemini model string
    - provider: claude
      model: haiku
```

**Supported model values:**

| Provider | Valid Models | Default | Source File |
|----------|-------------|---------|------------|
| **claude** | `opus`, `sonnet`, `haiku`, or any full ID (e.g., `claude-sonnet-4-5-20250929`) | `opus` | `src/bmad_assist_lite/providers/claude_sdk.py` |
| **gemini** | Any model string (validated by Gemini CLI at runtime, e.g., `gemini-2.5-flash`, `gemini-2.5-pro`) | `gemini-2.5-flash` | `src/bmad_assist_lite/providers/gemini.py` |

The config model definitions (Pydantic) are in `src/bmad_assist_lite/core/config.py` — see `MasterProviderConfig` and `MultiProviderConfig`.

### Skipping Phases

Remove phases from the `loop.story` list to skip them:

```yaml
loop:
  story:
    - create_story
    - dev_story          # Skip validation, go straight to implementation
    - code_review
    - code_review_synthesis
  epic_teardown: []      # Skip retrospective
```

## The 7-Phase Loop

| Phase | Role | Provider |
|-------|------|----------|
| **create_story** | Create story file with testing requirements & quality gates | Master |
| **validate_story** | Multi-LLM quality validation with Evidence Score | Multi (parallel) |
| **validate_story_synthesis** | Synthesize validator findings with pre-calculated Evidence Score | Master |
| **dev_story** | Implement story with toolchain detection, review continuation, quality gate enforcement (9 steps) | Master |
| **code_review** | Multi-LLM adversarial review with story test requirements verification (read-only) + Evidence Score | Multi (parallel) |
| **code_review_synthesis** | Synthesize findings, apply fixes, runtime verification with detected toolchain (8 steps) | Master |
| **retrospective** | Epic retrospective after all stories complete | Master |

## Battle-Hardened Workflow Patterns

The workflow templates include patterns ported from production usage in bmad-assist, made **tech-stack agnostic** (works with Python, Node, Java, Rust, Go, etc.).

### Toolchain Auto-Detection

Dev-story and code-review-synthesis automatically detect the project's build system and determine lint/typecheck/build/test commands:

| Indicator | Ecosystem | Detected Commands |
|-----------|-----------|-------------------|
| `package.json` + lockfile | npm/pnpm/yarn | `{pkg} run lint`, `{pkg} run typecheck`, `{pkg} run build`, `{pkg} test` |
| `pyproject.toml` / `setup.py` | Python | `ruff check`, `mypy`, `pytest`, `python -m build` |
| `pom.xml` | Maven | `mvn compile`, `mvn test`, `mvn verify` |
| `build.gradle` | Gradle | `gradle build`, `gradle test`, `gradle check` |
| `Makefile` | Make | `make lint`, `make test`, `make build` |
| `Cargo.toml` | Rust | `cargo check`, `cargo clippy`, `cargo build`, `cargo test` |

### Quality Gates (BLOCKING)

Stories created by `create_story` include mandatory quality gate sections with `<!-- QUALITY-GATE: BLOCKING -->` markers. Dev-story enforces these gates before marking a story complete:

- **Testing Requirements** — Unit tests (mandatory), negative test checklist, integration/E2E tests
- **Quality Gates Table** — Lint, Type Check, Build, Unit Tests, Integration Tests, Runtime Verification
- **Runtime Verification** — App starts without errors, no runtime errors, affected functionality works

### Review Continuation

When dev-story detects a "Senior Developer Review" section from a prior code-review, it prioritizes fixing review items (`[AI-Review]` tags) before implementing remaining regular tasks.

### Multi-LLM Safety Architecture

Code-review runs multiple LLMs in parallel, so it only performs **read-only** checks (file existence, assertion quality, git reality). All command execution (build, test, lint) is deferred to **code-review-synthesis**, which runs a single Master LLM.

| Phase | Command Execution | Why |
|-------|-------------------|-----|
| code-review | **NO** (read-only) | Multiple LLMs run in parallel — commands would conflict |
| code-review-synthesis | **YES** (full runtime verification) | Single Master LLM — safe for commands |

## Library Documentation (Context7)

When enabled, bmad-assist-lite fetches up-to-date API documentation for your project's dependencies from [Context7](https://context7.com) and injects them into dev-story and code-review-synthesis prompts. This helps LLMs use correct, current APIs instead of hallucinating outdated patterns.

**This feature is opt-in.** Add to your `bmad-assist-lite.yaml`:

```yaml
context_docs:
  enabled: true
  max_libs: 8               # max libraries to fetch (default: 8)
  max_tokens_per_lib: 5000  # max tokens per library (default: 5000)
```

### How It Works

1. **Detection** — Parses `package.json`, `pyproject.toml`, `requirements.txt`, or `Cargo.toml` for dependencies. Also scans epic and architecture docs for framework mentions.
2. **Fetching** — Resolves each library via Context7's API, fetches documentation as markdown.
3. **Caching** — Docs are cached per-library in `.bmad-assist-lite/cache/lib-docs/`. Fetched once, reused across epics.
4. **Injection** — Cached docs are injected into the compiler context for dev-story and code-review-synthesis phases.

### Requirements

```bash
# httpx is required (included in [dev] and [context7] extras)
pip install -e ".[context7]"
```

### Manual Pre-fetch

```bash
# Fetch docs for a specific epic (useful for debugging/inspection)
bmad-assist-lite fetch-docs --epic 1 -p /path/to/project -vv
```

### Optional: API Key

Context7 works without an API key (anonymous tier, rate-limited). For higher limits, set:

```bash
# In .env file (auto-loaded by bmad-assist-lite)
CONTEXT7_API_KEY=your-key-here

# Or as environment variable
export CONTEXT7_API_KEY=your-key-here
```

## Sprint Status Tracking

Sprint status lives at `_bmad-output/implementation-artifacts/sprint-status.yaml` and serves as the **single source of truth** for story discovery and progress tracking.

### How It Works

- **Sprint-status-driven discovery**: On `run`, the tool reads `sprint-status.yaml` to find backlog stories, validates epic files exist, then builds the story queue
- **Auto-sync**: After every phase execution, `state.yaml` is projected onto `sprint-status.yaml` with story/epic statuses
- **Story caching**: Resolved story queue (epic files, story keys) is cached to `.bmad-assist-lite/cache/story-queue.yaml` so workflows don't re-parse sprint-status
- **Resume validation**: On `--resume`, the saved state is cross-checked against sprint-status — done stories/epics are skipped automatically
- **Crash recovery**: On resume, partial `*.tmp` files in the cache are cleaned up, and DEV_STORY phase warns about uncommitted git changes

### Sprint Status File Format

```yaml
# _bmad-output/implementation-artifacts/sprint-status.yaml
generated: '2026-02-12'
development_status:
  epic-1: backlog
  1-1-project-setup: done
  1-2-user-auth: ready-for-dev
  1-3-api-endpoints: backlog
  epic-1-retrospective: optional
  epic-2: backlog
  2-1-dashboard: backlog
```

**Valid statuses:** `backlog`, `ready-for-dev`, `in-progress`, `review`, `done`, `blocked`, `deferred`, `optional`

**Phase-to-status mapping:**

| Phase | Status set |
|-------|-----------|
| `create_story` | `ready-for-dev` |
| `validate_story` / `validate_story_synthesis` | `in-progress` |
| `dev_story` | `in-progress` |
| `code_review` / `code_review_synthesis` | `review` |
| `retrospective` | `done` |

You can manually edit this file to mark stories as `done` or `deferred` — the loop will respect those statuses on next run.

## Project Structure

```
src/bmad_assist_lite/
  cli.py                    # 5 Typer commands: run, init, compile, reset-lock, fetch-docs
                            # Sprint-status-driven story discovery + epic file validation
  core/                     # Config, state, paths, exceptions, async utils
    sprint_status.py        # Sprint status model + YAML I/O + backlog discovery
    sprint_sync.py          # State → sprint-status one-way sync
    resume_validation.py    # Resume state validation against sprint-status
    paths.py                # Centralized path resolution (planning-artifacts, implementation-artifacts)
  providers/                # Claude SDK + Gemini CLI implementations
  compiler/                 # Workflow compilation pipeline
    workflows/              # 7 workflow-specific compiler modules
  loop/                     # Main loop, dispatch, transitions, signals, locking
    handlers/               # 7 phase handler implementations
    cleanup.py              # Crash recovery (temp file cleanup)
  context_docs/             # Context7 library doc fetching, caching, injection
  validation/               # Evidence Score system (scoring, parsing, aggregation)
  plugins/                  # Plugin protocols, registry, loader
  bmad/                     # Epic/story markdown parser
  workflows/                # Bundled workflow templates (YAML + XML/MD)
```

### Directory Layout (per project)

```
your-project/
  bmad-assist-lite.yaml               # Project config
  _bmad-output/
    planning-artifacts/               # Epic files, PRD, architecture (user-created)
      epic-1.md
      prd.md
      architecture.md
    implementation-artifacts/         # Generated stories, reviews, sprint status
      sprint-status.yaml              # Source of truth for story queue (user-created)
      1-1.md                          # Generated story files
      story-validations/
      code-reviews/
      retrospectives/
  .bmad-assist-lite/                  # Internal state (auto-created on first run)
    state.yaml                        # Loop state (current epic/story/phase)
    cache/
      story-queue.yaml                # Cached resolved story queue
      lib-docs/                       # Cached library documentation (Context7)
      epic-libs.yaml                  # Epic → libraries mapping
    plugins/                          # Local plugins
```

## Evidence Score System

The Evidence Score system provides deterministic quality scoring across validation and code review phases:

### Scoring Formula

| Severity | Score | Examples |
|----------|-------|---------|
| **CRITICAL** | +3 | Security vulnerabilities, data corruption, blocking bugs, task completion lies |
| **IMPORTANT** | +1 | SOLID violations, performance issues, missing tests, AC gaps |
| **MINOR** | +0.3 | Style violations, documentation issues, minor refactoring |
| **CLEAN PASS** | -0.5 | Each category with no issues found |

### Verdicts

| Verdict | Score Range | Meaning |
|---------|-------------|---------|
| **EXCELLENT / EXEMPLARY** | score <= -3 | Many clean passes, minimal issues |
| **PASS / APPROVE** | score < 4 | Acceptable quality |
| **MAJOR REWORK** | 4 <= score < 7 | Significant issues |
| **REJECT** | score >= 7 | Critical problems |

### How It Works

1. **Parallel validators** each produce structured findings with severity ratings
2. **Evidence Score parser** extracts findings from LLM output (table or bullet format)
3. **Aggregator** deduplicates findings across validators using fuzzy matching (85% similarity threshold), tracks consensus
4. **Pre-calculated score** is injected into the synthesis prompt so the Master LLM uses the deterministic verdict

## Security Review

The code review phase includes a dedicated **Security Vulnerability Scan** (substep 4i) that checks:

- **Credential exposure**: hardcoded secrets, logged credentials
- **Injection vectors**: SQL, command, template injection
- **Authentication issues**: weak validation, session problems
- **Authorization gaps**: missing permission checks
- **Data exposure**: sensitive data in logs, responses
- **Dependency vulnerabilities**: known CVEs

Security vulnerabilities are automatically rated **CRITICAL (+3 points)** in the Evidence Score, ensuring they trigger MAJOR REWORK or REJECT verdicts.

## Security Considerations

bmad-assist-lite includes several built-in protections, but there are risks users should be aware of when running LLM-orchestrated development:

### Built-in Protections

| Risk | Mitigation | File |
|------|-----------|------|
| **TOCTOU race on lock file** | Atomic exclusive file create using `os.O_CREAT \| os.O_EXCL` — no gap between check and create | `loop/locking.py` |
| **LLM output DoS** | Caps on parsed content (500KB), finding descriptions (1KB), and findings per report (100) to prevent memory/CPU exhaustion from oversized LLM responses | `validation/evidence_score.py` |
| **Path traversal via config_source** | Workflow `config_source` values are resolved and verified to be within the project root before loading | `compiler/variables.py` |
| **Path traversal via glob patterns** | All glob matches are resolved to canonical paths and rejected if outside the base directory | `compiler/discovery.py` |
| **Symlink attacks on local plugins** | Plugin directory is resolved to a canonical path before loading; a warning is logged whenever local plugins are loaded | `plugins/loader.py` |

### User Responsibilities

- **Local plugins execute arbitrary code.** Only place trusted `.py` files in `.bmad-assist-lite/plugins/`. Ensure the directory is only writable by your user account.
- **LLMs can produce malicious code.** The Master LLM modifies your project files during `dev_story`. Always review generated code before committing. The code review phase helps catch issues, but is not a guarantee.
- **Config files are trusted input.** `bmad-assist-lite.yaml` and `config_source` paths control what files the compiler reads. Don't accept config files from untrusted sources.
- **Sprint-status.yaml is the source of truth.** This file controls which stories are queued for development. Manually editing story statuses to `done` will cause those stories to be skipped. If someone with write access marks stories done prematurely, work will be skipped silently.
- **Provider credentials.** Claude and Gemini CLI tools manage their own authentication. bmad-assist-lite does not store or handle API keys directly, but ensure your CLI tools are properly secured.

## Adding Features from bmad-assist

bmad-assist-lite is designed to be extended via plugins without modifying core code. Here's how to port features from the full [bmad-assist](https://github.com/webdozo/bmad-assist) project.

### Plugin System Overview

Three plugin protocols enable extensibility:

| Protocol | Purpose | Example |
|----------|---------|---------|
| `ProviderPlugin` | Add new LLM providers | Codex, OpenCode, Amp, Cursor |
| `PhasePlugin` | Add new phases to the loop | TestArch, Deep Verify, QA |
| `WorkflowPlugin` | Add new workflow templates | Custom validation workflows |

### Method 1: Local Plugins (Quick & Project-Specific)

Create Python files in `.bmad-assist-lite/plugins/` in your project root. They're auto-discovered at startup.

**Example: Adding a new provider**

```python
# .bmad-assist-lite/plugins/codex_provider.py
from bmad_assist_lite.providers.base import BaseProvider, ProviderResult

class CodexProvider(BaseProvider):
    @property
    def provider_name(self) -> str:
        return "codex"

    def invoke(self, prompt, *, model=None, timeout=None,
               settings_file=None, cwd=None, allowed_tools=None,
               color_index=None) -> ProviderResult:
        # Implementation here
        ...

    def parse_output(self, result):
        return result.stdout

    def supports_model(self, model):
        return model in ("codex", "codex-mini")

# Auto-registration function (called by plugin loader)
def register(registry):
    registry.register_provider("codex", CodexProvider())
```

**Example: Adding a phase handler**

```python
# .bmad-assist-lite/plugins/deep_verify_handler.py
from bmad_assist_lite.loop.handlers.base import BaseHandler

class DeepVerifyHandler(BaseHandler):
    @property
    def phase_name(self):
        return "deep_verify"

    def build_context(self, state):
        return self._build_common_context(state)

def register(registry):
    registry.register_phase("deep_verify", DeepVerifyHandler)
```

Then add the phase to your config:

```yaml
loop:
  story:
    - create_story
    - validate_story
    - validate_story_synthesis
    - deep_verify              # New phase
    - dev_story
    - code_review
    - code_review_synthesis
```

### Method 2: Installable Plugins (Reusable Across Projects)

Create a pip-installable package with an entry point:

```toml
# your-plugin/pyproject.toml
[project.entry-points."bmad_assist_lite.plugins"]
my_plugin = "my_plugin:register"
```

```python
# my_plugin/__init__.py
def register(registry):
    from my_plugin.provider import MyProvider
    registry.register_provider("my-provider", MyProvider())
```

Install it: `pip install ./your-plugin` and it's auto-discovered.

### Feature Migration Guide

Here's what to port from bmad-assist and how:

| bmad-assist Feature | Plugin Type | Complexity | Source Files to Reference |
|---------------------|-------------|------------|--------------------------|
| **Additional providers** (Codex, OpenCode, Amp, Cursor, Copilot, Kimi) | ProviderPlugin | Low | `bmad-assist/src/bmad_assist/providers/{name}.py` |
| **Git branch management** | PhasePlugin | Low | `bmad-assist/src/bmad_assist/core/loop/helpers.py` |
| **Sprint status tracking** | **Built-in** | — | `core/sprint_status.py`, `core/sprint_sync.py`, `core/resume_validation.py` |
| **Deep Verify** (code quality verification) | PhasePlugin + WorkflowPlugin | Medium | `bmad-assist/src/bmad_assist/deep_verify/` |
| **TestArch** (test architecture) | PhasePlugin + WorkflowPlugin | High | `bmad-assist/src/bmad_assist/testarch/` |
| **Notifications** (Telegram/Discord) | Hook in runner | Medium | `bmad-assist/src/bmad_assist/notifications/` |
| **Dashboard** (web UI with SSE) | Separate process | High | `bmad-assist/src/bmad_assist/dashboard/` |
| **Benchmarking** | PhasePlugin | Medium | `bmad-assist/src/bmad_assist/benchmarking/` |
| **Anti-pattern detection** | Compile-time hook | Low | `bmad-assist/src/bmad_assist/antipatterns/` |
| **Strategic context** (source code embedding) | Compiler extension | Medium | `bmad-assist/src/bmad_assist/compiler/source_context.py` |
| **Workflow patching** (LLM-based prompt patches) | Compiler extension | High | `bmad-assist/src/bmad_assist/compiler/patching/` |

### Porting a Provider (Step by Step)

1. Copy the provider file from `bmad-assist/src/bmad_assist/providers/{name}.py`
2. Change imports from `bmad_assist` to `bmad_assist_lite`
3. Simplify the `invoke()` signature (remove unused kwargs like `disable_tools`, `no_cache`, `thinking`)
4. Add Windows process handling using `bmad_assist_lite.providers._windows`
5. Place in `.bmad-assist-lite/plugins/` or create an installable package

### Porting a Workflow

1. Copy the workflow template directory from `bmad-assist/src/bmad_assist/workflows/{name}/`
2. Copy the compiler module from `bmad-assist/src/bmad_assist/compiler/workflows/{name}.py`
3. Simplify: remove tri-modal support, TEA references, patching hooks
4. Update imports to `bmad_assist_lite`
5. Register via a WorkflowPlugin

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests (184 tests, ~0.7s)
pytest -q --tb=line --no-header

# Run specific test file
pytest tests/test_config.py

# Type checking
mypy src/

# Lint and format
ruff check src/
ruff format src/
```

## How It Differs from bmad-assist

| | bmad-assist | bmad-assist-lite |
|---|---|---|
| **Source files** | ~150+ | ~60 |
| **Evidence Score** | Built-in (validation + code review) | Built-in (full port) |
| **Security Review** | Built-in (6-category scan) | Built-in (full port) |
| **Quality Gates** | Built-in (Next.js-specific) | Built-in (tech-stack agnostic, auto-detect toolchain) |
| **Runtime Verification** | Built-in (pnpm-specific) | Built-in (auto-detect: npm/pnpm/yarn/pytest/maven/gradle/cargo) |
| **Review Continuation** | Built-in | Built-in (detect prior review, prioritize `[AI-Review]` fixes) |
| **Providers** | 9 (Claude, Gemini, Codex, OpenCode, Amp, Cursor, Copilot, Kimi, Claude-subprocess) | 2 (Claude SDK, Gemini) |
| **Windows support** | Partial (uses SIGKILL, killpg) | Native (taskkill, CREATE_NO_WINDOW, ctypes PID check) |
| **Config tiers** | 3 (global + CWD + project) | 2 (global + project) |
| **Plugin system** | None (monolithic) | Yes (ProviderPlugin, PhasePlugin, WorkflowPlugin) |
| **Dashboard** | SSE web UI | None (console only) |
| **Notifications** | Telegram/Discord | None (add via plugin) |
| **TestArch** | Built-in (8 handlers) | None (add via plugin) |
| **Deep Verify** | Built-in | None (add via plugin) |
| **Dependencies** | ~12+ (ruamel.yaml, httpx, scipy, jinja2, etc.) | 5 core (typer, pydantic, pyyaml, claude-agent-sdk, python-dotenv) + optional httpx for Context7 |

## License

MIT
