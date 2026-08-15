# PROGRESS — source of truth for the autonomous job

> Read this FIRST every iteration. Update it (queue + work log) after every chunk of work.
> The goal contract is `../goal.md`. Decisions for the operator go in `DECISIONS-NEEDED.md`.

## Settings (tunables)
```yaml
current_phase: P0_SETUP            # P0_SETUP → P1_RESEARCH → P2_REQUIREMENTS → P3_ARCHITECT
                                   # → P4_PARTY → P5_PLAN → P6_IMPLEMENT → Pf_RETRO
stop_after_phase: P5_PLAN          # RESEARCH-FIRST GATE for run #1: stop after the plan, touch NO
                                   # production code, emit PHASE-A-COMPLETE-BMAD-PERF. Set to null
                                   # for run #2 to proceed into P6+ implementation.
code_review_max_iterations: 3      # review→fix loop cap per item (WS4)
max_implementation_attempts: 3     # park an item after this many failed attempts
test_provider: claude              # Claude ONLY for any live bmad-assist-lite test run (non-negotiable)
measure_before_and_after: true     # capture real timings, never estimate
baseline_min_per_story: 53         # ~50–56 min/story (June 2026); re-measure in P0
speed_target_reduction_pct: 30     # primary metric; Phase A may refine
```

## Status legend
`TODO` · `IN-PROGRESS` · `IN-REVIEW` · `BLOCKED-ON-OPERATOR` · `VERIFIED` · `MERGED` · `DEFERRED`

## Task queue
> Seeded by Winston as a starting backlog. **Phase A (P5) decides the final order** after
> the architect + party-mode passes. IDs are stable; add new items as discovered.

| ID | WS | Title | Status | Deps | Branch | Notes |
|----|----|-------|--------|------|--------|-------|
| T01 | WS1 | Verify all report claims with standalone scripts | TODO | — | — | Output: research/verified-findings.md |
| T02 | WS1 | Draft testable requirements from verified findings | TODO | T01 | — | Output: requirements/ |
| T03 | WS1 | Architect review of requirements (bmad-agent-architect) | TODO | T02 | — | Iterate to mutual agreement; log ADRs |
| T04 | WS1 | Party-mode enhancement of requirements (bmad-party-mode) | TODO | T03 | — | Fold agents' input back in |
| T05 | WS1 | Prioritize & sequence backlog (Phase A decides order) | TODO | T04, T06 | — | Rewrite this queue's order |
| T06 | WS5 | DEEP OSS research (broad, ANY language) → evolve/harvest | TODO | T01 | — | PART OF INITIAL RESEARCH (P1). deep-research/web fan-out beyond named seeds; architect-reviewed; bounded Claude-only worktree spike within Phase A; rewrite rec → DECISIONS-NEEDED. Output: research/oss-landscape.md + oss-<name>.md |
| ADR | WS2 | ADR: session reuse vs fresh-start hygiene (settle the misconception) | TODO | T01 | — | Operator may veto |
| ADR | WS2/WS6 | ADR: continuity approach — pick ONE (session reuse / agentic_dev) | TODO | T01 | — | MCP option removed (no MCP in this project); don't build both |
| ADR | WS6 | ADR: merge-queue + rebase-before-merge protocol (goal.md §6) | TODO | T01 | — | Net-new design |
| T10 | WS2 | Per-phase model routing (Sonnet for low-judgment phases) | TODO | T05 | — | Top isolated speed win; config + get_model() |
| T11 | WS2/WS6 | Shared gate runner `core/gate_runner.py` (keystone) | TODO | T05 | — | Parallel gates + env/real classify + base bootstrap |
| T12 | WS2 | Parallel quality-gate commands (inside T11) | TODO | T11 | — | — |
| T13 | WS6 | Env-vs-real gate failure classification (inside T11) | TODO | T11 | — | Stop routing env fails to LLM fixer |
| T14 | WS6 | Base-repo bootstrap before post-merge QG (inside T11) | TODO | T11 | — | Fixes "failed gates: unknown" |
| T15 | WS6 | Merge queue + rebase-before-merge + no-data-loss (goal.md §6) | TODO | T11, merge ADR | — | Serialize concurrent merges |
| T16 | WS2 | Skip create_story when non-empty story .md exists | TODO | T05 | — | ~6–8 min on resume |
| T17 | WS2 | Cache toolchain/CLI detection (lru_cache, cwd-keyed) | TODO | T05 | — | Worktree-safe cache key |
| T18 | WS2 | Stale-state hygiene: stale *.tmp/state/sprint/parallel/queue/lock/checkpoints | TODO | T05 | — | NO MCP in this project; audit+extend loop/cleanup.py |
| T19 | WS2 | Drop/trim validation phases (DATA-GATED) | TODO | T05 | — | Cherry-pick diff logging first |
| T20 | WS3 | Prompt enhancement experiment (A/B variants, measured) | TODO | T05 | — | Evidence-score + finding-count metric |
| T21 | WS4 | Fold code-review→fix→architect-signoff into the loop (capped) | TODO | T05 | — | Reuse BMAD review logic |
| T22 | WS2 | Session reuse perf lever (GATED on T10 measured impact) | TODO | T10, continuity ADR | — | ~35–45% time if pursued |
| T23 | WS5 | OSS pattern-adoption items (emerge from T06 during P5) | DEFERRED | T06 | — | Concrete "adopt pattern X" items get added to the queue in P5; the OSS *research itself* is now T06 (Phase A) |
| T99 | — | Final: bmad-retrospective + final-report.md + verify §9 | TODO | all | — | Then emit completion promise |

## Work log (append-only; newest at bottom; read last 5–10 before acting)
- 2026-06-28 — Loop scaffold created by Winston (architect). Phase set to P0_SETUP. Queue seeded.
  Next action: P0 — confirm `.venv`, write a Claude-only test config, capture a baseline
  per-story timing measurement, then enter P1 and start T01.
- 2026-06-28 — RESEARCH-FIRST scoping: `stop_after_phase: P5_PLAN` set for run #1 (research +
  testing + plan only, NO production code). OSS research moved into P1 as T06 (deep, broad,
  any language) so it informs requirements + plan; old T23 deferred to "adopt patterns in P5".
```
