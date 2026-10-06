"""MCP delivery wording for failures raised inside PyChromecast's real send path.

Nothing here injects the domain error the MCP mapping expects. A real PyChromecast
`SocketClient` (its constructor opens no network connection; its thread is never started)
and its real `MediaController` are driven by the real `PyChromecastTransport`, the real
`ControlService` and the real bridge dispatch. Only the socket object and the running
application's namespaces are simulated, so the library itself produces the failure, the
shared adapter classifies it, and the MCP answer is checked at the end of that chain.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import anyio
import pytest
from mcp import types
from pychromecast import Chromecast
from pychromecast.controllers.media import MediaController
from pychromecast.socket_client import SocketClient

from control_tv.adapters import PyChromecastTransport
from control_tv.mcp_server import ControlTvTools
from control_tv.service import ControlService

DEVICE_ID = "12345678-1234-5678-1234-567812345678"
MEDIA_NAMESPACE = "urn:x-cast:com.google.cast.media"
DESTINATION = "receiver-transport"


class BreakingSocket:
    """A connected socket whose write fails after the bytes were handed to it."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.writes.append(data)
        raise OSError("connection reset while writing")


class NamespacesChangingAfterFirstCheck(list[str]):
    """The running application's namespaces, replaced by the socket thread between the
    controller's check and the socket client's check (a receiver status update)."""

    def __init__(self) -> None:
        super().__init__([MEDIA_NAMESPACE])
        self.checks = 0

    def __contains__(self, item: object) -> bool:
        self.checks += 1
        return self.checks == 1 and super().__contains__(item)


@pytest.fixture
def socket_client() -> Iterator[SocketClient]:
    client = SocketClient(
        cast_type="cast", tries=1, timeout=1.0, retry_wait=1.0, services=set(), zconf=None
    )
    client.connecting = False
    client.destination_id = DESTINATION
    client.session_id = "session-1"
    client._open_channels.append(DESTINATION)  # no CONNECT message before the command
    client.media_controller.status.media_session_id = 7
    yield client
    for end in client.socketpair:
        end.close()
    client.selector.close()


def mcp_over_real_adapter(socket_client: SocketClient) -> ControlTvTools:
    media_controller: MediaController = socket_client.media_controller
    cast_device = SimpleNamespace(
        uuid=UUID(DEVICE_ID),
        cast_info=SimpleNamespace(
            uuid=UUID(DEVICE_ID),
            friendly_name="Living room",
            host="192.168.1.20",
            port=8009,
            cast_type="cast",
            model_name="Chromecast",
        ),
        media_controller=media_controller,
        socket_client=socket_client,
        wait=lambda timeout=None: None,
        disconnect=lambda timeout=None: None,
    )

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        return [cast(Chromecast, cast_device)], SimpleNamespace(stop_discovery=lambda: None)

    transport = PyChromecastTransport(request_timeout=0.5, discoverer=discoverer)
    # Verification off: no status read before or after, so the command is the only traffic.
    tools = ControlTvTools(ControlService(transport, confirm_timeout=None))
    discovered = anyio.run(tools.call, "discover_devices", {})
    assert discovered.isError is False
    return tools


def answer(result: types.CallToolResult) -> tuple[dict[str, Any], str]:
    assert result.structuredContent is not None
    [block] = result.content
    assert isinstance(block, types.TextContent)
    return result.structuredContent, block.text


def test_a_socket_write_failure_after_bytes_left_is_reported_as_possibly_delivered(
    socket_client: SocketClient,
) -> None:
    socket = BreakingSocket()
    socket_client.socket = cast(Any, socket)
    socket_client.app_namespaces = [MEDIA_NAMESPACE]
    tools = mcp_over_real_adapter(socket_client)

    result = anyio.run(tools.call, "pause", {"deviceId": DEVICE_ID})

    body, text = answer(result)
    # PyChromecast caught the OSError, failed the request; the adapter made it
    # device_unavailable. The command bytes had already been handed to the socket.
    assert len(socket.writes) == 1 and b"PAUSE" in socket.writes[0]
    assert result.isError is True
    assert (body["error"]["code"], body["delivery"]) == ("device_unavailable", "unknown")
    assert "may or may not have reached the TV" in text
    assert "Do not resend it automatically" in text and "get_status" in text
    assert "Nothing was sent" not in text
    assert "192.168.1.20" not in text + str(body)


def test_a_library_error_raised_before_writing_never_claims_the_tv_received_it(
    socket_client: SocketClient,
) -> None:
    socket = BreakingSocket()
    socket_client.socket = cast(Any, socket)
    socket_client.app_namespaces = NamespacesChangingAfterFirstCheck()
    tools = mcp_over_real_adapter(socket_client)

    result = anyio.run(tools.call, "pause", {"deviceId": DEVICE_ID})

    body, text = answer(result)
    # UnsupportedNamespace was raised by the socket client before any write; the shared
    # adapter maps it to command_rejected, which therefore does not prove receipt.
    assert socket.writes == []
    assert (body["error"]["code"], body["delivery"]) == ("command_rejected", "unknown")
    assert "received the command" not in text
    assert "The command reached the TV" not in text and "Nothing was sent" not in text
    assert "may or may not have reached the TV" in text
    assert "Do not resend it automatically" in text


def test_each_failed_command_reaches_the_library_once_and_is_never_replayed(
    socket_client: SocketClient,
) -> None:
    socket = BreakingSocket()
    socket_client.socket = cast(Any, socket)
    socket_client.app_namespaces = [MEDIA_NAMESPACE]
    tools = mcp_over_real_adapter(socket_client)

    first = anyio.run(tools.call, "pause", {"deviceId": DEVICE_ID})
    after_first = len(socket.writes)
    socket_client._force_recon = False  # the library flags a reconnect after the failure
    second = anyio.run(tools.call, "stop", {"deviceId": DEVICE_ID})

    assert first.isError and second.isError
    assert after_first == 1  # one MCP call, one write attempt, no retry
    assert len(socket.writes) == 2 and b"STOP" in socket.writes[1]  # the next call only
