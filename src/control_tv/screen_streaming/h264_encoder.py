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
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from control_tv.screen_streaming.model import (
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
)
from control_tv.screencast.process import ManagedProcess, ProcessRunner, stop_process

STDERR_NAME = "ffmpeg-stderr.log"
_MAX_STOP_GRACE = 60.0
_DEFAULT_DEVICE = VaapiDevice("/dev/dri/renderD128")
_DEFAULT_SEGMENTING = HlsSegmenting()


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
    ) -> None:
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
        synthetic = self._d0_source(source)
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
                synthetic,
                frame,
                choice.plan,
                directory,
                settings.segmenting,
                settings.max_session_seconds,
            )
            process = self._runner.start(argv, stderr_path=directory / STDERR_NAME)
            # Registered inside the guard: an interruption before this completes is handled
            # below, so a started process is never left untracked.
            self._register(handle, _LiveSession(output, process))
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

    def _register(self, handle: str, session: _LiveSession) -> None:
        with self._lock:
            self._sessions[handle] = session
            self._released.discard(handle)

    def _abandon_start(
        self, handle: str, output: LiveOutput, process: ManagedProcess | None
    ) -> None:
        """Undo a failed or interrupted start. A process that started is stopped; when it cannot
        be (survives SIGKILL, signal error, another interruption) it is registered instead, so it
        stays in `unreleased` for `stop()`/`close()` with its output: never untracked."""
        with self._lock:
            session = self._sessions.get(handle)
            registered = session is not None and session.process is process
        if process is not None:
            try:
                status = stop_process(process, float(self._settings.stop_grace_seconds))
            except BaseException:
                if not registered:
                    self._register(handle, _LiveSession(output, process))
                raise
            if status is None:
                if not registered:
                    self._register(handle, _LiveSession(output, process))
                return
        with self._lock:
            if registered:
                del self._sessions[handle]
        shutil.rmtree(output.directory, ignore_errors=True)

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

    @staticmethod
    def _d0_source(source: VideoSource) -> SyntheticSource:
        if type(source) is not SyntheticVideoSource:
            raise InvalidStreamConfiguration(
                "only the synthetic source can be encoded: no capture adapter exists yet"
            )
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
