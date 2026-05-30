# Story 10.2 — Handoff

**Epic:** Epic 10 — Codex CLI Provider
**Story file:** _bmad-output/implementation-artifacts/10-2-provider-registry.md
**Started:** 2026-05-30T12:20:00Z

---

## Dev Summary
**Status:** done
**Files changed:**
- src/bmad_assist_lite/providers/__init__.py (modified)

**Tasks completed:** 6/6
**Decisions made:**
- Maintained alphabetical ordering for all insertion points (CodexProvider between ClaudeSDKProvider and GeminiProvider) to match the existing pattern
- Used "codex" as the registry key (lowercase, matching the pattern of "claude" and "gemini") which aligns with CodexProvider.provider_name returning "codex"
- Task 6 was verification-only: confirmed MasterProviderConfig.provider and MultiProviderConfig.provider are unconstrained str fields (no Literal, no validator, no allowlist), and get_provider() checks the _REGISTRY dict dynamically -- no config.py changes needed

**Blockers:** none

---

## Review Findings (Cycle 1)
**Verdict:** CLEAN

### Pass 1: BLIND HUNTER (Diff Analysis)

The diff adds exactly four insertion points plus the docstring update, all mechanically correct:

1. **Docstring** -- `CodexProvider: Codex CLI subprocess provider` inserted alphabetically between ClaudeSDKProvider and GeminiProvider. Correct.
2. **TYPE_CHECKING import** -- `from .codex import CodexProvider as CodexProvider` inserted alphabetically between claude_sdk and gemini imports. The `as CodexProvider` re-export pattern matches the existing convention for mypy public export recognition. Correct.
3. **`__all__`** -- `"CodexProvider"` inserted in alphabetical position between `"ClaudeSDKProvider"` and `"ExitStatus"`. Correct.
4. **`_lazy_imports`** -- `"CodexProvider": ".codex"` inserted between ClaudeSDKProvider and GeminiProvider entries. Module path `.codex` correctly points to `codex.py`. Correct.
5. **`_init_default_providers()`** -- Import `from .codex import CodexProvider` and registry entry `"codex": CodexProvider` both inserted in alphabetical order. Registry key `"codex"` matches `CodexProvider.provider_name` property (returns `"codex"`). Correct.

No import ordering issues. No registry key inconsistencies. All insertion points follow the exact same pattern as the existing ClaudeSDKProvider and GeminiProvider registrations.

### Pass 2: EDGE CASE HUNTER

1. **Import error in codex.py** -- If `codex.py` has an import error (e.g., missing dependency), the error surfaces in two places:
   - **Lazy import (`__getattr__`)**: `importlib.import_module(".codex", __package__)` would raise `ImportError`/`ModuleNotFoundError`, which propagates naturally. This is identical to how claude_sdk.py and gemini.py handle it. Acceptable.
   - **Registry init (`_init_default_providers`)**: `from .codex import CodexProvider` would raise `ImportError`. Since this runs inside `get_provider()`/`list_providers()`, the error propagates to the caller. This is the same behavior as existing providers -- no special handling exists for any provider import failure.

2. **`__getattr__` does not cache** -- The lazy import in `__getattr__` calls `importlib.import_module()` on every attribute access without caching the result on the module via `globals()[name] = cls`. This means repeated `from bmad_assist_lite.providers import CodexProvider` re-imports on each access. However, this is a pre-existing pattern (applies to all three providers equally), Python's import machinery caches modules in `sys.modules` so the `importlib.import_module` call is near-zero cost on subsequent calls, and `__getattr__` is only called for names not already in the module namespace. Not a bug introduced by this story.

3. **TYPE_CHECKING import correctness** -- The `from .codex import CodexProvider as CodexProvider` inside `if TYPE_CHECKING:` follows the exact pattern of the other two providers. The `as CodexProvider` is necessary for mypy to recognize it as a re-export. At runtime, this import never executes (guarded by TYPE_CHECKING). Correct.

4. **codex.py dependencies** -- `codex.py` imports from `bmad_assist_lite.core.exceptions`, `bmad_assist_lite.providers._windows`, `bmad_assist_lite.providers.base`, and `bmad_assist_lite.providers.result_collector` -- all internal modules. No external PyPI dependencies that could fail. The only runtime dependency is the `codex` CLI binary, which is checked via `shutil.which("codex")` at invocation time, not import time. Clean.

### Pass 3: ACCEPTANCE AUDITOR

- **AC1 (Registry lookup)** -- `get_provider("codex")` triggers `_init_default_providers()` which imports and registers `"codex": CodexProvider`. The function then returns `_REGISTRY["codex"]()` which instantiates `CodexProvider`. SATISFIED.

- **AC2 (Provider listing)** -- `list_providers()` triggers `_init_default_providers()` which populates `_REGISTRY` with "claude", "codex", "gemini". Returns `frozenset({"claude", "codex", "gemini"})`. SATISFIED.

- **AC3 (Config acceptance)** -- Verified: `MasterProviderConfig.provider` and `MultiProviderConfig.provider` are both `str` fields with no `Literal` constraint, no validator, and no allowlist (config.py lines 36 and 59). Runtime validation happens in `get_provider()` which checks `_REGISTRY` -- now includes "codex". Config with `provider: codex` will load without `ConfigError`. SATISFIED.

- **AC4 (Lazy import)** -- `_lazy_imports["CodexProvider"] = ".codex"` enables `__getattr__` to resolve `CodexProvider` via `importlib.import_module(".codex", __package__)` on first attribute access. Module is not eagerly imported at package import time. SATISFIED.

- **AC5 (Type checking)** -- `if TYPE_CHECKING: from .codex import CodexProvider as CodexProvider` provides mypy visibility. The `as CodexProvider` re-export pattern ensures mypy recognizes it as a public export of the package. SATISFIED.

### PATCH
None

### DECISION
None

### DEFERRED
None

### DISMISSED
- Count: 0

---

## QA Results
**Verdict:** PASS

| # | AC (short) | Status | Evidence | Fix Applied? |
|---|---|---|---|---|
| 1 | get_provider("codex") returns CodexProvider | PASS | `_init_default_providers()` registers `"codex": CodexProvider` (line 84); `get_provider()` returns `_REGISTRY["codex"]()` (line 100) | N/A |
| 2 | list_providers() includes "codex" | PASS | `list_providers()` returns `frozenset(_REGISTRY.keys())` (line 107); registry contains "claude", "codex", "gemini" after init | N/A |
| 3 | Config with provider: codex validates | PASS | `MasterProviderConfig.provider` (line 36) and `MultiProviderConfig.provider` (line 59) are unconstrained `str` fields -- no Literal, no validator, no allowlist. Runtime validation via `get_provider()` checks `_REGISTRY` which now includes "codex" | N/A |

**Fixes applied:** None
**Gaps remaining:** None
