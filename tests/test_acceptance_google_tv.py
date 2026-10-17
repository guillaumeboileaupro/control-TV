"""Instrumented C6/C7 evidence rules; no network or device is used."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from control_tv.command_trace import JsonLinesTraceSink, MemoryTraceSink, TraceRecorder
from control_tv.domain import Command, OperationTimeoutError
from control_tv.service import ControlService
from fakes import DEVICE_ID, FakeClock, FakeTransport

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if "acceptance" not in sys.modules:
    load("acceptance")
google = load("acceptance_google_tv")


def event(sequence: int, name: str, session: int | None = None) -> object:
    return google.TraceEvent(sequence, "ubuntu", name, "request-1", session, "pause")


def test_c6_accepts_ordered_session_bound_evidence() -> None:
    valid, reason = google.validate_c6(
        [
            event(1, "pre_status", 10),
            event(2, "send_attempt", 10),
            event(3, "session_changed", 11),
            event(4, "confirmation_status", 11),
        ],
        "request-1",
    )
    assert valid is True
    assert "captured session" in reason


def test_c6_accepts_confirmation_consistently_bound_to_captured_session() -> None:
    valid, _ = google.validate_c6(
        [
            event(1, "pre_status", 10),
            event(2, "send_attempt", 10),
            event(3, "session_changed", 11),
            event(4, "confirmation_status", 10),
            event(5, "command_confirmed", 10),
        ],
        "request-1",
    )
    assert valid is True


@pytest.mark.parametrize(
    "events",
    [
        [
            event(1, "pre_status", 10),
            event(2, "send_attempt", 11),
            event(3, "session_changed", 11),
            event(4, "confirmation_status", 11),
        ],
        [
            event(1, "send_attempt", 10),
            event(2, "pre_status", 10),
            event(3, "session_changed", 11),
            event(4, "confirmation_status", 11),
        ],
        [
            event(1, "pre_status", 10),
            event(2, "send_attempt", 10),
            event(3, "session_changed", 10),
            event(4, "confirmation_status", 10),
        ],
        [event(1, "pre_status", 10), event(2, "send_attempt", 10)],
        [
            event(1, "pre_status", 10),
            event(2, "send_attempt", 10),
            event(2, "session_changed", 11),
            event(4, "confirmation_status", 11),
        ],
        [
            event(1, "pre_status", 10),
            event(2, "send_attempt", 10),
            event(3, "session_changed", 11),
            event(4, "confirmation_status", 11),
            event(5, "command_confirmed", 11),
        ],
        [
            event(1, "pre_status", 10),
            event(2, "send_attempt", 10),
            event(3, "session_changed", 11),
            event(4, "confirmation_status", 10),
            event(5, "command_confirmed", 11),
        ],
        [
            event(1, "pre_status", 10),
            event(2, "send_attempt", 10),
            event(3, "command_confirmed", 10),
            event(4, "session_changed", 11),
            event(5, "confirmation_status", 10),
        ],
    ],
)
def test_c6_rejects_incomplete_retargeted_or_unordered_evidence(events: list[object]) -> None:
    assert google.validate_c6(events, "request-1")[0] is False


def test_c7_accepts_exactly_one_send_attempt_without_replay() -> None:
    valid, reason = google.validate_c7(
        [event(1, "send_attempt", 10), event(2, "ambiguous_result", 10)], "request-1"
    )
    assert valid is True
    assert "one local send attempt" in reason


@pytest.mark.parametrize(
    "events",
    [
        [event(1, "ambiguous_result")],
        [event(1, "send_attempt"), event(2, "send_attempt"), event(3, "ambiguous_result")],
        [event(1, "send_attempt"), event(2, "automatic_retry"), event(3, "ambiguous_result")],
        [event(1, "send_attempt")],
        [event(1, "ambiguous_result"), event(2, "send_attempt")],
        [event(1, "send_attempt"), event(2, "ambiguous_result"), event(3, "ambiguous_result")],
        [
            event(1, "send_attempt"),
            google.TraceEvent(2, "ubuntu", "ambiguous_result", "request-1", 10, "play"),
        ],
    ],
)
def test_c7_never_claims_no_replay_without_complete_instrumentation(events: list[object]) -> None:
    assert google.validate_c7(events, "request-1")[0] is False


def test_product_trace_proves_c6_without_confirming_the_replacement_session() -> None:
    sink = MemoryTraceSink()
    trace = TraceRecorder(
        sink,
        client="ubuntu",
        request_id_factory=lambda: "request-1",
        sequence_factory=iter(range(1, 100)).__next__,
    )
    clock = FakeClock()
    transport = FakeTransport(ignore_commands=True, clock=clock)
    transport.status_effects = [
        lambda: None,
        lambda: setattr(transport.tv, "media_session_id", 2),
    ]

    ControlService(
        transport,
        trace=trace,
        confirm_timeout=1.0,
        clock=clock.monotonic,
        sleep=clock.sleep,
    ).pause(DEVICE_ID)

    assert google.validate_c6(sink.events(), "request-1")[0] is True


def test_product_trace_proves_one_ambiguous_attempt_for_c7() -> None:
    sink = MemoryTraceSink()
    trace = TraceRecorder(
        sink,
        client="ubuntu",
        request_id_factory=lambda: "request-1",
        sequence_factory=iter(range(1, 100)).__next__,
    )
    transport = FakeTransport(
        fail_commands_with=OperationTimeoutError("answer lost", device_id=DEVICE_ID)
    )

    with pytest.raises(OperationTimeoutError):
        ControlService(transport, trace=trace).pause(DEVICE_ID)

    assert google.validate_c7(sink.events(), "request-1")[0] is True
    assert transport.attempted() == ["pause"]


def test_jsonl_trace_round_trip_contains_no_device_or_command_arguments(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(
        JsonLinesTraceSink(path),
        client="ubuntu",
        request_id_factory=lambda: "request-1",
        sequence_factory=lambda: 1,
    )

    command = recorder.command(Command.PAUSE)
    command.record("send_attempt", media_session_id=7)

    loaded = google.load_trace(path)
    assert [item.event for item in loaded] == ["command_prepared", "send_attempt"]
    payload = path.read_text(encoding="utf-8")
    assert "device" not in payload
    assert "media.local" not in payload


def test_trace_loader_rejects_malformed_or_boolean_identifiers(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(
        '{"sequence":true,"client":"ubuntu","event":"send_attempt",'
        '"request_id":"request-1","media_session_id":false}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="line 1"):
        google.load_trace(path)
