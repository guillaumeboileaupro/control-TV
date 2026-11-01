"""The computer's sound routed to the TV only while a screen is sent (`audio_route`).

`pactl` is a fake holding sinks, null-sink modules and playing sounds: no real audio server.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from control_tv.screen_streaming.audio_route import (
    DEFAULT_MONITOR,
    TV_SINK,
    TV_SOURCE,
    PulseAudioRoute,
    audio_route_journal_path,
    recover_interrupted_audio_route,
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
        # A second command refused on every call, independently of `fail`.
        self.also_fail: str | None = None
        # `unload-module` reports success but the null sink stays (a server that did not apply it).
        self.sticky = False
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
        if self.also_fail == args[0]:
            return subprocess.CompletedProcess(argv, 1, "", "refused")
        if self.fail == args[0]:
            if self.fail_once:
                self.fail = None
            return subprocess.CompletedProcess(argv, 1, "", "refused")
        out = ""
        if args[0] == "get-default-sink":
            out = self.default + "\n"
        elif args[0] == "load-module":
            out = self._load(" ".join(args[2:])) + "\n"
        elif args[0] == "unload-module" and self.sticky:
            pass
        elif args[0] == "unload-module":
            self.modules.pop(args[1])
            self.sinks = {i: s for i, s in self.sinks.items() if s != TV_SINK}
            # PulseAudio moves the sounds of a removed sink to the default one.
            self.inputs = {i: (s if s in self.sinks else "1") for i, s in self.inputs.items()}
            # PulseAudio picks another default output when the default one disappears.
            if self.default not in self.sinks.values():
                self.default = next(iter(self.sinks.values()), "")
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

    def dump(self) -> str:
        state = (self.default, self.sinks, self.modules, self.inputs, self._next)
        return json.dumps(state)

    @classmethod
    def load(cls, text: str) -> FakePactl:
        fake = cls(playing=0)
        fake.default, fake.sinks, fake.modules, fake.inputs, fake._next = json.loads(text)
        return fake


class PersistingPactl:
    """The fake PulseAudio server's state outlives the process using it, like the real one."""

    def __init__(self, fake: FakePactl, state: Path) -> None:
        self.fake, self.state = fake, state

    def __call__(
        self, argv: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        done = self.fake(argv, environ)
        self.state.write_text(self.fake.dump(), encoding="utf-8")
        return done


def route(
    pactl: FakePactl, *, has_pactl: bool = True, journal: Path | None = None
) -> PulseAudioRoute:
    return PulseAudioRoute(
        pactl, {}, lambda name: f"/usr/bin/{name}" if has_pactl else None, journal=journal
    )


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
    pactl.fail, pactl.also_fail = "move-sink-input", "unload-module"

    with pytest.raises(StreamStartError, match="move-sink-input"):
        audio.start()

    assert audio.active and pactl.modules
    pactl.fail = pactl.also_fail = None
    audio.stop()
    assert pactl.default == SPEAKERS and not pactl.modules and not audio.active


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
    retried = [call[0] for call in pactl.calls[before:]]
    assert retried.count("unload-module") == 1
    assert "set-default-sink" not in retried and "move-sink-input" not in retried
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


# D4-5: the sound is given back after a crash, from a private journal.


def mutations(pactl: FakePactl) -> list[str]:
    changing = ("load-module", "unload-module", "set-default-sink", "move-sink-input")
    return [call[0] for call in pactl.calls if call[0] in changing]


def test_the_previous_output_is_journaled_privately_before_any_change(tmp_path: Path) -> None:
    pactl = FakePactl()
    journal = tmp_path / "state" / "control-tv" / "audio-route.json"
    audio = route(pactl, journal=journal)

    audio.start()

    saved = json.loads(journal.read_text(encoding="utf-8"))
    assert saved == {"version": 1, "previous_sink": SPEAKERS}
    assert journal.stat().st_mode & 0o777 == 0o600
    assert journal.parent.stat().st_mode & 0o777 == 0o700
    audio.stop()
    assert not journal.exists() and pactl.default == SPEAKERS


def test_no_sound_setting_changes_when_the_journal_cannot_be_written(tmp_path: Path) -> None:
    pactl = FakePactl()
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block", encoding="utf-8")

    with pytest.raises(FileExistsError):
        route(pactl, journal=blocker / "audio-route.json").start()

    assert mutations(pactl) == [] and pactl.default == SPEAKERS


def test_a_killed_run_is_restored_by_the_next_start_up(tmp_path: Path) -> None:
    pactl = FakePactl()
    journal = tmp_path / "audio-route.json"
    route(pactl, journal=journal).start()  # then the process is gone: nothing else runs
    assert pactl.default == TV_SINK and journal.exists()

    route(pactl, journal=journal).recover()

    assert pactl.default == SPEAKERS and set(pactl.inputs.values()) == {"1"}
    assert not pactl.modules and TV_SINK not in pactl.sinks.values()
    assert not journal.exists()


def test_a_stream_started_after_a_crash_restores_first_instead_of_refusing(
    tmp_path: Path,
) -> None:
    # Before D4-5 the null sink was still the default, so the next stream was refused.
    pactl = FakePactl()
    journal = tmp_path / "audio-route.json"
    route(pactl, journal=journal).start()

    audio = route(pactl, journal=journal)
    assert audio.start() == TV_SOURCE
    assert list(pactl.sinks.values()).count(TV_SINK) == 1 and len(pactl.modules) == 1
    audio.stop()
    assert pactl.default == SPEAKERS and not journal.exists()


def test_the_restoration_keeps_a_choice_the_user_made_meanwhile(tmp_path: Path) -> None:
    pactl = FakePactl()
    pactl.sinks["3"] = "bluez_output.headset"
    journal = tmp_path / "audio-route.json"
    route(pactl, journal=journal).start()
    pactl.default = "bluez_output.headset"  # chosen in the sound settings while streaming
    pactl.inputs["12"] = "3"  # a sound the user moved there itself

    route(pactl, journal=journal).recover()

    assert pactl.default == "bluez_output.headset"
    assert pactl.inputs["12"] == "3"  # not taken back to the speakers
    assert {pactl.inputs["10"], pactl.inputs["11"]} == {"1"}
    assert not pactl.modules and not journal.exists()


def test_an_output_unplugged_meanwhile_is_left_to_the_sound_server(tmp_path: Path) -> None:
    pactl = FakePactl()
    journal = tmp_path / "audio-route.json"
    route(pactl, journal=journal).start()
    del pactl.sinks["1"]  # the speakers' device went away
    pactl.sinks["4"] = "alsa_output.hdmi"

    route(pactl, journal=journal).recover()

    assert "set-default-sink" not in [c[0] for c in pactl.calls[-6:]]
    assert pactl.default == "alsa_output.hdmi" and not pactl.modules
    assert not journal.exists()


def test_recovery_is_repeatable_and_changes_nothing_once_done(tmp_path: Path) -> None:
    pactl = FakePactl()
    journal = tmp_path / "audio-route.json"
    route(pactl, journal=journal).start()
    route(pactl, journal=journal).recover()
    done = mutations(pactl)

    for _ in range(2):
        route(pactl, journal=journal).recover()
        assert recover_interrupted_audio_route(lambda: route(pactl, journal=journal)) is None

    assert mutations(pactl) == done and pactl.default == SPEAKERS


def test_a_failed_recovery_keeps_the_journal_and_blocks_a_new_route(tmp_path: Path) -> None:
    pactl = FakePactl()
    journal = tmp_path / "audio-route.json"
    route(pactl, journal=journal).start()
    pactl.fail = "unload-module"

    problem = recover_interrupted_audio_route(lambda: route(pactl, journal=journal))
    assert problem is not None and "unload-module" in problem
    assert journal.exists()
    with pytest.raises(StreamStartError, match="not restored yet"):
        route(pactl, journal=journal).start()
    assert mutations(pactl).count("load-module") == 1  # no second null sink

    pactl.fail = None
    assert recover_interrupted_audio_route(lambda: route(pactl, journal=journal)) is None
    assert not journal.exists() and pactl.default == SPEAKERS and not pactl.modules


def test_a_restoration_not_confirmed_by_pulseaudio_keeps_the_journal(tmp_path: Path) -> None:
    pactl = FakePactl()
    journal = tmp_path / "audio-route.json"
    audio = route(pactl, journal=journal)
    audio.start()
    pactl.fail = "set-default-sink"  # the null sink stays the default: not restored

    with pytest.raises(StreamCleanupError):
        audio.stop()
    assert journal.exists() and audio.active

    pactl.fail = None
    audio.stop()
    assert not journal.exists() and pactl.default == SPEAKERS


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"version":2,"previous_sink":"x"}',
        '{"version":1}',
        f'{{"version":1,"previous_sink":"{TV_SINK}"}}',
    ],
)
def test_an_invalid_journal_is_kept_and_changes_nothing(tmp_path: Path, content: str) -> None:
    pactl = FakePactl(leftover=True)
    journal = tmp_path / "audio-route.json"
    journal.write_text(content, encoding="utf-8")

    with pytest.raises(StreamCleanupError, match="invalid"):
        route(pactl, journal=journal).recover()

    assert mutations(pactl) == [] and journal.read_text(encoding="utf-8") == content


