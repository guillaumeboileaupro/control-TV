from __future__ import annotations

import importlib.metadata
import math
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pychromecast.response_handler
import pytest
from pychromecast import Chromecast, PyChromecastError, RequestTimeout
from pychromecast.controllers.media import MediaController
from pychromecast.controllers.media import MediaStatus as PyMediaStatus
from pychromecast.error import RequestFailed, UnsupportedNamespace
from pychromecast.generated.cast_channel_pb2 import CastMessage
from pychromecast.socket_client import SocketClient

import control_tv.adapters.pychromecast as pychromecast_adapter
from control_tv.adapters import PyChromecastTransport
from control_tv.domain import (
    CommandRejectedError,
    Confirmation,
    DeviceId,
    DeviceKind,
    DeviceNotFoundError,
    DeviceUnavailableError,
    DiscoveryError,
    InvalidArgumentError,
    MediaRequest,
    MediaStatus,
    MetadataType,
    OperationTimeoutError,
    PlaybackState,
    StreamType,
    UnsupportedOperationError,
)
from control_tv.service import ControlService
from fakes import FakeClock

DEVICE_ID = DeviceId("12345678-1234-5678-1234-567812345678")
NOW = datetime(2026, 9, 25, 13, 0, tzinfo=UTC)


class FakeBrowser:
    def __init__(self) -> None:
        self.stopped = False
        self.stop_calls = 0
        self.stop_error: Exception | None = None

    def stop_discovery(self) -> None:
        self.stopped = True
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error


class FakeReceiverController:
    def __init__(self, clock: FakeClock | None = None, consumes: float = 0.0) -> None:
        self.error: Exception | None = None
        self.updates = 0
        self.launched: list[str] = []
        self._clock = clock
        self._consumes = consumes

    def update_status(self, *, callback_function: object) -> None:
        self.updates += 1
        if self._clock is not None and self._consumes:
            self._clock.sleep(self._consumes)
        if self.error is not None:
            raise self.error
        callback_function(True, {})  # type: ignore[operator]

    def launch_app(self, app_id: str, **kwargs: object) -> None:
        """Recorded, never performed: a status read must not start an application."""
        self.launched.append(app_id)
        callback = kwargs.get("callback_function")
        if callback is not None:
            callback(False, None)  # type: ignore[operator]


class ControlledMediaStatus(PyMediaStatus):
    def __init__(self, adjusted: float) -> None:
        super().__init__()
        self._adjusted = adjusted

    @property
    def adjusted_current_time(self) -> float:
        value = self._adjusted
        self._adjusted += 1.0
        return value


class FakeMediaController:
    def __init__(self) -> None:
        self.status = PyMediaStatus()
        # What the receiver answers on the media channel; empty means no media session.
        self.reported: list[dict[str, object]] = []
        self.status_requests = 0
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.error: Exception | None = None
        self.acknowledge_load = True
        self.load_sent = True
        self.load_response: dict[str, object] = {"type": "MEDIA_STATUS"}

    def _call(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))
        if self.error is not None:
            raise self.error

    def play_media(self, url: str, content_type: str, **kwargs: object) -> None:
        self._call("load_media", url, content_type, **kwargs)
        if self.acknowledge_load:
            callback = kwargs["callback_function"]
            callback(self.load_sent, self.load_response)  # type: ignore[operator]

    def send_message_nocheck(
        self,
        data: dict[str, object],
        *,
        callback_function: Callable[[bool, dict[str, object] | None], None],
    ) -> None:
        assert data == {"type": "GET_STATUS"}
        self.status_requests += 1
        callback_function(True, {"type": "MEDIA_STATUS", "status": list(self.reported)})

    def play(self, *, timeout: float) -> None:
        self._call("play", timeout=timeout)

    def pause(self, *, timeout: float) -> None:
        self._call("pause", timeout=timeout)

    def stop(self, *, timeout: float) -> None:
        self._call("stop", timeout=timeout)

    def seek(self, position: float, *, timeout: float) -> None:
        self._call("seek", position, timeout=timeout)


class FakeCast:
    def __init__(
        self,
        *,
        clock: FakeClock | None = None,
        wait_consumes: float = 0.0,
        disconnect_consumes: float = 0.0,
        browser: FakeBrowser | None = None,
    ) -> None:
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
        self.receiver_controller = FakeReceiverController(clock=clock)
        self.socket_client = SimpleNamespace(receiver_controller=self.receiver_controller)
        self.wait_error: Exception | None = None
        self.wait_timeouts: list[float | None] = []
        self.volume_calls: list[tuple[float, float]] = []
        self.mute_calls: list[tuple[bool, float]] = []
        self.disconnect_calls: list[float | None] = []
        self.disconnect_error: Exception | None = None
        self._browser = browser
        self._clock = clock
        self._wait_consumes = wait_consumes
        self._disconnect_consumes = disconnect_consumes

    def wait(self, timeout: float | None = None) -> None:
        self.wait_timeouts.append(timeout)
        if self._clock is not None and self._wait_consumes:
            self._clock.sleep(self._wait_consumes)
        if self.wait_error is not None:
            raise self.wait_error
        if self._browser is not None and self._browser.stopped:
            raise OSError("zeroconf context stopped before connection")

    def set_volume(self, level: float, *, timeout: float) -> float:
        self.volume_calls.append((level, timeout))
        return level

    def set_volume_muted(self, muted: bool, *, timeout: float) -> None:
        self.mute_calls.append((muted, timeout))

    def disconnect(self, timeout: float | None = None) -> None:
        self.disconnect_calls.append(timeout)
        if self._clock is not None and self._disconnect_consumes:
            consumed = (
                self._disconnect_consumes
                if timeout is None
                else min(timeout, self._disconnect_consumes)
            )
            self._clock.sleep(consumed)
        if self.disconnect_error is not None:
            raise self.disconnect_error


def make_transport(
    cast_device: FakeCast, browser: FakeBrowser | None = None
) -> tuple[PyChromecastTransport, FakeBrowser]:
    browser = browser or FakeBrowser()

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        assert timeout in (2.5, 1.0)
        return [cast(Chromecast, cast_device)], browser

    transport = PyChromecastTransport(
        connection_timeout=3.0,
        request_timeout=4.0,
        recovery_timeout=1.0,
        discoverer=discoverer,
        now=lambda: NOW,
    )
    transport.discover(timeout=2.5)
    return transport, browser


def send_playback_command(transport: PyChromecastTransport, command: str) -> None:
    if command == "play":
        transport.play(DEVICE_ID)
    elif command == "pause":
        transport.pause(DEVICE_ID)
    elif command == "stop":
        transport.stop(DEVICE_ID)
    else:
        raise AssertionError(f"not a playback command: {command}")


def test_discovery_maps_stable_identity_and_keeps_browser_alive() -> None:
    cast_device = FakeCast()
    transport, browser = make_transport(cast_device)

    devices = transport.discover(timeout=2.5)

    assert devices[0].id == DEVICE_ID
    assert devices[0].friendly_name == "Living room"
    assert devices[0].kind is DeviceKind.CAST
    assert devices[0].host == "192.168.1.20"
    assert browser.stopped is False

    transport.close()

    assert browser.stopped is True
    assert browser.stop_calls == 1


def test_discovery_failure_is_translated() -> None:
    def fail(timeout: float) -> tuple[list[Chromecast], object]:
        raise OSError("mDNS unavailable")

    transport = PyChromecastTransport(discoverer=fail)

    with pytest.raises(DiscoveryError, match="mDNS unavailable"):
        transport.discover(timeout=1.0)


def test_unknown_device_never_connects() -> None:
    transport = PyChromecastTransport()

    with pytest.raises(DeviceNotFoundError) as excinfo:
        transport.get_status(DEVICE_ID, timeout=5.0)

    assert excinfo.value.device_id == DEVICE_ID


def test_status_is_fresh_receiver_and_media_snapshot() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.reported = [playing_entry()]
    transport, _ = make_transport(cast_device)

    status = transport.get_status(DEVICE_ID, timeout=5.0)

    assert status.observed_at == NOW
    assert status.receiver is not None
    assert status.receiver.volume_level == 0.4
    assert status.media is not None
    assert status.media.playback_state is PlaybackState.PLAYING
    assert status.media.content_id == "https://media.local/movie.mp4"
    assert status.media.title == "Movie"
    assert cast_device.receiver_controller.updates == 1
    assert cast_device.media_controller.status_requests == 1
    assert cast_device.wait_timeouts[-1] == 3.0


def test_idle_status_without_content_has_no_media_session() -> None:
    cast_device = FakeCast()
    transport, _ = make_transport(cast_device)

    assert transport.get_status(DEVICE_ID, timeout=5.0).media is None


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.nan, math.inf])
def test_get_status_rejects_a_non_positive_or_non_finite_timeout(timeout: float) -> None:
    cast_device = FakeCast()
    transport, _ = make_transport(cast_device)

    with pytest.raises(InvalidArgumentError, match="must be a finite number > 0"):
        transport.get_status(DEVICE_ID, timeout=timeout)

    assert cast_device.wait_timeouts == []
    assert cast_device.receiver_controller.updates == 0


