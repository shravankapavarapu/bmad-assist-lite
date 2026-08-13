"""Guards for durable per-phase provider metrics (REQ-10.1 AC3/AC5).

T27 captured metrics onto ``ProviderResult``; nothing persisted them, so every
number died with the run. These tests hold the persistence honest in the three
ways that matter:

* every declared field survives a completed phase,
* a timed-out phase is *recorded as timed out* with ``None`` metrics — never
  ``0``, because a zero is indistinguishable from a cheap call and the
  measurement protocol rejects any snapshot containing a timed-out phase,
* a crash mid-write leaves the previously recorded runs intact.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from bmad_assist_lite.core.exceptions import MetricsError
from bmad_assist_lite.core.phase_metrics import (
    PHASE_METRIC_FIELDS,
    PhaseMetricRecord,
    append_record,
    load_records,
    phase_metrics_context,
    record_provider_call,
)

# The exact, complete field set. AC5: the record carries measurements and
# attribution — never prompt text, never provider output, never a secret.
EXPECTED_FIELDS = {
    "story_id",
    "phase",
    "model",
    "timestamp",
    "duration_ms",
    "api_duration_ms",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "total_cost_usd",
    "timed_out",
    "call_count",
    "provider_session_id",
    "resumed_session_id",
    "session_reused",
}


def _full_call() -> None:
    record_provider_call(
        model="opus",
        duration_ms=9_000,
        api_duration_ms=4_200,
        input_tokens=120,
        output_tokens=340,
        cache_read_tokens=90_000,
        cache_creation_tokens=1_500,
        total_cost_usd=0.37,
    )


class TestRecordShape:
    """AC5 — the record's field set is exactly the declared metric fields."""

    def test_field_set_is_exactly_the_declared_metric_fields(self) -> None:
        assert set(PhaseMetricRecord.model_fields) == EXPECTED_FIELDS
        assert PHASE_METRIC_FIELDS == frozenset(EXPECTED_FIELDS)

    def test_no_free_text_field_can_smuggle_prompt_content(self) -> None:
        """Every field is a scalar measurement or a short identifier."""
        annotations = {
            name: field.annotation for name, field in PhaseMetricRecord.model_fields.items()
        }
        assert annotations["story_id"] in (str | None,)
        assert annotations["phase"] is str
        assert annotations["model"] in (str | None,)
        assert annotations["provider_session_id"] in (str | None,)
        assert annotations["resumed_session_id"] in (str | None,)

    def test_record_is_frozen(self) -> None:
        record = PhaseMetricRecord(phase="dev_story", timestamp=datetime(2026, 8, 11), duration_ms=1)
        with pytest.raises(Exception):
            record.phase = "other"  # type: ignore[misc]


