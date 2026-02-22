# Fix Quality Gate — Root Cause Analysis

## The Problem

3 out of 5 stories (8.2, 8.3, 8.4) failed quality gates even after the fix attempt.
The user reports that when they manually run Opus on the same failures, it fixes them in one go.
This points to an **instructions/context problem**, not a flow problem.

## Current fix-quality-gate Setup

### What the LLM Receives

**Part 1 — Compiled XML prompt (~649 tokens):**
```xml
<compiled-workflow>
  <mission>Execute fix-quality-gate workflow</mission>
  <context>  <!-- EMPTY — no project files --></context>
  <variables>
    epic_num, story_num, story_id, date, timestamp, etc.
  </variables>
  <file-index />  <!-- EMPTY -->
  <instructions>
    Step 1: Analyze Failure Report
    Step 2: Apply Minimal Fixes (lint/type/test/build)
    Step 3: Summary
  </instructions>
  <output-template />  <!-- EMPTY -->
</compiled-workflow>
```

**Part 2 — Appended failure report:**
```xml
<qa-failure-report>
# Quality Gate Failures — Story 8.2
## Failed: Typecheck
**Command:** `pnpm run typecheck`
**Exit Code:** 1
**Output:**
{full stderr/stdout}

## Failed: Build
**Command:** `pnpm run build`
...
</qa-failure-report>
```

### Critical Gaps Identified

1. **NO story file in context** — The LLM doesn't know what the story is about, what files were changed, or what the implementation looks like. It must infer everything from error messages alone.

2. **NO project files in context** — `workflow.yaml` has zero `input_file_patterns`. The `<context>` section is completely empty. The LLM gets no architecture docs, no project context, nothing.

3. **Minimal instructions** — Only 3 steps, very generic. No guidance on reading the story file first, no guidance on understanding the codebase structure.

4. **Only 1 retry** — `qa_retry_count` logic gives exactly one chance. If the LLM's fix introduces new errors or doesn't fully resolve the issue, the story is immediately marked blocked.

5. **Mission text is wrong** — The `<mission>` says "Execute fix-quality-gate workflow" (generic fallback from the compiler) instead of the actual mission from instructions.xml ("Fix quality gate failures for Epic X Story Y").

### Why Manual Opus Works But Automated Doesn't

When you run Opus manually (via Claude Code CLI), it has:
- Full conversation context from the terminal session
- Access to read any file via tool use
- Your verbal description of the problem
- Ability to ask clarifying questions
- No time pressure (no 600s timeout)

When fix_quality_gate runs automatically, the LLM has:
- A 649-token prompt with empty context
- Error output (which may reference files the LLM hasn't seen)
- No knowledge of what the story was trying to accomplish
- Must figure out file paths, imports, patterns from error messages alone
- 600s timeout

The LLM CAN use Claude Code's file tools (Read, Edit, Glob, Grep, Bash) because it runs through the Claude SDK which gives it those tools. But it starts from zero context about the project, which means it spends significant time just understanding the codebase before it can fix anything.

## Proposed Fix

### 1. Add story file to workflow context (HIGH IMPACT, simple)

In `workflows/fix-quality-gate/workflow.yaml`, add:
```yaml
input_file_patterns:
  - key: story_file
    glob: "{implementation_artifacts}/*{epic_num}*{story_num}*.md"
    load_mode: FULL_LOAD
```

This gives the LLM the story file with all tasks, acceptance criteria, and implementation details. ~2000-4000 tokens added but dramatically improves the LLM's understanding.

### 2. Improve instructions (HIGH IMPACT, simple)

Rewrite `instructions.xml` to:
```xml
<instructions>
  <mission>Fix quality gate failures for Epic {epic_num} Story {story_num}</mission>

  <context>
    Quality gate checks have failed. A failure report is attached below with
    the exact commands that failed, their exit codes, and full output.
    The story file is provided for context on what was implemented.
    Your job is to apply minimal, targeted fixes so the quality gates pass.
  </context>

  <steps>
    <step number="1" name="Understand Context">
      Read the story file to understand what was implemented and which files were changed.
      Read the qa-failure-report below to identify all failures.
    </step>

    <step number="2" name="Analyze Failures">
      For each failure:
      - Read the failing file(s) referenced in the error output
      - Identify the root cause (missing import, wrong type, test assertion, etc.)
      - Determine if failures are cascading (one fix may resolve multiple errors)
      Focus on typecheck and build errors first — test failures often resolve once types are correct.
    </step>

    <step number="3" name="Apply Targeted Fixes">
      Fix each failure:
      - Type errors: read the file, understand the expected types, fix annotations/casts
      - Build errors: resolve import paths, missing exports, module resolution
      - Test failures: read the test AND the source under test, fix the mismatch
      - Lint errors: apply the specific fix for each lint rule violation

      CRITICAL CONSTRAINTS:
      - Do NOT refactor unrelated code
      - Do NOT add new features or improvements
      - Do NOT change passing tests
      - Keep changes as small and focused as possible
    </step>

    <step number="4" name="Verify Fixes">
      After applying fixes, run the failing commands to verify they pass:
      - If typecheck failed: run the typecheck command
      - If build failed: run the build command
      - If tests failed: run the test command
      If a fix introduces new failures, address those too before finishing.
    </step>

    <step number="5" name="Summary">
      Provide a brief summary of:
      - Which quality gates were failing and root causes
      - What fixes were applied (file:line for each change)
      - Verification results
    </step>
  </steps>
</instructions>
```

### 3. Consider allowing 2 retries instead of 1

In `runner.py`, the retry logic is:
```python
if result.next_phase == Phase.FIX_QUALITY_GATE:
    state.qa_retry_count += 1
```

And in `quality_gate.py`:
```python
if state.qa_retry_count == 0:
    # First failure → route to fix
else:
    # Already retried → skip story
```

Change to allow 2 retries:
```python
if state.qa_retry_count < 2:
    # Route to fix
else:
    # Skip story
```

Or make it configurable:
```yaml
quality_gate:
  max_retries: 2  # default 1
```

### 4. Include the previous fix attempt output on retry

If we allow multiple retries, the second fix attempt should know what was already tried. Include the previous fix response in the prompt so the LLM doesn't repeat the same failed approach.

## Expected Impact

With the story file in context + improved instructions + verify step:
- The LLM starts with full understanding of what was implemented
- It can read referenced files immediately instead of guessing
- The verify step catches cascading failures before returning
- Estimated fix rate improvement: from ~20% (1/5) to ~60-80%

With 2 retries:
- Even if first fix doesn't fully resolve, second attempt with updated error output should catch remaining issues
- Estimated fix rate: ~80-90%
