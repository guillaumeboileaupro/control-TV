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

Crash restoration (D4-5), with the D2 journal (`restore_journal`):
- the previous default output is journaled atomically *before* the null sink is loaded, and the
  journal is removed only once PulseAudio is seen restored (no null sink of ours, the default
  output is not it); a failed restoration keeps it;
- a process killed while streaming (SIGKILL, crash) leaves the journal; `recover` finishes the
  restoration at the bridge's next start, and `start` does it first too;
- the restoration never overrides the user: the default output is put back only while it is
  still the null sink, and only the sounds still playing into the null sink are moved, to the
  previous output when it still exists (else PulseAudio's own fallback takes them). Repeating it
  changes nothing.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from control_tv.screen_streaming.model import StreamCleanupError, StreamStartError
from control_tv.screen_streaming.restore_journal import RestoreJournal, state_path

TV_SINK = "controltv_tv"
TV_SOURCE = f"{TV_SINK}.monitor"
DEFAULT_MONITOR = "@DEFAULT_MONITOR@"
PACTL_TIMEOUT = 5.0
JOURNAL_NAME = "audio-route.json"

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


def audio_route_journal_path(environ: Mapping[str, str] | None = None) -> Path:
    """Private persistent state used only to finish an interrupted sound restoration."""
    return state_path(JOURNAL_NAME, environ)


def persistent_audio_route() -> PulseAudioRoute:
    """The production route, with crash restoration enabled."""
    return PulseAudioRoute(journal=audio_route_journal_path())


def recover_interrupted_audio_route(
    route: Callable[[], PulseAudioRoute] = persistent_audio_route,
) -> str | None:
    """At start-up: finish the sound restoration of a run that was killed. Returns why it could
    not be finished (the journal is then kept for the next start), or None."""
    try:
        route().recover()
    except (StreamCleanupError, OSError) as error:
        return str(error) or type(error).__name__
    return None


class PulseAudioRoute:
    """Routes the computer's sound to a null sink for the stream, then back."""

    def __init__(
        self,
        run: Runner = _run,
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        *,
        journal: Path | None = None,
    ) -> None:
        self._run, self._which = run, which
        self._environ = dict(os.environ if environ is None else environ)
        self._journal = None if journal is None else RestoreJournal(journal)
        # The output to give back while a route is tracked (streaming or not yet restored).
        self._previous: str | None = None
        self._restored = 0

    @property
    def active(self) -> bool:
        return self._previous is not None

    def start(self) -> str:
        """The source to capture: the null sink's monitor, or the default monitor without pactl."""
        if self._previous is not None:
            raise StreamStartError("the computer's sound is already routed to the TV")
        if self._which("pactl") is None:
            return DEFAULT_MONITOR
        try:
            self.recover()
        except StreamCleanupError as error:
            raise StreamStartError(
                f"the previous sound configuration is not restored yet: {error}"
            ) from None
        journal = self._journal
        if journal is not None and not journal.claim():
            raise StreamStartError("another control-TV stream already routes the computer's sound")
        try:
            previous = self._pactl("get-default-sink").strip()
            if not previous or previous == TV_SINK:
                raise StreamStartError("the computer's default sound output cannot be found")
            self._forget_leftover()
            if journal is not None:
                journal.write({"previous_sink": previous})
        except BaseException:
            if journal is not None:
                journal.release()  # nothing changed yet: nothing to own
            raise
        self._previous, self._restored = previous, 0
        try:
            self._pactl(
                "load-module",
                "module-null-sink",
                f"sink_name={TV_SINK}",
                "sink_properties=device.description=ControlTV-TV",
            )
            self._pactl("set-default-sink", TV_SINK)
            for sink_input in self._sink_inputs(exclude=TV_SINK):
                self._pactl("move-sink-input", sink_input, TV_SINK)
        except BaseException:
            with contextlib.suppress(StreamCleanupError):  # stays tracked for a later stop
                self.stop()
            raise
        return TV_SOURCE

    def recover(self) -> None:
        """Finish the restoration a killed run journaled; nothing to do without a journal or
        when a live stream (this process or another) owns it. A failure keeps the journal for
        the next attempt."""
        journal = self._journal
        if journal is None or self._previous is not None:
            return
        if journal.read() is None or not journal.claim():
            return
        try:
            saved = journal.read()  # again: its owner may have restored it meanwhile
            if saved is None:
                journal.release()
                return
            previous = saved.get("previous_sink")
            if not isinstance(previous, str) or not previous or previous == TV_SINK:
                raise StreamCleanupError("the saved sound restoration is invalid")
            if self._which("pactl") is None:
                raise StreamCleanupError("pactl is needed to restore the saved sound configuration")
            self._previous, self._restored = previous, 0
            self.stop()
        except BaseException:
            self._previous, self._restored = None, 0
            journal.release()
            raise

    def stop(self) -> None:
        """Give the default output back, move the sounds back, remove the null sink, then check
        it and forget the journal."""
        previous = self._previous
        if previous is None:
            return
        steps: tuple[Callable[[], None], ...] = (
            lambda: self._give_default_back(previous),
            lambda: self._move_back(previous),
            self._unload_tv_sinks,
        )
        for step in steps[self._restored :]:
            step()
            self._restored += 1
        if TV_SINK in self._sinks().values() or self._default() == TV_SINK:
            self._restored = 0  # every step again on a retry; each one is idempotent
            raise StreamCleanupError("the computer's sound configuration was not restored")
        if self._journal is not None:
            self._journal.clear()
        self._previous, self._restored = None, 0

    def _give_default_back(self, previous: str) -> None:
        """Only while the null sink is still the default: a choice made since then is kept."""
        if self._default() == TV_SINK and previous in self._sinks().values():
            self._cleanup("set-default-sink", previous)

    def _move_back(self, previous: str) -> None:
        if previous not in self._sinks().values():
            return  # gone (unplugged): removing the null sink lets PulseAudio's fallback take them
        for sink_input in self._sink_inputs(only=TV_SINK, cleanup=True):
            self._cleanup("move-sink-input", sink_input, previous)

    def _unload_tv_sinks(self) -> None:
        for module in self._tv_modules(cleanup=True):
            self._cleanup("unload-module", module)

    def _forget_leftover(self) -> None:
        """Remove a null sink of an interrupted run (its module), if any."""
        for module in self._tv_modules(cleanup=False):
            self._pactl("unload-module", module)

    def _tv_modules(self, *, cleanup: bool) -> list[str]:
        found = []
        for line in self._call(("list", "short", "modules"), cleanup).splitlines():
            fields = line.split("\t")
            ours = len(fields) >= 3 and fields[1] == "module-null-sink"
            if ours and f"sink_name={TV_SINK}" in fields[2].split():
                found.append(fields[0])
        return found

    def _default(self) -> str:
        return self._call(("get-default-sink",), cleanup=True).strip()

    def _sinks(self) -> dict[str, str]:
        return {
            fields[0]: fields[1]
            for fields in (
                line.split("\t")
                for line in self._call(("list", "short", "sinks"), cleanup=True).splitlines()
            )
            if len(fields) >= 2
        }

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
