"""Instrumented C6/C7 evidence rules; no network or device is used."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

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
    return google.TraceEvent(sequence, "ubuntu", name, "request-1", session)


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
    ],
)
def test_c7_never_claims_no_replay_without_complete_instrumentation(events: list[object]) -> None:
    assert google.validate_c7(events, "request-1")[0] is False
