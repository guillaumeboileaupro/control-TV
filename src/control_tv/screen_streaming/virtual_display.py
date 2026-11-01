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

Crash restoration: what an extension changes is journaled before the change (`restore_journal`)
and the journal is deleted once the desktop is verified restored. A run killed in between (SIGKILL,
crash, power cut of the bridge) is restored by the next bridge start (`recover_interrupted_
extension`), or before the next extension, whichever comes first. That recovery undoes only what
is still the killed run's: a screen the owner placed elsewhere since stays where it is, an output
that no longer shows the TV mode is not turned off, a screen unplugged since is not waited for. A
recovery that cannot finish keeps the journal for the next attempt.
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
from pathlib import Path

from control_tv.json_types import JsonObject
from control_tv.screen_streaming.model import (
    InvalidStreamConfiguration,
    StreamCleanupError,
    StreamStartError,
)
from control_tv.screen_streaming.restore_journal import RestoreJournal, state_path
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
_JOURNAL_NAME = "extended-display.json"


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
    # The desktop's screens moved to make room on the left or above, at their former places.
    moved: tuple[tuple[str, int, int], ...] = ()
    # How far they were moved; None for a journal written before it was recorded.
    shift: tuple[int, int] | None = (0, 0)


def extension_journal_path(environ: Mapping[str, str] | None = None) -> Path:
    """Private persistent state used only to finish an interrupted D2 restoration."""
    return state_path(_JOURNAL_NAME, environ)


def persistent_virtual_monitor() -> XrandrVirtualMonitor:
    """The production D2 adapter, with crash restoration enabled."""
    return XrandrVirtualMonitor(journal=extension_journal_path())


def recover_interrupted_extension(
    monitor: Callable[[], XrandrVirtualMonitor] = persistent_virtual_monitor,
) -> str | None:
    """At start-up: finish the restoration of an extension whose run was killed. Returns why it
    could not be finished (the journal is then kept for the next start), or None."""
    try:
        monitor()
    except (StreamCleanupError, OSError) as error:
        return str(error) or type(error).__name__
    return None


