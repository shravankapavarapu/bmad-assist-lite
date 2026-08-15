"""Shared quality-gate runner — the single gate-execution path in the tool.

Every quality-gate command executed by ``loop/handlers/quality_gate.py``,
``loop/handlers/epic_quality_gate.py`` and ``parallel/merger.py`` runs through
:func:`run_gates`. A second gate path would mean the post-merge re-gate proves
something different from what the per-story gate proved.

Three behaviours live here that the three former loops each lacked:

* independent gate commands run concurrently inside a **bounded** pool, while
  commands declared tree-mutating are serialised ("fan out reads, serialize writes");
* every result carries an env-vs-real **classification** derived from the shared
  :class:`~bmad_assist_lite.providers.base.ExitStatus` vocabulary, so an environment
  failure is never reported as a code failure;
* a base/merge-target repository can be bootstrapped through the existing
  ``parallel/bootstrap.py`` pipeline before its gates run, and once — and only once —
  after an ``env`` failure.

A classification never suppresses a failure. It downgrades *routing*, never
*visibility*: a failing gate is FAIL under every classification, and the console
summary names the gate, its classification, and the command that failed.
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from bmad_assist_lite.core.command_runner import (
    COMMAND_TIMEOUT_EXIT_CODE,
    CommandResult,
    run_command,
)
from bmad_assist_lite.core.exceptions import BmadAssistError
from bmad_assist_lite.providers.base import ExitStatus, write_progress

if TYPE_CHECKING:
    from bmad_assist_lite.core.config import Config
    from bmad_assist_lite.parallel.bootstrap import BootstrapResult

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

DEFAULT_GATE_POOL_SIZE: int = 4
"""Upper bound on concurrent gate commands.

Four is the canonical gate set (lint, typecheck, build, test), so the standard wave
is fully parallel while a longer command list still fans out into a *bounded* pool
rather than one thread per command.
"""

DEFAULT_COMMAND_TIMEOUT: int = 120

GATE_DURATION_LOG_PREFIX: str = "[GATE-DURATION]"
"""Stable prefix for the per-command duration log line.

The line's shape is :data:`GATE_DURATION_LOG_FORMAT`. Changing either is a
format-drift event for any harness that parses it.
"""

GATE_DURATION_LOG_FORMAT: str = (
    "%s label=%s gate=%s duration_ms=%d exit_code=%d classification=%s"
)

UNCLASSIFIED_FAILURE_CODES: frozenset[int] = frozenset(
    {1, COMMAND_TIMEOUT_EXIT_CODE}
)
"""Exit codes that mean "the tool ran and said no" and need no WARNING.

