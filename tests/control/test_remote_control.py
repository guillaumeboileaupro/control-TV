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
    AudibleVolume,
    CommandRejectedError,
    Confirmation,
    Device,
    DeviceId,
    DeviceKind,
    DeviceNotFoundError,
    InvalidArgumentError,
    KeyPress,
    PairingRequiredError,
    RemoteKey,
    RemoteObservation,
    TextEntry,
    UnsupportedOperationError,
)
from control_tv.ports import TvRemote
from control_tv.service import ControlService
from fakes import DEVICE_ID, FakeTransport

HOST = "192.168.1.20"  # FakeTransport's discovered address


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
        (RemoteKey.POWER, volume(10), volume(10), Confirmation.NOT_CHECKED),
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

    assert isinstance(service._remote, TvRemoteChannel)
    assert default_credentials_dir() == tmp_path / "control-tv" / "tv-remote"
    assert not (tmp_path / "control-tv").exists()  # nothing is written until it is used


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


def test_text_sent_without_an_active_field_reported_says_so() -> None:
    result = discovered(FakeRemote(field_reported=False)).type_remote_text(DEVICE_ID, "abc")

    assert result.detail == "sent, but the TV did not report an active text field; check the TV"
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