def test_get_status_bounds_the_connection_wait_by_the_caller_budget() -> None:
    """The caller's timeout wins even when it is tighter than the instance default."""
    clock = FakeClock()
    cast_device = FakeCast(clock=clock)
    transport = PyChromecastTransport(
        connection_timeout=3.0, request_timeout=4.0, clock=clock.monotonic
    )
    transport._casts[DEVICE_ID] = cast(Chromecast, cast_device)

    transport.get_status(DEVICE_ID, timeout=1.0)

    assert cast_device.wait_timeouts[-1] == 1.0


def test_get_status_never_starts_a_read_once_the_budget_is_exhausted() -> None:
    """A slow connect that eats the whole budget must not be followed by another read.

    Never confirms/fabricates a status from a read that was never attempted: the
    receiver-status round trip must not even start once nothing is left to spend on it.
    """
    clock = FakeClock()
    cast_device = FakeCast(clock=clock, wait_consumes=1.0)
    transport = PyChromecastTransport(
        connection_timeout=3.0, request_timeout=4.0, clock=clock.monotonic
    )
    transport._casts[DEVICE_ID] = cast(Chromecast, cast_device)

    with pytest.raises(OperationTimeoutError, match="status read budget exhausted"):
        transport.get_status(DEVICE_ID, timeout=1.0)

    assert cast_device.wait_timeouts == [1.0]
    assert cast_device.receiver_controller.updates == 0


def test_get_status_bounds_the_receiver_read_by_what_the_connect_left_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receiver-status round trip gets only what remains after connecting, not the
    adapter's full `request_timeout` default."""
    recorded_timeouts: list[float] = []

    class RecordingWaitResponse:
        def __init__(self, timeout: float, description: str) -> None:
            recorded_timeouts.append(timeout)
            self._real = pychromecast.response_handler.WaitResponse(timeout, description)

        @property
        def callback(self) -> Callable[..., None]:
            return self._real.callback

        @property
        def response(self) -> dict[str, object] | None:
            return self._real.response

        def wait_response(self) -> None:
            self._real.wait_response()

    monkeypatch.setattr(pychromecast_adapter, "WaitResponse", RecordingWaitResponse)
    clock = FakeClock()
    cast_device = FakeCast(clock=clock, wait_consumes=0.6)
    transport = PyChromecastTransport(
        connection_timeout=3.0, request_timeout=4.0, clock=clock.monotonic
    )
    transport._casts[DEVICE_ID] = cast(Chromecast, cast_device)

    status = transport.get_status(DEVICE_ID, timeout=1.0)

    assert status is not None
    assert cast_device.wait_timeouts[-1] == 1.0
    assert recorded_timeouts == [pytest.approx(0.4), pytest.approx(0.4)]


def test_get_status_recovers_a_stale_connection_within_its_own_budget() -> None:
    """Same-UUID recovery (used by every other command) is also budget-bounded here."""
    clock = FakeClock()
    stale = FakeCast(clock=clock)
    stale.wait_error = OSError("stale address")
    recovered = FakeCast(clock=clock)
    discover_calls: list[float] = []

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        discover_calls.append(timeout)
        return [cast(Chromecast, recovered)], FakeBrowser()

    transport = PyChromecastTransport(
        connection_timeout=1.0,
        request_timeout=1.0,
        recovery_timeout=5.0,
        discoverer=discoverer,
        clock=clock.monotonic,
    )
    transport._casts[DEVICE_ID] = cast(Chromecast, stale)

    status = transport.get_status(DEVICE_ID, timeout=3.0)

    assert status is not None
    # recovery_timeout=5.0 is capped down to whatever the 3.0s budget still has (the
    # instant failed connect attempt spent no simulated time), never the instance's own
    # larger default.
    assert discover_calls == [pytest.approx(3.0)]
    assert recovered.receiver_controller.updates == 1


def test_get_status_stops_recovering_once_the_budget_is_gone() -> None:
    clock = FakeClock()
    stale = FakeCast(clock=clock, wait_consumes=1.0)
    stale.wait_error = OSError("stale address")
    transport = PyChromecastTransport(connection_timeout=1.0, clock=clock.monotonic)
    transport._casts[DEVICE_ID] = cast(Chromecast, stale)

    with pytest.raises(OperationTimeoutError, match="status read budget exhausted"):
        transport.get_status(DEVICE_ID, timeout=1.0)

    assert clock.now == pytest.approx(1.0)
    assert stale.disconnect_calls == [0.0]


def test_get_status_translates_a_receiver_read_failure() -> None:
    cast_device = FakeCast()
    cast_device.receiver_controller.error = OSError("socket closed")
    transport, _ = make_transport(cast_device)

    with pytest.raises(DeviceUnavailableError, match="socket closed"):
        transport.get_status(DEVICE_ID, timeout=5.0)


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
    assert calls[0][1] == ("https://media.local/movie.mp4", "video/mp4")
    assert calls[0][2]["title"] is None
    assert [calls[index][2]["timeout"] for index in (1, 2, 3, 4)] == [4.0] * 4
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


@pytest.mark.parametrize("command", ["play", "pause", "stop"])
@pytest.mark.parametrize(
    ("library_error", "expected_error"),
    [
        (RequestTimeout("command", 4.0), OperationTimeoutError),
        (OSError("connection lost after send"), DeviceUnavailableError),
        (PyChromecastError("receiver rejected command"), CommandRejectedError),
    ],
)
def test_each_playback_command_error_is_translated_without_replay(
    command: str,
    library_error: Exception,
    expected_error: type[OperationTimeoutError | DeviceUnavailableError | CommandRejectedError],
) -> None:
    cast_device = FakeCast()
    cast_device.media_controller.error = library_error
    transport, _ = make_transport(cast_device)

    with pytest.raises(expected_error):
        send_playback_command(transport, command)

    assert [call[0] for call in cast_device.media_controller.calls] == [command]
    assert cast_device.wait_timeouts == [3.0]


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
    transport.close()

    assert cast_device.disconnect_calls == [3.0]
    with pytest.raises(DeviceNotFoundError):
        transport.play(DEVICE_ID)


def test_stale_connection_is_rediscovered_once_by_uuid_before_command() -> None:
    stale = FakeCast()
    stale.wait_error = OSError("stale address")
    recovered = FakeCast()
    browser = FakeBrowser()
    calls: list[float] = []

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        calls.append(timeout)
        selected = stale if len(calls) == 1 else recovered
        return [cast(Chromecast, selected)], browser

    transport = PyChromecastTransport(
        connection_timeout=3.0,
        request_timeout=4.0,
        recovery_timeout=1.0,
        discoverer=discoverer,
    )
    transport.discover(timeout=2.5)

    transport.play(DEVICE_ID)

    assert calls == [2.5, 1.0]
    assert stale.disconnect_calls == [3.0]
    assert [call[0] for call in stale.media_controller.calls] == []
    assert [call[0] for call in recovered.media_controller.calls] == ["play"]


def test_rediscovery_without_same_uuid_does_not_send_command() -> None:
    stale = FakeCast()
    stale.wait_error = OSError("stale address")
    other = FakeCast()
    other.uuid = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    other.cast_info.uuid = other.uuid
    discovered: list[FakeCast] = [stale]

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        selected = discovered.pop(0) if discovered else other
        return [cast(Chromecast, selected)], FakeBrowser()

    transport = PyChromecastTransport(
        recovery_timeout=1.0,
        discoverer=discoverer,
    )
    transport.discover(timeout=2.5)

    with pytest.raises(DeviceUnavailableError, match="not found during bounded rediscovery"):
        transport.play(DEVICE_ID)

    assert stale.media_controller.calls == []
    assert other.media_controller.calls == []


def test_command_failure_is_not_replayed_automatically() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.error = OSError("connection lost after send")
    transport, _ = make_transport(cast_device)

    with pytest.raises(DeviceUnavailableError):
        transport.play(DEVICE_ID)

    assert [call[0] for call in cast_device.media_controller.calls] == ["play"]
    assert cast_device.wait_timeouts == [3.0]


@pytest.mark.parametrize(
    "build",
    [
        lambda: PyChromecastTransport(connection_timeout=0.0),
        lambda: PyChromecastTransport(request_timeout=-1.0),
        lambda: PyChromecastTransport(recovery_timeout=float("inf")),
    ],
)
def test_transport_requires_positive_finite_timeouts(
    build: Callable[[], PyChromecastTransport],
) -> None:
    with pytest.raises(InvalidArgumentError, match="must be a finite number > 0"):
        build()


