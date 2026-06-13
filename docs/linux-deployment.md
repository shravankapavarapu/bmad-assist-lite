# Linux Deployment Guide: bmad-assist-lite with Cursor Provider

Step-by-step guide for deploying bmad-assist-lite on a dedicated Linux box with Cursor CLI as the master provider (Composer 2.5). Follow sections in order; each section has a clear go/no-go outcome.

For architectural rationale behind these decisions, see [`architecture.md`](../_bmad-output/planning-artifacts/architecture.md) (Decisions D1-D14, Cursor Provider Extension section).

---

## 1. Environment Setup

### 1.1 Install Cursor CLI

The Cursor CLI installs to `~/.local/bin/`:

```bash
curl https://cursor.com/install -fsS | bash
```

> **Note:** The installer places the `agent` binary in `~/.local/bin/`. The binary may also be available as `cursor-agent` depending on the installer version.

### 1.2 Verify PATH

Ensure `~/.local/bin` is on your PATH. Verify the binary is found:

```bash
# Prefer command -v over which for POSIX portability
command -v cursor-agent || command -v agent
```

If neither resolves, add `~/.local/bin` to your PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Then re-verify:

```bash
command -v cursor-agent || command -v agent
```

### 1.3 Configure API Key

Place your `CURSOR_API_KEY` in the package-root `.env` file (same convention as other provider API keys like `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`):

```bash
# In the bmad-assist-lite repository root, open .env in your editor:
nano .env
# Add the line:  CURSOR_API_KEY=your-cursor-pro-api-key-here
```

> **Security note:** Avoid using `echo 'CURSOR_API_KEY=...' >> .env` — this stores the key in your shell history (`~/.bash_history`). Use an editor instead, or prefix with a space and `HISTCONTROL=ignorespace` in your shell config.

> **Note:** `agent login` is NOT required. API key authentication via the `CURSOR_API_KEY` environment variable is sufficient for headless invocations. The key is loaded automatically from `.env` by `python-dotenv` at config load time (see `core/config.py`).

### 1.4 Clone Repository and Set Up Python Environment

```bash
git clone https://github.com/your-org/bmad-assist-lite.git
cd bmad-assist-lite
```

> **Note:** Replace `your-org/bmad-assist-lite` with the actual GitHub org/repo path for your fork.

### 1.5 Install Python 3.11

Most Linux distributions do not ship Python 3.11 by default. Install it if `python3.11 --version` fails:

```bash
# Ubuntu/Debian (deadsnakes PPA):
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update && sudo apt install -y python3.11 python3.11-venv

# RHEL/Fedora:
sudo dnf install -y python3.11
```

Create and activate a Python 3.11 virtual environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Install the package with development dependencies:

```bash
pip install -e ".[dev]"
```

Verify the installation:

```bash
python -c "import bmad_assist_lite; print('OK')"
```

---

## 2. Spike Checklist

Run these spikes on the Linux box to validate assumptions before running real epics. **S5 must pass first** — it gates the entire feature premise.

### S5: Model Availability (Run First -- Gates Everything)

**What:** Confirm `composer-2.5` is visible and selectable on your Cursor Pro plan key.

> **Note:** All spike commands below use `agent` as the binary name. If your installation uses `cursor-agent` instead (check with `command -v cursor-agent`), substitute `cursor-agent` wherever you see `agent` in this section.

```bash
agent --list-models
```

**Expected outcome:** `composer-2.5` appears in the model list output.

**If S5 fails:** The feature premise is blocked. `composer-2.5` is a Cursor-exclusive model that requires a Pro plan key. Before proceeding:
1. Verify the `CURSOR_API_KEY` is correct and corresponds to a Pro plan
2. Check Cursor account settings for model access
3. If the model is genuinely unavailable on the key, the Cursor provider feature cannot proceed -- revisit the plan/key role before building further

**Gates:** Entire Cursor provider viability (all subsequent spikes and validation gates).

---

### S1: Stdin Prompt Delivery

**What:** Test whether stdin-piped prompts work with `-p` mode and NDJSON output.

```bash
echo "What is 2+2?" | agent -p --output-format stream-json --model composer-2.5
```

