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
from typing import Any

import pytest

import control_tv
from control_tv.bridge import dispatch, handle_line, run
from control_tv.domain import (
    CommandRejectedError,
    ConnectionState,
    ControlError,
    DeviceId,
    DeviceStatus,
    DeviceUnavailableError,
    MediaStatus,
    OperationTimeoutError,
    PlaybackState,
    ReceiverStatus,
)
from control_tv.service import ControlService
from fakes import DEVICE_ID, MOVIE_URL, OBSERVED_AT, FakeClock, FakeTransport


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


# --- playback commands: plain forwards to ControlService; sent is not confirmed ---------


def playback_control(transport: FakeTransport, **overrides: float | None) -> ControlService:
    """A service over a fake clock, so confirmation timing is exact and nothing sleeps."""
    clock = FakeClock()
    transport.clock = clock
    return ControlService(
        transport,
        confirm_timeout=overrides.get("confirm_timeout", 1.0),
        poll_interval=0.25,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )


def command_request(method: str, **params: object) -> dict[str, object]:
    return {"id": 1, "method": method, "params": {"deviceId": str(DEVICE_ID), **params}}


@pytest.mark.parametrize(
    ("method", "before", "after"),
    [
        ("play", PlaybackState.PAUSED, "playing"),
        ("pause", PlaybackState.PLAYING, "paused"),
        ("stop", PlaybackState.PLAYING, "idle"),
    ],
)
def test_a_transport_command_is_forwarded_and_confirmed_by_an_observed_status(
    method: str, before: PlaybackState, after: str
) -> None:
    transport = FakeTransport()
    transport.tv.playback = before

    response = dispatch(playback_control(transport), command_request(method))

    assert response["ok"] is True
    result = response["result"]["result"]
    assert result["command"] == method
    assert result["deviceId"] == str(DEVICE_ID)
    assert result["confirmation"] == "confirmed"
    assert result["detail"] is None
    assert result["observed"]["media"]["playbackState"] == after
    assert transport.sent() == [method]


def test_seek_forwards_the_position_and_is_confirmed_by_the_observed_position() -> None:
    transport = FakeTransport()

    response = dispatch(playback_control(transport), command_request("seek", positionSeconds=42))

    result = response["result"]["result"]
    assert result["command"] == "seek"
    assert result["confirmation"] == "confirmed"
    assert result["observed"]["media"]["positionSeconds"] == 42.0
    assert transport.calls[-2][0] == "seek"
    assert transport.calls[-2][1] == (DEVICE_ID, 42.0)


def test_a_command_the_tv_never_acts_on_is_sent_but_unconfirmed_and_never_resent() -> None:
    transport = FakeTransport(ignore_commands=True)
    transport.tv.playback = PlaybackState.PLAYING

    response = dispatch(playback_control(transport), command_request("pause"))

    assert response["ok"] is True
    result = response["result"]["result"]
    assert result["confirmation"] == "unconfirmed"
    assert "expected playback paused" in result["detail"]
    assert result["observed"]["media"]["playbackState"] == "playing"
    assert transport.sent() == ["pause"]


def test_a_command_with_verification_disabled_is_reported_as_not_checked() -> None:
    transport = FakeTransport()

    response = dispatch(playback_control(transport, confirm_timeout=None), command_request("pause"))

    result = response["result"]["result"]
    assert result["confirmation"] == "not_checked"
    assert result["observed"] is None
    assert transport.sent() == ["pause"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (DeviceUnavailableError("tv is asleep", device_id=str(DEVICE_ID)), "device_unavailable"),
        (CommandRejectedError("nothing to pause", device_id=str(DEVICE_ID)), "command_rejected"),
        (OperationTimeoutError("no answer in 10s", device_id=str(DEVICE_ID)), "timeout"),
    ],
)
@pytest.mark.parametrize("method", ["play", "pause", "stop"])
def test_a_command_that_could_not_be_sent_is_an_error_with_its_code_never_a_result(
    method: str, error: ControlError, code: str
) -> None:
    transport = FakeTransport(fail_commands_with=error)

    response = dispatch(playback_control(transport), command_request(method))

    assert response["ok"] is False
    assert "result" not in response
    assert response["error"] == {"code": code, "message": error.message}
    assert transport.sent() == []


