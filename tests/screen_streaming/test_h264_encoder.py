"""The D1 encoder adapter over the D0 engine. Fakes for processes; one optional real FFmpeg run
on a synthetic picture into a temporary directory (no capture, network or device)."""

from __future__ import annotations

import math
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pytest

from control_tv.screen_streaming import (
    CapturedVideo,
    CaptureHandle,
    EncodedStream,
    EncodingProfile,
    InvalidStreamConfiguration,
    PublicationConfig,
    PublicationKind,
    PublishedStream,
    ReceiverSession,
    ReceiverTarget,
    SessionState,
    StreamCleanupError,
    StreamingSessionService,
    StreamRequest,
    StreamStartError,
    SyntheticPattern,
    SyntheticVideoSource,
    h264_encoder,
)
from control_tv.screen_streaming.h264_encoder import D0LiveEncoder, LiveEncoderSettings
from control_tv.screen_streaming.model import VideoSource
from control_tv.screencast.capabilities import (
    FfmpegCapabilityProbe,
    select_plan,
)
from control_tv.screencast.model import (
    EncoderPreference,
    EncoderUnavailableError,
    HlsSegmenting,
    InvalidScreenCastOption,
    SoftwareX264,
    VaapiCqp,
)
from control_tv.screencast.process import ManagedProcess, SubprocessRunner
from live_encoder_fakes import BOTH, CPU_ONLY, FakeGroup, FakeProbe, FakeRunner, encoder

SOURCE = SyntheticVideoSource()
PROFILE = EncodingProfile(width=1280, height=720, fps=30)


def held(owner: D0LiveEncoder | StreamingSessionService) -> tuple[str, ...]:
    """Reads `unreleased` through a call, so each check sees the current value."""
    return owner.unreleased


def nothing_left(tmp_path: Path) -> bool:
    return list(tmp_path.iterdir()) == []


# Selection and arguments ------------------------------------------------------------------


def test_vaapi_is_selected_and_its_constant_qp_command_is_run(tmp_path: Path) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)

    stream = live.start(SOURCE, PROFILE)

    output = live.output(stream)
    assert isinstance(output.encoder, VaapiCqp) and output.fallback_reason is None
    argv = runner.argv[0]
    assert "h264_vaapi" in argv and "-b:v" not in argv
    assert argv[-1] == str(output.playlist) and output.playlist.name == "index.m3u8"
    assert "testsrc2=size=1280x720:rate=30" in argv
    assert argv[argv.index("-t") + 1] == "3600"
    assert output.directory.stat().st_mode & 0o777 == 0o700


def test_libx264_is_the_explicit_fallback_with_its_reason(tmp_path: Path) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner, FakeProbe(CPU_ONLY))

    output = live.output(live.start(SOURCE, PROFILE))

    assert output.encoder == SoftwareX264() and output.fallback_reason
    assert "libx264" in runner.argv[0]


def test_no_fallback_unless_allowed(tmp_path: Path) -> None:
    runner = FakeRunner()
    live = encoder(
        tmp_path,
        runner,
        FakeProbe(CPU_ONLY),
        LiveEncoderSettings(preference=EncoderPreference.VAAPI_ONLY),
    )

    with pytest.raises(StreamStartError, match="software fallback was not allowed"):
        live.start(SOURCE, PROFILE)
    assert runner.argv == [] and nothing_left(tmp_path) and held(live) == ()


def test_the_configured_session_bound_and_segmenting_reach_the_command(tmp_path: Path) -> None:
    runner = FakeRunner()
    settings = LiveEncoderSettings(
        max_session_seconds=90, segmenting=HlsSegmenting(segment_seconds=4, playlist_size=3)
    )
    encoder(tmp_path, runner, settings=settings).start(SOURCE, EncodingProfile(fps=15))

    argv = runner.argv[0]
    assert argv[argv.index("-t") + 1] == "90"
    assert argv[argv.index("-g") + 1] == "60" and argv[argv.index("-hls_list_size") + 1] == "3"


