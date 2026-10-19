"""C6/C7 evidence rules and their end-to-end simulation; no network or device is used.

A PASS here is simulated, application-side evidence: never a hardware result.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import itertools
import sys
import threading
from pathlib import Path
from types import ModuleType

import pytest

from control_tv.acceptance_observer import IndependentObserver, ObserverOutcome
from control_tv.command_trace import (
    MemoryTraceSink,
    TraceConfigurationError,
    TraceEvent,
    TraceRecorder,
    TraceRole,
    application_request,
)
from control_tv.domain import Command, OperationTimeoutError
from control_tv.service import ControlService
from control_tv.trace_files import FileConfirmationGate, JsonLinesTraceSink
from fakes import DEVICE_ID, FakeClock, FakeTransport

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
TOKEN = "0123456789abcdef0123456789abcdef"


def load(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load("acceptance")
evidence = load("acceptance_trace_evidence")
google = load("acceptance_google_tv")
cli = load("validate_command_trace")

Step = tuple[str, int | None] | tuple[str, int | None, str]


def stream(
    client: str, steps: list[Step], *, first_sequence: int = 1, request_id: str = "r1"
) -> list[TraceEvent]:
    """One client's events for request `r1`, with contiguous indexes and local sequences."""
    events = []
    for index, step in enumerate(steps):
        token = step[2] if len(step) == 3 else None
        events.append(
            TraceEvent(
                sequence=first_sequence + index,
                client=client,
                event=step[0],
                request_id=request_id,
                index=index,
                media_session_id=step[1],
                command=None if client == "observer" else "pause",
                token=token,
            )
        )
    return events


REMOTE: list[Step] = [
    ("command_prepared", None),
    ("pre_status", 10),
    ("send_attempt", 10),
    ("transport_response", 10),
    ("confirmation_held", 10),
    ("confirmation_released", 10, TOKEN),
    ("confirmation_status", 11),
    ("command_unconfirmed", 11),
]
OBSERVER: list[Step] = [
    ("observer_hold_seen", 10),
    ("independent_session_change", 11),
    ("observer_release_sent", None, TOKEN),
]


def c6(remote: list[Step] = REMOTE, observer: list[Step] | None = OBSERVER) -> tuple[bool, str]:
    events = stream("ubuntu", remote)
    if observer is not None:
        events += stream("observer", observer)
    valid, reason = evidence.validate_c6(events, "r1")
    return bool(valid), str(reason)


def replace(steps: list[Step], name: str, new: Step) -> list[Step]:
    return [new if step[0] == name else step for step in steps]


# C6 ---------------------------------------------------------------------------------------


def test_c6_accepts_causally_linked_independent_evidence() -> None:
    valid, reason = c6()
    assert valid is True and "independent observer" in reason
    assert google.validate_c6 is evidence.validate_c6  # the runner's name for it


def test_c6_ignores_the_clocks_of_other_devices() -> None:
    # The observer's sequence numbers come from another machine: far before or after.
    for first in (1, 10**15):
        events = stream("ubuntu", REMOTE) + stream("observer", OBSERVER, first_sequence=first)
        assert evidence.validate_c6(events, "r1")[0] is True


def test_c6_reproduces_and_now_rejects_the_6fa6970_false_pass() -> None:
    """The audit's probe: the session switches BEFORE the send. At 6fa6970 the product's own
    confirmation read produced `session_changed` and the validator answered PASS."""
    sink = MemoryTraceSink()
    clock = FakeClock()
    transport = FakeTransport(ignore_commands=True)
    transport.clock = clock
    original = transport.pause

    def switch_then_pause(device_id: object, media_session_id: int) -> None:
        transport.tv.media_session_id = 2  # the switch reaches the TV before the command
        original(DEVICE_ID, media_session_id)

    transport.pause = switch_then_pause  # type: ignore[method-assign]
    ControlService(
        transport,
        trace=TraceRecorder(sink, role=TraceRole.UBUNTU, request_id_factory=lambda: "r1"),
        confirm_timeout=1.0,
        clock=clock.monotonic,
        sleep=clock.sleep,
    ).pause(DEVICE_ID)

    valid, reason = evidence.validate_c6(sink.events(), "r1")
    assert valid is False and "no independent observer" in reason