def test_seek_on_media_that_cannot_seek_is_refused_before_anything_is_sent() -> None:
    transport = FakeTransport()
    transport.tv.supports_seek = False

    response = dispatch(playback_control(transport), command_request("seek", positionSeconds=30))

    assert response["ok"] is False
    assert response["error"]["code"] == "unsupported_operation"
    assert transport.sent() == []


@pytest.mark.parametrize("method", ["play", "pause", "stop", "seek"])
def test_a_command_for_an_unknown_device_is_not_found_and_sends_nothing(method: str) -> None:
    transport = FakeTransport()
    request = {"id": 1, "method": method, "params": {"deviceId": "Living room"}}
    if method == "seek":
        request["params"]["positionSeconds"] = 5  # type: ignore[index]

    response = dispatch(playback_control(transport), request)

    assert response["ok"] is False
    assert response["error"]["code"] == "device_not_found"
    assert transport.sent() == []


@pytest.mark.parametrize("device_id", [None, 42, ["uuid-1"], "", "   "])
@pytest.mark.parametrize("method", ["play", "pause", "stop", "seek"])
def test_a_command_needs_a_non_blank_string_device_id(method: str, device_id: object) -> None:
    transport = FakeTransport()
    params: dict[str, object] = {"positionSeconds": 5} if method == "seek" else {}
    if device_id is not None:
        params["deviceId"] = device_id

    response = dispatch(playback_control(transport), {"id": 2, "method": method, "params": params})

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert transport.calls == []


@pytest.mark.parametrize(
    "position", [None, "30", True, False, [30], {"s": 30}, -1, -0.5, float("nan"), float("inf")]
)
def test_seek_rejects_a_missing_non_numeric_or_invalid_position_before_any_transport_call(
    position: object,
) -> None:
    transport = FakeTransport()
    params: dict[str, object] = {"deviceId": str(DEVICE_ID)}
    if position is not None:
        params["positionSeconds"] = position

    response = dispatch(playback_control(transport), {"id": 3, "method": "seek", "params": params})

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert transport.calls == []


def test_the_real_transport_refuses_commands_for_an_undiscovered_device_without_the_network() -> (
    None
):
    """Through the real `PyChromecastTransport` and real stdio: an id that was never
    discovered fails before any network I/O, so this sends nothing to any device."""
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
        for request_id, method in enumerate(["play", "pause", "stop", "seek"], start=10):
            params: dict[str, object] = {"deviceId": "never-discovered"}
            if method == "seek":
                params["positionSeconds"] = 5
            process.stdin.write(
                json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
            )
            process.stdin.flush()
            response = json.loads(process.stdout.readline())
            assert response["id"] == request_id
            assert response["ok"] is False
            assert response["error"]["code"] == "device_not_found"
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)


# --- volume and mute: plain forwards to ControlService; sent is not confirmed -----------


def sound_result(response: dict[str, Any]) -> dict[str, Any]:
    assert response["ok"] is True
    result: dict[str, Any] = response["result"]["result"]
    return result


@pytest.mark.parametrize("level", [0.8, 0.0, 1.0, 0, 1])
def test_set_volume_forwards_the_level_and_is_confirmed_by_the_observed_volume(
    level: float,
) -> None:
    transport = FakeTransport()

    response = dispatch(playback_control(transport), command_request("set_volume", level=level))

    result = sound_result(response)
    assert result["command"] == "set_volume"
    assert result["deviceId"] == str(DEVICE_ID)
    assert result["confirmation"] == "confirmed"
    assert result["detail"] is None
    assert result["observed"]["receiver"]["volumeLevel"] == float(level)
    assert transport.sent() == ["set_volume"]
    assert transport.calls[0] == ("set_volume", (DEVICE_ID, float(level)))


def test_a_whole_number_level_is_sent_to_the_control_layer_as_a_float() -> None:
    transport = FakeTransport()

    dispatch(playback_control(transport), command_request("set_volume", level=1))

    assert isinstance(transport.calls[0][1][1], float)


@pytest.mark.parametrize("muted", [True, False])
def test_set_muted_forwards_the_state_and_is_confirmed_by_the_observed_mute(muted: bool) -> None:
    transport = FakeTransport()
    transport.tv.muted = not muted

    response = dispatch(playback_control(transport), command_request("set_muted", muted=muted))

    result = sound_result(response)
    assert result["command"] == "set_muted"
    assert result["confirmation"] == "confirmed"
    assert result["observed"]["receiver"]["muted"] is muted
    assert transport.calls[0] == ("set_muted", (DEVICE_ID, muted))
    assert [name for name, _ in transport.calls].count("set_muted") == 1


