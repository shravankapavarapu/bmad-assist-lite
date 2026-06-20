# Performance & Quality-Gate Robustness Plan — bmad-assist-lite

> **Last refreshed: 2026-06-19.** This is the single source of truth for the combined
> "performance + quality-gate robustness" feature. It consolidates three threads:
> - **Part A** — per-story wall-clock optimization (original Feb 2026 analysis, reconciled against
>   current code + the June 2026 forensics).
> - **Part B** — parallel/merge robustness (post-merge quality-gate spurious blocks) — NEW; not in the
>   original report.
> - **Part C** — the unifying architecture (shared gate runner) that ties A's OPT-4 and B together.
>
> Status legend: ✅ done · ❌ not built (verified) · ⚠️ stale/changed · ❓ not re-verified this pass.

---

## Status at a glance (verified 2026-06-19)

| Item | Original status (Feb) | Verified code state (Jun 19) | Lands in |
|------|----------------------|------------------------------|----------|
| **OPT-1** per-phase model routing | APPROVED — biggest win | ❌ **not built** — no `phase_overrides`; `MasterProviderConfig` has one model/effort for all phases (`core/config.py:31`) | Part A · top lever |
| **OPT-4** parallel QG commands | APPROVED | ❌ **not built** — sequential `for entry in commands` loop (`quality_gate.py:248`) | Part A + Part C |
| **OPT-8** skip `create_story` if `.md` exists | APPROVED | ❌ **not built** (also tracked as the "cheap fix" in SESSION-HANDOFF §2) | Part A |
| **FIX-QG** (story file + retries + instructions) | proposed (separate doc) | ✅ **DONE** — `fix-quality-gate/workflow.yaml` FULL_LOADs the story file; `quality_gate.py:273` defaults `max_retries=2` | resolved |
| **OPT-2** skip validation phases | needs data; logging shipped | logging shipped on branch `debug/opt-2-validation-impact` (9 commits, **not cherry-picked to main**); decision pending | Part A · Tier 3 |
| **OPT-5** Context7 doc slimming | APPROVED (speed) | ⚠️ **premise stale** — June forensics measured Context7 at ~6s/run (one cached fetch). Token-cost/quality lever, **not a speed lever** | Part A · deprioritized |
| **OPT-6** cache toolchain detection | APPROVED | ❓ not re-verified | Part A · minor |
| **OPT-7** skip build in per-story QG | APPROVED | ❓ not re-verified | Part A · minor |
| **#1** post-merge QG robustness | (not in original report) | 🟡 root-caused, fix pending | Part B + Part C |

**Headline:** the two highest-leverage perf items (OPT-1, OPT-4) are still unbuilt and were
*independently re-confirmed* by the June forensics. FIX-QG is done — drop it from the backlog.

---

# Part A — Per-story wall-clock optimization

## A.1 Timing analysis — original run (Feb 2026: Epic 8, 5 stories, ~115+ min)

### Per-story breakdown

| Story | Total | Outcome | Biggest sinks |
|-------|-------|---------|---------------|
| 8.1 | ~30 min | PASSED | code_review_synthesis (8m40s), dev_story (7m29s) |
| 8.2 | ~25 min | BLOCKED | dev_story (8m38s), code_review_synthesis (5m49s) |
| 8.3 | ~22 min | BLOCKED | dev_story (6m39s), code_review_synthesis (4m5s) |
| 8.4 | ~26 min | BLOCKED | code_review_synthesis (6m2s), dev_story (5m55s) |
| 8.5 | ~12+ min | Incomplete | create_story (4m52s), still running |

### Time by phase type (all stories combined)

| Phase | Total time | % of run | Model |
|-------|-----------|----------|-------|
| dev_story | ~29 min | 28% | opus |
| code_review_synthesis | ~25 min | 24% | opus |
| create_story | ~15 min | 14% | opus |
| validate_story_synthesis | ~13 min | 13% | opus |
| code_review | ~10 min | 10% | parallel (gemini-flash + sonnet) |
| quality_gate + fix | ~14 min | 13% | non-LLM + opus |
| validate_story | ~6 min | 6% | parallel (gemini-flash + sonnet) |