@pytest.mark.parametrize(
    ("remote", "observer", "message"),
    [
        (REMOTE, None, "no independent observer"),
        (
            REMOTE,
            [("observer_hold_seen", 11), ("observer_session_already_changed", 11)],
            "already changed",
        ),
        (REMOTE, replace(OBSERVER, "observer_hold_seen", ("observer_hold_seen", 11)), "precede"),
        (
            REMOTE,
            replace(OBSERVER, "independent_session_change", ("independent_session_change", 10)),
            "different mediaSessionId",
        ),
        (
            REMOTE,
            replace(OBSERVER, "observer_release_sent", ("observer_release_sent", None, "f" * 32)),
            "not released by the observer's proof",
        ),
        (
            [*REMOTE[:5], ("confirmation_status", 10), *REMOTE[5:]],
            OBSERVER,
            "read before the observer released it",
        ),
        (
            replace(REMOTE, "confirmation_released", ("confirmation_hold_timeout", 10)),
            OBSERVER,
            "hold was not released",
        ),
        (
            replace(REMOTE, "send_attempt", ("send_attempt", 11)),
            OBSERVER,
            "not bound to the captured",
        ),
        (
            replace(REMOTE, "command_unconfirmed", ("command_confirmed", 11)),
            OBSERVER,
            "confirmed on a session other",
        ),
        (
            [REMOTE[0], REMOTE[2], REMOTE[1], *REMOTE[3:]],
            OBSERVER,
            "not in the order",
        ),
        (
            REMOTE,
            [OBSERVER[1], OBSERVER[0], OBSERVER[2]],
            "observer events are not in the order",
        ),
        (REMOTE[:-1], OBSERVER, "no single final outcome"),
        (
            [step for step in REMOTE if step[0] != "confirmation_held"],
            OBSERVER,
            "missing confirmation_held",
        ),
    ],
    ids=[
        "no-observer",
        "changed-before-hold",
        "switch-before-send",
        "no-new-session",
        "foreign-release",
        "early-confirmation",
        "hold-timeout",
        "retargeted-send",
        "confirmed-new-session",
        "remote-order",
        "observer-order",
        "no-outcome",
        "no-hold",
    ],
)
def test_c6_never_passes_without_complete_causal_proof(
    remote: list[Step], observer: list[Step] | None, message: str
) -> None:
    valid, reason = c6(remote, observer)
    assert valid is False and message in reason


def test_c6_rejects_a_gap_a_lost_event_two_remotes_or_two_commands() -> None:
    events = stream("ubuntu", REMOTE) + stream("observer", OBSERVER)
    gap = [event for event in events if event.event != "send_attempt"]
    assert "not contiguous" in evidence.validate_c6(gap, "r1")[1]

    lost = stream("ubuntu", [*REMOTE[:3], ("trace_incomplete", None), *REMOTE[3:]])
    assert "trace_incomplete" in evidence.validate_c6(lost + stream("observer", OBSERVER), "r1")[1]

    both = events + stream("android", REMOTE)
    assert "exactly one remote" in evidence.validate_c6(both, "r1")[1]

    mixed = [*events[:2], TraceEvent(99, "ubuntu", "x", "r1", 2, None, "play"), *events[3:]]
    assert evidence.validate_c6(mixed, "r1")[0] is False

    disordered = [
        TraceEvent(sequence=5 - index, client="ubuntu", event=name, request_id="r1", index=index)
        for index, name in enumerate(("command_prepared", "pre_status"))
    ]
    assert "sequence contradicts" in (evidence.integrity_problem(disordered, terminal=False) or "")

    commands = [
        TraceEvent(e.sequence, e.client, e.event, e.request_id, e.index, e.media_session_id, "play")
        if e.index == 2
        else e
        for e in stream("ubuntu", REMOTE)
    ]
    assert (
        "one identifiable command"
        in evidence.validate_c6(commands + stream("observer", OBSERVER), "r1")[1]
    )


