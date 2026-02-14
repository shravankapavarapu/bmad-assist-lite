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
