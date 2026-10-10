"""Two independent controllers on one TV: the state convergence contract (issue #37).

The desktop window and the Android app each own a `ControlService` and a transport, in
separate processes, with no link between them. The contract pinned here, on a simulated TV
shared by two such `ControlService` instances (`tests/shared_tv.py`):

1. no controller keeps a copy of the TV's state: its next `get_status` reads the TV, so each
   one sees the other's effect there, and nothing else;
2. each command is attempted once, by the controller that sent it, and is never resent;
   ambiguous delivery means the TV can have received it zero or one time;
3. a command is confirmed only from what the TV reports, for the same media session: a
   change made by the other controller during the confirmation (a new session, the same
   content reloaded, another state) never confirms it, and every CONFIRMED result rests on a
   status the TV itself returned, showing the requested state.

Known gap, pinned and recorded as a strict expected failure: a media-session switch between a
command's pre-command read and its send is not detected before sending, so the command acts
on the new session (never confirmed, never resent).

No real TV, network or timer is involved, except the concurrency tests, which use real
threads and a real one-second confirmation window.
"""

from __future__ import annotations

import random
import threading
from collections.abc import Callable
from functools import partial

import pytest

from control_tv.domain import (
    CommandResult,
    Confirmation,
    DeviceNotFoundError,
    DeviceStatus,
    DeviceUnavailableError,
    MediaRequest,
    OperationTimeoutError,
    PlaybackState,
)
from control_tv.service import ControlService
from fakes import DEVICE_ID, MOVIE_URL, FakeClock
from shared_tv import SharedTv, TvLink

OTHER_URL = "http://media.local/documentary.mp4"


def controller(link: TvLink, clock: FakeClock | None = None) -> ControlService:
    """A controller as the app builds one, discovering the TV itself; fake time by default."""
    clock = clock or FakeClock()
    service = ControlService(link, clock=clock.monotonic, sleep=clock.sleep)
    service.discover_devices(timeout=1.0)
    return service


def two_controllers(tv: SharedTv) -> tuple[ControlService, TvLink, ControlService, TvLink]:
    desktop_link, android_link = tv.link("desktop"), tv.link("android")
    return controller(desktop_link), desktop_link, controller(android_link), android_link


def same_view(a: DeviceStatus, b: DeviceStatus) -> bool:
    return (a.receiver, a.media, a.connection) == (b.receiver, b.media, b.connection)


def playback_is(state: PlaybackState) -> Callable[[DeviceStatus], bool]:
    return lambda status: status.media is not None and status.media.playback_state is state


def volume_is(level: float) -> Callable[[DeviceStatus], bool]:
    return lambda status: status.receiver is not None and status.receiver.volume_level == level


def muted_is(muted: bool) -> Callable[[DeviceStatus], bool]:
    return lambda status: status.receiver is not None and status.receiver.muted is muted


def assert_confirmation_backed_by_the_tv(
    result: CommandResult, link: TvLink, requested: Callable[[DeviceStatus], bool]
) -> None:
    """A CONFIRMED result must rest on a status the TV itself returned to this controller.

    Not the controller's own claim: the status it cites is one of the very objects the
    simulated TV handed to this link, and that status shows the requested state.
    """
    if result.confirmation is not Confirmation.CONFIRMED:
        return
    assert result.observed is not None, "confirmed without an observed status"
    assert any(read is result.observed for read in link.observed_reads), (
        "confirmed from a status the TV never returned to this controller"
    )
    assert requested(result.observed), "confirmed from a status without the requested state"


# --- 1. Each controller reads the TV, never a copy -----------------------------------------


def test_each_controller_must_discover_the_tv_itself() -> None:
    tv = SharedTv()
    desktop = controller(tv.link("desktop"))
    android = ControlService(tv.link("android"))

    assert desktop.get_status(DEVICE_ID).media is not None
    with pytest.raises(DeviceNotFoundError):
        android.get_status(DEVICE_ID)
    with pytest.raises(DeviceNotFoundError):
        android.pause(DEVICE_ID)
    assert tv.received() == []


