"""Two independent controllers on one TV: the state convergence contract (issue #37).

The desktop window and the Android app each own a `ControlService` and a transport, in
separate processes, with no link between them. The contract pinned here, on a simulated TV
shared by two such `ControlService` instances (`tests/shared_tv.py`):

1. no controller keeps a copy of the TV's state: its next `get_status` reads the TV, so each
   one sees the other's effect there, and nothing else;
2. each command is delivered once, by the controller that sent it, and is never resent,
   even when its delivery is ambiguous or the other controller changes the TV meanwhile;
3. a command is confirmed only from what the TV reports, for the same media session: a
   change made by the other controller during the confirmation (a new session, the same
   content reloaded, another state) never confirms it.

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
    desktop, _, android, _ = two_controllers(tv)

    assert desktop.pause(DEVICE_ID).confirmation is Confirmation.CONFIRMED
    assert android.get_status(DEVICE_ID).media.playback_state is PlaybackState.PAUSED  # type: ignore[union-attr]

    assert android.play(DEVICE_ID).confirmation is Confirmation.CONFIRMED
    assert desktop.get_status(DEVICE_ID).media.playback_state is PlaybackState.PLAYING  # type: ignore[union-attr]

    assert desktop.set_volume(DEVICE_ID, 0.3).confirmation is Confirmation.CONFIRMED
    assert android.get_status(DEVICE_ID).receiver.volume_level == 0.3  # type: ignore[union-attr]

    assert android.set_muted(DEVICE_ID, True).confirmation is Confirmation.CONFIRMED
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
    desktop, _, android, _ = two_controllers(tv)
    pick = random.Random(37)
    actions: list[Callable[[ControlService], CommandResult]] = [
        lambda c: c.play(DEVICE_ID),
        lambda c: c.pause(DEVICE_ID),
        lambda c: c.seek(DEVICE_ID, float(pick.randrange(0, 600))),
        lambda c: c.set_volume(DEVICE_ID, pick.randrange(0, 101) / 100),
        lambda c: c.set_muted(DEVICE_ID, pick.random() < 0.5),
    ]
    sent = 0

    for _ in range(60):
        actor = pick.choice([desktop, android])
        pick.choice(actions)(actor)
        sent += 1
        truth = tv.snapshot()
        assert same_view(desktop.get_status(DEVICE_ID), truth)
        assert same_view(android.get_status(DEVICE_ID), truth)

    assert len(tv.deliveries) == sent


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


def test_concurrent_play_and_pause_each_go_once_and_both_then_read_the_last_one() -> None:
    for round_ in range(6):
        tv = SharedTv()
        desktop_link, android_link = tv.link("desktop"), tv.link("android")
        desktop, android = real_time_controller(desktop_link), real_time_controller(android_link)

        results = run_together(partial(desktop.pause, DEVICE_ID), partial(android.play, DEVICE_ID))

        assert all(isinstance(r, CommandResult) for r in results), (round_, results)
        assert tv.received("desktop") == ["pause"]
        assert tv.received("android") == ["play"]
        last = tv.deliveries[-1].command
        expected = PlaybackState.PAUSED if last == "pause" else PlaybackState.PLAYING
        assert tv.snapshot().media.playback_state is expected  # type: ignore[union-attr]
        # The command delivered last always ends with the TV in its requested state.
        last_result = results[0] if last == "pause" else results[1]
        assert isinstance(last_result, CommandResult)
        assert last_result.confirmation is Confirmation.CONFIRMED
        for service in (desktop, android):
            assert service.get_status(DEVICE_ID).media.playback_state is expected  # type: ignore[union-attr]


def test_concurrent_volume_changes_converge_on_the_last_delivered_level() -> None:
    for round_ in range(6):
        tv = SharedTv()
        desktop_link, android_link = tv.link("desktop"), tv.link("android")
        desktop, android = real_time_controller(desktop_link), real_time_controller(android_link)

        results = run_together(
            partial(desktop.set_volume, DEVICE_ID, 0.2),
            partial(android.set_volume, DEVICE_ID, 0.8),
        )

        assert all(isinstance(r, CommandResult) for r in results), (round_, results)
        assert len(tv.deliveries) == 2
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


# --- 3. Ambiguous delivery: never replayed ------------------------------------------------


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        ("lose_answer_after_delivery", OperationTimeoutError),
        ("fail_after_delivery", DeviceUnavailableError),
    ],
)
def test_an_ambiguous_command_is_delivered_once_and_both_controllers_then_read_its_effect(
    fault: str, error: type[Exception]
) -> None:
    tv = SharedTv()
    desktop, desktop_link, android, _ = two_controllers(tv)
    setattr(desktop_link, fault, True)

    with pytest.raises(error):
        desktop.pause(DEVICE_ID)
    with pytest.raises(error):
        desktop.set_muted(DEVICE_ID, True)

    assert tv.received("desktop") == ["pause", "set_muted"]
    for service in (desktop, android, desktop, android):
        status = service.get_status(DEVICE_ID)
        assert status.media.playback_state is PlaybackState.PAUSED  # type: ignore[union-attr]
        assert status.receiver.muted is True  # type: ignore[union-attr]
    # Reads never send anything: the ambiguous commands stay delivered exactly once.
    assert tv.received() == ["pause", "set_muted"]


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