# Invalid configuration ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda: LiveEncoderSettings(qp=0),
        lambda: LiveEncoderSettings(crf=52),
        lambda: LiveEncoderSettings(max_session_seconds=0),
        lambda: LiveEncoderSettings(max_session_seconds=3601),
        lambda: LiveEncoderSettings(max_session_seconds=True),
        lambda: LiveEncoderSettings(stop_grace_seconds=-1),
        lambda: LiveEncoderSettings(stop_grace_seconds=math.nan),
        lambda: LiveEncoderSettings(stop_grace_seconds=61),
        lambda: LiveEncoderSettings(preference="anything"),  # type: ignore[arg-type]
    ],
    ids=[
        "qp",
        "crf",
        "session-0",
        "session-max",
        "session-bool",
        "grace-neg",
        "grace-nan",
        "grace-max",
        "preference",
    ],
)
def test_invalid_settings_are_refused(build: object) -> None:
    with pytest.raises(InvalidStreamConfiguration):
        build()  # type: ignore[operator]


def test_a_capture_source_or_a_foreign_profile_is_refused_before_any_process(
    tmp_path: Path,
) -> None:
    runner, probe = FakeRunner(), FakeProbe()
    live = encoder(tmp_path, runner, probe)

    with pytest.raises(InvalidStreamConfiguration, match="no capture adapter"):
        live.start(CapturedVideo(CaptureHandle("display-1")), PROFILE)
    with pytest.raises(InvalidStreamConfiguration, match="EncodingProfile"):
        live.start(SOURCE, object())  # type: ignore[arg-type]
    forged = SyntheticVideoSource()
    object.__setattr__(forged, "pattern", "x11grab")
    with pytest.raises(InvalidStreamConfiguration, match="cannot be encoded"):
        live.start(forged, PROFILE)
    malformed = EncodingProfile()
    object.__setattr__(malformed, "fps", "30:x")
    with pytest.raises(InvalidStreamConfiguration):
        live.start(SOURCE, malformed)

    assert runner.argv == [] and probe.calls == 0 and nothing_left(tmp_path)


# Start and stop ---------------------------------------------------------------------------


def test_stop_releases_the_group_then_the_output_exactly_once(tmp_path: Path) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    stream = live.start(SOURCE, PROFILE)
    assert held(live) == ("live-1",) and live.ended(stream) is False

    live.stop(stream)

    assert runner.group.signals == [signal.SIGTERM] and runner.group.releases == 1
    assert nothing_left(tmp_path) and held(live) == ()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (FileNotFoundError("ffmpeg"), "could not start: FileNotFoundError"),
        (EncoderUnavailableError("probe said no"), "probe said no"),
    ],
)
def test_a_process_that_cannot_start_leaves_nothing(
    tmp_path: Path, error: BaseException, message: str
) -> None:
    live = encoder(tmp_path, FakeRunner(error=error))

    with pytest.raises(StreamStartError, match=message):
        live.start(SOURCE, PROFILE)
    assert nothing_left(tmp_path) and held(live) == ()