def test_repeated_discovery_disconnects_only_the_superseded_same_uuid_instance() -> None:
    first = FakeCast()
    replacement = FakeCast()
    discoveries = iter((first, replacement, replacement))

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        return [cast(Chromecast, next(discoveries))], FakeBrowser()

    transport = PyChromecastTransport(connection_timeout=3.0, discoverer=discoverer)

    transport.discover(timeout=1.0)
    assert first.disconnect_calls == []

    transport.discover(timeout=1.0)
    assert first.disconnect_calls == [3.0]
    assert replacement.disconnect_calls == []

    transport.discover(timeout=1.0)
    assert first.disconnect_calls == [3.0]
    assert replacement.disconnect_calls == []

    transport.close()
    transport.close()
    assert first.disconnect_calls == [3.0]
    assert replacement.disconnect_calls == [3.0]


def test_status_after_discovery_uses_live_discovery_context() -> None:
    browser = FakeBrowser()
    cast_device = FakeCast(browser=browser)

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        assert timeout == 1.0
        return [cast(Chromecast, cast_device)], browser

    transport = PyChromecastTransport(discoverer=discoverer, now=lambda: NOW)

    transport.discover(timeout=1.0)
    status = transport.get_status(DEVICE_ID, timeout=1.0)

    assert status.device_id == DEVICE_ID
    assert browser.stopped is False
    assert len(cast_device.wait_timeouts) == 1
    assert cast_device.wait_timeouts[0] is not None
    assert 0 < cast_device.wait_timeouts[0] <= 1.0


def test_successive_discoveries_replace_all_owned_resources_and_remain_usable() -> None:
    first_browser = FakeBrowser()
    second_browser = FakeBrowser()
    first = FakeCast(browser=first_browser)
    replacement = FakeCast(browser=second_browser)
    discoveries = iter(((first, first_browser), (replacement, second_browser)))

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        cast_device, browser = next(discoveries)
        return [cast(Chromecast, cast_device)], browser

    transport = PyChromecastTransport(connection_timeout=3.0, discoverer=discoverer)

    transport.discover(timeout=1.0)
    transport.get_status(DEVICE_ID, timeout=1.0)
    transport.discover(timeout=1.0)
    status = transport.get_status(DEVICE_ID, timeout=1.0)

    assert status.device_id == DEVICE_ID
    assert first.disconnect_calls == [3.0]
    assert first_browser.stop_calls == 1
    assert replacement.disconnect_calls == []
    assert second_browser.stopped is False

    transport.close()

    assert replacement.disconnect_calls == [3.0]
    assert second_browser.stop_calls == 1


def test_close_before_connection_start_tolerates_pychromecast_join_races() -> None:
    browser = FakeBrowser()
    browser.stop_error = RuntimeError("cannot join thread before it is started")
    cast_device = FakeCast(browser=browser)
    cast_device.disconnect_error = RuntimeError("cannot join thread before it is started")
    transport = PyChromecastTransport(
        discoverer=lambda timeout: ([cast(Chromecast, cast_device)], browser)
    )

    transport.discover(timeout=1.0)
    transport.close()
    transport.close()

    assert cast_device.wait_timeouts == []
    assert cast_device.disconnect_calls == [10.0]
    assert browser.stop_calls == 1


def test_discovery_mapping_error_cleans_new_resources_without_replacing_cache() -> None:
    active_browser = FakeBrowser()
    active = FakeCast(browser=active_browser)
    broken_browser = FakeBrowser()
    broken = FakeCast(browser=broken_browser)
    broken.cast_info.port = "not-a-port"
    discoveries = iter(((active, active_browser), (broken, broken_browser)))

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        cast_device, browser = next(discoveries)
        return [cast(Chromecast, cast_device)], browser

    transport = PyChromecastTransport(discoverer=discoverer)
    transport.discover(timeout=1.0)

    with pytest.raises(TypeError):
        transport.discover(timeout=1.0)

    assert broken.disconnect_calls == [10.0]
    assert broken_browser.stop_calls == 1
    assert active.disconnect_calls == []
    assert active_browser.stopped is False
    assert transport.get_status(DEVICE_ID, timeout=1.0).device_id == DEVICE_ID


def test_cleanup_errors_do_not_orphan_logical_ownership_or_break_idempotence() -> None:
    first_browser = FakeBrowser()
    first_browser.stop_error = OSError("zeroconf shutdown failed")
    second_browser = FakeBrowser()
    first = FakeCast(browser=first_browser)
    first.disconnect_error = RuntimeError("worker was never started")
    replacement = FakeCast(browser=second_browser)
    discoveries = iter(((first, first_browser), (replacement, second_browser)))

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        cast_device, browser = next(discoveries)
        return [cast(Chromecast, cast_device)], browser

    transport = PyChromecastTransport(discoverer=discoverer)

    transport.discover(timeout=1.0)
    transport.discover(timeout=1.0)
    transport.close()
    transport.close()

    assert first.disconnect_calls == [10.0]
    assert first_browser.stop_calls == 1
    assert replacement.disconnect_calls == [10.0]
    assert second_browser.stop_calls == 1


def test_empty_discovery_stops_unowned_browser_immediately() -> None:
    browser = FakeBrowser()
    transport = PyChromecastTransport(discoverer=lambda timeout: ([], browser))

    assert transport.discover(timeout=1.0) == []

    assert browser.stop_calls == 1
    assert transport._browser is None

    transport.close()

    assert browser.stop_calls == 1


def test_status_recovery_bounds_every_superseded_disconnect_by_global_deadline() -> None:
    clock = FakeClock()
    old_browser = FakeBrowser()
    new_browser = FakeBrowser()
    stale = FakeCast(clock=clock, wait_consumes=0.2, disconnect_consumes=0.1)
    stale.wait_error = OSError("stale")
    other_one = FakeCast(clock=clock, disconnect_consumes=2.0)
    other_one.uuid = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    other_two = FakeCast(clock=clock, disconnect_consumes=2.0)
    other_two.uuid = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    recovered = FakeCast(clock=clock)

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        clock.sleep(0.3)
        return [cast(Chromecast, recovered)], new_browser

    transport = PyChromecastTransport(
        connection_timeout=3.0,
        recovery_timeout=3.0,
        discoverer=discoverer,
        clock=clock.monotonic,
    )
    transport._casts = {
        DEVICE_ID: cast(Chromecast, stale),
        DeviceId(str(other_one.uuid)): cast(Chromecast, other_one),
        DeviceId(str(other_two.uuid)): cast(Chromecast, other_two),
    }
    transport._browser = old_browser

    with pytest.raises(OperationTimeoutError, match="status read budget exhausted"):
        transport.get_status(DEVICE_ID, timeout=1.0)

    assert clock.now == pytest.approx(1.0)
    assert stale.disconnect_calls == [pytest.approx(0.8)]
    assert other_one.disconnect_calls == [pytest.approx(0.4)]
    assert other_two.disconnect_calls == [0.0]
    assert old_browser.stop_calls == 1
    assert new_browser.stopped is False


def test_playing_media_uses_adjusted_position_between_cast_events() -> None:
    status = ControlledMediaStatus(adjusted=18.5)
    status.player_state = "PLAYING"
    status.content_id = "https://media.local/movie.mp4"
    status.current_time = 12.0

    first = PyChromecastTransport._media_status(status)
    second = PyChromecastTransport._media_status(status)

    assert first is not None
    assert second is not None
    assert first.playback_state is PlaybackState.PLAYING
    assert first.position_seconds == 18.5
    assert second.position_seconds == 19.5


def test_paused_media_keeps_last_reported_position() -> None:
    status = ControlledMediaStatus(adjusted=18.5)
    status.player_state = "PAUSED"
    status.content_id = "https://media.local/movie.mp4"
    status.current_time = 12.0

    observed = PyChromecastTransport._media_status(status)

    assert observed is not None
    assert observed.playback_state is PlaybackState.PAUSED
    assert observed.position_seconds == 12.0


def test_discovery_uuid_selection_connects_and_reads_only_the_selected_status() -> None:
    first = FakeCast()
    first.uuid = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    first.cast_info.uuid = first.uuid
    second = FakeCast()
    browser = FakeBrowser()

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        assert timeout == 1.5
        return [cast(Chromecast, first), cast(Chromecast, second)], browser

    transport = PyChromecastTransport(discoverer=discoverer, now=lambda: NOW)

    devices = transport.discover(timeout=1.5)
    selected = next(device for device in devices if device.id == DEVICE_ID)
    status = transport.get_status(selected.id, timeout=2.0)

    assert [device.id for device in devices] == [DeviceId(str(first.uuid)), DEVICE_ID]
    assert status.device_id == DEVICE_ID
    assert first.wait_timeouts == []
    assert first.receiver_controller.updates == 0
    assert len(second.wait_timeouts) == 1
    assert second.wait_timeouts[0] is not None
    assert 0 < second.wait_timeouts[0] <= 2.0
    assert second.receiver_controller.updates == 1
    assert browser.stopped is False

    transport.close()

    assert browser.stopped is True


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.nan, math.inf])
def test_discovery_rejects_invalid_timeout_before_calling_library(timeout: float) -> None:
    calls: list[float] = []

    def discoverer(value: float) -> tuple[list[Chromecast], object]:
        calls.append(value)
        return [], FakeBrowser()

    transport = PyChromecastTransport(discoverer=discoverer)

    with pytest.raises(InvalidArgumentError, match="discovery timeout"):
        transport.discover(timeout=timeout)

    assert calls == []


