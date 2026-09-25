from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from pychromecast import Chromecast, PyChromecastError, RequestTimeout
from pychromecast.controllers.media import MediaStatus as PyMediaStatus

from control_tv.adapters import PyChromecastTransport
from control_tv.domain import (
    CommandRejectedError,
    DeviceId,
    DeviceKind,
    DeviceNotFoundError,
    DeviceUnavailableError,
    DiscoveryError,
    MediaRequest,
    OperationTimeoutError,
    PlaybackState,
)

DEVICE_ID = DeviceId("12345678-1234-5678-1234-567812345678")
NOW = datetime(2026, 9, 25, 13, 0, tzinfo=UTC)


class FakeBrowser:
    def __init__(self) -> None:
        self.stopped = False

    def stop_discovery(self) -> None:
        self.stopped = True


class FakeReceiverController:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.updates = 0

    def update_status(self, *, callback_function: object) -> None:
        self.updates += 1
        if self.error is not None:
            raise self.error
        callback_function(True, {})  # type: ignore[operator]


class FakeMediaController:
    def __init__(self) -> None:
        self.status = PyMediaStatus()
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.error: Exception | None = None
        self.acknowledge_load = True

    def _call(self, name: str, *args: object, **kwargs: object) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append((name, args, kwargs))

    def play_media(self, url: str, content_type: str, **kwargs: object) -> None:
        self._call("load_media", url, content_type, **kwargs)
        if self.acknowledge_load:
            callback = kwargs["callback_function"]
            callback(True, {})  # type: ignore[operator]

    def play(self, *, timeout: float) -> None:
        self._call("play", timeout=timeout)

    def pause(self, *, timeout: float) -> None:
        self._call("pause", timeout=timeout)

    def stop(self, *, timeout: float) -> None:
        self._call("stop", timeout=timeout)

    def seek(self, position: float, *, timeout: float) -> None:
        self._call("seek", position, timeout=timeout)


class FakeCast:
    def __init__(self) -> None:
        self.uuid = UUID(str(DEVICE_ID))
        self.cast_info = SimpleNamespace(
            uuid=self.uuid,
            friendly_name="Living room",
            host="192.168.1.20",
            port=8009,
            cast_type="cast",
            model_name="Chromecast",
        )
        self.status = SimpleNamespace(
            app_id="CC1AD845",
            display_name="Default Media Receiver",
            volume_level=0.4,
            volume_muted=False,
            is_stand_by=False,
        )
        self.media_controller = FakeMediaController()
        self.receiver_controller = FakeReceiverController()
        self.socket_client = SimpleNamespace(receiver_controller=self.receiver_controller)
        self.wait_error: Exception | None = None
        self.wait_timeouts: list[float | None] = []
        self.volume_calls: list[tuple[float, float]] = []
        self.mute_calls: list[tuple[bool, float]] = []
        self.disconnect_calls: list[float | None] = []

    def wait(self, timeout: float | None = None) -> None:
        self.wait_timeouts.append(timeout)
        if self.wait_error is not None:
            raise self.wait_error

    def set_volume(self, level: float, *, timeout: float) -> float:
        self.volume_calls.append((level, timeout))
        return level

    def set_volume_muted(self, muted: bool, *, timeout: float) -> None:
        self.mute_calls.append((muted, timeout))

    def disconnect(self, timeout: float | None = None) -> None:
        self.disconnect_calls.append(timeout)


def make_transport(
    cast_device: FakeCast, browser: FakeBrowser | None = None
) -> tuple[PyChromecastTransport, FakeBrowser]:
    browser = browser or FakeBrowser()

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        assert timeout == 2.5
        return [cast(Chromecast, cast_device)], browser

    transport = PyChromecastTransport(
        connection_timeout=3.0,
        request_timeout=4.0,
        discoverer=discoverer,
        now=lambda: NOW,
    )
    transport.discover(timeout=2.5)
    return transport, browser


def test_discovery_maps_stable_identity_and_stops_browser() -> None:
    cast_device = FakeCast()
    transport, browser = make_transport(cast_device)

    devices = transport.discover(timeout=2.5)

    assert devices[0].id == DEVICE_ID
    assert devices[0].friendly_name == "Living room"
    assert devices[0].kind is DeviceKind.CAST
    assert devices[0].host == "192.168.1.20"
    assert browser.stopped is True


