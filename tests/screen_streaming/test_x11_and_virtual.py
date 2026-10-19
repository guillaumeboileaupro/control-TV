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
from control_tv.screen_streaming.virtual_display import VIRTUAL_MONITOR, XrandrVirtualMonitor
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

    assert runner.argv[0][runner.argv[0].index("-i") + 1] == ":0+0,0"
    live.stop(stream)


# D2: the virtual monitor ----------------------------------------------------------------


def test_extend_adds_a_virtual_monitor_to_the_right_and_removes_it() -> None:
    xrandr = FakeXrandr()
    virtual = XrandrVirtualMonitor(X11, xrandr)

    output = virtual.create(1280, 720, consent=True)

    assert output == X11Output(VIRTUAL_MONITOR, 1280, 720, 3840, 0, False)
    assert ("--fb", "5120x1080") in xrandr.calls
    assert ("--setmonitor", VIRTUAL_MONITOR, "1280/338x720/190+3840+0", "none") in xrandr.calls
    assert virtual.active and xrandr.virtual

    virtual.remove()
    virtual.remove()  # idempotent

    assert not virtual.active and not xrandr.virtual
    assert (xrandr.width, xrandr.height) == (3840, 1080)  # the original screen size


def test_extend_needs_consent_and_a_supported_size_and_changes_nothing_otherwise() -> None:
    xrandr = FakeXrandr()
    virtual = XrandrVirtualMonitor(X11, xrandr)
    with pytest.raises(StreamStartError, match="explicit consent"):
        virtual.create(1280, 720, consent=False)
    with pytest.raises(InvalidStreamConfiguration, match="1280x720 or 1920x1080"):
        virtual.create(1024, 768, consent=True)
    assert xrandr.calls == []


def test_a_failed_monitor_restores_the_screen_size() -> None:
    xrandr = FakeXrandr(fail="--setmonitor")
    with pytest.raises(StreamStartError, match="setmonitor failed"):
        XrandrVirtualMonitor(X11, xrandr).create(1920, 1080, consent=True)
    assert (xrandr.width, xrandr.height) == (3840, 1080) and not xrandr.virtual


def test_a_leftover_virtual_monitor_is_removed_first_and_one_at_a_time() -> None:
    xrandr = FakeXrandr(leftover=True)
    virtual = XrandrVirtualMonitor(X11, xrandr)
    virtual.create(1920, 1080, consent=True)
    assert xrandr.calls.index(("--delmonitor", VIRTUAL_MONITOR)) < xrandr.calls.index(
        ("--fb", "5760x1080")
    )
    with pytest.raises(StreamStartError, match="already exists"):
        virtual.create(1920, 1080, consent=True)
    virtual.remove()


def test_a_screen_that_cannot_grow_or_be_read_is_refused() -> None:
    xrandr = FakeXrandr()
    xrandr.width = 16000
    with pytest.raises(StreamStartError, match="cannot grow"):
        XrandrVirtualMonitor(X11, xrandr).create(1920, 1080, consent=True)

    def unreadable(
        argv: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "Monitors: 0\n", "")

    with pytest.raises(StreamStartError, match="size cannot be read"):
        XrandrVirtualMonitor(X11, unreadable).create(1920, 1080, consent=True)

    def missing(
        argv: Sequence[str], environ: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("xrandr")

    with pytest.raises(StreamStartError, match="FileNotFoundError"):
        XrandrVirtualMonitor(X11, missing).create(1920, 1080, consent=True)


def test_a_removal_that_fails_keeps_the_monitor_for_a_retry() -> None:
    xrandr = FakeXrandr()
    virtual = XrandrVirtualMonitor(X11, xrandr)
    virtual.create(1280, 720, consent=True)
    xrandr.fail = "--delmonitor"
    with pytest.raises(StreamCleanupError, match="delmonitor failed"):
        virtual.remove()
    assert virtual.active
    xrandr.fail = None
    virtual.remove()
    assert not virtual.active and not xrandr.virtual


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
    assert not xrandr.virtual and ("--fb", "5120x1080") not in xrandr.calls


def test_extend_streams_the_virtual_monitor_and_removes_it(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    extended = session(tmp_path, d1.Picture(d1.Mode.EXTEND, None, 1280, 720, consent=True), xrandr)
    assert xrandr.virtual  # created before the session starts
    clock = Clock()

    assert extended.run(clock.time, clock.sleep) == "duration"

    assert not xrandr.virtual and (xrandr.width, xrandr.height) == (3840, 1080)


def test_duplicate_needs_a_screen_and_extend_needs_consent(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    with pytest.raises(InvalidStreamConfiguration, match="choose the screen"):
        session(tmp_path, d1.Picture(d1.Mode.DUPLICATE, None), xrandr)
    with pytest.raises(StreamStartError, match="explicit consent"):
        session(tmp_path, d1.Picture(d1.Mode.EXTEND, None, 1280, 720), xrandr)
    assert not xrandr.virtual


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
    assert not xrandr.virtual and (xrandr.width, xrandr.height) == (3840, 1080)


def test_a_failed_start_releases_the_capture_and_the_virtual_monitor(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    extended = session(tmp_path, d1.Picture(d1.Mode.EXTEND, None, 1280, 720, consent=True), xrandr)
    extended.encoder._runner = FakeRunner(error=OSError("no ffmpeg"))

    with pytest.raises(StreamStartError, match="could not start"):
        extended.start()

    assert not xrandr.virtual and extended.capture is not None
    assert extended.capture._captures == {}


def test_stop_attempts_every_step_and_raises_the_first_failure(tmp_path: Path) -> None:
    xrandr = FakeXrandr()
    extended = session(tmp_path, d1.Picture(d1.Mode.EXTEND, None, 1280, 720, consent=True), xrandr)
    extended.start()
    xrandr.fail = "--delmonitor"
    with pytest.raises(StreamCleanupError, match="delmonitor failed"):
        extended.stop()
    assert extended.publisher.unreleased == () and extended.capture is not None
    assert extended.capture._captures == {}  # the earlier steps ran
    xrandr.fail = None
    extended.stop()
    assert not xrandr.virtual


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
