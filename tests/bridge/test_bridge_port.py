"""The bridge depends on the `TvControl` port, not on `ControlService`.

`StubControl` is not a `ControlService`: the bridge dispatches to it all the same, and
mypy checks that it satisfies the port (`control: TvControl = StubControl()`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from control_tv.bridge import dispatch, handle_line
from control_tv.domain import (
    AppLaunchResult,
    CommandResult,
    Device,
    DeviceId,
    DeviceNotFoundError,
    DeviceStatus,
    MediaRequest,
    RemoteKey,
    RemoteKeyResult,
    RemoteObservation,
    RemoteTextResult,
    TvApp,
)
from control_tv.json_types import as_object
from control_tv.ports import TvControl
from fakes import DEVICE_ID


def _unexpected(name: str) -> AssertionError:
    return AssertionError(f"{name} is not part of this test")


@dataclass
class StubControl:
    apps: tuple[TvApp, ...] = (TvApp("com.example.video", "Video"),)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def discover_devices(self, *, timeout: float = 5.0) -> list[Device]:
        self.calls.append(("discover_devices", str(timeout)))
        return []

    def get_status(self, device_id: DeviceId) -> DeviceStatus:
        self.calls.append(("get_status", device_id))
        raise DeviceNotFoundError("not found by the last discovery", device_id=device_id)

    def load_media(self, device_id: DeviceId, request: MediaRequest) -> CommandResult:
        raise _unexpected("load_media")

    def play(self, device_id: DeviceId) -> CommandResult:
        raise _unexpected("play")

    def pause(self, device_id: DeviceId) -> CommandResult:
        raise _unexpected("pause")

    def stop(self, device_id: DeviceId) -> CommandResult:
        raise _unexpected("stop")

    def close_app(self, device_id: DeviceId, receiver_session_id: str) -> CommandResult:
        raise _unexpected("close_app")

    def seek(self, device_id: DeviceId, position_seconds: float) -> CommandResult:
        raise _unexpected("seek")

    def set_volume(self, device_id: DeviceId, level: float) -> CommandResult:
        raise _unexpected("set_volume")

    def set_muted(self, device_id: DeviceId, muted: bool) -> CommandResult:
        raise _unexpected("set_muted")

    def get_remote_status(self, device_id: DeviceId) -> RemoteObservation:
        raise _unexpected("get_remote_status")

    def start_remote_pairing(self, device_id: DeviceId) -> None:
        raise _unexpected("start_remote_pairing")

    def finish_remote_pairing(self, device_id: DeviceId, code: str) -> None:
        raise _unexpected("finish_remote_pairing")

    def press_remote_key(self, device_id: DeviceId, key: RemoteKey) -> RemoteKeyResult:
        raise _unexpected("press_remote_key")

    def type_remote_text(self, device_id: DeviceId, text: str) -> RemoteTextResult:
        raise _unexpected("type_remote_text")

    def list_tv_apps(self, device_id: DeviceId) -> list[TvApp]:
        self.calls.append(("list_tv_apps", device_id))
        return list(self.apps)

    def launch_tv_app(self, device_id: DeviceId, package: str) -> AppLaunchResult:
        raise _unexpected("launch_tv_app")

    def pair_tv_apps(self, device_id: DeviceId, pairing_endpoint: str, code: str) -> None:
        raise _unexpected("pair_tv_apps")


def test_the_bridge_dispatches_to_any_implementation_of_the_port() -> None:
    stub = StubControl()
    control: TvControl = stub

    devices = dispatch(control, {"id": 1, "method": "discover_devices", "params": {}})
    apps = dispatch(
        control, {"id": 2, "method": "list_tv_apps", "params": {"deviceId": str(DEVICE_ID)}}
    )

    assert devices == {"id": 1, "ok": True, "result": {"devices": []}}
    assert apps["result"] == {"apps": [{"package": "com.example.video", "name": "Video"}]}
    assert stub.calls == [("discover_devices", "5.0"), ("list_tv_apps", str(DEVICE_ID))]


def test_an_error_of_the_port_keeps_its_code_through_the_bridge() -> None:
    stub = StubControl()

    request = {"id": 3, "method": "get_status", "params": {"deviceId": str(DEVICE_ID)}}

    response = handle_line(stub, json.dumps(request))

    assert response["ok"] is False
    assert as_object(response["error"])["code"] == "device_not_found"
    assert stub.calls == [("get_status", str(DEVICE_ID))]
