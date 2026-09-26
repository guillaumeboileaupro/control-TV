from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from control_tv.domain import (
    Command,
    CommandRejectedError,
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
def transport(clock: FakeClock) -> FakeTransport:
    return FakeTransport(clock=clock)


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
    assert transport.status_reads() == 4  # one identity snapshot, then three confirmation reads
    assert clock.sleeps == [0.25, 0.25]


def test_fast_identity_snapshot_and_confirmation_share_one_budget(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.status_read_delays = [0.1, 0.1]
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert clock.now == pytest.approx(0.2)
    assert [args[1] for name, args in transport.calls if name == "get_status"] == [
        pytest.approx(1.0),
        pytest.approx(0.9),
    ]


def test_slow_identity_snapshot_leaves_only_its_remaining_budget_for_confirmation(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.status_read_delays = [0.75, 0.1]
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert clock.now == pytest.approx(0.85)
    assert [args[1] for name, args in transport.calls if name == "get_status"] == [
        pytest.approx(1.0),
        pytest.approx(0.25),
    ]


def test_blocked_identity_snapshot_exhausts_the_single_budget_before_confirmation(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.hang_status_reads = True
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.pause(DEVICE_ID)

    assert transport.calls == [
        ("get_status", (DEVICE_ID, 1.0)),
        ("pause", (DEVICE_ID,)),
    ]
    assert transport.sent() == ["pause"]
    assert transport.status_reads() == 1
    assert clock.now == pytest.approx(1.0)
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is None
    assert result.detail is not None
    assert "confirmation budget (1s) expired" in result.detail


def test_late_identity_snapshot_cannot_confirm_or_start_another_read(
    transport: FakeTransport, clock: FakeClock
) -> None:
    # Defense in depth against a transport that violates the timeout it received.
    transport.status_read_delays = [1.01]
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.pause(DEVICE_ID)

    assert transport.calls == [
        ("get_status", (DEVICE_ID, 1.0)),
        ("pause", (DEVICE_ID,)),
    ]
    assert transport.sent() == ["pause"]
    assert transport.status_reads() == 1
    assert clock.now == pytest.approx(1.01)
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is None


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
    assert "expected playback paused, but playback is playing" in result.detail


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


def test_zero_timeout_reads_the_status_zero_times(
    transport: FakeTransport, clock: FakeClock
) -> None:
    """A zero-second budget leaves nothing to spend on even one bounded read."""
    transport.ignore_commands = True
    service = make_service(transport, clock, confirm_timeout=0)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.status_reads() == 0
    assert clock.sleeps == []
    assert result.detail is not None
    assert "budget (0s) expired before a status could be read" in result.detail


# --- P2 review: each status read must itself be bounded by the confirmation budget ---------
#
# `CastTransport.get_status` takes an explicit `timeout`; `ControlService._verify` must pass
# only whatever remains of `confirm_timeout`, so that a slow or hung read can never itself
# exceed the global budget, and evidence that only arrives after the budget expired is never
# used to confirm anything.


def test_a_blocked_read_is_bounded_by_the_remaining_budget_not_left_hanging(
    transport: FakeTransport, clock: FakeClock
) -> None:
    """A read that never usefully completes must not be retried past the global budget."""
    transport.hang_status_reads = True
    service = make_service(transport, clock, confirm_timeout=1.0, poll_interval=0.4)

    result = service.set_volume(DEVICE_ID, 0.7)

    # The one read that was attempted consumed exactly the whole budget by itself (as a
    # spec-compliant transport must: it never blocks longer than the `timeout` it was
    # given), so nothing remains afterwards for a second attempt: the only recorded
    # "sleep" is the read itself, the service's own scheduler never gets to run.
    assert transport.status_reads() == 1
    assert transport.calls == [
        ("set_volume", (DEVICE_ID, 0.7)),
        ("get_status", (DEVICE_ID, 1.0)),
    ]
    assert clock.now == pytest.approx(1.0)
    assert clock.sleeps == [1.0]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is None
    assert result.detail is not None
    assert "could not read the TV status" in result.detail
    assert "timed out reading status" in result.detail


def test_each_poll_is_given_only_the_time_actually_remaining(
    transport: FakeTransport, clock: FakeClock
) -> None:
    """The `timeout` handed to the transport shrinks as the budget is spent, poll by poll."""
    transport.ignore_commands = True
    service = make_service(transport, clock, confirm_timeout=1.0, poll_interval=0.4)

    service.set_volume(DEVICE_ID, 0.7)

    status_read_timeouts = [args[1] for name, args in transport.calls if name == "get_status"]
    assert status_read_timeouts == [pytest.approx(1.0), pytest.approx(0.6), pytest.approx(0.2)]


def test_expiration_never_exceeds_the_global_budget_regardless_of_poll_interval(
    transport: FakeTransport, clock: FakeClock
) -> None:
    """A hung read is always given the *whole* remaining budget, not a per-poll slice.

    So a single hang exhausts it in one attempt: total elapsed time is exactly
    confirm_timeout, never more, and `poll_interval` (here deliberately smaller than the
    budget) never causes a second attempt to slip in past the deadline.
    """
    transport.hang_status_reads = True
    service = make_service(transport, clock, confirm_timeout=2.5, poll_interval=1.0)

    result = service.set_volume(DEVICE_ID, 0.7)

    assert clock.now == pytest.approx(2.5)
    assert transport.status_reads() == 1
    status_read_timeouts = [args[1] for name, args in transport.calls if name == "get_status"]
    assert status_read_timeouts == [pytest.approx(2.5)]
    assert result.confirmation is Confirmation.UNCONFIRMED


def test_a_match_confirmed_just_before_the_deadline_is_accepted(
    transport: FakeTransport, clock: FakeClock
) -> None:
    """Evidence that arrives with time to spare, however little, still confirms."""
    transport.status_read_delay = 0.99
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.set_volume(DEVICE_ID, 0.7)

    assert clock.now == pytest.approx(0.99)
    assert result.confirmation is Confirmation.CONFIRMED
    assert result.observed is not None


def test_a_match_arriving_after_the_deadline_is_never_confirmed(
    transport: FakeTransport, clock: FakeClock
) -> None:
    """The device answering correctly is not enough on its own: it must answer in time.

    A transport that (against its contract) takes longer than the `timeout` it was given
    must still never let the service report a false CONFIRMED - this is the defense-in-depth
    check, independent of whichever transport is behind `CastTransport`.
    """
    transport.status_read_delay = 1.01
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.set_volume(DEVICE_ID, 0.7)

    assert clock.now == pytest.approx(1.01)
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None  # the status was read; it just came too late
    assert result.detail is not None
    assert "answered after the confirmation window expired" in result.detail


def test_a_device_that_is_not_connected_never_confirms(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.tv.connection = ConnectionState.DISCONNECTED
    service = make_service(transport, clock)

    result = service.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.connection is ConnectionState.DISCONNECTED
    # Being disconnected is reported as exactly that, distinct from a contradicting state.
    assert result.detail is not None
    assert "connection is disconnected" in result.detail


def test_stop_never_fabricates_an_idle_state_the_device_did_not_report(
    transport: FakeTransport, clock: FakeClock
) -> None:
    """A receiver that merely stops reporting a media session is not proof of idle.

    Some receivers drop the media session entirely on stop instead of reporting an
    explicit idle state. The service must not treat that absence as confirmation.
    """
    transport.clears_media_on_stop = True
    service = make_service(transport, clock)

    result = service.stop(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.media is None
    assert result.detail is not None
    assert "expected playback stopped, but no active media session" in result.detail
    assert "idle" not in result.detail


# --- failures are errors, never results -----------------------------------------------------


def test_a_command_that_cannot_be_delivered_raises_and_never_returns_a_result(
    transport: FakeTransport, service: ControlService
) -> None:
    transport.fail_commands_with = DeviceUnavailableError("no route", device_id=DEVICE_ID)

    with pytest.raises(DeviceUnavailableError) as excinfo:
        service.set_volume(DEVICE_ID, 0.7)

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

    result = service.set_volume(DEVICE_ID, 0.7)

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.status_reads() == 2


def test_playback_command_without_precommand_identity_is_sent_but_unconfirmed(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.status_errors = [DeviceUnavailableError("pre-command status unavailable")]
    service = make_service(transport, clock)

    result = service.pause(DEVICE_ID)

    assert transport.sent() == ["pause"]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.detail is not None
    assert "media identity was not reported before the command" in result.detail


@pytest.mark.parametrize("content_id", [None, "", " ", " \t\n"])
def test_blank_precommand_media_identity_cannot_confirm_playback(
    transport: FakeTransport, clock: FakeClock, content_id: str | None
) -> None:
    transport.tv.content_id = content_id
    service = make_service(transport, clock)

    result = service.pause(DEVICE_ID)

    assert transport.sent() == ["pause"]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.detail is not None
    assert "media identity was not reported before the command" in result.detail
    assert clock.now == pytest.approx(1.0)


@pytest.mark.parametrize("replacement", ["", " ", " \t\n"])
def test_media_identity_becoming_blank_during_confirmation_is_not_accepted(
    transport: FakeTransport, clock: FakeClock, replacement: str
) -> None:
    def replace_media_identity() -> None:
        transport.tv.content_id = replacement
        transport.tv.playback = PlaybackState.PAUSED

    transport.ignore_commands = True
    transport.status_effects = [lambda: None, replace_media_identity]
    service = make_service(transport, clock)

    result = service.pause(DEVICE_ID)

    assert transport.sent() == ["pause"]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.content_id == replacement
    assert result.detail is not None
    assert f"loaded content changed to {replacement!r} from {MOVIE_URL!r}" in result.detail


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


@pytest.mark.parametrize(
    ("command", "initial_state", "replacement_state"),
    [
        ("play", PlaybackState.PAUSED, PlaybackState.PLAYING),
        ("pause", PlaybackState.PLAYING, PlaybackState.PAUSED),
        ("stop", PlaybackState.PLAYING, PlaybackState.IDLE),
    ],
)
def test_playback_command_is_not_confirmed_by_replaced_media(
    transport: FakeTransport,
    clock: FakeClock,
    command: str,
    initial_state: PlaybackState,
    replacement_state: PlaybackState,
) -> None:
    replacement = "http://media.local/replacement.mp4"

    def replace_media() -> None:
        transport.tv.content_id = replacement
        transport.tv.playback = replacement_state

    transport.tv.playback = initial_state
    transport.ignore_commands = True
    transport.status_effects = [lambda: None, replace_media]
    service = make_service(transport, clock)

    if command == "play":
        result = service.play(DEVICE_ID)
    elif command == "pause":
        result = service.pause(DEVICE_ID)
    else:
        result = service.stop(DEVICE_ID)

    assert transport.sent() == [command]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.content_id == replacement
    assert result.observed.media.playback_state is replacement_state
    assert result.detail is not None
    assert f"loaded content changed to {replacement!r} from {MOVIE_URL!r}" in result.detail
    assert clock.now == pytest.approx(1.0)


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
    assert result.detail is not None
    assert f"loaded content is {MOVIE_URL!r}, not " in result.detail


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


def test_seek_is_not_confirmed_by_matching_position_on_replaced_media(
    transport: FakeTransport, clock: FakeClock
) -> None:
    replacement = "http://media.local/replacement.mp4"

    def replace_media() -> None:
        transport.tv.content_id = replacement
        transport.tv.position = 120.0

    transport.ignore_commands = True
    transport.status_effects = [lambda: None, replace_media]
    service = make_service(transport, clock)

    result = service.seek(DEVICE_ID, 120.0)

    assert transport.sent() == ["seek"]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.content_id == replacement
    assert result.observed.media.position_seconds == 120.0
    assert result.detail is not None
    assert f"loaded content changed to {replacement!r} from {MOVIE_URL!r}" in result.detail
    assert clock.now == pytest.approx(1.0)


def test_seek_without_precommand_media_identity_remains_unconfirmed(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.tv.content_id = None
    service = make_service(transport, clock)

    result = service.seek(DEVICE_ID, 120.0)

    assert transport.sent() == ["seek"]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.position_seconds == 120.0
    assert result.detail is not None
    assert "media identity was not reported before the command" in result.detail


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


def test_receiver_rejection_raises_without_confirmation_or_invented_result(
    transport: FakeTransport, service: ControlService
) -> None:
    transport.fail_commands_with = CommandRejectedError(
        "receiver rejected load", device_id=DEVICE_ID
    )
    request = MediaRequest(url="https://media.local/rejected.mp4", content_type="video/mp4")

    with pytest.raises(CommandRejectedError) as excinfo:
        service.load_media(DEVICE_ID, request)

    assert excinfo.value.code is ErrorCode.COMMAND_REJECTED
    assert transport.sent() == []
    assert transport.status_reads() == 0
