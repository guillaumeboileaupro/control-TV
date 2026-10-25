"""Every key the remote sends is a key code the TV's remote protocol knows.

`androidtvremote2` sends a key by its name in the protocol's `RemoteKeyCode` enum; a name outside it
would only fail on the real TV. This pins every `RemoteKey`, including the Google TV keys added on
2026-10-25, to that enum.
"""

from __future__ import annotations

import pytest

from control_tv.domain.remote import RemoteKey

remotemessage = pytest.importorskip("androidtvremote2.remotemessage_pb2")


@pytest.mark.parametrize("key", list(RemoteKey), ids=lambda key: key.name)
def test_each_remote_key_is_a_key_code_of_the_tv_protocol(key: RemoteKey) -> None:
    assert remotemessage.RemoteKeyCode.Value(key.value) > 0  # raises on an unknown name


def test_the_speaker_mute_is_sent_never_the_microphone_mute() -> None:
    assert RemoteKey.VOLUME_MUTE.value == "KEYCODE_VOLUME_MUTE"
    assert "KEYCODE_MUTE" not in {key.value for key in RemoteKey}