**Key insight: Opus calls = 79% of total time.** 3/5 stories failed QG even after fix → ~12 min wasted
on stories that ended up blocked.

## A.2 June 2026 forensics — what is *actually* slow

Deep forensics on two later runs (3 parallel sub-agents over logs + a structural code read) overturned the
"it's overhead" assumptions:

- **96–98% of wall-clock is the model thinking/running tools.** Everything else is noise (measured):
  - SDK startup: ~1–3s first call, sub-second after. Negligible.
  - **MCP servers: 0 loaded** in either run (`setting_sources` empty → MCP never passed). The old "auto-loads
    4 MCP servers/phase" worry is **moot in practice.**
  - Inter-phase orchestration + Context7 inject + git/sprint-sync: **~2–5s TOTAL per story** (Context7 = one
    ~6s fetch at startup, cached).
  - The ~13 SDK `connect` tracebacks are harmless missing-`opentelemetry` (DEBUG, 0 retries, **0s lost**).

→ **Tuning the harness reclaims seconds. The hour is real LLM work.** So the fix is **structural** (which
model/effort per phase, how much redundant work each phase does), not infrastructural. This is why OPT-5/6
(harness-level) are deprioritized and OPT-1 (model routing) is the lever that matters.

### Where the hour goes (per story ≈ 50–56 min; apples-to-apples 8.5/8.6)

| Phase | Run A (main) | Run B (cursor) | Notes |
|---|---|---|---|
| create_story | 6m10 / 7m28 | 4m39 / 4m32 | Opus@max |
| validate_story (∥ codex+opus) | 4m30 / 4m57 | 4m12 / 3m12 | bounded by slowest validator |
| validate_story_synthesis | 7m07 / 4m53 | 6m04 / 5m52 | Opus@max |
| **dev_story** | **11m14 / 19m40** | **16m00 / 20m07†** | full lint/typecheck/build/test **in-turn** + per-task TDD; †hit 1200s timeout |
| code_review (∥ codex+opus) | 3m45 / 3m54 | 6m01 / 6m41 | |
| **code_review_synthesis** | **9m33 / 12m56** | **12m27 / 14m05** | applies fixes + **runs full build/test in-turn AGAIN** |
| quality_gate (+fix) | ~44s / ~2m | ~31s / ~2m | deterministic; fast |
| **Total** | **~43m / ~56m** | **~50m / ~56m** | |

## A.3 Root causes (structural — file:line)

1. **Uniform Opus @ effort=max on all 7 master phases.** The intended "Opus only for
   dev/code_review_synthesis/fix; Sonnet elsewhere" routing **was never built** —
   `MasterProviderConfig` (`core/config.py:31`) has ONE model/effort pair; `loop/handlers/base.py`
   returns it for every phase. No per-phase routing mechanism exists. **Highest-leverage gap. → OPT-1.**
2. **Redundant in-turn runtime verification.** Full lint/typecheck/build/test runs **inside dev_story's
   Opus turn** (`workflows/dev-story/instructions.xml` step 6), **again inside code_review_synthesis's
   Opus turn** (`workflows/code-review-synthesis/instructions.xml:37-47` step 6), then **again
   deterministically** in quality_gate → 3–4 full suite runs/story, two buried in Opus@max turns.
3. **No `max_turns` cap** (`providers/claude_sdk.py` options block) → dev_story loops unbounded → 20-min
   timeouts with truncated partial output.
4. **Validation phases** (~10 min/story combined) — removable via `loop.story` config. **→ OPT-2.**
5. **Quality-gate commands run sequentially** (`quality_gate.py:248`) — independent, parallelizable. **→ OPT-4.**

