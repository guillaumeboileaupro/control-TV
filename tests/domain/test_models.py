from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from control_tv.domain import (
    ConnectionState,
    Device,
    DeviceId,
    DeviceKind,
    DeviceStatus,
    ErrorCode,
    InvalidArgumentError,
    MediaRequest,
    MediaStatus,
    PlaybackState,
    ReceiverStatus,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def make_device(**overrides: object) -> Device:
    fields: dict[str, object] = {
        "id": DeviceId("uuid-1"),
        "friendly_name": "Living room",
        "host": "192.168.1.20",
        "port": 8009,
    }
    fields.update(overrides)
    return Device(**fields)  # type: ignore[arg-type]


def test_device_defaults_to_unknown_kind_and_no_model() -> None:
    device = make_device()

    assert device.kind is DeviceKind.UNKNOWN
    assert device.model_name is None


def test_devices_with_the_same_display_name_stay_distinct_by_id() -> None:
    first = make_device(id=DeviceId("uuid-1"))
    second = make_device(id=DeviceId("uuid-2"))

    assert first.friendly_name == second.friendly_name
    assert first != second


@pytest.mark.parametrize("blank", ["", "   "])
def test_device_rejects_blank_id_and_host(blank: str) -> None:
    with pytest.raises(InvalidArgumentError):
        make_device(id=DeviceId(blank))
    with pytest.raises(InvalidArgumentError):
        make_device(host=blank)


@pytest.mark.parametrize("port", [1, 8009, 65535])
def test_device_accepts_valid_ports(port: int) -> None:
    assert make_device(port=port).port == port


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_device_rejects_out_of_range_ports(port: int) -> None:
    with pytest.raises(InvalidArgumentError) as excinfo:
        make_device(port=port)

    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENT


def test_models_are_immutable() -> None:
    device = make_device()

    with pytest.raises(dataclasses.FrozenInstanceError):
        device.friendly_name = "Other"  # type: ignore[misc]


@pytest.mark.parametrize("level", [0.0, 0.5, 1.0])
def test_receiver_accepts_volume_within_bounds(level: float) -> None:
    assert ReceiverStatus(volume_level=level).volume_level == level


@pytest.mark.parametrize("level", [-0.01, 1.01])
def test_receiver_rejects_volume_out_of_bounds(level: float) -> None:
    with pytest.raises(InvalidArgumentError):
        ReceiverStatus(volume_level=level)


def test_receiver_reports_unknown_as_none_not_false() -> None:
    receiver = ReceiverStatus()

    assert receiver.muted is None
    assert receiver.volume_level is None
    assert receiver.standby is None


def test_media_status_defaults_to_unknown() -> None:
    media = MediaStatus()

    assert media.playback_state is PlaybackState.UNKNOWN
    assert media.supports_seek is None


def test_media_status_rejects_negative_times() -> None:
    with pytest.raises(InvalidArgumentError):
        MediaStatus(position_seconds=-1.0)
    with pytest.raises(InvalidArgumentError):
        MediaStatus(duration_seconds=-1.0)


def test_media_status_accepts_zero_times() -> None:
    media = MediaStatus(position_seconds=0.0, duration_seconds=0.0)

    assert media.position_seconds == 0.0
    assert media.duration_seconds == 0.0


def test_connected_status_may_carry_receiver_and_media() -> None:
    status = DeviceStatus(
        device_id=DeviceId("uuid-1"),
        connection=ConnectionState.CONNECTED,
        observed_at=NOW,
        receiver=ReceiverStatus(volume_level=0.3, muted=False),
        media=MediaStatus(playback_state=PlaybackState.PLAYING, position_seconds=12.5),
    )

    assert status.media is not None
    assert status.media.playback_state is PlaybackState.PLAYING


@pytest.mark.parametrize("connection", [ConnectionState.DISCONNECTED, ConnectionState.CONNECTING])
def test_unconnected_status_cannot_claim_tv_state(connection: ConnectionState) -> None:
    with pytest.raises(InvalidArgumentError):
        DeviceStatus(
            device_id=DeviceId("uuid-1"),
            connection=connection,
            observed_at=NOW,
            receiver=ReceiverStatus(muted=False),
        )
    with pytest.raises(InvalidArgumentError):
        DeviceStatus(
            device_id=DeviceId("uuid-1"),
            connection=connection,
            observed_at=NOW,
            media=MediaStatus(),
        )

    bare = DeviceStatus(device_id=DeviceId("uuid-1"), connection=connection, observed_at=NOW)
    assert bare.receiver is None
    assert bare.media is None


def test_status_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(InvalidArgumentError):
        DeviceStatus(
            device_id=DeviceId("uuid-1"),
            connection=ConnectionState.DISCONNECTED,
            observed_at=datetime(2026, 9, 25, 12, 0),  # naive on purpose
        )


def test_media_request_requires_url_and_content_type() -> None:
    assert MediaRequest(url="http://host/a.mp4", content_type="video/mp4").title is None

    with pytest.raises(InvalidArgumentError):
        MediaRequest(url=" ", content_type="video/mp4")
    with pytest.raises(InvalidArgumentError):
        MediaRequest(url="http://host/a.mp4", content_type="")
