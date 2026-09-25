from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from control_tv.domain import (
    Command,
    Confirmation,
    ConnectionState,
    DeviceId,
    DeviceNotFoundError,
    DeviceUnavailableError,
    DiscoveryError,
    ErrorCode,
    InvalidArgumentError,
    MediaRequest,
    PlaybackState,
    UnsupportedOperationError,
)
from control_tv.ports import CastTransport, TvControl
from control_tv.service import ControlService
from fakes import DEVICE_ID, MOVIE_URL, FakeClock, FakeTransport


def make_service(
    transport: FakeTransport,
    clock: FakeClock,
    *,
    confirm_timeout: float | None = 1.0,
    poll_interval: float = 0.25,
) -> ControlService:
    return ControlService(
        transport,
        confirm_timeout=confirm_timeout,
        poll_interval=poll_interval,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def service(transport: FakeTransport, clock: FakeClock) -> ControlService:
    return make_service(transport, clock)


def test_interfaces_are_satisfied_by_the_fake_and_the_service(
    transport: FakeTransport, service: ControlService
) -> None:
    # Static conformance, checked by mypy: a valid transport and a valid control surface.
    cast: CastTransport = transport
    control: TvControl = service

    assert cast is transport
    assert control is service


# --- confirmation: sent versus confirmed -------------------------------------------------


def test_command_is_confirmed_when_the_tv_shows_the_expected_state(
    service: ControlService, transport: FakeTransport, clock: FakeClock
) -> None:
    result = service.pause(DEVICE_ID)

    assert result.command is Command.PAUSE
    assert result.device_id == DEVICE_ID
    assert result.confirmation is Confirmation.CONFIRMED
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.playback_state is PlaybackState.PAUSED
    assert transport.sent() == ["pause"]
    assert clock.sleeps == []


def test_confirmation_waits_for_a_slow_tv(transport: FakeTransport, clock: FakeClock) -> None:
    transport.effect_delay_polls = 2
    service = make_service(transport, clock)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.status_reads() == 3
    assert clock.sleeps == [0.25, 0.25]


def test_a_command_the_tv_ignores_is_sent_but_unconfirmed(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.ignore_commands = True
    service = make_service(transport, clock)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.confirmed is False
    assert transport.sent() == ["pause"]
    # The result shows what the TV really reported, not what was asked for.
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.playback_state is PlaybackState.PLAYING
    assert result.detail is not None
    assert "did not show playback paused within 1s" in result.detail


def test_confirmation_is_bounded_by_the_timeout(transport: FakeTransport, clock: FakeClock) -> None:
    transport.ignore_commands = True
    service = make_service(transport, clock, confirm_timeout=1.0, poll_interval=0.4)

    service.pause(DEVICE_ID)

    assert clock.now == pytest.approx(1.0)
    assert clock.sleeps == pytest.approx([0.4, 0.4, 0.2])


def test_verification_can_be_disabled(transport: FakeTransport, clock: FakeClock) -> None:
    service = make_service(transport, clock, confirm_timeout=None)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.NOT_CHECKED
    assert result.observed is None
    assert transport.status_reads() == 0
    assert transport.sent() == ["pause"]


def test_zero_timeout_reads_the_status_once(transport: FakeTransport, clock: FakeClock) -> None:
    transport.ignore_commands = True
    service = make_service(transport, clock, confirm_timeout=0)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.status_reads() == 1
    assert clock.sleeps == []


def test_a_device_that_is_not_connected_never_confirms(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.tv.connection = ConnectionState.DISCONNECTED
    service = make_service(transport, clock)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.connection is ConnectionState.DISCONNECTED


# --- failures are errors, never results -----------------------------------------------------


def test_a_command_that_cannot_be_delivered_raises_and_never_returns_a_result(
    transport: FakeTransport, service: ControlService
) -> None:
    transport.fail_commands_with = DeviceUnavailableError("no route", device_id=DEVICE_ID)

    with pytest.raises(DeviceUnavailableError) as excinfo:
        service.pause(DEVICE_ID)

    assert excinfo.value.code is ErrorCode.DEVICE_UNAVAILABLE
    assert transport.sent() == []
    assert transport.status_reads() == 0


def test_an_unknown_device_is_reported_as_not_found(service: ControlService) -> None:
    with pytest.raises(DeviceNotFoundError):
        service.pause(DeviceId("uuid-unknown"))


def test_failing_to_read_the_status_after_sending_is_unconfirmed_not_an_error(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.status_errors = [DeviceUnavailableError("link lost")] * 10
    service = make_service(transport, clock)

    result = service.pause(DEVICE_ID)

    assert transport.sent() == ["pause"]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is None
    assert result.detail is not None
    assert "could not read the TV status: link lost" in result.detail


def test_a_transient_status_failure_recovers(transport: FakeTransport, clock: FakeClock) -> None:
    transport.status_errors = [DeviceUnavailableError("blip")]
    service = make_service(transport, clock)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.status_reads() == 2


# --- per-command expectations ---------------------------------------------------------------


def test_play_is_not_confirmed_while_buffering(transport: FakeTransport, clock: FakeClock) -> None:
    transport.tv.playback = PlaybackState.BUFFERING
    transport.ignore_commands = True
    service = make_service(transport, clock)

    assert service.play(DEVICE_ID).confirmation is Confirmation.UNCONFIRMED


def test_play_is_confirmed_when_playing(service: ControlService, transport: FakeTransport) -> None:
    transport.tv.playback = PlaybackState.PAUSED

    result = service.play(DEVICE_ID)

    assert result.command is Command.PLAY
    assert result.confirmed


def test_stop_is_confirmed_when_the_tv_is_idle(
    service: ControlService, transport: FakeTransport
) -> None:
    result = service.stop(DEVICE_ID)

    assert result.command is Command.STOP
    assert result.confirmed
    assert transport.tv.playback is PlaybackState.IDLE


def test_load_media_is_confirmed_by_the_content_the_tv_reports(
    service: ControlService, transport: FakeTransport
) -> None:
    request = MediaRequest(url="http://media.local/other.mp4", content_type="video/mp4")

    result = service.load_media(DEVICE_ID, request)

    assert result.command is Command.LOAD_MEDIA
    assert result.confirmed
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.content_id == request.url


def test_load_media_is_unconfirmed_while_the_tv_still_shows_other_content(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.ignore_commands = True
    service = make_service(transport, clock)

    result = service.load_media(
        DEVICE_ID, MediaRequest(url="http://media.local/other.mp4", content_type="video/mp4")
    )

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.content_id == MOVIE_URL


def test_seek_is_confirmed_within_tolerance(
    service: ControlService, transport: FakeTransport
) -> None:
    result = service.seek(DEVICE_ID, 120.0)

    assert result.command is Command.SEEK
    assert result.confirmed
    assert transport.calls[-2] == ("seek", (DEVICE_ID, 120.0))


def test_seek_is_unconfirmed_when_the_position_does_not_move(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.ignore_commands = True
    service = make_service(transport, clock)

    assert service.seek(DEVICE_ID, 120.0).confirmation is Confirmation.UNCONFIRMED


def test_seek_is_refused_before_sending_when_the_media_cannot_seek(
    service: ControlService, transport: FakeTransport
) -> None:
    transport.tv.supports_seek = False

    with pytest.raises(UnsupportedOperationError) as excinfo:
        service.seek(DEVICE_ID, 5.0)

    assert excinfo.value.code is ErrorCode.UNSUPPORTED_OPERATION
    assert transport.sent() == []


def test_seek_is_attempted_when_seek_support_is_unknown(
    service: ControlService, transport: FakeTransport
) -> None:
    transport.tv.supports_seek = None

    assert service.seek(DEVICE_ID, 5.0).confirmed


@pytest.mark.parametrize(("level", "expected"), [(0.0, 0.0), (0.3, 0.3), (1.0, 1.0)])
def test_volume_is_confirmed_when_the_tv_reports_it(
    service: ControlService, transport: FakeTransport, level: float, expected: float
) -> None:
    result = service.set_volume(DEVICE_ID, level)

    assert result.command is Command.SET_VOLUME
    assert result.confirmed
    assert transport.tv.volume == expected


def test_volume_is_unconfirmed_when_the_tv_keeps_its_level(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.ignore_commands = True
    service = make_service(transport, clock)

    assert service.set_volume(DEVICE_ID, 0.9).confirmation is Confirmation.UNCONFIRMED


@pytest.mark.parametrize("muted", [True, False])
def test_mute_is_confirmed_in_both_directions(
    service: ControlService, transport: FakeTransport, muted: bool
) -> None:
    transport.tv.muted = not muted

    result = service.set_muted(DEVICE_ID, muted)

    assert result.command is Command.SET_MUTED
    assert result.confirmed
    assert transport.tv.muted is muted


# --- arguments are validated before anything is sent ------------------------------------


@pytest.mark.parametrize("level", [-0.1, 1.1, math.nan, math.inf])
def test_invalid_volume_is_rejected_before_sending(
    service: ControlService, transport: FakeTransport, level: float
) -> None:
    with pytest.raises(InvalidArgumentError):
        service.set_volume(DEVICE_ID, level)

    assert transport.calls == []


@pytest.mark.parametrize("position", [-1.0, math.nan, math.inf])
def test_invalid_seek_position_is_rejected_before_sending(
    service: ControlService, transport: FakeTransport, position: float
) -> None:
    with pytest.raises(InvalidArgumentError):
        service.seek(DEVICE_ID, position)

    assert transport.calls == []


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_device_id_is_rejected_before_touching_the_transport(
    service: ControlService, transport: FakeTransport, blank: str
) -> None:
    device_id = DeviceId(blank)
    for call in (
        lambda: service.pause(device_id),
        lambda: service.play(device_id),
        lambda: service.stop(device_id),
        lambda: service.seek(device_id, 1.0),
        lambda: service.set_volume(device_id, 0.5),
        lambda: service.set_muted(device_id, True),
        lambda: service.get_status(device_id),
        lambda: service.load_media(device_id, MediaRequest(url="u", content_type="video/mp4")),
    ):
        with pytest.raises(InvalidArgumentError):
            call()

    assert transport.calls == []


# --- discovery and status -----------------------------------------------------------------


def test_discovery_returns_the_transport_devices(
    service: ControlService, transport: FakeTransport
) -> None:
    devices = service.discover_devices(timeout=2.0)

    assert [device.id for device in devices] == [DEVICE_ID]
    assert transport.calls == [("discover", (2.0,))]


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.nan, math.inf])
def test_discovery_timeout_must_be_bounded(
    service: ControlService, transport: FakeTransport, timeout: float
) -> None:
    with pytest.raises(InvalidArgumentError):
        service.discover_devices(timeout=timeout)

    assert transport.calls == []


def test_discovery_errors_propagate(service: ControlService, transport: FakeTransport) -> None:
    transport.discover_error = DiscoveryError("mdns unavailable")

    with pytest.raises(DiscoveryError):
        service.discover_devices()


def test_status_is_passed_through_unchanged(
    service: ControlService, transport: FakeTransport
) -> None:
    status = service.get_status(DEVICE_ID)

    assert status.device_id == DEVICE_ID
    assert status.media is not None
    assert status.media.playback_state is PlaybackState.PLAYING


# --- construction -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [
        lambda t: ControlService(t, confirm_timeout=-1.0),
        lambda t: ControlService(t, confirm_timeout=math.nan),
        lambda t: ControlService(t, poll_interval=0.0),
        lambda t: ControlService(t, poll_interval=math.inf),
        lambda t: ControlService(t, seek_tolerance=-0.1),
        lambda t: ControlService(t, volume_tolerance=math.nan),
    ],
    ids=[
        "negative-confirm-timeout",
        "nan-confirm-timeout",
        "zero-poll-interval",
        "infinite-poll-interval",
        "negative-seek-tolerance",
        "nan-volume-tolerance",
    ],
)
def test_invalid_configuration_is_rejected(
    transport: FakeTransport, build: Callable[[FakeTransport], ControlService]
) -> None:
    with pytest.raises(InvalidArgumentError):
        build(transport)