## A.4 Optimizations (reconciled)

### OPT-1 — Per-phase model routing  ❌ NOT BUILT — **top priority**
**Decision:** route by **MODEL, not effort** — keep `effort=max` globally (user preference). Opus stays on
`dev_story`, `code_review_synthesis`, `fix_quality_gate`; Sonnet for `create_story`,
`validate_story_synthesis`, `retrospective`.

**Config shape:**
```yaml
providers:
  master:
    provider: claude
    model: opus       # default
    effort: max       # kept globally
  phase_overrides:
    create_story: { model: sonnet }
    validate_story_synthesis: { model: sonnet }
    retrospective: { model: sonnet }
```

**Where to implement:**
- `core/config.py` — add `PhaseOverrideConfig` model + `phase_overrides` field on `ProvidersConfig`.
- `loop/handlers/base.py` — resolve the per-phase model from `phase_overrides` (fall back to master) at the
  single point where the master provider/model is selected.
- Keep `dev_story`, `code_review_synthesis`, `fix_quality_gate` on Opus.

**Est. savings:** ~10–15 min per 5 stories (create_story 3→1 min, validate_story_synthesis 3→1 min each).
**Isolated** — touches config + one resolution point, none of the gate code. Can ship independently/first.

### OPT-4 — Parallel quality-gate commands  ❌ NOT BUILT
Replace the sequential loop at `quality_gate.py:248` with concurrent execution
(`concurrent.futures.ThreadPoolExecutor` — reuse the pattern already in `code_review.py:136`). Commands are
truly independent (lint/typecheck/build/test); preserve original order in the failure report;
`command_timeout` stays per-command. **Est. savings ~20–30s/run × ~8 runs ≈ 3–4 min.**
→ **Build this as part of the shared gate runner (Part C), not a one-off.**

### OPT-2 — Skip validation phases  NEEDS DATA (logging shipped, not cherry-picked)
Drop `validate_story` + `validate_story_synthesis` from `loop.story` (config-only) — the single biggest cut
(~20 min combined across a story pair), but a **quality tradeoff** that needs data first. Diff-logging
instrumentation to gather that data exists on branch `debug/opt-2-validation-impact` (9 commits, **not yet
cherry-picked to main** — see SESSION-HANDOFF "Branch" notes). **Next action: cherry-pick the logging, run a
few stories, review `synthesis-diff-validate-*.patch` files, then decide.**

### OPT-8 — Skip `create_story` when `.md` exists  ❌ NOT BUILT
No skip-if-exists anywhere (`CreateStoryHandler` → `BaseHandler.execute()` → `create-story/instructions.xml`
all unconditional). Every run re-invokes Opus and rewrites the story `.md`, re-rolling the flaky CLI on a
heavy phase. Add a guard in `CreateStoryHandler.execute()`: if `{story_key}.md` exists & non-empty AND
sprint-status is `ready-for-dev` or beyond → return `PhaseResult.ok({"skipped": True})` with no LLM call.
(Also unlocks the optional batch `create-stories --epic N` command.) **Note:** verify the story-file naming
between `quality_gate`'s `story-{epic}.{story}.md` resolution and what create-story writes.

### OPT-5 — Context7 doc slimming  ⚠️ PREMISE STALE
Original framing was "40k tokens of irrelevant docs = a time sink." June forensics: Context7 is **~6s/run,
cached** — **not a wall-clock lever.** Reframe as a **token-cost / prompt-quality** improvement (story-specific
query refinement, lower `max_tokens_per_lib`/`max_libs`). Pursue only if token cost or prompt dilution is the
goal, not speed.

### OPT-6 — Cache toolchain detection  ❓ minor
`@lru_cache` on `core/toolchain.py:detect_toolchain()` and cache `shutil.which("gemini")` in
`providers/gemini.py`. ~10–30s total. Low priority; verify whether already done.

