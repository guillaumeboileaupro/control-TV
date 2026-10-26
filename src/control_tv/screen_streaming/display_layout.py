"""The desktop's screens, as GNOME's Settings > Displays shows them, read and changed with xrandr.

`DisplayManager.layout()` reads every output of the local X11 screen (`xrandr --verbose`): its
modes (size and refresh rate, by mode id), whether it is on, its position, orientation, scale and
whether it is the primary screen. `apply()` sets every output in one `xrandr` command, checks
that the X server now reports exactly that, and restores the previous configuration at once if
the command fails or the result differs. A configuration that was applied must then be kept
explicitly (`confirm()`) within `confirm_seconds`, otherwise it is reverted by itself, so a screen
left unusable comes back on its own; `revert()` restores it on request, `close()` when the
application quits. Everything is a runtime change of the running X server: nothing is written to
a configuration file, and GNOME's own saved configuration is untouched.

Scale follows xrandr: a factor below 1 renders fewer desktop pixels on the screen, so everything
looks larger (125 % is 0.8); orientation rotates the screen, so a quarter turn swaps its logical
width and height.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from control_tv.screen_streaming.model import (
    InvalidStreamConfiguration,
    StreamCleanupError,
    StreamStartError,
)
from control_tv.screen_streaming.x11_capture import Runner, X11Capture, run_command

CONFIRM_SECONDS = 20.0
# The scales GNOME offers, as xrandr factors: 100 %, 125 %, 150 % and 200 %.
SCALES = (1.0, 0.8, 0.667, 0.5)
_MAX_COORDINATE = 32767
_HEADER = re.compile(
    r"^(?P<name>\S+) (?P<state>connected|disconnected)(?P<primary> primary)?"
    r"(?: (?P<w>\d+)x(?P<h>\d+)\+(?P<x>-?\d+)\+(?P<y>-?\d+) \((?P<mode>0x[0-9a-f]+)\)"
    r" (?P<rotation>normal|left|inverted|right))?"
)
_MODE = re.compile(r"^\s+(?P<name>\S+) \((?P<id>0x[0-9a-f]+)\) [\d.]+MHz")
_WIDTH = re.compile(r"^\s+h: width\s+(\d+)")
_HEIGHT = re.compile(r"^\s+v: height\s+(\d+).*clock\s+([\d.]+)Hz")
_TRANSFORM = re.compile(r"^\s+Transform:\s+([\d.]+)")


class Rotation(StrEnum):
    NORMAL = "normal"
    LEFT = "left"
    RIGHT = "right"
    INVERTED = "inverted"


@dataclass(frozen=True)
class DisplayMode:
    """One mode an output offers: its X mode id, size and refresh rate."""

    id: str
    width: int
    height: int
    rate: float


@dataclass(frozen=True)
class OutputSetting:
    """How one output is set: off, or on with a mode, a position, orientation and scale."""

    name: str
    enabled: bool
    mode: str | None = None  # the X mode id, for example "0x48"
    x: int = 0
    y: int = 0
    rotation: Rotation = Rotation.NORMAL
    scale: float = 1.0
    primary: bool = False


@dataclass(frozen=True)
class Display:
    """One output as the X server reports it, with the modes it offers."""

    name: str
    connected: bool
    setting: OutputSetting
    modes: tuple[DisplayMode, ...]

    def mode(self, mode_id: str | None) -> DisplayMode | None:
        return next((mode for mode in self.modes if mode.id == mode_id), None)


def parse_layout(verbose: str) -> tuple[Display, ...]:
    """The outputs of `xrandr --verbose`; an output with neither a connection nor a mode is kept
    (it may be enabled), modes are listed in the X server's order (preferred first)."""
    displays: list[Display] = []
    header: re.Match[str] | None = None
    modes: list[DisplayMode] = []
    scale = 1.0
    pending: tuple[str, str] | None = None  # mode name and id waiting for its size lines
    width = 0

    def flush() -> None:
        if header is None:
            return
        enabled = header["mode"] is not None
        setting = OutputSetting(
            name=header["name"],
            enabled=enabled,
            mode=header["mode"],
            x=int(header["x"] or 0),
            y=int(header["y"] or 0),
            rotation=Rotation(header["rotation"] or "normal"),
            scale=scale,
            primary=header["primary"] is not None,
        )
        displays.append(
            Display(header["name"], header["state"] == "connected", setting, tuple(modes))
        )

    for line in verbose.splitlines():
        found = _HEADER.match(line)
        if found is not None:
            flush()
            header, modes, scale, pending = found, [], 1.0, None
            continue
        if header is None:
            continue
        transform = _TRANSFORM.match(line)
        if transform is not None:
            scale = round(float(transform.group(1)), 3)
            continue
        mode = _MODE.match(line)
        if mode is not None:
            pending = (mode["name"], mode["id"])
            continue
        size = _WIDTH.match(line)
        if size is not None and pending is not None:
            width = int(size.group(1))
            continue
        tall = _HEIGHT.match(line)
        if tall is not None and pending is not None:
            rate = round(float(tall.group(2)), 2)
            modes.append(DisplayMode(pending[1], width, int(tall.group(1)), rate))
            pending = None
    flush()
    return tuple(displays)


