# DECISIONS NEEDED — operator inbox

> The autonomous loop parks decisions here that it must NOT make itself: product/business
> calls, irreversible/cross-cutting architecture, and genuinely-close choices with lasting
> impact (see `../goal.md` §7). The loop keeps working other unblocked items meanwhile.
>
> This file is intentionally machine-appendable (stable entry schema + `status` field) so a
> future Discord/messaging notifier can watch it and a future responder can write answers
> back into the `Operator answer:` field. **Do not build that notifier in this job.**

## How to answer
Edit the `Operator answer:` line of an entry and set `status: ANSWERED`. The loop picks up
answers on its next iteration and unblocks the related queue item(s).

---

## Entry schema (copy for each new entry)
```
### D-NNNN — <short title>   [status: OPEN]
- Raised: <iteration / date>
- Blocks: <queue item id(s) in PROGRESS.md>
- Context: <why this came up>
- Options:
  - A. <option> — <trade-off>
  - B. <option> — <trade-off>
- Architect recommendation: <option + one-line why>
- Operator answer:
```

---

## Open decisions
_(none yet — the loop will append here)_
```
