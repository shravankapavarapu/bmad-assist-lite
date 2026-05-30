# Story 10.2: Provider Registry Integration

**Story ID:** 10-2-provider-registry
**Epic:** Epic-10 (Codex CLI Provider)
**Status:** dev-complete
**Points:** 1
**Priority:** High

## Story

As a developer configuring bmad-assist-lite,
I want to specify `provider: codex` in my config file,
So that Codex CLI is used for multi-LLM validation and code review.

## Description

Register `CodexProvider` in the provider registry alongside Claude and Gemini. Add lazy import, update `_init_default_providers()`, add to `__all__`, and verify config validation accepts `"codex"` as a provider name.

### Current State

`providers/__init__.py` registers two providers: `"claude": ClaudeSDKProvider`, `"gemini": GeminiProvider`. The lazy import dict maps `"ClaudeSDKProvider"` and `"GeminiProvider"` to their modules. The `__all__` list exports both provider classes. The `TYPE_CHECKING` block imports both for static analysis.

### Target State

Registry includes `"codex": CodexProvider`. Config `provider: codex` is accepted in both `master` and `multi` provider configs. All four registration points are updated:
1. `_lazy_imports` dict includes `"CodexProvider": ".codex"`
2. `_init_default_providers()` registers `"codex": CodexProvider`
3. `__all__` includes `"CodexProvider"`
4. `TYPE_CHECKING` block imports `CodexProvider`

### Config Validation Note

Provider names in `config.py` are free-form strings (`provider: str` in `MasterProviderConfig` and `MultiProviderConfig`). There is no hardcoded allowlist -- validation happens at runtime in `get_provider()` which checks the `_REGISTRY` dict. Adding `"codex"` to the registry is sufficient for config acceptance. No changes to `config.py` are needed.

## Acceptance Criteria

1. **Registry lookup** -- Given the provider registry is initialized, when `get_provider("codex")` is called, then a `CodexProvider` instance is returned.

2. **Provider listing** -- Given `list_providers()` is called, when the registry is initialized, then the result includes `"codex"` alongside `"claude"` and `"gemini"`.

3. **Config acceptance** -- Given `bmad-assist-lite.yaml` contains `provider: codex` in the multi config, when config is loaded via `load_config_with_project()`, then validation succeeds (no `ConfigError`).

4. **Lazy import** -- Given `CodexProvider` is accessed via `from bmad_assist_lite.providers import CodexProvider`, when the import resolves, then the `CodexProvider` class from `providers/codex.py` is returned without eagerly loading the module at package import time.

5. **Type checking** -- Given mypy analyzes code that references `CodexProvider` from the providers package, when type checking runs, then `CodexProvider` is recognized as a valid type (via `TYPE_CHECKING` import).

## Technical Notes

- This is a small, mechanical change to `providers/__init__.py` -- four insertion points
- `config.py` does NOT need changes. The `provider` field in `MasterProviderConfig` and `MultiProviderConfig` is an unconstrained `str`. Runtime validation happens in `get_provider()` which checks the registry dict, not a hardcoded list
- The `CodexProvider` class was created in Story 10.1 at `src/bmad_assist_lite/providers/codex.py`
- Follow the exact same pattern used for `ClaudeSDKProvider` and `GeminiProvider` in each of the four registration points
- The module docstring at the top of `__init__.py` should be updated to list CodexProvider in the Provider Registry section

## Tasks / Subtasks

