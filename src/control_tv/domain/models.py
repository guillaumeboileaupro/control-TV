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


class VolumeControlType(StrEnum):
    """How the receiver's volume can be controlled (Cast `volume.controlType`)."""

    ATTENUATION = "attenuation"
    FIXED = "fixed"
    MASTER = "master"


class PlaybackState(StrEnum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    BUFFERING = "buffering"
    UNKNOWN = "unknown"


class StreamType(StrEnum):
    """How the media is delivered, as the receiver reported it (Cast `streamType`)."""

    BUFFERED = "buffered"
    LIVE = "live"


class MetadataType(StrEnum):
    """What kind of media the receiver says it is (Cast `metadataType`)."""

    GENERIC = "generic"
    MOVIE = "movie"
    TV_SHOW = "tv_show"
    MUSIC_TRACK = "music_track"
    PHOTO = "photo"
    AUDIOBOOK_CHAPTER = "audiobook_chapter"


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
    if _MEDIA_TYPE_RE.fullmatch(media_type.rstrip()) is None:
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
    android_tv_remote: bool | None = None
    """Whether the device advertises the Android TV Remote service (protocol v2), the channel
    its own remote uses for keys such as volume and power: True or False when discovery looked
    for it, None when unknown (not looked for, the look failed, or a Cast group). control-TV
    does not use that channel; it only reports it."""

    def __post_init__(self) -> None:
        _require(bool(self.id.strip()), "device id must not be blank")
        _require(bool(self.host.strip()), "device host must not be blank")
        _require(0 < self.port < 65536, f"device port out of range: {self.port}")
        _require(
            self.android_tv_remote is None or isinstance(self.android_tv_remote, bool),
            f"android_tv_remote must be a boolean or None: {self.android_tv_remote!r}",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReceiverStatus:
    """Receiver-level state reported by the TV. `None` means the TV did not report it."""

    app_id: str | None = None
    app_name: str | None = None
    volume_level: float | None = None
    muted: bool | None = None
    standby: bool | None = None
    volume_control_type: VolumeControlType | None = None
    """Reported by the receiver; None means it did not say (never assumed adjustable or fixed)."""

    def __post_init__(self) -> None:
        _require(
            self.volume_control_type is None
            or isinstance(self.volume_control_type, VolumeControlType),
            f"invalid volume control type: {self.volume_control_type!r}",
        )
        _require(
            self.standby is None or isinstance(self.standby, bool),
            f"standby must be a boolean or None: {self.standby!r}",
        )
        if self.volume_level is not None:
            # A boolean is never a level, and NaN fails the range check.
            _require(
                isinstance(self.volume_level, int | float)
                and not isinstance(self.volume_level, bool)
                and 0.0 <= self.volume_level <= 1.0,
                f"volume level must be a number within 0.0-1.0: {self.volume_level!r}",
            )
        _require(
            self.muted is None or isinstance(self.muted, bool),
            f"muted must be a boolean or None: {self.muted!r}",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaStatus:
    """Media session state reported by the TV. `None` means unknown, not false."""

    playback_state: PlaybackState = PlaybackState.UNKNOWN
    content_id: str | None = None
    media_session_id: int | None = None
    """The receiver's mediaSessionId: it names a playback session, which can hold several
    queued items, so it narrows `content_id` but never identifies a media item on its own."""
    current_item_id: int | None = None
    """The receiver's currentItemId: the active item of the session's queue. It is unique only
    within that queue, so it narrows `content_id` and the session but identifies nothing alone."""
    content_type: str | None = None
    title: str | None = None
    artist: str | None = None
    """Who the receiver names as the media's artist (for a video, often the channel); never
    blank: an absent, null or blank value is unknown."""
    stream_type: StreamType | None = None
    metadata_type: MetadataType | None = None
    position_seconds: float | None = None
    duration_seconds: float | None = None
    supports_seek: bool | None = None
    supports_pause: bool | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("position", self.position_seconds),
            ("duration", self.duration_seconds),
        ):
            _require(value is None or value >= 0, f"media {name} must not be negative: {value}")
        for name, identifier in (
            ("session id", self.media_session_id),
            ("queue item id", self.current_item_id),
        ):
            _require(
                identifier is None
                or (
                    isinstance(identifier, int)
                    and not isinstance(identifier, bool)
                    and identifier >= 0
                ),
                f"media {name} must be a non-negative integer: {identifier!r}",
            )
        _require(
            self.artist is None or (isinstance(self.artist, str) and bool(self.artist.strip())),
            f"media artist must be None or a non-blank string: {self.artist!r}",
        )
        for name, flag in (
            ("supports_seek", self.supports_seek),
            ("supports_pause", self.supports_pause),
        ):
            _require(
                flag is None or isinstance(flag, bool),
                f"media {name} must be a boolean or None: {flag!r}",
            )
        _require(
            self.stream_type is None or isinstance(self.stream_type, StreamType),
            f"media stream type must be a StreamType or None: {self.stream_type!r}",
        )
        _require(
            self.metadata_type is None or isinstance(self.metadata_type, MetadataType),
            f"media metadata type must be a MetadataType or None: {self.metadata_type!r}",
        )


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