class XrandrVirtualMonitor:
    """One extra screen at a time on a free video output, enabled and removed with `xrandr`."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        run: Runner = run_command,
        sleep: Callable[[float], None] = time.sleep,
        *,
        journal: Path | None = None,
    ) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._run = run
        self._sleep = sleep
        self._extension: _Extension | None = None
        # Removal steps of the extension already done: a retry resumes after them, since xrandr
        # refuses to delete a mode that is already gone.
        self._removed = 0
        self._lock = threading.Lock()
        self._journal = None if journal is None else RestoreJournal(journal)
        self._recover()

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
        x: int | None = None,
        y: int | None = None,
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
        if (x is None) != (y is None) or any(
            isinstance(value, bool) or not isinstance(value, int) or not -32768 <= value <= 32767
            for value in (x, y)
            if value is not None
        ):
            raise InvalidStreamConfiguration("the extended screen coordinates are invalid")
        mode = f"{MODE_PREFIX}{width}x{height}-{refresh}"
        with self._lock:
            if self._extension is not None:
                raise StreamStartError("the display is already extended: stop it first")
            journal = self._journal
            # Owned before any xrandr command: even the cleanup of a refused extension would
            # otherwise undo the screen of a stream running in another process.
            if journal is not None and not journal.claim():
                raise StreamStartError("another control-TV stream already extends the display")
            try:
                output = self._extend(mode, width, height, refresh, position, x, y)
            except BaseException:
                if journal is not None:
                    journal.release()  # untracked: a kept journal is the next recovery's
                raise
        return output

    def _extend(
        self,
        mode: str,
        width: int,
        height: int,
        refresh: int,
        position: Position,
        x: int | None,
        y: int | None,
    ) -> X11Output:
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
            if x is not None and y is not None:
                placement, extension = self._place(extension, x, y)
            else:
                placement = (f"--{position.value}", primary)
            self._write_journal(extension)
            self._xrandr("--newmode", mode, *self._modeline(width, height, refresh))
            self._xrandr("--addmode", free, mode)
            self._xrandr("--output", free, "--mode", mode, *placement)
            output = self._accepted(free, width, height)
        except BaseException:
            self._forget(extension)
            self._clear_journal_if_restored(extension)
            raise
        self._extension = extension
        self._removed = 0
        return output

    def remove(self) -> None:
        """Turn the extra screen off, delete its mode and put the desktop back. Idempotent; a
        failure keeps it for a retry, which resumes after the steps already done."""
        with self._lock:
            extension = self._extension
            if extension is None:
                return
            steps: tuple[Callable[[], object], ...] = (
                lambda: self._xrandr("--output", extension.output, "--off", cleanup=True),
                lambda: self._xrandr("--delmode", extension.output, extension.mode, cleanup=True),
                lambda: self._xrandr("--rmmode", extension.mode, cleanup=True),
                lambda: self._put_back(extension.moved, cleanup=True),
            )
            for step in steps[self._removed :]:
                step()
                self._removed += 1
            self._clear_journal_if_restored(extension)
            self._extension = None

    def _recover(self) -> None:
        """Finish a restoration left by a killed process before accepting another extension; one
        owned by a live stream (this process or another) is left to it."""
        journal = self._journal
        if journal is None or _read_saved(journal) is None or not journal.claim():
            return
        try:
            extension = _read_saved(journal)  # again: its owner may have restored it meanwhile
            if extension is None:
                journal.release()
                return
            self._undo_killed_run(extension)
            self._clear_journal_if_restored(extension)
        except BaseException:
            journal.release()  # kept for the next attempt, here or at the next start
            raise

    def _undo_killed_run(self, extension: _Extension) -> None:
        """Best-effort undo of a killed run's extension, keeping what the owner changed since."""
        listing = self._xrandr("--query", cleanup=True)
        with contextlib.suppress(StreamCleanupError):
            self._put_back(self._still_moved(extension), cleanup=True)
        steps: list[tuple[str, ...]] = []
        if _shows_mode(listing, extension.output, extension.mode):
            steps.append(("--output", extension.output, "--off"))
        if extension.mode in listing:
            steps += [("--delmode", extension.output, extension.mode), ("--rmmode", extension.mode)]
        for arguments in steps:
            with contextlib.suppress(StreamCleanupError):
                self._xrandr(*arguments, cleanup=True)

    def _still_moved(self, extension: _Extension) -> tuple[tuple[str, int, int], ...]:
        """The moved screens still where the extension put them (all, for an older journal)."""
        if extension.shift is None or not extension.moved:
            return extension.moved
        places = self._places()
        shift_x, shift_y = extension.shift
        return tuple(
            (name, x, y)
            for name, x, y in extension.moved
            if places.get(name) == (x + shift_x, y + shift_y)
        )

    def _places(self) -> dict[str, tuple[int, int]]:
        return {
            item.name: (item.x, item.y)
            for item in parse_monitors(self._xrandr("--listmonitors", cleanup=True))
        }

    def _write_journal(self, extension: _Extension) -> None:
        if self._journal is None:
            return
        self._journal.write(
            {
                "output": extension.output,
                "mode": extension.mode,
                "moved": [list(item) for item in extension.moved],
                "shift": None if extension.shift is None else list(extension.shift),
            }
        )

    def _clear_journal_if_restored(self, extension: _Extension) -> None:
        if self._journal is None:
            return
        listing = self._xrandr("--query", cleanup=True)
        places = self._places()
        if extension.shift is None:
            moved_back = all(places.get(name) in (None, (x, y)) for name, x, y in extension.moved)
        else:
            # Back, placed elsewhere by the owner, or unplugged: only the TV's shift is undone.
            shift_x, shift_y = extension.shift
            moved_back = all(
                places.get(name) != (x + shift_x, y + shift_y) for name, x, y in extension.moved
            )
        if extension.mode in listing or not moved_back:
            raise StreamCleanupError("the saved extended-screen configuration was not restored")
        self._journal.clear()

    def _place(self, extension: _Extension, x: int, y: int) -> tuple[tuple[str, ...], _Extension]:
        """The arguments placing the TV at desktop coordinates `x`, `y`. X has no negative
        coordinates: a TV on the left of or above the desktop moves the desktop's screens by as
        much, in the same command, and they go back when the TV is removed."""
        shift_x, shift_y = max(0, -x), max(0, -y)
        arguments: list[str] = ["--pos", f"{x + shift_x}x{y + shift_y}"]
        if shift_x == 0 and shift_y == 0:
            return tuple(arguments), extension
        moved = tuple(
            (monitor.name, monitor.x, monitor.y)
            for monitor in parse_monitors(self._xrandr("--listmonitors"))
            if monitor.name != extension.output
        )
        for name, old_x, old_y in moved:
            arguments += ["--output", name, "--pos", f"{old_x + shift_x}x{old_y + shift_y}"]
        return tuple(arguments), _Extension(
            extension.output, extension.mode, moved, (shift_x, shift_y)
        )

    def _put_back(self, moved: tuple[tuple[str, int, int], ...], *, cleanup: bool) -> None:
        if moved:
            arguments: list[str] = []
            for name, x, y in moved:
                arguments += ["--output", name, "--pos", f"{x}x{y}"]
            self._xrandr(*arguments, cleanup=cleanup)

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
        with contextlib.suppress(StreamCleanupError):
            self._put_back(extension.moved, cleanup=True)
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
        try:
            display = X11Capture(self._environ, self._run).display
        except InvalidStreamConfiguration as error:
            if not cleanup:
                raise
            # A start-up recovery outside an X11 session: kept for a later one, never a crash.
            raise StreamCleanupError(error.message) from None
        try:
            done = self._run(("xrandr", *arguments), {**self._environ, "DISPLAY": display})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise error_type(f"xrandr {arguments[0]} failed: {type(error).__name__}") from None
        if done.returncode != 0:
            raise error_type(f"xrandr {arguments[0]} failed")
        return done.stdout