def test_an_interrupted_start_leaves_nothing_and_is_re_raised(tmp_path: Path) -> None:
    live = encoder(tmp_path, FakeRunner(error=KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        live.start(SOURCE, PROFILE)
    assert nothing_left(tmp_path) and held(live) == ()


def test_an_output_directory_that_cannot_be_created_or_made_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_directory() -> Path:
        raise PermissionError("read-only")

    live = D0LiveEncoder(FakeRunner(), FakeProbe(), make_workdir=no_directory)
    with pytest.raises(StreamStartError, match="could not be created: PermissionError"):
        live.start(SOURCE, PROFILE)

    def refuse(self: Path, mode: int) -> None:
        raise PermissionError("chmod refused")

    live = encoder(tmp_path)
    monkeypatch.setattr(Path, "chmod", refuse)
    with pytest.raises(StreamStartError, match="could not be made private"):
        live.start(SOURCE, PROFILE)
    assert nothing_left(tmp_path)


# Failures, interruptions and partial cleanup ----------------------------------------------


def test_a_group_surviving_sigkill_stays_owned_and_is_retried(tmp_path: Path) -> None:
    runner = FakeRunner(group=FakeGroup(survives=True))
    live = encoder(tmp_path, runner)
    stream = live.start(SOURCE, PROFILE)

    with pytest.raises(StreamCleanupError, match=r"pid 4242\) survived SIGKILL"):
        live.stop(stream)
    assert held(live) == ("live-1",) and not nothing_left(tmp_path)

    runner.group.survives = False  # the kernel lets it go
    live.stop(stream)
    assert held(live) == () and runner.group.releases == 1 and nothing_left(tmp_path)


def test_an_interrupted_stop_keeps_the_stream_and_a_retry_finishes(tmp_path: Path) -> None:
    runner = FakeRunner(group=FakeGroup(interrupt=KeyboardInterrupt()))
    live = encoder(tmp_path, runner)
    stream = live.start(SOURCE, PROFILE)

    with pytest.raises(KeyboardInterrupt):
        live.stop(stream)
    assert held(live) == ("live-1",) and runner.group.releases == 0

    live.stop(stream)
    assert held(live) == () and runner.group.releases == 1


def test_a_signal_error_keeps_the_stream(tmp_path: Path) -> None:
    runner = FakeRunner(group=FakeGroup(interrupt=PermissionError("not allowed")))
    live = encoder(tmp_path, runner)
    stream = live.start(SOURCE, PROFILE)

    with pytest.raises(StreamCleanupError, match="could not be stopped: PermissionError"):
        live.stop(stream)
    assert held(live) == ("live-1",)


def test_an_output_that_cannot_be_removed_is_retried_without_stopping_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    stream = live.start(SOURCE, PROFILE)
    real_rmtree = shutil.rmtree

    def refuse(path: object, *args: object, **kwargs: object) -> None:
        raise PermissionError("busy")

    monkeypatch.setattr(shutil, "rmtree", refuse)
    with pytest.raises(StreamCleanupError, match="could not be removed: PermissionError"):
        live.stop(stream)
    assert runner.group.releases == 1 and held(live) == ("live-1",)

    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    live.stop(stream)
    assert runner.group.releases == 1 and runner.group.signals == [signal.SIGTERM]
    assert held(live) == () and nothing_left(tmp_path)


def test_an_already_removed_output_is_not_an_error(tmp_path: Path) -> None:
    live = encoder(tmp_path)
    stream = live.start(SOURCE, PROFILE)
    shutil.rmtree(live.output(stream).directory)

    live.stop(stream)
    assert held(live) == ()


def test_close_attempts_every_stream_and_reports_what_stays(tmp_path: Path) -> None:
    handles = iter(["live-a", "live-b"])
    groups = iter([FakeGroup(survives=True), FakeGroup()])

    class Runner(FakeRunner):
        def start(self, argv: Sequence[str], *, stderr_path: Path) -> ManagedProcess:
            self.group = next(groups)
            return super().start(argv, stderr_path=stderr_path)

    counter = iter(range(10))

    def workdir() -> Path:
        path = tmp_path / f"live-{next(counter)}"
        path.mkdir()
        return path

    live = D0LiveEncoder(
        Runner(),
        FakeProbe(),
        LiveEncoderSettings(stop_grace_seconds=0.01),
        make_workdir=workdir,
        handle_factory=lambda: next(handles),
    )
    live.start(SOURCE, PROFILE)
    live.start(SOURCE, PROFILE)

    with pytest.raises(StreamCleanupError, match="survived SIGKILL"):
        live.close()
    assert held(live) == ("live-a",)


def test_an_unknown_stream_has_no_output_or_status(tmp_path: Path) -> None:
    live = encoder(tmp_path)
    for query in (live.output, live.ended):
        with pytest.raises(StreamStartError, match="owns no stream"):
            query(EncodedStream("live-unknown"))


# Session expiry -------------------------------------------------------------------------------


def test_an_expired_session_is_reported_and_still_released(tmp_path: Path) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner, settings=LiveEncoderSettings(max_session_seconds=1))
    stream = live.start(SOURCE, PROFILE)
    runner.group.ended = True  # FFmpeg reached its -t bound

    assert live.ended(stream) is True
    live.stop(stream)
    assert runner.group.signals == [] and runner.group.releases == 1 and nothing_left(tmp_path)


# With the D1 service --------------------------------------------------------------------------


@dataclass
class Publisher:
    events: list[str] = field(default_factory=list)

    def start(self, stream: EncodedStream, config: PublicationConfig) -> PublishedStream:
        self.events.append("publisher.start")
        return PublishedStream("http://127.0.0.1/session/opaque/index.m3u8")

    def stop(self, publication: PublishedStream) -> None:
        self.events.append("publisher.stop")


@dataclass
class Receiver:
    gate: threading.Event | None = None
    entered: threading.Event = field(default_factory=threading.Event)

    def start(
        self, target: ReceiverTarget, publication: PublishedStream, *, timeout: float
    ) -> ReceiverSession:
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5.0)
        return ReceiverSession("receiver-1")

    def stop(self, session: ReceiverSession) -> None:
        pass


