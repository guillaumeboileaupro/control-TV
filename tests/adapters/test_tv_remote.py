"""The Android TV Remote channel (`control_tv.adapters.tv_remote`), over a fake remote.

No network: the library's remote object is replaced by `FakeRemote`, which records every
call. The rules pinned: private credentials, an allowlist of keys, one connection per
operation that is always closed, a key sent exactly once even when it fails afterwards,
the TV's reports returned as observed, explicit pairing, and no TV address in any error.
"""

from __future__ import annotations

import asyncio
import stat
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from androidtvremote2 import CannotConnect, ConnectionClosed, InvalidAuth

from control_tv.adapters.tv_remote import CERT_FILE, KEY_FILE, TvRemoteChannel
from control_tv.domain import (
    AudibleVolume,
    CommandRejectedError,
    DeviceUnavailableError,
    InvalidArgumentError,
    OperationTimeoutError,
    PairingRequiredError,
    RemoteKey,
    RemoteObservation,
    UnsupportedOperationError,
)

HOST = "192.0.2.10"
CERT_PEM = b"-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"
KEY_PEM = b"-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n"


class FakeTv:
    """What the fake network knows about one TV, shared by every connection to it."""

    def __init__(self) -> None:
        self.is_on: bool | None = True
        self.volume: dict[str, Any] | None = {"level": 10, "max": 100, "muted": False}
        self.reports_volume_after_key = True
        self.paired_code = "A1B2C3"
        self.connect_error: Exception | None = None
        self.send_error: Exception | None = None
        self.connect_delay = 0.0
        self.current_app = ""
        self.field_counter = 0
        self.texts: list[str] = []
        self.log: list[tuple[str, ...]] = []
        self.remotes: list[FakeRemote] = []


class FakeRemote:
    def __init__(self, tv: FakeTv, certfile: str, keyfile: str, host: str) -> None:
        asyncio.get_running_loop()  # the library needs a running loop, so must this fake
        self.tv = tv
        self.certfile = certfile
        self.keyfile = keyfile
        self.host = host
        self.is_on: bool | None = None
        self.volume_info: dict[str, Any] | None = None
        self.current_app: str = ""
        self._remote_message_protocol = SimpleNamespace(ime_field_counter=0)
        self.disconnected = 0

    async def async_connect(self) -> None:
        self.tv.log.append(("connect",))
        if self.tv.connect_delay:
            await asyncio.sleep(self.tv.connect_delay)
        if self.tv.connect_error is not None:
            raise self.tv.connect_error
        self.is_on = self.tv.is_on
        self.volume_info = None if self.tv.volume is None else dict(self.tv.volume)
        self.current_app = self.tv.current_app
        self._remote_message_protocol.ime_field_counter = self.tv.field_counter

    def send_key_command(self, key_code: str) -> None:
        self.tv.log.append(("key", key_code))
        if self.tv.send_error is not None:
            raise self.tv.send_error
        if self.tv.volume is not None and self.tv.reports_volume_after_key:
            if key_code == "KEYCODE_VOLUME_UP":
                self.tv.volume["level"] += 1
            elif key_code == "KEYCODE_VOLUME_MUTE":
                self.tv.volume["muted"] = not self.tv.volume["muted"]
            self.volume_info = dict(self.tv.volume)

    def send_text(self, text: str) -> None:
        self.tv.log.append(("text", str(len(text))))  # the length only, like a real log
        self.tv.texts.append(text)
        if self.tv.send_error is not None:
            raise self.tv.send_error

    async def async_start_pairing(self) -> None:
        self.tv.log.append(("start_pairing",))
        if self.tv.connect_error is not None:
            raise self.tv.connect_error

    async def async_finish_pairing(self, code: str) -> None:
        self.tv.log.append(("finish_pairing", code))
        if code != self.tv.paired_code:
            raise InvalidAuth("bad code")

    def disconnect(self) -> None:
        self.disconnected += 1
        self.tv.log.append(("disconnect",))


@pytest.fixture
def tv() -> FakeTv:
    return FakeTv()


@pytest.fixture
def channel(tmp_path: Path, tv: FakeTv) -> Iterator[TvRemoteChannel]:
    def factory(client_name: str, certfile: str, keyfile: str, host: str) -> FakeRemote:
        assert client_name == "control-TV"
        tv.remotes.append(FakeRemote(tv, certfile, keyfile, host))
        return tv.remotes[-1]

    made = TvRemoteChannel(
        tmp_path / "remote",
        timeout=2.0,
        settle=0.2,
        remote_factory=factory,
        cert_generator=lambda name: (CERT_PEM, KEY_PEM),
    )
    yield made
    made.close()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# Credentials ---------------------------------------------------------------------------------