def test_one_controllers_command_is_seen_by_the_other_at_its_next_read() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, android_link = two_controllers(tv)

    paused = desktop.pause(DEVICE_ID)
    assert paused.confirmation is Confirmation.CONFIRMED
    assert_confirmation_backed_by_the_tv(paused, desktop_link, playback_is(PlaybackState.PAUSED))
    assert android.get_status(DEVICE_ID).media.playback_state is PlaybackState.PAUSED  # type: ignore[union-attr]

    played = android.play(DEVICE_ID)
    assert played.confirmation is Confirmation.CONFIRMED
    assert_confirmation_backed_by_the_tv(played, android_link, playback_is(PlaybackState.PLAYING))
    assert desktop.get_status(DEVICE_ID).media.playback_state is PlaybackState.PLAYING  # type: ignore[union-attr]

    lowered = desktop.set_volume(DEVICE_ID, 0.3)
    assert lowered.confirmation is Confirmation.CONFIRMED
    assert_confirmation_backed_by_the_tv(lowered, desktop_link, volume_is(0.3))
    assert android.get_status(DEVICE_ID).receiver.volume_level == 0.3  # type: ignore[union-attr]

    muted = android.set_muted(DEVICE_ID, True)
    assert muted.confirmation is Confirmation.CONFIRMED
    assert_confirmation_backed_by_the_tv(muted, android_link, muted_is(True))
    assert desktop.get_status(DEVICE_ID).receiver.muted is True  # type: ignore[union-attr]

    assert [(d.controller, d.command) for d in tv.deliveries] == [
        ("desktop", "pause"),
        ("android", "play"),
        ("desktop", "set_volume"),
        ("android", "set_muted"),
    ]


def test_a_controller_keeps_no_copy_of_the_tv_state() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)
    before = desktop.get_status(DEVICE_ID)

    android.load_media(DEVICE_ID, MediaRequest(url=OTHER_URL, content_type="video/mp4"))
    android.set_volume(DEVICE_ID, 0.8)
    after = desktop.get_status(DEVICE_ID)

    assert before.media.content_id == MOVIE_URL  # type: ignore[union-attr]
    assert after.media.content_id == OTHER_URL  # type: ignore[union-attr]
    assert after.receiver.volume_level == 0.8  # type: ignore[union-attr]
    assert desktop_link.status_reads == 2
    assert tv.received("desktop") == []


def test_after_any_sequence_of_commands_both_controllers_read_the_tvs_real_state() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, android_link = two_controllers(tv)
    pick = random.Random(37)

    def play(c: ControlService) -> tuple[CommandResult, Callable[[DeviceStatus], bool]]:
        return c.play(DEVICE_ID), playback_is(PlaybackState.PLAYING)

    def pause(c: ControlService) -> tuple[CommandResult, Callable[[DeviceStatus], bool]]:
        return c.pause(DEVICE_ID), playback_is(PlaybackState.PAUSED)

    def seek(c: ControlService) -> tuple[CommandResult, Callable[[DeviceStatus], bool]]:
        target = float(pick.randrange(0, 600))
        return c.seek(
            DEVICE_ID, target
        ), lambda s: s.media is not None and s.media.position_seconds == target

    def volume(c: ControlService) -> tuple[CommandResult, Callable[[DeviceStatus], bool]]:
        level = pick.randrange(0, 101) / 100
        return c.set_volume(DEVICE_ID, level), volume_is(level)

    def mute(c: ControlService) -> tuple[CommandResult, Callable[[DeviceStatus], bool]]:
        muted = pick.random() < 0.5
        return c.set_muted(DEVICE_ID, muted), muted_is(muted)

    actions = [play, pause, seek, volume, mute]
    sent = confirmed = 0

    for _ in range(60):
        actor, link = pick.choice([(desktop, desktop_link), (android, android_link)])
        result, requested = pick.choice(actions)(actor)
        assert_confirmation_backed_by_the_tv(result, link, requested)
        confirmed += result.confirmation is Confirmation.CONFIRMED
        sent += 1
        truth = tv.snapshot()
        assert same_view(desktop.get_status(DEVICE_ID), truth)
        assert same_view(android.get_status(DEVICE_ID), truth)

    assert len(tv.deliveries) == sent
    assert confirmed == sent  # nothing interferes here: every command is confirmed by the TV


# --- 2. Concurrent commands from both controllers ------------------------------------------


def real_time_controller(link: TvLink) -> ControlService:
    service = ControlService(link, confirm_timeout=1.0, poll_interval=0.005)
    service.discover_devices(timeout=1.0)
    return service