- [x] Task 1: Add `CodexProvider` to lazy imports dict (AC: #4)
  - [x] 1.1: Add `"CodexProvider": ".codex"` entry to the `_lazy_imports` dict, following the existing pattern for `ClaudeSDKProvider` and `GeminiProvider`

- [x] Task 2: Add `"codex"` to `_init_default_providers()` (AC: #1, #2)
  - [x] 2.1: Add `from .codex import CodexProvider` import inside `_init_default_providers()`
  - [x] 2.2: Add `"codex": CodexProvider` to the `_REGISTRY.update()` dict

- [x] Task 3: Add `CodexProvider` to `__all__` (AC: #4)
  - [x] 3.1: Add `"CodexProvider"` to the `__all__` list, maintaining alphabetical order

- [x] Task 4: Add `TYPE_CHECKING` import for `CodexProvider` (AC: #5)
  - [x] 4.1: Add `from .codex import CodexProvider as CodexProvider` inside the `if TYPE_CHECKING:` block, following the existing pattern for `ClaudeSDKProvider` and `GeminiProvider`

- [x] Task 5: Update module docstring (AC: #1, #2)
  - [x] 5.1: Add `CodexProvider: Codex CLI subprocess provider` to the Provider Registry section of the module docstring

- [x] Task 6: Verify config.py accepts "codex" as provider name (AC: #3)
  - [x] 6.1: Confirm that `MasterProviderConfig.provider` and `MultiProviderConfig.provider` are unconstrained `str` fields (no `Literal` type, no validator, no allowlist). This is a verification-only task -- no code change needed
  - [x] 6.2: Confirm that `get_provider()` in `__init__.py` dynamically checks the `_REGISTRY` dict (which now includes "codex") rather than a hardcoded list

## Dev Notes

### Architecture Patterns & Constraints

- **Lazy import pattern**: The `_lazy_imports` dict + `__getattr__` function enables lazy loading of provider modules. The actual class is only imported when first accessed as an attribute of the package. This avoids importing heavy dependencies (like `claude-agent-sdk`) when they're not needed
- **TYPE_CHECKING guard**: The `if TYPE_CHECKING:` import is needed so that mypy and other static analyzers can resolve `CodexProvider` as a type without triggering the lazy import at runtime. The `as CodexProvider` re-export pattern is required for mypy to recognize it as a public export
- **Registry initialization**: `_init_default_providers()` is called lazily on first `get_provider()` or `list_providers()` call. It uses real imports (not lazy) because at that point we need the actual class objects for instantiation
- **No config.py changes**: Provider names are validated dynamically via the registry, not statically via type constraints. This is by design -- it allows plugins to register custom providers without modifying config validation

### File to Modify

- `src/bmad_assist_lite/providers/__init__.py` -- four insertion points plus docstring update

### Files NOT Modified

- `src/bmad_assist_lite/core/config.py` -- no changes needed (provider name is unconstrained `str`)
- `src/bmad_assist_lite/providers/codex.py` -- already created in Story 10.1
- No test file changes -- comprehensive tests are in Story 10.6

### References

- `src/bmad_assist_lite/providers/__init__.py` -- target file for all changes
- `src/bmad_assist_lite/providers/codex.py` -- CodexProvider class (Story 10.1)
- `src/bmad_assist_lite/core/config.py` -- config models (verification only)
- Epic 10: `_bmad-output/planning-artifacts/epic-10.md` -- Story 10.2 specification

## Testing Requirements

Testing is deferred to Story 10.6 (E2E Testing & Hardening). However, the changes can be verified by:
- `get_provider("codex")` returns a `CodexProvider` instance
- `list_providers()` returns a frozenset containing `"codex"`
- `from bmad_assist_lite.providers import CodexProvider` resolves correctly
- `mypy src/bmad_assist_lite/providers/__init__.py` passes

## Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Lint | `ruff check src/bmad_assist_lite/providers/__init__.py` | |
| Typecheck | `mypy src/bmad_assist_lite/providers/__init__.py` | |
| Tests | Deferred to Story 10.6 | N/A |

## File List

- `src/bmad_assist_lite/providers/__init__.py` (modified) -- Added CodexProvider to lazy imports, registry, __all__, TYPE_CHECKING, and module docstring
- `src/bmad_assist_lite/core/config.py` (verified, not modified) -- Confirmed provider field is unconstrained str

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-05-30 | Story created from Epic 10, Story 10.2 | Claude (bmad-create-story) |
