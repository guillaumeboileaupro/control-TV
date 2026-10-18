"""The independent C6 observer: read-only, bounded, and never releasing without proof."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from control_tv.acceptance_observer import IndependentObserver, ObserverOutcome
from control_tv.command_trace import (
    MemoryTraceSink,
    TraceConfigurationError,
    TraceRecorder,
    TraceRole,
)
from control_tv.domain import OperationTimeoutError
from control_tv.trace_files import private_directory

TOKEN = "fedcba9876543210fedcba9876543210"


class Ticks:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def hold(directory: Path, session: int = 7) -> Path:
    marker = private_directory(directory) / "request-1.held"
    marker.write_text(json.dumps({"request_id": "request-1", "media_session_id": session}))
    return directory


def reader(*answers: int | Exception | None) -> Callable[[], int | None]:
    sequence: Iterator[int | Exception | None] = iter(answers)

    def read() -> int | None:
        answer = next(sequence)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return read


def observer(
    directory: Path,
    read: Callable[[], int | None],
    sink: MemoryTraceSink,
    ticks: Ticks,
    seen: list[str] | None = None,
    request_id: str | None = None,
    token: str = TOKEN,
) -> IndependentObserver:
    return IndependentObserver(
        directory,
        read,
        TraceRecorder(sink, role=TraceRole.OBSERVER),
        timeout_seconds=1,
        poll_seconds=0.1,
        clock=ticks.clock,
        sleep=ticks.sleep,
        token_factory=lambda: token,
        on_hold_seen=lambda: (seen if seen is not None else []).append("now"),
        request_id=request_id,
    )


def test_the_observer_proves_the_change_then_releases_with_its_token(tmp_path: Path) -> None:
    sink, ticks = MemoryTraceSink(), Ticks()
    gate = hold(tmp_path / "gate")
    read = reader(7, OperationTimeoutError("lost", device_id=None), 7, 8)

    prompts: list[str] = []
    outcome = observer(gate, read, sink, ticks, prompts).observe()

    assert outcome is ObserverOutcome.RELEASED and prompts == ["now"]
    assert [(e.event, e.media_session_id, e.token) for e in sink.events()] == [
        ("observer_hold_seen", 7, None),
        ("observer_read_failed", None, None),
        ("independent_session_change", 8, None),
        ("observer_release_sent", None, TOKEN),
    ]
    assert (gate / "request-1.release").read_text().strip() == TOKEN
    assert {e.client for e in sink.events()} == {"observer"}


def test_a_session_already_changed_at_the_hold_is_never_released(tmp_path: Path) -> None:
    sink, ticks = MemoryTraceSink(), Ticks()
    gate = hold(tmp_path / "gate", session=7)

    outcome = observer(gate, reader(8), sink, ticks).observe()

    assert outcome is ObserverOutcome.CHANGED_BEFORE_HOLD
    assert [e.event for e in sink.events()] == [
        "observer_hold_seen",
        "observer_session_already_changed",
    ]
    assert not (gate / "request-1.release").exists()


def test_no_hold_or_no_change_times_out_without_release(tmp_path: Path) -> None:
    sink, ticks = MemoryTraceSink(), Ticks()
    empty = private_directory(tmp_path / "empty")
    assert observer(empty, reader(), sink, ticks).observe() is ObserverOutcome.TIMEOUT
    assert sink.events() == ()

    gate = hold(tmp_path / "gate")
    outcome = observer(gate, reader(*([7] * 20)), sink, Ticks()).observe()
    assert outcome is ObserverOutcome.TIMEOUT
    assert [e.event for e in sink.events()][-1] == "observer_timeout"
    assert not (gate / "request-1.release").exists()


def test_an_unreadable_receiver_before_the_first_reading_times_out(tmp_path: Path) -> None:
    sink = MemoryTraceSink()
    gate = hold(tmp_path / "gate")
    failures = [OperationTimeoutError("lost", device_id=None)] * 20

    outcome = observer(gate, reader(*failures), sink, Ticks()).observe()

    assert outcome is ObserverOutcome.TIMEOUT
    assert {e.event for e in sink.events()} == {"observer_read_failed", "observer_timeout"}


def test_the_observer_needs_its_own_active_role() -> None:
    for recorder in (TraceRecorder(), TraceRecorder(MemoryTraceSink(), role=TraceRole.UBUNTU)):
        with pytest.raises(TraceConfigurationError, match="own role"):
            IndependentObserver(Path("."), reader(), recorder)


def test_two_observers_target_distinct_simultaneous_holds(tmp_path: Path) -> None:
    gate = private_directory(tmp_path / "gate")
    markers = (("request-a", 17), ("request-b", 29))
    for request_id, session_id in markers:
        (gate / f"{request_id}.held").write_text(
            json.dumps({"request_id": request_id, "media_session_id": session_id})
        )

    sinks = {request_id: MemoryTraceSink() for request_id, _ in markers}
    tokens = {"request-a": "a" * 32, "request-b": "b" * 32}
    outcomes = {
        request_id: observer(
            gate,
            reader(session_id, session_id + 1),
            sinks[request_id],
            Ticks(),
            request_id=request_id,
            token=tokens[request_id],
        ).observe()
        for request_id, session_id in reversed(markers)
    }

    assert outcomes == {
        "request-a": ObserverOutcome.RELEASED,
        "request-b": ObserverOutcome.RELEASED,
    }
    for request_id, session_id in markers:
        events = sinks[request_id].events()
        assert {event.request_id for event in events} == {request_id}
        assert [event.media_session_id for event in events[:2]] == [session_id, session_id + 1]
        assert (gate / f"{request_id}.release").read_text().strip() == tokens[request_id]


# P2: the real C6 script reaches the request filter; an ambiguous gate is refused --------------


def two_holds(directory: Path) -> Path:
    gate = private_directory(directory)
    for request_id, session in (("request-a", 17), ("request-b", 29)):
        (gate / f"{request_id}.held").write_text(
            json.dumps({"request_id": request_id, "media_session_id": session})
        )
    return gate


def test_without_a_request_id_several_held_commands_are_refused_not_guessed(
    tmp_path: Path,
) -> None:
    """Regression: the observer took the first held command alphabetically, so beside another
    remote's hold it could observe and release the wrong request."""
    gate = two_holds(tmp_path / "gate")
    sink = MemoryTraceSink()

    with pytest.raises(TraceConfigurationError, match="2 commands are held: name one"):
        observer(gate, reader(17, 18), sink, Ticks()).observe()
    assert sink.events() == () and not list(gate.glob("*.release"))


