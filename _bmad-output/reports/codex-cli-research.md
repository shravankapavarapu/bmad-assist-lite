# Codex CLI Research — Code Review Provider for bmad-assist-lite

**Date:** 2026-05-29
**Branch:** feature/parallel-story-execution
**Decision:** Replace Gemini CLI with Codex CLI as multi-LLM code reviewer

## Why Replace Gemini?

Gemini CLI has persistent Windows pipe race conditions:
- `[Errno 22] Invalid argument` — pipe handle invalid after subprocess exit
- `[Errno 32] Broken pipe` — pipe write to dead process
- ~50% validator failure rate on parallel reviews (1/2 succeeded consistently)

## Candidates Evaluated

### CodeRabbit CLI — REJECTED
- **No native Windows support** (macOS, Linux, WSL only)
- **7-30 minute review times** — non-starter for parallel reviews
- Cloud-only analysis, free tier 3 reviews/hour
- Good structured output (`--agent` NDJSON) but blocked by platform + speed

### Codex CLI — SELECTED
- **Native Windows support** (experimental but functional)
- **Open-source** (Apache-2.0, Rust-based, 75K+ GitHub stars)
- **`codex exec`** non-interactive mode designed for automation
- **Structured output**: `--output-schema` (user-defined JSON Schema) + `--output-last-message` (file output)
- **Built-in code review**: `/review` command + review-via-prompt in exec mode
- **File access**: reads codebase directly, follows imports

## Model & Pricing (May 2026)

| Model | Input/1M | Output/1M | Cost/Review (30K in, 2K out) |
|-------|----------|-----------|------------------------------|
| gpt-5.3-codex | $1.75 | $14.00 | ~$0.08 |
| gpt-5.4-mini | $0.75 | $4.50 | ~$0.03 |
| gpt-5.4 | $2.50 | $15.00 | ~$0.105 |

10x cached input discount available. Full 7-story epic with 14 reviews ≈ $1.12 (codex) / $0.42 (mini).

### Cross-Provider Comparison

| Provider | Model | Cost/Review | Free? |
|----------|-------|-------------|-------|
| Gemini CLI | gemini-3.1-pro | $0.00 | Yes (AI Studio) |
| OpenAI | gpt-5.3-codex | ~$0.08 | No |
| OpenAI | gpt-5.4-mini | ~$0.03 | No |
| Anthropic | Claude Opus | ~$0.20 | No |

## CLI vs API Decision

**CLI chosen** because:
1. `--output-schema` is CLI-only — deterministic structured JSON output
2. CLI reads codebase directly (file access, import following)
3. Consistent with existing BaseProvider subprocess pattern
4. `--output-last-message` writes result to file — no pipe parsing needed
5. Code review quality is better when reviewer can browse files

## Known Bugs to Handle

| Issue | Impact | Workaround |
|-------|--------|------------|
| stdin hang on non-TTY pipe (#20919) | Subprocess hangs on Windows | `stdin=subprocess.DEVNULL` |
| `--json` ignored with MCP tools (#15451) | No JSONL streaming | Don't enable MCP |
| `--output-schema` intermediate messages (#19816) | Schema applied to wrong message | Use `--output-last-message` as authoritative |
| JSON schema field drift v0.44.0+ (#4776) | Fields may not match docs | Pin version or test empirically |

## Invocation Pattern

```bash
codex exec \
  --model gpt-5.3-codex \
  --output-schema review-schema.json \
  --output-last-message /tmp/review-{story_id}.json \
  "Review the recent changes for bugs, security issues, and code quality"
```

## Evidence Score Mapping

| Codex Priority | Evidence Score | Points |
|----------------|---------------|--------|
| P0 | CRITICAL | +3 |
| P1 | IMPORTANT | +1 |
| P2 | MINOR | +0.3 |
| P3 | MINOR | +0.3 |