def test_credentials_are_created_once_in_a_private_directory(
    channel: TvRemoteChannel, tv: FakeTv, tmp_path: Path
) -> None:
    channel.observe(HOST)
    channel.observe(HOST)

    folder = tmp_path / "remote"
    assert mode(folder) == 0o700
    assert mode(folder / CERT_FILE) == 0o600
    assert mode(folder / KEY_FILE) == 0o600
    assert (folder / KEY_FILE).read_bytes() == KEY_PEM
    assert sorted(p.name for p in folder.iterdir()) == sorted([CERT_FILE, KEY_FILE])
    assert tv.remotes[0].certfile == str(folder / CERT_FILE)
    assert tv.remotes[0].keyfile == str(folder / KEY_FILE)


def test_loose_permissions_on_existing_credentials_are_tightened(
    tmp_path: Path, tv: FakeTv
) -> None:
    folder = tmp_path / "remote"
    folder.mkdir(mode=0o755)
    (folder / CERT_FILE).write_bytes(CERT_PEM)
    (folder / KEY_FILE).write_bytes(KEY_PEM)
    (folder / KEY_FILE).chmod(0o664)

    def refuse(name: str) -> tuple[bytes, bytes]:
        raise AssertionError("existing credentials must be kept, not regenerated")

    channel = TvRemoteChannel(
        folder,
        settle=0.0,
        remote_factory=lambda *args: FakeRemote(tv, *args[1:]),
        cert_generator=refuse,
    )
    try:
        channel.observe(HOST)
    finally:
        channel.close()

    assert mode(folder) == 0o700
    assert mode(folder / KEY_FILE) == 0o600
    assert mode(folder / CERT_FILE) == 0o600


def test_a_lone_certificate_without_its_key_is_replaced_by_a_new_pair(
    channel: TvRemoteChannel, tmp_path: Path
) -> None:
    folder = tmp_path / "remote"
    folder.mkdir(mode=0o700)
    (folder / CERT_FILE).write_bytes(b"orphan")

    channel.observe(HOST)

    assert (folder / CERT_FILE).read_bytes() == CERT_PEM
    assert (folder / KEY_FILE).read_bytes() == KEY_PEM


# Reading -------------------------------------------------------------------------------------