**Expected outcome:** An NDJSON stream containing a `{"type":"result"}` event with the answer.

**Feeds decision:** D1 (prompt delivery mechanism for >32K prompts). The current implementation passes prompts as argv arguments, which works on Linux (~2MB limit). If stdin works, it could be an alternative for very large prompts.

**If S1 fails:** Document the error output. If no NDJSON events appear:
- Try `--output-format text` as a fallback: `echo "What is 2+2?" | agent -p --output-format text --model composer-2.5`
- Note D1 implications: argv-based prompt delivery remains the primary mechanism
- Stdin delivery is a nice-to-have, not a requirement (argv handles >32K on Linux)

---

### S2: Read-Only Mode Composition

**What:** Determine if `--mode=ask` composes with `-p` for read-only invocation.

```bash
agent -p --mode=ask --output-format stream-json --model composer-2.5 "Summarize this file"
```

**Expected outcome:** Determine whether `--mode=ask` is accepted alongside `-p` and produces read-only behavior (no file writes, no shell execution).

**Feeds decision:** D3 (potential simplification of read-only enforcement beyond deny-config). If `--mode=ask` works with `-p`, it could simplify the read-only mechanism for multi-validator phases.

**If S2 fails:** `--mode=ask` may not compose with `-p`. Document the incompatibility and confirm that the deny-config mechanism (`.cursor/cli.json` with `{"permissions": {"deny": ["Write(**)", "Shell(**)"]}}`) remains the required read-only enforcement approach, which is already implemented in Story 11.4.

---

### S3: Context Window Size

**What:** Probe Composer 2.5's actual context window (conflicting claims: 256K vs 1M tokens).

**Prerequisite:** This spike uses stdin piping, which S1 tests. If S1 failed (stdin delivery broken), use an argv-based approach instead: `agent -p --output-format stream-json --model composer-2.5 "$(python3 -c "print('x ' * 100000)")"`.

**Test approach:** Feed progressively larger prompts and observe behavior:

```bash
# Generate a ~50K token prompt (~200KB of text: 'x ' * 100000 = 200,000 chars ≈ 50K tokens at ~4 chars/token)
python3 -c "print('x ' * 100000)" | agent -p --output-format stream-json --model composer-2.5

# If that works, try ~250K tokens (~1MB of text)
python3 -c "print('x ' * 500000)" | agent -p --output-format stream-json --model composer-2.5
```

**Expected outcome:** Determine the practical context limit. Note at what size the model starts failing or truncating.

**Feeds decision:** Context filtering budget decisions for `dev_story` prompts. The context filter in the compiler needs to know the effective limit to avoid exceeding it.

**If S3 is inconclusive:** Use the conservative 256K token budget for context filtering until the actual limit is confirmed. This is the safe default -- better to under-fill context than to exceed it and get truncation.

---

### S4: Auto-Update Behavior

**What:** Observe whether the Cursor CLI auto-updates during extended use.

```bash
# Record version before a run
agent --version

# After a multi-hour epic run, check again
agent --version
```

**Expected outcome:** Document whether the version changed during the run.

**Feeds decision:** Version pinning. The current approach accepts auto-update (no documented opt-out exists). If version drift is observed mid-run, evaluate whether pinning via direct tarball download is needed.

**If version drifts:** Document the observed version change (before/after). Evaluate:
- Did the version change cause any behavioral differences?
- Did NDJSON output format change?
- If drift causes issues, investigate tarball-based version pinning as a mitigation

---

## 3. Validation Gate Sequence

Run these gates in order. Each gate must pass before proceeding to the next. This sequence implements Decision D13: "Validate, don't rewrite."

### Gate 1: Full Test Suite

All existing tests must pass on the Linux box:

```bash
python -m pytest -v --tb=short
```

**Pass criteria:** Zero failures, zero errors.

**If Gate 1 fails:** Fix platform-specific test failures. Tests that mock Windows-only behavior may need `@pytest.mark.skipif(sys.platform == "win32", ...)` markers. Do not proceed to spikes until the test suite is green.

### Gate 2: Spikes S5 -> S1 -> S2 -> S3 -> S4