REQUEST = StreamRequest(
    SyntheticVideoSource(SyntheticPattern.SMPTE_BARS),
    EncodingProfile(),
    PublicationConfig(PublicationKind.HLS),
    ReceiverTarget("receiver-id"),
)


def test_the_d1_service_owns_and_releases_the_live_encoder(tmp_path: Path) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    service = StreamingSessionService(live, Publisher(), Receiver())

    service.start(REQUEST)
    assert service.state is SessionState.RUNNING and held(live) == ("live-1",)
    service.close()

    assert held(live) == () and runner.group.releases == 1 and nothing_left(tmp_path)


def test_a_concurrent_close_releases_the_live_encoder_of_the_cancelled_start(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    receiver = Receiver(gate=threading.Event())
    service = StreamingSessionService(live, Publisher(), receiver, close_timeout=0.01)
    errors: list[BaseException] = []

    def start() -> None:
        try:
            service.start(REQUEST)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=start)
    worker.start()
    assert receiver.entered.wait(5.0)
    service.close()  # cannot wait: recorded, then carried out by the start itself
    assert receiver.gate is not None
    receiver.gate.set()
    worker.join(5.0)

    assert len(errors) == 1 and isinstance(errors[0], StreamStartError)
    assert held(live) == () and runner.group.releases == 1 and nothing_left(tmp_path)


def test_a_d1_stop_failure_keeps_the_encoder_unreleased_for_a_retry(tmp_path: Path) -> None:
    runner = FakeRunner(group=FakeGroup(survives=True))
    live = encoder(tmp_path, runner)
    service = StreamingSessionService(live, Publisher(), Receiver())
    service.start(REQUEST)

    with pytest.raises(StreamCleanupError):
        service.stop()
    assert held(service) == ("encoder",) and held(live) == ("live-1",)

    runner.group.survives = False
    service.stop()
    assert held(service) == () and held(live) == () and runner.group.releases == 1


# One real run (synthetic picture, temporary directory, CPU encoder) ---------------------------


def _ffmpeg_has_libx264() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    listing = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False
    )
    return " libx264 " in listing.stdout


@pytest.mark.skipif(not _ffmpeg_has_libx264(), reason="needs a local FFmpeg with libx264")
def test_a_real_synthetic_live_encode_writes_a_playlist_and_segments() -> None:
    live = D0LiveEncoder(
        SubprocessRunner(),
        FfmpegCapabilityProbe(),
        LiveEncoderSettings(
            preference=EncoderPreference.SOFTWARE_ONLY,
            max_session_seconds=3,
            segmenting=HlsSegmenting(segment_seconds=1, playlist_size=3),
        ),
    )
    started = time.monotonic()
    stream = live.start(SyntheticVideoSource(), EncodingProfile(width=320, height=240, fps=10))
    output = live.output(stream)
    try:
        deadline = time.monotonic() + 30
        while not live.ended(stream) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert time.monotonic() - started >= 2.5  # paced in real time, not generated at once
        playlist = output.playlist.read_text(encoding="utf-8")
        assert playlist.startswith("#EXTM3U") and "#EXT-X-ENDLIST" in playlist
        assert len(list(output.directory.glob("segment-*.ts"))) >= 1
    finally:
        live.stop(stream)
    assert not output.directory.exists() and held(live) == ()


# ScreenEncoder.stop is idempotent ---------------------------------------------------------


