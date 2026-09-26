from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pychromecast.response_handler
import pytest
from pychromecast import Chromecast, PyChromecastError, RequestTimeout
from pychromecast.controllers.media import MediaStatus as PyMediaStatus
from pychromecast.error import RequestFailed

import control_tv.adapters.pychromecast as pychromecast_adapter
from control_tv.adapters import PyChromecastTransport
from control_tv.domain import (
    CommandRejectedError,
    DeviceId,
    DeviceKind,
    DeviceNotFoundError,
    DeviceUnavailableError,
    DiscoveryError,
    InvalidArgumentError,
    MediaRequest,
    OperationTimeoutError,
    PlaybackState,
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
        self._clock = clock
        self._consumes = consumes

    def update_status(self, *, callback_function: object) -> None:
        self.updates += 1
        if self._clock is not None and self._consumes:
            self._clock.sleep(self._consumes)
        if self.error is not None:
            raise self.error
        callback_function(True, {})  # type: ignore[operator]


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
    media = cast_device.media_controller.status
    media.player_state = "PLAYING"
    media.content_id = "https://media.local/movie.mp4"
    media.content_type = "video/mp4"
    media.current_time = 12.0
    media.duration = 90.0
    media.media_metadata = {"title": "Movie"}
    media.supported_media_commands = 2
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
    assert recorded_timeouts == [pytest.approx(0.4)]


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
