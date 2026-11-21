"""Codex CLI subprocess-based provider implementation with Windows-safe process management.

Implements the BaseProvider Template Method contract:
- _do_invoke() feeds a ResultCollector during NDJSON stream parsing
- _cleanup() terminates the subprocess via kill_process()
- invoke() is inherited from BaseProvider (not overridden)
"""

import contextlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from subprocess import PIPE, Popen, TimeoutExpired
from typing import Any

from bmad_assist_lite.core.exceptions import (
    ProviderError,
    ProviderExitCodeError,
)
from bmad_assist_lite.providers._windows import get_subprocess_kwargs, kill_process
from bmad_assist_lite.providers.base import (
    BaseProvider,
    ExitStatus,
    ProviderResult,
    format_tag,
    is_hermetic,
    resolve_cli_path,
    write_progress,
)
from bmad_assist_lite.providers.result_collector import CallMetrics, ResultCollector
from bmad_assist_lite.validation.evidence_score import determine_verdict
from bmad_assist_lite.validation.findings import Finding, Severity, render_findings_block

logger = logging.getLogger(__name__)

STDERR_TRUNCATE_LENGTH: int = 200

# Priority-to-severity mapping for Evidence Score integration (Story 10.4)
# Maps Codex structured JSON priority strings to (severity_label, emoji, score) tuples.
_PRIORITY_TO_SEVERITY: dict[str, tuple[str, str, float]] = {
    "P0": ("CRITICAL", "\U0001f534", 3.0),
    "P1": ("IMPORTANT", "\U0001f7e0", 1.0),
    "P2": ("MINOR", "\U0001f7e1", 0.3),
    "P3": ("MINOR", "\U0001f7e1", 0.3),
}

# Priority-to-severity mapping for the structured findings block that the
# structured-review merge parses (validation.findings). Unknown priorities fall
# back to LOW, matching the MINOR fallback of the evidence table.
_PRIORITY_TO_FINDING_SEVERITY: dict[str, Severity] = {
    "P0": Severity.HIGH,
    "P1": Severity.MEDIUM,
    "P2": Severity.LOW,
    "P3": Severity.LOW,
}

# Number of clean review categories for clean pass formatting. Must match the
# nine categories the code-review template scores Claude lanes against (SOLID,
# Hidden Bugs, Abstraction, Tests, Performance, Tech Debt, Style, Type Safety,
# Security), so a clean Codex review lands on the same -4.5 as a clean Claude one.
_DEFAULT_CLEAN_PASS_COUNT: int = 9

# Path to the bundled JSON Schema for structured review output
_REVIEW_SCHEMA_PATH: Path = (
    Path(__file__).resolve().parent.parent / "workflows" / "schemas" / "codex-review-schema.json"
)


def _codex_write_network_enabled() -> bool:
    """Return ``providers.codex_write_network`` from the loaded config (False if unset)."""
    try:
        from bmad_assist_lite.core.config import get_config

        return bool(get_config().providers.codex_write_network)
    except Exception:  # config not loaded (unit tests, ad-hoc calls)
        return False


def _load_codex_review(json_str: str) -> tuple[list[dict[str, Any]], str] | None:
    """Parse *json_str* as Codex review JSON.

    Args:
        json_str: Raw JSON string (potentially from ``--output-last-message``).

    Returns:
        ``(findings, overall_verdict)`` with non-dict findings dropped, or
        ``None`` if the text is not JSON or lacks the review schema's
        ``findings`` / ``overall_verdict`` / ``summary`` top-level keys.

    """
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None
    if not all(k in data for k in ("findings", "overall_verdict", "summary")):
        return None

    findings = data.get("findings", [])
    if not isinstance(findings, list):
        return None

    overall_verdict = data.get("overall_verdict", "")
    if not isinstance(overall_verdict, str):
        overall_verdict = ""

    return [f for f in findings if isinstance(f, dict)], overall_verdict


def _text_field(finding: dict[str, Any], key: str) -> str:
    """Return a finding's text field, or an empty string if absent or not text."""
    value = finding.get(key)
    return value if isinstance(value, str) else ""


