"""Command tracing is deterministic evidence and never changes command behavior."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pytest

from control_tv.command_trace import (
    TERMINAL_EVENTS,
    HoldOutcome,
    HoldRequest,
    MemoryTraceSink,
    TraceConfigurationError,
    TraceEvent,
    TraceRecorder,
    TraceRole,
)
from control_tv.domain import (
    Command,
    CommandRejectedError,
    Confirmation,
    DeviceStatus,
    DeviceUnavailableError,
    MediaRequest,
    OperationTimeoutError,
)
from control_tv.service import ControlService
from fakes import DEVICE_ID, FakeClock, FakeTransport


def recorder(
    sink: MemoryTraceSink, role: TraceRole = TraceRole.UBUNTU, prefix: str = "request"
) -> TraceRecorder:
    identifiers = (f"{prefix}-{n}" for n in itertools.count())
    return TraceRecorder(sink, role=role, request_id_factory=lambda: next(identifiers))


def names(sink: MemoryTraceSink) -> list[str]:
    return [event.event for event in sink.events()]


def assert_complete(events: tuple[TraceEvent, ...]) -> None:
    """Each request's events are contiguous from 0 and end with exactly one outcome."""
    for request_id in {event.request_id for event in events}:
        stream = [event for event in events if event.request_id == request_id]
        assert [event.index for event in stream] == list(range(len(stream)))
        outcomes = [event for event in stream if event.event in TERMINAL_EVENTS]
        assert len(outcomes) == 1 and stream[-1] is outcomes[0]


# The recorder ---------------------------------------------------------------------------------


def test_without_a_sink_nothing_is_recorded_and_no_role_is_needed() -> None:
    trace = TraceRecorder().command(Command.PAUSE)
    trace.record("send_attempt")
    assert TraceRecorder().active is False and TraceRecorder().role is None


def test_an_active_trace_needs_an_explicit_role() -> None:
    with pytest.raises(TraceConfigurationError, match="explicit client role"):
        TraceRecorder(MemoryTraceSink())


def test_indexes_are_contiguous_per_request_and_sequences_local() -> None:
    sink = MemoryTraceSink()
    trace = recorder(sink)
    first, second = trace.command(Command.PLAY), trace.command(Command.PAUSE)
    first.record("send_attempt")
    second.record("send_attempt")

    events = sink.events()
    assert [(e.request_id, e.index) for e in events] == [
        ("request-0", 0),
        ("request-1", 0),
        ("request-0", 1),
        ("request-1", 1),
    ]
    assert [e.sequence for e in events] == [1, 2, 3, 4]
    assert {e.client for e in events} == {"ubuntu"}


def test_only_the_observer_records_observations() -> None:
    with pytest.raises(TraceConfigurationError, match="observer role"):
        recorder(MemoryTraceSink()).observation("independent_session_change", request_id="r")
    sink = MemoryTraceSink()
    recorder(sink, TraceRole.OBSERVER).observation("seen", request_id="r", media_session_id=2)
    assert sink.events()[0].client == "observer" and sink.events()[0].command is None


@dataclass
class FlakySink:
    """Fails the writes whose number is in `failing` (1-based); keeps the others."""

    failing: set[int]
    events: list[TraceEvent] = field(default_factory=list)
    writes: int = 0

    def write(self, event: TraceEvent) -> None:
        self.writes += 1
        if self.writes in self.failing:
            raise OSError("evidence disk unavailable")
        self.events.append(event)


def test_a_lost_write_leaves_a_gap_and_a_trace_incomplete_marker() -> None:
    sink = FlakySink(failing={2})
    trace = TraceRecorder(sink, role=TraceRole.UBUNTU).command(Command.PAUSE)
    trace.record("send_attempt")  # lost
    trace.record("transport_response")

    assert [(e.event, e.index) for e in sink.events] == [
        ("command_prepared", 0),
        ("trace_incomplete", 2),
        ("transport_response", 3),
    ]