def _saved_extension(value: JsonObject) -> _Extension:
    """The extension a journal describes; ValueError for any other shape."""
    output, mode, moved = value.get("output"), value.get("mode"), value.get("moved")
    if not isinstance(output, str) or not isinstance(mode, str) or not isinstance(moved, list):
        raise ValueError("unexpected extended-screen journal")
    screens: list[tuple[str, int, int]] = []
    for item in moved:
        if not isinstance(item, list) or len(item) != 3:
            raise ValueError("unexpected moved screen")
        name, x, y = item
        if not isinstance(name, str) or type(x) is not int or type(y) is not int:
            raise ValueError("unexpected moved screen")
        screens.append((name, x, y))
    shift = value.get("shift")
    if shift is None:  # a journal written before the shift was recorded
        return _Extension(output, mode, tuple(screens), None)
    if not isinstance(shift, list) or len(shift) != 2:
        raise ValueError("unexpected screen shift")
    shift_x, shift_y = shift
    if type(shift_x) is not int or type(shift_y) is not int:
        raise ValueError("unexpected screen shift")
    return _Extension(output, mode, tuple(screens), (shift_x, shift_y))


def _read_saved(journal: RestoreJournal) -> _Extension | None:
    try:
        value = journal.read()
        return None if value is None else _saved_extension(value)
    except (StreamCleanupError, ValueError) as error:
        raise StreamCleanupError("the saved extended-screen restoration is invalid") from error


def _shows_mode(listing: str, output: str, mode: str) -> bool:
    """Whether `xrandr --query` shows `output` currently displaying `mode` (marked `*`)."""
    inside = False
    for line in listing.splitlines():
        if not line[:1].isspace():
            inside = line.startswith(f"{output} ")
        elif inside and line.split()[:1] == [mode] and "*" in line:
            return True
    return False


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
