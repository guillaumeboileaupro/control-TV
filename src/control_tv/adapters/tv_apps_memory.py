"""The last app list each TV gave, kept on this device for when its debugging is off.

A TV's debugging (which lists its apps) turns itself off, or is never turned on; the window then
still offers the apps the TV listed last time, opened through the TV's remote. One small JSON
file per TV in a private directory (0700, files 0600), named by a hash of the TV's id so no
device id appears in a file name. Best effort: an unreadable list is `None`, an unwritable one
is not kept; neither stops the window.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path

from control_tv.adapters.tv_apps import KNOWN_NAMES, checked_package
from control_tv.domain import ControlError, DeviceId, TvApp

# Not apps to open from a list: the home screen itself and its recommendations service.
_NOT_OFFERED = {"com.google.android.apps.tv.launcherx", "com.google.android.tvrecommendations"}


def known_apps() -> tuple[TvApp, ...]:
    """The apps control-TV knows by name, offered when a TV never listed its own."""
    return tuple(
        TvApp(package=package, name=name)
        for package, name in sorted(KNOWN_NAMES.items(), key=lambda item: item[1].lower())
        if package not in _NOT_OFFERED
    )


class FileAppMemory:
    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    def recall(self, device_id: DeviceId) -> tuple[TvApp, ...] | None:
        try:
            data = json.loads(self._file(device_id).read_text(encoding="utf-8"))
            apps = tuple(
                TvApp(
                    package=checked_package(entry["package"]),
                    name=KNOWN_NAMES.get(entry["package"]),
                )
                for entry in data["apps"]
            )
        except (OSError, ValueError, KeyError, TypeError, ControlError):
            return None
        return apps or None

    def remember(self, device_id: DeviceId, apps: tuple[TvApp, ...]) -> None:
        content = json.dumps({"apps": [{"package": app.package} for app in apps]})
        target = self._file(device_id)
        partial = target.with_name(target.name + ".partial")
        try:
            self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self._dir, 0o700)
            partial.unlink(missing_ok=True)
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(content)
            os.replace(partial, target)
        except OSError:
            with contextlib.suppress(OSError):
                partial.unlink(missing_ok=True)

    def _file(self, device_id: DeviceId) -> Path:
        digest = hashlib.sha256(str(device_id).encode("utf-8")).hexdigest()[:32]
        return self._dir / f"{digest}.json"