def test_the_incomplete_flag_survives_a_lost_marker_too() -> None:
    sink = FlakySink(failing={2, 3})
    trace = TraceRecorder(sink, role=TraceRole.UBUNTU).command(Command.PAUSE)
    trace.record("send_attempt")  # lost
    trace.record("transport_response")  # its marker is lost; this event is written

    trace.record("command_confirmed")
    assert [e.event for e in sink.events] == [
        "command_prepared",
        "transport_response",
        "trace_incomplete",
        "command_confirmed",
    ]
    assert [e.index for e in sink.events] == [0, 3, 4, 5]  # the gap stays visible


# Command paths --------------------------------------------------------------------------------


def test_a_confirmed_pause_is_one_correlated_complete_trace() -> None:
    sink = MemoryTraceSink()
    result = ControlService(FakeTransport(), trace=recorder(sink)).pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert names(sink) == [
        "command_prepared",
        "pre_status",
        "send_attempt",
        "transport_response",
        "confirmation_status",
        "command_confirmed",
    ]
    assert {e.request_id for e in sink.events()} == {"request-0"}
    assert {e.media_session_id for e in sink.events()} == {None, 1}
    assert_complete(sink.events())


def test_a_timeout_after_the_send_is_ambiguous_and_never_replayed() -> None:
    sink = MemoryTraceSink()
    transport = FakeTransport(
        fail_commands_with=OperationTimeoutError("answer lost", device_id=DEVICE_ID)
    )

    with pytest.raises(OperationTimeoutError):
        ControlService(transport, trace=recorder(sink)).pause(DEVICE_ID)

    assert transport.attempted() == ["pause"]
    assert names(sink)[-3:] == ["send_attempt", "command_failed", "ambiguous_result"]
    assert_complete(sink.events())


def test_a_lost_connection_during_the_send_is_ambiguous() -> None:
    sink = MemoryTraceSink()
    transport = FakeTransport(
        fail_commands_with=DeviceUnavailableError("gone", device_id=DEVICE_ID)
    )

    with pytest.raises(DeviceUnavailableError):
        ControlService(transport, trace=recorder(sink)).play(DEVICE_ID)

    assert names(sink)[-1] == "ambiguous_result" and transport.attempted() == ["play"]
    assert sink.events()[-1].error_code == "device_unavailable"


def test_a_rejected_command_ends_rejected_not_ambiguous() -> None:
    sink = MemoryTraceSink()
    transport = FakeTransport(fail_commands_with=CommandRejectedError("no", device_id=DEVICE_ID))

    with pytest.raises(CommandRejectedError):
        ControlService(transport, trace=recorder(sink)).pause(DEVICE_ID)

    assert names(sink)[-1] == "command_rejected" and "ambiguous_result" not in names(sink)
    assert_complete(sink.events())


def test_a_proven_not_sent_failure_is_never_ambiguous() -> None:
    sink = MemoryTraceSink()
    error = DeviceUnavailableError("not connected", device_id=DEVICE_ID)
    error.delivery = "not_sent"

    with pytest.raises(DeviceUnavailableError):
        ControlService(FakeTransport(fail_commands_with=error), trace=recorder(sink)).pause(
            DEVICE_ID
        )

    assert names(sink)[-1] == "not_sent" and "ambiguous_result" not in names(sink)


def test_a_timeout_before_the_send_is_not_sent_without_any_attempt() -> None:
    sink = MemoryTraceSink()
    transport = FakeTransport()

    with pytest.raises(OperationTimeoutError):
        ControlService(transport, trace=recorder(sink), confirm_timeout=0).pause(DEVICE_ID)

    assert "send_attempt" not in names(sink) and transport.attempted() == []
    assert names(sink) == ["command_prepared", "command_failed", "not_sent"]


def test_a_failed_identity_read_is_not_sent() -> None:
    sink = MemoryTraceSink()

    class Unreachable(FakeTransport):
        def get_status(self, device_id: object, *, timeout: float) -> DeviceStatus:
            raise OperationTimeoutError("no answer", device_id=DEVICE_ID)

    with pytest.raises(OperationTimeoutError):
        ControlService(Unreachable(), trace=recorder(sink)).stop(DEVICE_ID)
    assert names(sink) == ["command_prepared", "command_failed", "not_sent"]