def _finding_location(finding: dict[str, Any]) -> tuple[str, int | None]:
    """Return ``(file_path, first_line)`` from a finding's ``code_location``.

    Both parts are optional in practice even though the schema requires them;
    an absent file is an empty string and an absent line is None.
    """
    code_location = finding.get("code_location")
    if not isinstance(code_location, dict):
        return "", None
    file_path = code_location.get("file_path")
    file_str = file_path if isinstance(file_path, str) else ""
    line: int | None = None
    line_range = code_location.get("line_range")
    if isinstance(line_range, list) and line_range:
        first = line_range[0]
        if isinstance(first, int) and not isinstance(first, bool):
            line = first
    return file_str, line


def _render_evidence_table(findings: list[dict[str, Any]], overall_verdict: str) -> str:
    """Render Codex review findings as an Evidence Score Summary table.

    The printed ``Evidence Score`` is the exact sum of the table rows, so a
    clean review prints a negative total, and the verdict row comes from the
    shared :func:`determine_verdict` thresholds.
    """
    lines: list[str] = [
        "## Evidence Score Summary",
        "",
        "| Severity | Description | Source | Score |",
        "|----------|-------------|--------|-------|",
    ]

    total_score: float = 0.0

    for finding in findings:
        title = _text_field(finding, "title").replace("|", "/").replace("\n", " ")
        body = _text_field(finding, "body").replace("|", "/").replace("\n", " ")
        priority = finding.get("priority", "")

        severity_info = _PRIORITY_TO_SEVERITY.get(priority if isinstance(priority, str) else "")
        if severity_info is None:
            logger.warning(
                "Unknown Codex priority %r, defaulting to MINOR",
                priority,
            )
            severity_info = ("MINOR", "\U0001f7e1", 0.3)

        severity_label, emoji, score = severity_info
        total_score += score

        # Extract source from code_location.file_path (optional)
        file_path, _ = _finding_location(finding)
        source = file_path.replace("|", "/") if file_path else "—"  # em-dash default

        description = f"{title}: {body}" if title and body else (title or body or "")
        lines.append(f"| {emoji} {severity_label} | {description} | {source} | +{score} |")

    # Clean pass deductions based on finding count and verdict
    if not findings and overall_verdict == "PASS":
        clean_score = _DEFAULT_CLEAN_PASS_COUNT * -0.5
        total_score += clean_score
        lines.append(
            f"| \U0001f7e2 CLEAN PASS "
            f"| {_DEFAULT_CLEAN_PASS_COUNT} categories | — | {clean_score} |"
        )
    elif findings:
        # Partial clean pass: deduct for categories not flagged
        flagged = len(findings)
        clean_categories = max(0, _DEFAULT_CLEAN_PASS_COUNT - flagged)
        if clean_categories > 0:
            clean_score = clean_categories * -0.5
            total_score += clean_score
            lines.append(
                f"| \U0001f7e2 CLEAN PASS | {clean_categories} categories | — | {clean_score} |"
            )

    # No clamp: a negative total is valid (it means clean) and must equal the
    # row sum, or the downstream parser flags a parsed-vs-calculated mismatch.
    # Rounded like calculate_evidence_score(); "+ 0.0" folds -0.0 into 0.0.
    total_score = round(total_score, 1) + 0.0
    verdict_label = determine_verdict(total_score).value

    lines.append("")
    lines.append(f"### Evidence Score: {total_score:.1f}")
    lines.append("")
    lines.append("| Score | Verdict |")
    lines.append("|-------|---------|")
    lines.append(f"| **{total_score:.1f}** | **{verdict_label}** |")

    return "\n".join(lines)


def _render_findings_block(findings: list[dict[str, Any]]) -> str:
    """Render Codex review findings as the structured-review findings block.

    The block is what ``loop.review_merge.parse_reviewer_findings`` requires
    from every reviewer lane; without it a single Codex lane forces the
    synthesis back to legacy prose merging. Built through
    :func:`render_findings_block` so the wire shape cannot drift from the
    parser. The schema has no symbol field, so the title is the anchor.
    """
    records: list[Finding] = []
    for finding in findings:
        title = _text_field(finding, "title")
        file_path, line = _finding_location(finding)
        priority = finding.get("priority", "")
        severity = _PRIORITY_TO_FINDING_SEVERITY.get(
            priority if isinstance(priority, str) else "", Severity.LOW
        )
        # The anchor is the finding's identity across passes; the title is the
        # most stable thing the schema offers (no symbol field exists).
        records.append(
            Finding(
                file=file_path,
                title=title,
                severity=severity,
                anchor=title,
                line=line,
                detail=_text_field(finding, "body"),
            )
        )
    return render_findings_block(records)


