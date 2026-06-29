# GOAL — Autonomous Performance & Robustness Improvement of bmad-assist-lite

> **This file is the contract for a long-running autonomous job.** It is designed to be
> re-fed to the runner every iteration (Claude Code `/goal`, `/loop`, or `ralph-loop`).
> Read it top-to-bottom on the *first* iteration. On *every subsequent* iteration, read
> [`loop/PROGRESS.md`](loop/PROGRESS.md) FIRST (it is the source of truth for where you
> are), then re-read the sections of this file relevant to the current phase.
>
> Refined from the original brain-dump in [`goal.txt`](goal.txt) by Winston (System
> Architect) on 2026-06-28. The original is kept verbatim as the source of intent.

---

## 0. Runner contract — how this job executes

**Target codebase:** THIS repo — `bmad-assist-lite` (the orchestration tool itself).
The improvements modify this tool. The pain that motivated them was observed in a
*downstream* project (`content-ai-studio`, EPIC-8); those session logs are evidence, not
the work surface.

**How to launch (pick one; `/goal` is the intended runner):**
```text
/goal Execute docs/performance-improvement/goal.md. Read loop/PROGRESS.md first every
      iteration. Output the completion promise ONLY when Section 9's criteria are
      unequivocally met.
```
Fallback (Stop-hook loop) — equivalent behavior:
```text
/ralph-loop "Execute docs/performance-improvement/goal.md following its runner contract.
            Read docs/performance-improvement/loop/PROGRESS.md first each iteration."
            --completion-promise "GOAL-COMPLETE-BMAD-PERF" --max-iterations 250
```