def test_observe_returns_what_the_tv_reported_and_closes_the_connection(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    observed = channel.observe(HOST)

    assert observed == RemoteObservation(
        is_on=True, volume=AudibleVolume(level=10, maximum=100, muted=False), current_app=None
    )
    assert tv.log == [("connect",), ("disconnect",)]
    assert tv.remotes[0].host == HOST


def test_a_tv_that_reports_no_volume_leaves_it_unknown(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.volume = None
    tv.is_on = None

    assert channel.observe(HOST) == RemoteObservation(is_on=None, volume=None)


@pytest.mark.parametrize(
    "report",
    [
        {"level": 5, "max": 0, "muted": False},
        {"level": 101, "max": 100, "muted": False},
        {"level": True, "max": 100, "muted": False},
        {"level": 5, "max": 100, "muted": "no"},
        {"level": 5, "max": 100},
    ],
)
def test_an_inconsistent_volume_report_is_unknown_not_guessed(
    channel: TvRemoteChannel, tv: FakeTv, report: dict[str, Any]
) -> None:
    tv.volume = report

    assert channel.observe(HOST).volume is None


# Keys ----------------------------------------------------------------------------------------


def test_a_volume_key_is_sent_once_and_reports_the_volume_before_and_after(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    pressed = channel.press(HOST, RemoteKey.VOLUME_UP)

    assert tv.log == [("connect",), ("key", "KEYCODE_VOLUME_UP"), ("disconnect",)]
    assert pressed.before.volume == AudibleVolume(level=10, maximum=100, muted=False)
    assert pressed.after.volume == AudibleVolume(level=11, maximum=100, muted=False)


def test_mute_is_the_speaker_mute_key_and_its_effect_is_what_the_tv_reports(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    pressed = channel.press(HOST, RemoteKey.VOLUME_MUTE)

    assert ("key", "KEYCODE_VOLUME_MUTE") in tv.log
    assert pressed.before.volume is not None and pressed.before.volume.muted is False
    assert pressed.after.volume is not None and pressed.after.volume.muted is True


def test_a_tv_that_does_not_report_a_change_is_returned_unchanged_not_assumed(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.reports_volume_after_key = False

    pressed = channel.press(HOST, RemoteKey.VOLUME_UP)

    assert pressed.after == pressed.before


@pytest.mark.parametrize("key", [RemoteKey.POWER, RemoteKey.DPAD_CENTER, RemoteKey.HOME])
def test_other_allowed_keys_are_sent_by_their_key_code_name(
    channel: TvRemoteChannel, tv: FakeTv, key: RemoteKey
) -> None:
    channel.press(HOST, key)

    assert [entry for entry in tv.log if entry[0] == "key"] == [("key", key.value)]


@pytest.mark.parametrize("key", ["KEYCODE_VOLUME_UP", "text:hello", "KEYCODE_MUTE", 24])
def test_only_allowlisted_keys_can_be_sent(channel: TvRemoteChannel, tv: FakeTv, key: Any) -> None:
    with pytest.raises(InvalidArgumentError):
        channel.press(HOST, key)

    assert tv.log == []


def test_the_allowlist_has_no_text_launch_voice_or_microphone_key() -> None:
    assert {key.name for key in RemoteKey} == {
        "VOLUME_UP",
        "VOLUME_DOWN",
        "VOLUME_MUTE",
        "POWER",
        "DPAD_UP",
        "DPAD_DOWN",
        "DPAD_LEFT",
        "DPAD_RIGHT",
        "DPAD_CENTER",
        "BACK",
        "HOME",
        "DELETE",
        "ENTER",
    }


def test_a_failure_after_the_key_was_handed_over_is_reported_and_never_resent(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.send_error = ConnectionClosed("lost")

    with pytest.raises(DeviceUnavailableError):
        channel.press(HOST, RemoteKey.POWER)

    assert [entry for entry in tv.log if entry[0] == "key"] == [("key", "KEYCODE_POWER")]
    assert tv.log[-1] == ("disconnect",)


@pytest.mark.parametrize("host", ["tv.local", "", "192.0.2.300", None])
def test_a_tv_is_reached_by_ip_address_only(
    channel: TvRemoteChannel, tv: FakeTv, host: Any
) -> None:
    with pytest.raises(InvalidArgumentError):
        channel.press(host, RemoteKey.HOME)

    assert tv.log == []


# Errors --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (InvalidAuth("need to pair"), PairingRequiredError),
        (CannotConnect(f"Couldn't connect to {HOST}:6466"), DeviceUnavailableError),
        (ConnectionClosed("closed"), DeviceUnavailableError),
        (OSError(f"unreachable {HOST}"), DeviceUnavailableError),
    ],
)
def test_library_failures_become_control_errors_without_the_tv_address(
    channel: TvRemoteChannel, tv: FakeTv, raised: Exception, expected: type[Exception]
) -> None:
    tv.connect_error = raised

    with pytest.raises(expected) as excinfo:
        channel.press(HOST, RemoteKey.HOME)

    assert HOST not in str(excinfo.value)
    assert [entry for entry in tv.log if entry[0] == "key"] == []
    assert tv.log[-1] == ("disconnect",)


def test_a_tv_that_does_not_answer_in_time_is_a_timeout_and_the_connection_is_closed(
    tmp_path: Path, tv: FakeTv
) -> None:
    def factory(client_name: str, certfile: str, keyfile: str, host: str) -> FakeRemote:
        tv.remotes.append(FakeRemote(tv, certfile, keyfile, host))
        return tv.remotes[-1]

    tv.connect_delay = 5.0
    channel = TvRemoteChannel(
        tmp_path / "remote",
        timeout=0.2,
        remote_factory=factory,
        cert_generator=lambda name: (CERT_PEM, KEY_PEM),
    )
    try:
        with pytest.raises(OperationTimeoutError):
            channel.press(HOST, RemoteKey.HOME)
        tv.connect_delay = 0.0
        assert channel.observe(HOST).is_on is True  # still usable after a timeout
    finally:
        channel.close()

    assert tv.remotes[0].disconnected == 1
    assert ("key", "KEYCODE_HOME") not in tv.log


# Pairing -------------------------------------------------------------------------------------


def test_pairing_with_the_code_shown_on_the_tv(channel: TvRemoteChannel, tv: FakeTv) -> None:
    channel.start_pairing(HOST)
    channel.finish_pairing(HOST, " a1b2c3 ")

    assert tv.log == [("start_pairing",), ("finish_pairing", "A1B2C3"), ("disconnect",)]


def test_a_refused_code_is_rejected_and_pairing_must_start_again(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    channel.start_pairing(HOST)

    with pytest.raises(CommandRejectedError):
        channel.finish_pairing(HOST, "FFFFFF")
    with pytest.raises(PairingRequiredError):
        channel.finish_pairing(HOST, "A1B2C3")

    assert tv.log.count(("disconnect",)) == 1


@pytest.mark.parametrize("code", ["12345", "1234567", "GHIJKL", "", None])
def test_a_malformed_code_is_refused_before_anything_is_sent(
    channel: TvRemoteChannel, tv: FakeTv, code: Any
) -> None:
    channel.start_pairing(HOST)

    with pytest.raises(InvalidArgumentError):
        channel.finish_pairing(HOST, code)

    assert [entry[0] for entry in tv.log] == ["start_pairing"]


def test_finishing_without_starting_asks_to_start_pairing(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    with pytest.raises(PairingRequiredError):
        channel.finish_pairing(HOST, "A1B2C3")

    assert tv.log == []


def test_starting_again_drops_the_previous_pairing_attempt(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    channel.start_pairing(HOST)
    channel.start_pairing(HOST)

    assert tv.remotes[0].disconnected == 1
    assert tv.remotes[1].disconnected == 0


def test_a_tv_that_cannot_be_reached_for_pairing_keeps_nothing_open(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.connect_error = CannotConnect("no route")

    with pytest.raises(DeviceUnavailableError):
        channel.start_pairing(HOST)
    with pytest.raises(PairingRequiredError):
        channel.finish_pairing(HOST, "A1B2C3")

    assert tv.remotes[0].disconnected == 1


def test_closing_drops_a_pending_pairing(channel: TvRemoteChannel, tv: FakeTv) -> None:
    channel.start_pairing(HOST)
    channel.close()

    assert tv.remotes[0].disconnected == 1


# The real library, offline ---------------------------------------------------------------------


def test_the_default_factory_builds_the_library_remote_without_voice_or_network() -> None:
    from androidtvremote2 import AndroidTVRemote

    from control_tv.adapters.tv_remote import _default_remote_factory

    async def build() -> Any:
        return _default_remote_factory("control-TV", "cert.pem", "key.pem", HOST)

    remote = asyncio.run(build())

    assert isinstance(remote, AndroidTVRemote)
    assert remote.host == HOST
    assert remote._enable_ime is True  # carries text and reports the app in front
    assert remote._enable_voice is False


def test_the_default_certificate_is_a_pem_pair_named_after_the_client() -> None:
    from cryptography import x509

    from control_tv.adapters.tv_remote import _default_cert_generator

    cert_pem, key_pem = _default_cert_generator("control-TV")

    cert = x509.load_pem_x509_certificate(cert_pem)
    assert "control-TV" in cert.subject.rfc4514_string()
    assert key_pem.startswith(b"-----BEGIN")


def test_without_the_optional_extra_the_channel_says_so(
    channel: TvRemoteChannel, tv: FakeTv, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "androidtvremote2", None)

    with pytest.raises(UnsupportedOperationError, match="tvremote"):
        channel.observe(HOST)

    assert tv.log == []


def test_the_channel_reports_whether_its_library_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    from control_tv.adapters.tv_remote import channel_installed

    assert channel_installed() is True
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert channel_installed() is False

    def broken(name: str) -> None:
        raise ValueError("broken module spec")

    monkeypatch.setattr(importlib.util, "find_spec", broken)
    assert channel_installed() is False


# Text and the app in front ---------------------------------------------------------------------
def test_the_app_in_front_is_reported_as_the_tv_said(channel: TvRemoteChannel, tv: FakeTv) -> None:
    tv.current_app = "com.example.tv"

    assert channel.observe(HOST).current_app == "com.example.tv"


def test_text_is_sent_once_to_the_active_field(channel: TvRemoteChannel, tv: FakeTv) -> None:
    tv.field_counter = 3

    entry = channel.type_text(HOST, "héllo wörld")

    assert tv.texts == ["héllo wörld"]
    assert entry.length == 11 and entry.field_reported is True
    assert tv.log == [("connect",), ("text", "11"), ("disconnect",)]


def test_text_without_a_reported_field_is_not_sent(channel: TvRemoteChannel, tv: FakeTv) -> None:
    entry = channel.type_text(HOST, "abc")

    assert tv.texts == []
    assert entry.field_reported is False


@pytest.mark.parametrize("text", ["", None, 5, "a" * 257, "line\nbreak", "tab\t", "\x7f"])
def test_malformed_text_is_refused_before_anything_is_sent(
    channel: TvRemoteChannel, tv: FakeTv, text: Any
) -> None:
    with pytest.raises(InvalidArgumentError) as excinfo:
        channel.type_text(HOST, text)

    assert tv.log == []
    if isinstance(text, str) and text:
        assert text not in str(excinfo.value)


def test_a_failure_while_typing_never_shows_the_text(channel: TvRemoteChannel, tv: FakeTv) -> None:
    tv.send_error = ConnectionClosed("lost while sending secret-123")

    with pytest.raises(DeviceUnavailableError) as excinfo:
        channel.type_text(HOST, "secret-123")

    assert "secret-123" not in str(excinfo.value)
    assert tv.texts == ["secret-123"]  # sent once, not resent
