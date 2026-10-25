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
    DELETE = "KEYCODE_DEL"  # the keyboard's Backspace, in the TV's active text field
    ENTER = "KEYCODE_ENTER"  # the keyboard's Enter: submits the TV's active text field
    # The rest of a Google TV remote: as the TV's own remote sends them, each once.
    DIGIT_0 = "KEYCODE_0"
    DIGIT_1 = "KEYCODE_1"
    DIGIT_2 = "KEYCODE_2"
    DIGIT_3 = "KEYCODE_3"
    DIGIT_4 = "KEYCODE_4"
    DIGIT_5 = "KEYCODE_5"
    DIGIT_6 = "KEYCODE_6"
    DIGIT_7 = "KEYCODE_7"
    DIGIT_8 = "KEYCODE_8"
    DIGIT_9 = "KEYCODE_9"
    INFO = "KEYCODE_INFO"
    GUIDE = "KEYCODE_GUIDE"
    TV_INPUT = "KEYCODE_TV_INPUT"
    CAPTIONS = "KEYCODE_CAPTIONS"  # subtitles
    AUDIO_TRACK = "KEYCODE_MEDIA_AUDIO_TRACK"
    SETTINGS = "KEYCODE_SETTINGS"
    CHANNEL_UP = "KEYCODE_CHANNEL_UP"
    CHANNEL_DOWN = "KEYCODE_CHANNEL_DOWN"
    RED = "KEYCODE_PROG_RED"
    GREEN = "KEYCODE_PROG_GREEN"
    YELLOW = "KEYCODE_PROG_YELLOW"
    BLUE = "KEYCODE_PROG_BLUE"
    PLAY_PAUSE = "KEYCODE_MEDIA_PLAY_PAUSE"
    STOP = "KEYCODE_MEDIA_STOP"
    REWIND = "KEYCODE_MEDIA_REWIND"
    FAST_FORWARD = "KEYCODE_MEDIA_FAST_FORWARD"
    PREVIOUS = "KEYCODE_MEDIA_PREVIOUS"
    NEXT = "KEYCODE_MEDIA_NEXT"
    RECORD = "KEYCODE_MEDIA_RECORD"
    ASSISTANT = "KEYCODE_ASSIST"  # opens the TV's Google Assistant


VOLUME_KEYS = frozenset({RemoteKey.VOLUME_UP, RemoteKey.VOLUME_DOWN, RemoteKey.VOLUME_MUTE})


@dataclass(frozen=True, slots=True)
class AudibleVolume:
    """The TV's own volume, as the TV reported it: the sound the owner actually hears."""

    level: int
    maximum: int
    muted: bool


@dataclass(frozen=True, slots=True)
class RemoteObservation:
    """What the TV reported on its remote channel; `None` means it did not say.

    `current_app` is the package name of the app in front on the TV (for example
    `com.google.android.youtube.tv`), as the TV reported it.
    """

    is_on: bool | None
    volume: AudibleVolume | None
    current_app: str | None = None


@dataclass(frozen=True, slots=True)
class KeyPress:
    """A key that was sent once, with what the TV reported just before and just after."""

    key: RemoteKey
    before: RemoteObservation
    after: RemoteObservation


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteKeyResult:
    """A key that was sent, and whether the TV's own report shows its effect.

    `CONFIRMED` means the TV reported the expected change of its audible volume, mute or power
    state; it is the TV's report, not a person hearing the sound or seeing the picture. Keys
    whose effect the TV does not report (navigation) are `NOT_CHECKED`.
    """

    key: RemoteKey
    device_id: DeviceId
    confirmation: Confirmation
    before: RemoteObservation
    after: RemoteObservation
    detail: str


MAX_TEXT_LENGTH = 256


@dataclass(frozen=True, slots=True)
class TextEntry:
    """A guarded attempt to send text once to the TV's active text field.

    `field_reported`: whether the TV reported an active field and the text was sent.
    The text itself is never kept here: it may be a password.
    """

    length: int
    field_reported: bool
    observed: RemoteObservation


@dataclass(frozen=True, slots=True, kw_only=True)
class RemoteTextResult:
    """Guarded text entry into the TV's active field. The TV does not echo text back, so a
    send's effect is never confirmed by the TV: only the person in front of it can check."""

    device_id: DeviceId
    confirmation: Confirmation
    detail: str
    field_reported: bool
    observed: RemoteObservation


@dataclass(frozen=True, slots=True)
class TvApp:
    """An app installed on the TV that its home screen can launch.

    `package` is the Android package name, as the TV listed it; `name` is a display name
    control-TV knows for that package, or `None` (the TV's own labels are not available).
    """

    package: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class AppLaunch:
    """An app launch that was sent once, and the app the TV reported in front afterwards
    (`None` when the TV did not say)."""

    package: str
    front_app: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AppLaunchResult:
    """An app launch, and whether the TV reported that app in front afterwards."""

    package: str
    device_id: DeviceId
    confirmation: Confirmation
    detail: str
    front_app: str | None