def test_c6_two_concurrent_requests_cannot_exchange_sessions_or_tokens() -> None:
    token_a, token_b = "a" * 32, "b" * 32
    remote_a = replace(REMOTE, "confirmation_released", ("confirmation_released", 10, token_a))
    observer_a = replace(
        OBSERVER, "observer_release_sent", ("observer_release_sent", None, token_a)
    )

    def other_sessions(steps: list[Step]) -> list[Step]:
        changed: list[Step] = []
        for step in steps:
            session = 20 if step[1] == 10 else 21 if step[1] == 11 else step[1]
            changed.append((step[0], session, step[2]) if len(step) == 3 else (step[0], session))
        return changed

    remote_b = other_sessions(REMOTE)
    remote_b = replace(remote_b, "confirmation_released", ("confirmation_released", 20, token_b))
    observer_b = other_sessions(OBSERVER)
    observer_b = replace(
        observer_b, "observer_release_sent", ("observer_release_sent", None, token_b)
    )
    events = (
        stream("ubuntu", remote_a, request_id="request-a")
        + stream("observer", observer_a, request_id="request-a")
        + stream("android", remote_b, first_sequence=10_000, request_id="request-b")
        + stream("observer", observer_b, first_sequence=20_000, request_id="request-b")
    )

    assert evidence.validate_c6(events, "request-a")[0] is True
    assert evidence.validate_c6(events, "request-b")[0] is True

    wrong = [
        TraceEvent(
            event.sequence,
            event.client,
            event.event,
            event.request_id,
            event.index,
            event.media_session_id,
            event.command,
            event.error_code,
            (
                token_b
                if event.request_id == "request-a" and event.event == "confirmation_released"
                else event.token
            ),
        )
        for event in events
    ]
    valid, reason = evidence.validate_c6(wrong, "request-a")
    assert valid is False and "observer's proof" in reason


# The C6 simulation, end to end ------------------------------------------------------------


def run_c6_simulation(tmp_path: Path, *, switch_before_send: bool) -> tuple[bool, str]:
    """Ubuntu pauses with the file hold on; the observer, in its own thread and trace, sees the
    hold, then the owner's new video (simulated), then releases. Real files, real time."""
    gate_dir = tmp_path / "gate"
    remote_trace = tmp_path / "remote" / "trace.jsonl"
    observer_trace = tmp_path / "observer" / "trace.jsonl"
    transport = FakeTransport(ignore_commands=True)
    reads = itertools.count()

    def read_session() -> int | None:
        if next(reads) == 1:
            transport.tv.media_session_id = 2  # the owner starts another video now
        return transport.tv.media_session_id

    if switch_before_send:
        original = transport.pause

        def switch_then_pause(device_id: object, media_session_id: int) -> None:
            transport.tv.media_session_id = 2
            original(DEVICE_ID, media_session_id)

        transport.pause = switch_then_pause  # type: ignore[method-assign]

    observer = IndependentObserver(
        gate_dir,
        read_session,
        TraceRecorder(JsonLinesTraceSink(observer_trace), role=TraceRole.OBSERVER),
        timeout_seconds=5,
        poll_seconds=0.01,
    )
    outcomes: list[ObserverOutcome] = []
    thread = threading.Thread(target=lambda: outcomes.append(observer.observe()))
    thread.start()
    ControlService(
        transport,
        trace=TraceRecorder(
            JsonLinesTraceSink(remote_trace), role=TraceRole.UBUNTU, request_id_factory=lambda: "r1"
        ),
        gate=FileConfirmationGate(gate_dir, timeout_seconds=1, poll_seconds=0.01),
        confirm_timeout=0.2,
    ).pause(DEVICE_ID)
    thread.join(5)
    assert transport.attempted() == ["pause"]  # never resent, whatever the outcome
    events = evidence.load_traces([remote_trace, observer_trace])
    valid, reason = evidence.validate_c6(events, "r1")
    return bool(valid), str(reason)


