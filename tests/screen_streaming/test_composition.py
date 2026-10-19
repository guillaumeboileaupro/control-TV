"""D1 path-A composition and supervision with no real socket, process or receiver."""

from __future__ import annotations

import threading
import time
from typing import cast

import pytest

from control_tv.screen_streaming.composition import (
    HlsCompositionConfig,
    HlsStreamingSession,
    assemble_hls_session,
    routed_lan_ipv4,
)
from control_tv.screen_streaming.h264_encoder import LiveEncoderSettings
from control_tv.screen_streaming.model import (
    EncodedStream,
    EncodingProfile,
    InvalidStreamConfiguration,
    PublicationConfig,
    PublicationKind,
    PublishedStream,
    ReceiverSession,
    ReceiverTarget,
    SessionState,
    SyntheticVideoSource,
)
from control_tv.screen_streaming.ports import ScreenEncoder, StreamPublisher, StreamReceiver
from control_tv.screen_streaming.service import StreamingSessionService
from control_tv.screencast.capabilities import CapabilityProbe
from control_tv.screencast.process import ProcessRunner
from control_tv.service import ControlService


class FakeRouteSocket:
    def __init__(self, selected: str, *, failure: OSError | None = None) -> None:
        self.selected = selected
        self.failure = failure
        self.connected: tuple[str, int] | None = None
        self.closed = False

    def connect(self, address: tuple[str, int]) -> None:
        self.connected = address
        if self.failure is not None:
            raise self.failure

    def getsockname(self) -> tuple[str, int]:
        return self.selected, 12345

    def close(self) -> None:
        self.closed = True


def test_the_os_route_selects_one_home_network_ipv4_and_closes_its_socket() -> None:
    route = FakeRouteSocket("192.168.1.20")

    selected = routed_lan_ipv4("192.168.1.30", socket_factory=lambda _family, _kind: route)

    assert selected == "192.168.1.20"
    assert route.connected == ("192.168.1.30", 9) and route.closed


def test_an_unusable_route_is_refused_without_leaking_its_socket() -> None:
    route = FakeRouteSocket("192.168.1.20", failure=OSError("private detail"))

    with pytest.raises(InvalidStreamConfiguration, match=r"no IPv4 route.*OSError"):
        routed_lan_ipv4("192.168.1.30", socket_factory=lambda _family, _kind: route)

    assert route.closed


def test_a_socket_creation_failure_is_a_typed_configuration_error() -> None:
    def unavailable(_family: int, _kind: int) -> FakeRouteSocket:
        raise OSError("private detail")

    with pytest.raises(InvalidStreamConfiguration, match=r"cannot inspect.*OSError"):
        routed_lan_ipv4("192.168.1.30", socket_factory=unavailable)


def test_a_malformed_route_result_is_refused_and_closed() -> None:
    route = FakeRouteSocket("192.168.1.20")
    route.getsockname = lambda: cast(tuple[str, int], ("192.168.1.20",))  # type: ignore[method-assign]

    with pytest.raises(InvalidStreamConfiguration, match="ValueError"):
        routed_lan_ipv4("192.168.1.30", socket_factory=lambda _family, _kind: route)

    assert route.closed


@pytest.mark.parametrize(
    ("receiver", "selected", "message"),
    [
        ("8.8.8.8", "192.168.1.20", "home-network"),
        ("192.168.1.30", "8.8.8.8", "home-network"),
        ("192.168.1.30", "127.0.0.1", "different scopes"),
    ],
)
def test_public_or_cross_scope_routes_are_refused(
    receiver: str, selected: str, message: str
) -> None:
    route = FakeRouteSocket(selected)
    with pytest.raises(InvalidStreamConfiguration, match=message):
        routed_lan_ipv4(receiver, socket_factory=lambda _family, _kind: route)


class Encoder:
    def __init__(self) -> None:
        self.has_ended = False
        self.health_failure = False
        self.stopped = 0

    def start(self, source: object, profile: EncodingProfile) -> EncodedStream:
        return EncodedStream("encoded")

    def stop(self, stream: EncodedStream) -> None:
        self.stopped += 1

    def ended(self, stream: EncodedStream) -> bool:
        if self.health_failure:
            raise RuntimeError("private detail")
        return self.has_ended


class Publisher:
    def __init__(self) -> None:
        self.has_ended = False
        self.stopped = 0
        self.stop_failures = 0

    def start(self, stream: EncodedStream, config: PublicationConfig) -> PublishedStream:
        return PublishedStream("http://127.0.0.1/private/stream.m3u8")

    def stop(self, publication: PublishedStream) -> None:
        self.stopped += 1
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeError("simulated")

    def ended(self, publication: PublishedStream) -> bool:
        return self.has_ended


class Receiver:
    def __init__(self) -> None:
        self.stopped = 0

    def start(
        self, target: ReceiverTarget, publication: PublishedStream, *, timeout: float
    ) -> ReceiverSession:
        return ReceiverSession("receiver")

    def stop(self, session: ReceiverSession) -> None:
        self.stopped += 1