### OPT-7 — Skip build in per-story QG  ❓ minor
Config-only: drop `build` from per-story `quality_gate` (it runs in `epic_quality_gate` anyway). ~10–15s/run.
Partially overlaps root-cause #2. Low priority.

### FIX-QG — Fix-quality-gate context/instructions  ✅ DONE
The separate `fix-quality-gate-analysis.md` proposals shipped: the workflow now FULL_LOADs the story file
(`fix-quality-gate/workflow.yaml`), and `max_retries` defaults to 2 with retry context on 2nd+ attempt
(`quality_gate.py:273`). **No further work — kept here only so the backlog reads accurately.**

---

# Part B — Parallel/merge robustness (post-merge QG spurious blocks)  🟡 ROOT-CAUSED

> NEW since the original report. The parallel/merge subsystem (canary bootstrap, post-merge QG, conflict
> auto-resolution) postdates the Feb analysis. This is **correctness/robustness**, not speed — stories that
> passed in their worktree get **spuriously blocked** after merge.

## B.1 Root cause (confirmed 2026-06-18)
Post-merge QG ran every gate and failed in **~1 second** with:
```
WARN  Local package.json exists, but node_modules missing, did you mean to install?
'eslint' is not recognized ...   'tsc' is not recognized ...   'vitest' is not recognized ...
```
**The base repo has no `node_modules`.** Worktrees install deps via
`parallel.setup_commands: ["pnpm install --frozen-lockfile"]`, but the **base branch — where merges land and
post-merge QG runs — is never bootstrapped.** So on any fresh clone, post-merge QG always fails with
"command not found." The Opus fix subprocess then "produced no changes" (correctly — it's an env problem, not
a code one), and the result was logged as the useless **"failed gates: unknown."** Purely a base-repo env
issue — **not an SDK/cli_path regression.**

## B.2 Fixes to implement
1. **Bootstrap the base repo before post-merge QG** — run `setup_commands` in the base (or detect missing
   `node_modules`/deps and install) — and do the same install/validation **in the canary at run start** so
   broken deps fail fast (50 min earlier than post-merge).
2. **Classify env/tooling failures distinctly from real code-quality failures** — a gate that fails in ~1s
   with "command not recognized" / "node_modules missing" must NOT block the story or trigger a (pointless)
   Opus fix call. Surface it as a setup error; fix the "failed gates: unknown" reporting to name the cause.
   *(This is the same classification the shared gate runner provides — Part C.)*

## B.3 Open design questions (from the epic-8 `fable` run)
**(a) Conflict auto-resolution is fragile.** A one-file merge conflict gave the Claude CLI resolver 120s; it
timed out, the merge was **aborted**, and the branch/worktree were **deleted** — real, passing work didn't
land. Open:
  - Don't `git branch -D` / rmtree on resolution failure — preserve the branch so work isn't stranded.
  - Better than a timeout bump: retry, larger budget, or **fall back to conflict markers + a manual-resolve
    queue** instead of aborting.
  - Should a failed auto-resolve block the whole run, or park that story and continue?

**(b) Post-merge QG failures leave the base broken.** When tests fail post-merge, the auto-fix runs but a
follow-up `git commit` can fail (empty error → likely "nothing to commit"), leaving the base with merged code
and **red tests**, story marked blocked. Open:
  - Should a post-merge QG failure **roll back the merge** rather than leave the base broken?
  - Handle "fix produced no changes / commit failed" explicitly (distinct from "fix applied").
  - Where should post-merge fixes be committed, and how to verify before leaving the merge in place?

---

# Part C — Unifying architecture: the shared gate runner

**Why A (OPT-4) and B belong together:** gate commands now run in **three** places — per-story `quality_gate`
(`quality_gate.py:248`), post-merge QG (`parallel/merger.py`), and `epic_quality_gate`. Each open item modifies
that same command-running path:
- **OPT-4** wants those commands run **in parallel**.
- **B.2 (1)** wants deps **bootstrapped** before running them.
- **B.2 (2)** wants each result **classified** as `pass | real_failure | env_failure`.

