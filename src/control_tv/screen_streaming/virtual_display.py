"""D2: an independent virtual monitor on the local X11 screen, for "Étendre".

`create` enlarges the X framebuffer to the right of the current screen (`xrandr --fb`) and
declares a logical monitor there (`xrandr --setmonitor control-tv-virtual ... none`): the
desktop treats it as one more monitor (windows can be moved onto it) and the capture grabs it
like any output. `remove` deletes that monitor and restores the framebuffer's original size.
Nothing is written to a configuration file: these are runtime changes of the running X server
only, gone at the latest when the session ends, and undone by `remove` (idempotent). A monitor
left by an interrupted run is removed before a new one is created. Changing the display needs
the owner's explicit consent: the caller asks for it, this module only refuses without it.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass

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

VIRTUAL_MONITOR = "control-tv-virtual"
_SCREEN = re.compile(r"Screen \d+: minimum \d+ x \d+, current (\d+) x (\d+), maximum (\d+) x (\d+)")
_SIZES = {(1280, 720), (1920, 1080)}


@dataclass(frozen=True)
class _Framebuffer:
    width: int
    height: int


class XrandrVirtualMonitor:
    """One virtual monitor at a time, created and removed with `xrandr` (runtime only)."""

    def __init__(self, environ: Mapping[str, str] | None = None, run: Runner = run_command) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._run = run
        self._original: _Framebuffer | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._original is not None

    def create(self, width: int, height: int, *, consent: bool) -> X11Output:
        """Add the virtual monitor (`width` x `height`, one of 1280x720 or 1920x1080) to the
        right of the screen; `consent` is the owner's explicit agreement to change the display."""
        if consent is not True:
            raise StreamStartError("extending the display needs the owner's explicit consent")
        if (width, height) not in _SIZES:
            raise InvalidStreamConfiguration("the virtual monitor is 1280x720 or 1920x1080")
        with self._lock:
            if self._original is not None:
                raise StreamStartError("a virtual monitor already exists: stop it first")
            if any(output.name == VIRTUAL_MONITOR for output in self._monitors()):
                self._xrandr("--delmonitor", VIRTUAL_MONITOR)  # left by an interrupted run
            current, maximum = self._framebuffer()
            grown = _Framebuffer(current.width + width, max(current.height, height))
            if grown.width > maximum.width or grown.height > maximum.height:
                raise StreamStartError("the X screen cannot grow enough for a virtual monitor")
            self._xrandr("--fb", f"{grown.width}x{grown.height}")
            try:
                millimetres = f"{width}/{width * 254 // 960}x{height}/{height * 254 // 960}"
                self._xrandr(
                    "--setmonitor", VIRTUAL_MONITOR, f"{millimetres}+{current.width}+0", "none"
                )
            except BaseException:
                self._restore(current)
                raise
            self._original = current
        return X11Output(VIRTUAL_MONITOR, width, height, current.width, 0, False)

    def remove(self) -> None:
        """Delete the virtual monitor and restore the screen size. Idempotent; a failure keeps
        it for a retry."""
        with self._lock:
            original = self._original
            if original is None:
                return
            if any(output.name == VIRTUAL_MONITOR for output in self._monitors()):
                self._xrandr("--delmonitor", VIRTUAL_MONITOR, cleanup=True)
            self._restore(original)
            self._original = None

    def _restore(self, framebuffer: _Framebuffer) -> None:
        self._xrandr("--fb", f"{framebuffer.width}x{framebuffer.height}", cleanup=True)

    def _monitors(self) -> tuple[X11Output, ...]:
        return parse_monitors(self._xrandr("--listmonitors"))

    def _framebuffer(self) -> tuple[_Framebuffer, _Framebuffer]:
        match = _SCREEN.search(self._xrandr("--current"))
        if match is None:
            raise StreamStartError("the X screen size cannot be read")
        width, height, maximum_width, maximum_height = (int(value) for value in match.groups())
        return _Framebuffer(width, height), _Framebuffer(maximum_width, maximum_height)

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
