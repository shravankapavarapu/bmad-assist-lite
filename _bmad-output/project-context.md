---
project_name: 'bmad-assist-lite'
user_name: 'Shravan'
date: '2026-03-22'
sections_completed: ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
status: 'complete'
rule_count: 56
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

- **Python** >= 3.11 (`target-version = "py311"` in ruff; `python_version = "3.11"` in mypy)
- **Pydantic** >= 2.0.0 — All models use `ConfigDict(frozen=True)`. Use `model_copy(update={...})` for state mutations, never direct assignment
- **Typer** >= 0.9.0 — CLI entry point at `cli.py`. 5 commands: `run`, `init`, `compile`, `reset-lock`, `fetch-docs`
- **PyYAML** >= 6.0 — Always use `yaml.safe_load()` for reading, `yaml.dump()` with `default_flow_style=False, sort_keys=False` for writing
- **claude-agent-sdk** >= 0.1.0 — Claude provider wraps SDK subprocess calls
- **python-dotenv** >= 1.0.0 — `.env` loaded from project root in `load_config_with_project()`
- **httpx** >= 0.27.0 — Optional extra for Context7 docs fetching
- **mypy** strict mode with Pydantic plugin (`init_forbid_extra`, `init_typed`, `warn_required_dynamic_aliases`)
- **ruff** — Line length 100. Select rules: E, F, W, I, N, D, UP, B, C4, SIM. Key ignores: D100, D104, D203, D213, D401, D417, B008, E501, SIM108
- **pytest** — `asyncio_mode = "auto"`, `addopts = "-v --tb=short -m 'not slow'"`, test files must be `test_*.py` with `test_*` functions

## Critical Implementation Rules

### Language-Specific Rules

- **Type annotations required on all functions** — mypy `strict = true` + `disallow_untyped_defs = true`. Every function signature needs full type hints including return types
- **Union syntax** — Use `X | None` (PEP 604), not `Optional[X]`. The codebase consistently uses the modern pipe syntax (Python 3.11+)
- **Frozen Pydantic models** — Every `BaseModel` subclass must include `model_config = ConfigDict(frozen=True)`. State mutations use `model_copy(update={...})`, never direct attribute assignment
- **Singleton pattern with reset** — Config (`_config`), Paths (`_paths_instance`), and dispatch handlers use module-level singletons accessed via `get_*()` functions. Each must have a corresponding `_reset_*()` function for test isolation
- **Exception hierarchy** — All custom exceptions inherit from `BmadAssistError`. Use specific subclasses (`ConfigError`, `StateError`, `ProviderError`, `CompilerError`, etc.), never bare `Exception` or `BmadAssistError` directly
- **Logging convention** — `logger = logging.getLogger(__name__)` at module top. Never use `print()` in library code; use `write_progress()` from `providers.base` for user-visible console output
- **Path handling** — Always use `pathlib.Path`, never `os.path`. Use `.resolve()` for absolute paths. Use `cached_property` for lazy path computation in `ProjectPaths`
- **Atomic file writes** — State and sprint-status files must use the temp-file + `os.replace()` pattern to prevent corruption on crash. Pattern: write to `path.with_suffix(path.suffix + ".tmp")`, then `os.replace(temp, path)`
- **Import style** — Absolute imports only (`from bmad_assist_lite.core.config import ...`). No relative imports. Heavy imports inside functions to avoid circular imports (see `cli.py` pattern)
- **`TYPE_CHECKING` guard** — Use `from typing import TYPE_CHECKING` with `if TYPE_CHECKING:` block for imports only needed by type checkers (prevents circular imports at runtime)

### Framework-Specific Rules

