"""Private, durable memory of TVs explicitly kept by the person using this installation.

The file contains discovery coordinates, not credentials. It is replaced atomically with
private permissions. Invalid or unreadable content fails closed to an empty memory and is
never deleted or overwritten merely because a discovery returned nothing.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from control_tv.domain import Device, DeviceId, DeviceKind, InvalidArgumentError


class FileDeviceMemory:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def recall(self) -> tuple[Device, ...]:
        return self._read() or ()

    def _read(self) -> tuple[Device, ...] | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            entries = data["devices"]
            if not isinstance(entries, list):
                return None
            devices = tuple(self._decode(entry) for entry in entries)
        except FileNotFoundError:
            return ()
        except (OSError, ValueError, KeyError, TypeError, InvalidArgumentError):
            return None
        return devices

    def remember(self, device: Device) -> bool:
        recalled = self._read()
        if recalled is None:
            return False
        devices = {known.id: known for known in recalled}
        devices[device.id] = Device(
            id=device.id,
            friendly_name=device.friendly_name,
            host=device.host,
            port=device.port,
            kind=device.kind,
            model_name=device.model_name,
            android_tv_remote=device.android_tv_remote,
            known=True,
            available=False,
        )
        return self._write(tuple(devices.values()))

    def forget(self, device_id: DeviceId) -> bool:
        recalled = self._read()
        if recalled is None:
            return False
        devices = tuple(device for device in recalled if device.id != device_id)
        return self._write(devices)

    def _write(self, devices: tuple[Device, ...]) -> bool:
        content = json.dumps(
            {"version": 1, "devices": [self._encode(device) for device in devices]},
            sort_keys=True,
        )
        partial = self._path.with_name(self._path.name + ".partial")
        try:
            self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._path.parent, 0o700)
            partial.unlink(missing_ok=True)
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(content)
            os.replace(partial, self._path)
            os.chmod(self._path, 0o600)
            return True
        except OSError:
            with contextlib.suppress(OSError):
                partial.unlink(missing_ok=True)
            return False

    @staticmethod
    def _encode(device: Device) -> dict[str, object]:
        return {
            "id": str(device.id),
            "friendlyName": device.friendly_name,
            "host": device.host,
            "port": device.port,
            "kind": device.kind.value,
            "modelName": device.model_name,
            "androidTvRemote": device.android_tv_remote,
        }

    @staticmethod
    def _decode(value: object) -> Device:
        if not isinstance(value, dict):
            raise ValueError("device entry must be an object")
        device_id = value.get("id")
        name = value.get("friendlyName")
        host = value.get("host")
        port = value.get("port")
        kind = value.get("kind")
        model = value.get("modelName")
        remote = value.get("androidTvRemote")
        if not all(isinstance(item, str) for item in (device_id, name, host, kind)):
            raise ValueError("device text fields must be strings")
        if not isinstance(port, int) or isinstance(port, bool):
            raise ValueError("device port must be an integer")
        if model is not None and not isinstance(model, str):
            raise ValueError("device model must be a string or null")
        if remote is not None and not isinstance(remote, bool):
            raise ValueError("remote capability must be a boolean or null")
        assert isinstance(device_id, str)
        assert isinstance(name, str)
        assert isinstance(host, str)
        assert isinstance(kind, str)
        return Device(
            id=DeviceId(device_id),
            friendly_name=name,
            host=host,
            port=port,
            kind=DeviceKind(kind),
            model_name=model,
            android_tv_remote=remote,
            known=True,
            available=False,
        )
