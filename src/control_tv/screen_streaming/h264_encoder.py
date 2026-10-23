"""The D1 `ScreenEncoder` over the merged D0 H.264 engine (`control_tv.screencast`).

This is a translation layer, not a second FFmpeg engine. It reuses D0's selection policy
(`select_plan`: VA-API constant QP, the libx264 fallback only when allowed), its argument
builders (`live_hls_command`, which uses `source_arguments` and `encoder_arguments`), and its
process ownership (`SubprocessRunner`, `stop_process`: the whole process group, never reaped
while a member runs). It writes HLS segments and a playlist into a private temporary directory,
and opens no listener: serving them is the publisher's job.

Ownership. Every started stream is tracked until both its process group is released and its
directory removed. A stop that cannot finish (a group that survives SIGKILL, a directory that
cannot be removed, an interruption) keeps the stream in `unreleased` and raises, so the D1
service keeps it owned and retries; a retry only redoes what is left, so nothing is stopped or
removed twice. A stream nobody stops still ends by itself after `max_session_seconds`.

No screen is captured: D1's `CapturedVideo` is refused until a capture adapter exists.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from control_tv.screen_streaming.model import (
    CapturedVideo,
    CaptureHandle,
    EncodedStream,
    EncodingProfile,
    InvalidStreamConfiguration,
    StreamCleanupError,
    StreamStartError,
    SyntheticVideoSource,
    VideoSource,
)
from control_tv.screencast.capabilities import CapabilityProbe, select_plan
from control_tv.screencast.ffmpeg import live_hls_command
from control_tv.screencast.model import (
    HLS_PLAYLIST,
    MAX_LIVE_SESSION_SECONDS,
    EncoderChoice,
    EncoderPlan,
    EncoderPreference,
    HlsSegmenting,
    InvalidScreenCastOption,
    ScreenCastError,
    SoftwareX264,
    SyntheticSource,
    VaapiCqp,
    VaapiDevice,
    X11GrabSource,
)
from control_tv.screencast.process import ManagedProcess, ProcessRunner, stop_process

STDERR_NAME = "ffmpeg-stderr.log"
_MAX_STOP_GRACE = 60.0
_DEFAULT_DEVICE = VaapiDevice("/dev/dri/renderD128")
_DEFAULT_SEGMENTING = HlsSegmenting()
SYSTEM_AUDIO = "@DEFAULT_MONITOR@"
# A receiver loaded before the playlist holds a few segments starts at the live edge with no
# buffer and stalls at every segment (seen on the TV): start returns once this many are listed.
_READY_SEGMENTS = 4
_READY_MARGIN_SECONDS = 15.0
_READY_POLL_SECONDS = 0.1


def _preference(value: object) -> EncoderPreference:
    """The `EncoderPreference` member `value` names exactly: the member itself or its exact
    lower-case value as a plain `str`. Anything else (another enum with the same value, other
    case, surrounding spaces, an encoder name, a non-string) is refused, never guessed."""
    if type(value) is EncoderPreference:
        return value
    if type(value) is str:
        for member in EncoderPreference:
            if value == member.value:
                return member
    allowed = ", ".join(member.value for member in EncoderPreference)
    raise InvalidStreamConfiguration(
        f"invalid live encoder settings: preference must be one of {allowed}: {value!r}"
    )


@dataclass(frozen=True)
class LiveEncoderSettings:
    """Owner-chosen encoding policy for live streams; checked on construction."""

    preference: EncoderPreference = EncoderPreference.VAAPI_THEN_SOFTWARE
    device: VaapiDevice = _DEFAULT_DEVICE
    qp: int = 24
    crf: int = 23
    segmenting: HlsSegmenting = _DEFAULT_SEGMENTING
    max_session_seconds: int = MAX_LIVE_SESSION_SECONDS
    stop_grace_seconds: float = 5.0
    # The system sound sent with a captured screen (PulseAudio's monitor of the default output,
    # which follows the output the owner picks); None sends the picture only.
    system_audio: str | None = SYSTEM_AUDIO

    def __post_init__(self) -> None:
        # Stored as the enum member: `select_plan` compares by identity, so a plain string that
        # merely equals a value must never reach it (a software-only request selected VA-API).
        object.__setattr__(self, "preference", _preference(self.preference))
        try:
            VaapiCqp(self.device, self.qp)  # D0's own bounds for QP and CRF
            SoftwareX264(self.crf)
            HlsSegmenting(self.segmenting.segment_seconds, self.segmenting.playlist_size)
        except (InvalidScreenCastOption, AttributeError, TypeError, ValueError) as error:
            raise InvalidStreamConfiguration(f"invalid live encoder settings: {error}") from None
        seconds = self.max_session_seconds
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, int)
            or not 1 <= seconds <= MAX_LIVE_SESSION_SECONDS
        ):
            raise InvalidStreamConfiguration(
                f"max_session_seconds must be an integer from 1 to {MAX_LIVE_SESSION_SECONDS}: "
                f"{seconds!r}"
            )
        grace = self.stop_grace_seconds
        if (
            isinstance(grace, bool)
            or not isinstance(grace, int | float)
            or not math.isfinite(grace)
            or not 0 <= grace <= _MAX_STOP_GRACE
        ):
            raise InvalidStreamConfiguration(
                f"stop_grace_seconds must be a finite number from 0 to {_MAX_STOP_GRACE:g}: "
                f"{grace!r}"
            )


@dataclass(frozen=True)
class LiveOutput:
    """Where a live stream's HLS output is, for the publisher, and which encoder makes it."""

    directory: Path
    playlist: Path
    encoder: EncoderPlan
    fallback_reason: str | None


