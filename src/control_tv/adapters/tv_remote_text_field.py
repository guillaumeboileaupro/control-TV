"""The TV's active text field, as the TV itself announces it (androidtvremote2 0.3.2).

The library addresses text with the counters of the TV's last edit message. A TV applies text only
to the field it announces as active, in its input-method key-inject or show request. Observed on
2026-11-07 with the owner's authorization: ControlTV Receiver's session-code field stayed empty
twice with the library's `send_text`, and was filled once the edit named the announced field.

Imported only where the real library is used; the remote adapter reads the field through
`RemoteClient.active_text_field` and writes through `RemoteClient.send_text_to_field`.
"""

from __future__ import annotations

from androidtvremote2 import AndroidTVRemote
from androidtvremote2 import remote as library
from androidtvremote2.remotemessage_pb2 import (
    RemoteEditInfo,
    RemoteImeBatchEdit,
    RemoteImeObject,
    RemoteMessage,
)
from google.protobuf.message import DecodeError

# Set on the library's protocol object, per connection.
FIELD_ATTRIBUTE = "control_tv_text_field"


def announced_field(raw: bytes) -> tuple[bool, int | None]:
    """Whether `raw` announces the TV's text field, and that field (None: no field active)."""
    message = RemoteMessage()
    try:
        message.ParseFromString(raw)
    except DecodeError:
        return False, None
    if message.HasField("remote_ime_key_inject"):
        status = message.remote_ime_key_inject.text_field_status
    elif message.HasField("remote_ime_show_request"):
        status = message.remote_ime_show_request.remote_text_field_status
    else:
        return False, None
    return True, status.counter_field if status.counter_field > 0 else None


_library_handler = library.RemoteProtocol._handle_message


def _handle_message(self: library.RemoteProtocol, raw_msg: bytes) -> None:
    announces, field = announced_field(raw_msg)
    if announces:
        setattr(self, FIELD_ATTRIBUTE, field)
    _library_handler(self, raw_msg)


# Every connection records its announced field, including the announcement sent while it connects.
library.RemoteProtocol._handle_message = _handle_message  # type: ignore[method-assign]


class TextFieldRemote(AndroidTVRemote):
    """The library's client, addressing text to the field the TV announced as active."""

    @property
    def active_text_field(self) -> int | None:
        field = getattr(self._remote_message_protocol, FIELD_ATTRIBUTE, None)
        return field if isinstance(field, int) and not isinstance(field, bool) else None

    def send_text_to_field(self, text: str, field: int) -> None:
        """Replace the content of `field` with `text`, once. The text is never logged."""
        protocol = self._remote_message_protocol
        if protocol is None:
            raise ConnectionError("the TV remote is not connected")
        end = len(text) - 1
        edit = RemoteEditInfo(
            insert=1, text_field_status=RemoteImeObject(start=end, end=end, value=text)
        )
        message = RemoteMessage()
        message.remote_ime_batch_edit.CopyFrom(
            RemoteImeBatchEdit(
                ime_counter=protocol.ime_counter, field_counter=field, edit_info=[edit]
            )
        )
        protocol._send_message(message, should_debug_log=False)