def run_together(*calls: Callable[[], object]) -> list[object]:
    start = threading.Barrier(len(calls))
    results: list[object] = [None] * len(calls)

    def run(index: int, call: Callable[[], object]) -> None:
        start.wait()
        try:
            results[index] = call()
        except Exception as error:  # recorded, asserted by the caller
            results[index] = error

    threads = [threading.Thread(target=run, args=item) for item in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    return results


def force_send_overlap(*links: TvLink) -> list[str]:
    """Block inside every link's send until all concurrent calls reached that boundary."""
    gate = threading.Barrier(len(links))
    arrivals: list[str] = []
    lock = threading.Lock()

    def meet(controller_name: str) -> None:
        with lock:
            arrivals.append(controller_name)
        gate.wait(timeout=5)

    for link in links:
        link.before_send.append(partial(meet, link.controller))
    return arrivals


def test_concurrent_play_and_pause_each_go_once_and_both_then_read_the_last_one() -> None:
    for round_ in range(6):
        tv = SharedTv()
        desktop_link, android_link = tv.link("desktop"), tv.link("android")
        desktop, android = real_time_controller(desktop_link), real_time_controller(android_link)
        arrivals = force_send_overlap(desktop_link, android_link)

        results = run_together(partial(desktop.pause, DEVICE_ID), partial(android.play, DEVICE_ID))

        assert all(isinstance(r, CommandResult) for r in results), (round_, results)
        assert set(arrivals) == {"desktop", "android"}
        assert desktop_link.send_attempts == ["pause"]
        assert android_link.send_attempts == ["play"]
        assert tv.received("desktop") == ["pause"]
        assert tv.received("android") == ["play"]
        last = tv.deliveries[-1].command
        expected = PlaybackState.PAUSED if last == "pause" else PlaybackState.PLAYING
        assert tv.snapshot().media.playback_state is expected  # type: ignore[union-attr]
        # The command delivered last always ends with the TV in its requested state.
        last_result = results[0] if last == "pause" else results[1]
        assert isinstance(last_result, CommandResult)
        assert last_result.confirmation is Confirmation.CONFIRMED
        # Whichever results claim CONFIRMED (the first one may too, if it read its own
        # state before the other landed), each rests on a state the TV reported.
        assert isinstance(results[0], CommandResult) and isinstance(results[1], CommandResult)
        assert_confirmation_backed_by_the_tv(
            results[0], desktop_link, playback_is(PlaybackState.PAUSED)
        )
        assert_confirmation_backed_by_the_tv(
            results[1], android_link, playback_is(PlaybackState.PLAYING)
        )
        for service in (desktop, android):
            assert service.get_status(DEVICE_ID).media.playback_state is expected  # type: ignore[union-attr]


def test_concurrent_volume_changes_converge_on_the_last_delivered_level() -> None:
    for round_ in range(6):
        tv = SharedTv()
        desktop_link, android_link = tv.link("desktop"), tv.link("android")
        desktop, android = real_time_controller(desktop_link), real_time_controller(android_link)
        arrivals = force_send_overlap(desktop_link, android_link)

        results = run_together(
            partial(desktop.set_volume, DEVICE_ID, 0.2),
            partial(android.set_volume, DEVICE_ID, 0.8),
        )

        assert all(isinstance(r, CommandResult) for r in results), (round_, results)
        assert set(arrivals) == {"desktop", "android"}
        assert desktop_link.send_attempts == ["set_volume"]
        assert android_link.send_attempts == ["set_volume"]
        assert len(tv.deliveries) == 2
        assert isinstance(results[0], CommandResult) and isinstance(results[1], CommandResult)
        assert_confirmation_backed_by_the_tv(results[0], desktop_link, volume_is(0.2))
        assert_confirmation_backed_by_the_tv(results[1], android_link, volume_is(0.8))
        final = tv.deliveries[-1].args[0]
        assert tv.snapshot().receiver.volume_level == final  # type: ignore[union-attr]
        for service in (desktop, android):
            assert service.get_status(DEVICE_ID).receiver.volume_level == final  # type: ignore[union-attr]


def test_a_pause_undone_by_the_other_controller_is_unconfirmed_and_never_resent() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)
    # The other remote resumes playback right after the pause lands, before any check.
    desktop_link.before_read[:] = [lambda: None, partial(android.play, DEVICE_ID)]

    result = desktop.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert [(d.controller, d.command) for d in tv.deliveries] == [
        ("desktop", "pause"),
        ("android", "play"),
    ]
    for service in (desktop, android):
        assert service.get_status(DEVICE_ID).media.playback_state is PlaybackState.PLAYING  # type: ignore[union-attr]
    assert tv.received("desktop") == ["pause"]


