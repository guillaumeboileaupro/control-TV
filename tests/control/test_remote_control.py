"""The TV's own remote channel through `ControlService` and the bridge.

Fakes only: `FakeTransport` for Cast discovery and `FakeRemote` for the remote channel. The
rules pinned: the TV is reached at its discovered address, a Cast group or a TV without the
channel is refused before anything is sent, each key is sent once, and "confirmed" needs
the TV's own report of the expected volume or mute change.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from control_tv.bridge import REMOTE_KEYS, dispatch
from control_tv.domain import (
    AppLaunch,
    AudibleVolume,
    CommandRejectedError,
    Confirmation,
    DebuggingOffError,
    Device,
    DeviceId,
    DeviceKind,
    DeviceNotFoundError,
    InvalidArgumentError,
    KeyPress,
    OperationTimeoutError,
    PairingRequiredError,
    RemoteKey,
    RemoteObservation,
    TextEntry,
    TvApp,
    UnsupportedOperationError,
)
from control_tv.ports import TvRemote
from control_tv.service import ControlService
from fakes import DEVICE_ID, FakeTransport

HOST = "192.168.1.20"  # FakeTransport's discovered address


def power(is_on: bool | None) -> RemoteObservation:
    return RemoteObservation(is_on=is_on, volume=None)


def volume(level: int, muted: bool = False) -> RemoteObservation:
    return RemoteObservation(
        is_on=True, volume=AudibleVolume(level=level, maximum=100, muted=muted)
    )


@dataclass
class FakeRemote:
    """In-memory `TvRemote`: `after` is what the TV reports once the key was sent."""

    before: RemoteObservation = field(default_factory=lambda: volume(10))
    after: RemoteObservation | None = None
    error: Exception | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error

    def start_pairing(self, host: str) -> None:
        self.calls.append(("start_pairing", host))
        self._maybe_fail()

    def finish_pairing(self, host: str, code: str) -> None:
        self.calls.append(("finish_pairing", host, code))
        self._maybe_fail()

    def observe(self, host: str) -> RemoteObservation:
        self.calls.append(("observe", host))
        self._maybe_fail()
        return self.before

    def press(self, host: str, key: RemoteKey) -> KeyPress:
        self.calls.append(("press", host, key.value))
        self._maybe_fail()
        return KeyPress(key=key, before=self.before, after=self.after or self.before)

    field_reported: bool = True

    def type_text(self, host: str, text: str) -> TextEntry:
        self.calls.append(("type_text", host, text))
        self._maybe_fail()
        return TextEntry(length=len(text), field_reported=self.field_reported, observed=self.before)

    front: str | None = None

    def front_app(self, host: str, package: str) -> str | None:
        self.calls.append(("front_app", host, package))
        self._maybe_fail()
        return self.front


class GroupTransport(FakeTransport):
    def discover(self, *, timeout: float) -> Sequence[Device]:
        return [
            dataclasses.replace(d, kind=DeviceKind.GROUP) for d in super().discover(timeout=timeout)
        ]


class NoRemoteTransport(FakeTransport):
    def discover(self, *, timeout: float) -> Sequence[Device]:
        return [
            dataclasses.replace(d, android_tv_remote=False)
            for d in super().discover(timeout=timeout)
        ]


def discovered(remote: FakeRemote | None, transport: FakeTransport | None = None) -> ControlService:
    service = ControlService(transport or FakeTransport(), remote=remote)
    service.discover_devices(timeout=1.0)
    return service


def test_the_fake_satisfies_the_port() -> None:
    remote: TvRemote = FakeRemote()
    assert remote is not None


# Reaching the TV -----------------------------------------------------------------------------


def test_the_tv_is_reached_at_its_discovered_address() -> None:
    remote = FakeRemote()

    assert discovered(remote).get_remote_status(DEVICE_ID) == volume(10)
    assert remote.calls == [("observe", HOST)]


def test_a_tv_not_found_by_discovery_is_not_guessed() -> None:
    remote = FakeRemote()
    service = ControlService(FakeTransport(), remote=remote)

    with pytest.raises(DeviceNotFoundError):
        service.press_remote_key(DEVICE_ID, RemoteKey.VOLUME_UP)
    with pytest.raises(DeviceNotFoundError):
        discovered(remote).press_remote_key(DeviceId("uuid-other"), RemoteKey.VOLUME_UP)

    assert remote.calls == []


@pytest.mark.parametrize("transport", [GroupTransport(), NoRemoteTransport()])
def test_a_cast_group_or_a_tv_without_the_channel_is_refused_before_sending(
    transport: FakeTransport,
) -> None:
    remote = FakeRemote()

    with pytest.raises(UnsupportedOperationError):
        discovered(remote, transport).press_remote_key(DEVICE_ID, RemoteKey.VOLUME_UP)

    assert remote.calls == []


def test_without_a_remote_channel_the_service_says_so() -> None:
    with pytest.raises(UnsupportedOperationError):
        discovered(None).get_remote_status(DEVICE_ID)


# Keys and confirmation -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "before", "after", "expected"),
    [
        (RemoteKey.VOLUME_UP, volume(10), volume(11), Confirmation.CONFIRMED),
        (RemoteKey.VOLUME_UP, volume(100), volume(100), Confirmation.UNCONFIRMED),
        (RemoteKey.VOLUME_UP, volume(10), volume(9), Confirmation.UNCONFIRMED),
        (RemoteKey.VOLUME_DOWN, volume(10), volume(9), Confirmation.CONFIRMED),
        (RemoteKey.VOLUME_DOWN, volume(0), volume(0), Confirmation.UNCONFIRMED),
        (RemoteKey.VOLUME_MUTE, volume(10), volume(10, muted=True), Confirmation.CONFIRMED),
        (RemoteKey.VOLUME_MUTE, volume(10, muted=True), volume(10), Confirmation.CONFIRMED),
        (RemoteKey.VOLUME_MUTE, volume(10), volume(10), Confirmation.UNCONFIRMED),
        (
            RemoteKey.VOLUME_UP,
            RemoteObservation(is_on=True, volume=None),
            volume(11),
            Confirmation.UNCONFIRMED,
        ),
        (RemoteKey.POWER, power(False), power(True), Confirmation.CONFIRMED),
        (RemoteKey.POWER, power(True), power(False), Confirmation.CONFIRMED),
        (RemoteKey.POWER, power(False), power(False), Confirmation.UNCONFIRMED),
        (RemoteKey.POWER, power(None), power(True), Confirmation.UNCONFIRMED),
        (RemoteKey.POWER, power(False), power(None), Confirmation.UNCONFIRMED),
        (RemoteKey.DPAD_CENTER, volume(10), volume(10), Confirmation.NOT_CHECKED),
    ],
)
def test_a_key_is_confirmed_only_by_the_tvs_report_of_its_effect(
    key: RemoteKey,
    before: RemoteObservation,
    after: RemoteObservation,
    expected: Confirmation,
) -> None:
    remote = FakeRemote(before=before, after=after)

    result = discovered(remote).press_remote_key(DEVICE_ID, key)

    assert result.confirmation is expected
    assert (result.before, result.after) == (before, after)
    assert remote.calls == [("press", HOST, key.value)]


def test_the_detail_states_what_the_tv_reported() -> None:
    up = discovered(FakeRemote(after=volume(11))).press_remote_key(DEVICE_ID, RemoteKey.VOLUME_UP)
    mute = discovered(FakeRemote(after=volume(10))).press_remote_key(
        DEVICE_ID, RemoteKey.VOLUME_MUTE
    )

    assert up.detail == "the TV reports volume 11/100"
    assert mute.detail == "sent; the TV still reports its sound not muted"


def test_power_is_confirmed_only_by_the_tvs_power_report() -> None:
    def pressed(before: bool | None, after: bool | None) -> str:
        remote = FakeRemote(before=power(before), after=power(after))
        return discovered(remote).press_remote_key(DEVICE_ID, RemoteKey.POWER).detail

    assert pressed(False, True) == "the TV reports it is on"
    assert pressed(True, False) == "the TV reports it is off"
    assert pressed(False, False) == "sent; the TV still reports it is off"
    assert pressed(True, None) == "sent; the TV did not report whether it is on"


def test_a_tv_that_does_not_report_its_sound_is_named_for_the_key_pressed() -> None:
    silent = RemoteObservation(is_on=True, volume=None)

    up = discovered(FakeRemote(before=silent)).press_remote_key(DEVICE_ID, RemoteKey.VOLUME_UP)
    mute = discovered(FakeRemote(before=silent)).press_remote_key(DEVICE_ID, RemoteKey.VOLUME_MUTE)

    assert up.detail == "sent; the TV did not report its volume"
    assert mute.detail == "sent; the TV did not report whether its sound is muted"
    assert up.confirmation is mute.confirmation is Confirmation.UNCONFIRMED


def test_a_failed_key_is_an_error_and_is_not_resent() -> None:
    remote = FakeRemote(error=PairingRequiredError("pair first"))

    with pytest.raises(PairingRequiredError):
        discovered(remote).press_remote_key(DEVICE_ID, RemoteKey.VOLUME_UP)

    assert [c[0] for c in remote.calls] == ["press"]


def test_only_allowed_keys_reach_the_channel() -> None:
    remote = FakeRemote()

    with pytest.raises(InvalidArgumentError):
        discovered(remote).press_remote_key(DEVICE_ID, "KEYCODE_VOLUME_UP")  # type: ignore[arg-type]

    assert remote.calls == []


# Pairing -------------------------------------------------------------------------------------


def test_pairing_goes_to_the_discovered_address() -> None:
    remote = FakeRemote()
    service = discovered(remote)

    service.start_remote_pairing(DEVICE_ID)
    service.finish_remote_pairing(DEVICE_ID, "A1B2C3")

    assert remote.calls == [("start_pairing", HOST), ("finish_pairing", HOST, "A1B2C3")]


def test_a_pairing_code_must_be_text() -> None:
    remote = FakeRemote()

    with pytest.raises(InvalidArgumentError):
        discovered(remote).finish_remote_pairing(DEVICE_ID, 123456)  # type: ignore[arg-type]

    assert remote.calls == []


# Bridge --------------------------------------------------------------------------------------


def call(service: ControlService, method: str, **params: Any) -> dict[str, Any]:
    return dispatch(service, {"id": 1, "method": method, "params": params})


def test_bridge_remote_status_reports_what_the_tv_said() -> None:
    service = discovered(FakeRemote(before=RemoteObservation(is_on=None, volume=None)))

    response = call(service, "get_remote_status", deviceId=str(DEVICE_ID))

    assert response == {
        "id": 1,
        "ok": True,
        "result": {"remote": {"isOn": None, "currentApp": None, "volume": None}},
    }


def test_bridge_press_key_answers_with_the_tvs_reports() -> None:
    service = discovered(FakeRemote(after=volume(10, muted=True)))

    response = call(service, "press_remote_key", deviceId=str(DEVICE_ID), key="mute")

    assert response["ok"] is True
    assert response["result"]["result"] == {
        "key": "mute",
        "deviceId": str(DEVICE_ID),
        "confirmation": "confirmed",
        "detail": "the TV reports its sound muted",
        "before": {
            "isOn": True,
            "currentApp": None,
            "volume": {"level": 10, "max": 100, "muted": False},
        },
        "after": {
            "isOn": True,
            "currentApp": None,
            "volume": {"level": 10, "max": 100, "muted": True},
        },
    }


def test_bridge_key_names_cover_exactly_the_allowed_keys() -> None:
    assert set(REMOTE_KEYS.values()) == set(RemoteKey)
    assert len(REMOTE_KEYS) == len(RemoteKey)


@pytest.mark.parametrize("key", [None, 24, "KEYCODE_VOLUME_UP", "VOLUME_UP", "text:hi", "mic"])
def test_bridge_refuses_an_unknown_key_before_sending(key: object) -> None:
    remote = FakeRemote()

    response = call(discovered(remote), "press_remote_key", deviceId=str(DEVICE_ID), key=key)

    assert response["error"]["code"] == "invalid_argument"
    assert remote.calls == []


def test_bridge_pairing_flow() -> None:
    remote = FakeRemote()
    service = discovered(remote)

    started = call(service, "start_remote_pairing", deviceId=str(DEVICE_ID))
    finished = call(service, "finish_remote_pairing", deviceId=str(DEVICE_ID), code="a1b2c3")

    assert started["result"] == {"pairing": "code_shown"}
    assert finished["result"] == {"pairing": "paired"}
    assert remote.calls[-1] == ("finish_pairing", HOST, "a1b2c3")


def test_bridge_pairing_code_must_be_a_string() -> None:
    response = call(discovered(FakeRemote()), "finish_remote_pairing", deviceId=str(DEVICE_ID))

    assert response["error"]["code"] == "invalid_argument"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (PairingRequiredError("pair first"), "pairing_required"),
        (CommandRejectedError("refused"), "command_rejected"),
    ],
)
def test_bridge_remote_errors_keep_their_codes(error: Exception, code: str) -> None:
    service = discovered(FakeRemote(error=error))

    response = call(service, "finish_remote_pairing", deviceId=str(DEVICE_ID), code="A1B2C3")

    assert response["error"]["code"] == code


def test_every_host_runs_the_same_service_with_the_remote_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from control_tv.adapters.tv_remote import TvRemoteChannel, default_credentials_dir
    from control_tv.bridge import default_service

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    service = default_service()

    assert isinstance(service._channels._remote, TvRemoteChannel)
    assert default_credentials_dir() == tmp_path / "control-tv" / "tv-remote"
    assert not (tmp_path / "control-tv").exists()  # nothing is written until it is used


@pytest.mark.parametrize(
    ("adb", "expected"), [("/usr/bin/adb", "AdbAppInventory"), (None, "AdbShellAppInventory")]
)
def test_the_tv_apps_go_through_adb_where_it_exists_and_adb_shell_on_a_phone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, adb: str | None, expected: str
) -> None:
    import shutil

    from control_tv.bridge import default_service

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda name: adb if name == "adb" else None)
    monkeypatch.setenv("HOME", str(tmp_path))  # no Android SDK in this home
    for variable in ("CONTROL_TV_ADB", "ANDROID_HOME", "ANDROID_SDK_ROOT"):
        monkeypatch.delenv(variable, raising=False)

    service = default_service()

    assert type(service._channels._apps).__name__ == expected
    if adb is not None:
        assert service._channels._apps._adb == adb  # type: ignore[union-attr]


def test_every_remote_key_of_the_window_is_one_the_bridge_sends() -> None:
    import re
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "ui" / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-remote-key="([a-z0-9_]+)"', html))

    assert keys == set(REMOTE_KEYS)  # every key the bridge accepts has its button, and back


def test_without_xdg_data_home_credentials_go_to_the_users_local_share(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from control_tv.adapters.tv_remote import default_credentials_dir

    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_credentials_dir() == tmp_path / ".local" / "share" / "control-tv" / "tv-remote"


# Typing text ---------------------------------------------------------------------------------


def test_text_goes_once_to_the_discovered_tv_and_is_never_confirmed_by_it() -> None:
    remote = FakeRemote()

    result = discovered(remote).type_remote_text(DEVICE_ID, "hello world")

    assert remote.calls == [("type_text", HOST, "hello world")]
    assert result.confirmation is Confirmation.NOT_CHECKED
    assert result.detail == "sent to the TV's active text field; check the TV"


def test_text_is_not_sent_without_an_active_field_reported() -> None:
    result = discovered(FakeRemote(field_reported=False)).type_remote_text(DEVICE_ID, "abc")

    assert result.detail == "not sent because the TV did not report an active text field"
    assert result.field_reported is False


def test_text_must_be_a_string_and_a_group_gets_none() -> None:
    remote = FakeRemote()

    with pytest.raises(InvalidArgumentError):
        discovered(remote).type_remote_text(DEVICE_ID, 42)  # type: ignore[arg-type]
    with pytest.raises(UnsupportedOperationError):
        discovered(remote, GroupTransport()).type_remote_text(DEVICE_ID, "abc")

    assert remote.calls == []


def test_bridge_types_text_and_never_echoes_it() -> None:
    secret = "p4ss word"
    response = call(
        discovered(FakeRemote()), "type_remote_text", deviceId=str(DEVICE_ID), text=secret
    )

    assert response["ok"] is True
    assert response["result"]["result"]["confirmation"] == "not_checked"
    assert response["result"]["result"]["fieldReported"] is True
    assert secret not in str(response)


def test_bridge_refuses_text_that_is_not_a_string() -> None:
    remote = FakeRemote()

    response = call(discovered(remote), "type_remote_text", deviceId=str(DEVICE_ID), text=None)

    assert response["error"]["code"] == "invalid_argument"
    assert remote.calls == []


def test_bridge_reports_the_app_in_front_when_the_tv_says() -> None:
    observed = RemoteObservation(is_on=True, volume=None, current_app="com.example.tv")

    response = call(
        discovered(FakeRemote(before=observed)), "get_remote_status", deviceId=str(DEVICE_ID)
    )

    assert response["result"]["remote"]["currentApp"] == "com.example.tv"


def test_bridge_keyboard_keys_are_backspace_and_enter() -> None:
    assert REMOTE_KEYS["delete"] is RemoteKey.DELETE
    assert REMOTE_KEYS["enter"] is RemoteKey.ENTER


# The key map: every button, and the exact key the TV receives ----------------------------------
#
# Each name on the left is the one `press_remote_key` takes and the window's button carries
# (`data-remote-key`, `ui/src/remote.ts`); each code on the right is the Android key code the TV
# receives on its own remote channel. Both are written out by hand, independently of the
# implementation, so renaming a key on one side, or pointing a button at another key code, fails
# here instead of silently sending something else to the TV.
KEY_CODES: dict[str, str] = {
    # Sound, power and the D-pad.
    "volume_down": "KEYCODE_VOLUME_DOWN",
    "volume_up": "KEYCODE_VOLUME_UP",
    # The speaker's mute: `KEYCODE_MUTE` silences the microphone, not the sound.
    "mute": "KEYCODE_VOLUME_MUTE",
    "power": "KEYCODE_POWER",
    "up": "KEYCODE_DPAD_UP",
    "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT",
    "right": "KEYCODE_DPAD_RIGHT",
    # The D-pad's own OK, not `KEYCODE_ENTER`, which submits the TV's active text field.
    "ok": "KEYCODE_DPAD_CENTER",
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    # The keyboard's Backspace and Enter, for the TV's active text field.
    "delete": "KEYCODE_DEL",
    "enter": "KEYCODE_ENTER",
    # The digits, by the number keys' own codes.
    "digit_0": "KEYCODE_0",
    "digit_1": "KEYCODE_1",
    "digit_2": "KEYCODE_2",
    "digit_3": "KEYCODE_3",
    "digit_4": "KEYCODE_4",
    "digit_5": "KEYCODE_5",
    "digit_6": "KEYCODE_6",
    "digit_7": "KEYCODE_7",
    "digit_8": "KEYCODE_8",
    "digit_9": "KEYCODE_9",
    # The TV's own menus and information panels.
    "info": "KEYCODE_INFO",
    "guide": "KEYCODE_GUIDE",
    "input": "KEYCODE_TV_INPUT",
    "settings": "KEYCODE_SETTINGS",
    # Subtitles and the audio track, the two switches a player offers.
    "subtitles": "KEYCODE_CAPTIONS",
    "audio_track": "KEYCODE_MEDIA_AUDIO_TRACK",
    # Channel surfing.
    "channel_up": "KEYCODE_CHANNEL_UP",
    "channel_down": "KEYCODE_CHANNEL_DOWN",
    # The four colour keys are the programmed keys, not `KEYCODE_RED` and friends.
    "red": "KEYCODE_PROG_RED",
    "green": "KEYCODE_PROG_GREEN",
    "yellow": "KEYCODE_PROG_YELLOW",
    "blue": "KEYCODE_PROG_BLUE",
    # Playback on the TV's own remote: play and pause are one toggle key, and stop is its own.
    "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE",
    "stop": "KEYCODE_MEDIA_STOP",
    "rewind": "KEYCODE_MEDIA_REWIND",
    "fast_forward": "KEYCODE_MEDIA_FAST_FORWARD",
    "previous": "KEYCODE_MEDIA_PREVIOUS",
    "next": "KEYCODE_MEDIA_NEXT",
    "record": "KEYCODE_MEDIA_RECORD",
    # Google Assistant, from the TV's own microphone.
    "assistant": "KEYCODE_ASSIST",
}

# Names a person may reasonably try, or misremember, that this remote does not send. Each must be
# refused by name, before anything reaches the TV, and never mapped to the nearest key the channel
# happens to know: someone asking for "pause" is told ControlTV cannot, instead of being sent
# another key's effect.
UNSUPPORTED_KEYS: tuple[str, ...] = (
    "mic",
    "microphone",
    "voice",
    "search",
    "menu",
    "audio",
    "audio_language",
    "caption",
    "subtitle",
    "play",
    "pause",
    "record_stop",
    "channel_plus",
    "channel_minus",
    "channels_up",
    "channelup",
    "volume_plus",
    "vol_up",
    "power_toggle",
    "digit",
    "0",
    "5",
    "google_assistant",
    "assistant_key",
    "dpad_up",
    "UP",
    "PLAY_PAUSE",
    "KEYCODE_ASSIST",
    " up",
    "up ",
    "up\n",
    "text:hi",
)


@pytest.mark.parametrize(("name", "code"), sorted(KEY_CODES.items()))
def test_each_button_sends_its_exact_key_code_to_the_tv(name: str, code: str) -> None:
    remote = FakeRemote()

    response = call(discovered(remote), "press_remote_key", deviceId=str(DEVICE_ID), key=name)

    assert response["ok"] is True
    assert response["result"]["result"]["key"] == name
    assert remote.calls == [("press", HOST, code)]  # once, and nothing else


def test_the_key_map_matches_every_key_the_bridge_accepts() -> None:
    assert {name: key.value for name, key in REMOTE_KEYS.items()} == KEY_CODES
    assert set(KEY_CODES.values()) == {key.value for key in RemoteKey}
    assert len(set(KEY_CODES.values())) == len(KEY_CODES)  # no two buttons share a key code


def test_no_key_code_turns_on_the_microphone_the_voice_assist_or_the_search() -> None:
    sent = set(KEY_CODES.values())

    for forbidden in (
        "KEYCODE_MUTE",  # the microphone's mute, not the speaker's
        "KEYCODE_VOICE_ASSIST",
        "KEYCODE_SEARCH",
    ):
        assert forbidden not in sent


@pytest.mark.parametrize("code", sorted(KEY_CODES.values()))
def test_each_key_code_is_one_the_tv_library_knows(code: str) -> None:
    # The library resolves the name to the protocol's own key code and raises for an unknown one,
    # so a plausible but wrong name (KEYCODE_RED for the red colour key) fails here, not on the TV.
    from androidtvremote2.remotemessage_pb2 import RemoteKeyCode

    assert RemoteKeyCode.Value(code) > 0


@pytest.mark.parametrize("name", UNSUPPORTED_KEYS)
def test_a_button_controltv_cannot_send_is_refused_by_name_before_anything_is_sent(
    name: str,
) -> None:
    remote = FakeRemote()

    response = call(discovered(remote), "press_remote_key", deviceId=str(DEVICE_ID), key=name)

    assert response["error"]["code"] == "invalid_argument"
    assert remote.calls == []


def test_the_refusal_names_every_button_that_can_be_sent() -> None:
    service = discovered(FakeRemote())

    response = call(service, "press_remote_key", deviceId=str(DEVICE_ID), key="pause")

    message: str = response["error"]["message"]
    listing, _, refused = message.partition(": ")
    assert listing.startswith("key must be one of ")
    assert listing.removeprefix("key must be one of ").split(", ") == list(REMOTE_KEYS)
    assert refused == repr("pause")
    assert set(KEY_CODES) == set(REMOTE_KEYS)  # the map covers every listed button, and no other


# Apps ------------------------------------------------------------------------------------------


@dataclass
class FakeApps:
    apps: tuple[TvApp, ...] = (TvApp("com.example.video", "Video"), TvApp("org.example.music"))
    error: Exception | None = None
    front: str | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def list_apps(self, host: str) -> tuple[TvApp, ...]:
        self.calls.append(("list", host))
        if self.error is not None:
            raise self.error
        return self.apps

    def launch_app(self, host: str, package: str) -> AppLaunch:
        self.calls.append(("launch", host, package))
        if self.error is not None:
            raise self.error
        return AppLaunch(package=package, front_app=self.front)

    def pair(self, host: str, pairing_endpoint: str, code: str) -> None:
        self.calls.append(("pair", host, pairing_endpoint, code))
        if self.error is not None:
            raise self.error

    def open_classic_port(self, host: str) -> None:
        self.calls.append(("open_port", host))
        if self.error is not None:
            raise self.error


def with_apps(apps: FakeApps, transport: FakeTransport | None = None) -> ControlService:
    service = ControlService(transport or FakeTransport(), remote=FakeRemote(), apps=apps)
    service.discover_devices(timeout=1.0)
    return service


def test_the_app_list_comes_from_the_discovered_tv() -> None:
    apps = FakeApps()

    assert with_apps(apps).list_tv_apps(DEVICE_ID) == list(apps.apps)
    assert apps.calls == [("list", HOST)]


def test_wireless_debugging_pairing_targets_only_the_discovered_tv() -> None:
    apps = FakeApps()

    response = call(
        with_apps(apps),
        "pair_tv_apps",
        deviceId=str(DEVICE_ID),
        pairingEndpoint=f"{HOST}:40123",
        code="123456",
    )

    assert response["result"] == {"pairing": "paired"}
    assert apps.calls == [("pair", HOST, f"{HOST}:40123", "123456")]


def test_wireless_debugging_pairing_needs_text_and_the_apps_channel() -> None:
    with pytest.raises(InvalidArgumentError):
        with_apps(FakeApps()).pair_tv_apps(DEVICE_ID, None, "123456")  # type: ignore[arg-type]
    with pytest.raises(UnsupportedOperationError):
        discovered(FakeRemote()).pair_tv_apps(DEVICE_ID, f"{HOST}:40123", "123456")


def test_opening_the_tv_to_the_phone_targets_only_the_discovered_tv() -> None:
    apps = FakeApps()

    response = call(with_apps(apps), "open_tv_apps_port", deviceId=str(DEVICE_ID))

    assert response["result"] == {"port": 5555}
    assert apps.calls == [("open_port", HOST)]
    with pytest.raises(UnsupportedOperationError):
        discovered(FakeRemote()).open_tv_apps_port(DEVICE_ID)


def test_no_app_list_for_a_group_or_without_the_inventory() -> None:
    apps = FakeApps()

    with pytest.raises(UnsupportedOperationError):
        with_apps(apps, GroupTransport()).list_tv_apps(DEVICE_ID)
    with pytest.raises(UnsupportedOperationError):
        discovered(FakeRemote()).list_tv_apps(DEVICE_ID)
    assert apps.calls == []


@pytest.mark.parametrize(
    ("front", "confirmation", "detail"),
    [
        ("com.example.video", Confirmation.CONFIRMED, "the TV reports the app in front"),
        ("com.other", Confirmation.UNCONFIRMED, "sent; the TV still reports com.other in front"),
        (
            None,
            Confirmation.NOT_CHECKED,
            "sent; the TV did not report the app in front; check the TV",
        ),
    ],
)
def test_a_launch_is_confirmed_only_by_the_app_the_tv_reports_in_front(
    front: str | None, confirmation: Confirmation, detail: str
) -> None:
    apps = FakeApps(front=front)

    result = with_apps(apps).launch_tv_app(DEVICE_ID, "com.example.video")

    assert apps.calls == [("launch", HOST, "com.example.video")]
    assert (result.confirmation, result.detail, result.front_app) == (confirmation, detail, front)


def test_a_launch_needs_a_package_name_string_and_the_inventory() -> None:
    apps = FakeApps()

    with pytest.raises(InvalidArgumentError):
        with_apps(apps).launch_tv_app(DEVICE_ID, None)  # type: ignore[arg-type]
    with pytest.raises(UnsupportedOperationError):  # neither the receiver nor the inventory
        discovered(FakeRemote()).launch_tv_app(DEVICE_ID, "com.example.video")
    assert apps.calls == []


def test_bridge_lists_apps_with_their_known_names() -> None:
    response = call(with_apps(FakeApps()), "list_tv_apps", deviceId=str(DEVICE_ID))

    assert response["result"] == {
        "apps": [
            {"package": "com.example.video", "name": "Video"},
            {"package": "org.example.music", "name": None},
        ],
        "source": "tv",
        "reason": None,
    }


class MemoryApps:
    def __init__(self) -> None:
        self.kept: dict[DeviceId, tuple[TvApp, ...]] = {}

    def recall(self, device_id: DeviceId) -> tuple[TvApp, ...] | None:
        return self.kept.get(device_id)

    def remember(self, device_id: DeviceId, apps: tuple[TvApp, ...]) -> None:
        self.kept[device_id] = apps


def remembering(apps: FakeApps, memory: MemoryApps) -> ControlService:
    service = ControlService(FakeTransport(), remote=FakeRemote(), apps=apps, app_memory=memory)
    service.discover_devices(timeout=1.0)
    return service


def test_without_the_tvs_debugging_the_window_gets_its_last_list_never_other_apps() -> None:
    memory = MemoryApps()
    apps = FakeApps()
    service = remembering(apps, memory)
    call(service, "list_tv_apps", deviceId=str(DEVICE_ID))  # the TV answers: kept

    apps.error = DebuggingOffError("debugging off")
    remembered = call(service, "list_tv_apps", deviceId=str(DEVICE_ID))["result"]
    memory.kept.clear()
    nothing = call(service, "list_tv_apps", deviceId=str(DEVICE_ID))["result"]

    assert remembered["source"] == "remembered" and remembered["reason"] == "debugging_off"
    assert [app["package"] for app in remembered["apps"]] == [a.package for a in apps.apps]
    assert nothing == {"apps": [], "source": "none", "reason": "debugging_off"}


def test_the_strict_list_still_reports_the_tvs_error() -> None:
    apps = FakeApps(error=PairingRequiredError("allow control-TV on the TV"))

    with pytest.raises(PairingRequiredError):
        remembering(apps, MemoryApps()).list_tv_apps(DEVICE_ID)


def test_bridge_says_when_the_tv_debugging_is_off() -> None:
    apps = FakeApps(error=DebuggingOffError("turn on USB debugging"))

    listed = call(with_apps(apps), "list_tv_apps", deviceId=str(DEVICE_ID))
    launched = call(
        with_apps(apps), "launch_tv_app", deviceId=str(DEVICE_ID), package="com.example.video"
    )

    assert listed["result"]["reason"] == "debugging_off"
    assert launched["error"]["code"] == "debugging_off"  # no receiver here: only the debugging


def test_bridge_app_list_without_the_channel_is_not_reported_as_debugging_off() -> None:
    response = call(discovered(FakeRemote()), "list_tv_apps", deviceId=str(DEVICE_ID))

    assert response["result"]["reason"] == "unsupported_operation"


def test_a_group_or_an_unknown_tv_is_still_refused() -> None:
    response = call(with_apps(FakeApps()), "list_tv_apps", deviceId="not-a-discovered-tv")

    assert "error" in response


def test_bridge_launches_an_app_and_answers_with_what_the_tv_reported() -> None:
    service = with_apps(FakeApps(front="com.example.video"))

    response = call(service, "launch_tv_app", deviceId=str(DEVICE_ID), package="com.example.video")

    result = response["result"]["result"]
    assert result["confirmation"] == "confirmed"
    assert result["package"] == "com.example.video"
    assert result["frontApp"] == "com.example.video"


def test_bridge_refuses_a_launch_without_a_package_string() -> None:
    apps = FakeApps()

    response = call(with_apps(apps), "launch_tv_app", deviceId=str(DEVICE_ID), package=7)

    assert response["error"]["code"] == "invalid_argument"
    assert apps.calls == []


def test_apps_do_not_depend_on_what_discovery_saw_of_the_remote() -> None:
    apps = FakeApps(front="com.example.video")

    service = with_apps(apps, NoRemoteTransport())

    assert service.list_tv_apps(DEVICE_ID) == list(apps.apps)
    assert service.launch_tv_app(DEVICE_ID, "com.example.video").front_app == "com.example.video"
    with pytest.raises(UnsupportedOperationError):
        service.press_remote_key(DEVICE_ID, RemoteKey.HOME)


def test_the_tv_channels_need_only_a_device_lookup_not_the_cast_service() -> None:
    from control_tv.tv_channels import TvChannels

    tv = Device(id=DEVICE_ID, friendly_name="Living room", host=HOST, port=8009)
    remote = FakeRemote()
    channels = TvChannels({DEVICE_ID: tv}.get, remote=remote, apps=FakeApps())

    assert channels.get_remote_status(DEVICE_ID) == volume(10)
    assert channels.list_tv_apps(DEVICE_ID)[0].package == "com.example.video"
    assert remote.calls == [("observe", HOST)]
    with pytest.raises(DeviceNotFoundError):
        channels.get_remote_status(DeviceId("uuid-other"))


class ReceiverApps:
    def __init__(
        self,
        apps: tuple[TvApp, ...] = (),
        error: Exception | None = None,
        launch: AppLaunch | Exception | None = None,
    ) -> None:
        self.apps, self.error, self.hosts = apps, error, list[str]()
        self.launch, self.launches = launch, list[tuple[str, str]]()

    def list_apps(self, host: str) -> tuple[TvApp, ...]:
        self.hosts.append(host)
        if self.error is not None:
            raise self.error
        return self.apps

    def launch_app(self, host: str, package: str) -> AppLaunch:
        self.launches.append((host, package))
        if isinstance(self.launch, Exception):
            raise self.launch
        return self.launch or AppLaunch(package=package, front_app=None, started=True)


def with_receiver(apps: FakeApps, receiver: ReceiverApps, memory: MemoryApps) -> ControlService:
    service = ControlService(
        FakeTransport(),
        remote=FakeRemote(),
        apps=apps,
        app_memory=memory,
        receiver_apps=receiver,
    )
    service.discover_devices(timeout=1.0)
    return service


def test_without_the_tvs_debugging_its_receiver_lists_the_apps_and_they_are_kept() -> None:
    memory = MemoryApps()
    receiver = ReceiverApps((TvApp("fr.free.oqee", "OQEE by Free"),))
    service = with_receiver(FakeApps(error=DebuggingOffError("off")), receiver, memory)

    listing = service.browse_tv_apps(DEVICE_ID)

    assert (listing.source, listing.reason) == ("tv", None)
    assert listing.apps == receiver.apps
    assert receiver.hosts == [HOST]
    assert memory.kept[DEVICE_ID] == receiver.apps


def test_when_neither_answers_the_debuggings_reason_is_given() -> None:
    receiver = ReceiverApps(error=OperationTimeoutError("no receiver"))
    service = with_receiver(FakeApps(error=DebuggingOffError("off")), receiver, MemoryApps())

    listing = service.browse_tv_apps(DEVICE_ID)

    assert (listing.source, listing.reason, listing.apps) == ("none", "debugging_off", ())


def test_the_receiver_is_not_asked_when_the_debugging_answers() -> None:
    receiver = ReceiverApps()
    service = with_receiver(FakeApps(), receiver, MemoryApps())

    assert service.browse_tv_apps(DEVICE_ID).source == "tv"
    assert receiver.hosts == []


# Opening an app (recette 2026-10-26: the TV refused market://launch, ControlTV said "sent") --


def test_an_app_opens_through_the_receiver_without_the_tvs_debugging() -> None:
    apps = FakeApps(error=DebuggingOffError("debugging off"))
    started = AppLaunch(package="tv.arte.plus7", front_app="tv.arte.plus7", started=True)
    receiver = ReceiverApps(launch=started)
    service = with_receiver(apps, receiver, MemoryApps())

    result = call(service, "launch_tv_app", deviceId=str(DEVICE_ID), package="tv.arte.plus7")

    assert receiver.launches == [(HOST, "tv.arte.plus7")]
    assert apps.calls == []
    assert result["result"]["result"]["confirmation"] == "confirmed"
    assert result["result"]["result"]["started"] is True


def test_started_but_not_reported_in_front_says_the_tv_started_it_not_merely_sent() -> None:
    service = with_receiver(FakeApps(), ReceiverApps(), MemoryApps())

    result = service.launch_tv_app(DEVICE_ID, "tv.arte.plus7")

    assert result.confirmation is Confirmation.NOT_CHECKED
    assert result.started is True
    assert result.detail.startswith("the TV started it")


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (CommandRejectedError("this app is not installed on the TV"), "command_rejected"),
        (OperationTimeoutError("ControlTV Receiver did not answer"), "timeout"),
    ],
)
def test_a_receiver_failure_reaches_the_window_and_is_never_retried(
    failure: Exception, code: str
) -> None:
    apps = FakeApps()
    service = with_receiver(apps, ReceiverApps(launch=failure), MemoryApps())

    response = call(service, "launch_tv_app", deviceId=str(DEVICE_ID), package="tv.arte.plus7")

    assert response["error"]["code"] == code
    assert apps.calls == []  # a launch that may have reached the TV is not sent another way


def test_without_the_receiver_the_debugging_opens_the_app_and_says_sent_not_started() -> None:
    apps = FakeApps(front=None)

    result = with_apps(apps).launch_tv_app(DEVICE_ID, "com.example.video")

    assert result.started is False
    assert result.detail.startswith("sent;")


def test_an_unpaired_remote_asks_for_pairing_even_when_the_debugging_is_off() -> None:
    apps = FakeApps(error=DebuggingOffError("debugging off"))
    receiver = ReceiverApps(launch=PairingRequiredError("pair this TV first"))
    service = with_receiver(apps, receiver, MemoryApps())

    with pytest.raises(PairingRequiredError):
        service.launch_tv_app(DEVICE_ID, "com.example.video")
    assert apps.calls == [("launch", HOST, "com.example.video")]  # tried, refused, not resent