@pytest.mark.parametrize("position", [-1.0, math.nan, math.inf])
def test_direct_transport_seek_rejects_invalid_position_before_connecting(position: float) -> None:
    cast_device = FakeCast()
    transport, _ = make_transport(cast_device)
    cast_device.wait_timeouts.clear()

    with pytest.raises(InvalidArgumentError, match="seek position"):
        transport.seek(DEVICE_ID, position)

    assert cast_device.wait_timeouts == []
    assert cast_device.media_controller.calls == []


@pytest.mark.parametrize("level", [-0.1, 1.1, math.nan, math.inf])
def test_direct_transport_volume_rejects_invalid_level_before_connecting(level: float) -> None:
    cast_device = FakeCast()
    transport, _ = make_transport(cast_device)
    cast_device.wait_timeouts.clear()

    with pytest.raises(InvalidArgumentError, match="volume"):
        transport.set_volume(DEVICE_ID, level)

    assert cast_device.wait_timeouts == []
    assert cast_device.volume_calls == []


def test_direct_transport_mute_rejects_non_boolean_before_connecting() -> None:
    cast_device = FakeCast()
    transport, _ = make_transport(cast_device)
    cast_device.wait_timeouts.clear()

    with pytest.raises(InvalidArgumentError, match="boolean"):
        transport.set_muted(DEVICE_ID, 1)  # type: ignore[arg-type]

    assert cast_device.wait_timeouts == []
    assert cast_device.mute_calls == []


def test_status_recovery_bounds_stale_disconnect_by_remaining_budget() -> None:
    clock = FakeClock()
    stale = FakeCast(clock=clock, wait_consumes=0.4, disconnect_consumes=2.0)
    stale.wait_error = OSError("stale address")
    discover_calls: list[float] = []

    def discoverer(timeout: float) -> tuple[list[Chromecast], object]:
        discover_calls.append(timeout)
        return [], FakeBrowser()

    transport = PyChromecastTransport(
        connection_timeout=3.0,
        recovery_timeout=3.0,
        discoverer=discoverer,
        clock=clock.monotonic,
    )
    transport._casts[DEVICE_ID] = cast(Chromecast, stale)

    with pytest.raises(OperationTimeoutError, match="status read budget exhausted"):
        transport.get_status(DEVICE_ID, timeout=1.0)

    assert clock.now == pytest.approx(1.0)
    assert stale.disconnect_calls == [pytest.approx(0.6)]
    assert discover_calls == []


def test_get_status_never_returns_a_snapshot_from_an_overrunning_receiver_callback() -> None:
    clock = FakeClock()
    cast_device = FakeCast(clock=clock)
    cast_device.receiver_controller._consumes = 1.01
    transport = PyChromecastTransport(clock=clock.monotonic)
    transport._casts[DEVICE_ID] = cast(Chromecast, cast_device)

    with pytest.raises(OperationTimeoutError, match="status read budget exhausted"):
        transport.get_status(DEVICE_ID, timeout=1.0)

    assert clock.now == pytest.approx(1.01)
    assert cast_device.receiver_controller.updates == 1


def test_load_failed_response_is_an_explicit_rejection_and_is_not_replayed() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.load_response = {
        "type": "LOAD_FAILED",
        "detailedErrorCode": 104,
    }
    transport, _ = make_transport(cast_device)
    request = MediaRequest(url="https://media.local/movie.mp4", content_type="video/mp4")

    with pytest.raises(CommandRejectedError, match=r"LOAD_FAILED.*detailed error 104"):
        transport.load_media(DEVICE_ID, request)

    assert [call[0] for call in cast_device.media_controller.calls] == ["load_media"]


def test_request_not_sent_is_unavailable_not_receiver_rejection_and_is_not_replayed() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.error = RequestFailed("pause")
    transport, _ = make_transport(cast_device)

    with pytest.raises(DeviceUnavailableError, match="unavailable"):
        transport.pause(DEVICE_ID)

    assert [call[0] for call in cast_device.media_controller.calls] == ["pause"]


def test_load_not_sent_is_unavailable_and_is_not_replayed() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.load_sent = False
    transport, _ = make_transport(cast_device)
    request = MediaRequest(url="https://media.local/movie.mp4", content_type="video/mp4")

    with pytest.raises(DeviceUnavailableError, match="unavailable"):
        transport.load_media(DEVICE_ID, request)

    assert [call[0] for call in cast_device.media_controller.calls] == ["load_media"]


def test_media_status_is_the_explicit_success_response_for_load() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.load_response = {"type": "MEDIA_STATUS", "status": []}
    transport, _ = make_transport(cast_device)
    request = MediaRequest(url="https://media.local/movie.mp4", content_type="video/mp4")

    transport.load_media(DEVICE_ID, request)

    assert [call[0] for call in cast_device.media_controller.calls] == ["load_media"]


@pytest.mark.parametrize(
    "response_type",
    [
        pytest.param("LOAD_FAILED", id="load-failed"),
        pytest.param("LOAD_CANCELLED", id="load-cancelled"),
        pytest.param("INVALID_REQUEST", id="invalid-request"),
        pytest.param("GENERIC_ERROR", id="other-terminal-error"),
    ],
)
def test_every_non_success_terminal_load_response_is_rejected_without_replay(
    response_type: str,
) -> None:
    cast_device = FakeCast()
    cast_device.media_controller.load_response = {"type": response_type}
    transport, _ = make_transport(cast_device)
    request = MediaRequest(url="https://media.local/movie.mp4", content_type="video/mp4")

    with pytest.raises(CommandRejectedError, match=response_type):
        transport.load_media(DEVICE_ID, request)

    assert [call[0] for call in cast_device.media_controller.calls] == ["load_media"]


def test_terminal_load_rejection_never_starts_service_confirmation() -> None:
    cast_device = FakeCast()
    cast_device.media_controller.load_response = {"type": "LOAD_CANCELLED"}
    transport, _ = make_transport(cast_device)
    service = ControlService(transport, confirm_timeout=1.0)
    request = MediaRequest(url="https://media.local/movie.mp4", content_type="video/mp4")

    with pytest.raises(CommandRejectedError, match="LOAD_CANCELLED"):
        service.load_media(DEVICE_ID, request)

    assert [call[0] for call in cast_device.media_controller.calls] == ["load_media"]
    assert cast_device.receiver_controller.updates == 0


# --- Media status freshness -------------------------------------------------------------
#
# PyChromecast keeps one cached `MediaStatus` per device and merges every MEDIA_STATUS
# message into it (`MediaStatus.update`). The tests below first pin what PyChromecast
# 14.0.10 actually does with the messages a receiver sends, then require that a status
# read never presents, or lets a confirmation use, media information that the current read
# did not itself receive.

MEDIA_NAMESPACE = "urn:x-cast:com.google.cast.media"
MOVIE = "https://media.local/movie.mp4"
EPISODE = "https://media.local/episode.mp4"
SUPPORTS_PAUSE_AND_SEEK = 3


def playing_entry(content_id: str = MOVIE, session: int = 1) -> dict[str, object]:
    """One complete media session entry, as a receiver reports it after a load."""
    return {
        "mediaSessionId": session,
        "playerState": "PLAYING",
        "currentTime": 12.0,
        "playbackRate": 1,
        "supportedMediaCommands": SUPPORTS_PAUSE_AND_SEEK,
        "media": {
            "contentId": content_id,
            "contentType": "video/mp4",
            "duration": 90.0,
            "metadata": {"title": "Movie"},
        },
    }


def idle_entry_without_media(session: int = 1) -> dict[str, object]:
    """A partial entry: the state changed, the `media` block is not repeated."""
    return {"mediaSessionId": session, "playerState": "IDLE", "idleReason": "CANCELLED"}


def media_status_message(*entries: dict[str, object]) -> dict[str, object]:
    return {"type": "MEDIA_STATUS", "status": list(entries)}


def test_characterized_pychromecast_version() -> None:
    """The characterization below is for this exact version; revisit it on any upgrade."""
    assert importlib.metadata.version("PyChromecast") == "14.0.10"


