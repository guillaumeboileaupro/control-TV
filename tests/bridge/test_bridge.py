"""Tests for the stdio JSON bridge that the Tauri shell spawns and talks to.

`dispatch`/`handle_line` are exercised against a real `ControlService` over the
deterministic `FakeTransport`, so no real Chromecast, network or subprocess is involved
here - proving the mechanism reaches the shared control layer without duplicating it.
`test_main_serves_a_ping_over_real_stdio` is the one exception: it spawns the actual
`python -m control_tv.bridge` entrypoint to prove the process boundary itself works.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from io import StringIO

import pytest

import control_tv
from control_tv.bridge import dispatch, handle_line, run
from control_tv.domain import (
    ConnectionState,
    DeviceId,
    DeviceStatus,
    DeviceUnavailableError,
    MediaStatus,
    OperationTimeoutError,
    PlaybackState,
    ReceiverStatus,
)
from control_tv.service import ControlService
from fakes import DEVICE_ID, MOVIE_URL, OBSERVED_AT, FakeTransport


@dataclass
class ScriptedStatusTransport(FakeTransport):
    """`FakeTransport` whose status snapshot can be replaced by an exact, partial one."""

    scripted: DeviceStatus | None = None

    def get_status(self, device_id: DeviceId, *, timeout: float) -> DeviceStatus:
        if self.scripted is not None:
            self._require_known(device_id)
            return self.scripted
        return super().get_status(device_id, timeout=timeout)


@pytest.fixture
def control() -> ControlService:
    return ControlService(FakeTransport())


def status_request(device_id: object, request_id: int = 1) -> dict[str, object]:
    return {"id": request_id, "method": "get_status", "params": {"deviceId": device_id}}


def test_ping_reports_readiness_and_version(control: ControlService) -> None:
    response = dispatch(control, {"id": 1, "method": "ping", "params": {}})

    assert response == {
        "id": 1,
        "ok": True,
        "result": {"status": "ready", "controlTvVersion": control_tv.__version__},
    }


def test_ping_does_not_require_params(control: ControlService) -> None:
    response = dispatch(control, {"id": "abc", "method": "ping"})

    assert response["ok"] is True
    assert response["id"] == "abc"


def test_discover_devices_returns_transport_devices_as_json(control: ControlService) -> None:
    response = dispatch(control, {"id": 2, "method": "discover_devices", "params": {}})

    assert response["ok"] is True
    assert response["result"]["devices"] == [
        {
            "id": str(DEVICE_ID),
            "friendlyName": "Living room",
            "host": "192.168.1.20",
            "port": 8009,
            "kind": "unknown",
            "modelName": None,
        }
    ]


def test_discover_devices_forwards_the_timeout(
    control: ControlService, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float] = []
    original = ControlService.discover_devices

    def spy(self: ControlService, *, timeout: float = 5.0) -> list[object]:
        seen.append(timeout)
        return list(original(self, timeout=timeout))

    monkeypatch.setattr(ControlService, "discover_devices", spy)

    dispatch(control, {"id": 3, "method": "discover_devices", "params": {"timeoutSeconds": 2.5}})

    assert seen == [2.5]


def test_discover_devices_rejects_a_non_numeric_timeout(control: ControlService) -> None:
    response = dispatch(
        control, {"id": 4, "method": "discover_devices", "params": {"timeoutSeconds": "soon"}}
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"


def test_a_control_error_from_the_service_is_translated_not_raised(
    control: ControlService,
) -> None:
    """A real domain error (invalid discovery timeout) becomes an error response, not a crash."""
    response = dispatch(
        control, {"id": 5, "method": "discover_devices", "params": {"timeoutSeconds": -1.0}}
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert "timeout" in response["error"]["message"]


def test_get_status_returns_the_observed_status_as_json(control: ControlService) -> None:
    response = dispatch(control, status_request(str(DEVICE_ID)))

    assert response == {
        "id": 1,
        "ok": True,
        "result": {
            "status": {
                "deviceId": str(DEVICE_ID),
                "connection": "connected",
                "observedAt": "2026-09-25T12:00:00+00:00",
                "receiver": {
                    "appId": None,
                    "appName": None,
                    "volumeLevel": 0.5,
                    "muted": False,
                    "standby": None,
                },
                "media": {
                    "playbackState": "playing",
                    "contentId": MOVIE_URL,
                    "contentType": None,
                    "title": None,
                    "positionSeconds": 10.0,
                    "durationSeconds": None,
                    "supportsSeek": True,
                },
            }
        },
    }


def test_get_status_reports_unreported_fields_as_null_never_as_a_default() -> None:
    transport = ScriptedStatusTransport(
        scripted=DeviceStatus(
            device_id=DEVICE_ID,
            connection=ConnectionState.CONNECTED,
            observed_at=OBSERVED_AT,
            receiver=ReceiverStatus(app_name="Default Media Receiver"),
            media=MediaStatus(playback_state=PlaybackState.UNKNOWN),
        )
    )

    status = dispatch(ControlService(transport), status_request(str(DEVICE_ID)))["result"]["status"]

    assert status["receiver"] == {
        "appId": None,
        "appName": "Default Media Receiver",
        "volumeLevel": None,
        "muted": None,
        "standby": None,
    }
    assert status["media"] == {
        "playbackState": "unknown",
        "contentId": None,
        "contentType": None,
        "title": None,
        "positionSeconds": None,
        "durationSeconds": None,
        "supportsSeek": None,
    }


def test_get_status_reports_no_media_session_as_null_media() -> None:
    transport = FakeTransport()
    transport.tv.has_media = False

    status = dispatch(ControlService(transport), status_request(str(DEVICE_ID)))["result"]["status"]

    assert status["media"] is None
    assert status["receiver"]["volumeLevel"] == 0.5


def test_get_status_of_a_disconnected_device_carries_no_receiver_or_media() -> None:
    transport = FakeTransport()
    transport.tv.connection = ConnectionState.DISCONNECTED

    status = dispatch(ControlService(transport), status_request(str(DEVICE_ID)))["result"]["status"]

    assert status["connection"] == "disconnected"
    assert status["receiver"] is None
    assert status["media"] is None


def test_get_status_selects_the_device_by_stable_id_and_reads_it_once() -> None:
    transport = FakeTransport()

    dispatch(ControlService(transport), status_request(str(DEVICE_ID)))

    assert [name for name, _ in transport.calls] == ["get_status"]
    assert transport.calls[0][1][0] == DEVICE_ID


def test_get_status_never_resolves_a_display_name() -> None:
    transport = FakeTransport()

    response = dispatch(ControlService(transport), status_request("Living room"))

    assert response["ok"] is False
    assert response["error"]["code"] == "device_not_found"


def test_get_status_is_read_only_and_sends_no_command() -> None:
    transport = FakeTransport()

    dispatch(ControlService(transport), status_request(str(DEVICE_ID)))

    assert transport.sent() == []


@pytest.mark.parametrize("device_id", [None, 42, ["uuid-1"], ""])
def test_get_status_rejects_a_missing_or_non_string_or_blank_device_id(
    device_id: object,
) -> None:
    transport = FakeTransport()
    request: dict[str, object] = {"id": 9, "method": "get_status", "params": {}}
    if device_id is not None:
        request["params"] = {"deviceId": device_id}

    response = dispatch(ControlService(transport), request)

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert transport.status_reads() == 0


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (DeviceUnavailableError("tv is asleep", device_id=str(DEVICE_ID)), "device_unavailable"),
        (OperationTimeoutError("no answer in 5s", device_id=str(DEVICE_ID)), "timeout"),
    ],
)
def test_get_status_failures_keep_their_error_code_and_message(
    error: DeviceUnavailableError | OperationTimeoutError, code: str
) -> None:
    transport = FakeTransport(status_errors=[error])

    response = dispatch(ControlService(transport), status_request(str(DEVICE_ID), 5))

    assert response == {
        "id": 5,
        "ok": False,
        "error": {"code": code, "message": error.message},
    }


def test_an_unexpected_exception_is_an_internal_error_and_the_bridge_keeps_serving() -> None:
    class Exploding(FakeTransport):
        def get_status(self, device_id: DeviceId, *, timeout: float) -> DeviceStatus:
            raise RuntimeError("library blew up")

    stdin = StringIO(
        json.dumps(status_request(str(DEVICE_ID), 1)) + '\n{"id": 2, "method": "ping"}\n'
    )
    stdout = StringIO()

    run(ControlService(Exploding()), stdin=stdin, stdout=stdout)

    first, second = (json.loads(line) for line in stdout.getvalue().splitlines())
    assert first == {
        "id": 1,
        "ok": False,
        "error": {"code": "internal_error", "message": "RuntimeError: library blew up"},
    }
    assert second["ok"] is True
    assert second["id"] == 2


def test_unknown_method_is_rejected(control: ControlService) -> None:
    response = dispatch(control, {"id": 6, "method": "levitate", "params": {}})

    assert response == {
        "id": 6,
        "ok": False,
        "error": {"code": "invalid_argument", "message": "unknown method: 'levitate'"},
    }


def test_non_object_params_is_rejected(control: ControlService) -> None:
    response = dispatch(control, {"id": 7, "method": "ping", "params": ["not", "an", "object"]})

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"


def test_malformed_json_is_reported_with_a_null_id(control: ControlService) -> None:
    response = handle_line(control, "{not json")

    assert response["id"] is None
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert "malformed JSON request" in response["error"]["message"]


def test_a_json_array_is_not_a_valid_request(control: ControlService) -> None:
    response = handle_line(control, "[1, 2, 3]")

    assert response["id"] is None
    assert response["ok"] is False


def test_run_serves_one_response_per_request_line_and_skips_blanks(
    control: ControlService,
) -> None:
    stdin = StringIO('{"id": 1, "method": "ping"}\n\n{"id": 2, "method": "ping"}\n')
    stdout = StringIO()

    run(control, stdin=stdin, stdout=stdout)

    lines = stdout.getvalue().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["id"] for line in lines] == [1, 2]
    assert all(json.loads(line)["ok"] is True for line in lines)


def test_main_serves_a_ping_over_real_stdio() -> None:
    """End-to-end proof that `python -m control_tv.bridge` is what Rust will actually spawn."""
    process = subprocess.Popen(
        [sys.executable, "-m", "control_tv.bridge"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write('{"id": 1, "method": "ping"}\n')
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)

    assert response == {
        "id": 1,
        "ok": True,
        "result": {"status": "ready", "controlTvVersion": control_tv.__version__},
    }


def test_the_real_transport_reports_an_undiscovered_device_without_touching_the_network() -> None:
    """`get_status` through the real `PyChromecastTransport`: a device that was never
    discovered fails before any network I/O, so this needs no Chromecast."""
    process = subprocess.Popen(
        [sys.executable, "-m", "control_tv.bridge"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps(status_request("never-discovered", 7)) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)

    assert response["id"] == 7
    assert response["ok"] is False
    assert response["error"]["code"] == "device_not_found"