- **Plugin protocol pattern** — Three `@runtime_checkable` Protocol classes (`ProviderPlugin`, `PhasePlugin`, `WorkflowPlugin`) in `plugins/protocols.py`. New plugins must implement `name: str` attribute and `register(self, registry: Any) -> None` method. Built-ins register first, plugins override
- **Phase handler pattern** — All phase handlers subclass `BaseHandler` (in `loop/handlers/base.py`). Must implement `phase_name` property and `build_context()` method. Use `render_prompt()` → `invoke_provider()` flow from base class, don't call compiler/provider directly
- **Provider ABC** — New providers subclass `BaseProvider` with 4 abstract methods: `provider_name` (property), `invoke()`, `parse_output()`, `supports_model()`. The `invoke()` signature has exactly 6 keyword args — don't add more
- **Workflow compilation pipeline** — `workflow.yaml` → parser → variable resolution → file discovery → XML prompt output. Compiler modules live in `compiler/workflows/` and match workflow names (e.g., `create-story` → `create_story.py`)
- **Context Requirements validation** — The compiler's `apply_context_filter()` in `context_filter.py` validates epic Context Requirements references at compilation time. Missing non-optional documents or sections are collected into accumulators and raise a single `CompilerError` with all missing items grouped by category (missing documents, missing sections per document) and actionable fix instructions. Documents referenced with a `(skip)` directive that are missing are silently ignored (the user's intent — exclude the document — is already satisfied). References marked `(optional)` produce `logger.warning()` instead of contributing to the error
- **`(optional)` convention for Context Requirements** — Epic file Context Requirements tables support an `(optional)` marker (parsed case-insensitively) at two levels: document-level (`(full) (optional)` or `(skip) (optional)` in the Sections column sets `ContextRequirement.optional = True`) and per-section (`Section A; Section B (optional); Section C` records the index in `ContextRequirement.optional_sections: frozenset[int]`). The marker is stripped before section name matching so `Section Name (optional)` matches the heading `## Section Name` in the document. When optional refs are missing, warnings are logged instead of raising `CompilerError`
- **Two-tier config merge** — Global (`~/.bmad-assist-lite/config.yaml`) merges under project (`bmad-assist-lite.yaml`). Project values always win via `_deep_merge()`. Never read config files directly — use `load_config_with_project()` or `get_config()` singleton
- **State machine** — 10 phases in `Phase` enum. Story loop is configurable via `loop.story` list. `fix_quality_gate` is NOT in the story phase list — only reached via `next_phase` override in quality_gate handler. Epic teardown phases run after all stories complete
- **Sprint-status as source of truth** — Story discovery reads `sprint-status.yaml`, not filesystem. One-way sync: `state.yaml` → `sprint-status.yaml` (never reverse). Sprint sync is non-fatal — errors logged as warnings, never propagated
- **Windows-native process management** — Use `taskkill /F /T /PID` on Windows, `os.killpg()` on Unix. Core process cleanup in `providers/_windows.py`; bootstrap-specific process tree cleanup in `parallel/bootstrap.py` (`_kill_process_tree()`). Never use `SIGKILL`/`SIGTERM` on Windows

### Testing Rules

- **Autouse fixtures reset all singletons** — `conftest.py` has 3 autouse fixtures that run before/after every test: `reset_paths_singleton`, `reset_config_singleton` (loads `MINIMAL_CONFIG_DATA`), `reset_loop_dispatch`. Never assume singleton state persists between tests
- **Opt out of auto config** — Use `@pytest.mark.no_auto_config` to skip the auto config loading fixture when testing config loading itself or testing behavior with no config
- **MINIMAL_CONFIG_DATA** — The auto-loaded test config is `{"providers": {"master": {"provider": "claude", "model": "opus"}}}`. Tests that need multi-providers, timeouts, or other config must load their own via `load_config()`
- **Test markers** — `@pytest.mark.slow` for tests >5s (skipped by default via addopts). `@pytest.mark.integration` for integration tests. Register new markers in `pyproject.toml [tool.pytest.ini_options]`
- **Test file naming** — Files: `tests/test_*.py`. Functions: `test_*`. Group related tests in classes (`class TestLoadConfig:`, `class TestDeepMerge:`). No `__init__.py` in tests directory
- **Async tests** — `asyncio_mode = "auto"` means async test functions are auto-detected. Use `asyncio_default_fixture_loop_scope = "function"` (each test gets its own event loop)
- **No mocking singletons directly** — Use the provided `_reset_*()` functions rather than patching module globals. The reset functions are the designed test isolation mechanism
- **Coverage** — Source: `src/bmad_assist_lite`, branch coverage enabled. Exclusions: `pragma: no cover`, `__repr__`, `NotImplementedError`, `TYPE_CHECKING`, `__main__`, `@abstractmethod`

### Code Quality & Style Rules

- **Line length 100** — Enforced by ruff. Applies to all Python source in `src/`
- **Docstring style** — Module-level docstrings required (except `__init__.py` and `__main__.py` — D100/D104 ignored). First line is imperative summary. Multi-line uses Google style. D213 (multi-line-summary-second-line) and D203 (one-blank-line-before-class) are ignored in favor of D212/D211
- **File organization** — Source in `src/bmad_assist_lite/` with subsystem directories (`core/`, `providers/`, `compiler/`, `loop/`, `plugins/`, `context_docs/`, `parallel/`, `validation/`, `bmad/`, `workflows/`). Each directory has `__init__.py`. Tests flat in `tests/`
- **Naming conventions** — Modules: `snake_case.py`. Classes: `PascalCase`. Functions/methods: `snake_case`. Constants: `UPPER_SNAKE_CASE`. Private/reset functions: `_prefixed` (e.g., `_reset_config()`, `_deep_merge()`, `_utc_now()`)
- **Section separators in modules** — Use `# ============================================================================` comment blocks to separate logical sections within files (see `config.py`, `conftest.py`)
- **Ruff lint rules** — E (pycodestyle errors), F (pyflakes), W (warnings), I (isort), N (naming), D (docstrings), UP (pyupgrade), B (bugbear), C4 (comprehensions), SIM (simplify). B008 ignored (function call in default arg — needed for Typer). SIM108 ignored (no ternary enforcement)
- **Enum values match config keys** — `Phase` enum values are snake_case strings that match config keys exactly (e.g., `Phase.CREATE_STORY = "create_story"`). Workflow names use kebab-case (e.g., `create-story`). Conversion: `phase_name.replace("_", "-")`