def test_pychromecast_ignores_an_empty_status_list_and_keeps_the_ended_session() -> None:
    cached = PyMediaStatus()
    cached.update(media_status_message(playing_entry()))

    cached.update(media_status_message())

    assert cached.player_state == "PLAYING"
    assert cached.content_id == MOVIE
    assert cached.media_session_id == 1


def test_pychromecast_keeps_the_old_content_id_on_a_partial_idle_status() -> None:
    cached = PyMediaStatus()
    cached.update(media_status_message(playing_entry()))

    cached.update(media_status_message(idle_entry_without_media()))

    assert cached.player_state == "IDLE"
    assert cached.content_id == MOVIE
    assert cached.duration == 90.0
    assert cached.title == "Movie"


def test_pychromecast_carries_old_media_fields_into_a_new_session() -> None:
    cached = PyMediaStatus()
    cached.update(media_status_message(playing_entry(session=1)))

    cached.update(
        media_status_message({"mediaSessionId": 2, "playerState": "PAUSED", "currentTime": 3.0})
    )

    assert cached.media_session_id == 2
    assert cached.player_state == "PAUSED"
    assert cached.content_id == MOVIE
    assert cached.content_type == "video/mp4"
    assert cached.duration == 90.0


def test_a_new_pychromecast_status_holds_only_the_message_it_parsed() -> None:
    fresh = PyMediaStatus()
    fresh.update(media_status_message(idle_entry_without_media()))

    assert fresh.player_state == "IDLE"
    assert fresh.content_id is None
    assert fresh.duration is None

    empty = PyMediaStatus()
    empty.update(media_status_message())

    assert empty.player_state == "UNKNOWN"
    assert empty.content_id is None
    assert empty.media_session_id is None


class ScriptedMediaChannel:
    """The receiver's side of the media namespace, for PyChromecast's real MediaController.

    It mirrors what PyChromecast's `SocketClient.send_app_message` does: a namespace the
    running application does not expose is refused, and a reply is first merged by the
    controller into its cached status and then handed to the request's callback; like
    `SocketClient._route_message`, an exception raised while merging is swallowed (logged
    there) and the callback still receives the reply. `state` is
    what the receiver currently reports; a command listed in `after_command` changes it.
    `mode` decides how requests are answered: "reply", "silent" (never), "late" (held
    until `deliver_late`) or "refuse" (not sent).
    """

    def __init__(self, receiver_controller: FakeReceiverController) -> None:
        self.receiver_controller = receiver_controller
        self.app_namespaces = [MEDIA_NAMESPACE]
        self.destination_id = "transport-1"
        self.state: list[dict[str, object]] = []
        self.after_command: dict[str, list[dict[str, object]]] = {}
        self.mode = "reply"
        self.sent: list[dict[str, object]] = []
        self.controller = MediaController()
        self.controller.registered(cast(SocketClient, self))
        self._late: list[tuple[Callable[[bool, dict[str, object] | None], None], dict[str, object]]]
        self._late = []
        self._request_id = 0

    def send_app_message(
        self,
        namespace: str,
        message: dict[str, object],
        *,
        inc_session_id: bool = False,
        callback_function: Callable[[bool, dict[str, object] | None], None] | None = None,
        no_add_request_id: bool = False,
    ) -> None:
        del inc_session_id, no_add_request_id
        if namespace not in self.app_namespaces:
            if callback_function is not None:
                callback_function(False, None)
            raise UnsupportedNamespace(f"Namespace {namespace} is not supported")
        self.sent.append(dict(message))
        if self.mode == "refuse":
            if callback_function is not None:
                callback_function(False, None)
            return
        new_state = self.after_command.get(str(message["type"]))
        if new_state is not None:
            self.state = new_state
        self._request_id += 1
        reply = media_status_message(*self.state) | {"requestId": self._request_id}
        if self.mode == "silent" or callback_function is None:
            return
        if self.mode == "late":
            self._late.append((callback_function, reply))
            return
        self._merge(reply)
        callback_function(True, reply)

    def _merge(self, reply: dict[str, object]) -> None:
        with suppress(Exception):
            self.controller.receive_message(cast(CastMessage, None), reply)

    def broadcast(self, *entries: dict[str, object]) -> None:
        """An unsolicited MEDIA_STATUS pushed by the receiver to every connected sender."""
        self.controller.receive_message(cast(CastMessage, None), media_status_message(*entries))

    def deliver_late(self) -> None:
        for callback_function, reply in self._late:
            self._merge(reply)
            callback_function(True, reply)
        self._late = []

    def sent_types(self) -> list[str]:
        return [str(message["type"]) for message in self.sent]


def make_media_transport() -> tuple[PyChromecastTransport, ScriptedMediaChannel]:
    cast_device = FakeCast()
    channel = ScriptedMediaChannel(cast_device.receiver_controller)
    cast_device.media_controller = channel.controller  # type: ignore[assignment]
    cast_device.socket_client = channel  # type: ignore[assignment]
    transport = PyChromecastTransport(connection_timeout=3.0, request_timeout=4.0, now=lambda: NOW)
    transport._casts[DEVICE_ID] = cast(Chromecast, cast_device)
    return transport, channel


def test_status_read_reports_the_media_the_receiver_reports_now() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry()]

    media = transport.get_status(DEVICE_ID, timeout=5.0).media

    assert media is not None
    assert media.playback_state is PlaybackState.PLAYING
    assert media.content_id == MOVIE
    assert media.title == "Movie"
    assert media.duration_seconds == 90.0
    assert media.supports_seek is True
    assert channel.sent_types() == ["GET_STATUS"]


def test_an_ended_session_reported_as_an_empty_list_is_absent() -> None:
    transport, channel = make_media_transport()
    channel.broadcast(playing_entry())
    channel.state = []

    assert transport.get_status(DEVICE_ID, timeout=5.0).media is None


def test_a_partial_idle_status_does_not_inherit_the_previous_content() -> None:
    transport, channel = make_media_transport()
    channel.broadcast(playing_entry())
    channel.state = [idle_entry_without_media()]

    assert transport.get_status(DEVICE_ID, timeout=5.0).media is None


def test_media_fields_the_current_reply_omits_are_unknown_not_inherited() -> None:
    transport, channel = make_media_transport()
    channel.broadcast(playing_entry(session=1))
    channel.state = [{"mediaSessionId": 2, "playerState": "PAUSED", "currentTime": 3.0}]

    media = transport.get_status(DEVICE_ID, timeout=5.0).media

    assert media is not None
    assert media.playback_state is PlaybackState.PAUSED
    assert media.position_seconds == 3.0
    assert media.content_id is None
    assert media.content_type is None
    assert media.title is None
    assert media.duration_seconds is None
    assert media.supports_seek is None


def test_a_replaced_session_reports_only_the_new_content() -> None:
    transport, channel = make_media_transport()
    channel.broadcast(playing_entry(MOVIE, session=1))
    channel.state = [playing_entry(EPISODE, session=2)]

    media = transport.get_status(DEVICE_ID, timeout=5.0).media

    assert media is not None
    assert media.content_id == EPISODE


def test_an_application_without_media_has_no_session_and_nothing_is_launched() -> None:
    transport, channel = make_media_transport()
    channel.broadcast(playing_entry())
    channel.app_namespaces = []

    assert transport.get_status(DEVICE_ID, timeout=5.0).media is None
    assert channel.sent == []
    assert channel.receiver_controller.launched == []


def test_no_media_reply_within_the_budget_is_a_timeout_not_a_cached_status() -> None:
    transport, channel = make_media_transport()
    channel.broadcast(playing_entry())
    channel.mode = "silent"
    started = time.monotonic()

    with pytest.raises(OperationTimeoutError):
        transport.get_status(DEVICE_ID, timeout=0.2)

    assert time.monotonic() - started < 1.0
    assert channel.sent_types() == ["GET_STATUS"]


def test_the_media_request_gets_only_what_the_receiver_read_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, float]] = []

    class RecordingWaitResponse:
        def __init__(self, timeout: float, description: str) -> None:
            recorded.append((description, timeout))
            self._real = pychromecast.response_handler.WaitResponse(timeout, description)

        @property
        def callback(self) -> Callable[..., None]:
            return self._real.callback

        @property
        def response(self) -> dict[str, object] | None:
            return self._real.response

        def wait_response(self) -> None:
            self._real.wait_response()

    monkeypatch.setattr(pychromecast_adapter, "WaitResponse", RecordingWaitResponse)
    clock = FakeClock()
    transport, channel = make_media_transport()
    transport._clock = clock.monotonic
    channel.receiver_controller._clock = clock
    channel.receiver_controller._consumes = 0.3
    channel.state = [playing_entry()]

    transport.get_status(DEVICE_ID, timeout=1.0)

    assert recorded == [("receiver status", 1.0), ("media status", pytest.approx(0.7))]


