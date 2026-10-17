"""Typed values for the isolated D1 receiver/publication prototype."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

MAX_SESSION_SECONDS = 3600.0
_MAX_DIMENSION = 4096
_MAX_FPS = 60


class ScreenStreamError(Exception):
    """A stream session operation failed with a caller-safe explanation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidStreamConfiguration(ScreenStreamError):
    """A D1 prototype value is invalid or outside its resource bounds."""


class StreamStartError(ScreenStreamError):
    """A source, encoder, publisher or receiver could not be started."""


class StreamCleanupError(ScreenStreamError):
    """One or more owned resources could not be stopped."""


class SessionState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class PublicationKind(StrEnum):
    HLS = "hls"
    CAST_STREAMING = "cast_streaming"
    SUNSHINE = "sunshine"


class SyntheticPattern(StrEnum):
    TESTSRC2 = "testsrc2"
    SMPTE_BARS = "smptebars"


def _bounded_int(name: str, value: int, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise InvalidStreamConfiguration(
            f"{name} must be an integer from {low} to {high}: {value!r}"
        )


def _nonempty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidStreamConfiguration(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class EncodingProfile:
    """Resource bounds shared by future D0 encoder and D1 publisher adapters."""

    width: int = 1920
    height: int = 1080
    fps: int = 30

    def __post_init__(self) -> None:
        _bounded_int("width", self.width, 16, _MAX_DIMENSION)
        _bounded_int("height", self.height, 16, _MAX_DIMENSION)
        _bounded_int("fps", self.fps, 1, _MAX_FPS)
        if self.width % 2 or self.height % 2:
            raise InvalidStreamConfiguration(
                f"H.264 needs even dimensions: {self.width}x{self.height}"
            )


@dataclass(frozen=True)
class SyntheticVideoSource:
    """A safe test-picture specification; it contains no FFmpeg arguments."""

    pattern: SyntheticPattern = SyntheticPattern.TESTSRC2

    def __post_init__(self) -> None:
        if not isinstance(self.pattern, SyntheticPattern):
            raise InvalidStreamConfiguration(f"unsupported synthetic pattern: {self.pattern!r}")


@dataclass(frozen=True)
class DisplaySelection:
    """Future capture input selected explicitly by the user; unused by this prototype."""

    output_id: str

    def __post_init__(self) -> None:
        _nonempty("output_id", self.output_id)


@dataclass(frozen=True)
class CaptureHandle:
    value: str

    def __post_init__(self) -> None:
        _nonempty("capture handle", self.value)


@dataclass(frozen=True)
class CapturedVideo:
    handle: CaptureHandle


VideoSource = SyntheticVideoSource | CapturedVideo


@dataclass(frozen=True)
class EncodedStream:
    """Opaque handle owned by a ScreenEncoder implementation."""

    handle: str

    def __post_init__(self) -> None:
        _nonempty("encoded stream handle", self.handle)


@dataclass(frozen=True)
class PublicationConfig:
    """Publication intent. No adapter exists in this spike, so this opens no listener."""

    kind: PublicationKind
    max_session_seconds: float = 120.0

    def __post_init__(self) -> None:
        seconds = self.max_session_seconds
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, int | float)
            or not math.isfinite(seconds)
            or not 0 < seconds <= MAX_SESSION_SECONDS
        ):
            raise InvalidStreamConfiguration(
                "max_session_seconds must be a finite number from more than 0 "
                f"to {MAX_SESSION_SECONDS:g}: {seconds!r}"
            )
        if not isinstance(self.kind, PublicationKind):
            raise InvalidStreamConfiguration(f"unsupported publication kind: {self.kind!r}")


@dataclass(frozen=True)
class PublishedStream:
    """Receiver-facing endpoint returned by a future publisher adapter."""

    uri: str

    def __post_init__(self) -> None:
        _nonempty("published stream URI", self.uri)
        parsed = urlsplit(self.uri)
        if parsed.scheme not in {"http", "https", "cast-streaming", "moonlight"}:
            raise InvalidStreamConfiguration(f"unsupported stream URI scheme: {parsed.scheme!r}")
        if not parsed.netloc or parsed.username is not None or parsed.password is not None:
            raise InvalidStreamConfiguration("stream URI must have a host and no user information")
        if parsed.fragment:
            raise InvalidStreamConfiguration("stream URI must not contain a fragment")


@dataclass(frozen=True)
class ReceiverTarget:
    """Opaque receiver identity, not a network address."""

    device_id: str

    def __post_init__(self) -> None:
        _nonempty("receiver device_id", self.device_id)


@dataclass(frozen=True)
class ReceiverSession:
    handle: str

    def __post_init__(self) -> None:
        _nonempty("receiver session handle", self.handle)
