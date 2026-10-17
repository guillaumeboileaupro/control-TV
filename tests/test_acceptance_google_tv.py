"""C6/C7 evidence rules and their end-to-end simulation; no network or device is used.

A PASS here is simulated, application-side evidence: never a hardware result.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import threading
from pathlib import Path
from types import ModuleType

import pytest

from control_tv.acceptance_observer import IndependentObserver, ObserverOutcome
from control_tv.command_trace import MemoryTraceSink, TraceEvent, TraceRecorder, TraceRole
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


def stream(client: str, steps: list[Step], *, first_sequence: int = 1) -> list[TraceEvent]:
    """One client's events for request `r1`, with contiguous indexes and local sequences."""
    events = []
    for index, step in enumerate(steps):
        token = step[2] if len(step) == 3 else None
        events.append(
            TraceEvent(
                sequence=first_sequence + index,
                client=client,
                event=step[0],
                request_id="r1",
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

    with pytest.raises(OperationTimeoutError):
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
    assert cli.main(["c6", "--request-id", "r1", str(remote)]) == 1
    assert "no independent observer" in capsys.readouterr().out
    assert cli.main(["c7", "--request-id", "r1", str(remote)]) == 1
    capsys.readouterr()
    assert cli.main(["list", str(remote), str(observer)]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert listed == [
        "r1 observer - 3 events, outcome -",
        "r1 ubuntu pause 8 events, outcome command_unconfirmed",
    ]
    assert cli.main(["list", str(tmp_path / "missing.jsonl")]) == 1
    assert "unusable trace" in capsys.readouterr().out