def test_a_volume_the_tv_confirms_late_is_still_one_command() -> None:
    transport = FakeTransport(effect_delay_polls=2)

    response = dispatch(playback_control(transport), command_request("set_volume", level=0.8))

    assert sound_result(response)["confirmation"] == "confirmed"
    assert transport.sent().count("set_volume") == 1


def test_a_volume_the_tv_never_shows_is_sent_but_unconfirmed_and_never_resent() -> None:
    transport = FakeTransport(ignore_commands=True)

    response = dispatch(playback_control(transport), command_request("set_volume", level=0.8))

    result = sound_result(response)
    assert result["confirmation"] == "unconfirmed"
    assert "expected volume near 0.8" in result["detail"]
    assert result["observed"]["receiver"]["volumeLevel"] == 0.5
    assert transport.sent().count("set_volume") == 1
    assert transport.attempted() == ["set_volume"]


def test_a_volume_the_tv_reports_differently_is_unconfirmed_and_carries_what_it_reports() -> None:
    """A receiver that rounds to its own steps: the answer is what the TV really shows."""
    transport = FakeTransport()
    transport.status_effects = [lambda: setattr(transport.tv, "volume", 0.47)] * 12

    response = dispatch(playback_control(transport), command_request("set_volume", level=0.5))

    result = sound_result(response)
    assert result["confirmation"] == "unconfirmed"
    assert result["observed"]["receiver"]["volumeLevel"] == 0.47
    assert transport.sent().count("set_volume") == 1


def test_a_mute_the_tv_never_shows_is_sent_but_unconfirmed_and_never_resent() -> None:
    transport = FakeTransport(ignore_commands=True)

    response = dispatch(playback_control(transport), command_request("set_muted", muted=True))

    result = sound_result(response)
    assert result["confirmation"] == "unconfirmed"
    assert "expected mute on" in result["detail"]
    assert result["observed"]["receiver"]["muted"] is False
    assert transport.sent().count("set_muted") == 1
    assert transport.attempted() == ["set_muted"]


def test_an_unreported_volume_and_mute_state_cannot_confirm_a_command() -> None:
    transport = ScriptedStatusTransport(
        scripted=DeviceStatus(
            device_id=DEVICE_ID,
            connection=ConnectionState.CONNECTED,
            observed_at=OBSERVED_AT,
            receiver=ReceiverStatus(volume_level=None, muted=None),
            media=None,
        )
    )
    control = playback_control(transport)

    volume = sound_result(dispatch(control, command_request("set_volume", level=0.5)))
    mute = sound_result(dispatch(control, command_request("set_muted", muted=True)))

    assert volume["confirmation"] == "unconfirmed"
    assert "volume level was not reported" in volume["detail"]
    assert volume["observed"]["receiver"]["volumeLevel"] is None
    assert mute["confirmation"] == "unconfirmed"
    assert "mute state was not reported" in mute["detail"]
    assert mute["observed"]["receiver"]["muted"] is None


def test_volume_and_mute_with_verification_disabled_are_reported_as_not_checked() -> None:
    transport = FakeTransport()
    control = playback_control(transport, confirm_timeout=None)

    volume = sound_result(dispatch(control, command_request("set_volume", level=0.3)))
    mute = sound_result(dispatch(control, command_request("set_muted", muted=True)))

    for result in (volume, mute):
        assert result["confirmation"] == "not_checked"
        assert result["observed"] is None
    assert transport.sent() == ["set_volume", "set_muted"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (DeviceUnavailableError("tv is asleep", device_id=str(DEVICE_ID)), "device_unavailable"),
        (CommandRejectedError("volume is fixed", device_id=str(DEVICE_ID)), "command_rejected"),
        (OperationTimeoutError("no answer in 10s", device_id=str(DEVICE_ID)), "timeout"),
    ],
)
@pytest.mark.parametrize(
    ("method", "params"),
    [("set_volume", {"level": 0.5}), ("set_muted", {"muted": True})],
)
def test_a_sound_command_that_could_not_be_sent_is_an_error_with_its_code_never_a_result(
    method: str, params: dict[str, object], error: ControlError, code: str
) -> None:
    transport = FakeTransport(fail_commands_with=error)

    response = dispatch(playback_control(transport), command_request(method, **params))

    assert response["ok"] is False
    assert "result" not in response
    assert response["error"] == {"code": code, "message": error.message}
    assert transport.sent() == []
    assert transport.attempted() == [method]


