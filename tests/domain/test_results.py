from __future__ import annotations

from datetime import UTC, datetime

import pytest

from control_tv.domain import (
    Command,
    CommandResult,
    Confirmation,
    ConnectionState,
    DeviceId,
    DeviceStatus,
    InvalidArgumentError,
    MediaStatus,
    PlaybackState,
    ReceiverStatus,
)

DEVICE = DeviceId("uuid-1")
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def observed(
    *,
    device: DeviceId = DEVICE,
    connection: ConnectionState = ConnectionState.CONNECTED,
    state: PlaybackState = PlaybackState.PAUSED,
) -> DeviceStatus:
    connected = connection is ConnectionState.CONNECTED
    return DeviceStatus(
        device_id=device,
        connection=connection,
        observed_at=NOW,
        receiver=ReceiverStatus() if connected else None,
        media=MediaStatus(playback_state=state) if connected else None,
    )


def test_sent_command_without_verification_is_not_confirmed() -> None:
    result = CommandResult(
        command=Command.PAUSE, device_id=DEVICE, confirmation=Confirmation.NOT_CHECKED
    )

    assert result.observed is None
    assert result.confirmed is False


def test_confirmed_result_carries_the_status_observed_on_the_tv() -> None:
    result = CommandResult(
        command=Command.PAUSE,
        device_id=DEVICE,
        confirmation=Confirmation.CONFIRMED,
        observed=observed(),
    )

    assert result.confirmed is True
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.playback_state is PlaybackState.PAUSED


def test_unconfirmed_result_keeps_what_the_tv_actually_showed() -> None:
    result = CommandResult(
        command=Command.PAUSE,
        device_id=DEVICE,
        confirmation=Confirmation.UNCONFIRMED,
        observed=observed(state=PlaybackState.PLAYING),
        detail="TV still reports playing after 5s",
    )

    assert result.confirmed is False
    assert result.observed is not None
    assert result.observed.media is not None
    assert result.observed.media.playback_state is PlaybackState.PLAYING


def test_confirmed_without_observed_status_is_rejected() -> None:
    with pytest.raises(InvalidArgumentError):
        CommandResult(command=Command.PLAY, device_id=DEVICE, confirmation=Confirmation.CONFIRMED)


@pytest.mark.parametrize("connection", [ConnectionState.DISCONNECTED, ConnectionState.CONNECTING])
def test_confirmed_on_an_unconnected_device_is_rejected(connection: ConnectionState) -> None:
    with pytest.raises(InvalidArgumentError):
        CommandResult(
            command=Command.PLAY,
            device_id=DEVICE,
            confirmation=Confirmation.CONFIRMED,
            observed=observed(connection=connection),
        )


@pytest.mark.parametrize("confirmation", list(Confirmation))
def test_status_from_another_device_is_always_rejected(confirmation: Confirmation) -> None:
    with pytest.raises(InvalidArgumentError):
        CommandResult(
            command=Command.STOP,
            device_id=DEVICE,
            confirmation=confirmation,
            observed=observed(device=DeviceId("uuid-2")),
        )


def test_rejection_names_the_device() -> None:
    with pytest.raises(InvalidArgumentError) as excinfo:
        CommandResult(command=Command.PLAY, device_id=DEVICE, confirmation=Confirmation.CONFIRMED)

    assert excinfo.value.device_id == DEVICE
