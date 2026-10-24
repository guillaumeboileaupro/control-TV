"""D1 path A end to end, without a device: D0's live encoder (fake process), the HLS publisher
on loopback and the Cast receiver through the real ControlService and the fake transport."""

from __future__ import annotations

import http.client
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from control_tv.domain import DeviceId, DeviceUnavailableError, MediaRequest
from control_tv.domain.results import CommandResult
from control_tv.screen_streaming import (
    EncodingProfile,
    PublicationConfig,
    PublicationKind,
    ReceiverTarget,
    SessionState,
    StreamingSessionService,
    StreamRequest,
    StreamStartError,
    SyntheticVideoSource,
)
from control_tv.screen_streaming.cast_receiver import CastHlsReceiver
from control_tv.screen_streaming.hls_publisher import HlsPublisher
from control_tv.service import ControlService
from fakes import DEVICE_ID, FakeClock, FakeTransport
from live_encoder_fakes import FakeRunner, encoder

REQUEST = StreamRequest(
    SyntheticVideoSource(),
    EncodingProfile(width=1280, height=720, fps=30),
    PublicationConfig(PublicationKind.HLS),
    ReceiverTarget(str(DEVICE_ID)),
)


class InterruptedLoad(ControlService):
    def load_media(self, device_id: DeviceId, request: MediaRequest) -> CommandResult:
        raise KeyboardInterrupt


def session(
    tmp_path: Path, *, control: type[ControlService] = ControlService, **transport_options: object
) -> tuple[StreamingSessionService, HlsPublisher, FakeTransport, FakeRunner]:
    runner = FakeRunner()
    live = encoder(tmp_path, runner)
    publisher = HlsPublisher(live.output, bind_address="127.0.0.1")
    clock = FakeClock()
    transport = FakeTransport(clock=clock, **transport_options)  # type: ignore[arg-type]
    receiver = CastHlsReceiver(control(transport, clock=clock.monotonic, sleep=clock.sleep))
    return StreamingSessionService(live, publisher, receiver), publisher, transport, runner


def published(publisher: HlsPublisher) -> tuple[str, ...]:
    return publisher.unreleased


def state(service: StreamingSessionService) -> SessionState:
    """Reads the state through a call, so each check sees the current value."""
    return service.state


def fetch(uri: str) -> tuple[int, bytes]:
    parts = urlsplit(uri)
    assert parts.hostname is not None and parts.port is not None
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    connection.request("GET", parts.path)
    response = connection.getresponse()
    return response.status, response.read()


def refused(uri: str) -> bool:
    port = urlsplit(uri).port
    assert port is not None
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    except ConnectionRefusedError:
        return True
    return False


def test_the_encoder_publication_and_receiver_start_and_stop_together(tmp_path: Path) -> None:
    service, publisher, transport, runner = session(tmp_path)

    service.start(REQUEST)

    assert state(service) is SessionState.RUNNING
    uri = transport.tv.content_id
    assert uri is not None and published(publisher) == (uri,)
    status, body = fetch(uri)  # what the receiver is told to load is really served
    assert status == 200 and body.startswith(b"#EXTM3U")

    service.stop()

    assert state(service) is SessionState.STOPPED and service.unreleased == ()
    assert refused(uri) and published(publisher) == ()
    assert transport.attempted() == ["load_media", "stop", "close_app"]
    assert runner.group.releases == 1 and list(tmp_path.iterdir()) == []


def test_repeated_sessions_release_everything_each_time(tmp_path: Path) -> None:
    service, publisher, transport, _ = session(tmp_path)
    uris: list[str] = []

    for _ in range(5):
        service.start(REQUEST)
        uri = transport.tv.content_id
        assert uri is not None and fetch(uri)[0] == 200
        uris.append(uri)
        service.stop()
        assert refused(uri) and service.unreleased == () and published(publisher) == ()

    assert len(set(uris)) == 5  # a new port and token every session
    assert list(tmp_path.iterdir()) == []


def test_a_receiver_that_cannot_load_releases_the_publication_and_the_encoder(
    tmp_path: Path,
) -> None:
    service, publisher, _, runner = session(
        tmp_path, fail_commands_with=DeviceUnavailableError("no route")
    )

    with pytest.raises(StreamStartError, match="could not load the stream"):
        service.start(REQUEST)

    assert state(service) is SessionState.FAILED and service.unreleased == ()
    assert published(publisher) == () and runner.group.releases == 1
    assert list(tmp_path.iterdir()) == []


def test_an_interrupted_receiver_start_still_releases_everything(tmp_path: Path) -> None:
    service, publisher, _, runner = session(tmp_path, control=InterruptedLoad)

    with pytest.raises(KeyboardInterrupt):
        service.start(REQUEST)

    assert state(service) is SessionState.FAILED and service.unreleased == ()
    assert published(publisher) == () and runner.group.releases == 1


def test_two_sessions_are_isolated(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, _, first_tv, _ = session(tmp_path / "a")
    second, _, second_tv, _ = session(tmp_path / "b")
    first.start(REQUEST)
    second.start(REQUEST)
    a, b = first_tv.tv.content_id, second_tv.tv.content_id
    assert a is not None and b is not None and a != b

    first.stop()

    assert refused(a) and fetch(b)[0] == 200
    second.stop()
    assert refused(b)