Three call sites needing the same upgraded behavior → **Rule of Three is satisfied.** Extract a shared
**gate runner**:
- runs a list of gate commands **concurrently** (delivers OPT-4),
- returns a typed result per command classified `pass | real_failure | env_failure`
  ("command not found" / "node_modules missing" → `env_failure`),
- optionally ensures the working tree is bootstrapped first,
- is reused by per-story, post-merge, and epic gate phases for **consistent** behavior.

One abstraction delivers OPT-4's parallelism **and** B's env-vs-real classification **and** uniform gate
behavior everywhere. **OPT-1 (model routing) sits outside this** — it's config + one resolution point and
touches none of the gate code, so it ships independently.

---

## Implementation sequencing (2026-06-19)

1. **OPT-1 — per-phase model routing.** Isolated, biggest single speed win, low risk. **Start here.**
2. **Extract the shared gate runner** (Part C) with parallelism + `pass|real|env` classification → delivers
   OPT-4 and the foundation for Part B.
3. **B.2 (1) — base-repo / canary bootstrap** on top of the runner.
4. **B.3 — conflict-resolution robustness** (preserve branch on failure; roll-back-or-park decisions).
5. **Cheap follow-ons:** OPT-8 skip-if-exists; OPT-2 (cherry-pick logging → gather data → decide); OPT-6/7 if
   still unbuilt.

### Combined impact (June baseline ≈ 50–56 min/story)
| Stack | Effect |
|-------|--------|
| OPT-1 + OPT-4 | ~7–9 min/story, low risk |
| + remove in-turn verification from code_review_synthesis (root cause #2) | ~4–6 min/story — MEASURE failure-rate shift first |
| + OPT-2 drop validation phases | ~10 min/story — quality tradeoff, needs data → back toward historical 30–35 min |
| + `max_turns` cap on dev_story | bounds the 20-min runaway + truncated-dev cascade |

---

## Appendix — debug instrumentation already shipped

`validate_story_synthesis` and `code_review_synthesis` already emit diagnostics (run with `-vv`):

**validate_story_synthesis** — captures story file before/after, logs prompt-composition + per-validator
response sizes, computes a unified diff, prints a summary, and saves:
- `.bmad-assist-lite/cache/synthesis-diff-validate-{id}.patch`
- `.bmad-assist-lite/cache/synthesis-response-validate-{id}.md`

**code_review_synthesis** — captures `git diff --stat` before/after, logs sizes, prints a change summary, and
saves:
- `.bmad-assist-lite/cache/synthesis-diff-review-{id}.patch`
- `.bmad-assist-lite/cache/synthesis-response-review-{id}.md`

**How to use for OPT-2:** after a run, if most `synthesis-diff-validate-*.patch` files show no changes, the
validation synthesis isn't earning its ~10 min — supports dropping it. If they show real changes, compare
against `code_review` findings to check for redundancy before cutting.

### Cache files generated per story (with `-vv`)
| File | Phase | Content |
|------|-------|---------|
| `synthesis-diff-validate-{id}.patch` | validate_story_synthesis | Unified diff of story-file changes |
| `synthesis-response-validate-{id}.md` | validate_story_synthesis | Full LLM response text |
| `synthesis-diff-review-{id}.patch` | code_review_synthesis | Git diff of source-code changes |
| `synthesis-response-review-{id}.md` | code_review_synthesis | Full LLM response text |
| `validations.json` | validate_story | Raw validator outputs + evidence scores |
| `reviews.json` | code_review | Raw reviewer outputs + evidence scores |
| `qa-failures-{id}.md` | quality_gate | Failure report (if QG failed) |
| `post-merge-qg-failures-{id}.md` | post-merge QG | Post-merge failure report (see Part B) |