Run spikes in the order documented in Section 2. S5 is the go/no-go gate for everything else.

**Pass criteria:** S5 must pass. S1-S4 results are informational -- document outcomes even if they reveal limitations, as these feed architectural decisions rather than block deployment.

### Gate 3: One Complete Story Loop

Run one end-to-end story loop with `master: cursor` on a sample project:

```bash
# Configure cursor as master provider (see Section 4 for config example)
# Then run the full loop on a sample epic/story
bmad-assist-lite run
```

**Pass criteria (all must be met):**
- Story reaches "done" status
- All expected artifacts are generated (story file, implementation files)
- No unhandled errors in logs
- Provider does not fall back to a different model mid-run (check logs for the `"Cursor model mismatch"` warning)

**If Gate 3 fails:** Check logs for specific error messages (see Section 6: Troubleshooting). Common issues: timeouts on first run (increase `timeouts.dev_story`), binary resolution failures, model mismatch warnings.

### Gate 4: First Real Epic

After Gates 1-3 pass, run the first real epic on the Linux box. This is production validation on actual project work.

**Pass criteria:** Epic completes without requiring manual intervention for provider-related issues.

---

## 4. Configuration

### Example: Cursor as Master Provider

Create or update `bmad-assist-lite.yaml` in the project root:

```yaml
providers:
  master:
    provider: cursor
    model: composer-2.5
    # NOTE: The 'effort' key is NOT applicable to cursor/composer-2.5.
    # It only applies to Claude Opus 4.7. Omit it for cursor configs.
  multi:
    - provider: claude
      model: opus
      effort: max
    - provider: codex
      model: gpt-5.3-codex
  cli_paths:
    # Override if cursor-agent/agent is not on PATH:
    # cursor: "/home/user/.local/bin/agent"

timeouts:
  default: 300
  dev_story: 1200       # 20 minutes for implementation phases
  code_review: 900      # 15 minutes for code review
  code_review_synthesis: 900

loop:
  story:
    - create_story
    - validate_story
    - validate_story_synthesis
    - dev_story
    - code_review
    - code_review_synthesis
    - quality_gate
  epic_teardown:
    - epic_quality_gate
    - retrospective

quality_gate:
  lint: "ruff check src/"
  typecheck: "mypy src/"
  test: "pytest -q --tb=short --no-header"
  command_timeout: 180
```

### Configuration Notes

- **`providers.cli_paths.cursor`** -- Override the path to the cursor CLI binary if it is not found automatically. The provider resolves binaries in this order: config `providers.cli_paths.cursor` -> `shutil.which("cursor-agent")` -> `shutil.which("agent")` -> known platform install paths (`~/.local/bin`, `/usr/local/bin`). Within each tier, `cursor-agent` is tried before `agent` to avoid conflicts with other tools that may also install a generic `agent` binary.

- **`effort` key** -- Not applicable to cursor/composer-2.5. This key controls Claude Opus 4.7 thinking effort (`low|medium|high|xhigh|max`) and is ignored by all other providers. Do not include it in cursor provider configs.

- **Timeout recommendations** -- Cursor/Composer 2.5 processes at ~200 tok/s. For `dev_story` (the most token-heavy phase), 1200s (20 minutes) is a reasonable starting point. Adjust based on observed completion times during Gate 3 validation.

---

## 5. Architecture References

This deployment guide is deliberately operational (commands + checks). For the "why" behind these decisions:

| Topic | Reference |
|-------|-----------|
| Provider invocation flags | [architecture.md](../_bmad-output/planning-artifacts/architecture.md), Decision D1 |
| Read-only enforcement (deny-config) | [architecture.md](../_bmad-output/planning-artifacts/architecture.md), Decision D3 |
| Validate-don't-rewrite philosophy | [architecture.md](../_bmad-output/planning-artifacts/architecture.md), Decision D13 |
| Deployment documentation scope | [architecture.md](../_bmad-output/planning-artifacts/architecture.md), Decision D14 |
| Full requirements | [requirements-cursor-provider.md](../_bmad-output/planning-artifacts/requirements-cursor-provider.md) |

---

## 6. Troubleshooting

### Hung Processes / Hang Symptoms