def test_discovery_failure_is_translated() -> None:
    def fail(timeout: float) -> tuple[list[Chromecast], object]:
        raise OSError("mDNS unavailable")

    transport = PyChromecastTransport(discoverer=fail)

    with pytest.raises(DiscoveryError, match="mDNS unavailable"):
        transport.discover(timeout=1.0)


def test_unknown_device_never_connects() -> None:
    transport = PyChromecastTransport()

    with pytest.raises(DeviceNotFoundError) as excinfo:
        transport.get_status(DEVICE_ID)

    assert excinfo.value.device_id == DEVICE_ID


def test_status_is_fresh_receiver_and_media_snapshot() -> None:
    cast_device = FakeCast()
    media = cast_device.media_controller.status
    media.player_state = "PLAYING"
    media.content_id = "https://media.local/movie.mp4"
    media.content_type = "video/mp4"
    media.current_time = 12.0
    media.duration = 90.0
    media.media_metadata = {"title": "Movie"}
    media.supported_media_commands = 2
    transport, _ = make_transport(cast_device)

    status = transport.get_status(DEVICE_ID)

    assert status.observed_at == NOW
    assert status.receiver is not None
    assert status.receiver.volume_level == 0.4
    assert status.media is not None
    assert status.media.playback_state is PlaybackState.PLAYING
    assert status.media.content_id == "https://media.local/movie.mp4"
    assert status.media.title == "Movie"
    assert cast_device.receiver_controller.updates == 1
    assert cast_device.wait_timeouts[-1] == 3.0


def test_idle_status_without_content_has_no_media_session() -> None:
    cast_device = FakeCast()
    transport, _ = make_transport(cast_device)

    assert transport.get_status(DEVICE_ID).media is None


def test_all_commands_use_bounded_library_calls() -> None:
    cast_device = FakeCast()
    transport, _ = make_transport(cast_device)
    request = MediaRequest(url="https://media.local/movie.mp4", content_type="video/mp4")

    transport.load_media(DEVICE_ID, request)
    transport.play(DEVICE_ID)
    transport.pause(DEVICE_ID)
    transport.stop(DEVICE_ID)
    transport.seek(DEVICE_ID, 25.0)
    transport.set_volume(DEVICE_ID, 0.7)
    transport.set_muted(DEVICE_ID, True)

    calls = cast_device.media_controller.calls
    assert [call[0] for call in calls] == ["load_media", "play", "pause", "stop", "seek"]
    assert calls[0][2]["title"] is None
    assert calls[1][2]["timeout"] == 4.0
    assert calls[4][1] == (25.0,)
    assert cast_device.volume_calls == [(0.7, 4.0)]
    assert cast_device.mute_calls == [(True, 4.0)]


def test_load_waits_for_delivery_acknowledgement() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.acknowledge_load = False
    transport, _ = make_transport(cast_device)
    transport._request_timeout = 0.0

    with pytest.raises(OperationTimeoutError, match="load media"):
        transport.load_media(
            DEVICE_ID,
            MediaRequest(url="https://media.local/movie.mp4", content_type="video/mp4"),
        )


def test_connection_timeout_is_translated_with_device_context() -> None:
    cast_device = FakeCast()
    cast_device.wait_error = RequestTimeout("connect", 3.0)
    transport, _ = make_transport(cast_device)

    with pytest.raises(OperationTimeoutError) as excinfo:
        transport.play(DEVICE_ID)

    assert excinfo.value.device_id == DEVICE_ID


def test_socket_failure_is_device_unavailable() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.error = OSError("network down")
    transport, _ = make_transport(cast_device)

    with pytest.raises(DeviceUnavailableError, match="network down"):
        transport.pause(DEVICE_ID)


def test_library_rejection_is_command_rejected() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.error = PyChromecastError("receiver rejected command")
    transport, _ = make_transport(cast_device)

    with pytest.raises(CommandRejectedError, match="receiver rejected"):
        transport.stop(DEVICE_ID)


def test_close_disconnects_and_forgets_cached_devices() -> None:
    cast_device = FakeCast()
    transport, _ = make_transport(cast_device)

    transport.close()

    assert cast_device.disconnect_calls == [3.0]
    with pytest.raises(DeviceNotFoundError):
        transport.play(DEVICE_ID)
