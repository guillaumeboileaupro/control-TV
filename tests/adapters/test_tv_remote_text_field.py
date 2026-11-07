"""Tests for the TV's announced text field, on the real androidtvremote2 messages (no TV)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from androidtvremote2.remotemessage_pb2 import RemoteMessage

from control_tv.adapters import tv_remote_text_field as text_field


def key_inject(field: int) -> bytes:
    message = RemoteMessage()
    message.remote_ime_key_inject.app_info.app_package = "io.example.receiver"
    message.remote_ime_key_inject.text_field_status.counter_field = field
    return bytes(message.SerializeToString())


def show_request(field: int) -> bytes:
    message = RemoteMessage()
    message.remote_ime_show_request.remote_text_field_status.counter_field = field
    return bytes(message.SerializeToString())


def test_the_field_the_tv_announces_is_read_from_its_key_inject_and_show_request() -> None:
    assert text_field.announced_field(key_inject(23)) == (True, 23)
    assert text_field.announced_field(show_request(26)) == (True, 26)
    assert text_field.announced_field(key_inject(0)) == (True, None)  # no field active now


def test_other_messages_and_garbage_announce_nothing() -> None:
    edit = RemoteMessage()
    edit.remote_ime_batch_edit.field_counter = 1  # the library's counter, not the TV's field
    assert text_field.announced_field(edit.SerializeToString()) == (False, None)
    assert text_field.announced_field(b"\xff\xff not a message") == (False, None)


def test_each_connection_records_its_announced_field_then_the_library_handles_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled: list[bytes] = []
    monkeypatch.setattr(text_field, "_library_handler", lambda self, raw: handled.append(raw))
    protocol = SimpleNamespace()

    text_field._handle_message(protocol, key_inject(23))  # type: ignore[arg-type]
    text_field._handle_message(protocol, RemoteMessage().SerializeToString())  # type: ignore[arg-type]

    assert getattr(protocol, text_field.FIELD_ATTRIBUTE) == 23  # kept until announced again
    assert len(handled) == 2
    text_field._handle_message(protocol, key_inject(0))  # type: ignore[arg-type]
    assert getattr(protocol, text_field.FIELD_ATTRIBUTE) is None


class RecordingProtocol:
    ime_counter = 4

    def __init__(self) -> None:
        self.sent: list[tuple[RemoteMessage, bool]] = []

    def _send_message(self, message: RemoteMessage, should_debug_log: bool = True) -> None:
        self.sent.append((message, should_debug_log))


def remote_with(protocol: object) -> text_field.TextFieldRemote:
    async def build() -> text_field.TextFieldRemote:
        return text_field.TextFieldRemote("control-TV", "cert.pem", "key.pem", "192.0.2.10")

    remote = asyncio.run(build())
    remote._remote_message_protocol = protocol  # type: ignore[assignment]
    return remote


def test_text_is_addressed_to_the_announced_field_and_never_logged() -> None:
    protocol = RecordingProtocol()
    setattr(protocol, text_field.FIELD_ATTRIBUTE, 23)
    remote = remote_with(protocol)

    assert remote.active_text_field == 23
    remote.send_text_to_field("ctv1|secret", 23)

    ((message, logged),) = protocol.sent
    edit = message.remote_ime_batch_edit
    assert (edit.field_counter, edit.ime_counter) == (23, 4)
    assert edit.edit_info[0].text_field_status.value == "ctv1|secret"
    assert logged is False  # the session code never reaches the library's debug log


def test_without_a_connection_or_an_announcement_there_is_no_field() -> None:
    assert remote_with(SimpleNamespace()).active_text_field is None
    flagged = SimpleNamespace(**{text_field.FIELD_ATTRIBUTE: True})
    assert remote_with(flagged).active_text_field is None  # a bool is not a field
    with pytest.raises(ConnectionError):
        remote_with(None).send_text_to_field("abc", 1)