**Completion promise (exact string):** `GOAL-COMPLETE-BMAD-PERF`
Emit it ONLY when Section 9 is satisfied. Never emit it to escape the loop early, even if
stuck — instead, park the blocker (Section 7) and continue with other work.
**First-run marker:** when `loop/PROGRESS.md` has `stop_after_phase: P5_PLAN` (the default for
run #1 — research-first), emit `PHASE-A-COMPLETE-BMAD-PERF` at the gate instead, having
touched NO production code (Section 9).

**Iteration discipline (anti-context-rot):**
- One iteration = advance ONE task from the `PROGRESS.md` queue (or one coherent chunk).
- Offload heavy reading/searching/implementation to **fresh subagents** (Task tool) so the
  driving thread stays lean. Never let the main thread accumulate a giant transcript.
- After each chunk: update `PROGRESS.md` (append a work-log entry + update the queue),
  then re-orient from disk rather than from memory.

**Persistence is mandatory.** All durable state lives on disk under `loop/` so the job
resumes cleanly after any crash or context reset:
- `loop/PROGRESS.md` — current phase, task queue with statuses, append-only work log.
- `loop/DECISIONS-NEEDED.md` — parked product/business/irreversible decisions (your inbox).
- `loop/decisions/ADR-NNNN-*.md` — architecture decisions made autonomously (for later review).
- `loop/requirements/` — verified requirements (Phase A output).
- `loop/research/` — research notes (internal + OSS) and spike findings.
- `loop/verification/` — standalone verification scripts + their output logs.

---

## 1. Mission & success criteria

**Mission:** Make bmad-assist-lite **faster per story** and **robust through parallel
merges**, by turning the existing research into verified requirements, having the BMAD
architect and the agent party endorse them, and then implementing them autonomously with
adversarial review and architect sign-off — stopping only for product/business decisions.

**Primary success metrics (defaults; Phase A may refine and record the final targets in
`loop/requirements/`):**
1. **Speed:** ≥ 30% reduction in per-story wall-clock vs. the documented June baseline
   (~50–56 min/story). Measured, not estimated (Section 2 verification rules).
2. **Merge robustness:** zero spurious post-merge quality-gate blocks (env-vs-real failures
   correctly classified); concurrent worktree merges serialize correctly with no
   "second-merge-fails-because-base-moved" data loss.
3. **No regressions:** full test suite stays green (currently ~1760 tests); `mypy src/`
   and `ruff check src/` clean; project conventions in `CLAUDE.md` /
   `_bmad-output/project-context.md` upheld.

**Definition of done for the whole job:** see Section 9.

---

## 2. Operating rules (non-negotiable)

1. **Claude only for testing/spikes. NEVER use Codex. This is non-negotiable.** Any live
   run of bmad-assist-lite performed to validate a change must use a Claude-only provider
   config (master + multi all `provider: claude`). Do not add, enable, or invoke the Codex
   provider during this job. (The tool *supports* Codex; we simply don't use it here.)
2. **Verify with standalone scripts — do not trust prose.** Every claim taken from a
   research report, and every requirement, must be checked against the live code by a
   **standalone script** in `loop/verification/` (Python, runnable via the project `.venv`).
   "The doc says X is not built" is a hypothesis until a script proves it.
3. **Never self-verify.** The agent that wrote code/requirements does not get to declare
   them correct. Spawn a **separate verifier subagent** (fresh context) to run the
   verification script, run the relevant tests, and report back pass/fail with evidence.
   This mirrors the "don't get the agent to self-verify" rule from the loop research.
4. **Measure, don't assume.** A documented June incident shipped a *plausible-but-wrong*
   fix because the cause was assumed, not measured. Before optimizing, capture a real
   timing/profiling measurement; after, re-measure. Record both in `loop/verification/`.
5. **Respect existing architecture & conventions.** Frozen Pydantic + `model_copy`,
   absolute imports, `pathlib`, atomic writes, `X | None` syntax, line length 100, strict
   mypy, ruff. The full ruleset is `_bmad-output/project-context.md` — treat it as binding.
6. **Small, reversible, isolated changes.** Prefer config + single-resolution-point changes
   (e.g., per-phase routing) over sprawling refactors. Rule of Three before abstraction.
7. **Use `.venv` for all Python** (pip, pytest, mypy, ruff).
8. **Do not push or open PRs unless explicitly authorized.** Commit on a feature branch.
   Branch off `main`; never commit directly to `main`.

---

## 3. Agent-invocation protocol (call the BMAD agents directly)

This job is run by a captain, not a coder. **You MUST delegate to the BMAD agents via the
Skill tool at the points below** — do not do their jobs inline.

| When | Invoke (Skill) | Purpose |
|------|----------------|---------|
| Requirements drafted (end of Phase 2) | `bmad-agent-architect` (Winston) | Architect reviews the requirements. Iterate with the architect until **both agree**. Architect logs any design decisions as ADRs. |
| Architect agrees (end of Phase 3) | `bmad-party-mode` | Convene the BMAD agents (PM/John, Analyst/Mary, Dev/Amelia, UX/Sally, Tech-writer/Paige) to pressure-test and **enhance** the requirements. Fold their input back in. |
| Significant design choice before building an item | `bmad-agent-architect` and/or `bmad-create-architecture` | Make/record the design decision as an ADR (Section 7). |
| Code changes complete for an item | `bmad-code-review` | Adversarial multi-layer review (Blind Hunter / Edge-Case Hunter / Acceptance Auditor). This replaces your manual `/code-review` step. |
| Deeper edge-case pass when warranted | `bmad-review-edge-case-hunter` | Exhaustive boundary/branch analysis. |
| Review found issues | `bmad-agent-dev` (Amelia) | Fix the findings. Loop review→fix up to `code_review_max_iterations` (default 3, configurable in `PROGRESS.md` settings block). |
| After fixes land | `bmad-agent-architect` (Winston) | **Architect re-review to confirm correctness** of the changes — your explicit requirement. Architect must sign off before the item is marked verified. |
| Mid-job scope/strategy change | `bmad-correct-course` | Manage a significant pivot deliberately. |
| Job end | `bmad-retrospective` | Extract lessons; write the final report. |

**Rule:** when a BMAD skill exists for a step, prefer it over ad-hoc work. Record every
agent invocation and its outcome in the `PROGRESS.md` work log.

---

## 4. Phase state machine

The loop is a state machine. `PROGRESS.md` records `current_phase`. Phases run in order,
but the job is **autonomous and continuous** — there is **no hard human gate** between
planning and implementation (per the operator's decision). The loop flows from research
straight into building, escalating to the human only via the park mechanism (Section 7).

```
P0  SETUP        Initialize loop/ scaffold, settings, Claude-only test config, baseline measurement.
P1  RESEARCH     Read all reports + perf plan + transcripts; write standalone verification scripts;
                 confirm/correct each claim against live code; AND run the deep OSS-landscape
                 research (WS5 — broad, ANY language). Outputs: loop/research/verified-findings.md
                 + loop/research/oss-landscape.md.
P2  REQUIREMENTS Convert verified findings into concrete, testable requirements. Output: loop/requirements/.
P3  ARCHITECT    Invoke bmad-agent-architect to review requirements; iterate to mutual agreement; log ADRs.
P4  PARTY        Invoke bmad-party-mode; enhance requirements with the agents' input.
P5  PLAN         Prioritize & sequence the work (PHASE A DECIDES THE ORDER). Build the task queue in PROGRESS.md.
P6+ IMPLEMENT    For each queued item, run the per-item cycle (below) until the queue is drained.
Pf  RETRO        Invoke bmad-retrospective; write final report; verify Section 9; emit completion promise.
```

**Run scoping (`stop_after_phase`).** `loop/PROGRESS.md` carries a `stop_after_phase` setting.
For **run #1 it is `P5_PLAN`**: the loop executes P0→P5 (all research, verification, the OSS
spike, architect + party review, and the prioritized plan) and then **STOPS without touching
production code**, emitting `PHASE-A-COMPLETE-BMAD-PERF`. This is the research-first gate — you
review the verified plan, then set `stop_after_phase: null` for **run #2** to proceed into P6+.
Note: P1–P5 still legitimately write *investigation code* (verification scripts, a baseline
measurement harness, throwaway worktree spikes) — that is research/testing, not production
changes to the tool.

**Per-item implementation cycle (P6+), each item runs end-to-end before the next where deps allow:**
```
a. DESIGN     Architect decision + ADR if the item involves a design choice (Section 7).
b. BRANCH     Create a feature/worktree branch off main (isolation; Claude-only test config).
c. BUILD      Invoke bmad-agent-dev (Amelia) to implement per the requirement + ADR.
d. VERIFY     Standalone verification script + targeted tests, run by a SEPARATE verifier subagent.
e. REVIEW     bmad-code-review (adversarial). Loop d↔e with Amelia fixes up to code_review_max_iterations.
f. ARCH-SIGNOFF  bmad-agent-architect re-reviews the changes; must sign off.
g. MEASURE    Re-measure the relevant metric; compare to baseline; record in loop/verification/.
h. MERGE      Merge via the merge protocol (Section 6). Update PROGRESS.md → item VERIFIED/MERGED.
```
If any step surfaces a product/business/irreversible decision → **park it (Section 7) and
move to the next unblocked item.** Do not stall the whole loop.

---

## 5. Workstreams → phases, with known starting facts

The original goal braids six workstreams. They map onto the phases as backlog items. The
**known facts below are pre-verified context** so the runner does not rediscover them — but
P1 must still re-confirm each with a standalone script before acting (Rule 2).

### WS1 — Research → verified requirements  (Phases P1–P2)
Read everything in `_bmad-output/reports/` and `docs/performance-optimization-plan.md`,
plus `docs/fix-quality-gate-analysis.md`. Produce `loop/research/verified-findings.md` with
a verification script per material claim.

### WS2 — Performance / speed  (top priority for the operator)
Known top levers (all **NOT built** as of 2026-06-19 unless noted):
- **Per-phase model routing** — route Sonnet to low-judgment phases (create_story,
  validate_story_synthesis, retrospective); keep Opus@max on dev_story,
  code_review_synthesis, fix_quality_gate. Route by **model, not effort** (keep
  `effort: max`). ~10–15 min / 5 stories. *Highest isolated win; ships independently —
  config + one resolution point in `BaseHandler.get_model()`.*
- **Shared gate runner ("Part C")** — extract `core/gate_runner.py` consumed by
  `quality_gate.py`, `merger.py`, `epic_quality_gate.py`. Unifies (a) parallel gate
  commands, (b) env-vs-real failure classification, (c) base-repo bootstrap. **Keystone
  refactor** — it is the foundation for WS6 robustness too.
- **Parallel quality-gate commands** — build inside the shared gate runner, not as a one-off.
- **Skip create_story when a non-empty story .md already exists** — small/low; ~6–8 min on resume.
- **Cache toolchain / CLI detection** (`@lru_cache`, key must include cwd for worktrees).
- **Drop / trim validation phases** — config-only but **data-gated**: cherry-pick the diff
  logging from branch `debug/opt-2-validation-impact`, run stories, inspect patch sizes,
  cut only if consistently trivial.
- **Session reuse (resume) as a perf lever** — ~35–45% time / ~79% token. Bigger; gate on
  whether per-phase routing already meets the latency target. **Distinct from** fresh-start
  hygiene below.
- **Fresh-start / stale-state hygiene** (NOT the same as "no session reuse") — ensure stale
  on-disk state cannot corrupt or slow a run: stale `*.tmp` cache files, stale
  `state.yaml` / `sprint-status.yaml` / `parallel-state.yaml`, the `story-queue.yaml` cache,
  a stale `running.lock`, and any stale resume checkpoints. `loop/cleanup.py` already cleans
  `*.tmp` on resume — audit and extend it. **NOTE (operator-confirmed):** the ~265 s/run
  host-contention in the session logs came from un-reaped **MCP servers in the *downstream*
  content-ai-studio project** — **bmad-assist-lite itself uses NO MCP servers**, so that
  specific remedy does not apply here. The transferable, still-real risk is stale-checkpoint
  hygiene.
> **Clarified for the operator:** "prevent session resume to gain speed" is a
> misconception — *resume is a speed gain*. The first ADR in P3 must settle resume-vs-hygiene
> explicitly so the operator can veto.

### WS3 — Prompt enhancement  (perf + quality)
Instead of one static template for all stories, **enhance the prompt per task** for story
creation (and consider dev/review). Architecture docs + Context7 are already injected; the
hypothesis is task-specific tuning beats one-size-fits-all. **Treat as an experiment:** A/B
two or more prompt variants, measure via Evidence Score variance and review-finding counts,
adopt the winner. Position-aware ordering (AC/tasks first, instructions last) is a candidate
variant. Quality lever primarily, not pure speed.

### WS4 — Enhanced in-loop code review + architect re-review
Automate the operator's manual post-run loop (he *always* finds issues with `/code-review`,
then re-runs the dev agent, then has the architect confirm). Fold `bmad-code-review` +
dev-fix + architect-sign-off into the per-item cycle (Section 4 d–f), **looped with a
configurable iteration cap** (`code_review_max_iterations`). Study how the BMAD review
workflows operate and reuse their logic rather than inventing a parallel mechanism.

### WS5 — OSS research → evolve (NOT rewrite, unless overwhelming)  ← part of INITIAL research (P1)
Decision recorded: **evolve / harvest patterns.** This runs in **P1 as part of the initial
research, NOT deferred to the end** — its findings must shape the requirements and the
prioritized plan; research that lands after implementation cannot influence what we build.

**Do a genuinely DEEP, BROAD discovery — do not stop at the named seeds.** Use the
`deep-research` skill (or fan-out WebSearch/WebFetch subagents) to find
agentic-engineering / autonomous-software-engineering workflow projects **in ANY language**.
The named repos are only a starting point — GNHF (`kunchenguid/gnhf`), firstmate
(`kunchenguid/firstmate`), `AI-Builder-Club/skills`, ralph-loop /
`mikeyobrien/ralph-orchestrator`. Cast wider: search angles like "agentic coding loop /
orchestrator", "autonomous SWE agent", "multi-agent dev pipeline", "ralph wiggum technique",
"spec-driven development agent", and survey notable peers (BMAD-METHOD, OpenHands, SWE-agent,
Aider, claude-flow, etc.). Judge each on whether it solves **OUR** problems: per-story speed,
merge robustness, in-loop review, and cross-phase continuity.

For each promising project: write `loop/research/oss-<name>.md` (what it does, what we lack,
what to harvest, and fit vs. our subprocess/worktree architecture), and review it with
`bmad-agent-architect`. If the architect agrees it adds value, run a **bounded, throwaway
spike in a separate worktree (Claude-only)** to learn how it actually works — this is
research/testing, so it happens **within Phase A, before the production-code gate**. Produce a
consolidated `loop/research/oss-landscape.md`. Recommend a full rewrite **only** if the
evidence is overwhelming (standing verdict: EVOLVE); any rewrite recommendation is a **parked
product decision** (Section 7), never executed autonomously. Concrete "adopt pattern X" items
are added to the queue during P5 (the old end-of-queue T23 is superseded by this P1 research).

### WS6 — Merge robustness + parallel-merge race condition  (the operator's "Final" item)
Two problems:
1. **Post-merge tests fail / spurious quality-gate blocks.** Root cause (verified
   2026-06-18): the **base repo is never bootstrapped**, so post-merge gates run against
   missing deps → "failed gates: unknown." Fix: bootstrap the base/merge target like
   worktrees, and **classify env-vs-real failures** (don't send env failures to the Opus
   fixer). Build both inside the **shared gate runner** (WS2).
2. **Concurrent-merge race condition** (a requirement, not yet observed in logs — design it
   carefully). See Section 6 for the design brief.

---

## 6. Merge protocol design brief (WS6)

The operator's stated requirement, made precise. The architecture reports do **not** cover
this — it is net-new design. The architect must produce an ADR before implementing.

**Problem.** With parallel stories in separate worktrees, story A merges to the integration
branch first. Story B then tries to merge, but the base has moved; if A and B touched the
same files, B's merge fails. If a third story C tries to merge while B is mid-rebase, and C
merges first, B's rebase is invalidated and B must redo everything.

**Required behavior (the operator's words, formalized):**
1. **Serialize merges with a queue.** Only one merge may be *in flight* at a time. While B
   is rebasing/merging, C must **wait** — it does not jump ahead.
2. **Rebase-before-merge.** Before merging, a story must: (a) check for new merges on the
   integration branch, (b) rebase its branch onto the latest integration head, (c) resolve
   any merge conflicts (Claude-only conflict resolution; existing
   `merger._build_resolution_prompt()` is the starting point), (d) **re-run the full test
   suite / quality gate on the rebased code**, (e) only then acquire the merge lock and merge.
3. **No data loss on failure.** On conflict-resolution or post-merge-QG failure, do **not**
   delete the branch/worktree (current behavior strands passing work). Park the merge for
   manual resolution or retry with a larger budget; keep the work.
4. **Post-merge verification with classification.** After merge, run the gate via the shared
   gate runner; an **env failure** (missing deps, exit 127) must not be reported as a code
   failure or routed to the LLM fixer — it triggers a base bootstrap + retry.

**Design questions the architect must resolve in the ADR (trade-offs, not verdicts):**
- Queue mechanism: in-process async lock vs. on-disk lock file (crash-safe across the
  subprocess-per-worktree model already in use)? Lean toward the existing battle-tested
  on-disk locking pattern.
- Integration target: merge into `main` directly, or into a dedicated integration branch
  that is fast-forwarded to `main` at epic teardown?
- Rebase vs. merge-commit for incorporating new base changes (rebase keeps history linear
  but rewrites SHAs the worktree may reference).
- Retry/backoff policy and the maximum number of rebase attempts before parking.
- How the queue interacts with the existing `merger.py` merge queue and orchestrator
  (extend, don't replace — the orchestrator subprocess model is "battle-tested, don't
  change" per the reports).

Build the runner-facing pieces on top of the **shared gate runner** so post-merge gates,
classification, and base bootstrap are shared with per-story and epic gates (Rule of Three).

---

## 7. Escalation & decision logging (the autonomy boundary)

Operator decision: **Architect decides + park-and-continue.**

**Decide autonomously (and log an ADR in `loop/decisions/`):** any decision that is
consistent with the existing architecture and conventions, reversible, and contained within
the tool's current design. Use the ADR template; number sequentially `ADR-0001`, etc.
Record: context, options weighed, decision, consequences, and how to reverse it.

**PARK in `loop/DECISIONS-NEEDED.md` and continue with other unblocked work** — do NOT
decide these yourself:
- **Product/business** decisions (what the tool should do for its users; default
  provider/model strategy as a shipped default; pricing/cost posture; UX of the CLI).
- **Architecturally irreversible or cross-cutting** changes (a rewrite recommendation; a
  new external dependency; changing the public config schema in a breaking way; anything
  that would invalidate large amounts of existing work).
- **Anything where two options are genuinely close and the choice has lasting impact** and
  no clear architecture-aligned default.

When you park an item: write a `DECISIONS-NEEDED.md` entry (Section 8 schema), mark the
related queue item `BLOCKED-ON-OPERATOR` in `PROGRESS.md`, and **move on**. The loop never
fully stalls; it drains everything it *can* do and surfaces the rest.

**Future hook (design now, don't build yet):** the park mechanism is the seam where a
Discord/messaging notifier or a human-proxy agent will later be attached. Keep
`DECISIONS-NEEDED.md` machine-appendable (stable entry schema, status field) so a future
notifier can watch it and a future responder can write answers back into it. Do not build
the notifier in this job.

---

## 8. State files & schemas

### `loop/PROGRESS.md` (source of truth — read FIRST every iteration)
Contains, in order: a **settings block** (tunables), `current_phase`, the **task queue**
(table with id, workstream, title, status, deps, branch, notes), and an **append-only work
log** (newest entries at the bottom; read the last 5–10 before acting).
Statuses: `TODO`, `IN-PROGRESS`, `IN-REVIEW`, `BLOCKED-ON-OPERATOR`, `VERIFIED`, `MERGED`,
`DEFERRED`.

### `loop/DECISIONS-NEEDED.md` (operator inbox)
Per-entry schema:
```
### D-NNNN — <short title>   [status: OPEN | ANSWERED | WONTFIX]
- Raised: <iteration / date>
- Blocks: <queue item id(s)>
- Context: <why this came up>
- Options: <A / B / C with the architect's trade-off read>
- Architect recommendation: <option + one-line why>
- Operator answer: <left blank for the human; a future notifier may fill this>
```

### `loop/decisions/ADR-NNNN-<slug>.md` (autonomous decisions, for later review)
Use `loop/decisions/ADR-TEMPLATE.md`. Keep them short and reversible-by-design.

### `loop/verification/`
One script per verified claim/requirement (`verify_<topic>.py`), plus its captured output
(`verify_<topic>.out.txt`). Scripts must be runnable standalone via `.venv` and exit
non-zero on failure so the verifier subagent gets a clean signal.

---

## 9. Stop conditions & completion

**Emit `GOAL-COMPLETE-BMAD-PERF` only when ALL of these hold:**
1. Every task-queue item is `MERGED`, `VERIFIED`, `DEFERRED`, or `BLOCKED-ON-OPERATOR`
   (i.e., no `TODO`/`IN-PROGRESS`/`IN-REVIEW` work remains that you can do unaided).
2. The full test suite is green; `mypy src/` and `ruff check src/` are clean.
3. The primary success metrics (Section 1) are either **met and measured**, or the reason
   they cannot be met without an operator decision is captured in `DECISIONS-NEEDED.md`.
4. `bmad-retrospective` has run and a final report exists at
   `loop/research/final-report.md` (what shipped, measured before/after, what's parked,
   what's deferred, recommended next job).

**Phase-scoped stop (run #1 — research-first).** If `loop/PROGRESS.md` sets `stop_after_phase`,
then when that phase completes: write the phase summary + the deliverables list to
`PROGRESS.md`, ensure **no production code under `src/` was changed** (investigation code under
`loop/` is fine), and emit `PHASE-A-COMPLETE-BMAD-PERF` (NOT the full promise), then stop.
Run #2 sets `stop_after_phase: null` to continue into P6+.

**Safety stops (do NOT emit the promise):**
- Iteration cap reached (runner-enforced) → write a status summary to `PROGRESS.md` and stop.
- Token/quota budget low → checkpoint state to disk and stop cleanly (resume later).
- Repeated failure on the same item (≥ 3 attempts) → park it and continue; never loop a
  known-bad action.

---

## 10. Pre-verified starting context (re-confirm in P1, but don't rediscover blind)

- **Baseline:** ~50–56 min/story (June 2026 forensics); 96–98% of wall-clock is the model
  thinking/running tools, ~79% of run time is Opus calls. Levers are **structural** (which
  model per phase, how much redundant work), not infrastructural.
- **Already shipped — drop from scope:** `fix_quality_gate` (story file now full-loaded;
  `max_retries` default 2); the `cli_path` lever (claude system CLI wired into the SDK).
- **NOT built (the real backlog):** per-phase model routing, shared gate runner, parallel
  gate commands, env-vs-real classification, base-repo bootstrap, skip-create-story-if-md,
  toolchain/CLI caching, session reuse, the merge queue/rebase protocol of Section 6.
- **The 2026-06-13 disaster** (~5h wasted) was a toolchain misconfig (pnpm/tsc/vitest run
  against a pure-Python repo), not a code-quality failure — toolchain auto-detection +
  env-vs-real classification would have caught it. Weight this when prioritizing.
- **Merge race condition** is a *requirement from the operator*, not an incident in the
  logs — design it from first principles (Section 6).
- **Enterprise verdict:** EVOLVE, not rewrite. Its top named gap was MCP tool integration —
  but **the operator has confirmed bmad-assist-lite uses NO MCP servers and wants MCP off
  the table.** So MCP-based continuity is OUT of scope for this job.
- **Continuity approaches now narrow to TWO** (session reuse / agentic_dev impl-summary) —
  the MCP agentic-loop option is removed per the operator. Both solve the same "fresh
  session per phase" waste; **choose ONE** primary — do not build both. Architect decision
  (ADR) early in P3.
- **Gemini `--resume` contradiction:** two reports disagree on whether Gemini supports
  `--resume`. Resolve empirically (`gemini --help`) before relying on it. (Low priority —
  we test Claude-only anyway.)

---

## 11. Source material index

- `goal.txt` — original operator brain-dump (intent of record).
- `docs/performance-optimization-plan.md` — consolidated perf + QG-robustness plan (Parts A/B/C).
- `_bmad-output/reports/pending-features-and-architecture-study.md` — master backlog, build-status verified.
- `_bmad-output/reports/session-reuse-architecture.md` — single-provider session resume.
- `_bmad-output/reports/multi-provider-session-architecture.md` — generalized session mgmt.
- `_bmad-output/reports/agentic-worktree-execution-plan.md` — agentic_dev zones (alt continuity approach).
- `_bmad-output/reports/enterprise-architecture-assessment.md` — EVOLVE verdict, MCP/structured-output gaps.
- `docs/fix-quality-gate-analysis.md` — fix-QG (already shipped).
- `docs/performance-improvement/transcript/{code_less_loop_more,loop_engineer,agentic_workflow}.txt`
  — looping methodology (loop contract, shared-brain files, no-self-verify, worktree mgmt, caps).
- `docs/performance-improvement/sessions/` — real post-EPIC-8 pain (hollow stories, deferred-test
  failures, MCP pile-up/host-contention, status drift).
- `_bmad-output/project-context.md` — binding code conventions for every change.
```
