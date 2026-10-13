"""The raw Cast receiver replies, checked and read without PyChromecast's defaults.

PyChromecast fills a field the receiver omitted with a plausible default (a volume of 1, not
muted, a position of 0, ...). These functions read the receiver's own reply instead: a field
that is omitted, null, or not what the Cast protocol defines is reported as `None` (not
reported), never as a plausible value and never as a crash. They are pure: no network, no
PyChromecast object.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from control_tv.domain import (
    DeviceId,
    DeviceUnavailableError,
    MetadataType,
    StreamType,
    VolumeControlType,
)


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an integer too large for a float
        return False


def _non_negative_number(value: object) -> bool:
    return _finite_number(value) and isinstance(value, int | float) and value >= 0


def _non_negative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _text(value: object) -> bool:
    return isinstance(value, str)


def _unusable_to_null(
    fields: dict[str, object], checks: tuple[tuple[str, Callable[[object], bool]], ...]
) -> dict[str, object]:
    """A copy of `fields` where each checked value that is present but unusable is null."""
    checked = dict(fields)
    for key, usable in checks:
        if checked.get(key) is not None and not usable(checked[key]):
            checked[key] = None
    return checked


def _checked_media_block(block: object) -> dict[str, object] | None:
    if not isinstance(block, dict):
        return None
    checked = _unusable_to_null(
        block,
        (
            ("contentId", _text),
            ("contentType", _text),
            ("streamType", _text),
            ("duration", _non_negative_number),
            ("metadata", lambda value: isinstance(value, dict)),
        ),
    )
    metadata = checked.get("metadata")
    if isinstance(metadata, dict):
        checked["metadata"] = _unusable_to_null(
            metadata,
            (("title", _text), ("artist", _text), ("metadataType", _non_negative_integer)),
        )
    return checked


_STREAM_TYPES = {"BUFFERED": StreamType.BUFFERED, "LIVE": StreamType.LIVE}
_METADATA_TYPES = {
    0: MetadataType.GENERIC,
    1: MetadataType.MOVIE,
    2: MetadataType.TV_SHOW,
    3: MetadataType.MUSIC_TRACK,
    4: MetadataType.PHOTO,
    5: MetadataType.AUDIOBOOK_CHAPTER,
}


def reported_details(
    entry: dict[str, object],
) -> tuple[str | None, StreamType | None, MetadataType | None]:
    """Artist, stream type and metadata type exactly as the checked entry reports them.

    They are read here rather than from PyChromecast, whose defaults would invent a value for
    an omitted field (its placeholder `stream_type`, an empty metadata dict). An omitted,
    null, blank or unrecognized value is not reported (`None`): Cast's "NONE" stream type
    and metadata types beyond the known ones name nothing the product can use.
    """
    media = entry.get("media")
    media = media if isinstance(media, dict) else {}
    metadata = media.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    artist = metadata.get("artist")
    stream = media.get("streamType")
    kind = metadata.get("metadataType")
    return (
        artist.strip() if isinstance(artist, str) and artist.strip() else None,
        _STREAM_TYPES.get(stream) if isinstance(stream, str) else None,
        _METADATA_TYPES.get(kind) if isinstance(kind, int) and not isinstance(kind, bool) else None,
    )


def checked_media_entry(reply: dict[str, object], device_id: DeviceId) -> dict[str, object] | None:
    """The first media session of a MEDIA_STATUS reply, checked before PyChromecast parses it.

    A reply that is not shaped like a media status (no status list, an entry that is not an
    object) fails the read. Inside a well-shaped entry, a field whose value cannot be what the
    Cast protocol defines (wrong JSON type, negative or non-finite number) is set to null, so
    it is treated as not reported (`None`), exactly like an omitted field: never as a plausible
    value and
    never as a crash. The media block may come from `extendedStatus`, as PyChromecast reads it;
    the media-channel volume is not mapped and is dropped when it is not an object.
    Returns None when the reply reports no media session (an empty status list).
    """
    entries = reply.get("status")
    if not isinstance(entries, list) or (entries and not isinstance(entries[0], dict)):
        raise DeviceUnavailableError(
            f"device {device_id} sent a malformed media status", device_id=device_id
        )
    if not entries:
        return None
    entry = _unusable_to_null(
        entries[0],
        (
            ("currentTime", _non_negative_number),
            ("playbackRate", _finite_number),
            ("playerState", _text),
            ("supportedMediaCommands", _non_negative_integer),
            ("mediaSessionId", _non_negative_integer),
            ("currentItemId", _non_negative_integer),
        ),
    )
    media = _checked_media_block(entry.get("media"))
    if media is None:
        extended = entry.get("extendedStatus")
        media = _checked_media_block(extended.get("media")) if isinstance(extended, dict) else None
    entry.pop("extendedStatus", None)
    entry.pop("media", None)
    if media is not None:
        entry["media"] = media
    if not isinstance(entry.get("volume", {}), dict):
        del entry["volume"]
    return entry


_VOLUME_CONTROL_TYPES = {
    "attenuation": VolumeControlType.ATTENUATION,
    "fixed": VolumeControlType.FIXED,
    "master": VolumeControlType.MASTER,
}


def _reported_volume(reply: dict[str, object] | None) -> dict[str, object]:
    """The `status.volume` object of a raw receiver status reply, or an empty one."""
    status = reply.get("status") if isinstance(reply, dict) else None
    volume = status.get("volume") if isinstance(status, dict) else None
    return volume if isinstance(volume, dict) else {}


def reported_volume_control_type(reply: dict[str, object] | None) -> VolumeControlType | None:
    """The volume control type exactly as the receiver status reply reports it.

    Read from the raw reply because PyChromecast's `CastStatus` substitutes "attenuation"
    when the receiver omits `controlType`; an omitted or unrecognized value is not reported.
    """
    kind = _reported_volume(reply).get("controlType")
    return _VOLUME_CONTROL_TYPES.get(kind) if isinstance(kind, str) else None


def reported_volume_level(reply: dict[str, object] | None) -> float | None:
    """The volume level exactly as the receiver status reply reports it.

    PyChromecast's `CastStatus` substitutes 1.0 when the receiver omits `level`, which would
    show a full volume nobody reported and could confirm a request for 100%. An omitted or
    null level, a boolean, a non-number and a number outside 0-1 (or not finite) are not reported.
    """
    level = _reported_volume(reply).get("level")
    if isinstance(level, bool) or not isinstance(level, int | float):
        return None
    if not (0 <= level <= 1):  # also false for NaN
        return None
    return float(level)


def reported_muted(reply: dict[str, object] | None) -> bool | None:
    """The mute state exactly as the receiver status reply reports it.

    PyChromecast's `CastStatus` substitutes False when the receiver omits `muted`, which
    would show "not muted" nobody reported and could confirm an unmute. Anything but an
    explicit JSON boolean is not reported.
    """
    muted = _reported_volume(reply).get("muted")
    return muted if isinstance(muted, bool) else None


def reported_standby(reply: dict[str, object] | None) -> bool | None:
    """The standby state exactly as the receiver status reply reports it (`status.isStandBy`).

    PyChromecast's `CastStatus` substitutes True when a video device (cast type "cast") omits
    `isStandBy`, which would show a standby nobody reported. Anything but an explicit JSON
    boolean is not reported.
    """
    status = reply.get("status") if isinstance(reply, dict) else None
    standby = status.get("isStandBy") if isinstance(status, dict) else None
    return standby if isinstance(standby, bool) else None
