"""The Android TV Remote channel (`control_tv.adapters.tv_remote`), over a fake remote.

No network: the library's remote object is replaced by `FakeRemote`, which records every
call. The rules pinned: private credentials, an allowlist of keys, one connection per
operation that is always closed, a key sent exactly once even when it fails afterwards,
the TV's reports returned as observed, explicit pairing, and no TV address in any error.
"""

from __future__ import annotations

import asyncio
import stat
import time
from collections.abc import Callable, Iterator
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from androidtvremote2 import CannotConnect, ConnectionClosed, InvalidAuth

from control_tv.adapters.tv_remote import CERT_FILE, IDENTITY_FILE, KEY_FILE, TvRemoteChannel
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
        # Seconds after Power until the TV reports its new power state; None: never reports it.
        self.power_report_delay: float | None = None
        self.paired_code = "A1B2C3"
        self.connect_error: Exception | None = None
        self.send_error: Exception | None = None
        self.connect_delay = 0.0
        self.current_app = ""
        self.field_counter = 0
        self.texts: list[str] = []
        self.log: list[tuple[str, ...]] = []
        self.remotes: list[FakeRemote] = []
        # Whether a connection stays open like the library's (a live transport and a pending
        # "connection lost" future); by default the fake looks closed, so every call connects.
        self.keeps_open = False
        self.opens_launched_app = True


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
        if self.tv.keeps_open:
            self._remote_message_protocol.on_con_lost = asyncio.get_running_loop().create_future()
            self._remote_message_protocol.transport = SimpleNamespace(is_closing=lambda: False)

    def lose_connection(self) -> None:
        """The library noticed the connection was lost (TV gone, idle timeout)."""
        self._remote_message_protocol.on_con_lost.set_result(None)

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
        if key_code == "KEYCODE_POWER" and self.tv.power_report_delay is not None:
            asyncio.get_running_loop().call_later(self.tv.power_report_delay, self._power_toggled)

    def _power_toggled(self) -> None:
        self.tv.is_on = not self.tv.is_on
        self.is_on = self.tv.is_on

    def send_launch_app_command(self, app_link_or_app_id: str) -> None:
        self.tv.log.append(("launch", app_link_or_app_id))
        if self.tv.send_error is not None:
            raise self.tv.send_error
        if self.tv.opens_launched_app:
            self.current_app = app_link_or_app_id

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
        assert client_name == "control-TV 01234567"
        tv.remotes.append(FakeRemote(tv, certfile, keyfile, host))
        return tv.remotes[-1]

    made = TvRemoteChannel(
        tmp_path / "remote",
        timeout=2.0,
        settle=0.2,
        power_settle=0.5,
        remote_factory=factory,
        cert_generator=lambda name: (CERT_PEM, KEY_PEM),
        identity_generator=lambda: "01234567",
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
    assert sorted(p.name for p in folder.iterdir()) == sorted([CERT_FILE, IDENTITY_FILE, KEY_FILE])
    assert mode(folder / IDENTITY_FILE) == 0o600
    assert (folder / IDENTITY_FILE).read_text(encoding="utf-8") == "control-TV 01234567"
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


def test_existing_pairing_gets_a_stable_distinct_name_without_replacing_credentials(
    tmp_path: Path, tv: FakeTv
) -> None:
    folder = tmp_path / "remote"
    folder.mkdir(mode=0o700)
    (folder / CERT_FILE).write_bytes(CERT_PEM)
    (folder / KEY_FILE).write_bytes(KEY_PEM)
    names: list[str] = []

    def factory(client_name: str, certfile: str, keyfile: str, host: str) -> FakeRemote:
        names.append(client_name)
        return FakeRemote(tv, certfile, keyfile, host)

    def refuse_certificate(name: str) -> tuple[bytes, bytes]:
        raise AssertionError("existing credentials must be preserved")

    for _ in range(2):
        channel = TvRemoteChannel(
            folder,
            settle=0.0,
            remote_factory=factory,
            cert_generator=refuse_certificate,
            identity_generator=lambda: "ffffffff",
        )
        try:
            channel.observe(HOST)
        finally:
            channel.close()

    expected = f"control-TV {sha256(CERT_PEM).hexdigest()[:8]}"
    assert names == [expected, expected]
    assert (folder / IDENTITY_FILE).read_text(encoding="utf-8") == expected
    assert (folder / CERT_FILE).read_bytes() == CERT_PEM
    assert (folder / KEY_FILE).read_bytes() == KEY_PEM


def test_two_installations_generate_distinct_persistent_pairing_names(
    tmp_path: Path, tv: FakeTv
) -> None:
    names: list[str] = []

    def factory(client_name: str, certfile: str, keyfile: str, host: str) -> FakeRemote:
        names.append(client_name)
        return FakeRemote(tv, certfile, keyfile, host)

    def identity_generator(suffix: str) -> Callable[[], str]:
        def identity() -> str:
            return suffix

        return identity

    for directory, suffix in (("ubuntu", "11111111"), ("android", "22222222")):
        channel = TvRemoteChannel(
            tmp_path / directory,
            settle=0.0,
            remote_factory=factory,
            cert_generator=lambda name: (CERT_PEM, KEY_PEM),
            identity_generator=identity_generator(suffix),
        )
        try:
            channel.observe(HOST)
        finally:
            channel.close()

    assert names == ["control-TV 11111111", "control-TV 22222222"]


def test_invalid_generated_identity_is_reported_before_pairing(tmp_path: Path, tv: FakeTv) -> None:
    channel = TvRemoteChannel(
        tmp_path / "remote",
        settle=0.0,
        remote_factory=lambda *args: FakeRemote(tv, *args[1:]),
        cert_generator=lambda name: (CERT_PEM, KEY_PEM),
        identity_generator=lambda: "not-an-identity",
    )
    try:
        with pytest.raises(RuntimeError, match="generated remote identity is invalid"):
            channel.observe(HOST)
    finally:
        channel.close()


def test_unreadable_identity_is_recovered_from_existing_certificate(
    tmp_path: Path, tv: FakeTv, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "remote"
    folder.mkdir(mode=0o700)
    cert = folder / CERT_FILE
    cert.write_bytes(CERT_PEM)
    (folder / KEY_FILE).write_bytes(KEY_PEM)
    identity_file = folder / IDENTITY_FILE
    identity_file.write_bytes(b"old")
    read_text = Path.read_text

    def unreadable(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if path == identity_file:
            raise OSError("unreadable")
        return read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", unreadable)
    channel = TvRemoteChannel(
        folder,
        settle=0.0,
        remote_factory=lambda *args: FakeRemote(tv, *args[1:]),
        cert_generator=lambda name: (CERT_PEM, KEY_PEM),
    )
    try:
        channel.observe(HOST)
    finally:
        channel.close()
    monkeypatch.undo()

    assert identity_file.read_text(encoding="utf-8") == (
        f"control-TV {sha256(CERT_PEM).hexdigest()[:8]}"
    )


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


def test_power_waits_for_the_tv_to_report_its_new_power_state(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.is_on = False
    tv.power_report_delay = 0.25  # later than the volume settle, within the power one

    pressed = channel.press(HOST, RemoteKey.POWER)

    assert (pressed.before.is_on, pressed.after.is_on) == (False, True)
    assert [entry for entry in tv.log if entry[0] == "key"] == [("key", "KEYCODE_POWER")]


def test_power_without_a_new_report_returns_what_the_tv_last_said(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.is_on = False

    pressed = channel.press(HOST, RemoteKey.POWER)

    assert (pressed.before.is_on, pressed.after.is_on) == (False, False)


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


def test_the_allowlist_is_exactly_the_keys_of_a_google_tv_remote() -> None:
    # Owner decision of 2026-10-25: the keys of a Google TV remote, including its Assistant key;
    # still no microphone mute (KEYCODE_MUTE) and no free launch or text key.
    digits = {f"DIGIT_{n}" for n in range(10)}
    assert {key.name for key in RemoteKey} == digits | {
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
        "INFO",
        "GUIDE",
        "TV_INPUT",
        "CAPTIONS",
        "AUDIO_TRACK",
        "SETTINGS",
        "CHANNEL_UP",
        "CHANNEL_DOWN",
        "RED",
        "GREEN",
        "YELLOW",
        "BLUE",
        "PLAY_PAUSE",
        "STOP",
        "REWIND",
        "FAST_FORWARD",
        "PREVIOUS",
        "NEXT",
        "RECORD",
        "ASSISTANT",
    }
    assert "KEYCODE_MUTE" not in {key.value for key in RemoteKey}


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
    tv.field_counter = 1
    tv.send_error = ConnectionClosed("lost while sending secret-123")

    with pytest.raises(DeviceUnavailableError) as excinfo:
        channel.type_text(HOST, "secret-123")

    assert "secret-123" not in str(excinfo.value)
    assert tv.texts == ["secret-123"]  # sent once, not resent


# One open connection per TV -------------------------------------------------------------------


def run_on(channel: TvRemoteChannel, action: Callable[[], None]) -> None:
    """Run `action` on the channel's event loop, as the library's own callbacks would."""

    async def call() -> None:
        action()

    asyncio.run_coroutine_threadsafe(call(), channel._event_loop()).result(1)


def factory_for(tv: FakeTv) -> Callable[[str, str, str, str], FakeRemote]:
    def factory(client_name: str, certfile: str, keyfile: str, host: str) -> FakeRemote:
        tv.remotes.append(FakeRemote(tv, certfile, keyfile, host))
        return tv.remotes[-1]

    return factory


def test_keys_in_a_row_share_one_open_connection_closed_with_the_channel(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.keeps_open = True

    channel.press(HOST, RemoteKey.DPAD_UP)
    channel.press(HOST, RemoteKey.DPAD_CENTER)
    observed = channel.observe(HOST)

    assert tv.log == [("connect",), ("key", "KEYCODE_DPAD_UP"), ("key", "KEYCODE_DPAD_CENTER")]
    assert observed.is_on is True
    channel.close()
    assert tv.log[-1] == ("disconnect",)
    assert len(tv.remotes) == 1


def test_a_lost_connection_is_never_reused(channel: TvRemoteChannel, tv: FakeTv) -> None:
    tv.keeps_open = True
    channel.press(HOST, RemoteKey.HOME)

    run_on(channel, tv.remotes[0].lose_connection)
    channel.press(HOST, RemoteKey.BACK)

    assert tv.log == [
        ("connect",),
        ("key", "KEYCODE_HOME"),
        ("disconnect",),
        ("connect",),
        ("key", "KEYCODE_BACK"),
    ]


def test_a_failed_call_closes_its_connection_and_the_next_one_connects_again(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.keeps_open = True
    tv.send_error = ConnectionClosed("lost")
    with pytest.raises(DeviceUnavailableError):
        channel.press(HOST, RemoteKey.HOME)
    tv.send_error = None

    channel.press(HOST, RemoteKey.HOME)

    assert [entry[0] for entry in tv.log] == ["connect", "key", "disconnect", "connect", "key"]
    assert [entry for entry in tv.log if entry[0] == "key"] == [("key", "KEYCODE_HOME")] * 2


def test_an_unused_connection_is_closed_after_its_keep_open_time(
    tmp_path: Path, tv: FakeTv
) -> None:
    tv.keeps_open = True
    channel = TvRemoteChannel(
        tmp_path / "remote",
        settle=0.2,
        keep_open=0.1,
        remote_factory=factory_for(tv),
        cert_generator=lambda name: (CERT_PEM, KEY_PEM),
    )
    try:
        channel.press(HOST, RemoteKey.HOME)
        time.sleep(0.4)
        assert tv.log == [("connect",), ("key", "KEYCODE_HOME"), ("disconnect",)]
    finally:
        channel.close()
    assert tv.remotes[0].disconnected == 1


def test_pairing_closes_the_open_connection(channel: TvRemoteChannel, tv: FakeTv) -> None:
    tv.keeps_open = True
    channel.press(HOST, RemoteKey.HOME)

    channel.start_pairing(HOST)

    assert tv.remotes[0].disconnected == 1


def test_a_volume_key_on_a_tv_that_reports_no_volume_does_not_wait_for_one(
    tmp_path: Path, tv: FakeTv
) -> None:
    tv.keeps_open = True
    tv.volume = None
    channel = TvRemoteChannel(
        tmp_path / "remote",
        settle=1.0,
        remote_factory=factory_for(tv),
        cert_generator=lambda name: (CERT_PEM, KEY_PEM),
    )
    try:
        channel.press(HOST, RemoteKey.VOLUME_UP)  # a new connection: one settle at most
        started = time.monotonic()
        pressed = channel.press(HOST, RemoteKey.VOLUME_UP)  # reused: no wait at all
        assert time.monotonic() - started < 0.5
    finally:
        channel.close()
    assert pressed.before.volume is None and pressed.after.volume is None


# The app in front, read only; no app is ever opened by its package from here -------------------


def test_the_app_in_front_is_read_without_sending_anything(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.current_app = "tv.arte.plus7"

    assert channel.front_app(HOST, "tv.arte.plus7") == "tv.arte.plus7"
    assert [entry for entry in tv.log if entry[0] in ("key", "launch", "text")] == []


def test_a_tv_that_does_not_report_the_app_returns_what_it_said(
    channel: TvRemoteChannel, tv: FakeTv
) -> None:
    tv.current_app = "com.google.android.tvlauncher"

    assert channel.front_app(HOST, "tv.arte.plus7") == "com.google.android.tvlauncher"


def test_the_market_launch_link_this_google_tv_refuses_is_gone() -> None:
    assert not hasattr(TvRemoteChannel, "launch")


LINK = "controltv-receiver://apps?reply=http://192.0.2.20:40123/apps&token=" + "a" * 32
LAUNCH = (
    "controltv-receiver://launch?package=tv.arte.plus7"
    "&reply=http://192.0.2.20:40123/launch&token=" + "a" * 32
)


def test_the_receivers_launch_link_is_opened_once(channel: TvRemoteChannel, tv: FakeTv) -> None:
    channel.open_receiver_link(HOST, LAUNCH)

    assert [entry for entry in tv.log if entry[0] == "launch"] == [("launch", LAUNCH)]


def test_the_receivers_app_list_link_is_opened_once(channel: TvRemoteChannel, tv: FakeTv) -> None:
    channel.open_receiver_link(HOST, LINK)

    assert [entry for entry in tv.log if entry[0] == "launch"] == [("launch", LINK)]


@pytest.mark.parametrize(
    "link",
    [
        "https://example.com/",
        "market://launch?id=com.netflix.ninja",
        "com.netflix.ninja",
        LINK.replace("/apps&", "/other&"),
        LAUNCH.replace("/launch&", "/apps&"),
        LAUNCH.replace("tv.arte.plus7", "not a package"),
        LAUNCH.replace("package=tv.arte.plus7&", ""),
        LINK.replace("a" * 32, "short"),
        LINK + "&extra=1",
        None,
    ],
)
def test_no_other_link_is_ever_opened(channel: TvRemoteChannel, tv: FakeTv, link: Any) -> None:
    with pytest.raises(InvalidArgumentError):
        channel.open_receiver_link(HOST, link)
    assert tv.log == []