# --- 3. Ambiguous delivery: attempted once, zero or one reception, never replayed ----------


@pytest.mark.parametrize(
    ("fault", "error", "expected_receptions"),
    [
        ("fail_before_delivery", DeviceUnavailableError, 0),
        ("lose_answer_after_delivery", OperationTimeoutError, 1),
        ("fail_after_delivery", DeviceUnavailableError, 1),
    ],
)
def test_an_ambiguous_command_is_attempted_once_and_may_be_received_zero_or_one_time(
    fault: str, error: type[Exception], expected_receptions: int
) -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)
    setattr(desktop_link, fault, True)

    with pytest.raises(error):
        desktop.pause(DEVICE_ID)
    with pytest.raises(error):
        desktop.set_muted(DEVICE_ID, True)

    assert desktop_link.send_attempts == ["pause", "set_muted"]
    assert len(tv.received("desktop")) == expected_receptions * 2
    for service in (desktop, android, desktop, android):
        status = service.get_status(DEVICE_ID)
        expected_playback = PlaybackState.PAUSED if expected_receptions else PlaybackState.PLAYING
        assert status.media.playback_state is expected_playback  # type: ignore[union-attr]
        assert status.receiver.muted is bool(expected_receptions)  # type: ignore[union-attr]
    # Status reads do not create another attempt or delivery.
    assert desktop_link.send_attempts == ["pause", "set_muted"]
    assert len(tv.received()) == expected_receptions * 2


def test_an_ambiguous_command_does_not_block_or_duplicate_the_other_controllers_commands() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)
    desktop_link.lose_answer_after_delivery = True

    with pytest.raises(OperationTimeoutError):
        desktop.pause(DEVICE_ID)
    assert android.play(DEVICE_ID).confirmation is Confirmation.CONFIRMED

    assert [(d.controller, d.command) for d in tv.deliveries] == [
        ("desktop", "pause"),
        ("android", "play"),
    ]
    assert desktop.get_status(DEVICE_ID).media.playback_state is PlaybackState.PLAYING  # type: ignore[union-attr]


# --- 4. The media session changes while a command is being confirmed -----------------------


def during_confirmation(link: TvLink, action: Callable[[], object]) -> None:
    """Run `action` (the other controller) at this link's first read after its command.

    Playback commands read the TV once before sending (the media identity), so the action
    lands on the second read; it must never be confused with the command's own effect.
    """
    link.before_read[:] = [lambda: None, action]


def other_controller_loads_before_the_send(link: TvLink, android: ControlService) -> None:
    """The other controller starts new media after this one's pre-command read and before
    its send: in the window where the command was already prepared for the old session."""
    link.before_send.append(
        partial(
            android.load_media, DEVICE_ID, MediaRequest(url=OTHER_URL, content_type="video/mp4")
        )
    )


def test_a_session_switch_before_the_send_leaves_the_command_unconfirmed_and_unrepeated() -> None:
    # Current behavior, pinned: the pause prepared for the first session is still sent, once,
    # and acts on whatever session the TV holds at that moment; it is never confirmed, since
    # the media identity read before it no longer matches, and it is never resent.
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)
    other_controller_loads_before_the_send(desktop_link, android)

    result = desktop.pause(DEVICE_ID)

    assert desktop_link.send_attempts == ["pause"]
    assert [(d.controller, d.command) for d in tv.deliveries] == [
        ("android", "load_media"),
        ("desktop", "pause"),
    ]
    media = tv.snapshot().media
    assert media is not None
    assert (media.content_id, media.media_session_id) == (OTHER_URL, 2)
    assert media.playback_state is PlaybackState.PAUSED  # the new session got the pause
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert result.detail is not None and "content changed" in result.detail


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known gap (PR #38 review P3-3): ControlService does not re-check the media session "
        "between its pre-command read and the send, and CastTransport carries no expected "
        "session id: PyChromecast fills in the mediaSessionId it last heard of, which can "
        "already be the new session, so a command prepared for a session that ended reaches "
        "the new one. Fixing it needs the transport to send the pre-read mediaSessionId so "
        "the receiver refuses a stale one (a CastTransport and adapter change, outside this "
        "PR, to be validated on a real receiver)."
    ),
)
def test_a_command_is_not_sent_to_a_session_that_ended_after_the_pre_read() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)
    other_controller_loads_before_the_send(desktop_link, android)

    desktop.pause(DEVICE_ID)

    assert tv.received("desktop") == []
    assert tv.snapshot().media.playback_state is PlaybackState.PLAYING  # type: ignore[union-attr]