@dataclass
class _LiveSession:
    output: LiveOutput
    process: ManagedProcess | None
    # Serializes the stops of this stream, so concurrent stops never release anything twice.
    stopping: threading.Lock = field(default_factory=threading.Lock)


def _private_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="control-tv-live-"))


class D0LiveEncoder:
    """`ScreenEncoder` for D1: one D0 FFmpeg process group per stream, HLS output on disk."""

    def __init__(
        self,
        runner: ProcessRunner,
        probe: CapabilityProbe,
        settings: LiveEncoderSettings | None = None,
        *,
        ffmpeg: str = "ffmpeg",
        make_workdir: Callable[[], Path] = _private_workdir,
        handle_factory: Callable[[], str] = lambda: f"live-{uuid.uuid4().hex}",
        capture_region: Callable[[CaptureHandle], X11GrabSource] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """`capture_region` resolves a `CapturedVideo` source into the X11 region FFmpeg grabs
        (`X11Capture.region`); without it, only the synthetic picture can be encoded."""
        self._capture_region = capture_region
        self._clock = clock
        self._sleep = sleep
        self._runner = runner
        self._probe = probe
        self._settings = settings or LiveEncoderSettings()
        self._ffmpeg = ffmpeg
        self._make_workdir = make_workdir
        self._handle_factory = handle_factory
        self._sessions: dict[str, _LiveSession] = {}
        self._released: set[str] = set()  # handles issued here and fully released since
        self._lock = threading.Lock()

    @property
    def unreleased(self) -> tuple[str, ...]:
        """Handles of streams not yet fully released (process group or output directory)."""
        with self._lock:
            return tuple(self._sessions)

    def start(self, source: VideoSource, profile: EncodingProfile) -> EncodedStream:
        frame = self._checked_profile(profile)
        live_source = self._live_source(source)
        settings = self._settings
        try:
            choice = select_plan(
                settings.preference,
                settings.device,
                qp=settings.qp,
                crf=settings.crf,
                capabilities=self._probe.probe(settings.device),
            )
        except ScreenCastError as error:
            raise StreamStartError(f"no live encoder can run: {error.message}") from None
        handle = self._handle_factory()
        directory = self._new_directory()
        output = self._output(directory, choice)
        process: ManagedProcess | None = None
        try:
            argv = live_hls_command(
                self._ffmpeg,
                live_source,
                frame,
                choice.plan,
                directory,
                settings.segmenting,
                settings.max_session_seconds,
                settings.system_audio if type(live_source) is X11GrabSource else None,
            )
            process = self._runner.start(argv, stderr_path=directory / STDERR_NAME)
            # Registered inside the guard: an interruption before this completes is handled
            # below, so a started process is never left untracked.
            self._register(handle, _LiveSession(output, process))
            self._await_segments(output.playlist, process, settings.segmenting)
        except BaseException as error:
            self._abandon_start(handle, output, process)
            if isinstance(error, OSError):
                raise StreamStartError(
                    f"the live encoder could not start: {type(error).__name__}"
                ) from None
            if isinstance(error, ScreenCastError):
                raise StreamStartError(error.message) from None
            raise
        return EncodedStream(handle)

    def _await_segments(
        self, playlist: Path, process: ManagedProcess, segmenting: HlsSegmenting
    ) -> None:
        """Return once the playlist lists `_READY_SEGMENTS` segments, so a receiver loaded next
        starts with a buffer behind the live edge; bounded, and refused if FFmpeg ends first."""
        ready = min(_READY_SEGMENTS, segmenting.playlist_size)
        deadline = self._clock() + ready * segmenting.segment_seconds + _READY_MARGIN_SECONDS
        while _listed_segments(playlist) < ready:
            if process.exit_status() is not None:
                raise StreamStartError("the live encoder stopped before its first segments")
            if self._clock() >= deadline:
                raise StreamStartError("the live encoder produced no segment in time")
            self._sleep(_READY_POLL_SECONDS)

    def _register(self, handle: str, session: _LiveSession) -> None:
        with self._lock:
            self._sessions[handle] = session
            self._released.discard(handle)

    def _abandon_start(
        self, handle: str, output: LiveOutput, process: ManagedProcess | None
    ) -> None:
        """Undo a failed or interrupted start; the caller re-raises its own exception. A process
        that started is stopped, then the output is removed. Whatever cannot be released (a
        process surviving SIGKILL, a signal error, an output that cannot be removed, another
        interruption) stays registered, so it is in `unreleased` and `stop()`/`close()` retry
        exactly what is left: never untracked, never released twice. A removal failure is kept
        for that retry instead of replacing the caller's exception."""
        with self._lock:
            session = self._sessions.get(handle)
        if session is None or session.process is not process:
            session = _LiveSession(output, process)
        try:
            if session.process is not None:
                status = stop_process(session.process, float(self._settings.stop_grace_seconds))
                if status is None:
                    self._register(handle, session)
                    return
                session.process = None  # released: a retry only removes the output
            try:
                shutil.rmtree(output.directory)
            except FileNotFoundError:
                pass  # already gone: nothing left to release
            except OSError:
                self._register(handle, session)
                return
        except BaseException:
            self._register(handle, session)
            raise
        with self._lock:
            if self._sessions.get(handle) is session:
                del self._sessions[handle]

    def stop(self, stream: EncodedStream) -> None:
        """Release the stream's process group, then its output. Idempotent, as `ScreenEncoder`
        requires: stopping a stream already released succeeds without touching anything, so a
        caller interrupted before recording a release can retry. Raises (keeping the stream) when
        either part cannot be released; an interruption keeps it too. Concurrent stops of one
        stream are serialized; nothing is ever released twice. A handle this encoder never issued
        is refused."""
        with self._lock:
            session = self._sessions.get(stream.handle)
            if session is None:
                if stream.handle in self._released:
                    return
                raise StreamCleanupError(f"this encoder owns no stream {stream.handle}")
        with session.stopping:
            with self._lock:
                if self._sessions.get(stream.handle) is not session:
                    return  # a concurrent stop released it meanwhile
            self._release(stream.handle, session)

    def _release(self, handle: str, session: _LiveSession) -> None:
        process = session.process
        if process is not None:
            try:
                status = stop_process(process, float(self._settings.stop_grace_seconds))
            except OSError as error:
                raise StreamCleanupError(
                    f"the live encoder could not be stopped: {type(error).__name__}"
                ) from None
            if status is None:
                raise StreamCleanupError(
                    f"the live encoder (pid {process.pid}) survived SIGKILL; it stays owned"
                )
            session.process = None
        try:
            shutil.rmtree(session.output.directory)
        except FileNotFoundError:
            pass  # already gone: nothing left to release
        except OSError as error:
            raise StreamCleanupError(
                f"the live output could not be removed: {type(error).__name__}"
            ) from None
        with self._lock:
            del self._sessions[handle]
            self._released.add(handle)

    def close(self) -> None:
        """Stop every stream still owned (application exit). Every stream is attempted; the ones
        that cannot be released stay in `unreleased` and the failures are raised together."""
        failures: list[str] = []
        for handle in self.unreleased:
            try:
                self.stop(EncodedStream(handle))
            except StreamCleanupError as error:
                failures.append(error.message)
        if failures:
            raise StreamCleanupError("; ".join(failures))

    def output(self, stream: EncodedStream) -> LiveOutput:
        """The stream's HLS output, for the publisher (`StreamStartError` if not owned)."""
        with self._lock:
            session = self._sessions.get(stream.handle)
        if session is None:
            raise StreamStartError(f"this encoder owns no stream {stream.handle}")
        return session.output

    def ended(self, stream: EncodedStream) -> bool:
        """Whether the stream's encoder has exited by itself (session bound reached or failure)."""
        with self._lock:
            session = self._sessions.get(stream.handle)
        if session is None:
            raise StreamStartError(f"this encoder owns no stream {stream.handle}")
        return session.process is None or session.process.exit_status() is not None

    # Internals -----------------------------------------------------------------------------

    @staticmethod
    def _checked_profile(profile: EncodingProfile) -> EncodingProfile:
        if type(profile) is not EncodingProfile:
            raise InvalidStreamConfiguration("profile must be an EncodingProfile")
        # Rebuilt so a value changed after construction is checked again (its own bounds).
        return EncodingProfile(profile.width, profile.height, profile.fps)

    def _live_source(self, source: VideoSource) -> SyntheticSource | X11GrabSource:
        if type(source) is CapturedVideo:
            if self._capture_region is None:
                raise InvalidStreamConfiguration(
                    "a captured screen needs a capture adapter: no capture adapter is configured"
                )
            return self._capture_region(source.handle)
        if type(source) is not SyntheticVideoSource:
            raise InvalidStreamConfiguration("only the synthetic picture or a screen capture")
        try:
            return SyntheticSource(source.pattern.value)
        except (InvalidScreenCastOption, AttributeError) as error:
            raise InvalidStreamConfiguration(
                f"the synthetic source cannot be encoded: {type(error).__name__}"
            ) from None

    def _new_directory(self) -> Path:
        try:
            directory = self._make_workdir()
        except OSError as error:
            raise StreamStartError(
                f"the live output directory could not be created: {type(error).__name__}"
            ) from None
        try:
            directory.chmod(0o700)
        except OSError as error:
            shutil.rmtree(directory, ignore_errors=True)
            raise StreamStartError(
                f"the live output directory could not be made private: {type(error).__name__}"
            ) from None
        return directory

    @staticmethod
    def _output(directory: Path, choice: EncoderChoice) -> LiveOutput:
        return LiveOutput(directory, directory / HLS_PLAYLIST, choice.plan, choice.fallback_reason)


def _listed_segments(playlist: Path) -> int:
    """How many segments the playlist lists; 0 while FFmpeg has not written it."""
    try:
        text = playlist.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.startswith("#EXTINF"))