def test_simulated_c6_passes_when_the_switch_follows_the_send(tmp_path: Path) -> None:
    valid, reason = run_c6_simulation(tmp_path, switch_before_send=False)
    assert valid is True, reason


def test_simulated_c6_fails_when_the_switch_precedes_the_send(tmp_path: Path) -> None:
    valid, reason = run_c6_simulation(tmp_path, switch_before_send=True)
    assert valid is False
    assert "already changed" in reason or "precede" in reason


# C7 ---------------------------------------------------------------------------------------


def c7_trace(sink: object) -> TraceRecorder:
    return TraceRecorder(sink, role=TraceRole.ANDROID, request_id_factory=lambda: "r1")  # type: ignore[arg-type]


def test_product_trace_proves_one_ambiguous_attempt_for_c7() -> None:
    sink = MemoryTraceSink()
    transport = FakeTransport(
        fail_commands_with=OperationTimeoutError("answer lost", device_id=DEVICE_ID)
    )

    with application_request("app-request-1"), pytest.raises(OperationTimeoutError):
        ControlService(transport, trace=c7_trace(sink)).pause(DEVICE_ID)

    valid, reason = evidence.validate_c7(sink.events(), "r1")
    assert valid is True and "no application replay" in reason
    assert transport.attempted() == ["pause"]


def test_c7_rejects_a_replay_whose_evidence_was_lost() -> None:
    """The audit's second probe: a second send_attempt whose write fails used to vanish."""

    class DropSecondAttempt:
        def __init__(self) -> None:
            self.kept: list[TraceEvent] = []
            self.attempts = 0

        def write(self, event: TraceEvent) -> None:
            if event.event == "send_attempt":
                self.attempts += 1
                if self.attempts == 2:
                    raise OSError("disk full")
            self.kept.append(event)

    sink = DropSecondAttempt()
    trace = c7_trace(sink).command(Command.PAUSE)
    trace.record("send_attempt")
    trace.record("send_attempt")  # a replay, lost
    trace.record("command_failed")
    trace.record("ambiguous_result")

    valid, reason = evidence.validate_c7(tuple(sink.kept), "r1")
    assert valid is False and ("not contiguous" in reason or "trace_incomplete" in reason)


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([("command_prepared", None), ("ambiguous_result", None)], "exactly one"),
        (
            [
                ("command_prepared", None),
                ("send_attempt", 1),
                ("send_attempt", 1),
                ("ambiguous_result", 1),
            ],
            "exactly one",
        ),
        (
            [
                ("command_prepared", None),
                ("send_attempt", 1),
                ("automatic_retry", 1),
                ("ambiguous_result", 1),
            ],
            "retry or replay",
        ),
        ([("command_prepared", None), ("send_attempt", 1), ("not_sent", 1)], "ambiguous"),
        (
            [
                ("command_prepared", None),
                ("send_attempt", 1),
                ("transport_response", 1),
                ("ambiguous_result", 1),
            ],
            "transport answered",
        ),
    ],
    ids=["no-attempt", "two-attempts", "retry", "not-sent", "answered"],
)
def test_c7_never_claims_no_replay_without_complete_evidence(
    steps: list[Step], message: str
) -> None:
    valid, reason = evidence.validate_c7(stream("android", steps), "r1")
    assert valid is False and message in reason


# C7 across bridge invocations: one application request, every attempt it caused ----------


def bridge_pause(service: ControlService, message_id: int, request_id: str | None) -> object:
    from control_tv import bridge

    request: dict[str, object] = {
        "id": message_id,
        "method": "pause",
        "params": {"deviceId": str(DEVICE_ID)},
    }
    if request_id is not None:
        request["requestId"] = request_id
    return bridge.dispatch(service, request)  # type: ignore[arg-type]


def ambiguous_service(sink: MemoryTraceSink) -> tuple[ControlService, FakeTransport]:
    transport = FakeTransport(
        fail_commands_with=OperationTimeoutError("answer lost", device_id=DEVICE_ID)
    )
    recorder = TraceRecorder(sink, role=TraceRole.UBUNTU)  # a fresh trace id per invocation
    return ControlService(transport, trace=recorder), transport