def test_a_pause_is_not_confirmed_by_media_the_other_controller_loaded_meanwhile() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)

    def android_loads_and_pauses_other_media() -> None:
        android.load_media(DEVICE_ID, MediaRequest(url=OTHER_URL, content_type="video/mp4"))
        android.pause(DEVICE_ID)

    during_confirmation(desktop_link, android_loads_and_pauses_other_media)
    result = desktop.pause(DEVICE_ID)

    # The TV does report "paused", but for other media: never the desktop's pause.
    assert tv.snapshot().media.playback_state is PlaybackState.PAUSED  # type: ignore[union-attr]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert tv.received("desktop") == ["pause"]
    assert tv.received("android") == ["load_media", "pause"]
    assert android.get_status(DEVICE_ID).media.content_id == OTHER_URL  # type: ignore[union-attr]


def test_a_pause_is_not_confirmed_by_the_same_content_reloaded_in_a_new_session() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)

    def android_reloads_the_same_content_and_pauses() -> None:
        android.load_media(DEVICE_ID, MediaRequest(url=MOVIE_URL, content_type="video/mp4"))
        android.pause(DEVICE_ID)

    during_confirmation(desktop_link, android_reloads_the_same_content_and_pauses)
    result = desktop.pause(DEVICE_ID)

    final = tv.snapshot().media
    assert final.content_id == MOVIE_URL and final.playback_state is PlaybackState.PAUSED  # type: ignore[union-attr]
    assert final.media_session_id == 2  # type: ignore[union-attr]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert tv.received("desktop") == ["pause"]


def test_a_seek_is_not_confirmed_by_a_new_session_at_the_same_position() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)

    def android_loads_and_seeks_to_the_same_position() -> None:
        android.load_media(DEVICE_ID, MediaRequest(url=OTHER_URL, content_type="video/mp4"))
        android.seek(DEVICE_ID, 120.0)

    during_confirmation(desktop_link, android_loads_and_seeks_to_the_same_position)
    result = desktop.seek(DEVICE_ID, 120.0)

    assert tv.snapshot().media.position_seconds == 120.0  # type: ignore[union-attr]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert tv.received("desktop") == ["seek"]


def test_a_stop_is_not_confirmed_once_the_other_controller_started_new_media() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)

    def android_loads_other_media_then_stops_it() -> None:
        android.load_media(DEVICE_ID, MediaRequest(url=OTHER_URL, content_type="video/mp4"))
        android.stop(DEVICE_ID)

    during_confirmation(desktop_link, android_loads_other_media_then_stops_it)
    result = desktop.stop(DEVICE_ID)

    assert tv.snapshot().media.playback_state is PlaybackState.IDLE  # type: ignore[union-attr]
    assert result.confirmation is Confirmation.UNCONFIRMED
    assert tv.received("desktop") == ["stop"]


def test_a_change_that_keeps_the_media_session_does_not_block_a_confirmation() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)

    during_confirmation(desktop_link, partial(android.set_volume, DEVICE_ID, 0.1))
    result = desktop.pause(DEVICE_ID)

    assert result.confirmation is Confirmation.CONFIRMED
    assert_confirmation_backed_by_the_tv(result, desktop_link, playback_is(PlaybackState.PAUSED))
    assert [(d.controller, d.command) for d in tv.deliveries] == [
        ("desktop", "pause"),
        ("android", "set_volume"),
    ]


def test_a_mute_undone_by_the_other_controller_is_not_confirmed_and_not_resent() -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)
    # Mute has no pre-command read: the other remote unmutes before the first check.
    desktop_link.before_read[:] = [lambda: android.set_muted(DEVICE_ID, False)]

    result = desktop.set_muted(DEVICE_ID, True)

    assert result.confirmation is Confirmation.UNCONFIRMED
    assert [(d.controller, d.command) for d in tv.deliveries] == [
        ("desktop", "set_muted"),
        ("android", "set_muted"),
    ]
    assert desktop.get_status(DEVICE_ID).receiver.muted is False  # type: ignore[union-attr]