def supervised() -> tuple[HlsStreamingSession, Encoder, Publisher, Receiver]:
    encoder = Encoder()
    publisher = Publisher()
    receiver = Receiver()
    service = StreamingSessionService(
        cast(ScreenEncoder, encoder),
        cast(StreamPublisher, publisher),
        cast(StreamReceiver, receiver),
        encoder_ended=encoder.ended,
        publisher_ended=publisher.ended,
    )
    session = HlsStreamingSession(
        service,
        PublicationConfig(PublicationKind.HLS, 10),
        receiver=ReceiverTarget("tv"),
        bind_address="127.0.0.1",
        health_interval_seconds=0.001,
    )
    return session, encoder, publisher, receiver


def wait_stopped(session: HlsStreamingSession) -> None:
    deadline = time.monotonic() + 1
    while session.state is SessionState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.001)
    assert session.state is SessionState.STOPPED


def wait_failed(session: HlsStreamingSession) -> None:
    deadline = time.monotonic() + 1
    while session.state is SessionState.RUNNING and time.monotonic() < deadline:
        time.sleep(0.001)
    assert session.state is SessionState.FAILED


@pytest.mark.parametrize("component", ["encoder", "publisher"])
def test_a_component_ending_by_itself_releases_the_whole_session(component: str) -> None:
    session, encoder, publisher, receiver = supervised()
    session.start(SyntheticVideoSource(), EncodingProfile())

    if component == "encoder":
        encoder.has_ended = True
    else:
        publisher.has_ended = True
    wait_stopped(session)

    assert session.unreleased == ()
    assert receiver.stopped == publisher.stopped == encoder.stopped == 1
    session.stop()  # idempotent after the monitor's cleanup
    assert receiver.stopped == publisher.stopped == encoder.stopped == 1


def test_failed_spontaneous_cleanup_stays_owned_and_is_retried() -> None:
    session, encoder, publisher, receiver = supervised()
    publisher.stop_failures = 1
    session.start(SyntheticVideoSource(), EncodingProfile())

    encoder.has_ended = True
    wait_failed(session)

    assert session.unreleased == ("publisher",)
    assert receiver.stopped == encoder.stopped == 1 and publisher.stopped == 1
    session.stop()
    assert session.state is SessionState.STOPPED and not session.unreleased
    assert publisher.stopped == 2


def test_a_health_check_failure_fails_closed_and_releases_everything() -> None:
    session, encoder, publisher, receiver = supervised()
    session.start(SyntheticVideoSource(), EncodingProfile())

    encoder.health_failure = True
    wait_failed(session)

    assert session.unreleased == ()
    assert session.last_error == "stream health check failed: RuntimeError"
    assert receiver.stopped == publisher.stopped == encoder.stopped == 1


def test_a_second_start_is_refused_while_the_monitor_is_running() -> None:
    session, _, _, _ = supervised()
    session.start(SyntheticVideoSource(), EncodingProfile())

    with pytest.raises(InvalidStreamConfiguration, match="monitor is already running"):
        session.start(SyntheticVideoSource(), EncodingProfile())

    session.close()
    assert session.state is SessionState.STOPPED and not session.unreleased


def test_a_monitor_start_failure_releases_the_started_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, encoder, publisher, receiver = supervised()

    def fail_start(_thread: threading.Thread) -> None:
        raise RuntimeError("simulated")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="simulated"):
        session.start(SyntheticVideoSource(), EncodingProfile())

    assert session.state is SessionState.STOPPED and not session.unreleased
    assert receiver.stopped == publisher.stopped == encoder.stopped == 1


def test_encoder_bound_cannot_exceed_publication_bound() -> None:
    config = HlsCompositionConfig(ReceiverTarget("tv"), "192.168.1.30", publication_seconds=10)
    settings = LiveEncoderSettings(max_session_seconds=11)

    with pytest.raises(InvalidStreamConfiguration, match="cannot exceed"):
        assemble_hls_session(
            cast(ControlService, object()),
            cast(ProcessRunner, object()),
            cast(CapabilityProbe, object()),
            config,
            encoder_settings=settings,
            route_address=lambda _receiver: "192.168.1.20",
        )


@pytest.mark.parametrize("duration", [True, 10.5])
def test_the_composition_duration_is_an_integer(duration: object) -> None:
    with pytest.raises(InvalidStreamConfiguration, match="publication_seconds"):
        HlsCompositionConfig(
            ReceiverTarget("tv"),
            "192.168.1.30",
            publication_seconds=duration,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("interval", [True, 0, float("inf"), 5.1])
def test_the_health_interval_is_bounded(interval: object) -> None:
    with pytest.raises(InvalidStreamConfiguration, match="health_interval_seconds"):
        HlsCompositionConfig(
            ReceiverTarget("tv"),
            "192.168.1.30",
            health_interval_seconds=interval,  # type: ignore[arg-type]
        )


def test_assembly_uses_the_selected_address_and_one_shared_duration() -> None:
    config = HlsCompositionConfig(ReceiverTarget("tv"), "192.168.1.30", publication_seconds=10)
    session = assemble_hls_session(
        cast(ControlService, object()),
        cast(ProcessRunner, object()),
        cast(CapabilityProbe, object()),
        config,
        encoder_settings=LiveEncoderSettings(max_session_seconds=10),
        route_address=lambda receiver: "192.168.1.20" if receiver == "192.168.1.30" else "",
    )

    assert session.bind_address == "192.168.1.20"
