# bmad-assist-lite — Pending Features & Architecture Study

**Date:** 2026-06-19
**Scope:** Full reconciled backlog of pending and shipped features, verified against current code on branch `feature/cursor-cli` (June 2026).
**Status of codebase:** 1760 tests green; Epics 1–10 shipped; Epic 11 (Cursor provider) implemented on branch, pending merge to `main`.

---

## 1. Executive Summary

This report consolidates a fully code-verified backlog of ~90 candidate features, drawn from six prior research/architecture reports, the consolidated performance plan, the Epic 11 planning artifacts, and forensic analysis of the 2026-06-13 production runs. Each item was re-checked against actual source (file:line evidence), and stale claims were corrected.

### State of the backlog

The backlog splits cleanly into three pools:

- **Shipped and verified (~35 items):** The entire Cursor provider epic (11.1–11.6), the Codex provider (10.1–10.7), the whole parallel-execution subsystem (orchestrator, dependency graph, worktree manager, merge queue, state persistence, crash recovery, graceful shutdown, bootstrap/canary, CLI commands, logging/reporting), the SIGTERM→SIGKILL escalation, the `cli_path` lever, FIX-QG context+retries, and the `(optional)` context-requirement convention. These are done; a few carry minor documented caveats (e.g., summary-report time-saved suppression, non-canary bootstrap output surfacing).
- **Researched but not built (~40 items):** The entire session-reuse epic (SessionManager, SessionCapable protocol, replay strategy, native resume wiring, crash-recovery via session IDs, and ~6 future API providers), the `agentic_dev` composite-phase epic, MCP tool integration, JSON-schema tool_use for Evidence Score, PostToolUse hooks, CI/CD pipelines, and most Phase 2/3 vision items.
- **Partially built — performance + robustness levers and related caveats (~18 items, per Appendix C):** OPT-1 through OPT-8, the shared gate runner (Part C), env-vs-real failure classification (Part B), bootstrap-before-post-merge-QG, conflict-resolution and merge-rollback robustness, several forensic bugs surfaced by the 2026-06-13 run, plus Codex hardening/config caveats and a few devex partials.

### Headline findings

1. **Per-story wall-clock is dominated by uniform Opus@max routing.** `MasterProviderConfig` (`core/config.py:31`) carries one model/effort pair for all seven master phases; `BaseHandler.get_model()` (`loop/handlers/base.py:93-95`) returns it unconditionally — no phase override exists anywhere in `src/`. June forensics attribute ~79% of run time to Opus calls. This is the single highest-leverage move (OPT-1) and the implementation is isolated.
2. **The 2026-06-13 epic-11 run was effectively a toolchain-misconfiguration disaster, not a code-quality one.** Quality gates ran `pnpm`/`tsc`/`vitest` against a pure-Python repo (no `package.json`), failing every gate with `ERR_PNPM_NO_IMPORTER_MANIFEST_FOUND`, burning ~5 hours of LLM time, and marking the whole epic blocked. The detect_toolchain code is correct; the failure is workflow design (LLM-inferred commands) plus a missing env-vs-real failure classifier.
3. **Three independent gate-command loops exist** (`quality_gate.py:248`, `merger.py:837`, `epic_quality_gate.py:96`), all sequential, with duplicated command-resolution logic (`quality_gate._get_commands` vs `merger._resolve_qg_commands` at `merger.py:692`) and no failure classification. This satisfies the Rule of Three for extracting a shared gate runner (Part C), which simultaneously delivers OPT-4 parallelism, env-vs-real classification (Part B), and base-repo bootstrap.
4. **Session reuse is thoroughly researched, partially scaffolded, and entirely unwired.** `ProviderResult.provider_session_id` (`base.py:371`) exists and is populated by Gemini (`gemini.py:372`) and Cursor (`cursor.py:521`), but never read by any handler. The Claude SDK provider does not even capture it. The research is sound but deferred — and OPT-1 outranks it on ROI per the performance plan.
5. **Multiple forensic bugs in parallel-merge robustness strand real work or hide causes:** conflict-resolution timeout deletes branches+worktrees (losing passing work); post-merge QG failures leave the base broken with no rollback; a bare `Exit code 1` blocks an entire epic with no captured child stdout/stderr; the base repo is never bootstrapped before post-merge QG.

### The 3–5 highest-leverage moves

| Rank | Move | Why | Effort/Risk |
|---|---|---|---|
| 1 | **OPT-1 per-phase model routing** (Sonnet for create_story/validate_synthesis/retrospective; keep Opus@max for dev/synthesis/fix) | Top wall-clock lever (~10–15 min per 5 stories); isolated to config + one resolution point | small / low |
| 2 | **Shared gate runner (Part C)** delivering OPT-4 parallelism + env-vs-real classification (Part B) + base-repo bootstrap | Fixes the spurious-block disaster *and* parallelizes gates *and* unifies three duplicated loops | medium / low |
| 3 | **Toolchain pre-detection + validation injected into prompts** | Root cause of the 5-hour wasted run; stops LLM-hallucinated `pnpm` commands | medium / medium |
| 4 | **OPT-6 cache toolchain/CLI lookups** + **OPT-8 skip create_story if exists** | Cheap, isolated quick wins (~10–30s + ~6–8 min on resume) | small / low |
| 5 | **Conflict-resolution / post-merge-QG robustness** (preserve branches on failure; rollback or commit-handling decision) | Prevents stranded passing work and broken base branches in parallel runs | medium / medium |

---

## 2. Methodology & Provenance

This report is the output of a multi-agent reconciliation pass. For each candidate feature, an agent was given the feature description, a list of *claims to verify*, and the live codebase, and produced a status verdict with file:line evidence, an effort/risk estimate, dependencies, and a recommendation. The verdicts were then cross-checked against the documented project memory and the performance plan, and **stale claims were explicitly corrected** (see Appendix B). Spot re-verification on the final pass confirmed the load-bearing corrections (OPT-1 single-model routing, OPT-8 dual-naming resolution, merger prompt builder, session-id population) against current source on `feature/cursor-cli`. An independent completeness & correctness critic pass (run after the main synthesis) re-verified all six load-bearing code claims against current source, confirmed roadmap-sequencing consistency, and drove the minor count/coverage corrections folded into this version.

### Sources reconciled