def _format_codex_json_as_evidence_text(json_str: str) -> str | None:
    """Convert Codex structured JSON output to evidence score text format.

    Attempts to parse *json_str* as JSON matching the Codex review schema
    (``findings``, ``overall_verdict``, ``summary`` top-level keys).  If the
    JSON is valid and matches the schema, returns a formatted text string
    containing an Evidence Score Summary table that the existing evidence
    score parser can recognise.

    Args:
        json_str: Raw JSON string (potentially from ``--output-last-message``).

    Returns:
        Formatted evidence text if *json_str* is valid Codex review JSON,
        or ``None`` if it is not valid JSON or does not match the schema.

    """
    review = _load_codex_review(json_str)
    if review is None:
        return None
    findings, overall_verdict = review
    return _render_evidence_table(findings, overall_verdict)


def _format_codex_review(json_str: str) -> str | None:
    """Convert Codex review JSON to the evidence table plus the findings block.

    Returns:
        The Evidence Score Summary table followed by the structured findings
        block, or ``None`` if *json_str* is not Codex review JSON.

    """
    review = _load_codex_review(json_str)
    if review is None:
        return None
    findings, overall_verdict = review
    table = _render_evidence_table(findings, overall_verdict)
    return f"{table}\n\n{_render_findings_block(findings)}"


# ============================================================================
# Usage telemetry
# ============================================================================