Story 11.1 implemented SIGTERM->SIGKILL escalation for Unix process management. When a Cursor CLI subprocess hangs:

1. **SIGTERM** is sent to the process group (`killpg`)
2. The process is polled for up to **5 seconds** (`SIGTERM_GRACE_SECONDS = 5`)
3. If still alive after the grace period, **SIGKILL** is sent

This logic lives in `providers/_windows.py` (despite the filename, this module contains cross-platform process management logic including Unix signal handling -- the name is historical from the Windows-first development).

**Action:** If you observe hung processes despite this escalation, check:
- Whether the process was started in its own process group (`start_new_session=True` in `get_subprocess_kwargs()`)
- System-level zombie processes: `ps aux | grep agent`

### Cost-Guard Warnings

The cost guard detects when Cursor silently switches to a cheaper/more expensive model variant. Look for this log warning:

```
WARNING - Cursor model mismatch: requested composer-2.5, got composer-2.5-fast
```

This indicates the run used `composer-2.5-fast` instead of the requested `composer-2.5` -- a known Cursor bug (June 2026) where the fast variant costs **6x more** ($3/$15 per M tokens vs $0.50/$2.50).

**Action:** Check Cursor account/plan settings. Verify the API key has access to the full `composer-2.5` model. The warning is logged but the run continues (tokens already spent are sunk costs; visibility is the goal per Decision D6).

### Deny-Config Leftovers

When the Cursor provider runs in read-only mode (multi-validator phases), it creates a temporary `.cursor/cli.json` file with deny rules. If the process crashes mid-invocation, this file may be left behind.

**Automatic cleanup:** On the next run, the crash recovery sweep in `loop/cleanup.py` reads the marker file at `.bmad-assist-lite/cache/cursor-deny-config.marker`, validates the referenced path (must be absolute, must end with `cli.json`), and removes both the deny-config and the marker.

**Manual cleanup:** If automatic cleanup doesn't resolve the issue:

```bash
# 1. Check the marker file to see which path bmad-assist-lite created
cat .bmad-assist-lite/cache/cursor-deny-config.marker

# 2. Only remove the .cursor/cli.json if the marker confirms bmad-assist-lite created it
#    (the marker contains the absolute path that was written).
#    WARNING: Do NOT delete .cursor/cli.json if you created it manually for your own Cursor settings.
#    The provider only creates this file if it doesn't already exist at invocation time.
rm .cursor/cli.json                                    # only if bmad-created
rm .bmad-assist-lite/cache/cursor-deny-config.marker   # always safe to remove
```

### Binary Not Found

If the Cursor CLI binary cannot be resolved, you'll see:

```
ProviderError: Cursor CLI binary not found at resolved path: <path>.
Set providers.cli_paths.cursor in config to specify explicitly.
```

**Resolution:**
1. Verify the binary is installed: `command -v cursor-agent || command -v agent`
2. If installed but not on PATH, set the explicit path in config:
   ```yaml
   providers:
     cli_paths:
       cursor: "/home/user/.local/bin/agent"
   ```
3. If not installed, run the installer: `curl https://cursor.com/install -fsS | bash`

### Stream Ended Without Result Event

```
ProviderError: Cursor CLI stream ended without result event (exit code 0)
```

This is a known Cursor CLI quirk where the NDJSON stream closes without emitting the terminal `{"type":"result"}` event, even on exit code 0.

**Action:** Retry is usually sufficient. If persistent:
1. Check stderr output in debug logs for hints
2. Verify the model is available: `agent --list-models`
3. Try a simple test prompt to confirm CLI functionality:
   ```bash
   agent -p --output-format stream-json --model composer-2.5 "What is 2+2?"
   ```

### `--trust` Flag

The `--trust` flag is automatically included in all headless invocations by the Cursor provider (per Decision D1). This flag allows the CLI to run without interactive trust prompts.

**Important:**
- Do NOT add `--trust` manually when running CLI commands outside the provider harness for testing/debugging
- Be aware of its security implications: `--trust` allows the CLI to execute actions without confirmation prompts
- Within the bmad-assist-lite provider, this is safe because the provider controls the subprocess lifecycle, timeout enforcement, and cleanup