1. **`docs/performance-optimization-plan.md`** (refreshed 2026-06-19) — the consolidated Part A (per-story wall-clock), Part B (post-merge QG robustness), Part C (shared gate runner) plan. Single source of truth for OPT-1..8.
2. **`_bmad-output/reports/session-reuse-architecture.md`** (2026-03-31, "Research complete — FEASIBLE") and **`multi-provider-session-architecture.md`** (2026-04-01, "Research complete") — the session-reuse epic.
3. **`_bmad-output/reports/agentic-worktree-execution-plan.md`** — the `agentic_dev` composite-phase plan.
4. **`_bmad-output/reports/enterprise-architecture-assessment.md`** — MCP integration, hooks, JSON-schema tool_use, CI/CD, prompt ordering, deterministic auto-fix.
5. **`_bmad-output/reports/codex-cli-research.md` + `codex-provider-implementation-plan.md`** (2026-05-29) — Codex provider.
6. **Planning artifacts** (`prd.md`, `architecture.md`, `epics.md`, `epic-11.md`, `requirements-cursor-provider.md`, `requirements-parallel-story-execution.md`) — Cursor + parallel epics.
7. **2026-06-13 run logs** (`run-20260613-145639.log`, `run-20260613-150344.log`, `parallel-20260613-141645.log`, `parallel-20260613-145534.log`), `sprint-status.yaml`, and `epic-11-qa-report.md` — forensic evidence for the toolchain-mismatch incident and the parallel-run failures.

### Confidence and limitations

- **High confidence** on built/not-built status: backed by direct file:line citations and test references.
- **Medium confidence** on effort/savings estimates: derived from the performance plan's forensic timing (e.g., dev_story 11–20 min, code_review_synthesis 9–14 min, Context7 ~6s) plus reasoning, not from controlled benchmarks. Several NFR targets (orchestrator overhead <1%, worktree create <30s) are *asserted but unmeasured* (see item `parallel-performance-baseline-measurements`).
- **Open uncertainty** flagged explicitly in §6: the value of validation phases (OPT-2 needs data), the Gemini `--resume` contradiction between two reports, and several merge-rollback design questions.

---

## 3. Reconciled Pending-Features Backlog

Grouped by category. Status legend: **B** = built, **P** = partial, **NB** = not built, **S** = superseded, plus inline notes for corrected/stale claims.

### 3.1 Performance levers (OPT-1..8 and related)

| ID | Status | Key evidence (file:line) | Effort/Risk | Recommendation |
|---|---|---|---|---|
| per-phase-model-routing (OPT-1) | **NB** | `core/config.py:31-56` MasterProviderConfig single model/effort; `loop/handlers/base.py:93-95` get_model returns master.model unconditionally; zero `phase_override` in `src/` (re-verified) | small / low | **Build first.** Add `PhaseOverrideConfig` + `phase_overrides` on `ProvidersConfig`; resolve in `get_model()`. Route Sonnet to create_story/validate_synthesis/retrospective; keep Opus@max on dev/synthesis/fix. |
| parallel-quality-gate-commands (OPT-4) | **NB** | `quality_gate.py:248`, `epic_quality_gate.py:96`, `merger.py:837` all sequential; `code_review.py:136` shows reusable ThreadPoolExecutor; `command_runner.py:57` per-command timeout | medium / low | Build inside the shared gate runner (Part C), not as a one-off. ~3–4 min/run total. |
| opt2-drop-validation-phases | **P** (corrected) | `core/config.py:199-210` loop.story is mutable; `validate_story_synthesis.py:150-152` writes `synthesis-diff-validate-*.patch`; ~6–8 debug-logging commits on `debug/opt-2-validation-impact` not on main | small / medium | **Gather data first.** Cherry-pick diff-logging, run a few stories, inspect patch sizes; cut only if consistently trivial. *Corrected: phases are fully built/enabled, not "not built"; multi config is now Gemini 3.1 Pro + Sonnet, not Codex+Opus.* |
| opt5-context7-doc-slimming | **P** | `config.py:160-169` exposes max_libs/max_tokens; `resolver.py:323-356` does story-specific filtering for table-source epics; auto-detect path uses generic query (`resolver.py:116`) | medium / low | **Reframe as token-cost, not speed** (Context7 ~6s, one-time). Table-source query refinement shipped; auto-detect refinement low priority. |
| opt6-cache-toolchain-detection | **NB** | No `lru_cache` anywhere; `toolchain.py:104-117` plain fn; `resolve_cli_path()` `base.py:130-190` no memoization | small / low | **Quick win.** `@lru_cache` on `detect_toolchain` and `resolve_cli_path`; both pure. ~10–30s/run. Note: invalidate/skip cache in parallel worktrees where cwd differs per story. |
| opt7-skip-build-in-per-story-qg | **NB / reframe** | Build comes from story-file Quality Gates table (`create-story/template.md:41`), not config (`bmad-assist-lite.yaml` has no build); epic QG runs build (`epic_quality_gate.py:88`) | small / low | **Defer/reframe.** Not config-only as claimed; real issue is build running 3–4x/story across dev/synthesis/QG/epic. Address via Part C + removing in-turn verification. |
| opt8-skip-create-story-if-exists | **NB** | `create_story.py:15-82` no execute() override; `base.py:125-151` unconditional; `quality_gate._resolve_story_path()` (`quality_gate.py:137-160`) already resolves both `story-{e}.{s}.md` and `{e}-{s}-*.md` glob (re-verified) | small / low | **Build.** Guard `CreateStoryHandler.execute()` on file-exists + sprint-status≥ready-for-dev. *Corrected: the naming-mismatch claim is FALSE — quality_gate already resolves both patterns.* |
| max-turns-cap-dev-story | **NB** | `claude_sdk.py:120-132` extra_args carries only `effort`; no `max_turns`; `bmad-assist-lite.yaml:41-43` dev_story timeout 1200 | small / low | **Fallback lever.** Verify cli_path mitigation prevents truncation first; implement max_turns only if runaway recurs. |
| position-aware-prompt-ordering | **NB** | `compiler/output.py:83-90` FILE_ORDER_PATTERNS doesn't prioritize story file (sorts last); instructions already last | medium / medium | Build to counter lost-in-the-middle: AC/task-list first, architecture middle, instructions last. Measure evidence-score variance before/after. |
| section-level-epic-extraction-all-phases | **P** | `context_filter.py:260-327` filter_epic_to_story works; called only in create_story/validate_story/code_review (not dev_story, synthesis, fix_qg, retrospective) | medium / low | **Add `filter_epic_to_story()` to dev_story** as immediate win; defer synthesis/non-story phases (may not benefit). |
| structured-fact-extraction-sprint-status | **NB / stale premise** | Synthesis workflows do NOT load sprint-status today (`validate-story-synthesis/workflow.yaml`, `code-review-synthesis/workflow.yaml` no sprint pattern) | small / low | **Clarify need first.** Premise is false — sprint-status isn't passed to synthesis. Build only if dependency/blocked context is actually wanted. |
| toolchain-detection-python-vs-js | **P** | `epic-11-qa-report.md` shows pnpm against no-`package.json`; `toolchain.py:104-117` logic correct; workflows ask LLM to "determine commands"; `quality_gate.py:164-170` prioritizes story-file table over auto-detect | medium / medium | **Root cause of the 5-hour wasted run.** Pre-compute toolchain at epic load, inject as explicit context, validate story Quality Gates against detected commands. |
| run-code-review-parallel-with-qg | **NB** | `runner.py:145-300` strictly sequential phases; within-phase parallelism only for validators | large / high | **Defer.** Cross-phase overlap is high-complexity for ~1–2 min; OPT-4 is higher ROI and isolated. |
| shared-cache-symlink-worktree-coldstart | **NB** | `bootstrap.py:41-160` full copy per worktree, no symlink/cache | large / medium | Phase 2. Defer behind OPT-1/OPT-4/Part B. |
| context7-serial-fetch-latency | **P** | `resolver.py:286-306` serial per-library fetch; library IDs not cached (only final docs) | medium / low | **Deprioritize.** ~6s cold-start; table path sidesteps search. Async only if 15+ libs without table. |