def logical_size(display: Display, setting: OutputSetting) -> tuple[int, int]:
    """The size the screen takes on the desktop: its mode, turned and scaled."""
    mode = display.mode(setting.mode)
    if mode is None:
        return 0, 0
    width, height = mode.width, mode.height
    if setting.rotation in (Rotation.LEFT, Rotation.RIGHT):
        width, height = height, width
    return round(width * setting.scale), round(height * setting.scale)


def check(displays: Sequence[Display], settings: Sequence[OutputSetting]) -> None:
    """Refuse a configuration the X server or the owner could not use: an unknown output or mode,
    no screen on, not exactly one primary screen on, coordinates out of range, or screens that
    overlap without being clones (same position and size)."""
    by_name = {display.name: display for display in displays}
    names = [setting.name for setting in settings]
    if len(set(names)) != len(names) or set(names) != set(by_name):
        raise InvalidStreamConfiguration("the configuration must set every screen exactly once")
    on = [setting for setting in settings if setting.enabled]
    if not on:
        raise InvalidStreamConfiguration("at least one screen must stay on")
    if sum(setting.primary for setting in on) != 1 or any(
        setting.primary for setting in settings if not setting.enabled
    ):
        raise InvalidStreamConfiguration("exactly one screen that is on must be the main one")
    boxes: list[tuple[int, int, int, int]] = []
    for setting in on:
        display = by_name[setting.name]
        if display.mode(setting.mode) is None:
            raise InvalidStreamConfiguration(f"{setting.name} does not offer that resolution")
        if type(setting.rotation) is not Rotation:
            raise InvalidStreamConfiguration("the orientation is normal, left, right or inverted")
        if setting.scale not in SCALES:
            raise InvalidStreamConfiguration("the scale is 100, 125, 150 or 200 %")
        for value in (setting.x, setting.y):
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidStreamConfiguration("a screen position must be whole pixels")
            if not 0 <= value <= _MAX_COORDINATE:
                raise InvalidStreamConfiguration("a screen position is out of range")
        width, height = logical_size(display, setting)
        boxes.append((setting.x, setting.y, width, height))
    for index, (x, y, width, height) in enumerate(boxes):
        for other in boxes[index + 1 :]:
            if (x, y, width, height) == other:
                continue  # a clone
            ox, oy, ow, oh = other
            if x < ox + ow and ox < x + width and y < oy + oh and oy < y + height:
                raise InvalidStreamConfiguration("two screens overlap: move one beside the other")


def xrandr_arguments(settings: Sequence[OutputSetting]) -> tuple[str, ...]:
    """One `xrandr` command line setting every output (the screens that stay on first)."""
    arguments: list[str] = []
    for setting in sorted(settings, key=lambda setting: not setting.enabled):
        if not setting.enabled:
            arguments += ["--output", setting.name, "--off"]
            continue
        assert setting.mode is not None  # checked
        arguments += [
            "--output",
            setting.name,
            "--mode",
            setting.mode,
            "--pos",
            f"{setting.x}x{setting.y}",
            "--rotate",
            setting.rotation.value,
            "--scale",
            f"{setting.scale:g}x{setting.scale:g}",
        ]
        if setting.primary:
            arguments.append("--primary")
    return tuple(arguments)