def test_a_late_media_reply_is_never_used_by_a_later_read() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry()]
    channel.mode = "late"

    with pytest.raises(OperationTimeoutError):
        transport.get_status(DEVICE_ID, timeout=0.2)

    channel.deliver_late()
    channel.mode = "reply"
    channel.state = []

    assert transport.get_status(DEVICE_ID, timeout=5.0).media is None


def test_a_media_request_that_could_not_be_sent_is_an_unavailable_device() -> None:
    transport, channel = make_media_transport()
    channel.broadcast(playing_entry())
    channel.mode = "refuse"

    with pytest.raises(DeviceUnavailableError):
        transport.get_status(DEVICE_ID, timeout=5.0)


def test_stop_is_not_confirmed_by_a_content_id_the_receiver_did_not_repeat() -> None:
    """A receiver answering STOP with a partial IDLE entry leaves PyChromecast's cache as
    IDLE plus the old content id; that inherited identity must not confirm the stop."""
    transport, channel = make_media_transport()
    channel.broadcast(playing_entry())
    channel.state = [playing_entry()]
    channel.after_command = {"STOP": [idle_entry_without_media()]}
    service = ControlService(transport, confirm_timeout=0.3, poll_interval=0.05)

    result = service.stop(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert channel.sent_types().count("STOP") == 1


def test_pause_is_confirmed_by_a_complete_paused_reply_for_the_same_content() -> None:
    transport, channel = make_media_transport()
    channel.broadcast(playing_entry())
    channel.state = [playing_entry()]
    paused = playing_entry() | {"playerState": "PAUSED"}
    channel.after_command = {"PAUSE": [paused]}
    service = ControlService(transport, confirm_timeout=1.0, poll_interval=0.05)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert channel.sent_types().count("PAUSE") == 1


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(None, id="no-data"),
        pytest.param({"type": "INVALID_REQUEST", "reason": "INVALID_COMMAND"}, id="error-reply"),
    ],
)
def test_a_media_reply_that_is_not_a_status_is_unavailable_not_an_absent_session(
    reply: dict[str, object] | None,
) -> None:
    cast_device = FakeCast()

    def answer_without_data(
        data: dict[str, object],
        *,
        callback_function: Callable[[bool, dict[str, object] | None], None],
    ) -> None:
        callback_function(True, reply)

    cast_device.media_controller.send_message_nocheck = answer_without_data  # type: ignore[method-assign]
    transport, _ = make_transport(cast_device)

    with pytest.raises(DeviceUnavailableError):
        transport.get_status(DEVICE_ID, timeout=5.0)


def entry_without(field: str, **changes: object) -> dict[str, object]:
    """A complete entry for the same content, with one field left out of the raw reply."""
    entry = playing_entry() | changes
    del entry[field]
    return entry


@pytest.mark.parametrize("state", ["PLAYING", "PAUSED", "BUFFERING"])
def test_an_omitted_position_is_unknown_not_zero(state: str) -> None:
    transport, channel = make_media_transport()
    channel.state = [entry_without("currentTime", playerState=state)]

    media = transport.get_status(DEVICE_ID, timeout=5.0).media

    assert media is not None
    assert media.content_id == MOVIE
    assert media.position_seconds is None


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        pytest.param(0, 0.0, id="explicit-zero"),
        pytest.param(42.5, 42.5, id="valid"),
        pytest.param(None, None, id="explicit-null"),
    ],
)
def test_a_reported_position_is_kept_as_reported(
    reported: float | None, expected: float | None
) -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry() | {"playerState": "PAUSED", "currentTime": reported}]

    media = transport.get_status(DEVICE_ID, timeout=5.0).media

    assert media is not None
    assert media.position_seconds == expected


@pytest.mark.parametrize(
    ("commands", "expected"),
    [
        pytest.param("omitted", None, id="absent-is-unknown"),
        pytest.param(None, None, id="explicit-null-is-unknown"),
        pytest.param(0, False, id="explicit-zero-mask"),
        pytest.param(1, False, id="pause-without-seek"),
        pytest.param(SUPPORTS_PAUSE_AND_SEEK, True, id="seek-supported"),
    ],
)
def test_seek_capability_is_unknown_only_when_the_reply_omits_it(
    commands: int | str | None, expected: bool | None
) -> None:
    transport, channel = make_media_transport()
    channel.state = [
        entry_without("supportedMediaCommands")
        if commands == "omitted"
        else playing_entry() | {"supportedMediaCommands": commands}
    ]

    media = transport.get_status(DEVICE_ID, timeout=5.0).media

    assert media is not None
    assert media.supports_seek is expected


def test_seek_to_zero_is_not_confirmed_by_an_omitted_position() -> None:
    """Same content, the reply after SEEK omits currentTime: PyChromecast's default of 0.0
    must not be read as the receiver reporting position 0."""
    transport, channel = make_media_transport()
    channel.state = [playing_entry()]
    channel.after_command = {"SEEK": [entry_without("currentTime")]}
    service = ControlService(transport, confirm_timeout=0.3, poll_interval=0.05)

    result = service.seek(DEVICE_ID, 0.0)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "position was not reported" in (result.detail or "")
    assert channel.sent_types().count("SEEK") == 1


def test_seek_to_zero_is_confirmed_by_a_reported_position_of_zero() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry()]
    channel.after_command = {"SEEK": [playing_entry() | {"currentTime": 0.0}]}
    service = ControlService(transport, confirm_timeout=1.0, poll_interval=0.05)

    result = service.seek(DEVICE_ID, 0.0)

    assert result.confirmation is Confirmation.CONFIRMED
    assert channel.sent_types().count("SEEK") == 1


def test_seek_is_sent_when_the_reply_omits_the_capability() -> None:
    """Unknown capability is not "unsupported": the service only refuses an explicit False."""
    transport, channel = make_media_transport()
    channel.state = [entry_without("supportedMediaCommands")]
    channel.after_command = {"SEEK": [entry_without("supportedMediaCommands", currentTime=30.0)]}
    service = ControlService(transport, confirm_timeout=1.0, poll_interval=0.05)

    result = service.seek(DEVICE_ID, 30.0)

    assert channel.sent_types().count("SEEK") == 1
    assert result.confirmation is Confirmation.CONFIRMED


def test_seek_is_refused_before_sending_when_the_reply_reports_no_seek() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry() | {"supportedMediaCommands": 1}]
    service = ControlService(transport, confirm_timeout=1.0, poll_interval=0.05)

    with pytest.raises(UnsupportedOperationError):
        service.seek(DEVICE_ID, 30.0)

    assert "SEEK" not in channel.sent_types()


def test_seek_is_sent_and_confirmed_when_the_capability_is_null() -> None:
    """A null command mask is unknown: no internal error, and no refusal as unsupported."""
    transport, channel = make_media_transport()
    channel.state = [playing_entry() | {"supportedMediaCommands": None}]
    channel.after_command = {
        "SEEK": [playing_entry() | {"supportedMediaCommands": None, "currentTime": 30.0}]
    }
    service = ControlService(transport, confirm_timeout=1.0, poll_interval=0.05)

    result = service.seek(DEVICE_ID, 30.0)

    assert result.confirmation is Confirmation.CONFIRMED
    assert channel.sent_types().count("SEEK") == 1


def test_a_null_playback_rate_keeps_the_reported_position() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry() | {"playbackRate": None}]

    media = transport.get_status(DEVICE_ID, timeout=5.0).media

    assert media is not None
    assert media.playback_state is PlaybackState.PLAYING
    assert media.position_seconds == 12.0


def test_null_metadata_leaves_the_title_unknown() -> None:
    transport, channel = make_media_transport()
    media_block = cast(dict[str, object], playing_entry()["media"]) | {"metadata": None}
    channel.state = [playing_entry() | {"media": media_block}]

    media = transport.get_status(DEVICE_ID, timeout=5.0).media

    assert media is not None
    assert media.content_id == MOVIE
    assert media.title is None


# --- Structurally invalid MEDIA_STATUS replies --------------------------------------------
#
# A field whose value cannot be what the Cast protocol defines (wrong JSON type, a negative
# or non-finite number) is reported as unknown, exactly like an omitted or null field: never
# as a plausible value (0, false, "") and never as a crash. A reply whose overall shape is not
# a media status (no status list, an entry that is not an object) fails the read cleanly.


def paused_entry(**changes: object) -> dict[str, object]:
    return playing_entry() | {"playerState": "PAUSED"} | changes


def paused_entry_with_media(**media_changes: object) -> dict[str, object]:
    media_block = cast(dict[str, object], playing_entry()["media"]) | media_changes
    return paused_entry(media=media_block)


def read_media(entry: dict[str, object]) -> MediaStatus | None:
    transport, channel = make_media_transport()
    channel.state = [entry]
    return transport.get_status(DEVICE_ID, timeout=5.0).media


