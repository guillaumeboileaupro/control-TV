from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path

from control_tv.adapters.device_memory import FileDeviceMemory
from control_tv.domain import Device, DeviceId, DeviceKind

FIRST = Device(
    id=DeviceId("11111111-1111-1111-1111-111111111111"),
    friendly_name="First",
    host="192.0.2.1",
    port=8009,
    kind=DeviceKind.CAST,
    model_name="TV",
    android_tv_remote=True,
)
SECOND = Device(
    id=DeviceId("22222222-2222-2222-2222-222222222222"),
    friendly_name="Second",
    host="192.0.2.2",
    port=8009,
)


def test_memory_is_private_persistent_and_updates_a_tv_by_stable_id(tmp_path: Path) -> None:
    path = tmp_path / "state" / "known-devices.json"
    memory = FileDeviceMemory(path)

    memory.remember(FIRST)
    memory.remember(SECOND)
    memory.remember(replace(FIRST, friendly_name="Renamed"))

    recalled = FileDeviceMemory(path).recall()
    assert [(device.id, device.friendly_name) for device in recalled] == [
        (FIRST.id, "Renamed"),
        (SECOND.id, "Second"),
    ]
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert all(device.known and not device.available for device in recalled)


def test_invalid_file_is_preserved_and_never_treated_as_a_device(tmp_path: Path) -> None:
    path = tmp_path / "known-devices.json"
    path.write_text("not json", encoding="utf-8")

    memory = FileDeviceMemory(path)
    assert memory.recall() == ()
    memory.remember(FIRST)
    memory.forget(FIRST.id)
    assert path.read_text(encoding="utf-8") == "not json"


def test_forgetting_known_device_does_not_touch_pairing_credentials(tmp_path: Path) -> None:
    directory = tmp_path / "control-tv"
    path = directory / "known-devices.json"
    certificate = directory / "tv-remote" / "client.pem"
    certificate.parent.mkdir(parents=True)
    certificate.write_text("preserve", encoding="utf-8")
    memory = FileDeviceMemory(path)
    memory.remember(FIRST)
    memory.remember(SECOND)

    memory.forget(FIRST.id)

    assert [device.id for device in memory.recall()] == [SECOND.id]
    assert certificate.read_text(encoding="utf-8") == "preserve"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