def _usage_int(value: object, key: str) -> int | None:
    """Return a usage count as a non-negative int, or None with a WARNING.

    None means "not supplied" and passes silently. Booleans, non-integers and
    negative values are coercion failures and are warned about, so a dropped
    metric is never silent.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        logger.warning(
            "Codex usage field %r is %r, not a token count; it is unavailable for this call.",
            key,
            value,
        )
        return None
    return value


def _extract_codex_usage(usage: object) -> CallMetrics | None:
    """Read token counts out of a Codex ``usage`` payload.

    Codex (like the OpenAI API) reports ``cached_input_tokens`` as a *subset*
    of ``input_tokens``. The project convention is that ``input_tokens`` is the
    uncached remainder and the full prompt is ``input + cache_read +
    cache_creation``, so the cached share is subtracted out here. Cache writes
    arrive as ``cache_write_input_tokens`` (codex-cli 0.166.0) and are recorded
    verbatim as ``cache_creation_tokens``; absent on older CLIs, they stay None.

    Args:
        usage: The ``usage`` value from an NDJSON event, of unknown shape.

    Returns:
        The metrics (fields None where unavailable), or None if the payload is
        not a dict and nothing should be recorded.

    """
    if not isinstance(usage, dict):
        logger.warning(
            "Codex usage payload is %s, not a dict; token metrics for this event are unavailable.",
            type(usage).__name__,
        )
        return None

    total_input = _usage_int(usage.get("input_tokens"), "input_tokens")
    cached = _usage_int(usage.get("cached_input_tokens"), "cached_input_tokens")
    output = _usage_int(usage.get("output_tokens"), "output_tokens")
    cache_write = _usage_int(usage.get("cache_write_input_tokens"), "cache_write_input_tokens")

    uncached_input = total_input
    if total_input is not None and cached is not None:
        if cached <= total_input:
            uncached_input = total_input - cached
        else:
            logger.warning(
                "Codex usage reports more cached tokens (%d) than input tokens (%d); "
                "the uncached input count is unavailable for this call.",
                cached,
                total_input,
            )
            uncached_input = None

    return CallMetrics(
        input_tokens=uncached_input,
        output_tokens=output,
        cache_read_tokens=cached,
        cache_creation_tokens=cache_write,
    )


def _extract_codex_usage_safe(usage: object) -> CallMetrics | None:
    """Call :func:`_extract_codex_usage`, degrading to None on any error.

    The NDJSON reader thread must never die on a telemetry payload: losing the
    token counts is acceptable, losing the response text is not.
    """
    try:
        return _extract_codex_usage(usage)
    except Exception as e:
        logger.warning("Failed to extract Codex usage tokens: %s", e)
        return None


def _extract_agent_message_text(item: dict[str, Any]) -> str:
    """Extract text from a Codex agent_message item, accepting both wire shapes.

    Current Codex CLIs (observed on codex-cli 0.166.0) put the message on a
    top-level ``text`` field::

        {"type": "agent_message", "text": "..."}

    Older CLIs used a ``content`` array of blocks::

        [{"type": "output_text", "text": "..."}, ...]

    The top-level field wins when it carries text; otherwise the block walk
    runs, so either CLI generation streams its response into the collector.
    (Before the top-level shape was handled, every agent message on a current
    CLI extracted as "" — stdout stayed empty for whole runs, masked only by
    the structured-output fallback on read-only lanes.)

    Args:
        item: The ``item`` dict from an ``item.completed`` NDJSON event.

    Returns:
        The message text, or empty string if neither shape carries any.

    """
    text = item.get("text")
    if isinstance(text, str) and text:
        return text
    content = item.get("content")
    if not content or not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "output_text":
            block_text = block.get("text", "")
            if block_text:
                parts.append(block_text)
    return "".join(parts)


#: Claude effort ladder -> Codex ``model_reasoning_effort`` levels. Codex has
#: no ``max`` level, so it caps at ``xhigh``.
_CODEX_EFFORT_MAP: dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "xhigh",
}


def _map_reasoning_effort(effort: str | None) -> str | None:
    """Map a config effort value to a Codex ``model_reasoning_effort`` level.

    Returns None for unset or unrecognised values, in which case no flag is
    passed and the Codex CLI default applies — a bad value degrades to the
    default instead of failing the lane.
    """
    if effort is None:
        return None
    level = _CODEX_EFFORT_MAP.get(effort.strip().lower())
    if level is None:
        logger.warning("Unrecognised effort %r for Codex; using the CLI default", effort)
    return level


class CodexProvider(BaseProvider):
    """Codex CLI subprocess-based provider with Windows-safe process management.

    Uses the BaseProvider Template Method: invoke() is inherited and drives the
    full lifecycle (create collector -> _do_invoke() -> handle timeout -> _cleanup()).
    This class only implements the hooks: _do_invoke(), _cleanup(), parse_output(),
    and supports_model().
    """

    def __init__(self) -> None:
        """Initialize provider with process and thread tracking for cleanup."""
        super().__init__()
        self._current_process: Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._temp_output_path: Path | None = None
        self._structured_output: str | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider identifier string."""
        return "codex"

    @property
    def default_model(self) -> str | None:
        """Return the default model identifier."""
        return "gpt-5.3-codex"

    def supports_model(self, model: str) -> bool:
        """Return True if the model is a known Codex/OpenAI model."""
        return model.startswith(("gpt-", "codex-", "o1-", "o3-", "o4-"))

    def parse_output(self, result: ProviderResult) -> str:
        """Extract response text from provider result.

        When cached structured JSON is available (from ``_do_invoke()``),
        attempts to convert Codex review JSON via ``_format_codex_review()``.
        If the JSON matches the review schema, returns the Evidence Score
        Summary table followed by the structured findings block.  If
        conversion returns ``None`` (not review JSON), falls back to the
        raw cached JSON string.  Falls back to ``result.stdout.strip()``
        if no structured output was captured at all.
        """
        if self._structured_output is not None:
            formatted = _format_codex_review(self._structured_output)
            if formatted is not None:
                logger.debug(
                    "parse_output: converted structured JSON to evidence text format",
                )
                return formatted
            logger.debug(
                "parse_output: structured JSON is not review schema, returning raw JSON",
            )
            return self._structured_output

        logger.debug("parse_output: using stdout text fallback")
        return result.stdout.strip()

    def _do_invoke(
        self,
        prompt: str,
        *,
        collector: ResultCollector,
        model: str | None = None,
        timeout: int = 300,
        settings_file: Path | None = None,
        cwd: Path | None = None,
        allowed_tools: list[str] | None = None,
        effort: str | None = None,
        color_index: int | None = None,
        system_prompt: str | None = None,
        resume: str | None = None,
        stream_capture_path: Path | None = None,
    ) -> ProviderResult:
        """Execute Codex CLI with NDJSON streaming and collector integration.

        Resolves model, spawns subprocess with ``codex exec --json``, parses
        NDJSON events from stdout via reader threads, and feeds collector.
        Raises TimeoutError on subprocess timeout for base class grace period
        handling.

        Args:
            prompt: The prompt text to send to the provider.
            collector: ResultCollector to accumulate streaming chunks into.
            model: Model identifier, or None for provider default.
            timeout: Timeout in seconds (always an int, resolved by invoke()).
            settings_file: Optional path to provider settings file (unused).
            cwd: Working directory for the provider process.
            allowed_tools: List of tool names the provider may use.
            effort: Reasoning-effort hint, forwarded to the CLI as a
                ``model_reasoning_effort`` config override (``max`` maps to
                ``xhigh``; unrecognised values fall back to the CLI default).
            color_index: Index for ANSI color differentiation in output.

        Returns:
            ProviderResult with timed_out=False on successful completion.

        Raises:
            TimeoutError: When subprocess.wait() times out (handled by base class).
            ProviderError: On CLI not found or FileNotFoundError.
            ProviderExitCodeError: On non-zero CLI exit code.
            ValueError: When timeout <= 0.

        """
        _ = settings_file  # Codex CLI has no settings file concept

        # Drop anything a previous call on this instance left behind, so a
        # write-mode call never returns an earlier reviewer call's JSON.
        self._structured_output = None
        self._temp_output_path = None

        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")

        reasoning_effort = _map_reasoning_effort(effort)

        if is_hermetic():
            logger.debug("Codex ignores hermetic=True (Claude-only mechanism)")

        effective_model = model or self.default_model or "codex-mini-latest"

        # Build tool restriction prompt if needed
        final_prompt = prompt
        if allowed_tools is not None:
            # Codex has no Read/Glob/Grep tools: shell commands ARE how it
            # reads files. The old Claude-vocabulary warning ("FORBIDDEN:
            # Bash") made an obedient Codex decline to open ANY file, so
            # reviewers judged only the inlined diff. Enforcement is the
            # OS-level read-only sandbox below (blocks writes and network);
            # the prompt's job is to authorize read-only inspection inside it
            # and state the intent the sandbox enforces.
            restriction_warning = (
                "\n\n**CRITICAL - READ-ONLY REVIEW:**\n"
                "You are a CODE REVIEWER inside an OS-enforced READ-ONLY "
                "sandbox.\n"
                "- You MAY run read-only shell commands to read and search "
                "files (cat, head, ls, rg, git diff/log/show).\n"
                "- You MUST NOT modify any files, run builds, tests or "
                "installs, or access the network. The sandbox blocks writes "
                "and network; do not attempt them.\n"
            )
            final_prompt = prompt + restriction_warning

        codex_bin = resolve_cli_path("codex")
        logger.debug("Codex CLI resolved to: %s", codex_bin)

        command: list[str] = [
            codex_bin,
            "exec",
            "--json",
            "--model",
            effective_model,
        ]

        # Forward reasoning effort as a config override. The value is quoted
        # because -c parses its value as TOML (a bare word is not a TOML string).
        if reasoning_effort is not None:
            command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])

        # Sandbox enforcement (OS-level, not prompt-level). Codex's tool
        # restriction prompt is advisory only — the model can ignore it — so we
        # also constrain the CLI sandbox that governs model-generated shell
        # commands. Read-only phases (validators/reviewers) get a read-only
        # sandbox that blocks BOTH workspace writes and network access, which
        # prevents an unintended ``git fetch``/clone from reaching the worktree.
        # Write phases are confined to ``workspace-write`` (no network, no escape
        # outside the working root). See providers.base.READ_ONLY_TOOLS.
        sandbox_mode = "read-only" if allowed_tools is not None else "workspace-write"
        command.extend(["--sandbox", sandbox_mode])
        if sandbox_mode == "workspace-write" and _codex_write_network_enabled():
            command.extend(["-c", "sandbox_workspace_write.network_access=true"])

        # Add --output-schema and --output-last-message for structured output,
        # but only on read-only (reviewer/validator) lanes. Write and master
        # phases must stay free-form: the forced review schema would stop them
        # emitting anything else, such as the AC-audit verdict table.
        if allowed_tools is not None:
            if _REVIEW_SCHEMA_PATH.is_file():
                temp_dir = (cwd or Path.cwd()) / ".bmad-assist-lite" / "cache"
                temp_dir.mkdir(parents=True, exist_ok=True)
                temp_output = temp_dir / f"codex-review-{uuid.uuid4().hex[:8]}.json"
                self._temp_output_path = temp_output
                command.extend(
                    [
                        "--output-schema",
                        str(_REVIEW_SCHEMA_PATH),
                        "--output-last-message",
                        str(temp_output),
                    ]
                )
                logger.debug(
                    "Structured output: schema=%s, output=%s",
                    _REVIEW_SCHEMA_PATH,
                    temp_output,
                )
            else:
                logger.warning(
                    "Review schema not found at %s, proceeding without "
                    "--output-schema (structured output disabled)",
                    _REVIEW_SCHEMA_PATH,
                )

        response_text_parts: list[str] = []
        stderr_chunks: list[str] = []
        start_time = time.monotonic()

        env = os.environ.copy()
        if cwd is not None:
            # Only set PWD; the subprocess cwd is passed to Popen(cwd=...) below,
            # which is what Codex uses to resolve the repo.
            #
            # DO NOT set GIT_WORK_TREE / GIT_DIR here. Those env vars are global to
            # every git invocation Codex makes, including its OWN internal plumbing
            # (notably the plugin "curated-sync", which runs `git reset --hard` +
            # `git clean -fdx`). Pointing GIT_WORK_TREE at the worktree redirects
            # that `git clean -fdx` onto the worktree and DELETES its untracked /
            # ignored files (the .bmad-assist-lite/cache output dir, _bmad-output,
            # potentially .env). GIT_DIR was also wrong for a worktree anyway, where
            # `.git` is a pointer file, not a directory. Codex reads the repo fine
            # from cwd, so these overrides are both unnecessary and destructive.
            env["PWD"] = str(cwd)

        try:
            popen_kwargs = get_subprocess_kwargs()
            process = Popen(
                command,
                stdin=PIPE,
                stdout=PIPE,
                stderr=PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                env=env,
                **popen_kwargs,
            )
            if process.stdin:
                process.stdin.write(final_prompt)
                process.stdin.close()
        except FileNotFoundError as e:
            raise ProviderError(
                f"Codex CLI binary not found at resolved path: {codex_bin}. "
                "Set providers.cli_paths.codex in config to specify explicitly."
            ) from e

        self._current_process = process

        def process_ndjson_stream(
            stream: Any,
            text_parts: list[str],
            color_idx: int | None,
            result_collector: ResultCollector,
        ) -> None:
            """Read NDJSON events from stdout, extracting agent messages and usage."""
            for line in iter(stream.readline, ""):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    msg = json.loads(stripped)
                    if not isinstance(msg, dict):
                        continue
                    msg_type = msg.get("type", "")

                    # Token usage normally rides on "turn.completed", but any
                    # event with a top-level "usage" is accepted. The last
                    # usable envelope wins; a malformed one records nothing.
                    if "usage" in msg:
                        metrics = _extract_codex_usage_safe(msg["usage"])
                        if metrics is not None:
                            result_collector.record_metrics(metrics)

                    if msg_type == "item.completed":
                        item = msg.get("item", {})
                        if not isinstance(item, dict):
                            continue
                        item_type = item.get("type", "")

                        if item_type == "agent_message":
                            text = _extract_agent_message_text(item)
                            if text:
                                text_parts.append(text)
                                result_collector.add(text)
                                if logger.isEnabledFor(logging.INFO):
                                    preview = text[:200] + ("..." if len(text) > 200 else "")
                                    tag = format_tag("ASSISTANT", color_idx)
                                    write_progress(f"{tag} {preview}")

                        elif item_type == "command_execution":
                            if logger.isEnabledFor(logging.INFO):
                                cmd_details = item.get("command", "")
                                if not cmd_details:
                                    cmd_details = item.get("input", "")
                                tag = format_tag("TOOL Codex", color_idx)
                                write_progress(f"{tag} {cmd_details}")

                except json.JSONDecodeError:
                    pass
            stream.close()

        def read_stderr(stream: Any, chunks: list[str]) -> None:
            """Read stderr lines and accumulate."""
            for line in iter(stream.readline, ""):
                chunks.append(line)
            stream.close()

        stdout_thread = threading.Thread(
            target=process_ndjson_stream,
            args=(
                process.stdout,
                response_text_parts,
                color_index,
                collector,
            ),
        )
        stderr_thread = threading.Thread(
            target=read_stderr,
            args=(process.stderr, stderr_chunks),
        )

        self._stdout_thread = stdout_thread
        self._stderr_thread = stderr_thread

        stdout_thread.start()
        stderr_thread.start()

        try:
            returncode = process.wait(timeout=timeout)
        except TimeoutExpired:
            raise TimeoutError(f"Codex CLI timeout after {timeout}s") from None

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

        duration_ms = int((time.monotonic() - start_time) * 1000)
        stderr_content = "".join(stderr_chunks)

        if returncode != 0:
            exit_status = ExitStatus.from_code(returncode)
            if not stderr_content:
                stderr_truncated = "(empty)"
            elif len(stderr_content) <= STDERR_TRUNCATE_LENGTH:
                stderr_truncated = stderr_content.strip()
            else:
                stderr_truncated = "..." + stderr_content[-STDERR_TRUNCATE_LENGTH:].strip()
            message = f"Codex CLI failed with exit code {returncode}: {stderr_truncated}"
            raise ProviderExitCodeError(
                message,
                exit_code=returncode,
                exit_status=exit_status,
                stderr=stderr_content,
                command=tuple(command),
            )

        response_text = "".join(response_text_parts)

        # Read structured output file before _cleanup() deletes it
        if self._temp_output_path is not None:
            try:
                content = self._temp_output_path.read_text(encoding="utf-8")
                if content.strip():
                    # Validate it is parseable JSON before caching
                    json.loads(content)
                    self._structured_output = content.strip()
                    logger.debug(
                        "Cached structured JSON output from %s",
                        self._temp_output_path,
                    )
                else:
                    logger.debug(
                        "Structured output file is empty, will use stdout fallback",
                    )
            except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Failed to read structured output from %s (%s), will use stdout fallback",
                    self._temp_output_path,
                    exc,
                )

        # None here means the stream carried no usage envelope: unavailable,
        # not zero.
        metrics = collector.metrics or CallMetrics()

        logger.info(
            "Codex CLI completed: duration=%dms, exit_code=%d, text_len=%d, "
            "input_tokens=%s, cached_input_tokens=%s, output_tokens=%s",
            duration_ms,
            returncode,
            len(response_text),
            metrics.input_tokens,
            metrics.cache_read_tokens,
            metrics.output_tokens,
        )

        return ProviderResult(
            stdout=response_text,
            stderr=stderr_content,
            exit_code=returncode,
            duration_ms=duration_ms,
            model=effective_model,
            command=tuple(command),
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            cache_read_tokens=metrics.cache_read_tokens,
            cache_creation_tokens=metrics.cache_creation_tokens,
        )

    def _cleanup(self) -> None:
        """Terminate subprocess, join reader threads, and remove temp files.

        Called by the base class invoke() in a finally block -- guaranteed to run
        on success, timeout, and unexpected exceptions.

        If the subprocess is still running (poll() returns None), calls
        kill_process() for platform-safe termination. Joins reader threads
        with a short timeout to prevent thread leaks. Removes the temp output
        file created for ``--output-last-message`` if it exists.
        """
        process = self._current_process
        stdout_thread = self._stdout_thread
        stderr_thread = self._stderr_thread
        temp_output = self._temp_output_path

        # Reset state first to prevent re-entry
        self._current_process = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._temp_output_path = None
        # Note: _structured_output is intentionally NOT reset here.
        # It must survive _cleanup() so parse_output() can read it afterward.

        # Kill process if still running
        if process is not None and process.poll() is None:
            logger.warning("Killing Codex subprocess (still running at cleanup)")
            kill_process(process)
            # Wait for process to be fully reaped so pipe handles close on Windows,
            # allowing reader threads to unblock from readline().
            with contextlib.suppress(Exception):
                process.wait(timeout=3)

        # Join threads to prevent leaks
        if stdout_thread is not None:
            stdout_thread.join(timeout=1)
        if stderr_thread is not None:
            stderr_thread.join(timeout=1)

        # Clean up temp output file (may be locked by antivirus on Windows)
        if temp_output is not None:
            with contextlib.suppress(OSError):
                temp_output.unlink(missing_ok=True)