def trace_ids(sink: MemoryTraceSink) -> list[str]:
    return list(dict.fromkeys(event.request_id for event in sink.events()))


def test_c7_fails_when_a_new_bridge_invocation_reissues_the_same_request() -> None:
    """Regression (Codex P1 on 7bf2e77): each invocation got its own trace id, so validating the
    first one saw one send and an ambiguous result and passed C7 although the same application
    request had been sent again by a second invocation."""
    sink = MemoryTraceSink()
    service, transport = ambiguous_service(sink)

    bridge_pause(service, 1, "app-request-1")
    bridge_pause(service, 2, "app-request-1")  # the same request, re-issued

    assert transport.attempted() == ["pause", "pause"]
    first, second = trace_ids(sink)
    for request_id in (first, second):
        valid, reason = evidence.validate_c7(sink.events(), request_id)
        assert valid is False and "sent 2 times" in reason


def test_c7_passes_for_one_correlated_invocation_and_keeps_distinct_requests_apart() -> None:
    sink = MemoryTraceSink()
    service, _ = ambiguous_service(sink)

    bridge_pause(service, 1, "app-request-1")
    bridge_pause(service, 2, "app-request-2")  # a distinct, deliberate request

    for request_id in trace_ids(sink):
        valid, reason = evidence.validate_c7(sink.events(), request_id)
        assert valid is True and "no application replay" in reason
    assert {event.application_request_id for event in sink.events()} == {
        "app-request-1",
        "app-request-2",
    }


def test_without_a_host_request_id_each_bridge_message_is_its_own_request() -> None:
    sink = MemoryTraceSink()
    service, _ = ambiguous_service(sink)

    bridge_pause(service, 1, None)
    (request_id,) = trace_ids(sink)
    events = sink.events()
    assert len({event.application_request_id for event in events}) == 1
    assert events[0].application_request_id is not None
    assert evidence.validate_c7(events, request_id)[0] is True


@pytest.mark.parametrize("request_id", ["", "a b", "../x", "x" * 65, 7])
def test_a_malformed_host_request_id_is_refused_before_any_command(request_id: object) -> None:
    sink = MemoryTraceSink()
    service, transport = ambiguous_service(sink)

    response = bridge_pause(service, 1, request_id)  # type: ignore[arg-type]

    assert response["ok"] is False and "requestId" in response["error"]["message"]  # type: ignore[index]
    assert transport.attempted() == [] and sink.events() == ()


def test_c7_never_passes_for_a_trace_with_no_application_request() -> None:
    """A trace recorded outside any application request cannot prove the absence of replay."""
    sink = MemoryTraceSink()
    transport = FakeTransport(
        fail_commands_with=OperationTimeoutError("answer lost", device_id=DEVICE_ID)
    )
    with pytest.raises(OperationTimeoutError):
        ControlService(transport, trace=c7_trace(sink)).pause(DEVICE_ID)

    valid, reason = evidence.validate_c7(sink.events(), "r1")
    assert valid is False and "not correlated to an application request" in reason


def test_c6_is_unchanged_by_the_application_request_id() -> None:
    events = [
        dataclasses.replace(event, application_request_id="app-request-1")
        for event in stream("ubuntu", REMOTE) + stream("observer", OBSERVER)
    ]
    assert evidence.validate_c6(events, "r1")[0] is True
    assert c6()[0] is True


# Files and the offline command ------------------------------------------------------------


def test_a_jsonl_round_trip_holds_no_device_data(tmp_path: Path) -> None:
    path = tmp_path / "evidence" / "trace.jsonl"
    trace = TraceRecorder(JsonLinesTraceSink(path), role=TraceRole.UBUNTU).command(Command.PAUSE)
    trace.record("confirmation_released", media_session_id=7, token=TOKEN)

    loaded = evidence.load_trace(path)
    assert [item.event for item in loaded] == ["command_prepared", "confirmation_released"]
    assert loaded[1].token == TOKEN and loaded[1].index == 1
    payload = path.read_text(encoding="utf-8")
    assert "device" not in payload and "http" not in payload


