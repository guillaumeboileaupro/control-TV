"""Shared fake `xrandr` for the X11 capture, the virtual monitor and the screen controller tests."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence

from control_tv.screen_streaming.virtual_display import VIRTUAL_MONITOR

LISTING = """Monitors: 2
 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1
 1: +HDMI-1 2561/597x1441/336+1920+0  HDMI-1
"""
X11 = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}
Done = subprocess.CompletedProcess[str]


class FakeXrandr:
    """`xrandr` on a 3840x1080 screen (maximum 16384): records calls, applies --fb/--setmonitor."""

    def __init__(self, *, fail: str | None = None, leftover: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.width, self.height = 3840, 1080
        self.virtual = leftover
        self.fail = fail

    def __call__(
        self, argv: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        arguments = tuple(argv[1:])
        self.calls.append(arguments)
        assert environ["DISPLAY"] == ":0"
        if self.fail == arguments[0]:
            return subprocess.CompletedProcess(argv, 1, "", "refused")
        out = ""
        if arguments[0] == "--listmonitors":
            out = LISTING + (
                f" 2: {VIRTUAL_MONITOR} 1280/338x720/190+3840+0  none\n" if self.virtual else ""
            )
        elif arguments[0] == "--current":
            current = f"current {self.width} x {self.height}"
            out = f"Screen 0: minimum 320 x 200, {current}, maximum 16384 x 16384\n"
        elif arguments[0] == "--fb":
            width, height = arguments[1].split("x")
            self.width, self.height = int(width), int(height)
        elif arguments[0] == "--setmonitor":
            self.virtual = True
        elif arguments[0] == "--delmonitor":
            self.virtual = False
        return subprocess.CompletedProcess(argv, 0, out, "")
