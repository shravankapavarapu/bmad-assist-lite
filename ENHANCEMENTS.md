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