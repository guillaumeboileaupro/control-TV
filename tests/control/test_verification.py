"""Unit tests for the pure per-command `Observation` builders in `control_tv.service`.

These check every branch directly against hand-built `DeviceStatus` values: what a real
Cast receiver actually reports is free to omit any of these fields, and the service must
describe that honestly rather than treat missing information as the desired state.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from control_tv.domain import (
    ConnectionState,
    DeviceId,
    DeviceStatus,
    MediaStatus,
    PlaybackState,
    ReceiverStatus,
)
from control_tv.service import (
    _loaded,
    _muted_is,
    _playback_in,
    _position_near,
    _volume_near,
)

DEVICE_ID = DeviceId("uuid-1")
OBSERVED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
URL = "http://media.local/movie.mp4"


def status(
    *, media: MediaStatus | None = None, receiver: ReceiverStatus | None = None
) -> DeviceStatus:
    return DeviceStatus(
        device_id=DEVICE_ID,
        connection=ConnectionState.CONNECTED,
        observed_at=OBSERVED_AT,
        media=media,
        receiver=receiver,
    )


# --- _playback_in -----------------------------------------------------------------------


def test_playback_in_reports_no_media_session_honestly_never_as_idle() -> None:
    observation = _playback_in(PlaybackState.IDLE)(status())

    assert observation.matched is False
    assert observation.description == "no active media session"


def test_playback_in_matches_one_of_several_accepted_states() -> None:
    for state in (PlaybackState.PLAYING, PlaybackState.BUFFERING):
        observation = _playback_in(PlaybackState.PLAYING, PlaybackState.BUFFERING)(
            status(media=MediaStatus(playback_state=state))
        )
        assert observation.matched is True
        assert observation.description == f"playback is {state.value}"


def test_playback_in_reports_the_contradicting_state() -> None:
    observation = _playback_in(PlaybackState.PAUSED)(
        status(media=MediaStatus(playback_state=PlaybackState.PLAYING))
    )

    assert observation.matched is False
    assert observation.description == "playback is playing"


# --- _loaded -----------------------------------------------------------------------------


def test_loaded_reports_no_media_session() -> None:
    observation = _loaded(URL)(status())

    assert observation.matched is False
    assert observation.description == "no active media session"


def test_loaded_reports_the_wrong_content_distinctly_from_the_wrong_state() -> None:
    other = MediaStatus(content_id="http://other/clip.mp4", playback_state=PlaybackState.PLAYING)
    observation = _loaded(URL)(status(media=other))

    assert observation.matched is False
    assert observation.description == f"loaded content is 'http://other/clip.mp4', not {URL!r}"


@pytest.mark.parametrize("state", [PlaybackState.IDLE, PlaybackState.UNKNOWN])
def test_loaded_reports_right_content_but_not_yet_playable_state(state: PlaybackState) -> None:
    observation = _loaded(URL)(status(media=MediaStatus(content_id=URL, playback_state=state)))

    assert observation.matched is False
    assert observation.description == f"content is loaded but playback is {state.value}"


def test_loaded_matches_right_content_in_a_loaded_state() -> None:
    observation = _loaded(URL)(
        status(media=MediaStatus(content_id=URL, playback_state=PlaybackState.PAUSED))
    )

    assert observation.matched is True


# --- _position_near ------------------------------------------------------------------------


def test_position_near_reports_no_media_session() -> None:
    observation = _position_near(10.0, 1.0, URL)(status())

    assert observation.matched is False
    assert observation.description == "no active media session"


def test_position_near_requires_a_previously_observed_media_identity() -> None:
    observation = _position_near(10.0, 1.0, None)(
        status(media=MediaStatus(content_id=URL, position_seconds=10.0))
    )

    assert observation.matched is False
    assert observation.description == "media identity was not reported before the command"


def test_position_near_rejects_a_matching_position_on_replaced_media() -> None:
    replacement = "http://media.local/replacement.mp4"
    observation = _position_near(10.0, 1.0, URL)(
        status(media=MediaStatus(content_id=replacement, position_seconds=10.0))
    )

    assert observation.matched is False
    assert observation.description == f"loaded content changed to {replacement!r} from {URL!r}"


def test_position_near_reports_when_the_device_does_not_report_a_position() -> None:
    observation = _position_near(10.0, 1.0, URL)(status(media=MediaStatus(content_id=URL)))

    assert observation.matched is False
    assert observation.description == "position was not reported"


@pytest.mark.parametrize(("position", "matched"), [(9.0, True), (11.0, True), (7.0, False)])
def test_position_near_applies_the_tolerance(position: float, matched: bool) -> None:
    observation = _position_near(10.0, 1.0, URL)(
        status(media=MediaStatus(content_id=URL, position_seconds=position))
    )

    assert observation.matched is matched
    assert observation.description == f"position is {position:g}s"


# --- _volume_near --------------------------------------------------------------------------


def test_volume_near_reports_no_receiver_status() -> None:
    assert _volume_near(0.5, 0.01)(status()).description == "no receiver status"


def test_volume_near_reports_when_the_device_does_not_report_a_level() -> None:
    observation = _volume_near(0.5, 0.01)(status(receiver=ReceiverStatus()))

    assert observation.matched is False
    assert observation.description == "volume level was not reported"


@pytest.mark.parametrize(("level", "matched"), [(0.5, True), (0.505, True), (0.6, False)])
def test_volume_near_applies_the_tolerance(level: float, matched: bool) -> None:
    observation = _volume_near(0.5, 0.01)(status(receiver=ReceiverStatus(volume_level=level)))

    assert observation.matched is matched
    assert observation.description == f"volume is {level:g}"


# --- _muted_is -----------------------------------------------------------------------------


def test_muted_is_reports_no_receiver_status() -> None:
    assert _muted_is(True)(status()).description == "no receiver status"


def test_muted_is_reports_when_the_device_does_not_report_mute_state() -> None:
    observation = _muted_is(True)(status(receiver=ReceiverStatus()))

    assert observation.matched is False
    assert observation.description == "mute state was not reported"


@pytest.mark.parametrize(
    ("actual", "expected", "matched"),
    [(True, True, True), (False, False, True), (True, False, False), (False, True, False)],
)
def test_muted_is_matches_the_exact_mute_state(actual: bool, expected: bool, matched: bool) -> None:
    observation = _muted_is(expected)(status(receiver=ReceiverStatus(muted=actual)))

    assert observation.matched is matched
    assert observation.description == f"mute is {'on' if actual else 'off'}"