def test_an_unexpected_exception_is_recorded_ambiguous_and_raised_unchanged() -> None:
    sink = MemoryTraceSink()

    class Broken(FakeTransport):
        def pause(self, device_id: object, media_session_id: int) -> None:
            raise RuntimeError("bug in a library")

    with pytest.raises(RuntimeError, match="bug in a library"):
        ControlService(Broken(), trace=recorder(sink)).pause(DEVICE_ID)
    assert names(sink)[-3:] == ["send_attempt", "unexpected_error", "ambiguous_result"]


def test_failed_confirmation_reads_are_traced() -> None:
    sink = MemoryTraceSink()
    clock = FakeClock()
    reads = itertools.count()

    class Flaky(FakeTransport):
        def get_status(self, device_id: object, *, timeout: float) -> DeviceStatus:
            if next(reads) == 1:
                raise OperationTimeoutError("read lost", device_id=DEVICE_ID)
            return super().get_status(DEVICE_ID, timeout=timeout)

    transport = Flaky()
    transport.clock = clock
    ControlService(
        transport, trace=recorder(sink), confirm_timeout=5, clock=clock.monotonic, sleep=clock.sleep
    ).pause(DEVICE_ID)

    failed = [e for e in sink.events() if e.event == "confirmation_read_failed"]
    assert len(failed) == 1 and failed[0].error_code == "timeout"
    assert names(sink)[-1] == "command_confirmed"


def test_the_product_names_its_own_session_observation_apart_from_c6_evidence() -> None:
    sink = MemoryTraceSink()
    clock = FakeClock()
    transport = FakeTransport(ignore_commands=True)
    transport.clock = clock

    def switch() -> None:
        transport.tv.media_session_id = 2

    transport.status_effects = [lambda: None, switch]
    ControlService(
        transport, trace=recorder(sink), confirm_timeout=1, clock=clock.monotonic, sleep=clock.sleep
    ).pause(DEVICE_ID)

    assert "confirmation_session_changed" in names(sink)
    assert "session_changed" not in names(sink) and "independent_session_change" not in names(sink)


@pytest.mark.parametrize(
    "run",
    [
        lambda s: s.set_volume(DEVICE_ID, 0.4),
        lambda s: s.set_muted(DEVICE_ID, True),
        lambda s: s.load_media(
            DEVICE_ID, MediaRequest(url="https://media.invalid/a.mp4", content_type="video/mp4")
        ),
        lambda s: s.seek(DEVICE_ID, 3.0),
        lambda s: s.play(DEVICE_ID),
    ],
    ids=["volume", "mute", "load", "seek", "play"],
)
def test_every_command_trace_is_complete(run: Callable[[ControlService], object]) -> None:
    sink = MemoryTraceSink()
    run(ControlService(FakeTransport(), trace=recorder(sink)))
    assert_complete(sink.events())


def test_alternating_remotes_keep_their_own_identities_on_one_tv() -> None:
    sink = MemoryTraceSink()
    tv = FakeTransport()
    ubuntu = ControlService(tv, trace=recorder(sink, TraceRole.UBUNTU, "u"))
    android = ControlService(tv, trace=recorder(sink, TraceRole.ANDROID, "a"))

    ubuntu.pause(DEVICE_ID)
    android.play(DEVICE_ID)
    ubuntu.pause(DEVICE_ID)

    events = sink.events()
    assert_complete(events)
    by_request = {e.request_id: e.client for e in events}
    assert by_request == {"u-0": "ubuntu", "a-0": "android", "u-1": "ubuntu"}
    assert [e.request_id for e in events if e.event == "send_attempt"] == ["u-0", "a-0", "u-1"]
    assert tv.attempted() == ["pause", "play", "pause"]


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
    attempts = [e for e in sink.events() if e.event == "send_attempt"]
    assert sorted(e.request_id for e in attempts) == ["request-0", "request-1"]
    assert_complete(sink.events())


def test_a_broken_trace_sink_does_not_change_or_repeat_the_command() -> None:
    class BrokenSink:
        def write(self, event: TraceEvent) -> None:
            raise OSError("evidence disk unavailable")

    transport = FakeTransport()
    service = ControlService(transport, trace=TraceRecorder(BrokenSink(), role=TraceRole.UBUNTU))

    assert service.pause(DEVICE_ID).confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == ["pause"]