def test_without_pactl_a_saved_restoration_is_an_error_not_a_silent_loss(tmp_path: Path) -> None:
    journal = tmp_path / "audio-route.json"
    journal.write_text(f'{{"version":1,"previous_sink":"{SPEAKERS}"}}', encoding="utf-8")

    with pytest.raises(StreamCleanupError, match="pactl is needed"):
        route(FakePactl(), has_pactl=False, journal=journal).recover()
    assert journal.exists()


def test_the_journal_lives_in_the_private_state_directory() -> None:
    assert audio_route_journal_path({"XDG_STATE_HOME": "/run/state"}) == Path(
        "/run/state/control-tv/audio-route.json"
    )
    assert audio_route_journal_path({}).parts[-4:] == (
        ".local",
        "state",
        "control-tv",
        "audio-route.json",
    )


_KILLED_CHILD = """
import runpy, sys, time
from pathlib import Path
from control_tv.screen_streaming.audio_route import PulseAudioRoute
tests = runpy.run_path(sys.argv[1])
state, journal = Path(sys.argv[2]), Path(sys.argv[3])
pactl = tests["PersistingPactl"](tests["FakePactl"](), state)
route = PulseAudioRoute(pactl, {}, lambda name: "/usr/bin/pactl", journal=journal)
route.start()
print("streaming", flush=True)
time.sleep(60)
"""