def test_a_second_stop_confirms_the_release_without_releasing_twice(tmp_path: Path) -> None:
    """Regression: a repeated stop raised "owns no stream", against the port's idempotency."""
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    stream = live.start(SOURCE, PROFILE)
    live.stop(stream)

    live.stop(stream)
    live.stop(stream)

    assert runner.group.signals == [signal.SIGTERM] and runner.group.releases == 1
    assert held(live) == () and nothing_left(tmp_path)


def test_a_transient_failure_is_retried_then_further_stops_are_no_ops(tmp_path: Path) -> None:
    runner = FakeRunner(group=FakeGroup(interrupt=PermissionError("not allowed")))
    live = encoder(tmp_path, runner)
    stream = live.start(SOURCE, PROFILE)

    with pytest.raises(StreamCleanupError, match="could not be stopped: PermissionError"):
        live.stop(stream)
    assert held(live) == ("live-1",) and runner.group.releases == 0

    live.stop(stream)
    live.stop(stream)
    assert held(live) == () and runner.group.releases == 1
    assert runner.group.signals == [signal.SIGTERM]


def test_an_unreleased_stream_is_never_reported_released(tmp_path: Path) -> None:
    runner = FakeRunner(group=FakeGroup(survives=True))
    live = encoder(tmp_path, runner)
    stream = live.start(SOURCE, PROFILE)

    for _ in range(2):  # every stop says the truth while the group survives
        with pytest.raises(StreamCleanupError, match="survived SIGKILL"):
            live.stop(stream)
        assert held(live) == ("live-1",) and runner.group.releases == 0

    runner.group.survives = False
    live.stop(stream)
    live.stop(stream)
    assert held(live) == () and runner.group.releases == 1


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(7)])
def test_an_interruption_after_the_release_lets_the_d1_retry_confirm_it(
    tmp_path: Path, interruption: BaseException
) -> None:
    """Codex's scenario: the adapter released everything, then an interruption reached the
    service before it recorded the release; the service's retry must confirm, not fail."""
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    calls: list[str] = []

    class InterruptedAfterStop:
        def start(self, source: VideoSource, profile: EncodingProfile) -> EncodedStream:
            return live.start(source, profile)

        def stop(self, stream: EncodedStream) -> None:
            live.stop(stream)
            calls.append("stopped")
            if len(calls) == 1:
                raise interruption

    service = StreamingSessionService(InterruptedAfterStop(), Publisher(), Receiver())
    service.start(REQUEST)

    with pytest.raises(type(interruption)):
        service.stop()
    assert held(service) == ("encoder",) and held(live) == ()  # the service still owns it

    service.stop()  # the retry confirms the release
    assert held(service) == () and service.state is SessionState.STOPPED
    assert calls == ["stopped", "stopped"] and runner.group.releases == 1
    assert nothing_left(tmp_path)


def test_concurrent_stops_release_the_stream_once(tmp_path: Path) -> None:
    gate, entered = threading.Event(), threading.Event()

    class SlowGroup(FakeGroup):
        def signal_group(self, sig: signal.Signals) -> None:
            entered.set()
            assert gate.wait(5.0)
            super().signal_group(sig)

    runner = FakeRunner(group=SlowGroup())
    live = encoder(tmp_path, runner)
    stream = live.start(SOURCE, PROFILE)
    failures: list[BaseException] = []

    def stop() -> None:
        try:
            live.stop(stream)
        except BaseException as error:
            failures.append(error)

    first = threading.Thread(target=stop)
    first.start()
    assert entered.wait(5.0)
    second = threading.Thread(target=stop)
    second.start()  # waits for the first stop of the same stream, then confirms
    gate.set()
    first.join(5.0)
    second.join(5.0)

    assert not first.is_alive() and not second.is_alive() and failures == []
    assert runner.group.signals == [signal.SIGTERM] and runner.group.releases == 1
    assert held(live) == () and nothing_left(tmp_path)


def test_a_stream_this_encoder_never_issued_is_still_refused(tmp_path: Path) -> None:
    live = encoder(tmp_path)
    with pytest.raises(StreamCleanupError, match="owns no stream live-unknown"):
        live.stop(EncodedStream("live-unknown"))


