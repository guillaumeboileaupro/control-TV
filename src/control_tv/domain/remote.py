"""The vocabulary of a TV's own remote-control channel (the Android TV Remote protocol).

A Google TV whose Cast volume is fixed is heard at the volume the TV itself sets. Its own
remote channel reports that audible volume and mute, and accepts a small set of keys. These
types carry what was sent and what the TV reported, kept apart as for Cast commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from control_tv.domain.models import DeviceId
from control_tv.domain.results import Confirmation


class RemoteKey(StrEnum):
    """The keys this application may send, by their Android key code name."""

    VOLUME_UP = "KEYCODE_VOLUME_UP"
    VOLUME_DOWN = "KEYCODE_VOLUME_DOWN"
    VOLUME_MUTE = "KEYCODE_VOLUME_MUTE"  # the speaker; KEYCODE_MUTE is the microphone
    POWER = "KEYCODE_POWER"
    DPAD_UP = "KEYCODE_DPAD_UP"
    DPAD_DOWN = "KEYCODE_DPAD_DOWN"
    DPAD_LEFT = "KEYCODE_DPAD_LEFT"
    DPAD_RIGHT = "KEYCODE_DPAD_RIGHT"
    DPAD_CENTER = "KEYCODE_DPAD_CENTER"
    BACK = "KEYCODE_BACK"
    HOME = "KEYCODE_HOME"


VOLUME_KEYS = frozenset({RemoteKey.VOLUME_UP, RemoteKey.VOLUME_DOWN, RemoteKey.VOLUME_MUTE})


@dataclass(frozen=True, slots=True)
class AudibleVolume:
    """The TV's own volume, as the TV reported it: the sound the owner actually hears."""

    level: int
    maximum: int
    muted: bool


@dataclass(frozen=True, slots=True)
class RemoteObservation:
    """What the TV reported on its remote channel; `None` means it did not say."""

    is_on: bool | None
    volume: AudibleVolume | None


@dataclass(frozen=True, slots=True)
class KeyPress:
    """A key that was sent once, with what the TV reported just before and just after."""

    key: RemoteKey
    before: RemoteObservation
    after: RemoteObservation


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteKeyResult:
    """A key that was sent, and whether the TV's own report shows its effect.

    `CONFIRMED` means the TV reported the expected change of its audible volume or mute; it
    is the TV's report, not a person hearing the sound. Keys whose effect the TV does not
    report (power, navigation) are `NOT_CHECKED`.
    """

    key: RemoteKey
    device_id: DeviceId
    confirmation: Confirmation
    before: RemoteObservation
    after: RemoteObservation
    detail: str