Any *other* code that lands in :attr:`ExitStatus.ERROR` is unmapped: it is classified
``real`` (the conservative direction) and logged at WARNING with the code, rather than
falling through to ``env``.
"""

_ENV_STATUSES: frozenset[ExitStatus] = frozenset(
    {ExitStatus.NOT_FOUND, ExitStatus.CANNOT_EXECUTE}
)

ENV_BLOCKED_LABEL: str = "env-blocked"
"""Terminal state name for an ``env`` failure that survived its one bootstrap retry."""


class GateRunnerError(BmadAssistError):
    """A gate-runner invariant was violated."""


# ============================================================================
# Classification
# ============================================================================


class GateClassification(StrEnum):
    """Closed set of gate failure classifications.

    Exactly two members in v1. ``flaky`` and ``test_maintenance`` are deliberately
    absent: a bucket with no detector behind it degrades silently.
    """

    REAL = "real"
    ENV = "env"


def classify_exit_code(exit_code: int, platform: str | None = None) -> GateClassification:
    """Classify a gate command's exit code as an environment or a real failure.

    Delegates to :meth:`ExitStatus.from_code` — the tool's only exit-code
    vocabulary — and maps its semantic statuses onto the two gate buckets. No
    exit-code comparison and no stderr text parsing happens here.

    Args:
        exit_code: Raw process exit code.
        platform: Platform string to classify for; defaults to ``sys.platform``.

    Returns:
        ``ENV`` when the command could not be executed, ``REAL`` otherwise.

    """
    status = ExitStatus.from_code(exit_code, platform=platform)
    if status in _ENV_STATUSES:
        return GateClassification.ENV
    if status is ExitStatus.ERROR and exit_code not in UNCLASSIFIED_FAILURE_CODES:
        logger.warning(
            "Gate command exited with unmapped code %d — classifying as %s "
            "(conservative direction; unmapped codes never fall through to %s)",
            exit_code,
            GateClassification.REAL.value,
            GateClassification.ENV.value,
        )
    return GateClassification.REAL


# ============================================================================
# Models
# ============================================================================


class GateCommand(BaseModel):
    """A single named gate command to execute."""

    model_config = ConfigDict(frozen=True)

    name: str
    command: str
    serial: bool = Field(
        default=False,
        description="True when the command mutates the working tree and must not "
        "overlap any other gate command.",
    )


class GateOutcome(BaseModel):
    """Immutable result of one gate command."""

    model_config = ConfigDict(frozen=True)

    name: str
    command: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    classification: GateClassification
    retried_after_bootstrap: bool = False

    @property
    def status_label(self) -> str:
        """Return ``PASS`` or ``FAIL`` — never ``unknown``."""
        return "PASS" if self.passed else "FAIL"


class GateRunResult(BaseModel):
    """Immutable result of a whole gate run."""

    model_config = ConfigDict(frozen=True)

    all_passed: bool
    outcomes: list[GateOutcome]
    duration_ms: int
    pool_size: int
    bootstrap_attempted: bool = False
    bootstrap_failed: bool = False
    env_blocked: bool = False

    @property
    def failures(self) -> list[GateOutcome]:
        """Return the failing outcomes, in declared command order."""
        return [o for o in self.outcomes if not o.passed]

    @property
    def overall_classification(self) -> GateClassification | None:
        """Classify the run as a whole, ``real`` taking precedence over ``env``.

        A mixed run (one ``env`` failure and one ``real`` failure) is ``real``: the
        conservative direction, because a real code failure must still reach the fixer.
        Returns ``None`` when nothing failed.
        """
        failures = self.failures
        if not failures:
            return None
        if any(o.classification is GateClassification.REAL for o in failures):
            return GateClassification.REAL
        return GateClassification.ENV

    def classification_label(self, outcome: GateOutcome) -> str:
        """Return the operator-facing classification label for one outcome."""
        if self.env_blocked and outcome.classification is GateClassification.ENV:
            return ENV_BLOCKED_LABEL
        return outcome.classification.value


# ============================================================================
# Tree-mutation guard
# ============================================================================

_TREE_LOCK = threading.RLock()
_ACTIVE_LOCK = threading.Lock()
_active_gate_commands: int = 0


def active_gate_commands() -> int:
    """Return how many gate commands are executing right now, process-wide."""
    with _ACTIVE_LOCK:
        return _active_gate_commands


def assert_tree_quiescent(what: str) -> None:
    """Raise unless no gate command is currently executing.

    Bootstrap-and-retry mutates the working tree but is *not* a gate command, so the
    ``serial`` flag does not cover it. This is the runtime invariant that keeps it
    from firing while a gate command is still running.

    Args:
        what: Human-readable name of the tree-mutating operation.

    Raises:
        GateRunnerError: If any gate command is still in flight.

    """
    active = active_gate_commands()
    if active:
        raise GateRunnerError(
            f"{what} is tree-mutating and may not run while {active} gate "
            "command(s) are still executing"
        )


class _ActiveCommand:
    """Context manager tracking in-flight gate commands for the tree guard."""

    def __enter__(self) -> "_ActiveCommand":
        global _active_gate_commands
        with _ACTIVE_LOCK:
            _active_gate_commands += 1
        return self

    def __exit__(self, *exc: object) -> None:
        global _active_gate_commands
        with _ACTIVE_LOCK:
            _active_gate_commands -= 1


# ============================================================================
# Base-repo bootstrap
# ============================================================================

BootstrapHook = Callable[[bool], "BootstrapResult"]
"""Callable taking ``force`` and returning the existing frozen ``BootstrapResult``."""

_BOOTSTRAP_STATE_LOCK = threading.Lock()
_bootstrapped_roots: set[str] = set()


def reset_base_bootstrap_state() -> None:
    """Forget which repositories have been bootstrapped in this process."""
    with _BOOTSTRAP_STATE_LOCK:
        _bootstrapped_roots.clear()


def make_base_bootstrap(project_root: Path, config: "Config | None") -> BootstrapHook | None:
    """Build a once-per-process base-repo bootstrap hook, or ``None``.

    Reuses ``parallel/bootstrap.py``'s pipeline against the base repository itself —
    the merge target was never bootstrapped, which is the verified root cause of a
    post-merge gate failing for an environment reason and being reported as a code
    failure.

    Validation is skipped for the base repo: ``quality_gate`` is the sole authoritative
    suite run, and a validation command here would be a second one.

    Returns ``None`` when the operator has configured no bootstrap at all, so that
    a post-merge gate never turns into an implicit dependency install.

    Args:
        project_root: The base/merge-target repository root.
        config: Loaded configuration, or ``None``.

    Returns:
        A hook accepting ``force`` and returning ``BootstrapResult``, or ``None``.

    """
    parallel = getattr(config, "parallel", None) if config is not None else None
    if parallel is None:
        return None
    if not (parallel.setup_commands or parallel.copy_to_worktree):
        logger.debug(
            "[BOOTSTRAP] No parallel bootstrap configuration — base repo bootstrap skipped"
        )
        return None

    # Validation belongs to the canary, not to every post-merge gate.
    base_config = parallel.model_copy(update={"validation_command": None})
    key = str(project_root.resolve())

    def _hook(force: bool) -> "BootstrapResult":
        # Imported here, at call time, so the runner never owns a bootstrap
        # implementation of its own and never creates an import cycle.
        from bmad_assist_lite.parallel.bootstrap import BootstrapResult, bootstrap_worktree

        assert_tree_quiescent("base-repo bootstrap")
        with _BOOTSTRAP_STATE_LOCK:
            already = key in _bootstrapped_roots
            if already and not force:
                logger.debug("[BOOTSTRAP] Base repo already bootstrapped: %s", key)
                return BootstrapResult(success=True, output="already bootstrapped")
            _bootstrapped_roots.add(key)
        logger.info("[BOOTSTRAP] Bootstrapping base repo before quality gate: %s", key)
        return bootstrap_worktree(
            project_root=project_root,
            worktree_path=project_root,
            config=base_config,
            validate=False,
        )

    return _hook


# ============================================================================
# Execution
# ============================================================================


def _execute(
    cmd: GateCommand,
    cwd: Path,
    timeout: int,
    platform: str | None,
    retried: bool,
) -> GateOutcome:
    """Run one gate command and classify the result."""
    with _ActiveCommand():
        if cmd.serial:
            with _TREE_LOCK:
                result: CommandResult = run_command(cmd.command, cwd, timeout=timeout)
        else:
            result = run_command(cmd.command, cwd, timeout=timeout)

    classification = (
        GateClassification.REAL
        if result.success
        else classify_exit_code(result.exit_code, platform=platform)
    )
    return GateOutcome(
        name=cmd.name,
        command=cmd.command,
        passed=result.success,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        classification=classification,
        retried_after_bootstrap=retried,
    )


def _run_wave(
    indexed: Sequence[tuple[int, GateCommand]],
    cwd: Path,
    timeout: int,
    pool_size: int,
    platform: str | None,
    retried: bool,
) -> dict[int, GateOutcome]:
    """Execute one wave of commands, fanning out reads and serialising writes.

    Concurrent commands all complete before any serial command starts, so a
    tree-mutating command can never overlap a read-only one. Results are keyed by the
    command's declared index, so ordering is independent of completion order.
    """
    outcomes: dict[int, GateOutcome] = {}
    concurrent = [(i, c) for i, c in indexed if not c.serial]
    serial = [(i, c) for i, c in indexed if c.serial]

    if concurrent:
        workers = max(1, min(pool_size, len(concurrent)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gate") as pool:
            futures = {
                pool.submit(_execute, c, cwd, timeout, platform, retried): i
                for i, c in concurrent
            }
            for future, index in futures.items():
                outcomes[index] = future.result()

    for index, cmd in serial:
        outcomes[index] = _execute(cmd, cwd, timeout, platform, retried)

    return outcomes


def _log_durations(outcomes: Sequence[GateOutcome], label: str) -> None:
    """Emit one durable duration line per gate command."""
    for outcome in outcomes:
        logger.info(
            GATE_DURATION_LOG_FORMAT,
            GATE_DURATION_LOG_PREFIX,
            label,
            outcome.name,
            outcome.duration_ms,
            outcome.exit_code,
            outcome.classification.value,
        )


def _report(result: GateRunResult) -> None:
    """Write the operator-facing gate summary.

    Every failing gate is named together with its classification and the exact
    command string that failed. There is no code path that prints ``unknown`` for a
    gate outcome — that line is the symptom this runner exists to remove.
    """
    for outcome in result.outcomes:
        icon = "\u2714" if outcome.passed else "\u2718"
        if outcome.passed:
            write_progress(f"    {icon} {outcome.name}: PASS ({outcome.duration_ms} ms)")
        else:
            label = result.classification_label(outcome)
            write_progress(
                f"    {icon} {outcome.name}: FAIL [{label}] "
                f"exit={outcome.exit_code} command: {outcome.command}"
            )

    failures = result.failures
    passed = len(result.outcomes) - len(failures)
    if not failures:
        write_progress(f"    Gate summary: {passed} passed, 0 failed")
        return

    detail = "; ".join(
        f"{o.name} [{result.classification_label(o)}] command: {o.command}"
        for o in failures
    )
    write_progress(
        f"    Gate summary: {passed} passed, {len(failures)} failed — {detail}"
    )


def run_gates(
    commands: Sequence[GateCommand],
    cwd: Path,
    *,
    timeout: int = DEFAULT_COMMAND_TIMEOUT,
    pool_size: int = DEFAULT_GATE_POOL_SIZE,
    label: str = "gate",
    bootstrap: BootstrapHook | None = None,
    bootstrap_first: bool = False,
    platform: str | None = None,
    report: bool = True,
) -> GateRunResult:
    """Execute an ordered set of gate commands and return a classified result.

    Args:
        commands: Ordered gate commands. Result order always matches this order.
        cwd: Working directory for every command.
        timeout: Per-command timeout in seconds.
        pool_size: Upper bound on concurrent commands. ``1`` degrades the runner to
            the sequential behaviour it replaced — the designed kill switch.
        label: Short tag used in duration log lines.
        bootstrap: Optional hook used to bootstrap ``cwd``. Bootstrap-and-retry runs
            **at most once** per invocation.
        bootstrap_first: Bootstrap before the first wave as well as after an ``env``
            failure.
        platform: Platform string for classification; defaults to ``sys.platform``.
        report: Write the console gate summary.

    Returns:
        A :class:`GateRunResult`. A failing command is FAIL under every
        classification — ``env`` changes routing, never the verdict.

    """
    started = time.perf_counter()
    ordered = list(commands)
    if not ordered:
        return GateRunResult(
            all_passed=True, outcomes=[], duration_ms=0, pool_size=pool_size
        )

    bootstrap_attempted = False
    bootstrap_failed = False
    prelude: list[GateOutcome] = []

    if bootstrap is not None and bootstrap_first:
        bootstrap_attempted = True
        pre = bootstrap(False)
        if not pre.success:
            bootstrap_failed = True
            prelude.append(_bootstrap_outcome(pre))

    if report:
        for cmd in ordered:
            write_progress(f"    Running: {cmd.command}")

    indexed = list(enumerate(ordered))
    outcomes = _run_wave(indexed, cwd, timeout, pool_size, platform, retried=False)

    env_blocked = False
    env_failures = [
        (i, ordered[i])
        for i, o in sorted(outcomes.items())
        if not o.passed and o.classification is GateClassification.ENV
    ]

    # One bootstrap-and-retry, and only one. A second env failure after it is a named
    # terminal state, not another retry.
    if env_failures and bootstrap is not None and not bootstrap_failed:
        bootstrap_attempted = True
        retry_bootstrap = bootstrap(True)
        if retry_bootstrap.success:
            retried = _run_wave(
                env_failures, cwd, timeout, pool_size, platform, retried=True
            )
            outcomes.update(retried)
            env_blocked = any(
                not o.passed and o.classification is GateClassification.ENV
                for o in retried.values()
            )
        else:
            bootstrap_failed = True
            env_blocked = True
            prelude.append(_bootstrap_outcome(retry_bootstrap))
    elif env_failures:
        # No bootstrap available: the env failure is terminal and must stay visible.
        env_blocked = True

    final = prelude + [outcomes[i] for i in range(len(ordered))]
    result = GateRunResult(
        all_passed=all(o.passed for o in final),
        outcomes=final,
        duration_ms=int((time.perf_counter() - started) * 1000),
        pool_size=pool_size,
        bootstrap_attempted=bootstrap_attempted,
        bootstrap_failed=bootstrap_failed,
        env_blocked=env_blocked and not all(o.passed for o in final),
    )

    _log_durations(final, label)
    if result.env_blocked:
        logger.warning(
            "[%s] Gate run ended %s — %s",
            label,
            ENV_BLOCKED_LABEL,
            "; ".join(
                f"{o.name}: {o.command} (exit {o.exit_code})" for o in result.failures
            ),
        )
    if report:
        _report(result)
    return result


def _bootstrap_outcome(bootstrap_result: "BootstrapResult") -> GateOutcome:
    """Surface a bootstrap failure as a named, visible gate outcome.

    Copy and setup failures are environment failures by definition. A *validation*
    command failure is not: it runs project code and can fail for a real code reason,
    so it keeps the ``real`` classification and stays routable to the fixer.
    """
    phase = bootstrap_result.failed_phase or "bootstrap"
    if bootstrap_result.classification is not None:
        classification = GateClassification(bootstrap_result.classification)
    else:
        classification = (
            GateClassification.REAL if phase == "validation" else GateClassification.ENV
        )
    return GateOutcome(
        name=f"Bootstrap ({phase})",
        command=bootstrap_result.error_message or "bootstrap pipeline",
        passed=False,
        exit_code=1,
        stdout=bootstrap_result.output,
        stderr=bootstrap_result.error_message or "",
        duration_ms=0,
        classification=classification,
    )
