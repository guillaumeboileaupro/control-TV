"""The computer's sound plays on the TV only while a screen is sent with it.

Capturing the default output's monitor sends the sound to the TV but leaves it on the
computer's speakers too, and muting the speakers also silences that monitor (checked on the
owner's PulseAudio). So for the stream, a null sink becomes the default output: what plays goes
there instead of the speakers, and its monitor is what the TV receives. Stopping restores the
previous default output, moves the sounds back to it and removes the null sink.

Rules:
- `start` returns the PulseAudio source to capture; without `pactl` it returns the default
  output's monitor (the sound then plays on both, as before) rather than failing the stream.
- `stop` is idempotent; a failed step keeps the route for a retry, which resumes after the steps
  already done. A null sink left by an interrupted run is removed before a new one is made.
- Only `pactl` is run, with fixed arguments: nothing else on the system is changed.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence

from control_tv.screen_streaming.model import StreamCleanupError, StreamStartError

TV_SINK = "controltv_tv"
TV_SOURCE = f"{TV_SINK}.monitor"
DEFAULT_MONITOR = "@DEFAULT_MONITOR@"
PACTL_TIMEOUT = 5.0

Runner = Callable[[Sequence[str], Mapping[str, str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str], environ: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        env=dict(environ),
        capture_output=True,
        text=True,
        timeout=PACTL_TIMEOUT,
        check=False,
    )


class PulseAudioRoute:
    """Routes the computer's sound to a null sink for the stream, then back."""

    def __init__(
        self,
        run: Runner = _run,
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._run, self._which = run, which
        self._environ = dict(os.environ if environ is None else environ)
        self._module: str | None = None
        self._previous: str | None = None
        self._restored = 0

    @property
    def active(self) -> bool:
        return self._module is not None

    def start(self) -> str:
        """The source to capture: the null sink's monitor, or the default monitor without pactl."""
        if self._module is not None:
            raise StreamStartError("the computer's sound is already routed to the TV")
        if self._which("pactl") is None:
            return DEFAULT_MONITOR
        previous = self._pactl("get-default-sink").strip()
        if not previous or previous == TV_SINK:
            raise StreamStartError("the computer's default sound output cannot be found")
        self._forget_leftover()
        module = self._pactl(
            "load-module",
            "module-null-sink",
            f"sink_name={TV_SINK}",
            "sink_properties=device.description=ControlTV-TV",
        ).strip()
        self._module, self._previous, self._restored = module, previous, 0
        try:
            self._pactl("set-default-sink", TV_SINK)
            for sink_input in self._sink_inputs(exclude=TV_SINK):
                self._pactl("move-sink-input", sink_input, TV_SINK)
        except BaseException:
            with contextlib.suppress(StreamCleanupError):  # stays tracked for a later stop
                self.stop()
            raise
        return TV_SOURCE

    def stop(self) -> None:
        """Restore the previous output, move the sounds back, remove the null sink."""
        module, previous = self._module, self._previous
        if module is None or previous is None:
            return
        steps: tuple[Callable[[], None], ...] = (
            lambda: self._cleanup("set-default-sink", previous),
            lambda: self._move_back(previous),
            lambda: self._cleanup("unload-module", module),
        )
        for step in steps[self._restored :]:
            step()
            self._restored += 1
        self._module, self._previous, self._restored = None, None, 0

    def _move_back(self, previous: str) -> None:
        for sink_input in self._sink_inputs(only=TV_SINK, cleanup=True):
            self._cleanup("move-sink-input", sink_input, previous)

    def _forget_leftover(self) -> None:
        """Remove a null sink of an interrupted run (its module), if any."""
        for line in self._pactl("list", "short", "modules").splitlines():
            fields = line.split("\t")
            leftover = len(fields) >= 3 and fields[1] == "module-null-sink"
            if leftover and f"sink_name={TV_SINK}" in fields[2].split():
                self._pactl("unload-module", fields[0])

    def _sink_inputs(
        self, *, exclude: str | None = None, only: str | None = None, cleanup: bool = False
    ) -> list[str]:
        sinks = {
            fields[0]: fields[1]
            for fields in (
                line.split("\t")
                for line in self._call(("list", "short", "sinks"), cleanup).splitlines()
            )
            if len(fields) >= 2
        }
        found = []
        for line in self._call(("list", "short", "sink-inputs"), cleanup).splitlines():
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            sink = sinks.get(fields[1], fields[1])
            if (only is None or sink == only) and (exclude is None or sink != exclude):
                found.append(fields[0])
        return found

    def _pactl(self, *arguments: str) -> str:
        return self._call(arguments, cleanup=False)

    def _cleanup(self, *arguments: str) -> None:
        self._call(arguments, cleanup=True)

    def _call(self, arguments: Sequence[str], cleanup: bool) -> str:
        error_type = StreamCleanupError if cleanup else StreamStartError
        try:
            done = self._run(("pactl", *arguments), self._environ)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise error_type(f"pactl {arguments[0]} failed: {type(error).__name__}") from None
        if done.returncode != 0:
            raise error_type(f"pactl {arguments[0]} failed")
        return done.stdout