def test_a_request_id_selects_its_hold_among_several(tmp_path: Path) -> None:
    gate = two_holds(tmp_path / "gate")
    sink = MemoryTraceSink()

    outcome = observer(gate, reader(29, 30), sink, Ticks(), request_id="request-b").observe()

    assert outcome is ObserverOutcome.RELEASED
    assert {event.request_id for event in sink.events()} == {"request-b"}
    assert [path.name for path in gate.glob("*.release")] == ["request-b.release"]


@pytest.mark.parametrize("request_id", ["", "../x", "a b", "x" * 65])
def test_a_request_id_that_cannot_name_a_hold_is_refused(tmp_path: Path, request_id: str) -> None:
    with pytest.raises(TraceConfigurationError, match="request id"):
        observer(tmp_path, reader(), MemoryTraceSink(), Ticks(), request_id=request_id)


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "acceptance_observer_script", Path(__file__).parents[2] / "scripts/acceptance_observer.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_c6_script_passes_the_request_id_to_the_observer(tmp_path: Path) -> None:
    script = load_script()
    gate = two_holds(tmp_path / "gate")
    arguments = script.parse_arguments(
        ["--gate", str(gate), "--trace", str(tmp_path / "o.jsonl"), "--request-id", "request-a"]
    )
    assert arguments.request_id == "request-a" and arguments.timeout == 60.0

    sink = MemoryTraceSink()
    built = script.build_observer(
        arguments, reader(17, 18), TraceRecorder(sink, role=TraceRole.OBSERVER), lambda: None
    )
    built._clock, built._sleep, built._poll = Ticks().clock, (lambda seconds: None), 0.1

    assert built.observe() is ObserverOutcome.RELEASED
    assert {event.request_id for event in sink.events()} == {"request-a"}

    unfiltered = script.parse_arguments(["--gate", str(gate), "--trace", "t.jsonl"])
    assert unfiltered.request_id is None


@pytest.mark.parametrize("request_id", ["", "../x", "a b"])
def test_the_c6_script_refuses_a_malformed_request_id(request_id: str) -> None:
    with pytest.raises(SystemExit):
        load_script().parse_arguments(
            ["--gate", "g", "--trace", "t.jsonl", "--request-id", request_id]
        )
