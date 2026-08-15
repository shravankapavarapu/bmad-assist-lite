# requirements/ — verified, testable requirements (Phase A output)

Produced in P2 from `../research/verified-findings.md`, then reviewed by the architect (P3)
and enhanced by party mode (P4). Each requirement must be:

- **Testable** — has an acceptance check expressible as a standalone verification script.
- **Traceable** — links back to the source finding and forward to its PROGRESS.md item.
- **Sized** — small/reversible where possible (Rule of Three before abstraction).

Suggested file split: `req-performance.md`, `req-merge-robustness.md`, `req-code-review.md`,
`req-prompts.md`, plus `targets.md` (the final, agreed success metrics).
