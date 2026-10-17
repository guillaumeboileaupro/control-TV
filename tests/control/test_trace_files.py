"""Acceptance trace files: private, link-proof, bounded, and off unless configured."""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from control_tv import trace_files
from control_tv.command_trace import (
    HoldRequest,
    TraceConfigurationError,
    TraceEvent,
    TraceRole,
)
from control_tv.domain import Command
from control_tv.trace_files import (
    FileConfirmationGate,
    JsonLinesTraceSink,
    android_tracing,
    held_commands,
    private_directory,
    release_command,
    tracing_from_environment,
)

EVENT = TraceEvent(1, "ubuntu", "send_attempt", "request-1", 0, 7, "pause")
TOKEN = "0123456789abcdef0123456789abcdef"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


# The JSONL sink ------------------------------------------------------------------------------


def test_events_are_appended_to_a_0600_file_in_a_new_0700_directory(tmp_path: Path) -> None:
    path = tmp_path / "evidence" / "nested" / "trace.jsonl"
    sink = JsonLinesTraceSink(path)

    sink.write(EVENT)
    sink.write(EVENT)

    assert mode(path) == 0o600 and mode(path.parent) == 0o700
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["event"] == "send_attempt"
    assert sink.path == path


def test_a_directory_that_others_can_read_is_refused(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)

    with pytest.raises(TraceConfigurationError, match="must be private"):
        JsonLinesTraceSink(shared / "trace.jsonl").write(EVENT)


def test_a_linked_directory_or_a_plain_file_is_refused(tmp_path: Path) -> None:
    real = private_directory(tmp_path / "real")
    (tmp_path / "link").symlink_to(real)
    (tmp_path / "file").write_text("x")

    for path in (tmp_path / "link", tmp_path / "file"):
        with pytest.raises(TraceConfigurationError, match="real directory"):
            private_directory(path)


def test_a_linked_trace_file_is_never_followed(tmp_path: Path) -> None:
    directory = private_directory(tmp_path / "evidence")
    victim = tmp_path / "victim.txt"
    victim.write_text("owner data\n")
    victim.chmod(0o644)
    (directory / "trace.jsonl").symlink_to(victim)

    with pytest.raises(OSError):
        JsonLinesTraceSink(directory / "trace.jsonl").write(EVENT)

    assert victim.read_text() == "owner data\n" and mode(victim) == 0o644
    assert (directory / "trace.jsonl.incomplete").is_file()


def test_a_failed_write_leaves_an_incomplete_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = private_directory(tmp_path / "evidence")
    real_open = trace_files._open_private

    def refuse_trace(path: Path, flags: int) -> int:
        if path.name == "trace.jsonl":
            raise OSError("disk full")
        return real_open(path, flags)

    monkeypatch.setattr(trace_files, "_open_private", refuse_trace)
    with pytest.raises(OSError, match="disk full"):
        JsonLinesTraceSink(directory / "trace.jsonl").write(EVENT)
    assert mode(directory / "trace.jsonl.incomplete") == 0o600


def test_a_failing_fchmod_closes_the_file_and_fails_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[int] = []
    real_close = os.close

    def track_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    def refuse(descriptor: int, mode: int) -> None:
        raise PermissionError("fchmod refused")

    monkeypatch.setattr(os, "fchmod", refuse)
    monkeypatch.setattr(os, "close", track_close)
    with pytest.raises(PermissionError):
        JsonLinesTraceSink(tmp_path / "evidence" / "trace.jsonl").write(EVENT)
    assert closed  # the descriptor was not leaked


def test_a_path_that_is_not_a_regular_file_is_refused(tmp_path: Path) -> None:
    directory = private_directory(tmp_path / "evidence")
    (directory / "trace.jsonl").mkdir()

    with pytest.raises(OSError):
        JsonLinesTraceSink(directory / "trace.jsonl").write(EVENT)


# The file confirmation hold ------------------------------------------------------------------


def request(request_id: str = "request-1") -> HoldRequest:
    return HoldRequest(request_id, Command.PAUSE, 7)


class Ticks:
    """A deterministic clock whose sleep advances time and can run a side effect."""

    def __init__(self, on_sleep: object = None) -> None:
        self.now = 0.0
        self.on_sleep = on_sleep

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if callable(self.on_sleep):
            self.on_sleep()


def test_a_hold_is_released_by_the_observer_token_and_cleaned(tmp_path: Path) -> None:
    gate_dir = tmp_path / "gate"

    def observer() -> None:
        held = held_commands(gate_dir)
        if held:
            assert held[0].request_id == "request-1" and held[0].media_session_id == 7
            release_command(gate_dir, "request-1", TOKEN)

    ticks = Ticks(observer)
    gate = FileConfirmationGate(gate_dir, timeout_seconds=5, clock=ticks.clock, sleep=ticks.sleep)

    outcome = gate.hold(request())

    assert outcome.released and outcome.token == TOKEN
    assert list(gate_dir.iterdir()) == [] and mode(gate_dir) == 0o700


def test_a_hold_times_out_bounded_and_ignores_a_malformed_release(tmp_path: Path) -> None:
    gate_dir = private_directory(tmp_path / "gate")

    def forge() -> None:
        (gate_dir / "request-1.release").write_text("not-a-token\n")

    ticks = Ticks(forge)
    outcome = FileConfirmationGate(
        gate_dir, timeout_seconds=1, clock=ticks.clock, sleep=ticks.sleep, poll_seconds=0.25
    ).hold(request())

    assert outcome.released is False and outcome.token is None
    assert ticks.now == pytest.approx(1.0) and list(gate_dir.iterdir()) == []


