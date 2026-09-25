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


@pytest.mark.parametrize(
    ("url", "content_type"),
    [
        ("http://media.local/movie.mp4", "video/mp4"),
        ("https://media.local:8443/live.m3u8?token=abc", "application/vnd.apple.mpegurl"),
        ("https://media.local/audio", 'audio/aac; codecs="mp4a.40.2"'),
    ],
)
def test_media_request_accepts_castable_network_targets(url: str, content_type: str) -> None:
    request = MediaRequest(url=url, content_type=content_type, title="Living room media")

    assert request.url == url
    assert request.content_type == content_type


@pytest.mark.parametrize(
    "url",
    [
        "",
        " ",
        "movie.mp4",
        "/movie.mp4",
        "ftp://media.local/movie.mp4",
        "https:///movie.mp4",
        "https://media.local/movie file.mp4",
        " https://media.local/movie.mp4",
        "https://media.local:99999/movie.mp4",
        "https://[broken/movie.mp4",
    ],
)
def test_media_request_rejects_non_http_or_malformed_urls(url: str) -> None:
    with pytest.raises(InvalidArgumentError, match="absolute HTTP"):
        MediaRequest(url=url, content_type="video/mp4")


@pytest.mark.parametrize(
    "content_type",
    [
        "",
        " ",
        "video",
        "/mp4",
        "video/",
        "video /mp4",
        "video/mp4\ninvalid",
        "video/mp4; charset",
        "video/mp4; =utf-8",
        "video/mp4; charset=",
    ],
)
def test_media_request_rejects_invalid_content_types(content_type: str) -> None:
    with pytest.raises(InvalidArgumentError, match="valid MIME"):
        MediaRequest(url="https://media.local/movie.mp4", content_type=content_type)


@pytest.mark.parametrize("title", ["", "  ", "Movie\nInjected", "Movie\0Injected"])
def test_media_request_rejects_blank_or_controlled_titles(title: str) -> None:
    with pytest.raises(InvalidArgumentError, match="media title"):
        MediaRequest(url="https://media.local/movie.mp4", content_type="video/mp4", title=title)


@pytest.mark.parametrize(
    "content_type",
    [
        pytest.param("video/mp4", id="simple"),
        pytest.param('video/mp4; codecs="avc1.4d401f"', id="parameters"),
        pytest.param('video/mp4 ; codecs="avc1.4d401f"', id="optional-space-before-semicolon"),
    ],
)
def test_media_request_accepts_valid_mime_parameter_spacing(content_type: str) -> None:
    request = MediaRequest(url="https://media.local/movie.mp4", content_type=content_type)

    assert request.content_type == content_type


def test_media_request_still_rejects_a_truly_invalid_mime_value() -> None:
    with pytest.raises(InvalidArgumentError, match="valid MIME"):
        MediaRequest(url="https://media.local/movie.mp4", content_type="video / mp4 ; codecs")