def test_a_reissued_handle_is_stopped_again(tmp_path: Path) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)  # the test factory always answers "live-1"
    live.stop(live.start(SOURCE, PROFILE))
    runner.group = FakeGroup()

    stream = live.start(SOURCE, PROFILE)
    assert held(live) == ("live-1",)
    live.stop(stream)
    assert held(live) == () and runner.group.releases == 1


# P2-1: a started encoder process is never left untracked ----------------------------------


class FailingOnceLock:
    """The encoder's lock, except its next acquisition raises `error` once: the window between
    the process start and its registration (the first use of the lock in `start`)."""

    def __init__(self, error: BaseException) -> None:
        self._lock = threading.Lock()
        self._error: BaseException | None = error

    def __enter__(self) -> bool:
        if self._error is not None:
            error, self._error = self._error, None
            raise error
        return self._lock.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self._lock.release()


@pytest.mark.parametrize(
    "error", [KeyboardInterrupt(), SystemExit(8), RuntimeError("bug")], ids=["kbi", "exit", "bug"]
)
def test_an_interruption_before_registration_stops_the_started_process(
    tmp_path: Path, error: BaseException
) -> None:
    """Regression: an interruption after the runner returned but before the session was recorded
    left FFmpeg running untracked until its one-hour bound, with its output directory leaked."""
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    live._lock = FailingOnceLock(error)  # type: ignore[assignment]

    with pytest.raises(type(error)):
        live.start(SOURCE, PROFILE)

    assert runner.argv  # the process had started
    assert runner.group.signals == [signal.SIGTERM] and runner.group.releases == 1
    assert held(live) == () and nothing_left(tmp_path)


def test_a_started_process_that_cannot_be_stopped_stays_tracked(tmp_path: Path) -> None:
    runner = FakeRunner(group=FakeGroup(survives=True))
    live = encoder(tmp_path, runner)
    live._lock = FailingOnceLock(KeyboardInterrupt())  # type: ignore[assignment]

    with pytest.raises(KeyboardInterrupt):
        live.start(SOURCE, PROFILE)

    assert held(live) == ("live-1",) and not nothing_left(tmp_path)  # owned, not lost
    runner.group.survives = False
    live.close()  # application exit releases it
    assert held(live) == () and runner.group.releases == 1 and nothing_left(tmp_path)


def test_an_interruption_while_abandoning_the_start_keeps_the_process_tracked(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(group=FakeGroup(interrupt=SystemExit(9)))
    live = encoder(tmp_path, runner)
    live._lock = FailingOnceLock(KeyboardInterrupt())  # type: ignore[assignment]

    with pytest.raises(SystemExit):
        live.start(SOURCE, PROFILE)  # the second interruption hits the stop of the first

    assert held(live) == ("live-1",) and runner.group.releases == 0
    live.stop(EncodedStream("live-1"))
    assert held(live) == () and runner.group.releases == 1 and nothing_left(tmp_path)


@pytest.mark.parametrize(
    "error",
    [KeyboardInterrupt(), SystemExit(10), OSError("exec failed")],
    ids=["kbi", "exit", "oserror"],
)
def test_a_start_failing_inside_the_runner_leaves_nothing(
    tmp_path: Path, error: BaseException
) -> None:
    runner = FakeRunner(error=error)
    live = encoder(tmp_path, runner)

    with pytest.raises(StreamStartError if isinstance(error, OSError) else type(error)):
        live.start(SOURCE, PROFILE)
    assert held(live) == () and nothing_left(tmp_path)


def test_a_command_that_cannot_be_built_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)

    def interrupted(*args: object) -> tuple[str, ...]:
        raise KeyboardInterrupt

    monkeypatch.setattr(h264_encoder, "live_hls_command", interrupted)
    with pytest.raises(KeyboardInterrupt):
        live.start(SOURCE, PROFILE)
    assert runner.argv == [] and held(live) == () and nothing_left(tmp_path)


