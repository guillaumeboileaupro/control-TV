"""D1 path-A composition, without capture, UI or automatic launch.

Construction selects the one local IPv4 address routed towards the receiver and wires D0's live
encoder, the HLS publisher, the Cast receiver and the existing lifecycle service. Nothing starts
until ``HlsStreamingSession.start`` is called explicitly.
"""

from __future__ import annotations

import contextlib
import math
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from control_tv.screen_streaming.cast_receiver import CastHlsReceiver
from control_tv.screen_streaming.h264_encoder import D0LiveEncoder, LiveEncoderSettings
from control_tv.screen_streaming.hls_publisher import HlsPublisher, home_network_address
from control_tv.screen_streaming.model import (
    EncodingProfile,
    InvalidStreamConfiguration,
    PublicationConfig,
    PublicationKind,
    ReceiverTarget,
    SessionState,
    SyntheticVideoSource,
)
from control_tv.screen_streaming.service import StreamingSessionService, StreamRequest
from control_tv.screencast.capabilities import CapabilityProbe
from control_tv.screencast.process import ProcessRunner
from control_tv.service import ControlService

_ROUTE_PORT = 9


class RouteSocket(Protocol):
    def connect(self, address: tuple[str, int]) -> None: ...

    def getsockname(self) -> tuple[str, int]: ...

    def close(self) -> None: ...


RouteSocketFactory = Callable[[int, int], RouteSocket]


def routed_lan_ipv4(
    receiver_ipv4: str,
    *,
    socket_factory: RouteSocketFactory = socket.socket,
) -> str:
    """Return the local home-network IPv4 selected by the OS route to ``receiver_ipv4``.

    UDP ``connect`` performs route selection without transmitting a datagram. The receiver and
    selected local address are both constrained to home-network IPv4 addresses; loopback is only
    accepted when both sides are loopback, which is useful for offline integration tests.
    """
    receiver = home_network_address(receiver_ipv4)
    try:
        route = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as error:
        raise InvalidStreamConfiguration(
            f"cannot inspect the IPv4 route: {type(error).__name__}"
        ) from None
    try:
        route.connect((receiver, _ROUTE_PORT))
        endpoint = route.getsockname()
        if type(endpoint) is not tuple or len(endpoint) != 2 or not isinstance(endpoint[0], str):
            raise ValueError("malformed route endpoint")
        selected = endpoint[0]
    except (OSError, TypeError, ValueError) as error:
        raise InvalidStreamConfiguration(
            f"no IPv4 route to the receiver: {type(error).__name__}"
        ) from None
    finally:
        with contextlib.suppress(OSError):
            route.close()
    local = home_network_address(selected)
    if (receiver.startswith("127.")) != (local.startswith("127.")):
        raise InvalidStreamConfiguration("the receiver and selected address use different scopes")
    return local


@dataclass(frozen=True)
class HlsCompositionConfig:
    """Fixed bounds for one assembled path-A service."""

    receiver: ReceiverTarget
    receiver_ipv4: str
    publication_seconds: int = 120
    health_interval_seconds: float = 0.25

    def __post_init__(self) -> None:
        if type(self.receiver) is not ReceiverTarget:
            raise InvalidStreamConfiguration("receiver must be a ReceiverTarget")
        if isinstance(self.publication_seconds, bool) or not isinstance(
            self.publication_seconds, int
        ):
            raise InvalidStreamConfiguration("publication_seconds must be an integer")
        PublicationConfig(PublicationKind.HLS, self.publication_seconds)
        interval = self.health_interval_seconds
        if (
            isinstance(interval, bool)
            or not isinstance(interval, int | float)
            or not math.isfinite(interval)
            or not 0 < interval <= 5
        ):
            raise InvalidStreamConfiguration(
                "health_interval_seconds must be a finite number from more than 0 to 5"
            )
        home_network_address(self.receiver_ipv4)


class HlsStreamingSession:
    """Bounded path-A facade plus health supervision for the existing lifecycle service."""

    def __init__(
        self,
        service: StreamingSessionService,
        publication: PublicationConfig,
        *,
        receiver: ReceiverTarget,
        bind_address: str,
        health_interval_seconds: float,
    ) -> None:
        self._service = service
        self._publication = publication
        self._receiver = receiver
        self._bind_address = bind_address
        self._interval = health_interval_seconds
        self._wake = threading.Event()
        self._monitor: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def bind_address(self) -> str:
        return self._bind_address

    @property
    def state(self) -> SessionState:
        return self._service.state

    @property
    def unreleased(self) -> tuple[str, ...]:
        return self._service.unreleased

    @property
    def last_error(self) -> str | None:
        return self._service.last_error

    def start(
        self,
        source: SyntheticVideoSource,
        profile: EncodingProfile,
        *,
        receiver_timeout: float = 10.0,
    ) -> None:
        with self._lock:
            if self._monitor is not None and self._monitor.is_alive():
                raise InvalidStreamConfiguration("a stream monitor is already running")
            self._wake.clear()
            self._service.start(
                StreamRequest(source, profile, self._publication, self._receiver, receiver_timeout)
            )
            monitor = threading.Thread(
                target=self._watch,
                name="control-tv-stream-health",
                daemon=True,
            )
            self._monitor = monitor
            try:
                monitor.start()
            except BaseException as error:
                self._monitor = None
                self._wake.set()
                try:
                    self._service.stop()
                except BaseException as cleanup_error:
                    if isinstance(error, Exception) and not isinstance(cleanup_error, Exception):
                        raise cleanup_error from error
                raise

    def stop(self) -> None:
        with self._lock:
            self._stop_monitor()
            self._service.stop()

    def close(self) -> None:
        with self._lock:
            self._stop_monitor()
            self._service.close()

    def _stop_monitor(self) -> None:
        self._wake.set()
        monitor = self._monitor
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join()
        self._monitor = None

    def _watch(self) -> None:
        while not self._wake.wait(self._interval):
            try:
                if self._service.reconcile():
                    return
            except BaseException:
                return


def assemble_hls_session(
    control: ControlService,
    runner: ProcessRunner,
    probe: CapabilityProbe,
    config: HlsCompositionConfig,
    *,
    encoder_settings: LiveEncoderSettings | None = None,
    route_address: Callable[[str], str] = routed_lan_ipv4,
) -> HlsStreamingSession:
    """Wire the existing D0/D1 components with coherent resource bounds."""
    settings = encoder_settings or LiveEncoderSettings(
        max_session_seconds=config.publication_seconds
    )
    if settings.max_session_seconds > config.publication_seconds:
        raise InvalidStreamConfiguration(
            "the encoder session bound cannot exceed the publication bound"
        )
    bind_address = home_network_address(route_address(config.receiver_ipv4))
    receiver_target = ReceiverTarget(config.receiver.device_id)
    encoder = D0LiveEncoder(runner, probe, settings)
    publisher = HlsPublisher(encoder.output, bind_address=bind_address)
    receiver = CastHlsReceiver(control)
    service = StreamingSessionService(
        encoder,
        publisher,
        receiver,
        encoder_ended=encoder.ended,
        publisher_ended=publisher.ended,
    )
    publication = PublicationConfig(PublicationKind.HLS, config.publication_seconds)
    return HlsStreamingSession(
        service,
        publication,
        receiver=receiver_target,
        bind_address=bind_address,
        health_interval_seconds=config.health_interval_seconds,
    )
