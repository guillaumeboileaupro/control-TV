"""Command tracing is deterministic evidence and never changes command behavior."""

from __future__ import annotations

import itertools
from concurrent.futures import ThreadPoolExecutor

import pytest

from control_tv.command_trace import MemoryTraceSink, TraceEvent, TraceRecorder
from control_tv.domain import Confirmation, DeviceUnavailableError, OperationTimeoutError
from control_tv.service import ControlService
from fakes import DEVICE_ID, FakeClock, FakeTransport


def recorder(sink: MemoryTraceSink) -> TraceRecorder:
    identifiers = iter(("request-a", "request-b", "request-c"))
    sequences = itertools.count(1)
    return TraceRecorder(
        sink,
        client="ubuntu",
        request_id_factory=lambda: next(identifiers),
        sequence_factory=lambda: next(sequences),
    )


def test_playback_trace_binds_send_and_confirmation_to_one_request_and_session() -> None:
    sink = MemoryTraceSink()
    service = ControlService(FakeTransport(), trace=recorder(sink))

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    events = sink.events()
    assert [event.event for event in events] == [
        "command_prepared",
        "pre_status",
        "send_attempt",
        "transport_response",
        "confirmation_status",
        "command_confirmed",
    ]
    assert {event.request_id for event in events} == {"request-a"}
    assert [event.media_session_id for event in events if event.media_session_id is not None] == [
        1,
        1,
        1,
        1,
        1,
    ]


def test_ambiguous_failure_records_one_attempt_and_never_replays() -> None:
    sink = MemoryTraceSink()
    transport = FakeTransport(
        fail_commands_with=OperationTimeoutError("answer lost", device_id=DEVICE_ID)
    )
    service = ControlService(transport, trace=recorder(sink))

    with pytest.raises(OperationTimeoutError):
        service.pause(DEVICE_ID)

    assert transport.attempted() == ["pause"]
    assert [event.event for event in sink.events()] == [
        "command_prepared",
        "pre_status",
        "send_attempt",
        "command_failed",
        "ambiguous_result",
    ]


def test_proven_not_sent_failure_is_never_traced_as_ambiguous() -> None:
    sink = MemoryTraceSink()
    error = DeviceUnavailableError("not connected", device_id=DEVICE_ID)
    error.delivery = "not_sent"
    transport = FakeTransport(fail_commands_with=error)

    with pytest.raises(DeviceUnavailableError):
        ControlService(transport, trace=recorder(sink)).pause(DEVICE_ID)

    names = [event.event for event in sink.events()]
    assert names.count("send_attempt") == 1
    assert names.count("not_sent") == 1
    assert "ambiguous_result" not in names
    assert transport.attempted() == ["pause"]


def test_independent_session_observation_completes_c6_evidence() -> None:
    sink = MemoryTraceSink()
    trace = recorder(sink)
    transport = FakeTransport(ignore_commands=True)
    clock = FakeClock()
    transport.clock = clock

    def switch_session() -> None:
        transport.tv.media_session_id = 2

    transport.status_effects = [lambda: None, switch_session]
    result = ControlService(
        transport,
        trace=trace,
        confirm_timeout=1.0,
        clock=clock.monotonic,
        sleep=clock.sleep,
    ).pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    events = sink.events()
    assert [event.event for event in events].count("send_attempt") == 1
    names = [event.event for event in events]
    assert names.index("pre_status") < names.index("send_attempt")
    assert names.index("send_attempt") < names.index("session_changed")
    assert names.index("session_changed") < names.index("confirmation_status")
    assert next(event for event in events if event.event == "pre_status").media_session_id == 1
    assert next(event for event in events if event.event == "send_attempt").media_session_id == 1
    assert next(event for event in events if event.event == "session_changed").media_session_id == 2
    assert (
        next(event for event in events if event.event == "confirmation_status").media_session_id
        == 2
    )
    assert "command_confirmed" not in names


def test_concurrent_commands_keep_distinct_correlations_and_one_attempt_each() -> None:
    sink = MemoryTraceSink()
    trace = recorder(sink)
    services = (
        ControlService(FakeTransport(), trace=trace),
        ControlService(FakeTransport(), trace=trace),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda service: service.pause(DEVICE_ID), services))

    assert all(result.confirmation is Confirmation.CONFIRMED for result in results)
    attempts = [event for event in sink.events() if event.event == "send_attempt"]
    assert len(attempts) == 2
    assert {event.request_id for event in attempts} == {"request-a", "request-b"}


def test_broken_trace_sink_does_not_change_or_repeat_the_command() -> None:
    class BrokenSink:
        def write(self, event: TraceEvent) -> None:
            del event
            raise OSError("evidence disk unavailable")

    transport = FakeTransport()
    service = ControlService(transport, trace=TraceRecorder(BrokenSink()))

    assert service.pause(DEVICE_ID).confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == ["pause"]
