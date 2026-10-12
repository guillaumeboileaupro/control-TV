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
    MetadataType,
    PlaybackState,
    ReceiverStatus,
    StreamType,
    VolumeControlType,
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


@pytest.mark.parametrize(
    "level",
    [-0.01, 1.01, float("nan"), float("inf"), True, False, "0.5"],
    ids=["below", "above", "nan", "infinite", "true", "false", "string"],
)
def test_receiver_rejects_a_volume_that_is_not_a_level(level: object) -> None:
    with pytest.raises(InvalidArgumentError, match="volume level must be a number"):
        ReceiverStatus(volume_level=level)  # type: ignore[arg-type]


@pytest.mark.parametrize("level", [0, 1])
def test_receiver_accepts_the_integer_bounds_as_levels(level: int) -> None:
    assert ReceiverStatus(volume_level=level).volume_level == level


@pytest.mark.parametrize("muted", [None, True, False])
def test_receiver_accepts_a_known_or_unknown_mute_state(muted: bool | None) -> None:
    assert ReceiverStatus(muted=muted).muted is muted


@pytest.mark.parametrize("muted", [0, 1, "false", "true", 0.0, [], {}])
def test_receiver_rejects_a_mute_state_that_is_not_a_boolean(muted: object) -> None:
    with pytest.raises(InvalidArgumentError, match="muted must be a boolean"):
        ReceiverStatus(muted=muted)  # type: ignore[arg-type]


@pytest.mark.parametrize("standby", [None, True, False])
def test_receiver_accepts_a_known_or_unknown_standby(standby: bool | None) -> None:
    assert ReceiverStatus(standby=standby).standby is standby


@pytest.mark.parametrize("standby", [0, 1, "true", "false", 0.0, [], {}])
def test_receiver_rejects_a_standby_that_is_not_a_boolean(standby: object) -> None:
    with pytest.raises(InvalidArgumentError, match="standby must be a boolean"):
        ReceiverStatus(standby=standby)  # type: ignore[arg-type]


def test_receiver_reports_unknown_as_none_not_false() -> None:
    receiver = ReceiverStatus()

    assert receiver.muted is None
    assert receiver.volume_level is None
    assert receiver.standby is None


@pytest.mark.parametrize(
    "control_type",
    [
        None,
        VolumeControlType.FIXED,
        VolumeControlType.ATTENUATION,
        VolumeControlType.MASTER,
    ],
    ids=["unknown", "fixed", "attenuation", "master"],
)
def test_receiver_accepts_a_known_or_unknown_volume_control_type(
    control_type: VolumeControlType | None,
) -> None:
    assert ReceiverStatus(volume_control_type=control_type).volume_control_type is control_type


@pytest.mark.parametrize(
    "control_type",
    ["fixed", True, 1, 1.0, object(), [], {}],
    ids=["string", "bool", "int", "float", "object", "list", "dict"],
)
def test_receiver_rejects_an_invalid_volume_control_type(control_type: object) -> None:
    with pytest.raises(InvalidArgumentError, match="invalid volume control type"):
        ReceiverStatus(volume_control_type=control_type)  # type: ignore[arg-type]


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


# --- Media details --------------------------------------------------------------------------


@pytest.mark.parametrize("artist", ["", "   ", 5, True])
def test_media_artist_is_none_or_a_non_blank_string(artist: object) -> None:
    with pytest.raises(InvalidArgumentError):
        MediaStatus(artist=artist)  # type: ignore[arg-type]


def test_media_details_default_to_unknown() -> None:
    media = MediaStatus()

    assert media.artist is None
    assert media.stream_type is None
    assert media.metadata_type is None
    assert media.supports_pause is None


def test_media_details_keep_reported_values() -> None:
    media = MediaStatus(
        artist="A channel",
        stream_type=StreamType.LIVE,
        metadata_type=MetadataType.MUSIC_TRACK,
        supports_pause=False,
    )

    assert media.artist == "A channel"
    assert media.stream_type is StreamType.LIVE
    assert media.metadata_type is MetadataType.MUSIC_TRACK
    assert media.supports_pause is False


@pytest.mark.parametrize(
    "changes",
    [
        {"supports_pause": 1},
        {"supports_seek": "yes"},
        {"stream_type": "live"},
        {"metadata_type": 0},
    ],
    ids=["pause-int", "seek-string", "stream-raw-string", "metadata-raw-int"],
)
def test_media_details_require_their_own_types(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidArgumentError):
        MediaStatus(**changes)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False, None])
def test_device_android_tv_remote_is_a_boolean_or_unknown(value: bool | None) -> None:
    assert make_device(android_tv_remote=value).android_tv_remote is value


@pytest.mark.parametrize("value", [1, 0, "true", "yes"])
def test_device_rejects_a_non_boolean_android_tv_remote(value: object) -> None:
    with pytest.raises(InvalidArgumentError, match="android_tv_remote"):
        make_device(android_tv_remote=value)