### 3.2 Parallel / merge robustness

The **parallel subsystem core is fully built and tested** (orchestrator, dependency graph with Kahn's + scheduling scores, worktree manager, git_ops wrapper, merger agent with Claude-CLI conflict resolution, state persistence, sprint-status manager, config model, CLI run/status/unblock, branch guard, output multiplexing, graceful shutdown/drain, crash recovery/resume, blocked-story cascade, orchestrator log, enhanced status, summary report, epic teardown, integration flags, bootstrap/canary). Robustness *gaps and forensic bugs* remain:

| ID | Status | Key evidence (file:line) | Effort/Risk | Recommendation |
|---|---|---|---|---|
| classify-env-vs-real-gate-failures | **NB** | `logging.py:303` "failed gates: unknown"; `quality_gate.py:274-280` routes ANY fail to fix_qg; `base.py:302-316` ExitStatus enum exists but unused by gates; `command_runner.py:107-114` detects 127 but caller ignores | medium / low | **Build inside Part C.** `GateClassification` enum (pass/real_failure/env_failure); skip fix_qg for env failures; name the actual cause. Cuts wasted Opus fix calls 50–80%. |
| bootstrap-base-repo-canary-before-postmerge-qg | **P** | Canary bootstrap built (`orchestrator.py:1417-1554`); `merger.py:794-873` runs gates on base branch with ZERO prior bootstrap; sequential quality_gate also no bootstrap | medium / low | **Run setup_commands in base repo before post-merge QG.** Root cause of ~1s "command not found" spurious blocks. *Corrected: canary DOES validate; base repo does not.* |
| conflict-auto-resolution-robustness | **P** | 120s timeout (`config.py:36`); on failure aborts + `git branch -D` + rmtree (`worktree_manager.py:186,199`) loses passing work; no retry/fallback | medium / medium | **Preserve branch/worktree on resolution failure.** Add retry/larger budget or manual-resolve queue. Decide: park story vs block run. |
| post-merge-qg-rollback-commit-handling | **P** | `merger.py:1015-1020` checks `git status --porcelain` before commit (race window remains); no rollback on QG fail; only `all_passed: bool`, no cause distinction | medium / medium | **Decide rollback policy** + add result enum (no_changes/fix_failed/commit_failed). *Corrected: the "nothing to commit" claim is mitigated by a pre-check, but a race remains.* |
| opaque-exit-code-no-child-output | **P** | `orchestrator.py:789` blocks story with only "Exit code N"; OutputMultiplexer (`output.py:41-78`) has no buffering; canary surfaces output (1491-1504), non-canary does not (582-618) | small / low | **Build.** Add last-N-line buffer to OutputMultiplexer; store tail in `StoryState.error`. Stop fast crashes cascading to whole-epic block. |
| sprint-status-conflates-code-vs-env-failure | **P** | `runner.py:213-223` auto-commits + adds to failed_qa regardless of failure type; `sprint_sync.py:60-61` all map to "blocked"; no env-specific status | medium / medium | **Add classifier + `blocked_env` status.** Communicate recoverable-via-config-fix vs real defect. Depends on the env/real classifier. |
| deterministic-autofix-before-escalation | **NB** | `quality_gate.py:236-283` routes to fix_qg with no local recovery; no `ruff format` or test re-run | medium / low | Build inside Part C: `ruff format` for lint, re-run flaky test once, escalate only on persistent failure. |
| structured-error-responses-to-llm | **NB** | `quality_gate.py:212-234` plain markdown report; `_deduplicate_test_output()` (30-126) groups by signature (foundation exists); ExitStatus + ProviderExitCodeError exist | medium / low | Add errorCategory (transient/validation/permission) + isRetryable to failure report; route fix_qg by category. Related to but distinct from env/real classifier. |
| postmerge-vs-epic-qg-overlap | **B** (tracking item) | `quality_gate.py:184` `(test_unit or test)`; `merger.py:729-732` prefers full suite; `epic_quality_gate.py:31` full suite | medium / medium | Both intentional (per-story integration + epic defense-in-depth). **Track overlap** as design debt for Part C; don't remove yet. |
| merger-prompt-template-gap | **B** (corrected) | `merger.py:161-199` `_build_resolution_prompt()` includes story context, conflict files, markers, delimiter instructions; called at `merger.py:330`; tested `test_merger_conflict_resolution.py:127-189` (re-verified) | small / low | **Already done.** The architecture flagged this as a gap; it was implemented as a function. |
| worktree-session-isolation | **B** | `orchestrator.py:660,669` cwd=worktree; `session-reuse-architecture.md:203` per-cwd sessions | small / low | **Already works** — leverage for future session reuse, no change needed. |

### 3.3 Session caching / reuse (entire epic NOT built; infrastructure half-scaffolded)

The session-reuse research is comprehensive and feasible, but **no SessionManager, no SessionCapable protocol, no `session_reuse` config, and no handler wiring exist.** `ProviderResult.provider_session_id` (`base.py:371`) is populated by Gemini (`gemini.py:372`) and Cursor (`cursor.py:521`) but never read. The Claude SDK provider does not capture it (re-verified: `provider_session_id` appears in `base.py`, `cursor.py`, `gemini.py` only — not `claude_sdk.py`).

| ID | Status | Effort/Risk | Recommendation |
|---|---|---|---|
| persistent-claude-session-across-master-phases | **NB** | medium / low | Defer. Top-level epic (~6–8 min/story, ~4.7x token reduction). Build after OPT-1/Part C. Option A (SDK resume) recommended. |
| claude-sdk-session-id-support | **P** (corrected) | small / low | **Foundational; build when epic starts.** Gemini/Cursor already model the pattern; only Claude SDK lacks capture. SDK v0.1.34+ supports `resume` + `ResultMessage.session_id`. *Corrected: status "partial" not "not built" — field + 2/4 providers done.* |
| session-manager-layer | **NB** | large / medium | Defer until after gate-runner clarifies orchestration patterns. |
| session-capable-protocol | **NB** | small / low | A simpler path (session_id param + handler tracking) may beat the proposed protocol. |
| wire-session-manager-into-handler-layer | **NB** | medium / medium | Critical missing wiring; the whole epic is inert without it. |
| session-aware-handler-subclass | **NB** | medium / medium | Defer pending performance data. |
| replay-session-strategy-summarization | **NB** | medium / medium | Phase 3, needs Ollama. Lower ROI than native resume (2.8x vs 4.7x). |
| gemini-resume-support | **NB** ⚠️ **CONTRADICTION** | small / **high** | **Resolve report conflict first.** session-reuse doc says Gemini has NO resume; multi-provider doc (1 day later) says it ALREADY supports `--resume`. Run `gemini --help \| grep resume` before building. |
| session-reuse-config-block | **NB** | small / low | Cheap feature gate; de-risks the epic. Defaults OFF. |
| crash-recovery-via-persisted-session-ids | **NB** | medium / low | Benefits fix_qg most. Needs `session_id` in `state.yaml`. |
| session-resume-failure-fallback | **NB** | large / medium | Robustness layer; build with SessionManager. |
| context-window-compaction-mitigation | **NB / superseded-pending** | large / medium | Moot until sessions persist. CLAUDE.md compaction-survival is live but unused. |
| provider-switchover-mid-story-handling | **NB** | medium / medium | Only meaningful once sessions persist; no fallback chain exists today. |
| multi-llm-no-session-reuse-constraint | **B** | small / low | **Already enforced** by parallel-spawn pattern (`validate_story.py:137-146`, `code_review.py:142-150`). |
| sdk-session-management-utilities | **NB** | small / low | `list_sessions`/`get_session_messages` exist in SDK; integrate with SessionManager later. |
| session-id-via-extra-args | **P** | medium / low | `extra_args` proven for `effort`; `--session-id` workaround documented but never attempted. |
| session-continuity-resume-fixqg | **NB** | medium / medium | Targeted dev_story→fix_qg chain; subset of the broader epic. |
| option-b-cli-resume-mechanism | **NB** | medium / high | Drop; Option A (SDK resume) is lower-effort. |
| reject-anthropic-api-direct | **NB (decision)** | small / low | Documented rejected alternative (loses Claude Code tools). No action. |
| industry-server-side-session-convergence | **NB (guidance)** | large / medium | Design guidance for NativeSessionStrategy; not an item to build. |
| drop-prompt-precompilation | **S** | large / medium | Superseded by session reuse. Document the decision; do not build pre-compilation. |

### 3.4 Provider integration

**Codex provider (10.1–10.7): fully built and tested.** **Cursor provider (11.1–11.6): fully built and tested on `feature/cursor-cli`.**

| ID | Status | Notes |
|---|---|---|
| codex-provider-replace-gemini-reviewer | **B** | `codex.py` 592 lines, 1437 test lines; config lists Codex as primary multi. Replacement is config-driven; Gemini coexists. |
| codex-provider-core-do-invoke-cleanup | **B** | stdin-based prompt (avoids Windows 32K limit), NDJSON parsing, 66 tests. |
| codex-provider-registration | **B** | Lazy import + registry + config acceptance verified at runtime. |
| codex-structured-output-schema | **B** | `--output-schema`/`--output-last-message`, graceful fallback, 38 tests. |
| codex-evidence-score-integration | **B** | P0→CRITICAL(+3)/P1→IMPORTANT(+1)/P2,P3→MINOR(+0.3); Option B (parse_output→evidence text), zero handler changes. |
| codex-config-and-docs | **P** ⚠️ | **Auth discrepancy:** README uses `CODEX_API_KEY`, plan uses `OPENAI_API_KEY`; CLAUDE.md `codex login --with-api-key` vs plan `codex auth login --api-key`. Codex not in init defaults. Resolve docs. |
| codex-e2e-testing-hardening | **P** ⚠️ | **Gaps:** no rate-limit retry (Gemini has it), no `OPENAI_API_KEY` validation, structured-JSON-on-timeout gap (file ready but unreachable; partial-text recovery ineffective since Codex `text_len=0`). |
| codex-known-bugs-workarounds | **P** ⚠️ | stdin=PIPE (32K fix) used instead of stdin=DEVNULL (#20919 hang) — different bug solved; MCP-off ✓, output-last-message authoritative ✓, defensive parsing ✓, no version pin. |
| codex-model-support-and-default | **B** ⚠️ | default `gpt-5.3-codex`; README/PKG-INFO say `codex-mini-latest` (doc drift). |
| codex-reviewer-windows-tool-flailing | **P** | Codex spams PowerShell variants (`Get-Content`/`findstr`/`Select-String`) on Windows (152–265s/review) because diff isn't embedded and tool restriction is prompt-only. **Embed git diff in prompt.** |
| cursor-provider-core | **B** | 580 lines, 70 tests; NDJSON dispatch, write-mode predicate, deny-config lifecycle. |
| cursor-cost-guard | **B** | Parses init model, WARNs on mismatch, records actual model — guards 6x composer-2.5-fast cost. |
| cursor-readonly-deny-config-lifecycle | **B** | Atomic create-if-absent + marker ownership; never touches user `.cursor/cli.json`. |
| cursor-deny-config-crash-recovery-sweep | **B** | `cleanup.py:19-76` marker-based sweep, no-op when absent. |
| cursor-cli-binary-resolution | **B** | `_PROVIDER_BINARY_NAMES` tries `cursor-agent` before `agent`; 32 tests. |
| config-accepts-cursor-provider | **B** | `provider: str` open; rejection deferred to `get_provider()`. |
| cursor-version-logging | **B** | Lazy once-per-process cache, INFO log. |
| cursor-tool-event-activity-tracking | **B** | `cursor.py:401-407` `collector.add('')` on tool events prevents false grace denial. |
| sigterm-sigkill-escalation | **B** | `_windows.py:67-91` SIGTERM→poll→SIGKILL, `SIGTERM_GRACE_SECONDS=5`, 21 tests; Windows taskkill untouched. |
| cursor-spike-s5-list-models | **B** (deploy gate) | Documented in linux-deployment.md; must RUN on Linux box. |
| cursor-spike-s1-stdin-prompt | **NB** (spike) | argv-only with stdin=DEVNULL today; spike to test stdin semantics for >32K. |
| cursor-spike-s2-mode-ask | **NB** (spike) | deny-config used today; `--mode=ask` could simplify. |
| cursor-spike-s3-context-window | **NB** (spike) | No per-provider context budget (uniform 20K hard limit `output.py:20`); probe 256K vs 1M. |
| cursor-spike-s4-and-version-pinning | **P** | Version logging built; tarball pinning deferred pending S4. |
| cursor-stream-partial-output | **NB** | Complete-message streaming sufficient; defer. |
| claude-sdk-cli-path-not-applied | **B** (corrected) | `claude_sdk.py:92-132` wires `resolve_cli_path('claude')`→`cli_path`; falls back to bundled when unconfigured. *Corrected: built and validated; the run used bundled only because config was unset.* |
| claude-sdk-no-pid-tracking-orphan-risk | **NB / stale** | `claude_sdk.py:257` never assigns PID — **SDK API limitation, not a bug.** Downgraded to DEBUG (commit eaa2f88). Fallback: subprocess+NDJSON rewrite. |
| context7-em-dash-placeholder-fetch | **NB** | `epic_table.py:151` truthiness check passes em-dash `—`; forwarded as libraryId → HTTP 400. **Skip placeholder rows.** |
| mcp-tool-integration-dev-phases | **NB** | Zero MCP anywhere; `allowed_tools` never set for dev_story. Highest-impact gap per enterprise assessment; large effort, needs SDK MCP support. |
| evidence-score-json-schema-tool-use | **NB** | `evidence_score.py:220-255` fragile regex; needs Anthropic SDK direct API or SDK tool_choice. |
| openai-api-provider-future / gemini-interactions-api-provider-future / ollama-provider-stateless / mistral-api-provider / glm5-direct-api-provider / opencode-provider | **NB** | Phase 4 API providers; defer behind session-reuse. (opencode notable for full native session support per the multi-provider doc — revisit it first when the session-reuse epic starts.) |
| glm5-via-claude-code-cli | **NB** (corrected) | Config-only in theory, but `ClaudeSDKProvider` doesn't plumb `ANTHROPIC_*` env vars to `ClaudeAgentOptions.env` (SDK supports it). *Corrected: "trivial/no code" claim is inaccurate.* Medium effort + untested CLI env passthrough. |
| enterprise-rejected-patterns | **NB (decision)** | Batch Processing API, Fork Session, human-review confidence-calibration, and MCP content-catalog Resources explicitly NOT adopted per the enterprise assessment. No action. |

### 3.5 Architecture / composite-phase (agentic_dev) — NOT built

| ID | Status | Notes |
|---|---|---|
| agentic-dev-unified-handler | **NB** | Collapses dev/review/synthesis/QG/fix into one. Partially **superseded by session reuse** (which solves cross-phase context loss more elegantly). Major crossroads. |
| agentic-dev-phase-enum-and-dispatch | **NB** | Plumbing (enum, dispatch, exports, timeout). Runner/transitions already handle arbitrary phase names. |
| agentic-dev-workflow-templates-and-compiler | **NB** | C1/C2 dual-prompt compiler; compiler loader infra exists. |
| agentic-dev-internal-qg-retry-and-recovery | **NB** | Internal QG loop + sub-phase checkpoints. |
| implementation-summary-protocol | **NB** | impl-summary file across session boundary; **unnecessary under native session reuse.** |
| agentic-dev-tests-and-verification | **NB** | Tests + timing comparison. |
| zone1-story-spec-unchanged | **B** (decision) | create_story/validate/synthesis kept separate by design. |
| native-agentic-loop-dev-and-fixqg | **NB** | Migrate to Claude `-p` tool-use loops. XL/high; defer pending use case. |

### 3.6 Multi-provider review enhancements

| ID | Status | Notes |
|---|---|---|
| synthesis-iterative-refinement-loop | **NB** | Gap-detection + targeted re-review. PhaseResult.next_phase + Evidence Score consensus_ratio provide the hooks. |
| split-pass-review-large-stories | **NB** | Per-file + integration passes for lost-in-the-middle. **Defer pending data** (consensus ratio on large stories). |

### 3.7 DevEx

| ID | Status | Notes |
|---|---|---|
| batch-create-stories-command | **NB** | Follow-on to OPT-8; may be redundant if `run --epic N` auto-skips. |
| claude-rules-path-specific | **NB** | Split monolithic CLAUDE.md into `.claude/rules/`. Pure org change; verify Claude Code supports path-scoped rules. |
| skills-context-fork | **NB** | Add `context: fork` to 5–8 high-output BMAD skills (45 skills, zero use it). |
| cicd-pr-review-pipeline | **NB** | No `.github/workflows/`. Large; understand merge-queue first. |
| linux-ci-runner | **NB** | No CI at all; manual validation gate. Tests already cross-platform via mocking. |
| linux-deployment-docs | **B** | `docs/linux-deployment.md` 419 lines, S1–S5 + gates. |
| forensic-artifacts-retention-vs-cleanup | **B** ⚠️ **conflict** | `cleanup.py:131-148` deletes synthesis-diff/qa-failures on transition; **conflicts with the documented forensic-analysis plan.** Add to `_KEEP_FILENAMES` or a `forensics/` dir. Risk: high (destroys analysis inputs). |
| create-story-missing-doc-preflight | **B** | `context_filter.py:462-539` raises CompilerError on missing non-optional docs; `(optional)` downgrades to warning. Already as desired. |
| config-hygiene-posix-commands | **P** | Defaults POSIX-portable; no validation for user-added Windows syntax. Doc enhancement. |
| windows-only-test-skip-markers | **P** (superseded) | Commit 179ec6b fixed root cause (`_SIGKILL` fallback + path normalization) rather than adding skip markers — superior. |
| worktree-integration-test-fixtures | **P** | Real-git tests exist for auto-commit (`test_git.py`); worktree ops fully mocked. **Drop from v1** — git is mature. |
| parallel-performance-baseline-measurements | **P** | NFR9/10 tested (DAG <1s, status <2s); NFR6/7/8 (overhead, create, cleanup) asserted but untested. Add 3 timing tests. |
| web-dashboard-parallel / automatic-unblock-detection / multi-epic-parallel / distributed-execution / smart-concurrency-tuning / merge-conflict-learning | **NB** | Phase 2/3 vision items; defer. |
| fix-quality-gate-context-and-retries (FIX-QG) | **B** | `fix-quality-gate/workflow.yaml` FULL_LOAD story; `max_retries=2` (`config.py:183`); retry context (`fix_quality_gate.py:50-70`). Shipped commit e17542c. |

---

## 4. Architecture Suggestions (cross-cutting, trade-offs)

These are structural moves where the *how* matters more than the *whether*. Presented as trade-offs.

### 4.1 Per-phase model routing (OPT-1) — the cheap structural win

**Move:** Add `PhaseOverrideConfig` + `phase_overrides` dict on `ProvidersConfig`; resolve the per-phase model at the single point in `BaseHandler.get_model()` (fall back to master).

- **Trade-off:** Pure win on cost/latency; the only risk is quality regression on phases routed to Sonnet. Mitigate by routing only create_story / validate_synthesis / retrospective (low-judgment phases) and keeping Opus@max on dev/synthesis/fix.
- **Why isolated:** No handler inheritance, workflow, or provider changes. The 2-tier YAML merge already handles nesting. `get_model()` needs the current phase name in scope — `BaseHandler` already carries `State`, so thread the phase through from the handler's phase identity (no signature change to provider invocation).

### 4.2 Shared gate runner (Part C) — the unifying refactor

**Move:** Extract `core/gate_runner.py` consumed by all three gate sites (`quality_gate.py:248`, `merger.py:837`, `epic_quality_gate.py:96`). It (a) runs commands concurrently (OPT-4), (b) returns per-command results classified `pass | real_failure | env_failure` (Part B), and (c) optionally bootstraps the working tree first (base-repo bootstrap).

- **Rule of Three is satisfied:** three duplicated sequential loops + two duplicated command-resolution paths (`quality_gate._get_commands` vs `merger._resolve_qg_commands` at `merger.py:692`).
- **Trade-offs:**
  - *Concurrency:* reuse `code_review.py`'s ThreadPoolExecutor + `asyncio.gather`; but concurrent progress reporting needs buffering to avoid interleaved console output. Bound the executor to a small pool (gates are I/O-light but CPU-heavy for build/test) — over-parallelizing test + build can thrash a single machine.
  - *Classification heuristics:* exit 127 / "command not found" / "node_modules missing" → env_failure; exit 124 (timeout) → real_failure; other non-zero → real_failure. Risk of mis-classifying genuine "binary missing because the code deleted it" — acceptable, surfaces as setup error to operator.
  - *Bootstrap coupling:* reuses `parallel/bootstrap.py` utilities but the base repo path needs identical treatment to worktrees.
- **Payoff:** simultaneously kills the spurious-block disaster, parallelizes gates, and unblocks deterministic auto-fix and structured-error-response features that all sit on this path.

### 4.3 Toolchain pre-detection vs LLM inference

**Move:** Pre-compute `detect_toolchain()` at epic load, inject commands as explicit YAML/JSON context into create_story/dev_story prompts, and validate story-file Quality Gates against detected commands.

- **Trade-off:** The current design trusts the LLM to infer commands from project artifacts (flexible, but the proven failure mode — `pnpm` against Python). Programmatic injection is more rigid but deterministic. The detect code is *already correct and tested*; the fix is wiring, not logic. Pairs naturally with the env/real classifier (both parse the same toolchain signals). Combining this with OPT-6 (`@lru_cache` on `detect_toolchain`) means the epic-load pre-detection and the per-story gate runs share one cached result.

### 4.4 Session reuse — the deferred big lever

**Move:** Native SDK `resume` for the master LLM's sequential phases; multi-LLM phases stay fresh sessions (already enforced).

- **Trade-offs:**
  - *Two-tier abstraction (SessionManager + SessionCapable protocol)* as researched is elegant but heavy (large effort, 5–8 pts). A *simpler path* — `session_id` param on `_do_invoke()` + tracking in `BaseHandler` — captures most of the value with less ceremony.
  - *agentic_dev partially superseded:* session continuity solves the dev→fix_qg context loss that agentic_dev's impl-summary protocol was designed to work around. Building session reuse first makes the agentic_dev epic largely unnecessary — a genuine fork in the roadmap.
  - *Crash recovery interaction:* persisting session_id in `state.yaml` lets resume pick up with memory; but stale/expired sessions need the resume-failure fallback (graceful degrade to fresh session).
- **Ranking:** Per the performance plan, OPT-1 outranks this on ROI per effort. Build session reuse only after OPT-1 + Part C, and only if timing data justifies the ~6–8 min/story claim.

### 4.5 Conflict-resolution and merge-rollback robustness

**Move:** (a) Do **not** delete branch/worktree on conflict-resolution failure (preserve passing work); (b) decide whether post-merge QG failure rolls back the merge or leaves the base broken; (c) add a result enum distinguishing no_changes / fix_failed / commit_failed.

- **Trade-offs:** Rolling back keeps the base green but discards merged work (re-merge later); leaving it leaves the base red but preserves the integration point. Parking a story vs blocking the run is a UX choice — parking continues progress but risks an inconsistent base. These are genuine open design questions (§6), not foregone conclusions.

### 4.6 Observability surface (forensic artifact retention + child output)

**Move:** Buffer child stdout/stderr tails in `OutputMultiplexer` and store in `StoryState.error`; preserve `synthesis-diff-*.patch` / `qa-failures-*.md` across transitions.

- **Trade-off:** Cache growth vs diagnosability. The current cleanup is *working as designed* but conflicts with the documented forensic-analysis plan — a low-effort, high-value alignment. Bound the retention (e.g., last N runs, or a size cap on `forensics/`) so the diagnosability win doesn't become unbounded cache growth.

---

## 5. Prioritized Roadmap

Sequenced into waves. Rationale and dependencies inline. "Data-gated" items are called out.

### Wave 0 — Cheap isolated wins (days)

| Item | Effort | Rationale |
|---|---|---|
| **OPT-1 per-phase model routing** | small | Top wall-clock lever; isolated; unblocks Sonnet routing. **Do this first.** |
| **OPT-6 cache toolchain/CLI lookups** | small | Pure-function `@lru_cache`; ~10–30s/run; zero risk. Becomes a dependency of Wave 1 toolchain pre-detection. |
| **OPT-8 skip create_story if exists** | small | ~6–8 min on resume; naming already handled. |
| **opaque-exit-code child-output capture** | small | Stops fast-crash → whole-epic-block; improves diagnosability. |
| **forensic-artifact retention** | small | Aligns cleanup with the analysis plan (currently destroys inputs). |
| **context7 em-dash row skip** | small | Trivial parse fix; stops spurious HTTP 400. |
| **Resolve Codex doc/auth drift + Gemini --resume contradiction** | small | Documentation/verification; unblocks downstream items (the Gemini check gates `gemini-resume-support` in Wave 3). |

### Wave 1 — The unifying refactor (1–2 weeks)

| Item | Effort | Rationale / Dependencies |
|---|---|---|
| **Shared gate runner (Part C)** delivering **OPT-4 parallelism** + **env-vs-real classification (Part B)** + **base-repo bootstrap** | medium | Rule of Three met. Fixes the 5-hour spurious-block disaster, parallelizes gates, and is the foundation for deterministic auto-fix + structured errors + `blocked_env` status. **Highest structural payoff.** |
| **Toolchain pre-detection + prompt injection + Quality-Gate validation** | medium | Root cause of the wasted run; pairs with the classifier (same signal parsing). Depends on Wave 0 OPT-6 cache to avoid re-detecting per story. |
| **section-level epic extraction in dev_story** | medium | Quick token win once classifier work is in flight. |

### Wave 2 — Robustness in parallel merge (1–2 weeks)

| Item | Effort | Rationale |
|---|---|---|
| **conflict-auto-resolution robustness** (preserve branch on failure) | medium | Stops stranding passing work. **Needs design decision** (park vs block). |
| **post-merge-QG rollback/commit handling** | medium | Stops broken base branches. **Needs design decision** (rollback policy). |
| **sprint-status blocked_env status** | medium | Communicates recoverable vs real; depends on Wave 1 Part B classifier. |
| **deterministic auto-fix** (`ruff format`, test re-run) | medium | Build on the Wave 1 gate runner; cuts wasted Opus fix calls. |
| **Codex hardening** (rate-limit retry, API-key validation, structured-JSON-on-timeout) | medium | Closes the verified Codex gaps. |
| **Codex Windows tool-flailing** (embed git diff in review prompt) | medium | 152–265s→fast reviews; depends on compiler diff injection. |

### Wave 3 — Bigger levers, data-gated (multi-week)

| Item | Effort | Gate |
|---|---|---|
| **OPT-2 drop validation phases** | small (config) | **Data-gated:** cherry-pick diff logging, run stories, inspect patch sizes. |
| **Remove redundant in-turn verification from code_review_synthesis** | medium | **Data-gated:** measure post-merge QG failure-rate shift first. |
| **Session reuse epic** (Wave 3a: Claude SDK session_id capture → config block → handler wiring → crash-recovery persistence → resume-failure fallback) | medium→large | Build only if OPT-1 hasn't already met latency goals. Resolve Gemini --resume contradiction (Wave 0) first. Largely supersedes agentic_dev. |
| **Position-aware prompt ordering** | medium | Measure evidence-score variance before/after. |
| **Synthesis iterative refinement loop** | medium | Define gap thresholds operationally first. |

### Wave 4 — Strategic / vision (deferred)

MCP tool integration, JSON-schema tool_use for Evidence Score, PostToolUse hooks, CI/CD pipelines, `.claude/rules/` split, skills `context: fork`, split-pass review, and all Phase 2/3 vision items (web dashboard, auto-unblock, multi-epic, distributed execution, smart concurrency, merge-conflict learning, shared cache/symlink). Most are large/high or need a validated use case.

### What must come first and why

**OPT-1 first** because it is the single biggest wall-clock lever, is fully isolated (config + one resolution point), carries low risk, and unblocks nothing else (so it can ship in parallel with Wave 0). **The shared gate runner (Wave 1) second** because it is the keystone: it converts a one-off OPT-4 into a reusable abstraction that simultaneously resolves the most damaging production failure (spurious env blocks), the toolchain mismatch's downstream effects, and the prerequisites for half a dozen robustness items. **Wave 2 robustness depends on Wave 1's classifier** (`blocked_env`, deterministic auto-fix both consume the gate runner's classification), so it must follow. **Session reuse (Wave 3) is gated on OPT-1's measured impact** — if OPT-1 already meets latency goals, the large session-reuse epic may not earn its cost.

---

## 6. Risks & Open Questions

### Unresolved design questions (must decide before building)

1. **Conflict-resolution failure: park or block?** On Claude-CLI conflict-resolution timeout, should the failed story be parked (run continues, base may be inconsistent) or block the whole run? Currently it blocks-and-deletes (losing work). From the epic-8 "fable" run.
2. **Post-merge QG failure: roll back or leave broken?** Rolling back keeps the base green but discards merged work; leaving it leaves red tests on the base. Plus: where should post-merge fixes be committed, and how verified before leaving the merge in place?
3. **Do validation phases earn their time (OPT-2)?** Evidence scores were consistently low/PASS with tiny synthesis diffs; need the diff-logging data (on `debug/opt-2-validation-impact`) before cutting ~10–20 min/story of quality signal.
4. **Does Gemini CLI support `--resume`?** The two session-reuse reports directly contradict each other (one day apart). Effort and feasibility of gemini-resume-support hinge entirely on this — verify against the CLI before any work.
5. **Build session reuse or agentic_dev?** They largely overlap on solving cross-phase context loss. Session reuse is more elegant and supersedes the impl-summary protocol; agentic_dev reduces invocation count. Pick one path.
6. **Should post-merge QG and epic QG coexist long-term?** Both run full suites (defense-in-depth vs redundant cost). Track overlap data before removing either.

### Execution risks

- **Stale-doc drift is recurring:** Codex auth command (CLAUDE.md vs plan), Codex default model (code vs README), the architecture.md "Two LLM providers" line (intentionally stale with drift note). Keep planning artifacts and code reconciled.
- **NFR targets are unmeasured:** orchestrator overhead <1%, worktree create <30s, cleanup <10s are asserted, not benchmarked. Add the three timing tests before claiming compliance.
- **`cli_path` truncation cure is unproven:** The 147-char truncation quirk is intermittent and didn't fire in either validation run, so the cli_path lever is "no regression" not "proven cure." `max_turns` remains the documented fallback.
- **Claude SDK PID orphan risk is a real SDK limitation, not fixable in-tree** without a subprocess+NDJSON provider rewrite — mitigated by the grace-period timeout + `finally`-block cleanup, but a hard kill mid-phase can orphan `claude.exe`.
- **OPT-6 cache correctness under parallel cwd switching:** `@lru_cache` on `detect_toolchain`/`resolve_cli_path` is safe only if the cache key includes the working directory; parallel worktrees run distinct cwds, so a naive no-arg cache could return the wrong toolchain. Key on the resolved path.
- **Spike S5 gates the whole Cursor premise** and can only be run on the Linux box (`agent --list-models` confirming composer-2.5 on the Pro key).

### Honest uncertainty

Savings figures (OPT-1 ~10–15 min/5 stories; session reuse ~6–8 min/story; gates ~3–4 min) come from forensic timing + reasoning, not controlled A/B benchmarks. Treat them as directional. The forensic run was on a misconfigured (Python-vs-JS) toolchain, so some timing is contaminated by environmental failures — another reason Wave 1's classifier is needed before trusting future timing data.

---

## 7. Appendix

### Appendix A — Per-source provenance

| Source | Date / status | Items primarily derived | Reliability note |
|---|---|---|---|
| `docs/performance-optimization-plan.md` | 2026-06-19, refreshed | OPT-1..8, Part B (post-merge QG robustness), Part C (shared gate runner), conflict/rollback design questions | Single source of truth for perf; reconciled to verified code state. |
| `session-reuse-architecture.md` | 2026-03-31, "FEASIBLE" | Native Claude resume, SessionManager, crash recovery, prompt-precompilation supersession | 2.5+ months old; predates current perf planning. Contradicts the multi-provider doc on Gemini. |
| `multi-provider-session-architecture.md` | 2026-04-01, "Research complete" | SessionCapable protocol, replay strategy, future API providers (OpenAI/Gemini/Mistral/Ollama/OpenCode/GLM), industry convergence | Thorough; claims Gemini `--resume` (disputed). |
| `agentic-worktree-execution-plan.md` | (undated in backlog) | agentic_dev epic (handler, plumbing, templates, internal QG loop, impl-summary, tests, Zone 1 decision) | Detailed 160-line plan; partially superseded by session reuse. |
| `enterprise-architecture-assessment.md` | ~Mar 2026 | MCP integration (P0 gap), PostToolUse hooks, JSON-schema tool_use, CI/CD, prompt ordering, deterministic auto-fix, structured errors, split-pass review, `.claude/rules/`, skills fork | Certification-pattern-oriented; some recommendations theory-based, not run-validated. |
| `codex-cli-research.md` + `codex-provider-implementation-plan.md` | 2026-05-29 | Codex provider epic (core, registration, schema, evidence integration, config, e2e, known bugs, model support) | Implementation diverged in places (stdin=PIPE vs DEVNULL; output via NDJSON). |
| Planning artifacts (prd/architecture/epics/epic-11/requirements-cursor/requirements-parallel) | Epic 11 era | Cursor provider epic, parallel-execution epic, spikes S1–S5, Phase 2/3 vision items, branch guard, NFRs | architecture.md carries an intentional stale "Two LLM providers" drift note. Coding-convention/enforcement patterns (architecture.md §356-499) are intentionally excluded as non-feature build conventions. PRD Phase-2 shared-cache/symlink item is tracked in §3.1, not §3.7. |
| 2026-06-13 run logs + sprint-status + epic-11-qa-report | 2026-06-13 | Toolchain mismatch, opaque exit codes, bootstrap-before-QG gap, conflict/rollback bugs, Codex Windows flailing, sprint-status conflation, cli_path-not-applied, PID orphan logs, em-dash fetch, serial Context7 latency | Most empirically grounded source; but contaminated by the Python-vs-JS misconfiguration. |

### Appendix B — Corrected / stale claims (full list)

| Item | Original claim | Correction |
|---|---|---|
| opt8-skip-create-story-if-exists | Naming mismatch between quality_gate and create-story | **FALSE** — `quality_gate._resolve_story_path()` (`quality_gate.py:137-160`) already handles both `story-{e}.{s}.md` and `{e}-{s}-*.md`. Re-verified on final pass. |
| opt2-drop-validation-phases | "not built; 9 commits not cherry-picked" | **PARTIAL** — phases fully built/enabled; ~6–8 (not 9) debug commits on branch; multi config now Gemini 3.1 Pro + Sonnet (not Codex+Opus); synthesis-diff files ARE produced on main. |
| claude-sdk-session-id-support | Implied not built | **PARTIAL** — `provider_session_id` field exists (`base.py:371`); Gemini (`gemini.py:372`) + Cursor (`cursor.py:521`) fully implement; only Claude SDK missing. SDK v0.1.34+ has the infra. Re-verified. |
| claude-sdk-cli-path-not-applied | Override "was not applied/configured" | **BUILT** — wiring exists and was validated (2.1.181 in real run); bundled used only because config unset. Graceful fallback by design. |
| claude-sdk-no-pid-tracking-orphan-risk | Orphan-risk bug | **STALE** — SDK API limitation, not a codebase bug; downgraded to DEBUG; documented fallback is provider rewrite. |
| opt7-skip-build-in-per-story-qg | "config-only change" | **MISLEADING** — build comes from story-file Quality Gates table, not config; requires template/create_story changes. |
| post-merge-qg-rollback-commit-handling | Commit fails with "nothing to commit" | **MITIGATED** — `git status --porcelain` pre-check exists; a race window remains. |
| bootstrap-base-repo-canary-before-postmerge-qg | Canary doesn't validate at run start | **STALE** — canary DOES validate (`bootstrap_worktree(validate=True)`); the gap is the *base repo* never bootstrapping. |
| structured-fact-extraction-sprint-status | Full sprint-status passed to synthesis | **FALSE PREMISE** — synthesis workflows don't load sprint-status at all. |
| merger-prompt-template-gap | Prompt content undefined (gap) | **BUILT** — `_build_resolution_prompt()` (`merger.py:161`) implemented and called at `merger.py:330`. Re-verified. |
| glm5-via-claude-code-cli | Trivial/no-code config integration | **INACCURATE** — `ClaudeSDKProvider` doesn't plumb `ANTHROPIC_*` env to `ClaudeAgentOptions.env`; medium effort + untested CLI passthrough. |
| windows-only-test-skip-markers | Should add skip markers | **SUPERSEDED** — commit 179ec6b fixed root cause (`_SIGKILL` fallback + path normalization) instead, which is superior. |
| agentic-dev-unified-handler / implementation-summary-protocol | fix_quality_gate lacks story context | **STALE** — fix-quality-gate now FULL_LOADs the story file (shipped commit e17542c). |
| codex-model-support-and-default | default `codex-mini-latest` | **DOC DRIFT** — actual code default is `gpt-5.3-codex`; README/PKG-INFO stale. |
| codex-known-bugs-workarounds | stdin=DEVNULL for #20919 hang | **TRADED** — stdin=PIPE used instead (solves the 32K command-line limit, a different bug). Stdin-hang workaround not applied. |
| multi-llm-no-session-reuse-constraint | Implied feature | **DESIGN CONSTRAINT** — enforced by parallel-spawn pattern, not explicit code. |
| drop-prompt-precompilation | Optimization to consider | **SUPERSEDED** by session reuse (no prompts to compile after turn 1). |

### Appendix C — Status tally

- **Built / already-done:** ~35 (entire Cursor + Codex provider epics, full parallel subsystem core, SIGKILL escalation, cli_path lever, FIX-QG, `(optional)` convention, several "decision/constraint" items).
- **Partial (gaps or caveats):** ~18 (OPT-2/5, section-level extraction, all Part B robustness items, Codex hardening/config/bugs, cursor S4, config hygiene, test markers, forensic retention, NFR baselines).
- **Not built:** ~40 (entire session-reuse epic + future API providers, entire agentic_dev epic, OPT-1/4/6/7/8, MCP, JSON-schema tool_use, hooks, CI/CD, prompt ordering, refinement loop, devex polish, Phase 2/3 vision).
- **Superseded:** 2 (prompt pre-compilation; windows-only skip markers approach).
- **Stale claims corrected:** 17 distinct items across 18 Appendix-B rows (one row bundles agentic-dev-unified-handler + implementation-summary-protocol).