@pytest.mark.parametrize(
    ("method", "params"),
    [("set_volume", {"level": 0.5}), ("set_muted", {"muted": True})],
)
def test_a_sound_command_for_an_unknown_device_is_not_found_and_sends_nothing(
    method: str, params: dict[str, object]
) -> None:
    transport = FakeTransport()
    request = {"id": 1, "method": method, "params": {"deviceId": "Living room", **params}}

    response = dispatch(playback_control(transport), request)

    assert response["ok"] is False
    assert response["error"]["code"] == "device_not_found"
    assert transport.sent() == []


@pytest.mark.parametrize("device_id", [None, 42, ["uuid-1"], "", "   "])
@pytest.mark.parametrize(
    ("method", "params"),
    [("set_volume", {"level": 0.5}), ("set_muted", {"muted": True})],
)
def test_a_sound_command_needs_a_non_blank_string_device_id(
    method: str, params: dict[str, object], device_id: object
) -> None:
    transport = FakeTransport()
    request_params: dict[str, object] = dict(params)
    if device_id is not None:
        request_params["deviceId"] = device_id

    response = dispatch(
        playback_control(transport), {"id": 2, "method": method, "params": request_params}
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert transport.calls == []


@pytest.mark.parametrize(
    "level",
    [
        None,
        "0.5",
        True,
        False,
        [0.5],
        {"level": 0.5},
        -0.1,
        1.01,
        2,
        -1,
        float("nan"),
        float("inf"),
        float("-inf"),
        pytest.param(10**400, id="integer-too-large-for-a-float"),
    ],
)
def test_set_volume_rejects_a_missing_non_numeric_or_out_of_range_level_before_any_call(
    level: object,
) -> None:
    transport = FakeTransport()
    params: dict[str, object] = {"deviceId": str(DEVICE_ID)}
    if level is not None:
        params["level"] = level

    response = dispatch(
        playback_control(transport), {"id": 3, "method": "set_volume", "params": params}
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert transport.calls == []


@pytest.mark.parametrize("muted", [None, "true", "false", 0, 1, 1.0, [True], {"muted": True}])
def test_set_muted_rejects_anything_that_is_not_a_boolean_before_any_call(muted: object) -> None:
    transport = FakeTransport()
    params: dict[str, object] = {"deviceId": str(DEVICE_ID)}
    if muted is not None:
        params["muted"] = muted

    response = dispatch(
        playback_control(transport), {"id": 3, "method": "set_muted", "params": params}
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert transport.calls == []


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_level_written_as_json_is_rejected(literal: str) -> None:
    transport = FakeTransport()
    line = (
        '{"id": 4, "method": "set_volume", "params": '
        f'{{"deviceId": "{DEVICE_ID}", "level": {literal}}}}}'
    )

    response = handle_line(playback_control(transport), line)

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_argument"
    assert transport.calls == []


def test_sound_commands_never_touch_playback_or_each_other() -> None:
    transport = FakeTransport()
    control = playback_control(transport)

    dispatch(control, command_request("set_volume", level=0.2))
    dispatch(control, command_request("set_muted", muted=True))

    assert transport.sent().count("set_volume") == 1
    assert transport.sent().count("set_muted") == 1
    assert not {"play", "pause", "stop", "seek"} & set(transport.sent())
    assert transport.tv.volume == 0.2
    assert transport.tv.muted is True


def test_the_real_transport_refuses_sound_commands_for_an_undiscovered_device_offline() -> None:
    """Through the real `PyChromecastTransport` and real stdio: an id that was never
    discovered fails before any network I/O, so this sends nothing to any device."""
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
        requests = [
            ("set_volume", {"deviceId": "never-discovered", "level": 0.4}),
            ("set_muted", {"deviceId": "never-discovered", "muted": True}),
            ("set_muted", {"deviceId": "never-discovered", "muted": False}),
        ]
        for request_id, (method, params) in enumerate(requests, start=20):
            process.stdin.write(
                json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
            )
            process.stdin.flush()
            response = json.loads(process.stdout.readline())
            assert response["id"] == request_id
            assert response["ok"] is False
            assert response["error"]["code"] == "device_not_found"
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=5)