class TestCompletedPhase:
    """AC3 — a completed phase writes a record carrying every field."""

    def test_completed_phase_writes_a_record_with_every_field(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"

        with phase_metrics_context(story_id="1.2", phase="dev_story", path=path) as handle:
            _full_call()
            handle.set_duration_ms(12_345)

        records = load_records(path)
        assert len(records) == 1
        record = records[0]

        assert record.story_id == "1.2"
        assert record.phase == "dev_story"
        assert record.model == "opus"
        assert isinstance(record.timestamp, datetime)
        assert record.timestamp.tzinfo is None, "timestamps are naive UTC"
        assert record.duration_ms == 12_345
        assert record.api_duration_ms == 4_200
        assert record.input_tokens == 120
        assert record.output_tokens == 340
        assert record.cache_read_tokens == 90_000
        assert record.cache_creation_tokens == 1_500
        assert record.total_cost_usd == pytest.approx(0.37)
        assert record.timed_out is False
        assert record.call_count == 1

    def test_multi_llm_phase_sums_tokens_and_keeps_every_model(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"

        with phase_metrics_context(story_id="1.2", phase="code_review", path=path) as handle:
            record_provider_call(model="sonnet", duration_ms=1_000, input_tokens=10, output_tokens=1)
            record_provider_call(
                model="gemini-2.5-flash", duration_ms=2_000, input_tokens=20, output_tokens=2
            )
            handle.set_duration_ms(3_000)

        record = load_records(path)[0]
        assert record.call_count == 2
        assert record.model == "sonnet,gemini-2.5-flash"
        assert record.input_tokens == 30
        assert record.output_tokens == 3

    def test_phase_with_no_provider_call_still_records_timing(self, tmp_path: Path) -> None:
        """quality_gate is non-LLM; its wall-clock is still measurement data."""
        path = tmp_path / "phase-metrics.jsonl"

        with phase_metrics_context(story_id="1.2", phase="quality_gate", path=path) as handle:
            handle.set_duration_ms(4_000)

        record = load_records(path)[0]
        assert record.phase == "quality_gate"
        assert record.duration_ms == 4_000
        assert record.call_count == 0
        assert record.model is None

    def test_records_append_across_phases(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        for phase in ("dev_story", "code_review", "quality_gate"):
            with phase_metrics_context(story_id="1.2", phase=phase, path=path) as handle:
                handle.set_duration_ms(1)
        assert [r.phase for r in load_records(path)] == [
            "dev_story",
            "code_review",
            "quality_gate",
        ]


class TestTimedOutPhase:
    """NEG — a timed-out phase is recorded as such, with None metrics, never 0."""

    def test_timed_out_phase_records_the_flag_with_none_metrics(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"

        with phase_metrics_context(story_id="1.2", phase="dev_story", path=path) as handle:
            record_provider_call(model="opus", duration_ms=1_200_000, timed_out=True)
            handle.set_duration_ms(1_200_000)

        record = load_records(path)[0]
        assert record.timed_out is True
        for field in (
            "api_duration_ms",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "total_cost_usd",
        ):
            value = getattr(record, field)
            assert value is None, f"{field} must be None on a timed-out call, got {value!r}"
            assert value != 0

    def test_timed_out_flag_survives_serialization(self, tmp_path: Path) -> None:
        """Protocol rule 10 rejects a snapshot containing a timed-out phase — so the
        flag has to be readable off disk, not just present in memory."""
        path = tmp_path / "phase-metrics.jsonl"
        with phase_metrics_context(story_id="1.2", phase="dev_story", path=path) as handle:
            record_provider_call(model="opus", duration_ms=1, timed_out=True)
            handle.set_duration_ms(1)

        raw = json.loads(path.read_text(encoding="utf-8").strip())
        assert raw["timed_out"] is True
        assert raw["api_duration_ms"] is None

    def test_one_timed_out_call_marks_the_whole_phase(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        with phase_metrics_context(story_id="1.2", phase="code_review", path=path) as handle:
            record_provider_call(model="sonnet", duration_ms=1, input_tokens=5)
            record_provider_call(model="haiku", duration_ms=2, timed_out=True)
            handle.set_duration_ms(3)

        record = load_records(path)[0]
        assert record.timed_out is True
        assert record.input_tokens == 5, "a partial phase still reports what it did measure"


class TestAtomicity:
    """Records survive a crash mid-write."""

    def test_crash_during_replace_leaves_previous_records_intact(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        first = PhaseMetricRecord(
            story_id="1.1", phase="dev_story", timestamp=datetime(2026, 8, 11), duration_ms=1
        )
        append_record(first, path)
        before = path.read_text(encoding="utf-8")

        second = PhaseMetricRecord(
            story_id="1.2", phase="dev_story", timestamp=datetime(2026, 8, 11), duration_ms=2
        )
        with patch(
            "bmad_assist_lite.core.phase_metrics.os.replace",
            side_effect=OSError("host died mid-write"),
        ):
            with pytest.raises(MetricsError):
                append_record(second, path)

        assert path.read_text(encoding="utf-8") == before
        assert load_records(path) == [first]

    def test_no_temp_file_is_left_behind_after_a_failed_write(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        record = PhaseMetricRecord(
            phase="dev_story", timestamp=datetime(2026, 8, 11), duration_ms=1
        )
        with patch(
            "bmad_assist_lite.core.phase_metrics.os.replace", side_effect=OSError("nope")
        ):
            with pytest.raises(MetricsError):
                append_record(record, path)

        assert list(tmp_path.glob("*.tmp")) == []

    def test_write_uses_temp_plus_replace_not_direct_append(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        record = PhaseMetricRecord(
            phase="dev_story", timestamp=datetime(2026, 8, 11), duration_ms=1
        )
        with patch(
            "bmad_assist_lite.core.phase_metrics.os.replace", wraps=os.replace
        ) as replace_spy:
            append_record(record, path)
        assert replace_spy.call_count == 1


class TestRoundTrip:
    def test_records_round_trip_through_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        records = [
            PhaseMetricRecord(
                story_id="1.2",
                phase="dev_story",
                model="opus",
                timestamp=datetime(2026, 8, 11, 12, 30, 15, 500000),
                duration_ms=12_345,
                api_duration_ms=4_200,
                input_tokens=120,
                output_tokens=340,
                cache_read_tokens=90_000,
                cache_creation_tokens=1_500,
                total_cost_usd=0.37,
                timed_out=False,
                call_count=1,
            ),
            PhaseMetricRecord(
                story_id="1.3",
                phase="quality_gate",
                timestamp=datetime(2026, 8, 11, 12, 45),
                duration_ms=900,
            ),
        ]
        for record in records:
            append_record(record, path)

        assert load_records(path) == records

    def test_missing_file_reads_as_no_records(self, tmp_path: Path) -> None:
        assert load_records(tmp_path / "absent.jsonl") == []

    def test_corrupt_line_is_reported_not_silently_dropped(self, tmp_path: Path) -> None:
        """A capture that lost records is a failure to capture, not a result."""
        path = tmp_path / "phase-metrics.jsonl"
        path.write_text("{not json}\n", encoding="utf-8")
        with pytest.raises(MetricsError):
            load_records(path)


class TestInstrumentationCannotKillARun:
    """NEG — instrumentation sits on the path every phase uses. If it can raise,
    it can end a multi-hour run. It must not."""

    def test_recording_outside_a_phase_is_a_no_op(self) -> None:
        record_provider_call(model="opus", duration_ms=1)  # must not raise

    def test_hostile_metric_values_never_raise(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        with phase_metrics_context(story_id="1.2", phase="dev_story", path=path) as handle:
            record_provider_call(
                model=object(),  # type: ignore[arg-type]
                duration_ms="not-a-number",  # type: ignore[arg-type]
                api_duration_ms=float("nan"),  # type: ignore[arg-type]
                input_tokens="many",  # type: ignore[arg-type]
                total_cost_usd=float("inf"),
            )
            handle.set_duration_ms(5)

        record = load_records(path)[0]
        assert record.api_duration_ms is None
        assert record.input_tokens is None
        assert record.total_cost_usd is None, "non-finite cost corrupts an aggregate worse than 0"

    def test_an_unwritable_path_does_not_propagate_out_of_the_context(
        self, tmp_path: Path
    ) -> None:
        unwritable = tmp_path / "a-file"
        unwritable.write_text("blocking", encoding="utf-8")
        with phase_metrics_context(
            story_id="1.2", phase="dev_story", path=unwritable / "nested" / "m.jsonl"
        ) as handle:
            handle.set_duration_ms(1)

    def test_an_exception_inside_the_phase_still_records_it(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        with pytest.raises(ValueError):
            with phase_metrics_context(story_id="1.2", phase="dev_story", path=path) as handle:
                handle.set_duration_ms(7)
                raise ValueError("handler blew up")

        record = load_records(path)[0]
        assert record.duration_ms == 7

    def test_duration_defaults_to_measured_wall_clock_when_never_set(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        with phase_metrics_context(story_id="1.2", phase="dev_story", path=path):
            pass
        assert load_records(path)[0].duration_ms >= 0


class TestProviderHook:
    """Every provider call flows through BaseProvider.invoke(), so that is where
    the metrics are handed over — one hook, including the multi-LLM fan-out."""

    def _provider(self, result_kwargs: dict | None = None, raise_timeout: bool = False):
        from bmad_assist_lite.providers.base import BaseProvider, ProviderResult

        class _Fake(BaseProvider):
            @property
            def provider_name(self) -> str:
                return "fake"

            def _do_invoke(self, prompt, **kwargs):  # type: ignore[no-untyped-def]
                if raise_timeout:
                    raise TimeoutError
                return ProviderResult(
                    stdout="text",
                    stderr="",
                    exit_code=0,
                    duration_ms=1_000,
                    model=kwargs.get("model"),
                    command=("fake",),
                    **(result_kwargs or {}),
                )

            def _cleanup(self) -> None:
                return None

            def parse_output(self, result) -> str:  # type: ignore[no-untyped-def]
                return result.stdout

            def supports_model(self, model: str) -> bool:
                return True

        return _Fake()

    def test_a_successful_invocation_is_recorded(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        provider = self._provider(
            {"api_duration_ms": 900, "input_tokens": 7, "total_cost_usd": 0.01}
        )

        with phase_metrics_context(story_id="2.1", phase="dev_story", path=path) as handle:
            result = provider.invoke("prompt", model="opus", timeout=5)
            handle.set_duration_ms(1_100)

        assert result.stdout == "text", "capture must not disturb the response"
        record = load_records(path)[0]
        assert record.call_count == 1
        assert record.model == "opus"
        assert record.api_duration_ms == 900
        assert record.input_tokens == 7
        assert record.total_cost_usd == pytest.approx(0.01)

    def test_provider_session_id_flows_from_the_result(self, tmp_path: Path) -> None:
        """AC(L4) — the session id captured on ProviderResult reaches the row.

        Without this the field is write-only on the result and dropped at the
        one hook that could persist it.
        """
        path = tmp_path / "phase-metrics.jsonl"
        provider = self._provider(
            {"provider_session_id": "sess-xyz", "session_reused": True,
             "resumed_session_id": "sess-prev"}
        )

        with phase_metrics_context(story_id="2.1", phase="dev_story", path=path) as handle:
            provider.invoke("prompt", model="opus", timeout=5)
            handle.set_duration_ms(1_100)

        record = load_records(path)[0]
        assert record.provider_session_id == "sess-xyz"
        assert record.resumed_session_id == "sess-prev"
        assert record.session_reused is True

    def test_a_raised_timeout_is_still_recorded_as_timed_out(self, tmp_path: Path) -> None:
        """The raise path never builds a ProviderResult, so without an explicit
        record the most expensive phase in the run would leave no trace at all."""
        from bmad_assist_lite.core.exceptions import ProviderTimeoutError

        path = tmp_path / "phase-metrics.jsonl"
        provider = self._provider(raise_timeout=True)

        with phase_metrics_context(story_id="2.1", phase="dev_story", path=path) as handle:
            with pytest.raises(ProviderTimeoutError):
                provider.invoke("prompt", model="opus", timeout=1)
            handle.set_duration_ms(1_000)

        record = load_records(path)[0]
        assert record.timed_out is True
        assert record.call_count == 1
        assert record.input_tokens is None

    def test_invocation_outside_a_phase_does_not_raise(self) -> None:
        provider = self._provider()
        assert provider.invoke("prompt", model="opus", timeout=5).stdout == "text"


class TestSessionAttribution:
    """L4 — session ids and the reuse flag fold from the phase's calls to the row."""

    def test_cold_phase_leaves_session_fields_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        with phase_metrics_context(story_id="1.2", phase="dev_story", path=path) as handle:
            _full_call()  # reports no session id
            handle.set_duration_ms(1)
        record = load_records(path)[0]
        assert record.provider_session_id is None
        assert record.resumed_session_id is None
        assert record.session_reused is False

    def test_single_call_records_its_session(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        with phase_metrics_context(story_id="1.2", phase="dev_story", path=path) as handle:
            record_provider_call(model="opus", duration_ms=1, provider_session_id="sess-abc")
            handle.set_duration_ms(1)
        record = load_records(path)[0]
        assert record.provider_session_id == "sess-abc"
        assert record.session_reused is False

    def test_resume_populates_prior_id_and_flag(self, tmp_path: Path) -> None:
        path = tmp_path / "phase-metrics.jsonl"
        with phase_metrics_context(story_id="1.2", phase="fix_quality_gate", path=path) as handle:
            record_provider_call(
                model="opus",
                duration_ms=1,
                provider_session_id="sess-2",
                resumed_session_id="sess-1",
                session_reused=True,
            )
            handle.set_duration_ms(1)
        record = load_records(path)[0]
        assert record.provider_session_id == "sess-2"
        assert record.resumed_session_id == "sess-1"
        assert record.session_reused is True

    def test_fan_out_keeps_every_distinct_session_and_ors_the_flag(self, tmp_path: Path) -> None:
        """Fan-out folds sessions like ``model`` — distinct ids, call order.

        Comma-joined; the reuse flag is the OR across the phase's calls.
        """
        path = tmp_path / "phase-metrics.jsonl"
        with phase_metrics_context(story_id="1.2", phase="code_review", path=path) as handle:
            record_provider_call(model="fable", duration_ms=1, provider_session_id="rev-A")
            record_provider_call(
                model="opus",
                duration_ms=1,
                provider_session_id="rev-B",
                resumed_session_id="rev-B0",
                session_reused=True,
            )
            record_provider_call(model="fable", duration_ms=1, provider_session_id="rev-A")
            handle.set_duration_ms(1)
        record = load_records(path)[0]
        assert record.provider_session_id == "rev-A,rev-B"
        assert record.resumed_session_id == "rev-B0"
        assert record.session_reused is True


class TestDispatchPersistence:
    """The wiring: execute_phase() opens the context and the record lands on disk."""

    def _run(self, tmp_path: Path, handler):  # type: ignore[no-untyped-def]
        from bmad_assist_lite.core.paths import get_paths, init_paths
        from bmad_assist_lite.core.state import Phase, State
        from bmad_assist_lite.loop import dispatch

        init_paths(tmp_path)
        state = State(current_epic=1, current_story="1.2", current_phase=Phase.DEV_STORY)
        with patch.object(dispatch, "get_handler", return_value=handler):
            result = dispatch.execute_phase(state)
        return result, get_paths().phase_metrics_file

    def test_execute_phase_persists_a_record(self, tmp_path: Path) -> None:
        from bmad_assist_lite.loop.types import PhaseResult

        def handler(state):  # type: ignore[no-untyped-def]
            record_provider_call(model="opus", duration_ms=50, input_tokens=11)
            return PhaseResult.ok({"response": "hi"})

        result, path = self._run(tmp_path, handler)

        assert result.success is True
        record = load_records(path)[0]
        assert record.story_id == "1.2"
        assert record.phase == "dev_story"
        assert record.model == "opus"
        assert record.input_tokens == 11
        assert record.duration_ms == result.outputs["duration_ms"]

    def test_a_failing_phase_is_recorded_too(self, tmp_path: Path) -> None:
        def handler(state):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        result, path = self._run(tmp_path, handler)

        assert result.success is False
        assert load_records(path)[0].phase == "dev_story"

    def test_uninitialised_paths_do_not_break_the_phase(self) -> None:
        """NEG — the singleton is not initialised in every context the loop runs in.
        Persistence is best-effort; the phase result is not."""
        from bmad_assist_lite.core.state import Phase, State
        from bmad_assist_lite.loop import dispatch
        from bmad_assist_lite.loop.types import PhaseResult

        state = State(current_epic=1, current_story="1.2", current_phase=Phase.DEV_STORY)
        with patch.object(dispatch, "get_handler", return_value=lambda s: PhaseResult.ok()):
            assert dispatch.execute_phase(state).success is True