@pytest.mark.parametrize(
    "line",
    [
        '{"sequence":true,"client":"ubuntu","event":"e","request_id":"r","index":0}',
        '{"sequence":1,"client":"ubuntu","event":"e","request_id":"r"}',
        '{"sequence":1,"client":"ubuntu","event":"e","request_id":"r","index":0,"token":"x"}',
        '{"sequence":1,"client":"","event":"e","request_id":"r","index":0}',
        '{"sequence":1,"client":"u","event":"e","request_id":"r","index":0,"media_session_id":-1}',
        "[]",
        "not json",
    ],
)
def test_the_loader_rejects_malformed_events(tmp_path: Path, line: str) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        evidence.load_trace(path)


def test_a_file_marked_incomplete_is_never_evidence(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text("", encoding="utf-8")
    (tmp_path / "trace.jsonl.incomplete").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="marked incomplete"):
        evidence.load_trace(path)


def write_jsonl(path: Path, events: list[TraceEvent]) -> Path:
    sink = JsonLinesTraceSink(path)
    for event in events:
        sink.write(event)
    return path


def test_the_offline_command_needs_no_flag_and_reports_pass_or_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    remote = write_jsonl(tmp_path / "r" / "remote.jsonl", stream("ubuntu", REMOTE))
    observer = write_jsonl(tmp_path / "o" / "observer.jsonl", stream("observer", OBSERVER))

    assert cli.main(["c6", "--request-id", "r1", str(remote), str(observer)]) == 0
    assert capsys.readouterr().out.startswith("PASS: C6")
    assert cli.main(["c6", "--request-id", "r1", str(remote)]) == 2  # no observer: BLOCKED
    assert "BLOCKED: C6 has no independent observer" in capsys.readouterr().out
    assert cli.main(["c7", "--request-id", "r1", str(remote)]) == 1  # answered: a C7 FAIL
    assert capsys.readouterr().out.startswith("FAIL: ")
    assert cli.main(["list", str(remote), str(observer)]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert listed == [
        "r1 observer - 3 events, outcome -",
        "r1 ubuntu pause 8 events, outcome command_unconfirmed",
    ]
    assert cli.main(["list", str(tmp_path / "missing.jsonl")]) == 2
    assert capsys.readouterr().out.startswith("BLOCKED: unusable trace")


def test_the_application_request_id_survives_the_jsonl_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(JsonLinesTraceSink(path), role=TraceRole.ANDROID)
    with application_request("app-request-9"):
        trace = recorder.command(Command.PAUSE)
    trace.record("send_attempt")

    events = evidence.load_trace(path)
    assert {event.application_request_id for event in events} == {"app-request-9"}

    path.write_text(path.read_text().replace("app-request-9", "app request"))
    with pytest.raises(ValueError, match="invalid application request id"):
        evidence.load_trace(path)


@pytest.mark.parametrize("request_id", ["", "a/b", "x" * 65])
def test_an_invalid_application_request_id_is_refused(request_id: str) -> None:
    with (
        pytest.raises(TraceConfigurationError, match="application request id"),
        application_request(request_id),
    ):
        pass  # pragma: no cover - never entered


# Classification: no usable evidence is BLOCKED, a demonstrated violation is FAIL ------------


def correlated(events: list[TraceEvent], application: str = "app-request-1") -> list[TraceEvent]:
    return [dataclasses.replace(event, application_request_id=application) for event in events]


C7_AMBIGUOUS: list[Step] = [
    ("command_prepared", None),
    ("send_attempt", 1),
    ("command_failed", 1),
    ("ambiguous_result", 1),
]


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return int(code), capsys.readouterr().out


@pytest.mark.parametrize("step", ["c6", "c7"])
def test_missing_empty_or_unusable_traces_are_blocked_not_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], step: str
) -> None:
    """Regression (Codex P2): an absent, empty or unusable trace, or an unknown request id, was
    reported FAIL - a hardware failure - although no evidence could be read at all."""
    absent = tmp_path / "absent.jsonl"
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    broken = tmp_path / "broken.jsonl"
    broken.write_text("{not json\n", encoding="utf-8")
    other = write_jsonl(tmp_path / "o" / "other.jsonl", correlated(stream("ubuntu", C7_AMBIGUOUS)))

    for traces, reason in (
        ([absent], "unusable trace"),
        ([broken], "unusable trace"),
        ([empty], "no event for request r1"),
        ([other], "no event for request unknown-id"),
    ):
        request = "unknown-id" if traces == [other] else "r1"
        code, out = run_cli(capsys, step, "--request-id", request, *map(str, traces))
        assert code == 2 and out.startswith("BLOCKED: ") and reason in out


