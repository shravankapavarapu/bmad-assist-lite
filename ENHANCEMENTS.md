# Enhancements Backlog

Tracked future enhancements for bmad-assist-lite.

## Story Ordering via `recommended_order`

**Priority:** Low (current insertion-order approach works for most cases)

### Context

Epic entries in `sprint-status.yaml` can include a `recommended_order` field that explicitly defines story processing sequence:

```yaml
epic-6:
  title: "Testing Foundation"
  status: in-progress
  recommended_order:
    - "6-1-blog-data-layer-unit-tests"
    - "6-2-blog-ui-component-unit-tests"
    - "6-3-blog-article-component-unit-tests"
    - "6-4-contact-form-unit-tests"
    - "6-5-blog-e2e-tests"
    - "6-6-contact-e2e-tests"
```

Currently, stories are processed in **sprint-status.yaml insertion order** (dict key order), which works because users already arrange stories in the intended sequence. However, if `recommended_order` is present, it should take precedence.

### Proposed Behavior

1. After `find_backlog_stories()` groups stories by epic, check if the epic entry has a `recommended_order` list
2. If present, sort the epic's stories to match `recommended_order` (match by key prefix)
3. Stories not in `recommended_order` are appended at the end
4. If no `recommended_order`, fall back to current insertion order

### Where to Implement

- `SprintStatus` — Add `get_recommended_order(epic_id) -> list[str] | None`
- `cli.py` — After building `stories_for_epic`, sort each epic's stories using recommended order
- Alternatively, `find_backlog_stories()` could accept an optional ordering hint

### Notes

- Several completed epics in the webdozo project already have `recommended_order` (epic-swvc, epic-hp-rebrand, epic-blog-tc, etc.)
- This also pairs well with dependency-based gating (skip stories whose dependencies aren't done yet)

### Things to be worked next
- When there is no context7 libraries needed, template need to specifically mention that and code needs to skip context7 load completely.
- Also check if there is db involved during start up and see if it's reachable. If not reachable exit script saying it can't move forward without db.
- Multi thread implementation : Now for each story need to create separate worktree and this should run sperately on it's on. 
- There should be a orchestrator to handle to over see this project. 
- Need config to limit how many threads can be run at time.
- Orchestrator need to figure out if there are any dependencies before launching each thread, if there is then it should wait untill that dependencies are met and then only 
- Need to research for this see if there is already implemented in actual process, check orchestrator tweets and also auto claude for archetural direction - use BMAD archiect

### For TDD
| #   | Command                          |
| --- | -------------------------------- |
| 1   | `/bmad-bmm-create-story`         |
| 2   | `/bmad-tea-testarch-atdd`        |
| 3   | `/bmad-bmm-dev-story`            |
| 4   | `/bmad-tea-testarch-automate`    |
| 5   | `/bmad-tea-testarch-test-review` |
| 6   | `/bmad-bmm-code-review`          |
| 7   | `/bmad-tea-testarch-trace`       |

### Plan for epic
| #   | Command                          |
| --- | -------------------------------- |
| 1   | `/bmad-tea-testarch-test-review` |
| 2   | `/bmad-tea-testarch-nfr-assess`  |
| 3   | `/bmad-tea-testarch-trace`       |

## Resilient failure handling: don't discard completed work on a transient failure

**Priority:** High

### Context

Observed live while supervising webdozo-studio epic 13 on 2026-09-17 (a 15-story
parallel run at `max_concurrency: 1`). Two kinds of expensive waste showed up, both
where the failure was transient or environmental rather than a real code defect:

1. **A whole parallel wave was discarded on a false-negative gate.** Stories 13.5,
   13.9 and 13.15 built cleanly and merged, but the post-merge quality gate failed
   typecheck and build for all three. The cause was not the code: a stale
   `apps/studio/.next/dev/types/validator.ts` in the MAIN repo (left by an earlier
   `pnpm build`) referenced routes one of the stories deleted, so `tsc` failed on the
   generated file. The orchestrator rolled the integration head back and marked the
   three blocked. The completed `parallel/13-*` branches survived, but a naive resume
   would have rebuilt all three from scratch (re-running create-story/dev). The
   supervisor recovered only by merging the surviving stacked branch by hand.

2. **In-flight reviews dropped on a usage limit.** Two review-phase invocations died
   mid-run on a rate-limit (HTTP 429, subscription session limit), losing all their
   work, and had to be re-run after the window reset.

The common thread: the orchestrator treats a transient/environmental failure the same
as a real code defect — it discards the worktree and blocks the story — so a
recoverable stumble costs a full rebuild.

### Proposed behavior

1. **Classify the failure before discarding.** The gate runner already tags results
   (`classification=real`). Extend that so the merge/worktree lifecycle distinguishes a
   *transient/environmental* failure (a usage 429; a gate that fails only in the
   accumulating main repo, e.g. a stale `.next`) from a *real code defect* that survives
   the fix loop. Only the latter should block-and-discard.

2. **Run the post-merge quality gate in a clean state.** The false-negative happened
   because the re-gate ran in the main repo where the stale `.next` lived. Build the
   post-merge check against a clean checkout, or clean framework build artifacts
   (`.next`, etc.) before it runs, so an accumulating artifact from a prior build can
   never false-fail a route-changing merge.

3. **Reuse the surviving branch on resume for BLOCKED stories, not just parked ones.**
   `retry_parked_on_resume` already re-enters the merge ladder for *parked* merges whose
   branch still exists. Extend the same idea to *blocked* stories whose branch survives:
   on resume, re-attempt the merge + post-merge gate on the completed branch (now that
   the environment is fixed) rather than re-running create-story/dev from scratch. The
   branches already survive today; the orchestrator just does not reuse them.

### Notes

- The webdozo-studio runner skill now works around #2 by removing `apps/studio/.next`
  before each resume; that is a band-aid the tool should not need.
- The session that surfaced this is inspectable: transcript
  `~/.claude/projects/-home-ksk-webdozo-webdozo-studio/fb9570a6-5a84-46f0-966f-ce66039bb1c7.jsonl`
  (large — grep, don't cat), and the supervision journal at
  `<webdozo-studio>/.bmad-assist-lite/cache/runner-journal-epic-13.md` (the compact
  narrative; look for "false-negative", "stale-.next", and the wave that blocked
  13.5/13.9/13.15).