"""D1 screen capture and D2 virtual monitor with a fake `xrandr`: no display is read or changed."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from control_tv.screen_streaming import (
    CapturedVideo,
    CaptureHandle,
    DisplaySelection,
    EncodingProfile,
    InvalidStreamConfiguration,
    StreamCleanupError,
    StreamStartError,
    d1,
)
from control_tv.screen_streaming.h264_encoder import LiveEncoderSettings
from control_tv.screen_streaming.model import SyntheticVideoSource
from control_tv.screen_streaming.virtual_display import Position, XrandrVirtualMonitor
from control_tv.screen_streaming.x11_capture import X11Capture, X11Output, parse_monitors
from control_tv.screencast.ffmpeg import encoder_arguments, live_hls_command
from control_tv.screencast.model import (
    HlsSegmenting,
    InvalidScreenCastOption,
    SoftwareX264,
    SyntheticSource,
    VaapiCqp,
    VaapiDevice,
    X11GrabSource,
)
from live_encoder_fakes import FakeRunner, encoder
from x11_fakes import LISTING, X11, Done, FakeXrandr

# FFmpeg arguments for a captured region --------------------------------------------------


def test_a_captured_region_is_grabbed_in_real_time_and_scaled_to_the_requested_size(
    tmp_path: Path,
) -> None:
    region = X11GrabSource(":0", 1920, 0, 2560, 1440)
    profile = EncodingProfile(1280, 720, 30)
    argv = live_hls_command(
        "ffmpeg", region, profile, SoftwareX264(), tmp_path, HlsSegmenting(), 60
    )
    grab = argv.index("x11grab")
    assert argv[grab - 1] == "-f" and "-re" not in argv
    assert argv[argv.index("-video_size") + 1] == "2560x1440"
    assert argv[argv.index("-i") + 1] == ":0+1920,0"
    assert argv[argv.index("-framerate") + 1] == "30"
    assert argv[argv.index("-vf") + 1] == "scale=1280:720"
    synthetic = live_hls_command(
        "ffmpeg", SyntheticSource(), profile, SoftwareX264(), tmp_path, HlsSegmenting(), 60
    )
    assert "-re" in synthetic and "-vf" not in synthetic


def test_va_api_scales_before_uploading() -> None:
    plan = VaapiCqp(VaapiDevice("/dev/dri/renderD128"), 24)
    assert encoder_arguments(plan, (1280, 720))[1] == "scale=1280:720,format=nv12,hwupload"
    assert encoder_arguments(plan)[1] == "format=nv12,hwupload"


@pytest.mark.parametrize(
    "build",
    [
        lambda: X11GrabSource("remote:0", 0, 0, 1920, 1080),
        lambda: X11GrabSource(":0", -1, 0, 1920, 1080),
        lambda: X11GrabSource(":0", 0, 0, 1921, 1080),
        lambda: X11GrabSource(":0", 0, 0, 8, 1080),
        lambda: X11GrabSource(":0", True, 0, 1920, 1080),
    ],
    ids=["remote", "negative", "odd", "small", "bool"],
)
def test_only_a_valid_local_region_can_be_captured(build: object) -> None:
    with pytest.raises(InvalidScreenCastOption):
        build()  # type: ignore[operator]


# X11 outputs and the capture -------------------------------------------------------------


def test_the_monitors_are_parsed_primary_first() -> None:
    assert parse_monitors(LISTING) == (
        X11Output("eDP-1", 1920, 1080, 0, 0, True),
        X11Output("HDMI-1", 2561, 1441, 1920, 0, False),
    )
    capture = X11Capture(X11, FakeXrandr())
    assert [output.name for output in capture.outputs()] == ["eDP-1", "HDMI-1"]


def test_a_capture_resolves_the_chosen_output_with_an_even_size() -> None:
    capture = X11Capture(X11, FakeXrandr())
    handle = capture.start(DisplaySelection("HDMI-1"))

    assert capture.region(handle) == X11GrabSource(":0", 1920, 0, 2560, 1440)
    capture.stop(handle)
    capture.stop(handle)  # idempotent
    with pytest.raises(StreamStartError, match="not active"):
        capture.region(handle)
    with pytest.raises(StreamCleanupError, match="never started"):
        capture.stop(CaptureHandle("x11-never"))


def test_an_absent_output_or_an_unreadable_listing_starts_nothing() -> None:
    with pytest.raises(StreamStartError, match="not connected"):
        X11Capture(X11, FakeXrandr()).start(DisplaySelection("DP-9"))
    with pytest.raises(StreamStartError, match="xrandr failed"):
        X11Capture(X11, FakeXrandr(fail="--listmonitors")).outputs()

    def missing(
        argv: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("xrandr")

    with pytest.raises(StreamStartError, match="FileNotFoundError"):
        X11Capture(X11, missing).outputs()


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}, "Wayland"),
        ({"XDG_SESSION_TYPE": "x11"}, "local X11 display"),
        ({"DISPLAY": "host:0", "XDG_SESSION_TYPE": "x11"}, "local X11 display"),
    ],
)
def test_only_a_local_x11_session_can_be_captured(environ: dict[str, str], message: str) -> None:
    xrandr = FakeXrandr()
    with pytest.raises(InvalidStreamConfiguration, match=message):
        X11Capture(environ, xrandr).outputs()
    assert xrandr.calls == []


def test_an_output_too_small_to_encode_is_refused() -> None:
    def tiny(argv: Sequence[str], environ: Mapping[str, str]) -> Done:
        return subprocess.CompletedProcess(
            argv, 0, "Monitors: 1\n 0: +*tiny 10/1x10/1+0+0  x\n", ""
        )

    with pytest.raises(StreamStartError, match="cannot be captured"):
        X11Capture(X11, tiny).start(DisplaySelection("tiny"))


def test_the_live_encoder_grabs_the_capture_region(tmp_path: Path) -> None:
    runner = FakeRunner()
    capture = X11Capture(X11, FakeXrandr())
    handle = capture.start(DisplaySelection("eDP-1"))
    live = encoder(tmp_path, runner)
    live._capture_region = capture.region  # what d1.assemble wires

    stream = live.start(CapturedVideo(handle), EncodingProfile(1280, 720, 30))

    argv = runner.argv[0]
    assert argv[argv.index("-i") + 1] == ":0+0,0"
    # The system sound goes with a captured screen, in the same segments.
    assert argv[argv.index("pulse") + 3 : argv.index("pulse") + 5] == ("-i", "@DEFAULT_MONITOR@")
    sound = argv[argv.index("1:a:0") - 1 : argv.index("1:a:0") + 5]
    assert sound == ("-map", "1:a:0", "-af", "aresample=async=1000", "-c:a", "aac")
    # The grab has its own queue, so reading the sound never makes it miss frames.
    grab = argv.index("x11grab")
    assert argv[grab + 1 : grab + 3] == ("-thread_queue_size", "1024")
    live.stop(stream)


def test_the_synthetic_picture_has_no_sound_and_sound_can_be_turned_off(tmp_path: Path) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    live.stop(live.start(SyntheticVideoSource(), EncodingProfile(1280, 720, 30)))
    assert "pulse" not in runner.argv[0]

    silent = FakeRunner()
    capture = X11Capture(X11, FakeXrandr())
    (tmp_path / "quiet").mkdir()
    quiet = encoder(
        tmp_path / "quiet",
        silent,
        settings=LiveEncoderSettings(stop_grace_seconds=0.05, system_audio=None),
    )
    quiet._capture_region = capture.region
    stream = quiet.start(
        CapturedVideo(capture.start(DisplaySelection("eDP-1"))), EncodingProfile(1280, 720, 30)
    )
    assert "pulse" not in silent.argv[0]
    quiet.stop(stream)


# D2: the extended screen ---------------------------------------------------------------


def no_wait(seconds: float) -> None:
    pass


def test_extend_enables_a_forced_output_at_the_chosen_place_and_removes_it() -> None:
    xrandr = FakeXrandr()
    extension = XrandrVirtualMonitor(X11, xrandr, no_wait)

    output = extension.create(1280, 720, consent=True, refresh=50, position=Position.BELOW)

    assert output == X11Output("HDMI-2", 1280, 720, 0, 1080, False)
    assert ("cvt", "1280", "720", "50") in xrandr.calls
    mode = "control-tv-1280x720-50"
    assert ("--addmode", "HDMI-2", mode) in xrandr.calls
    assert ("--output", "HDMI-2", "--mode", mode, "--below", "eDP-1") in xrandr.calls
    newmode = next(call for call in xrandr.calls if call[0] == "--newmode")
    assert newmode[:3] == ("--newmode", mode, "173.00")
    assert extension.active

    extension.remove()
    extension.remove()  # idempotent

    assert not extension.active and xrandr.enabled is None and xrandr.modes == set()
    assert xrandr.calls[-3:] == [
        ("--output", "HDMI-2", "--off"),
        ("--delmode", "HDMI-2", mode),
        ("--rmmode", mode),
    ]


def test_extend_needs_consent_and_a_supported_screen_and_changes_nothing_otherwise() -> None:
    xrandr = FakeXrandr()
    extension = XrandrVirtualMonitor(X11, xrandr, no_wait)
    with pytest.raises(StreamStartError, match="explicit consent"):
        extension.create(1280, 720, consent=False)
    with pytest.raises(InvalidStreamConfiguration, match="1280x720 or 1920x1080"):
        extension.create(1024, 768, consent=True)
    with pytest.raises(InvalidStreamConfiguration, match="60 or 50 Hz"):
        extension.create(1920, 1080, consent=True, refresh=30)
    with pytest.raises(InvalidStreamConfiguration, match="left, right, above or below"):
        extension.create(1920, 1080, consent=True, position="right-of")  # type: ignore[arg-type]
    assert xrandr.calls == []


def test_without_a_forced_output_nothing_is_changed() -> None:
    xrandr = FakeXrandr(forced=False)
    with pytest.raises(StreamStartError, match="no free video output is enabled"):
        XrandrVirtualMonitor(X11, xrandr, no_wait).create(1920, 1080, consent=True)
    assert xrandr.calls == [("--query",)]


def test_a_desktop_without_a_primary_screen_is_refused() -> None:
    def bare(argv: Sequence[str], environ: Mapping[str, str]) -> Done:
        return subprocess.CompletedProcess(argv, 0, "Screen 0: current 1 x 1\n", "")

    with pytest.raises(StreamStartError, match="primary screen cannot be found"):
        XrandrVirtualMonitor(X11, bare, no_wait).create(1920, 1080, consent=True)


@pytest.mark.parametrize(
    ("xrandr", "message"),
    [
        (FakeXrandr(fail="--output"), "xrandr --output failed"),
        (FakeXrandr(fail="cvt"), "cvt gave no mode"),
        (FakeXrandr(accept=False), "did not accept the extended screen"),
    ],
)
def test_a_failed_extension_undoes_every_step(xrandr: FakeXrandr, message: str) -> None:
    extension = XrandrVirtualMonitor(X11, xrandr, no_wait)
    with pytest.raises(StreamStartError, match=message):
        extension.create(1920, 1080, consent=True)
    assert not extension.active and xrandr.modes == set() and xrandr.enabled is None


def test_cvt_that_cannot_run_starts_nothing() -> None:
    xrandr = FakeXrandr()

    def no_cvt(argv: Sequence[str], environ: Mapping[str, str]) -> Done:
        if argv[0] == "cvt":
            raise FileNotFoundError("cvt")
        return xrandr(argv, environ)

    with pytest.raises(StreamStartError, match="cvt failed: FileNotFoundError"):
        XrandrVirtualMonitor(X11, no_cvt, no_wait).create(1920, 1080, consent=True)


def test_a_leftover_mode_is_removed_first_and_one_extension_at_a_time() -> None:
    mode = "control-tv-1920x1080-60"
    xrandr = FakeXrandr(leftover=mode)
    extension = XrandrVirtualMonitor(X11, xrandr, no_wait)
    extension.create(1920, 1080, consent=True, position=Position.LEFT)
    assert xrandr.calls.index(("--rmmode", mode)) < next(
        index for index, call in enumerate(xrandr.calls) if call[0] == "--newmode"
    )
    with pytest.raises(StreamStartError, match="already extended"):
        extension.create(1920, 1080, consent=True)
    extension.remove()


def test_a_missing_xrandr_is_reported() -> None:
    def missing(argv: Sequence[str], environ: Mapping[str, str]) -> Done:
        raise FileNotFoundError("xrandr")

    with pytest.raises(StreamStartError, match="FileNotFoundError"):
        XrandrVirtualMonitor(X11, missing, no_wait).create(1920, 1080, consent=True)


def test_a_removal_that_fails_keeps_the_extension_for_a_retry() -> None:
    xrandr = FakeXrandr()
    extension = XrandrVirtualMonitor(X11, xrandr, no_wait)
    extension.create(1280, 720, consent=True, position=Position.ABOVE)
    xrandr.fail = "--delmode"
    with pytest.raises(StreamCleanupError, match="delmode failed"):
        extension.remove()
    assert extension.active
    xrandr.fail = None
    extension.remove()
    assert not extension.active and xrandr.modes == set()


# The d1 session modes ------------------------------------------------------------------


def session(tmp_path: Path, picture: d1.Picture, xrandr: FakeXrandr) -> d1.D1Session:
    capture = X11Capture(X11, xrandr)
    live = encoder(tmp_path, FakeRunner())
    live._capture_region = capture.region
    return d1.assemble(
        seconds=5,
        bind_address="127.0.0.1",
        receiver=d1.NoReceiver(lambda uri: None),
        device_id="no-tv",
        picture=picture,
        encoder=live,
        capture=capture,
        virtual=XrandrVirtualMonitor(X11, xrandr),
    )


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_duplicate_streams_the_chosen_screen_and_forgets_the_capture(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    duplicated = session(tmp_path, d1.Picture(d1.Mode.DUPLICATE, "HDMI-1", 1280, 720), xrandr)
    clock = Clock()

    assert duplicated.run(clock.time, clock.sleep) == "duration"

    assert duplicated.capture is not None and duplicated.capture._captures == {}
    assert xrandr.enabled is None and not any(call[0] == "--newmode" for call in xrandr.calls)


def test_extend_streams_the_virtual_monitor_and_removes_it(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    extended = session(tmp_path, d1.Picture(d1.Mode.EXTEND, None, 1280, 720, consent=True), xrandr)
    assert xrandr.enabled is not None  # created before the session starts
    clock = Clock()

    assert extended.run(clock.time, clock.sleep) == "duration"

    assert xrandr.enabled is None and xrandr.modes == set()


def test_duplicate_needs_a_screen_and_extend_needs_consent(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    with pytest.raises(InvalidStreamConfiguration, match="choose the screen"):
        session(tmp_path, d1.Picture(d1.Mode.DUPLICATE, None), xrandr)
    with pytest.raises(StreamStartError, match="explicit consent"):
        session(tmp_path, d1.Picture(d1.Mode.EXTEND, None, 1280, 720), xrandr)
    assert xrandr.enabled is None


def test_a_failed_capture_removes_the_new_virtual_monitor(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    capture = X11Capture(X11, xrandr)

    def no_output(display: DisplaySelection) -> CaptureHandle:
        raise StreamStartError("that screen output is not connected")

    capture.start = no_output  # type: ignore[method-assign]
    with pytest.raises(StreamStartError, match="not connected"):
        d1.assemble(
            seconds=5,
            bind_address="127.0.0.1",
            receiver=d1.NoReceiver(lambda uri: None),
            device_id="no-tv",
            picture=d1.Picture(d1.Mode.EXTEND, None, 1280, 720, consent=True),
            encoder=encoder(tmp_path, FakeRunner()),
            capture=capture,
            virtual=XrandrVirtualMonitor(X11, xrandr),
        )
    assert xrandr.enabled is None and xrandr.modes == set()


def test_a_failed_start_releases_the_capture_and_the_virtual_monitor(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    extended = session(tmp_path, d1.Picture(d1.Mode.EXTEND, None, 1280, 720, consent=True), xrandr)
    extended.encoder._runner = FakeRunner(error=OSError("no ffmpeg"))

    with pytest.raises(StreamStartError, match="could not start"):
        extended.start()

    assert xrandr.enabled is None and extended.capture is not None
    assert extended.capture._captures == {}


def test_stop_attempts_every_step_and_raises_the_first_failure(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    extended = session(tmp_path, d1.Picture(d1.Mode.EXTEND, None, 1280, 720, consent=True), xrandr)
    extended.start()
    xrandr.fail = "--delmode"
    with pytest.raises(StreamCleanupError, match="delmode failed"):
        extended.stop()
    assert extended.publisher.unreleased == () and extended.capture is not None
    assert extended.capture._captures == {}  # the earlier steps ran
    xrandr.fail = None
    extended.stop()
    assert xrandr.enabled is None


def test_the_command_lists_the_screens(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(d1, "X11Capture", lambda: X11Capture(X11, FakeXrandr()))
    assert d1.main(["--list-screens"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "eDP-1: 1920x1080+0+0 (primary)",
        "HDMI-1: 2561x1441+1920+0",
    ]


def test_the_command_passes_the_mode_size_rate_and_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[d1.Picture] = []

    def assembled(**options: object) -> d1.D1Session:
        picture = options["picture"]
        assert isinstance(picture, d1.Picture)
        seen.append(picture)
        raise StreamStartError("stop here")

    monkeypatch.setattr(d1, "assemble", assembled)
    assert d1.main(["--no-tv", "--screen", "HDMI-1", "--size", "1280x720", "--fps", "25"]) == 1
    assert d1.main(["--no-tv", "--extend", "--size", "1920x1080", "--yes-change-display"]) == 1
    assert d1.main(["--no-tv"]) == 1
    assert seen == [
        d1.Picture(d1.Mode.DUPLICATE, "HDMI-1", 1280, 720, 25),
        d1.Picture(d1.Mode.EXTEND, None, 1920, 1080, 30, consent=True),
        d1.Picture(d1.Mode.SYNTHETIC, None, 1920, 1080, 30),
    ]
    for bad in (["--no-tv", "--size", "big"], []):
        with pytest.raises(SystemExit):
            d1.main(bad)


def test_the_small_remaining_paths(tmp_path: Path) -> None:
    from control_tv.screen_streaming.x11_capture import run_command

    assert run_command(("true",), {"PATH": "/usr/bin:/bin"}).returncode == 0
    assert parse_monitors("Monitors: 2\n garbage\n") == ()
    assert X11GrabSource(":0", 0, 0, 16, 16).kind.value == "x11"
    with pytest.raises(KeyboardInterrupt):
        d1._interrupt(15, None)
    live = encoder(tmp_path, FakeRunner())
    with pytest.raises(InvalidStreamConfiguration, match="synthetic picture or a screen"):
        live.start(object(), EncodingProfile())  # type: ignore[arg-type]