def test_the_sound_comes_back_after_a_real_sigkill(tmp_path: Path) -> None:
    state, journal = tmp_path / "pulseaudio.json", tmp_path / "audio-route.json"
    child = subprocess.Popen(
        [sys.executable, "-c", _KILLED_CHILD, __file__, str(state), str(journal)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "streaming"
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)
    pactl = FakePactl.load(state.read_text(encoding="utf-8"))
    assert pactl.default == TV_SINK and journal.exists()  # silent computer before D4-5

    assert recover_interrupted_audio_route(lambda: route(pactl, journal=journal)) is None

    assert pactl.default == SPEAKERS and not pactl.modules and not journal.exists()


def test_steps_that_succeed_without_effect_are_not_taken_for_a_restoration(
    tmp_path: Path,
) -> None:
    pactl = FakePactl()
    journal = tmp_path / "audio-route.json"
    audio = route(pactl, journal=journal)
    audio.start()
    pactl.sticky = True

    with pytest.raises(StreamCleanupError, match="was not restored"):
        audio.stop()
    assert journal.exists() and audio.active

    pactl.sticky = False
    audio.stop()  # every step again: the null sink is removed this time
    assert not journal.exists() and not pactl.modules and not audio.active


def test_the_production_route_journals_in_the_state_directory() -> None:
    from control_tv.screen_streaming.audio_route import persistent_audio_route

    audio = persistent_audio_route()
    assert not audio.active
    assert "control-tv-test-state-" in str(audio_route_journal_path())
