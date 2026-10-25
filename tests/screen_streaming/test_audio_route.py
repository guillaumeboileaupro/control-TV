"""The computer's sound routed to the TV only while a screen is sent (`audio_route`).

`pactl` is a fake holding sinks, null-sink modules and playing sounds: no real audio server.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence

import pytest

from control_tv.screen_streaming.audio_route import (
    DEFAULT_MONITOR,
    TV_SINK,
    TV_SOURCE,
    PulseAudioRoute,
)
from control_tv.screen_streaming.model import StreamCleanupError, StreamStartError

SPEAKERS = "alsa_output.speakers"


class FakePactl:
    def __init__(self, *, playing: int = 2, leftover: bool = False) -> None:
        self.default = SPEAKERS
        self.sinks = {"1": SPEAKERS}
        self.modules: dict[str, str] = {}
        self.inputs = {str(10 + n): "1" for n in range(playing)}
        self.calls: list[tuple[str, ...]] = []
        self.fail: str | None = None
        self.fail_once = False
        self.raise_error: Exception | None = None
        self._next = 30
        if leftover:
            self._load(f"sink_name={TV_SINK} sink_properties=x")

    def _load(self, args: str) -> str:
        module, sink = str(self._next), str(self._next + 1)
        self._next += 2
        self.modules[module] = args
        self.sinks[sink] = TV_SINK
        return module

    def sink_of(self, name: str) -> str:
        return next(index for index, sink in self.sinks.items() if sink == name)

    def __call__(
        self, argv: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        args = tuple(argv[1:])
        self.calls.append(args)
        if self.raise_error is not None:
            raise self.raise_error
        if self.fail == args[0]:
            if self.fail_once:
                self.fail = None
            return subprocess.CompletedProcess(argv, 1, "", "refused")
        out = ""
        if args[0] == "get-default-sink":
            out = self.default + "\n"
        elif args[0] == "load-module":
            out = self._load(" ".join(args[2:])) + "\n"
        elif args[0] == "unload-module":
            self.modules.pop(args[1])
            self.sinks = {i: s for i, s in self.sinks.items() if s != TV_SINK}
            # PulseAudio moves the sounds of a removed sink to the default one.
            self.inputs = {i: (s if s in self.sinks else "1") for i, s in self.inputs.items()}
        elif args[0] == "set-default-sink":
            self.default = args[1]
        elif args[0] == "move-sink-input":
            self.inputs[args[1]] = self.sink_of(args[2])
        elif args[:2] == ("list", "short"):
            if args[2] == "modules":
                out = "".join(f"{m}\tmodule-null-sink\t{a}\n" for m, a in self.modules.items())
                out += "7\tmodule-udev-detect\t\n"
            elif args[2] == "sinks":
                out = "".join(f"{i}\t{s}\tmodule\ts16le\tRUNNING\n" for i, s in self.sinks.items())
            else:
                out = "bad line\n" + "".join(
                    f"{i}\t{s}\t3\tfloat32le\n" for i, s in self.inputs.items()
                )
        return subprocess.CompletedProcess(argv, 0, out, "")


def route(pactl: FakePactl, *, has_pactl: bool = True) -> PulseAudioRoute:
    return PulseAudioRoute(pactl, {}, lambda name: f"/usr/bin/{name}" if has_pactl else None)


def test_the_sound_goes_to_a_null_sink_for_the_stream_and_back_after() -> None:
    pactl = FakePactl()
    audio = route(pactl)

    assert audio.start() == TV_SOURCE
    tv = pactl.sink_of(TV_SINK)
    assert pactl.default == TV_SINK and set(pactl.inputs.values()) == {tv}
    assert audio.active

    audio.stop()
    assert pactl.default == SPEAKERS and set(pactl.inputs.values()) == {"1"}
    assert not pactl.modules and TV_SINK not in pactl.sinks.values()
    assert not audio.active
    audio.stop()  # idempotent


def test_without_pactl_the_default_monitor_is_captured_and_nothing_changes() -> None:
    pactl = FakePactl()
    audio = route(pactl, has_pactl=False)

    assert audio.start() == DEFAULT_MONITOR
    assert pactl.calls == [] and not audio.active


def test_a_null_sink_left_by_an_interrupted_run_is_removed_first() -> None:
    pactl = FakePactl(leftover=True)
    audio = route(pactl)

    audio.start()

    assert len(pactl.modules) == 1 and list(pactl.sinks.values()).count(TV_SINK) == 1


@pytest.mark.parametrize("default", ["", TV_SINK])
def test_an_unknown_default_output_refuses_the_route(default: str) -> None:
    pactl = FakePactl()
    pactl.default = default

    with pytest.raises(StreamStartError, match="default sound output"):
        route(pactl).start()
    assert not pactl.modules


def test_a_failed_switch_puts_everything_back_and_reports_the_start_error() -> None:
    pactl = FakePactl()
    pactl.fail = "move-sink-input"
    audio = route(pactl)

    with pytest.raises(StreamStartError, match="move-sink-input"):
        audio.start()

    assert pactl.default == SPEAKERS and not pactl.modules and not audio.active


def test_a_failed_cleanup_during_a_failed_start_keeps_the_route_for_a_later_stop() -> None:
    pactl = FakePactl()
    audio = route(pactl)
    pactl.fail = "set-default-sink"

    with pytest.raises(StreamStartError):
        audio.start()

    assert audio.active
    pactl.fail = None
    audio.stop()
    assert pactl.default == SPEAKERS and not pactl.modules


def test_a_route_already_active_refuses_a_second_start() -> None:
    audio = route(FakePactl())
    audio.start()

    with pytest.raises(StreamStartError, match="already"):
        audio.start()


def test_a_stop_that_fails_late_resumes_after_the_steps_already_done() -> None:
    pactl = FakePactl()
    audio = route(pactl)
    audio.start()
    pactl.fail, pactl.fail_once = "unload-module", True

    with pytest.raises(StreamCleanupError, match="unload-module"):
        audio.stop()
    assert audio.active and pactl.default == SPEAKERS

    before = len(pactl.calls)
    audio.stop()
    assert pactl.calls[before:] == [("unload-module", next(iter(()), "30"))]
    assert not audio.active and not pactl.modules


def test_pactl_that_cannot_run_is_a_start_or_cleanup_error() -> None:
    pactl = FakePactl()
    audio = route(pactl)
    pactl.raise_error = subprocess.TimeoutExpired("pactl", 5.0)
    with pytest.raises(StreamStartError, match="TimeoutExpired"):
        audio.start()

    pactl.raise_error = None
    audio.start()
    pactl.raise_error = OSError("gone")
    with pytest.raises(StreamCleanupError, match="OSError"):
        audio.stop()
