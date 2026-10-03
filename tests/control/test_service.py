from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import pytest

from control_tv.domain import (
    Command,
    CommandRejectedError,
    CommandResult,
    Confirmation,
    ConnectionState,
    DeviceId,
    DeviceNotFoundError,
    DeviceStatus,
    DeviceUnavailableError,
    DiscoveryError,
    ErrorCode,
    InvalidArgumentError,
    MediaRequest,
    MediaStatus,
    OperationTimeoutError,
    PlaybackState,
    UnsupportedOperationError,
    VolumeControlType,
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


def run_playback_command(service: ControlService, command: Command) -> CommandResult:
    if command is Command.PLAY:
        return service.play(DEVICE_ID)
    if command is Command.PAUSE:
        return service.pause(DEVICE_ID)
    if command is Command.STOP:
        return service.stop(DEVICE_ID)
    raise AssertionError(f"not a playback command: {command}")


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

    result = service.set_muted(DEVICE_ID, True)

    # The one read that was attempted consumed exactly the whole budget by itself (as a
    # spec-compliant transport must: it never blocks longer than the `timeout` it was
    # given), so nothing remains afterwards for a second attempt: the only recorded
    # "sleep" is the read itself, the service's own scheduler never gets to run.
    assert transport.status_reads() == 1
    assert transport.calls == [
        ("set_muted", (DEVICE_ID, True)),
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

    service.set_muted(DEVICE_ID, True)

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

    result = service.set_muted(DEVICE_ID, True)

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

    result = service.set_muted(DEVICE_ID, True)

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
    """A receiver that ends the media session on stop instead of reporting IDLE.

    With the media identified before the command and no contradiction since, that fresh end
    of the session confirms the stop; the observed status still says there is no media
    session, never a fabricated IDLE state.
    """
    transport.clears_media_on_stop = True
    service = make_service(transport, clock)

    result = service.stop(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert result.observed is not None
    assert result.observed.media is None
    assert transport.attempted() == ["stop"]


# --- failures are errors, never results -----------------------------------------------------


def test_a_command_that_cannot_be_delivered_raises_and_never_returns_a_result(
    transport: FakeTransport, service: ControlService
) -> None:
    transport.fail_commands_with = DeviceUnavailableError("no route", device_id=DEVICE_ID)

    with pytest.raises(DeviceUnavailableError) as excinfo:
        service.set_muted(DEVICE_ID, True)

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


@pytest.mark.parametrize("command", [Command.PLAY, Command.PAUSE, Command.STOP])
@pytest.mark.parametrize(
    ("error_type", "message"),
    [
        (DeviceUnavailableError, "device vanished before delivery"),
        (CommandRejectedError, "receiver rejected command"),
        (OperationTimeoutError, "delivery result is ambiguous"),
    ],
)
def test_each_playback_delivery_error_propagates_without_result_or_confirmation(
    transport: FakeTransport,
    clock: FakeClock,
    command: Command,
    error_type: type[DeviceUnavailableError | CommandRejectedError | OperationTimeoutError],
    message: str,
) -> None:
    transport.fail_commands_with = error_type(message, device_id=DEVICE_ID)
    service = make_service(transport, clock)

    with pytest.raises(error_type):
        run_playback_command(service, command)

    assert transport.status_reads() == 1
    assert transport.attempted() == [command.value]
    assert transport.sent() == []


@pytest.mark.parametrize(
    ("command", "precommand_state", "contradictory_state"),
    [
        (Command.PLAY, PlaybackState.PLAYING, PlaybackState.PAUSED),
        (Command.PAUSE, PlaybackState.PAUSED, PlaybackState.PLAYING),
        (Command.STOP, PlaybackState.IDLE, PlaybackState.PLAYING),
    ],
)
def test_precommand_expected_state_never_confirms_a_contradictory_postcommand_state(
    transport: FakeTransport,
    clock: FakeClock,
    command: Command,
    precommand_state: PlaybackState,
    contradictory_state: PlaybackState,
) -> None:
    def show_precommand_state() -> None:
        transport.tv.playback = precommand_state

    def show_postcommand_state() -> None:
        transport.tv.playback = contradictory_state

    transport.ignore_commands = True
    transport.status_effects = [show_precommand_state, show_postcommand_state]
    service = make_service(transport, clock)

    result = run_playback_command(service, command)

    assert transport.sent() == [command.value]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.playback_state is contradictory_state


@pytest.mark.parametrize("command", [Command.PLAY, Command.PAUSE, Command.STOP])
def test_device_disappearing_after_each_playback_command_remains_unconfirmed(
    transport: FakeTransport, clock: FakeClock, command: Command
) -> None:
    def disconnect_after_delivery() -> None:
        transport.tv.connection = ConnectionState.DISCONNECTED

    transport.ignore_commands = True
    transport.status_effects = [lambda: None, disconnect_after_delivery]
    service = make_service(transport, clock)

    result = run_playback_command(service, command)

    assert transport.sent() == [command.value]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is not None
    assert result.observed.connection is ConnectionState.DISCONNECTED
    assert result.detail is not None
    assert "connection is disconnected" in result.detail


@pytest.mark.parametrize("command", [Command.PLAY, Command.PAUSE, Command.STOP])
def test_late_identity_snapshot_never_confirms_any_playback_command(
    transport: FakeTransport, clock: FakeClock, command: Command
) -> None:
    transport.status_read_delays = [1.01]
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = run_playback_command(service, command)

    assert transport.sent() == [command.value]
    assert transport.status_reads() == 1
    assert clock.now == pytest.approx(1.01)
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is None


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


def test_seek_zero_budget_rejects_before_status_read_or_command(
    transport: FakeTransport, clock: FakeClock
) -> None:
    service = make_service(transport, clock, confirm_timeout=0)

    with pytest.raises(OperationTimeoutError):
        service.seek(DEVICE_ID, 120.0)

    assert transport.calls == []
    assert clock.now == pytest.approx(0.0)


def test_seek_fast_snapshot_and_confirmation_share_one_budget(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.status_read_delays = [0.1, 0.1]
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.seek(DEVICE_ID, 120.0)

    assert result.confirmation is Confirmation.CONFIRMED
    assert clock.now == pytest.approx(0.2)
    assert [args[1] for name, args in transport.calls if name == "get_status"] == [
        pytest.approx(1.0),
        pytest.approx(0.9),
    ]


def test_seek_slow_snapshot_leaves_only_the_remaining_confirmation_budget(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.status_read_delays = [0.75, 0.1]
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.seek(DEVICE_ID, 120.0)

    assert result.confirmation is Confirmation.CONFIRMED
    assert clock.now == pytest.approx(0.85)
    assert [args[1] for name, args in transport.calls if name == "get_status"] == [
        pytest.approx(1.0),
        pytest.approx(0.25),
    ]


def test_seek_snapshot_that_exhausts_the_budget_starts_no_confirmation_read(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.status_read_delays = [1.0]
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.seek(DEVICE_ID, 120.0)

    assert transport.calls == [
        ("get_status", (DEVICE_ID, 1.0)),
        ("seek", (DEVICE_ID, 120.0)),
    ]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is None
    assert clock.now == pytest.approx(1.0)


def test_seek_snapshot_at_deadline_still_blocks_explicitly_unsupported_seek(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.tv.supports_seek = False
    transport.status_read_delays = [1.0]
    service = make_service(transport, clock, confirm_timeout=1.0)

    with pytest.raises(UnsupportedOperationError):
        service.seek(DEVICE_ID, 120.0)

    assert transport.calls == [("get_status", (DEVICE_ID, 1.0))]
    assert transport.sent() == []
    assert clock.now == pytest.approx(1.0)


def test_seek_blocked_snapshot_is_bounded_and_sends_no_command(
    transport: FakeTransport, clock: FakeClock
) -> None:
    transport.hang_status_reads = True
    service = make_service(transport, clock, confirm_timeout=1.0)

    with pytest.raises(OperationTimeoutError):
        service.seek(DEVICE_ID, 120.0)

    assert transport.calls == [("get_status", (DEVICE_ID, 1.0))]
    assert transport.sent() == []
    assert clock.now == pytest.approx(1.0)


def test_seek_late_snapshot_cannot_confirm_or_start_another_read(
    transport: FakeTransport, clock: FakeClock
) -> None:
    # Defense in depth against a transport that violates the timeout it received.
    transport.status_read_delays = [1.01]
    service = make_service(transport, clock, confirm_timeout=1.0)

    result = service.seek(DEVICE_ID, 120.0)

    assert transport.calls == [
        ("get_status", (DEVICE_ID, 1.0)),
        ("seek", (DEVICE_ID, 120.0)),
    ]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.observed is None
    assert clock.now == pytest.approx(1.01)


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


@pytest.mark.parametrize("content_id", [None, "", " ", "\t\n"])
def test_seek_without_usable_precommand_media_identity_remains_unconfirmed(
    transport: FakeTransport, clock: FakeClock, content_id: str | None
) -> None:
    transport.tv.content_id = content_id
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


# --- Input contract at the service boundary ---------------------------------------------
#
# The GUI bridge already refuses these values, but every other caller (a future MCP adapter,
# scripts) reaches the service directly, so the service itself must refuse them before any
# status read or transport call.

HUGE_INTEGER = 10**400  # a valid JSON number that no float can hold


@pytest.mark.parametrize(
    "level",
    [True, False, "0.5", None, HUGE_INTEGER, math.nan, math.inf, -0.1, 1.1],
    ids=["true", "false", "string", "none", "huge-integer", "nan", "infinity", "below", "above"],
)
def test_set_volume_accepts_only_a_finite_number_from_0_to_1(
    service: ControlService, transport: FakeTransport, level: object
) -> None:
    with pytest.raises(InvalidArgumentError):
        service.set_volume(DEVICE_ID, level)  # type: ignore[arg-type]

    assert transport.calls == []
    assert transport.attempted() == []


@pytest.mark.parametrize("level", [0, 1, 0.25])
def test_set_volume_sends_an_integer_or_float_level_as_a_float(
    service: ControlService, transport: FakeTransport, level: float
) -> None:
    service.set_volume(DEVICE_ID, level)

    sent = [args for name, args in transport.calls if name == "set_volume"]
    assert sent == [(DEVICE_ID, float(level))]
    assert isinstance(sent[0][1], float)


@pytest.mark.parametrize(
    "muted",
    [1, 0, 1.0, "true", "false", None],
    ids=["one", "zero", "float-one", "string-true", "string-false", "none"],
)
def test_set_muted_accepts_only_a_real_boolean(
    service: ControlService, transport: FakeTransport, muted: object
) -> None:
    with pytest.raises(InvalidArgumentError):
        service.set_muted(DEVICE_ID, muted)  # type: ignore[arg-type]

    assert transport.calls == []
    assert transport.attempted() == []


@pytest.mark.parametrize("muted", [True, False])
def test_set_muted_sends_a_real_boolean(
    service: ControlService, transport: FakeTransport, muted: bool
) -> None:
    service.set_muted(DEVICE_ID, muted)

    assert [args for name, args in transport.calls if name == "set_muted"] == [(DEVICE_ID, muted)]


@pytest.mark.parametrize(
    "position",
    [True, False, "5", None, HUGE_INTEGER, math.nan, math.inf, -math.inf, -1.0],
    ids=["true", "false", "string", "none", "huge-integer", "nan", "inf", "-inf", "negative"],
)
def test_seek_rejects_an_invalid_position_before_any_status_read_or_command(
    service: ControlService, transport: FakeTransport, position: object
) -> None:
    with pytest.raises(InvalidArgumentError):
        service.seek(DEVICE_ID, position)  # type: ignore[arg-type]

    assert transport.calls == []
    assert transport.attempted() == []


def test_seek_sends_an_integer_position_as_a_float(
    service: ControlService, transport: FakeTransport
) -> None:
    service.seek(DEVICE_ID, 0)

    sent = [args for name, args in transport.calls if name == "seek"]
    assert sent == [(DEVICE_ID, 0.0)]
    assert isinstance(sent[0][1], float)


def test_seek_sets_no_maximum_position_of_its_own(
    service: ControlService, transport: FakeTransport
) -> None:
    """The domain defines no maximum: a large finite position is the receiver's to judge."""
    service.seek(DEVICE_ID, 1e300)

    assert [args for name, args in transport.calls if name == "seek"] == [(DEVICE_ID, 1e300)]


@pytest.mark.parametrize(
    "timeout",
    [True, "5", None, HUGE_INTEGER, math.nan, math.inf, 0, -1.0],
    ids=["true", "string", "none", "huge-integer", "nan", "inf", "zero", "negative"],
)
def test_discover_devices_rejects_an_invalid_timeout_before_discovery(
    service: ControlService, transport: FakeTransport, timeout: object
) -> None:
    with pytest.raises(InvalidArgumentError):
        service.discover_devices(timeout=timeout)  # type: ignore[arg-type]

    assert transport.calls == []


# --- Media identity: content id and media session ---------------------------------------
#
# A receiver's mediaSessionId names a playback session, which can hold several queued items
# (QUEUE_INSERT/QUEUE_UPDATE stay in the same session), so it never identifies a media item
# on its own. The content id stays required; a session reported before the command must be
# reported, unchanged, by the confirming read. A richer identity may only turn a match into
# a mismatch, never the reverse.

SESSION = 7
OTHER_SESSION = 8
OTHER_CONTENT = "https://media.example/other.mp4"

PLAYBACK_COMMANDS: dict[str, tuple[PlaybackState, Callable[[ControlService], CommandResult]]] = {
    "play": (PlaybackState.PAUSED, lambda control: control.play(DEVICE_ID)),
    "pause": (PlaybackState.PLAYING, lambda control: control.pause(DEVICE_ID)),
    "stop": (PlaybackState.PLAYING, lambda control: control.stop(DEVICE_ID)),
    "seek": (PlaybackState.PLAYING, lambda control: control.seek(DEVICE_ID, 30.0)),
}


def identity_run(
    command: str,
    *,
    before: tuple[str | None, int | None],
    after: tuple[str | None, int | None] | None = None,
) -> tuple[CommandResult, FakeTransport]:
    """Send one command with the TV reporting `before` on the pre-command read and `after`
    (default: unchanged) on every read that follows it."""
    clock = FakeClock()
    initial_state, send = PLAYBACK_COMMANDS[command]
    transport = FakeTransport(clock=clock)
    transport.tv.playback = initial_state
    transport.tv.content_id, transport.tv.media_session_id = before

    def report_after() -> None:
        if after is not None:
            transport.tv.content_id, transport.tv.media_session_id = after

    transport.status_effects = [lambda: None] + [report_after] * 20
    result = send(make_service(transport, clock))
    return result, transport


ALL_COMMANDS = pytest.mark.parametrize("command", sorted(PLAYBACK_COMMANDS))


@ALL_COMMANDS
def test_same_content_and_same_session_confirm(command: str) -> None:
    result, transport = identity_run(command, before=(MOVIE_URL, SESSION))

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_same_content_in_a_new_session_does_not_confirm(command: str) -> None:
    result, transport = identity_run(
        command, before=(MOVIE_URL, SESSION), after=(MOVIE_URL, OTHER_SESSION)
    )

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media session changed" in (result.detail or "")
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_other_content_in_the_same_session_does_not_confirm(command: str) -> None:
    """A queue moving to its next item keeps the session but changes the media."""
    result, transport = identity_run(
        command, before=(MOVIE_URL, SESSION), after=(OTHER_CONTENT, SESSION)
    )

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "loaded content changed" in (result.detail or "")
    assert transport.attempted() == [command]


@ALL_COMMANDS
@pytest.mark.parametrize("content_id", [None, "", "   "], ids=["absent", "empty", "blank"])
def test_a_session_alone_never_identifies_the_media(command: str, content_id: str | None) -> None:
    result, transport = identity_run(command, before=(content_id, SESSION))

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media identity was not reported before the command" in (result.detail or "")
    assert transport.attempted() == [command]


@ALL_COMMANDS
@pytest.mark.parametrize(
    "after_session", [None, SESSION], ids=["never-reported", "reported-only-after"]
)
def test_a_session_unknown_before_the_command_keeps_the_content_rule(
    command: str, after_session: int | None
) -> None:
    result, _ = identity_run(command, before=(MOVIE_URL, None), after=(MOVIE_URL, after_session))

    assert result.confirmation is Confirmation.CONFIRMED


@ALL_COMMANDS
def test_a_session_missing_after_the_command_does_not_confirm(command: str) -> None:
    result, transport = identity_run(command, before=(MOVIE_URL, SESSION), after=(MOVIE_URL, None))

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media session was not reported after the command" in (result.detail or "")
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_no_identity_before_the_command_does_not_confirm(command: str) -> None:
    result, transport = identity_run(command, before=(None, None), after=(MOVIE_URL, SESSION))

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_a_session_change_while_polling_never_confirms(command: str) -> None:
    """The TV shows the expected state only in a new session: every read is a mismatch."""
    clock = FakeClock()
    initial_state, send = PLAYBACK_COMMANDS[command]
    transport = FakeTransport(clock=clock, effect_delay_polls=2)
    transport.tv.playback = initial_state
    transport.tv.content_id, transport.tv.media_session_id = MOVIE_URL, SESSION
    transport.status_effects = [lambda: None, lambda: None] + [
        lambda: setattr(transport.tv, "media_session_id", OTHER_SESSION)
    ] * 20

    result = send(make_service(transport, clock))

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == [command]
    assert transport.status_reads() > 2


@ALL_COMMANDS
def test_a_matching_session_that_only_arrives_after_the_deadline_does_not_confirm(
    command: str,
) -> None:
    clock = FakeClock()
    initial_state, send = PLAYBACK_COMMANDS[command]
    transport = FakeTransport(clock=clock)
    transport.tv.playback = initial_state
    transport.tv.content_id, transport.tv.media_session_id = MOVIE_URL, SESSION
    transport.status_effects = [lambda: None] + [
        lambda: setattr(transport.tv, "media_session_id", None)
    ] * 200

    result = send(make_service(transport, clock, confirm_timeout=1.0))

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert clock.now <= 1.0 + 1e-9
    assert transport.attempted() == [command]


@pytest.mark.parametrize(
    "session", [True, False, -1, 1.5, "7"], ids=["true", "false", "negative", "float", "string"]
)
def test_a_media_session_id_must_be_a_non_negative_integer(session: object) -> None:
    with pytest.raises(InvalidArgumentError):
        MediaStatus(content_id=MOVIE_URL, media_session_id=session)  # type: ignore[arg-type]


@pytest.mark.parametrize("session", [None, 0, 7])
def test_a_media_session_id_can_be_unknown_zero_or_positive(session: int | None) -> None:
    assert MediaStatus(media_session_id=session).media_session_id == session


def session_sequence_run(
    command: str,
    sessions_after: list[int | None],
    service_transport: tuple[ControlService, FakeTransport] | None = None,
) -> tuple[CommandResult, FakeTransport]:
    """One command with the pre-command read in SESSION, then the given sessions on the
    following reads (the last one repeats). The TV acts on the command at once, so every
    read after it already shows the expected state or position."""
    if service_transport is None:
        clock = FakeClock()
        transport = FakeTransport(clock=clock)
        control = make_service(transport, clock)
    else:
        control, transport = service_transport
    initial_state, send = PLAYBACK_COMMANDS[command]
    transport.tv.playback = initial_state
    transport.tv.content_id, transport.tv.media_session_id = MOVIE_URL, SESSION
    reads = [*sessions_after, *[sessions_after[-1]] * 20]
    transport.status_effects = [lambda: None] + [
        partial(setattr, transport.tv, "media_session_id", session) for session in reads
    ]
    return send(control), transport


@ALL_COMMANDS
def test_a_session_replaced_then_restored_during_confirmation_never_confirms(command: str) -> None:
    """A -> B -> A: once another session was explicitly reported, a later read back in A
    cannot show which change the command caused."""
    result, transport = session_sequence_run(command, [OTHER_SESSION, SESSION])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media session changed" in (result.detail or "")
    assert transport.attempted() == [command]
    assert transport.status_reads() > 2


@ALL_COMMANDS
def test_a_session_replaced_for_good_during_confirmation_never_confirms(command: str) -> None:
    result, transport = session_sequence_run(command, [OTHER_SESSION, OTHER_SESSION])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_a_stable_session_confirms(command: str) -> None:
    result, transport = session_sequence_run(command, [SESSION])

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_a_session_briefly_unreported_is_not_a_replacement(command: str) -> None:
    """A -> not reported -> A: a missing session is no evidence either way; that read is
    rejected on its own, and a later read in A may confirm (unchanged contract)."""
    result, transport = session_sequence_run(command, [None, SESSION])

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command]


def test_seek_to_the_exact_target_after_a_session_round_trip_is_not_confirmed() -> None:
    result, transport = session_sequence_run("seek", [OTHER_SESSION, SESSION])

    assert transport.tv.position == 30.0
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == ["seek"]


@ALL_COMMANDS
def test_each_command_starts_with_its_own_confirmation_state(command: str) -> None:
    """An ambiguity seen while confirming one command never carries over to the next."""
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    control = make_service(transport, clock)

    first, _ = session_sequence_run(command, [OTHER_SESSION, SESSION], (control, transport))
    second, _ = session_sequence_run(command, [SESSION], (control, transport))

    assert first.confirmation is Confirmation.UNCONFIRMED
    assert second.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command, command]


Identity = tuple[str | None, int | None]


def identity_sequence_run(
    command: str,
    reads_after: list[Identity],
    service_transport: tuple[ControlService, FakeTransport] | None = None,
) -> tuple[CommandResult, FakeTransport]:
    """One command with the pre-command read reporting (MOVIE_URL, SESSION), then the given
    (content id, session) pairs on the following reads (the last one repeats). The TV acts on
    the command at once, so every read after it already shows the expected state or position.
    """
    if service_transport is None:
        clock = FakeClock()
        transport = FakeTransport(clock=clock)
        control = make_service(transport, clock)
    else:
        control, transport = service_transport
    initial_state, send = PLAYBACK_COMMANDS[command]
    transport.tv.playback = initial_state
    transport.tv.content_id, transport.tv.media_session_id = MOVIE_URL, SESSION

    def report(identity: Identity) -> None:
        transport.tv.content_id, transport.tv.media_session_id = identity

    reads = [*reads_after, *[reads_after[-1]] * 20]
    transport.status_effects = [lambda: None] + [partial(report, identity) for identity in reads]
    return send(control), transport


X_A: Identity = (MOVIE_URL, SESSION)
Y_A: Identity = (OTHER_CONTENT, SESSION)
Y_B: Identity = (OTHER_CONTENT, OTHER_SESSION)


@ALL_COMMANDS
def test_content_replaced_then_restored_during_confirmation_never_confirms(command: str) -> None:
    """X -> Y -> X in one session: a queue can move to another item and back, so once another
    usable content id was reported, a later read of X cannot show what the command caused."""
    result, transport = identity_sequence_run(command, [Y_A, X_A])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "loaded content changed" in (result.detail or "")
    assert transport.attempted() == [command]
    assert transport.status_reads() > 2


@ALL_COMMANDS
def test_content_replaced_for_good_during_confirmation_never_confirms(command: str) -> None:
    result, transport = identity_sequence_run(command, [Y_A, Y_A])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "loaded content changed" in (result.detail or "")
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_content_and_session_replaced_then_restored_never_confirm(command: str) -> None:
    result, transport = identity_sequence_run(command, [Y_B, X_A])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_a_stable_identity_confirms(command: str) -> None:
    result, transport = identity_sequence_run(command, [X_A])

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command]


@ALL_COMMANDS
@pytest.mark.parametrize("unreported", [None, "", "   "], ids=["absent", "empty", "blank"])
def test_a_content_id_briefly_unreported_is_not_a_contradiction(
    command: str, unreported: str | None
) -> None:
    """X -> nothing usable -> X: a read without a usable content id is rejected on its own but
    names no other media, so a later read of X may still confirm."""
    result, transport = identity_sequence_run(command, [(unreported, SESSION), X_A])

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command]


def test_seek_to_the_exact_target_after_a_content_round_trip_is_not_confirmed() -> None:
    result, transport = identity_sequence_run("seek", [Y_A, X_A])

    assert transport.tv.position == 30.0
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "loaded content changed" in (result.detail or "")
    assert transport.attempted() == ["seek"]


@ALL_COMMANDS
def test_a_content_contradiction_does_not_carry_over_to_the_next_command(command: str) -> None:
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    control = make_service(transport, clock)

    first, _ = identity_sequence_run(command, [Y_A, X_A], (control, transport))
    second, _ = identity_sequence_run(command, [X_A], (control, transport))

    assert first.confirmation is Confirmation.UNCONFIRMED
    assert second.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command, command]


@ALL_COMMANDS
def test_a_restored_identity_that_only_arrives_after_the_deadline_does_not_confirm(
    command: str,
) -> None:
    clock = FakeClock()
    initial_state, send = PLAYBACK_COMMANDS[command]
    transport = FakeTransport(clock=clock)
    transport.tv.playback = initial_state
    transport.tv.content_id, transport.tv.media_session_id = X_A
    # Every read inside the 1 s window reports another item; X only after the window.
    transport.status_effects = [lambda: None] + [
        partial(setattr, transport.tv, "content_id", OTHER_CONTENT)
    ] * 200

    result = send(make_service(transport, clock, confirm_timeout=1.0))

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert clock.now <= 1.0 + 1e-9
    assert transport.attempted() == [command]


# --- Queue item identity (currentItemId) ---------------------------------------------------
#
# Two items of one queue can share the content id and the session and differ only by their
# currentItemId. When the item was reported before the command it narrows the identity like
# the session: an explicitly different item is terminal for the attempt, a missing one only
# rejects that read, and it never identifies the media on its own.

ITEM = 101
OTHER_ITEM = 102
Full = tuple[str | None, int | None, int | None]
X_A_101: Full = (MOVIE_URL, SESSION, ITEM)


def item_sequence_run(
    command: str,
    reads_after: list[Full],
    *,
    before: Full = X_A_101,
    service_transport: tuple[ControlService, FakeTransport] | None = None,
) -> tuple[CommandResult, FakeTransport]:
    """One command with the pre-command read reporting `before` (content, session, item), then
    `reads_after` on the following reads (the last one repeats). The TV acts on the command at
    once, so every read after it already shows the expected state or position."""
    if service_transport is None:
        clock = FakeClock()
        transport = FakeTransport(clock=clock)
        control = make_service(transport, clock)
    else:
        control, transport = service_transport
    initial_state, send = PLAYBACK_COMMANDS[command]
    transport.tv.playback = initial_state

    def report(identity: Full) -> None:
        tv = transport.tv
        tv.content_id, tv.media_session_id, tv.current_item_id = identity

    report(before)
    reads = [*reads_after, *[reads_after[-1]] * 20]
    transport.status_effects = [lambda: None] + [partial(report, identity) for identity in reads]
    return send(control), transport


@ALL_COMMANDS
def test_a_stable_queue_item_confirms(command: str) -> None:
    result, transport = item_sequence_run(command, [X_A_101])

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_another_queue_item_does_not_confirm(command: str) -> None:
    result, transport = item_sequence_run(command, [(MOVIE_URL, SESSION, OTHER_ITEM)])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media queue item changed to 102 from 101" in (result.detail or "")
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_a_queue_item_replaced_then_restored_never_confirms(command: str) -> None:
    """Item 101 -> 102 -> 101 with the same content id and session."""
    result, transport = item_sequence_run(command, [(MOVIE_URL, SESSION, OTHER_ITEM), X_A_101])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media queue item changed" in (result.detail or "")
    assert transport.attempted() == [command]
    assert transport.status_reads() > 2


def test_seek_to_the_exact_target_after_a_queue_item_round_trip_is_not_confirmed() -> None:
    result, transport = item_sequence_run("seek", [(MOVIE_URL, SESSION, OTHER_ITEM), X_A_101])

    assert transport.tv.position == 30.0
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == ["seek"]


@ALL_COMMANDS
def test_a_queue_item_briefly_unreported_is_not_a_contradiction(command: str) -> None:
    result, transport = item_sequence_run(command, [(MOVIE_URL, SESSION, None), X_A_101])

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command]


@ALL_COMMANDS
def test_a_queue_item_missing_after_the_command_does_not_confirm(command: str) -> None:
    result, transport = item_sequence_run(command, [(MOVIE_URL, SESSION, None)])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media queue item was not reported after the command" in (result.detail or "")
    assert transport.attempted() == [command]


@ALL_COMMANDS
@pytest.mark.parametrize("after_item", [ITEM, OTHER_ITEM, None])
def test_a_queue_item_unknown_before_the_command_adds_no_requirement(
    command: str, after_item: int | None
) -> None:
    """An item that only appears after the command cannot be compared with anything: the
    content id and the session decide, exactly as before items were read."""
    result, _ = item_sequence_run(
        command, [(MOVIE_URL, SESSION, after_item)], before=(MOVIE_URL, SESSION, None)
    )

    assert result.confirmation is Confirmation.CONFIRMED


@ALL_COMMANDS
@pytest.mark.parametrize(
    "contradiction",
    [(MOVIE_URL, OTHER_SESSION, ITEM), (OTHER_CONTENT, SESSION, ITEM)],
    ids=["session", "content"],
)
def test_other_contradictions_with_the_same_item_still_never_confirm(
    command: str, contradiction: Full
) -> None:
    result, transport = item_sequence_run(command, [contradiction, X_A_101])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == [command]


def test_the_first_of_several_contradictions_is_the_one_reported() -> None:
    result, transport = item_sequence_run(
        "pause",
        [
            (OTHER_CONTENT, SESSION, ITEM),
            (MOVIE_URL, OTHER_SESSION, ITEM),
            (MOVIE_URL, SESSION, OTHER_ITEM),
            X_A_101,
        ],
    )

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert f"loaded content changed to {OTHER_CONTENT!r} from {MOVIE_URL!r}" in (
        result.detail or ""
    )
    assert "media session changed" not in (result.detail or "")
    assert "media queue item changed" not in (result.detail or "")
    assert transport.attempted() == ["pause"]


@ALL_COMMANDS
@pytest.mark.parametrize("content_id", [None, "", "   "], ids=["absent", "empty", "blank"])
def test_a_queue_item_never_identifies_the_media_without_a_content_id(
    command: str, content_id: str | None
) -> None:
    result, _ = item_sequence_run(
        command, [(content_id, SESSION, ITEM)], before=(content_id, SESSION, ITEM)
    )

    assert result.confirmation is Confirmation.UNCONFIRMED


@ALL_COMMANDS
def test_a_queue_item_contradiction_does_not_carry_over_to_the_next_command(command: str) -> None:
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    control = make_service(transport, clock)

    first, _ = item_sequence_run(
        command, [(MOVIE_URL, SESSION, OTHER_ITEM), X_A_101], service_transport=(control, transport)
    )
    second, _ = item_sequence_run(command, [X_A_101], service_transport=(control, transport))

    assert first.confirmation is Confirmation.UNCONFIRMED
    assert second.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == [command, command]


@ALL_COMMANDS
def test_the_original_item_only_after_the_deadline_does_not_confirm(command: str) -> None:
    clock = FakeClock()
    initial_state, send = PLAYBACK_COMMANDS[command]
    transport = FakeTransport(clock=clock)
    transport.tv.playback = initial_state
    tv = transport.tv
    tv.content_id, tv.media_session_id, tv.current_item_id = X_A_101
    transport.status_effects = [lambda: None] + [
        partial(setattr, transport.tv, "current_item_id", None)
    ] * 200

    result = send(make_service(transport, clock, confirm_timeout=1.0))

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert clock.now <= 1.0 + 1e-9
    assert transport.attempted() == [command]


@pytest.mark.parametrize(
    "item", [True, False, -1, 1.5, "101"], ids=["true", "false", "negative", "float", "string"]
)
def test_a_queue_item_id_must_be_a_non_negative_integer(item: object) -> None:
    with pytest.raises(InvalidArgumentError):
        MediaStatus(content_id=MOVIE_URL, current_item_id=item)  # type: ignore[arg-type]


@pytest.mark.parametrize("item", [None, 0, 101])
def test_a_queue_item_id_can_be_unknown_zero_or_positive(item: int | None) -> None:
    assert MediaStatus(current_item_id=item).current_item_id == item


# --- Seek hardware finding (2026-09-29, YouTube receiver) -----------------------------------
#
# On real hardware a Seek +30 s was sent once; the receiver then reported target + elapsed
# time while the picture did not move, later replaced its session (19 -> 20) and realigned to
# the no-seek timeline. CONFIRMED means "the receiver reported the expected state for the
# same identified media", never an independent proof of the physical effect. These tests pin
# that meaning without changing it.


def test_seek_is_confirmed_from_the_receiver_report_alone() -> None:
    """With a usable identity, a receiver reporting the target confirms the seek: the
    confirmation rests on what the receiver reports, not on what the screen shows."""
    result, transport = item_sequence_run("seek", [X_A_101])

    assert result.confirmation is Confirmation.CONFIRMED
    assert transport.attempted() == ["seek"]


def test_seek_reported_in_a_replaced_session_is_not_confirmed() -> None:
    """The receiver reports the target position, but from another session (19 -> 20)."""
    result, transport = item_sequence_run("seek", [(MOVIE_URL, OTHER_SESSION, ITEM)])

    assert transport.tv.position == 30.0
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media session changed" in (result.detail or "")
    assert transport.attempted() == ["seek"]


def test_seek_target_then_replaced_session_then_original_never_confirms() -> None:
    result, transport = item_sequence_run(
        "seek", [(MOVIE_URL, OTHER_SESSION, ITEM), X_A_101, X_A_101]
    )

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == ["seek"]


def test_seek_with_an_empty_content_id_stays_unconfirmed_like_the_real_youtube_case() -> None:
    """As observed: empty content id, stable session and item, target reported -> UNCONFIRMED."""
    empty = ("", SESSION, ITEM)

    result, transport = item_sequence_run("seek", [empty], before=empty)

    assert transport.tv.position == 30.0
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "media identity was not reported before the command" in (result.detail or "")
    assert transport.attempted() == ["seek"]


# --- Stop confirmed by the end of the media session ----------------------------------------


def stop_run(
    reads_after: list[Callable[[FakeTransport], None]],
    *,
    before: Full = X_A_101,
    confirm_timeout: float = 1.0,
) -> tuple[CommandResult, FakeTransport, FakeClock]:
    """One Stop; the pre-command read reports `before`, then each later read first applies the
    next change (the last one repeats). The TV does not act by itself: the changes say what it
    reports."""
    clock = FakeClock()
    transport = FakeTransport(clock=clock, ignore_commands=True)
    tv = transport.tv
    tv.playback = PlaybackState.PLAYING
    tv.content_id, tv.media_session_id, tv.current_item_id = before
    reads = [*reads_after, *[reads_after[-1]] * 400]
    transport.status_effects = [lambda: None] + [partial(change, transport) for change in reads]
    result = make_service(transport, clock, confirm_timeout=confirm_timeout).stop(DEVICE_ID)
    return result, transport, clock


def session_ends(transport: FakeTransport) -> None:
    transport.tv.has_media = False


def reports(identity: Full) -> Callable[[FakeTransport], None]:
    def change(transport: FakeTransport) -> None:
        tv = transport.tv
        tv.has_media = True
        tv.content_id, tv.media_session_id, tv.current_item_id = identity

    return change


def test_stop_is_confirmed_when_the_identified_session_ends() -> None:
    result, transport, _ = stop_run([session_ends])

    assert result.confirmation is Confirmation.CONFIRMED
    assert result.observed is not None
    assert result.observed.media is None
    assert transport.attempted() == ["stop"]


def test_stop_is_confirmed_by_idle_on_the_same_media_as_before() -> None:
    def idle(transport: FakeTransport) -> None:
        transport.tv.playback = PlaybackState.IDLE

    result, _transport, _ = stop_run([idle])

    assert result.confirmation is Confirmation.CONFIRMED
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.playback_state is PlaybackState.IDLE


@pytest.mark.parametrize("content_id", [None, "", "   "], ids=["absent", "empty", "blank"])
def test_a_session_end_confirms_nothing_without_a_media_identity_before(
    content_id: str | None,
) -> None:
    result, transport, _ = stop_run([session_ends], before=(content_id, SESSION, ITEM))

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == ["stop"]


@pytest.mark.parametrize(
    "replacement",
    [
        (MOVIE_URL, OTHER_SESSION, ITEM),
        (OTHER_CONTENT, SESSION, ITEM),
        (MOVIE_URL, SESSION, OTHER_ITEM),
    ],
    ids=["session", "content", "queue-item"],
)
def test_a_replacement_then_a_session_end_never_confirms(replacement: Full) -> None:
    result, transport, _ = stop_run([reports(replacement), session_ends])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert "changed" in (result.detail or "")
    assert result.observed is not None
    assert result.observed.media is None
    assert transport.attempted() == ["stop"]


def test_a_session_end_after_a_missing_identity_read_still_confirms() -> None:
    """A read without a session id is no contradiction, so the later end still confirms."""
    result, _, _ = stop_run([reports((MOVIE_URL, None, ITEM)), session_ends])

    assert result.confirmation is Confirmation.CONFIRMED


def test_a_session_that_ends_only_after_the_deadline_does_not_confirm() -> None:
    playing = reports(X_A_101)
    result, transport, clock = stop_run([playing] * 50 + [session_ends], confirm_timeout=1.0)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert clock.now <= 1.0 + 1e-9
    assert transport.attempted() == ["stop"]


def test_a_disconnected_receiver_after_stop_does_not_confirm() -> None:
    def disconnected(transport: FakeTransport) -> None:
        transport.tv.connection = ConnectionState.DISCONNECTED
        transport.tv.has_media = False

    result, transport, _ = stop_run([disconnected])

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert transport.attempted() == ["stop"]


def test_play_and_pause_are_still_never_confirmed_by_a_session_end() -> None:
    for command in ("play", "pause"):
        clock = FakeClock()
        initial_state, send = PLAYBACK_COMMANDS[command]
        transport = FakeTransport(clock=clock, ignore_commands=True)
        transport.tv.playback = initial_state
        tv = transport.tv
        tv.content_id, tv.media_session_id, tv.current_item_id = X_A_101
        transport.status_effects = [lambda: None] + [partial(session_ends, transport)] * 50

        result = send(make_service(transport, clock))

        assert result.confirmation is Confirmation.UNCONFIRMED
        assert transport.attempted() == [command]


# --- Fixed volume ---------------------------------------------------------------------------


@dataclass
class ScriptedReceiverTransport(FakeTransport):
    """`FakeTransport` whose receiver reports a given volume control type."""

    control_type: VolumeControlType | None = None

    def get_status(self, device_id: DeviceId, *, timeout: float) -> DeviceStatus:
        status = super().get_status(device_id, timeout=timeout)
        if status.receiver is None:
            return status
        receiver = dataclasses.replace(status.receiver, volume_control_type=self.control_type)
        return dataclasses.replace(status, receiver=receiver)


def test_a_reported_fixed_volume_is_refused_before_anything_is_sent(clock: FakeClock) -> None:
    transport = ScriptedReceiverTransport(clock=clock, control_type=VolumeControlType.FIXED)

    with pytest.raises(UnsupportedOperationError, match="fixed volume"):
        make_service(transport, clock).set_volume(DEVICE_ID, 0.7)

    assert transport.attempted() == []
    assert transport.sent() == []
    assert transport.status_reads() == 1


@pytest.mark.parametrize(
    "control_type",
    [None, VolumeControlType.ATTENUATION, VolumeControlType.MASTER],
    ids=["not-reported", "attenuation", "master"],
)
def test_a_volume_not_reported_as_fixed_is_sent_once(
    clock: FakeClock, control_type: VolumeControlType | None
) -> None:
    transport = ScriptedReceiverTransport(clock=clock, control_type=control_type)

    result = make_service(transport, clock).set_volume(DEVICE_ID, 0.7)

    assert transport.attempted() == ["set_volume"]
    assert result.confirmation is Confirmation.CONFIRMED


def test_a_failed_pre_command_read_does_not_make_the_volume_fixed(clock: FakeClock) -> None:
    transport = ScriptedReceiverTransport(
        clock=clock,
        control_type=VolumeControlType.FIXED,
        status_errors=[DeviceUnavailableError("blip", device_id=DEVICE_ID)],
    )

    make_service(transport, clock).set_volume(DEVICE_ID, 0.7)

    assert transport.attempted() == ["set_volume"]


def test_the_fixed_volume_check_shares_the_single_confirmation_deadline(clock: FakeClock) -> None:
    transport = ScriptedReceiverTransport(clock=clock, status_read_delays=[0.4])
    transport.ignore_commands = True

    result = make_service(transport, clock, confirm_timeout=1.0).set_volume(DEVICE_ID, 0.7)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert clock.now <= 1.0 + 1e-9
    reads = [args[1] for name, args in transport.calls if name == "get_status"]
    assert reads[0] == pytest.approx(1.0)
    assert reads[1] == pytest.approx(0.6)


def test_mute_is_not_affected_by_a_fixed_volume(clock: FakeClock) -> None:
    transport = ScriptedReceiverTransport(clock=clock, control_type=VolumeControlType.FIXED)

    make_service(transport, clock).set_muted(DEVICE_ID, True)

    assert transport.attempted() == ["set_muted"]