class DisplayManager:
    """Reads and applies the desktop's screen configuration, with confirmation and rollback."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        run: Runner = run_command,
        *,
        confirm_seconds: float = CONFIRM_SECONDS,
        timer: Callable[[float, Callable[[], None]], threading.Timer] = threading.Timer,
    ) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._run = run
        self._confirm_seconds = confirm_seconds
        self._timer = timer
        self._lock = threading.RLock()
        self._previous: tuple[OutputSetting, ...] | None = None
        self._pending_timer: threading.Timer | None = None
        self._reverted_by_itself = False

    @property
    def awaiting_confirmation(self) -> bool:
        with self._lock:
            return self._previous is not None

    def layout(self) -> tuple[Display, ...]:
        """Every output with its modes and current setting (read only)."""
        return parse_layout(self._xrandr("--verbose"))

    def apply(self, settings: Sequence[OutputSetting]) -> tuple[Display, ...]:
        """Set every output; on failure the previous configuration is restored before the error
        is raised. The new configuration must be confirmed, or it is reverted by itself."""
        with self._lock:
            if self._previous is not None:
                raise StreamStartError("keep or revert the last change first")
            current = self.layout()
            check(current, settings)
            previous = tuple(display.setting for display in current)
            try:
                self._xrandr(*xrandr_arguments(settings))
                applied = self.layout()
                if not _matches(applied, settings):
                    raise StreamStartError("the screens did not take that configuration")
            except StreamStartError as error:
                try:
                    self._restore(previous)
                except StreamCleanupError as failed:
                    raise StreamStartError(
                        f"{error.message}; the previous one could not be restored: {failed.message}"
                    ) from None
                raise StreamStartError(f"{error.message}; the previous one was restored") from None
            self._previous = previous
            self._reverted_by_itself = False
            self._pending_timer = self._timer(self._confirm_seconds, self._expire)
            self._pending_timer.daemon = True
            self._pending_timer.start()
            return applied

    def confirm(self) -> None:
        """Keep the applied configuration (idempotent)."""
        with self._lock:
            self._cancel_timer()
            self._previous = None

    def revert(self) -> tuple[Display, ...]:
        """Restore the configuration before the last change (nothing to do when none waits)."""
        with self._lock:
            self._cancel_timer()
            previous = self._previous
            if previous is not None:
                self._restore(previous)
                self._previous = None
            return self.layout()

    def close(self) -> None:
        """The application quits: an unconfirmed change is reverted."""
        with self._lock:
            if self._previous is not None:
                self.revert()

    def take_reverted_notice(self) -> bool:
        """Whether a change expired and was reverted by itself since the last call."""
        with self._lock:
            reverted, self._reverted_by_itself = self._reverted_by_itself, False
            return reverted

    def _expire(self) -> None:
        with self._lock:
            if self._previous is None:
                return
            previous = self._previous
            self._pending_timer = None
            try:
                self._restore(previous)
            except StreamCleanupError:
                # Keep the snapshot: an explicit Revert or close() can retry instead of losing
                # the only route back to a usable desktop configuration.
                return
            self._previous = None
            self._reverted_by_itself = True

    def _cancel_timer(self) -> None:
        if self._pending_timer is not None:
            self._pending_timer.cancel()
            self._pending_timer = None

    def _restore(self, settings: Sequence[OutputSetting]) -> None:
        known = {display.name for display in self.layout()}
        kept = [setting for setting in settings if setting.name in known]
        self._xrandr(*xrandr_arguments(kept), cleanup=True)
        if not _matches(self.layout(), kept):
            raise StreamCleanupError("the previous screen configuration was not restored")

    def _xrandr(self, *arguments: str, cleanup: bool = False) -> str:
        error_type = StreamCleanupError if cleanup else StreamStartError
        display = X11Capture(self._environ, self._run).display
        try:
            done = self._run(("xrandr", *arguments), {**self._environ, "DISPLAY": display})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise error_type(f"xrandr failed: {type(error).__name__}") from None
        if done.returncode != 0:
            raise error_type("xrandr refused the configuration")
        return done.stdout


def _matches(displays: Sequence[Display], settings: Sequence[OutputSetting]) -> bool:
    reported = {display.name: display.setting for display in displays}
    for setting in settings:
        now = reported.get(setting.name)
        if now is None or now.enabled != setting.enabled:
            return False
        if setting.enabled and (
            now.mode,
            now.x,
            now.y,
            now.rotation,
            now.primary,
            round(now.scale, 2),
        ) != (
            setting.mode,
            setting.x,
            setting.y,
            setting.rotation,
            setting.primary,
            round(setting.scale, 2),
        ):
            return False
    return True