@pytest.mark.parametrize("request_id", ["", "a b", "../r1", "x" * 65])
def test_an_invalid_request_id_is_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], request_id: str
) -> None:
    trace = write_jsonl(tmp_path / "t.jsonl", correlated(stream("ubuntu", C7_AMBIGUOUS)))
    code, out = run_cli(capsys, "c7", "--request-id", request_id, str(trace))
    assert code == 2 and out.startswith("BLOCKED: invalid request id")


def test_incomplete_or_uncorrelated_evidence_is_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gap = correlated(stream("ubuntu", C7_AMBIGUOUS))
    del gap[1]  # an event lost: the per-request index is no longer contiguous
    uncorrelated = stream("ubuntu", C7_AMBIGUOUS)
    no_observer = write_jsonl(tmp_path / "c6.jsonl", stream("ubuntu", REMOTE))

    for name, events in (("gap", gap), ("uncorrelated", uncorrelated)):
        trace = write_jsonl(tmp_path / name / "t.jsonl", events)
        code, out = run_cli(capsys, "c7", "--request-id", "r1", str(trace))
        assert code == 2 and out.startswith("BLOCKED: "), name
    code, out = run_cli(capsys, "c6", "--request-id", "r1", str(no_observer))
    assert code == 2 and "no independent observer" in out


def test_a_demonstrated_violation_still_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    answered = correlated(
        stream(
            "ubuntu",
            [
                ("command_prepared", None),
                ("send_attempt", 1),
                ("transport_response", 1),
                ("command_unconfirmed", 1),
            ],
        )
    )
    replayed = correlated(stream("ubuntu", C7_AMBIGUOUS)) + correlated(
        stream("ubuntu", C7_AMBIGUOUS, first_sequence=100, request_id="r2")
    )
    wrong_session = replace(REMOTE, "send_attempt", ("send_attempt", 99))

    trace = write_jsonl(tmp_path / "a" / "t.jsonl", answered)
    assert run_cli(capsys, "c7", "--request-id", "r1", str(trace))[0] == 1
    trace = write_jsonl(tmp_path / "b" / "t.jsonl", replayed)
    code, out = run_cli(capsys, "c7", "--request-id", "r1", str(trace))
    assert code == 1 and out.startswith("FAIL: ") and "sent 2 times" in out
    remote = write_jsonl(tmp_path / "c" / "r.jsonl", stream("ubuntu", wrong_session))
    observer = write_jsonl(tmp_path / "c" / "o.jsonl", stream("observer", OBSERVER))
    code, out = run_cli(capsys, "c6", "--request-id", "r1", str(remote), str(observer))
    assert code == 1 and "captured mediaSessionId" in out


def test_the_classification_functions_tell_blocked_from_fail() -> None:
    blocked, reason = evidence.classify_c7((), "r1")
    assert blocked is evidence.Classification.BLOCKED and "no event" in reason
    fail, _ = evidence.classify_c7(
        correlated(stream("ubuntu", C7_AMBIGUOUS))
        + correlated(stream("ubuntu", C7_AMBIGUOUS, first_sequence=100, request_id="r2")),
        "r1",
    )
    assert fail is evidence.Classification.FAIL
    passed, _ = evidence.classify_c6(stream("ubuntu", REMOTE) + stream("observer", OBSERVER), "r1")
    assert passed is evidence.Classification.PASS
    assert evidence.validate_c7((), "r1")[0] is False  # the boolean API is unchanged
