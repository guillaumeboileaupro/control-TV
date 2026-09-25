"""Typed device, connection and TV-state models.

These are plain immutable values with no dependency on a Cast library, a UI or MCP.
A `DeviceStatus` is always something that was *observed on the TV*; what a caller merely
asked for is never stored in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType
from urllib.parse import urlsplit

from control_tv.domain.errors import InvalidArgumentError

DeviceId = NewType("DeviceId", str)
"""Stable device identifier (for Cast, the device UUID). Never a display name."""


class DeviceKind(StrEnum):
    CAST = "cast"
    AUDIO = "audio"
    GROUP = "group"
    UNKNOWN = "unknown"


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class PlaybackState(StrEnum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    BUFFERING = "buffering"
    UNKNOWN = "unknown"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidArgumentError(message)


_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$%&+.^_~-]*\Z")
_MEDIA_TYPE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$%&+.^_~-]*/[A-Za-z0-9][A-Za-z0-9!#$%&+.^_~-]*\Z"
)


def _valid_media_url(value: str) -> bool:
    if value != value.strip() or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Invalid or out-of-range ports raise ValueError.
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and parsed.hostname is not None


def _valid_content_type(value: str) -> bool:
    if value != value.strip() or any(character in value for character in "\r\n\0"):
        return False
    media_type, separator, parameters = value.partition(";")
    if _MEDIA_TYPE_RE.fullmatch(media_type) is None:
        return False
    if not separator:
        return True
    for part in parameters.split(";"):
        name, equals, value = part.partition("=")
        if not equals or _TOKEN_RE.fullmatch(name.strip()) is None or not value.strip():
            return False
    return True


@dataclass(frozen=True, slots=True, kw_only=True)
class Device:
    """A discovered device, identified by `id`; `friendly_name` is display-only."""

    id: DeviceId
    friendly_name: str
    host: str
    port: int
    kind: DeviceKind = DeviceKind.UNKNOWN
    model_name: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.id.strip()), "device id must not be blank")
        _require(bool(self.host.strip()), "device host must not be blank")
        _require(0 < self.port < 65536, f"device port out of range: {self.port}")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReceiverStatus:
    """Receiver-level state reported by the TV. `None` means the TV did not report it."""

    app_id: str | None = None
    app_name: str | None = None
    volume_level: float | None = None
    muted: bool | None = None
    standby: bool | None = None

    def __post_init__(self) -> None:
        if self.volume_level is not None:
            _require(
                0.0 <= self.volume_level <= 1.0,
                f"volume level must be within 0.0-1.0: {self.volume_level}",
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaStatus:
    """Media session state reported by the TV. `None` means unknown, not false."""

    playback_state: PlaybackState = PlaybackState.UNKNOWN
    content_id: str | None = None
    content_type: str | None = None
    title: str | None = None
    position_seconds: float | None = None
    duration_seconds: float | None = None
    supports_seek: bool | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("position", self.position_seconds),
            ("duration", self.duration_seconds),
        ):
            _require(value is None or value >= 0, f"media {name} must not be negative: {value}")


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceStatus:
    """A snapshot of one device as observed on the TV at `observed_at`."""

    device_id: DeviceId
    connection: ConnectionState
    observed_at: datetime
    receiver: ReceiverStatus | None = None
    media: MediaStatus | None = None

    def __post_init__(self) -> None:
        _require(
            self.observed_at.tzinfo is not None,
            "observed_at must be timezone-aware so snapshots can be compared safely",
        )
        _require(
            self.connection is ConnectionState.CONNECTED
            or (self.receiver is None and self.media is None),
            "receiver and media state can only be observed on a connected device",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaRequest:
    """A validated network media target ready for the Cast transport."""

    url: str
    content_type: str
    title: str | None = None

    def __post_init__(self) -> None:
        _require(
            _valid_media_url(self.url),
            "media url must be an absolute HTTP(S) URL without whitespace",
        )
        _require(
            _valid_content_type(self.content_type),
            "media content type must be a valid MIME media type",
        )
        _require(
            self.title is None
            or (bool(self.title.strip()) and not any(c in self.title for c in "\r\n\0")),
            "media title must be non-blank and contain no control characters",
        )
