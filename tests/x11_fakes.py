"""Shared fake `xrandr` (and `cvt`) for the X11 capture, the extended screen and the screen
controller tests."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence

LISTING = """Monitors: 2
 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1
 1: +HDMI-1 2561/597x1441/336+1920+0  HDMI-1
"""
X11 = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}
Done = subprocess.CompletedProcess[str]
CVT = (
    "# 1920x1080 59.96 Hz (CVT 2.07M9) hsync: 67.16 kHz; pclk: 173.00 MHz\n"
    'Modeline "1920x1080_60.00"  173.00  1920 2048 2248 2576  1080 1083 1088 1120 -hsync +vsync\n'
)
_PLACES = {"--right-of": (1920, 0), "--left-of": (0, 0), "--above": (0, 0), "--below": (0, 1080)}


class FakeXrandr:
    """`xrandr` on the eDP-1 + HDMI-1 desktop of `LISTING`, with HDMI-2 forced on (connected, no
    screen) unless `forced` is False: records calls, defines and enables modes."""

    def __init__(
        self,
        *,
        fail: str | None = None,
        forced: bool = True,
        accept: bool = True,
        leftover: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail = fail
        self.forced = forced
        self.accept = accept
        self.modes: set[str] = set() if leftover is None else {leftover}
        # Modes added to an output (`--addmode`): like the real xrandr, `--delmode` refuses a mode
        # that is not on the output and `--rmmode` one that is undefined or still on an output.
        self.added: set[tuple[str, str]] = set()
        self.enabled: tuple[str, int, int, int, int] | None = None  # output, w, h, x, y
        self.shown: str | None = None  # the mode the enabled output displays
        # Where the desktop's own screens are (moved when the TV goes left of or above them).
        self.places = {"eDP-1": (0, 0), "HDMI-1": (1920, 0)}

    def __call__(
        self, argv: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        if argv[0] == "cvt":
            self.calls.append(tuple(argv))
            if self.fail == "cvt":
                return subprocess.CompletedProcess(argv, 1, "", "refused")
            return subprocess.CompletedProcess(argv, 0, CVT, "")
        arguments = tuple(argv[1:])
        self.calls.append(arguments)
        assert environ["DISPLAY"] == ":0"
        if self.fail == arguments[0]:
            return subprocess.CompletedProcess(argv, 1, "", "refused")
        out = ""
        if arguments[0] == "--listmonitors":
            sizes = {"eDP-1": "*eDP-1 1920/344x1080/194", "HDMI-1": "HDMI-1 2561/597x1441/336"}
            out = "Monitors: 2\n" + "".join(
                f" {index}: +{sizes[name]}+{x}+{y}  {name}\n"
                for index, (name, (x, y)) in enumerate(self.places.items())
            )
            if self.enabled is not None and self.accept:
                name, width, height, x, y = self.enabled
                out += f" 2: +{name} {width}/508x{height}/286{x:+d}{y:+d}  {name}\n"
        elif arguments[0] == "--query":
            out = self._query()
        elif arguments[0] == "--newmode":
            self.modes.add(arguments[1])
        elif arguments[0] == "--addmode":
            self.added.add((arguments[1], arguments[2]))
        elif arguments[0] == "--delmode":
            if (arguments[1], arguments[2]) not in self.added:
                return subprocess.CompletedProcess(argv, 1, "", "cannot find mode on output")
            self.added.discard((arguments[1], arguments[2]))
        elif arguments[0] == "--rmmode":
            if arguments[1] not in self.modes or any(m == arguments[1] for _, m in self.added):
                return subprocess.CompletedProcess(argv, 1, "", "cannot remove mode")
            self.modes.discard(arguments[1])
        elif arguments[0] == "--output":
            self._outputs(arguments)
        return subprocess.CompletedProcess(argv, 0, out, "")

    def _outputs(self, arguments: tuple[str, ...]) -> None:
        """Every `--output NAME ...` group of one command."""
        starts = [index for index, value in enumerate(arguments) if value == "--output"]
        for start, end in zip(starts, [*starts[1:], len(arguments)], strict=True):
            name, *rest = arguments[start + 1 : end]
            if rest == ["--off"]:
                self.enabled = self.shown = None
            elif rest[0] == "--pos":  # a screen of the desktop moved
                x, y = (int(value) for value in rest[1].split("x"))
                self.places[name] = (x, y)
            else:
                size = rest[1].removeprefix("control-tv-").split("-")[0]
                width, height = (int(value) for value in size.split("x"))
                if rest[2] == "--pos":
                    x, y = (int(value) for value in rest[3].split("x"))
                else:
                    x, y = _PLACES[rest[2]]
                self.enabled, self.shown = (name, width, height, x, y), rest[1]

    def _query(self) -> str:
        lines = [
            "Screen 0: minimum 320 x 200, current 4481 x 1441, maximum 16384 x 16384",
            "eDP-1 connected primary 1920x1080+0+0 (normal left inverted right) 344mm x 194mm",
            "HDMI-1 connected 2561x1441+1920+0 (normal left inverted right) 597mm x 336mm",
            "HDMI-2 "
            + ("connected" if self.forced else "disconnected")
            + " (normal left inverted right x axis y axis)"
            + (" 0mm x 0mm" if self.forced else ""),
        ]
        # Like xrandr: the modes of an output under it, the current one marked `*`, and the
        # modes on no output at the end.
        on_output = sorted(mode for output, mode in self.added if output == "HDMI-2")
        lines += [f"   {mode}  60.00{'*' if mode == self.shown else ''}" for mode in on_output]
        lines.append("DP-1 disconnected (normal left inverted right x axis y axis)")
        lines += [
            f"  {mode} (0x1a8)  173.000MHz -HSync +VSync"
            for mode in sorted(self.modes)
            if mode not in on_output
        ]
        return "\n".join(lines) + "\n"
