"""D2: the TV as a second screen of the local X11 desktop, for "Étendre".

GNOME (Mutter) treats only real RandR outputs as monitors: a logical `xrandr --setmonitor`
monitor over an enlarged framebuffer is undone within half a second (seen on the owner's
desktop), so windows cannot go there and the capture falls outside the screen. `create` therefore
turns a free video output of the computer into an extra screen: a mode of the chosen size and
refresh rate is defined (`cvt`), added to that output and enabled to the left, right, above or
below the primary screen. GNOME extends the desktop onto it like a plugged monitor; the capture
grabs it like any output. GNOME ignores an output that reports "disconnected" (the area stays
black and windows cannot go there, seen on the owner's desktop): the output must first be
reported connected with no screen behind it, which the owner does once per boot with
`echo on-digital | sudo tee /sys/class/drm/<card>-HDMI-A-<n>/status` (root, not done here).

Nothing is written to a configuration file: these are runtime changes of the running X server
only. `remove` turns the output off and deletes the mode (idempotent); a mode left by an
interrupted run is deleted before a new one is defined. Changing the display needs the owner's
explicit consent: the caller asks for it, this module only refuses without it.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from control_tv.screen_streaming.model import (
    InvalidStreamConfiguration,
    StreamCleanupError,
    StreamStartError,
)
from control_tv.screen_streaming.x11_capture import (
    Runner,
    X11Capture,
    X11Output,
    parse_monitors,
    run_command,
)

MODE_PREFIX = "control-tv-"
# Measured on the owner's desktop (2026-10-20): every size at 60 and 50 Hz, in every position, was
# accepted; 1920x1080 at 30 Hz was not, so 30 Hz is not offered.
SIZES = ((1280, 720), (1920, 1080))
REFRESH_RATES = (60, 50)
_OUTPUT = re.compile(
    r"^(\S+) (connected|disconnected)( primary)?(?:.*\) (\d+)mm x (\d+)mm)?", re.MULTILINE
)
_MODELINE = re.compile(r'^Modeline "[^"]+"\s+(.+)$', re.MULTILINE)
_ACCEPT_SECONDS = 3.0
_ACCEPT_POLL_SECONDS = 0.1


class Position(StrEnum):
    """Where the extra screen sits next to the primary one (xrandr's own words)."""

    LEFT = "left-of"
    RIGHT = "right-of"
    ABOVE = "above"
    BELOW = "below"


@dataclass(frozen=True)
class _Extension:
    output: str
    mode: str


class XrandrVirtualMonitor:
    """One extra screen at a time on a free video output, enabled and removed with `xrandr`."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        run: Runner = run_command,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._run = run
        self._sleep = sleep
        self._extension: _Extension | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._extension is not None

    def free_output(self) -> str | None:
        """The output an extension would use (read only), or None when none is enabled."""
        return _outputs(self._xrandr("--query"))[1]

    def create(
        self,
        width: int,
        height: int,
        *,
        consent: bool,
        refresh: int = 60,
        position: Position = Position.RIGHT,
    ) -> X11Output:
        """Extend the desktop onto a free output (`width` x `height` at `refresh` Hz, placed at
        `position` of the primary screen); `consent` is the owner's explicit agreement."""
        if consent is not True:
            raise StreamStartError("extending the display needs the owner's explicit consent")
        if (width, height) not in SIZES:
            raise InvalidStreamConfiguration("the extended screen is 1280x720 or 1920x1080")
        if refresh not in REFRESH_RATES:
            raise InvalidStreamConfiguration("the extended screen refreshes at 60 or 50 Hz")
        if type(position) is not Position:
            raise InvalidStreamConfiguration("the extended screen is left, right, above or below")
        mode = f"{MODE_PREFIX}{width}x{height}-{refresh}"
        with self._lock:
            if self._extension is not None:
                raise StreamStartError("the display is already extended: stop it first")
            listing = self._xrandr("--query")
            primary, free = _outputs(listing)
            if primary is None:
                raise StreamStartError("the primary screen cannot be found")
            if free is None:
                raise StreamStartError(
                    "no free video output is enabled for the extension: enable one first "
                    "(echo on-digital | sudo tee /sys/class/drm/<card>-HDMI-A-<n>/status)"
                )
            extension = _Extension(free, mode)
            if mode in listing:
                self._forget(extension)  # left by an interrupted run
            try:
                self._xrandr("--newmode", mode, *self._modeline(width, height, refresh))
                self._xrandr("--addmode", free, mode)
                self._xrandr("--output", free, "--mode", mode, f"--{position.value}", primary)
                output = self._accepted(free, width, height)
            except BaseException:
                self._forget(extension)
                raise
            self._extension = extension
        return output

    def remove(self) -> None:
        """Turn the extra screen off and delete its mode. Idempotent; a failure keeps it for a
        retry."""
        with self._lock:
            extension = self._extension
            if extension is None:
                return
            self._xrandr("--output", extension.output, "--off", cleanup=True)
            self._xrandr("--delmode", extension.output, extension.mode, cleanup=True)
            self._xrandr("--rmmode", extension.mode, cleanup=True)
            self._extension = None

    def _accepted(self, output: str, width: int, height: int) -> X11Output:
        """The new screen once the desktop lists it; GNOME may refuse a mode silently."""
        waited = 0.0
        while True:
            for monitor in parse_monitors(self._xrandr("--listmonitors")):
                if monitor.name == output and (monitor.width, monitor.height) == (width, height):
                    return monitor
            if waited >= _ACCEPT_SECONDS:
                raise StreamStartError("the desktop did not accept the extended screen")
            self._sleep(_ACCEPT_POLL_SECONDS)
            waited += _ACCEPT_POLL_SECONDS

    def _forget(self, extension: _Extension) -> None:
        """Best-effort undo of a partial or leftover extension (each step may already be gone)."""
        for arguments in (
            ("--output", extension.output, "--off"),
            ("--delmode", extension.output, extension.mode),
            ("--rmmode", extension.mode),
        ):
            with contextlib.suppress(StreamCleanupError):
                self._xrandr(*arguments, cleanup=True)

    def _modeline(self, width: int, height: int, refresh: int) -> tuple[str, ...]:
        try:
            done = self._run(("cvt", str(width), str(height), str(refresh)), self._environ)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StreamStartError(f"cvt failed: {type(error).__name__}") from None
        match = _MODELINE.search(done.stdout) if done.returncode == 0 else None
        if match is None:
            raise StreamStartError("cvt gave no mode for the extended screen")
        return tuple(match.group(1).split())

    def _xrandr(self, *arguments: str, cleanup: bool = False) -> str:
        error_type = StreamCleanupError if cleanup else StreamStartError
        display = X11Capture(self._environ, self._run).display
        try:
            done = self._run(("xrandr", *arguments), {**self._environ, "DISPLAY": display})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise error_type(f"xrandr {arguments[0]} failed: {type(error).__name__}") from None
        if done.returncode != 0:
            raise error_type(f"xrandr {arguments[0]} failed")
        return done.stdout


def _outputs(listing: str) -> tuple[str | None, str | None]:
    """The primary connected output and the output to extend onto: a connector reported
    connected with no physical size (forced on, no screen behind it), HDMI before DisplayPort.
    A disconnected output is never used: the desktop would ignore it."""
    primary: str | None = None
    first_connected: str | None = None
    forced: list[str] = []
    for name, state, is_primary, width_mm, height_mm in _OUTPUT.findall(listing):
        if state != "connected":
            continue
        if not is_primary and (width_mm, height_mm) == ("0", "0"):
            forced.append(name)
        else:
            first_connected = first_connected or name
            if is_primary:
                primary = name
    forced.sort(key=lambda name: not name.startswith("HDMI"))
    return primary or first_connected, (forced[0] if forced else None)