# P2-2: the encoder preference is normalized before any selection ---------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (EncoderPreference.SOFTWARE_ONLY, EncoderPreference.SOFTWARE_ONLY),
        ("software_only", EncoderPreference.SOFTWARE_ONLY),
        ("vaapi_only", EncoderPreference.VAAPI_ONLY),
        ("vaapi_then_software", EncoderPreference.VAAPI_THEN_SOFTWARE),
    ],
)
def test_a_valid_preference_is_stored_as_the_enum_member(
    given: str, expected: EncoderPreference
) -> None:
    settings = LiveEncoderSettings(preference=given)  # type: ignore[arg-type]
    assert type(settings.preference) is EncoderPreference and settings.preference is expected


def test_a_software_only_string_never_selects_vaapi(tmp_path: Path) -> None:
    """Regression: "software_only" passed validation but stayed a string; `select_plan` compares
    by identity, so a VA-API-capable host selected VA-API for a software-only request."""
    runner = FakeRunner()
    settings = LiveEncoderSettings(preference="software_only", stop_grace_seconds=0.05)  # type: ignore[arg-type]
    live = encoder(tmp_path, runner, FakeProbe(BOTH), settings)

    stream = live.start(SOURCE, PROFILE)

    assert isinstance(live.output(stream).encoder, SoftwareX264)
    assert "libx264" in runner.argv[0] and "h264_vaapi" not in runner.argv[0]
    live.stop(stream)


class OtherPreference(StrEnum):
    SOFTWARE_ONLY = "software_only"


@pytest.mark.parametrize(
    "given",
    [
        "SOFTWARE_ONLY",
        " software_only",
        "software_only\n",
        "libx264",
        "",
        None,
        True,
        1,
        OtherPreference.SOFTWARE_ONLY,  # same value, another enum: ambiguous
    ],
    ids=["upper", "space", "newline", "encoder-name", "empty", "none", "bool", "int", "foreign"],
)
def test_an_invalid_or_ambiguous_preference_is_refused(given: object) -> None:
    with pytest.raises(InvalidStreamConfiguration, match="preference"):
        LiveEncoderSettings(preference=given)  # type: ignore[arg-type]


def test_select_plan_refuses_a_preference_that_is_not_the_enum() -> None:
    with pytest.raises(InvalidScreenCastOption, match="EncoderPreference"):
        select_plan(
            "software_only",  # type: ignore[arg-type]
            LiveEncoderSettings().device,
            qp=24,
            crf=23,
            capabilities=BOTH,
        )


@pytest.mark.parametrize("survives", [False, True], ids=["stopped", "kept"])
def test_an_interruption_right_after_registration_is_undone_once(
    tmp_path: Path, survives: bool
) -> None:
    runner = FakeRunner(group=FakeGroup(survives=survives))
    live = encoder(tmp_path, runner)
    register = live._register

    def register_then_interrupted(handle: str, session: object) -> None:
        register(handle, session)  # type: ignore[arg-type]
        raise KeyboardInterrupt

    live._register = register_then_interrupted  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt):
        live.start(SOURCE, PROFILE)

    if survives:  # still registered once, with its output, for a later stop
        assert held(live) == ("live-1",) and not nothing_left(tmp_path)
        runner.group.survives = False
        live.stop(EncodedStream("live-1"))
    assert held(live) == () and runner.group.releases == 1 and nothing_left(tmp_path)


# The output of an abandoned start is never left untracked ---------------------------------