UNUSABLE_NUMBERS = [
    pytest.param("", id="empty-string"),
    pytest.param("12", id="numeric-string"),
    pytest.param(True, id="boolean"),
    pytest.param(-5, id="negative"),
    pytest.param(math.nan, id="nan"),
    pytest.param(math.inf, id="infinity"),
    pytest.param(10**400, id="integer-too-large-for-a-float"),
    pytest.param({}, id="object"),
]


@pytest.mark.parametrize("value", UNUSABLE_NUMBERS)
def test_an_unusable_position_is_unknown(value: object) -> None:
    media = read_media(paused_entry(currentTime=value))

    assert isinstance(media, MediaStatus)
    assert media.position_seconds is None
    assert media.content_id == MOVIE
    assert media.playback_state is PlaybackState.PAUSED


@pytest.mark.parametrize("value", UNUSABLE_NUMBERS)
def test_an_unusable_duration_is_unknown(value: object) -> None:
    media = read_media(paused_entry_with_media(duration=value))

    assert isinstance(media, MediaStatus)
    assert media.duration_seconds is None
    assert media.content_id == MOVIE


def test_an_explicit_zero_duration_is_kept() -> None:
    media = read_media(paused_entry_with_media(duration=0))

    assert isinstance(media, MediaStatus)
    assert media.duration_seconds == 0.0


@pytest.mark.parametrize("value", [0, 12, {}, [], True], ids=str)
def test_a_content_id_that_is_not_a_string_is_unknown(value: object) -> None:
    media = read_media(paused_entry_with_media(contentId=value))

    assert isinstance(media, MediaStatus)
    assert media.content_id is None


@pytest.mark.parametrize("value", [5, {}, True], ids=str)
def test_a_content_type_that_is_not_a_string_is_unknown(value: object) -> None:
    media = read_media(paused_entry_with_media(contentType=value))

    assert isinstance(media, MediaStatus)
    assert media.content_type is None
    assert media.content_id == MOVIE


@pytest.mark.parametrize("value", [5, {}, True], ids=str)
def test_a_player_state_that_is_not_a_string_is_unknown(value: object) -> None:
    media = read_media(paused_entry(playerState=value))

    assert isinstance(media, MediaStatus)
    assert media.playback_state is PlaybackState.UNKNOWN
    assert media.content_id == MOVIE


@pytest.mark.parametrize("value", ["3", 3.5, True, -1, {}], ids=str)
def test_an_unusable_command_mask_leaves_seek_capability_unknown(value: object) -> None:
    media = read_media(paused_entry(supportedMediaCommands=value))

    assert isinstance(media, MediaStatus)
    assert media.supports_seek is None


@pytest.mark.parametrize("metadata", ["x", [], 5, {"title": 5}, {"title": {}}], ids=str)
def test_unusable_metadata_leaves_the_title_unknown(metadata: object) -> None:
    media = read_media(paused_entry_with_media(metadata=metadata))

    assert isinstance(media, MediaStatus)
    assert media.title is None
    assert media.content_id == MOVIE


@pytest.mark.parametrize("rate", ["1", True, math.nan, math.inf, {}], ids=str)
def test_an_unusable_playback_rate_keeps_the_reported_position(rate: object) -> None:
    media = read_media(playing_entry() | {"playbackRate": rate})

    assert isinstance(media, MediaStatus)
    assert media.playback_state is PlaybackState.PLAYING
    assert media.position_seconds == 12.0


@pytest.mark.parametrize("volume", [None, "x", 5, []], ids=str)
def test_an_unusable_media_volume_block_is_ignored(volume: object) -> None:
    """The media-channel volume is not mapped; a malformed one must not break the read."""
    media = read_media(paused_entry(volume=volume))

    assert isinstance(media, MediaStatus)
    assert media.content_id == MOVIE


@pytest.mark.parametrize("media_block", ["x", 5, []], ids=str)
def test_a_media_block_that_is_not_an_object_leaves_the_content_unknown(
    media_block: object,
) -> None:
    media = read_media(paused_entry(media=media_block))

    assert isinstance(media, MediaStatus)
    assert media.playback_state is PlaybackState.PAUSED
    assert media.content_id is None
    assert media.duration_seconds is None


def test_media_from_the_extended_status_is_checked_the_same_way() -> None:
    entry = paused_entry()
    del entry["media"]
    entry["extendedStatus"] = {"media": {"contentId": 0, "duration": "", "metadata": "x"}}

    media = read_media(entry)

    assert isinstance(media, MediaStatus)
    assert media.content_id is None
    assert media.duration_seconds is None
    assert media.title is None


@pytest.mark.parametrize("extended", ["x", 5, None], ids=str)
def test_an_extended_status_that_is_not_an_object_is_ignored(extended: object) -> None:
    entry = paused_entry()
    del entry["media"]
    entry["extendedStatus"] = extended

    media = read_media(entry)

    assert isinstance(media, MediaStatus)
    assert media.content_id is None


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param({"type": "MEDIA_STATUS"}, id="no-status-list"),
        pytest.param({"type": "MEDIA_STATUS", "status": None}, id="null-status"),
        pytest.param({"type": "MEDIA_STATUS", "status": "x"}, id="status-string"),
        pytest.param({"type": "MEDIA_STATUS", "status": {}}, id="status-object"),
        pytest.param({"type": "MEDIA_STATUS", "status": ["x"]}, id="entry-string"),
        pytest.param({"type": "MEDIA_STATUS", "status": [None]}, id="entry-null"),
    ],
)
def test_a_reply_that_is_not_shaped_like_a_media_status_fails_the_read(
    reply: dict[str, object],
) -> None:
    cast_device = FakeCast()

    def answer(
        data: dict[str, object],
        *,
        callback_function: Callable[[bool, dict[str, object] | None], None],
    ) -> None:
        callback_function(True, reply)

    cast_device.media_controller.send_message_nocheck = answer  # type: ignore[method-assign]
    transport, _ = make_transport(cast_device)

    with pytest.raises(DeviceUnavailableError, match="malformed media status"):
        transport.get_status(DEVICE_ID, timeout=5.0)


def test_a_content_id_that_is_not_a_string_never_confirms_a_command() -> None:
    """Before this check a numeric content id reached the service and crashed its identity
    check; now it is unknown, so the pause is sent once and stays unconfirmed."""
    transport, channel = make_media_transport()
    numeric_id = paused_entry_with_media(contentId=0) | {"playerState": "PLAYING"}
    channel.state = [numeric_id]
    channel.after_command = {"PAUSE": [paused_entry_with_media(contentId=0)]}
    service = ControlService(transport, confirm_timeout=0.3, poll_interval=0.05)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert channel.sent_types().count("PAUSE") == 1


# --- mediaSessionId -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(1, 1, id="valid"),
        pytest.param(0, 0, id="explicit-zero"),
        pytest.param("omitted", None, id="absent"),
        pytest.param(None, None, id="null"),
        pytest.param(True, None, id="boolean"),
        pytest.param("1", None, id="string"),
        pytest.param(1.5, None, id="float"),
        pytest.param(-1, None, id="negative"),
        pytest.param(10**400, 10**400, id="large-integer"),
        pytest.param({}, None, id="object"),
    ],
)
def test_the_media_session_id_is_kept_only_when_it_is_a_non_negative_integer(
    raw: object, expected: int | None
) -> None:
    entry = paused_entry()
    if raw == "omitted":
        del entry["mediaSessionId"]
    else:
        entry["mediaSessionId"] = raw

    media = read_media(entry)

    assert isinstance(media, MediaStatus)
    assert media.media_session_id == expected
    assert media.content_id == MOVIE


def test_pause_answered_from_a_new_session_of_the_same_content_is_not_confirmed() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry(session=1)]
    channel.after_command = {"PAUSE": [paused_entry(mediaSessionId=2)]}
    service = ControlService(transport, confirm_timeout=0.3, poll_interval=0.05)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media session changed" in (result.detail or "")
    assert channel.sent_types().count("PAUSE") == 1


def test_a_queue_moving_to_another_item_in_the_same_session_is_not_confirmed() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry(MOVIE, session=1)]
    channel.after_command = {
        "PAUSE": [playing_entry(EPISODE, session=1) | {"playerState": "PAUSED"}]
    }
    service = ControlService(transport, confirm_timeout=0.3, poll_interval=0.05)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "loaded content changed" in (result.detail or "")
    assert channel.sent_types().count("PAUSE") == 1


def test_an_empty_content_id_stays_unconfirmed_even_with_a_stable_session() -> None:
    """Generic rule, no per-application logic: a session id never replaces the content id."""
    transport, channel = make_media_transport()
    channel.state = [paused_entry_with_media(contentId="") | {"playerState": "PLAYING"}]
    channel.after_command = {"PAUSE": [paused_entry_with_media(contentId="")]}
    service = ControlService(transport, confirm_timeout=0.3, poll_interval=0.05)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media identity was not reported before the command" in (result.detail or "")
    assert channel.sent_types().count("PAUSE") == 1