### Development Workflow Rules

- **Entry point** — `bmad_assist_lite.cli:app` (Typer). The `run` command is the main loop entry. Story discovery happens at CLI level before the loop starts — the loop receives pre-resolved epic/story lists
- **Config loading order** — `load_config_with_project()` → loads `.env` → loads global YAML → loads project YAML → deep-merges → validates with Pydantic. Config is immutable after loading
- **Paths initialization** — `init_paths(project_root)` must be called before any `get_paths()` usage. Paths singleton is separate from config singleton. `ensure_directories()` creates all output dirs on first `run`
- **Phase execution flow** — For each story: iterate `loop.story` phases → handler.execute(state) → save state → sprint sync → advance. Quality gate failure detours through `fix_quality_gate` (up to `max_retries` attempts)
- **Multi-LLM safety** — Code-review runs multiple LLMs in parallel → read-only checks only (no command execution). Code-review-synthesis runs single Master LLM → safe for build/test/lint execution. Never run shell commands from parallel multi-LLM phases
- **Auto-commit timing** — Commits happen after quality_gate outcomes (both pass and skip_story), NOT after code_review_synthesis
- **Crash recovery** — `loop/cleanup.py` cleans `*.tmp` files from cache on resume. Lock file at `.bmad-assist-lite/running.lock` prevents concurrent runs. Use `reset-lock` command if stale
- **Context7 docs** — Fetched at epic level at startup, cached in `.bmad-assist-lite/cache/lib-docs/`. Epic table in epic file enables story-level library filtering. Auto-detection parses `package.json`/`pyproject.toml` as fallback

### Critical Don't-Miss Rules

- **Never mutate frozen Pydantic models** — `State` uses `model_copy(update={...})` for immutable transitions (e.g., `with_phase()`, `with_story()`). Direct `state.current_phase = ...` is only done inside `update_position()` which is the designated mutation point. Config models are truly immutable — no mutation at all
- **`_utc_now()` strips timezone info** — `datetime.now(timezone.utc).replace(tzinfo=None)` produces naive UTC datetimes. All timestamps in state/sprint-status are naive UTC. Never use `datetime.now()` without timezone, and always strip `tzinfo` after
- **Quality gate command sources have priority order** — Story file Quality Gates table → config `quality_gate` section → auto-detected toolchain. Agents must check all three in order, not just one
- **`fix_quality_gate` is a detour, not a listed phase** — It's in the `Phase` enum but NOT in `loop.story` config list. Reached only via `next_phase` override from quality_gate handler. After fix attempt, always returns to `quality_gate` for re-check
- **Sprint sync is one-way and non-fatal** — `state.yaml` → `sprint-status.yaml` only. Never write state based on sprint-status. Sync errors are logged as warnings, never raised as exceptions
- **Temp file cleanup on error** — Any function using the atomic write pattern must clean up temp files in its except/finally block: `if temp_path.exists(): temp_path.unlink()`
- **`os.replace()` not `shutil.move()`** — Atomic rename requires `os.replace()` which is guaranteed atomic on the same filesystem. `shutil.move()` may copy+delete which is not atomic
- **Thread safety for provider output** — `_OUTPUT_LOCK` (threading.Lock) in `providers/base.py` guards all console writes during parallel multi-LLM execution. Always use `write_progress()`, never raw `print()`
- **Story ID format** — `"{epic_num}.{story_num}"` (e.g., `"1.2"`). Sprint-status keys use full slug format (e.g., `"1-2-story-title"`). The `story_key_map` in CLI maps between them
- **Epic file validation** — `_is_dedicated_epic_file()` checks that an epic file is specifically for that epic (contains `epic-{N}` in stem), not a master fallback file. Epics without dedicated files are skipped with a warning

---

## Usage Guidelines

**For AI Agents:**

- Read this file before implementing any code
- Follow ALL rules exactly as documented
- When in doubt, prefer the more restrictive option
- Update this file if new patterns emerge

**For Humans:**

- Keep this file lean and focused on agent needs
- Update when technology stack changes
- Review quarterly for outdated rules
- Remove rules that become obvious over time

Last Updated: 2026-05-30