def refusing_rmtree(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Make directory removal fail (busy, permission) until the returned callable restores it."""
    real = shutil.rmtree

    def refuse(path: object, *args: object, ignore_errors: bool = False, **kwargs: object) -> None:
        if ignore_errors:
            return  # like the real rmtree: the failure is swallowed and the directory stays
        raise PermissionError("busy")

    monkeypatch.setattr(shutil, "rmtree", refuse)
    return lambda: monkeypatch.setattr(shutil, "rmtree", real)


@pytest.mark.parametrize(
    "error", [KeyboardInterrupt(), SystemExit(11), RuntimeError("bug")], ids=["kbi", "exit", "bug"]
)
def test_an_output_that_cannot_be_removed_after_an_abandoned_start_stays_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """Regression: the session was dropped before its directory was removed with errors
    ignored, so a failed removal leaked the HLS directory with nothing left to retry it."""
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    live._lock = FailingOnceLock(error)  # type: ignore[assignment]
    restore = refusing_rmtree(monkeypatch)

    with pytest.raises(type(error)) as raised:  # the initial exception, never the removal's
        live.start(SOURCE, PROFILE)
    assert raised.value is error

    assert runner.group.releases == 1  # the process was stopped once
    assert held(live) == ("live-1",) and not nothing_left(tmp_path)  # output still tracked

    restore()
    live.close()  # application exit retries only the removal
    assert held(live) == () and nothing_left(tmp_path)
    assert runner.group.releases == 1 and runner.group.signals == [signal.SIGTERM]
    live.stop(EncodedStream("live-1"))  # idempotent afterwards
    assert runner.group.releases == 1


def test_an_output_that_cannot_be_removed_when_the_runner_failed_stays_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner(error=OSError("exec failed"))
    live = encoder(tmp_path, runner)
    restore = refusing_rmtree(monkeypatch)

    with pytest.raises(StreamStartError, match="could not start: OSError"):
        live.start(SOURCE, PROFILE)
    assert held(live) == ("live-1",) and not nothing_left(tmp_path)

    restore()
    live.stop(EncodedStream("live-1"))
    assert held(live) == () and nothing_left(tmp_path)


def test_a_persistent_removal_failure_keeps_the_output_and_close_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    live._lock = FailingOnceLock(KeyboardInterrupt())  # type: ignore[assignment]
    refusing_rmtree(monkeypatch)

    with pytest.raises(KeyboardInterrupt):
        live.start(SOURCE, PROFILE)
    for _ in range(2):
        with pytest.raises(StreamCleanupError, match="could not be removed: PermissionError"):
            live.close()
        assert held(live) == ("live-1",) and runner.group.releases == 1  # never stopped twice


def test_an_output_already_gone_when_a_start_is_abandoned_is_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    live._lock = FailingOnceLock(KeyboardInterrupt())  # type: ignore[assignment]
    real = shutil.rmtree

    def already_gone(path: Path, *args: object, **kwargs: object) -> None:
        real(path)
        raise FileNotFoundError(str(path))

    monkeypatch.setattr(shutil, "rmtree", already_gone)
    with pytest.raises(KeyboardInterrupt):
        live.start(SOURCE, PROFILE)
    assert held(live) == () and nothing_left(tmp_path) and runner.group.releases == 1


# Readiness: a receiver is loaded only once the playlist holds a few segments -------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _waiting(tmp_path: Path, runner: FakeRunner, clock: _Clock) -> D0LiveEncoder:
    return D0LiveEncoder(
        runner,
        FakeProbe(),
        LiveEncoderSettings(stop_grace_seconds=0.05),
        make_workdir=lambda: tmp_path,
        clock=clock,
        sleep=clock.sleep,
    )


def test_start_waits_until_the_playlist_lists_enough_segments(tmp_path: Path) -> None:
    clock = _Clock()
    runner = FakeRunner(segments=1)
    live = _waiting(tmp_path, runner, clock)

    def grow(seconds: float) -> None:
        clock.now += seconds
        if clock.now >= 3:  # FFmpeg has written more segments meanwhile
            listed = "".join(f"#EXTINF:2.0,\nsegment-{n:05d}.ts\n" for n in range(4))
            (tmp_path / "index.m3u8").write_text(f"#EXTM3U\n{listed}")

    live._sleep = grow
    stream = live.start(SOURCE, PROFILE)

    assert clock.now >= 3
    live.stop(stream)


def test_an_encoder_that_ends_or_writes_nothing_in_time_starts_nothing(tmp_path: Path) -> None:
    ended = FakeRunner(segments=0)
    ended.group.ended = True
    (tmp_path / "a").mkdir()
    with pytest.raises(StreamStartError, match="stopped before its first segments"):
        _waiting(tmp_path / "a", ended, _Clock()).start(SOURCE, PROFILE)

    silent = FakeRunner(segments=0)
    (tmp_path / "b").mkdir()
    live = _waiting(tmp_path / "b", silent, _Clock())
    with pytest.raises(StreamStartError, match="no segment in time"):
        live.start(SOURCE, PROFILE)
    assert live.unreleased == () and silent.group.signals