def test_pause_in_the_same_session_and_content_is_confirmed() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry(session=4)]
    channel.after_command = {"PAUSE": [paused_entry(mediaSessionId=4)]}
    service = ControlService(transport, confirm_timeout=1.0, poll_interval=0.05)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert channel.sent_types().count("PAUSE") == 1


# --- currentItemId (not parsed by PyChromecast 14.0.10: read from the checked raw entry) --


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(101, 101, id="valid"),
        pytest.param(0, 0, id="explicit-zero"),
        pytest.param("omitted", None, id="absent"),
        pytest.param(None, None, id="null"),
        pytest.param(True, None, id="true"),
        pytest.param(False, None, id="false"),
        pytest.param(-1, None, id="negative"),
        pytest.param(1.5, None, id="float"),
        pytest.param("101", None, id="string"),
        pytest.param(10**400, 10**400, id="large-integer"),
        pytest.param({}, None, id="object"),
    ],
)
def test_the_queue_item_id_is_kept_only_when_it_is_a_non_negative_integer(
    raw: object, expected: int | None
) -> None:
    entry = paused_entry()
    if raw != "omitted":
        entry["currentItemId"] = raw

    media = read_media(entry)

    assert isinstance(media, MediaStatus)
    assert media.current_item_id == expected
    assert media.content_id == MOVIE


def test_pause_answered_by_another_queue_item_of_the_same_content_is_not_confirmed() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry(session=1) | {"currentItemId": 101}]
    channel.after_command = {"PAUSE": [paused_entry(mediaSessionId=1, currentItemId=102)]}
    service = ControlService(transport, confirm_timeout=0.3, poll_interval=0.05)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media queue item changed to 102 from 101" in (result.detail or "")
    assert channel.sent_types().count("PAUSE") == 1


def test_pause_on_the_same_queue_item_is_confirmed() -> None:
    transport, channel = make_media_transport()
    channel.state = [playing_entry(session=1) | {"currentItemId": 101}]
    channel.after_command = {"PAUSE": [paused_entry(mediaSessionId=1, currentItemId=101)]}
    service = ControlService(transport, confirm_timeout=1.0, poll_interval=0.05)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert channel.sent_types().count("PAUSE") == 1


# --- Media details: artist, stream type, metadata type, pause support ----------------------
#
# Read from the checked reply, never from PyChromecast's defaults: an omitted, null, blank,
# wrongly typed or unrecognized value is unknown (None), never a plausible invented value.


def entry_with_metadata(**metadata_changes: object) -> dict[str, object]:
    media_block = cast(dict[str, object], playing_entry()["media"])
    metadata = cast(dict[str, object], media_block["metadata"]) | metadata_changes
    return paused_entry(media=media_block | {"metadata": metadata})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("JOYCA", "JOYCA", id="valid"),
        pytest.param("  JOYCA  ", "JOYCA", id="surrounding-spaces"),
        pytest.param("omitted", None, id="absent"),
        pytest.param(None, None, id="null"),
        pytest.param("", None, id="empty"),
        pytest.param("   ", None, id="blank"),
        pytest.param(5, None, id="number"),
        pytest.param({}, None, id="object"),
        pytest.param(True, None, id="boolean"),
    ],
)
def test_the_artist_is_kept_only_when_it_is_a_non_blank_string(
    raw: object, expected: str | None
) -> None:
    entry = entry_with_metadata() if raw == "omitted" else entry_with_metadata(artist=raw)

    media = read_media(entry)

    assert isinstance(media, MediaStatus)
    assert media.artist == expected
    assert media.title == "Movie"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("BUFFERED", StreamType.BUFFERED, id="buffered"),
        pytest.param("LIVE", StreamType.LIVE, id="live"),
        pytest.param("NONE", None, id="cast-none"),
        pytest.param("buffered", None, id="unrecognized-case"),
        pytest.param("omitted", None, id="absent"),
        pytest.param(None, None, id="null"),
        pytest.param("", None, id="empty"),
        pytest.param(5, None, id="number"),
    ],
)
def test_the_stream_type_is_kept_only_when_it_is_a_known_value(
    raw: object, expected: StreamType | None
) -> None:
    media_block = cast(dict[str, object], playing_entry()["media"])
    if raw == "omitted":
        media_block = {k: v for k, v in media_block.items() if k != "streamType"}
    else:
        media_block = media_block | {"streamType": raw}

    media = read_media(paused_entry(media=media_block))

    assert isinstance(media, MediaStatus)
    assert media.stream_type is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(0, MetadataType.GENERIC, id="generic"),
        pytest.param(1, MetadataType.MOVIE, id="movie"),
        pytest.param(2, MetadataType.TV_SHOW, id="tv-show"),
        pytest.param(3, MetadataType.MUSIC_TRACK, id="music-track"),
        pytest.param(4, MetadataType.PHOTO, id="photo"),
        pytest.param(5, MetadataType.AUDIOBOOK_CHAPTER, id="audiobook-chapter"),
        pytest.param(6, None, id="unknown-number"),
        pytest.param(-1, None, id="negative"),
        pytest.param(True, None, id="boolean"),
        pytest.param("0", None, id="string"),
        pytest.param(1.0, None, id="float"),
        pytest.param("omitted", None, id="absent"),
        pytest.param(None, None, id="null"),
    ],
)
def test_the_metadata_type_is_kept_only_when_it_is_a_known_number(
    raw: object, expected: MetadataType | None
) -> None:
    entry = entry_with_metadata() if raw == "omitted" else entry_with_metadata(metadataType=raw)

    media = read_media(entry)

    assert isinstance(media, MediaStatus)
    assert media.metadata_type is expected


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        pytest.param(1, True, id="pause-bit"),
        pytest.param(207, True, id="real-youtube-mask"),
        pytest.param(2, False, id="seek-only"),
        pytest.param(0, False, id="explicit-zero"),
        pytest.param("omitted", None, id="absent"),
        pytest.param(None, None, id="null"),
        pytest.param(True, None, id="boolean"),
        pytest.param("1", None, id="string"),
    ],
)
def test_pause_support_is_known_only_from_a_reported_mask(
    mask: object, expected: bool | None
) -> None:
    entry = paused_entry()
    if mask == "omitted":
        del entry["supportedMediaCommands"]
    else:
        entry["supportedMediaCommands"] = mask

    media = read_media(entry)

    assert isinstance(media, MediaStatus)
    assert media.supports_pause is expected


def test_an_omitted_stream_type_is_not_pychromecasts_unknown_default() -> None:
    """PyChromecast fills `stream_type` with "UNKNOWN" when the reply omits it; that default
    must not reach the domain as an observation."""
    media_block = {
        k: v
        for k, v in cast(dict[str, object], playing_entry()["media"]).items()
        if k != "streamType"
    }

    media = read_media(paused_entry(media=media_block))

    assert isinstance(media, MediaStatus)
    assert media.stream_type is None


def test_the_details_of_a_real_youtube_reply_are_kept() -> None:
    """Shape observed on a real receiver (anonymized): empty content id, title and artist in
    generic metadata, buffered stream, mask 207, session and queue item ids."""
    entry = {
        "mediaSessionId": 19,
        "playerState": "PAUSED",
        "playbackRate": 0,
        "currentTime": 3085.668,
        "supportedMediaCommands": 207,
        "volume": {"level": 0, "muted": False},
        "activeTrackIds": [],
        "media": {
            "contentId": "",
            "streamType": "BUFFERED",
            "metadata": {"metadataType": 0, "title": "A video", "artist": "A channel"},
            "duration": 6259.801,
        },
        "currentItemId": 1,
        "repeatMode": "REPEAT_OFF",
    }

    media = read_media(entry)

    assert isinstance(media, MediaStatus)
    assert media.playback_state is PlaybackState.PAUSED
    assert media.content_id == ""
    assert media.title == "A video"
    assert media.artist == "A channel"
    assert media.stream_type is StreamType.BUFFERED
    assert media.metadata_type is MetadataType.GENERIC
    assert media.supports_seek is True
    assert media.supports_pause is True
    assert media.media_session_id == 19
    assert media.current_item_id == 1
    assert media.position_seconds == 3085.668
    assert media.duration_seconds == 6259.801


def test_details_read_from_the_extended_status_are_checked_the_same_way() -> None:
    entry = paused_entry()
    del entry["media"]
    entry["extendedStatus"] = {
        "media": {
            "contentId": MOVIE,
            "streamType": "LIVE",
            "metadata": {"metadataType": 1, "artist": "  "},
        }
    }

    media = read_media(entry)

    assert isinstance(media, MediaStatus)
    assert media.stream_type is StreamType.LIVE
    assert media.metadata_type is MetadataType.MOVIE
    assert media.artist is None