def test_an_unexpected_confirmation_error_is_traced_without_its_message() -> None:
    class BrokenConfirmation(FakeTransport):
        reads = 0

        def get_status(self, device_id: object, *, timeout: float) -> DeviceStatus:
            self.reads += 1
            if self.reads > 1:
                raise RuntimeError("sensitive receiver failure")
            return super().get_status(DEVICE_ID, timeout=timeout)

    sink = MemoryTraceSink()
    transport = BrokenConfirmation()

    with pytest.raises(RuntimeError, match="sensitive receiver failure"):
        ControlService(transport, trace=recorder(sink)).pause(DEVICE_ID)

    assert names(sink)[-1] == "confirmation_unexpected_error"
    assert names(sink).count("send_attempt") == 1
    assert "sensitive receiver failure" not in repr(sink.events())


# The confirmation hold (acceptance only) ------------------------------------------------------


@dataclass
class ScriptedGate:
    """A hold that answers `outcome` (or raises `error`), optionally letting time pass."""

    outcome: HoldOutcome = field(default_factory=lambda: HoldOutcome(True, "a" * 32))
    error: BaseException | None = None
    elapse: Callable[[], None] = lambda: None
    requests: list[HoldRequest] = field(default_factory=list)

    def hold(self, request: HoldRequest) -> HoldOutcome:
        self.requests.append(request)
        self.elapse()
        if self.error is not None:
            raise self.error
        return self.outcome


def held_service(sink: MemoryTraceSink, gate: ScriptedGate, clock: FakeClock) -> ControlService:
    transport = FakeTransport()
    transport.clock = clock
    return ControlService(
        transport,
        trace=recorder(sink),
        gate=gate,
        confirm_timeout=2,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )


def test_a_released_hold_precedes_every_confirmation_read_with_a_fresh_budget() -> None:
    sink, clock = MemoryTraceSink(), FakeClock()
    gate = ScriptedGate(elapse=lambda: clock.sleep(10))  # longer than the whole budget

    result = held_service(sink, gate, clock).pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert names(sink) == [
        "command_prepared",
        "pre_status",
        "send_attempt",
        "transport_response",
        "confirmation_held",
        "confirmation_released",
        "confirmation_status",
        "command_confirmed",
    ]
    released = next(e for e in sink.events() if e.event == "confirmation_released")
    assert released.token == "a" * 32
    assert gate.requests == [HoldRequest("request-0", Command.PAUSE, 1)]


@pytest.mark.parametrize(
    ("gate", "event"),
    [
        (ScriptedGate(outcome=HoldOutcome(False)), "confirmation_hold_timeout"),
        (ScriptedGate(error=OSError("gate directory gone")), "confirmation_hold_failed"),
    ],
    ids=["timeout", "error"],
)
def test_a_hold_that_is_not_released_never_blocks_or_changes_the_command(
    gate: ScriptedGate, event: str
) -> None:
    sink, clock = MemoryTraceSink(), FakeClock()

    result = held_service(sink, gate, clock).pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert event in names(sink) and names(sink).count("send_attempt") == 1


def test_an_interrupted_hold_propagates_without_a_resend() -> None:
    sink, clock = MemoryTraceSink(), FakeClock()
    gate = ScriptedGate(error=KeyboardInterrupt())
    service = held_service(sink, gate, clock)

    with pytest.raises(KeyboardInterrupt):
        service.pause(DEVICE_ID)
    assert names(sink).count("send_attempt") == 1


def test_no_hold_without_a_gate_or_without_confirmation() -> None:
    sink = MemoryTraceSink()
    gate = ScriptedGate()
    ControlService(FakeTransport(), trace=recorder(sink), gate=gate, confirm_timeout=None).pause(
        DEVICE_ID
    )
    ControlService(FakeTransport(), trace=recorder(sink)).pause(DEVICE_ID)

    assert gate.requests == [] and "confirmation_held" not in names(sink)
    assert "confirmation_skipped" in names(sink)