def test_an_interrupted_hold_cleans_its_files(tmp_path: Path) -> None:
    gate_dir = tmp_path / "gate"

    def interrupt() -> None:
        assert held_commands(gate_dir)
        raise KeyboardInterrupt

    ticks = Ticks(interrupt)
    with pytest.raises(KeyboardInterrupt):
        FileConfirmationGate(gate_dir, clock=ticks.clock, sleep=ticks.sleep).hold(request())
    assert list(gate_dir.iterdir()) == []


@pytest.mark.parametrize("seconds", [0, -1, 121])
def test_a_hold_is_always_bounded(tmp_path: Path, seconds: float) -> None:
    with pytest.raises(TraceConfigurationError, match="at most 120"):
        FileConfirmationGate(tmp_path, timeout_seconds=seconds)


def test_a_request_id_that_cannot_name_a_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TraceConfigurationError, match="cannot name"):
        FileConfirmationGate(tmp_path / "gate").hold(request("../escape"))


def test_malformed_hold_markers_are_ignored_and_bad_releases_refused(tmp_path: Path) -> None:
    gate_dir = private_directory(tmp_path / "gate")
    (gate_dir / "a.held").write_text("not json")
    (gate_dir / "b.held").write_text(json.dumps({"request_id": "other", "media_session_id": 1}))
    (gate_dir / "c.held").write_text(json.dumps({"request_id": "c", "media_session_id": True}))
    (gate_dir / "d.held").write_text("[]")

    assert held_commands(gate_dir) == ()
    with pytest.raises(TraceConfigurationError, match="invalid request id or release token"):
        release_command(gate_dir, "c", "short")


# Configuration -------------------------------------------------------------------------------


def test_tracing_is_off_unless_a_trace_file_is_named() -> None:
    tracing = tracing_from_environment({})
    assert tracing.recorder.active is False and tracing.gate is None and tracing.trace_file is None


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({"CONTROL_TV_CONFIRMATION_GATE": "/g"}, "needs CONTROL_TV_COMMAND_TRACE"),
        ({"CONTROL_TV_COMMAND_TRACE": "/t.jsonl"}, "must be one of: ubuntu, android, observer"),
        (
            {"CONTROL_TV_COMMAND_TRACE": "/t.jsonl", "CONTROL_TV_TRACE_CLIENT": "control-tv"},
            "must be one of",
        ),
        (
            {
                "CONTROL_TV_COMMAND_TRACE": "/t.jsonl",
                "CONTROL_TV_TRACE_CLIENT": "observer",
                "CONTROL_TV_CONFIRMATION_GATE": "/g",
            },
            "observer never holds",
        ),
        (
            {
                "CONTROL_TV_COMMAND_TRACE": "/t.jsonl",
                "CONTROL_TV_TRACE_CLIENT": "ubuntu",
                "CONTROL_TV_CONFIRMATION_GATE": "/g",
                "CONTROL_TV_CONFIRMATION_GATE_SECONDS": "soon",
            },
            "number of seconds",
        ),
    ],
    ids=["gate-alone", "no-role", "unknown-role", "observer-gate", "bad-seconds"],
)
def test_an_invalid_configuration_is_refused_not_guessed(
    environ: dict[str, str], message: str
) -> None:
    with pytest.raises(TraceConfigurationError, match=message):
        tracing_from_environment(environ, frozen=False)


def test_the_hold_is_refused_in_a_release_bridge() -> None:
    environ = {
        "CONTROL_TV_COMMAND_TRACE": "/t.jsonl",
        "CONTROL_TV_TRACE_CLIENT": "ubuntu",
        "CONTROL_TV_CONFIRMATION_GATE": "/g",
    }
    with pytest.raises(TraceConfigurationError, match="refused in a release bridge"):
        tracing_from_environment(environ, frozen=True)


def test_a_development_bridge_gets_its_role_trace_and_bounded_hold(tmp_path: Path) -> None:
    tracing = tracing_from_environment(
        {
            "CONTROL_TV_COMMAND_TRACE": str(tmp_path / "t.jsonl"),
            "CONTROL_TV_TRACE_CLIENT": "android",
            "CONTROL_TV_CONFIRMATION_GATE": str(tmp_path / "gate"),
            "CONTROL_TV_CONFIRMATION_GATE_SECONDS": "12",
        },
        frozen=False,
    )
    assert tracing.recorder.role is TraceRole.ANDROID and tracing.recorder.active
    assert isinstance(tracing.gate, FileConfirmationGate)
    assert tracing.trace_file == tmp_path / "t.jsonl"
    assert (
        tracing_from_environment(
            {"CONTROL_TV_COMMAND_TRACE": "/t.jsonl", "CONTROL_TV_TRACE_CLIENT": "ubuntu"}
        ).gate
        is None
    )


def test_the_android_mode_keeps_everything_in_a_private_directory(tmp_path: Path) -> None:
    tracing = android_tracing(tmp_path / "files" / "acceptance")

    assert tracing.recorder.role is TraceRole.ANDROID
    assert tracing.trace_file == tmp_path / "files" / "acceptance" / "command-trace.jsonl"
    assert mode(tmp_path / "files" / "acceptance") == 0o700
    assert isinstance(tracing.gate, FileConfirmationGate)


def test_a_concurrent_hold_and_release_with_real_time(tmp_path: Path) -> None:
    gate_dir = tmp_path / "gate"
    gate = FileConfirmationGate(gate_dir, timeout_seconds=5, poll_seconds=0.01)
    released = threading.Event()

    def observer() -> None:
        for _ in range(500):
            if held_commands(gate_dir):
                release_command(gate_dir, "request-1", TOKEN)
                released.set()
                return
            threading.Event().wait(0.01)

    thread = threading.Thread(target=observer)
    thread.start()
    outcome = gate.hold(request())
    thread.join(5)

    assert released.is_set() and outcome.released and outcome.token == TOKEN
