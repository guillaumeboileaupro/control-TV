"""`ScreenCapture` for D1: one output (monitor) of the local X11 session, captured by FFmpeg.

The outputs are read with `xrandr --listmonitors` (read only: nothing on the display is changed).
Starting a capture only resolves the chosen output's region; the grab itself is FFmpeg's
`x11grab` input of the live encoder, so it ends with the encoder. Only a local X11 session is
accepted (not Wayland, never a remote X server). A virtual monitor (D2) is listed like any other.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from control_tv.screen_streaming.model import (
    CaptureHandle,
    DisplaySelection,
    InvalidStreamConfiguration,
    StreamCleanupError,
    StreamStartError,
)
from control_tv.screencast.model import InvalidScreenCastOption, X11GrabSource

# " 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1" (name, width/mm x height/mm + x + y, outputs).
# RandR uses signed coordinates when a monitor sits left of or above the primary one.
_MONITOR = re.compile(r"\s*\d+:\s+[+*]*(\S+)\s+(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)(?:\s.*)?")
_LIST_TIMEOUT = 5.0

Runner = Callable[[Sequence[str], Mapping[str, str]], "subprocess.CompletedProcess[str]"]


def run_command(
    argv: Sequence[str], environ: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
        env=dict(environ),
        timeout=_LIST_TIMEOUT,
    )


@dataclass(frozen=True)
class X11Output:
    """One monitor of the X11 screen, as the capture sees it."""

    name: str
    width: int
    height: int
    x: int
    y: int
    primary: bool


def parse_monitors(listing: str) -> tuple[X11Output, ...]:
    """The monitors of `xrandr --listmonitors`; unparsable lines are ignored."""
    outputs: list[X11Output] = []
    for line in listing.splitlines()[1:]:
        match = _MONITOR.fullmatch(line)
        if match is None:
            continue
        name, width, height, x, y = match.groups()
        primary = "*" in line.split(":", 1)[1].split()[0]
        outputs.append(X11Output(name, int(width), int(height), int(x), int(y), primary))
    return tuple(outputs)


class X11Capture:
    """`ScreenCapture` over the local X11 session's outputs."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        run: Runner = run_command,
    ) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._run = run
        self._captures: dict[str, X11GrabSource] = {}
        self._released: set[str] = set()
        self._lock = threading.Lock()

    @property
    def display(self) -> str:
        """The local X11 display; `InvalidStreamConfiguration` when there is none."""
        session = self._environ.get("XDG_SESSION_TYPE", "x11")
        if session != "x11":
            raise InvalidStreamConfiguration(
                f"screen capture needs an X11 session, not {session!r} (Wayland is not supported)"
            )
        display = self._environ.get("DISPLAY", "")
        try:
            X11GrabSource(display, 0, 0, 16, 16)  # the same local-display rule as the encoder
        except InvalidScreenCastOption:
            raise InvalidStreamConfiguration(
                "screen capture needs a local X11 display (DISPLAY=:N)"
            ) from None
        return display

    def outputs(self) -> tuple[X11Output, ...]:
        """The X11 screen's monitors, primary first (read only)."""
        display = self.display
        try:
            listed = self._run(("xrandr", "--listmonitors"), {**self._environ, "DISPLAY": display})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise StreamStartError(
                f"the screen's outputs cannot be read: {type(error).__name__}"
            ) from None
        if listed.returncode != 0:
            raise StreamStartError("the screen's outputs cannot be read (xrandr failed)")
        outputs = parse_monitors(listed.stdout)
        return tuple(sorted(outputs, key=lambda output: not output.primary))

    def start(self, display: DisplaySelection) -> CaptureHandle:
        """Resolve `display`'s output into the region the encoder grabs."""
        output = next(
            (output for output in self.outputs() if output.name == display.output_id), None
        )
        if output is None:
            raise StreamStartError("that screen output is not connected")
        # H.264 needs an even size: an odd output loses its last row or column.
        width, height = output.width - output.width % 2, output.height - output.height % 2
        try:
            region = X11GrabSource(self.display, output.x, output.y, width, height)
        except InvalidScreenCastOption as error:
            raise StreamStartError(f"that output cannot be captured: {error.message}") from None
        handle = CaptureHandle(f"x11-{uuid.uuid4().hex}")
        with self._lock:
            self._captures[handle.value] = region
        return handle

    def region(self, capture: CaptureHandle) -> X11GrabSource:
        """The region to grab for `capture` (for the live encoder)."""
        with self._lock:
            region = self._captures.get(capture.value)
        if region is None:
            raise StreamStartError("this capture is not active")
        return region

    def stop(self, capture: CaptureHandle) -> None:
        """Forget the capture (FFmpeg's grab ends with the encoder). Idempotent."""
        with self._lock:
            if self._captures.pop(capture.value, None) is not None:
                self._released.add(capture.value)
                return
            if capture.value in self._released:
                return
        raise StreamCleanupError("this capture was never started here")
