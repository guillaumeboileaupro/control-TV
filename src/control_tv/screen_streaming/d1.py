"""D1 path A assembled: D0's live encoder, the HLS publisher and a receiver, one bounded session.

    python3 -m control_tv.screen_streaming.d1 --no-tv [--seconds 60] [--encoder software]
    python3 -m control_tv.screen_streaming.d1 --device DEVICE_ID [--seconds 60] [--encoder software]

`--no-tv` contacts no TV and binds loopback only: it prints the playlist URL for a local player
(`ffplay URL`). `--device` discovers the TV on the home network, publishes on the IPv4 address
this computer reaches it from, and loads the stream on its Cast receiver: run it only with the
owner's authorization. The picture is synthetic (no capture). One duration bounds the encoder
(FFmpeg `-t`) and, a little longer, the publication; the session ends at that duration, on
Ctrl-C, or as soon as FFmpeg ends by itself, and everything is then stopped.
"""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from control_tv.ports import TvControl
from control_tv.screen_streaming.cast_receiver import CastHlsReceiver
from control_tv.screen_streaming.h264_encoder import D0LiveEncoder, LiveEncoderSettings
from control_tv.screen_streaming.hls_publisher import HlsPublisher, home_network_address
from control_tv.screen_streaming.model import (
    MAX_SESSION_SECONDS,
    CapturedVideo,
    CaptureHandle,
    DisplaySelection,
    EncodedStream,
    EncodingProfile,
    InvalidStreamConfiguration,
    PublicationConfig,
    PublicationKind,
    PublishedStream,
    ReceiverSession,
    ReceiverTarget,
    ScreenStreamError,
    SyntheticVideoSource,
)
from control_tv.screen_streaming.ports import StreamReceiver
from control_tv.screen_streaming.service import StreamingSessionService, StreamRequest
from control_tv.screen_streaming.virtual_display import Position, XrandrVirtualMonitor
from control_tv.screen_streaming.x11_capture import X11Capture
from control_tv.screencast.capabilities import FfmpegCapabilityProbe
from control_tv.screencast.model import EncoderPreference
from control_tv.screencast.process import SubprocessRunner

# The publication outlives the encoder a little, so the last segments can still be fetched.
PUBLICATION_MARGIN_SECONDS = 15
MAX_D1_SECONDS = int(MAX_SESSION_SECONDS) - PUBLICATION_MARGIN_SECONDS
_POLL_SECONDS = 0.5
_ENCODERS = {
    "software": EncoderPreference.SOFTWARE_ONLY,
    "vaapi": EncoderPreference.VAAPI_ONLY,
    "auto": EncoderPreference.VAAPI_THEN_SOFTWARE,
}


def lan_address_towards(
    host: str, socket_factory: Callable[[int, int], socket.socket] = socket.socket
) -> str:
    """The local IPv4 address this computer reaches `host` from, as a home-network address.
    A UDP `connect` only selects the route: no packet is sent."""
    with socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect((host, 9))
        except OSError as error:
            raise InvalidStreamConfiguration(
                f"no route to the TV from this computer: {type(error).__name__}"
            ) from None
        address = str(probe.getsockname()[0])
    return home_network_address(address)


class NoReceiver:
    """`StreamReceiver` for `--no-tv`: nothing is sent anywhere; the URL is only printed."""

    def __init__(self, show: Callable[[str], None]) -> None:
        self._show = show

    def start(
        self, target: ReceiverTarget, publication: PublishedStream, *, timeout: float
    ) -> ReceiverSession:
        self._show(publication.uri)
        return ReceiverSession("no-receiver")

    def stop(self, session: ReceiverSession) -> None:
        """Nothing to stop: no receiver was contacted."""


class Mode(StrEnum):
    SYNTHETIC = "synthetic"  # the test picture, nothing of the desktop
    DUPLICATE = "duplicate"  # "Dupliquer": one existing screen output
    EXTEND = "extend"  # "Étendre": a new virtual monitor (D2)


@dataclass
class D1Session:
    """One assembled path A session: `start`, `ended`, `stop` (or `run` for the command)."""

    encoder: D0LiveEncoder
    publisher: HlsPublisher
    service: StreamingSessionService
    request: StreamRequest
    seconds: int
    capture: X11Capture | None = None
    capture_handle: CaptureHandle | None = None
    virtual: XrandrVirtualMonitor | None = None

    def start(self) -> None:
        """Start the encoder, the publication and the receiver; on failure everything this
        session prepared (capture, virtual monitor) is released too."""
        try:
            self.service.start(self.request)
        except BaseException:
            self.stop()
            raise

    def ended(self) -> bool:
        """Whether the encoder or publisher ended without an explicit stop."""
        return self._ended_reason() is not None

    def _ended_reason(self) -> str | None:
        streams = self.encoder.unreleased
        if not streams or self.encoder.ended(EncodedStream(streams[0])):
            return "encoder ended"
        publications = self.publisher.unreleased
        if not publications or self.publisher.ended(PublishedStream(publications[0])):
            return "publisher ended"
        return None

    def stop(self) -> None:
        """Stop the receiver, the publication and the encoder, then forget the capture and
        remove the virtual monitor. Every step is attempted, even after an interrupt; then an
        interrupt (`KeyboardInterrupt`, `SystemExit`) is raised first, else the first failure."""
        failures: list[BaseException] = []
        steps: list[Callable[[], None]] = [self.service.close]
        if self.capture is not None and self.capture_handle is not None:
            capture, handle = self.capture, self.capture_handle
            steps.append(lambda: capture.stop(handle))
        if self.virtual is not None:
            steps.append(self.virtual.remove)
        for step in steps:
            try:
                step()
            except BaseException as error:  # the next steps still run
                failures.append(error)
        interrupts = [error for error in failures if not isinstance(error, Exception)]
        if interrupts or failures:
            raise (interrupts or failures)[0]

    def run(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> str:
        """Why the session ended: "duration", "encoder ended" or "interrupted". Everything is
        stopped in every case."""
        self.start()
        reason = "duration"
        try:
            deadline = clock() + self.seconds
            while clock() < deadline:
                ended = self._ended_reason()
                if ended is not None:
                    reason = ended
                    break
                sleep(_POLL_SECONDS)
        except KeyboardInterrupt:
            reason = "interrupted"
        finally:
            self.stop()
        return reason


@dataclass(frozen=True)
class Picture:
    """What to stream: the mode, the output to duplicate, and the encoded size and rate."""

    mode: Mode = Mode.SYNTHETIC
    screen: str | None = None
    width: int = 1920
    height: int = 1080
    fps: int = 30
    consent: bool = False  # the owner's explicit agreement to change the display (EXTEND)
    refresh: int = 60  # the extended screen's refresh rate (EXTEND)
    position: Position = Position.RIGHT  # where the extended screen sits (EXTEND)
    x: int | None = None
    y: int | None = None


def assemble(
    *,
    seconds: int,
    bind_address: str,
    receiver: StreamReceiver,
    device_id: str,
    preference: EncoderPreference = EncoderPreference.SOFTWARE_ONLY,
    picture: Picture | None = None,
    encoder: D0LiveEncoder | None = None,
    capture: X11Capture | None = None,
    virtual: XrandrVirtualMonitor | None = None,
) -> D1Session:
    """The composition root: one duration for the encoder and (with a margin) the publication;
    the picture is the test pattern, a duplicated output or a new virtual monitor."""
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int)
        or not 1 <= seconds <= MAX_D1_SECONDS
    ):
        raise InvalidStreamConfiguration(f"seconds must be an integer from 1 to {MAX_D1_SECONDS}")
    chosen = picture or Picture()
    profile = EncodingProfile(chosen.width, chosen.height, chosen.fps)
    source: SyntheticVideoSource | CapturedVideo = SyntheticVideoSource()
    handle: CaptureHandle | None = None
    used_virtual: XrandrVirtualMonitor | None = None
    if chosen.mode is not Mode.SYNTHETIC:
        capture = capture or X11Capture()
        if chosen.mode is Mode.EXTEND:
            used_virtual = virtual or XrandrVirtualMonitor()
            output = used_virtual.create(
                chosen.width,
                chosen.height,
                consent=chosen.consent,
                refresh=chosen.refresh,
                position=chosen.position,
                x=chosen.x,
                y=chosen.y,
            )
            screen = output.name
        elif chosen.screen is None:
            raise InvalidStreamConfiguration("choose the screen output to duplicate")
        else:
            screen = chosen.screen
        try:
            handle = capture.start(DisplaySelection(screen))
        except BaseException:
            if used_virtual is not None:
                used_virtual.remove()
            raise
        source = CapturedVideo(handle)
    live = encoder or D0LiveEncoder(
        SubprocessRunner(),
        FfmpegCapabilityProbe(),
        LiveEncoderSettings(preference=preference, max_session_seconds=seconds),
        capture_region=None if capture is None else capture.region,
    )
    publisher = HlsPublisher(live.output, bind_address=bind_address)
    publication = PublicationConfig(
        PublicationKind.HLS, max_session_seconds=float(seconds + PUBLICATION_MARGIN_SECONDS)
    )
    request = StreamRequest(
        source, profile, publication, ReceiverTarget(device_id), receiver_timeout=30.0
    )
    service = StreamingSessionService(live, publisher, receiver)
    return D1Session(live, publisher, service, request, seconds, capture, handle, used_virtual)


def tv_and_route(device_id: str) -> tuple[TvControl, str]:  # pragma: no cover - a real TV
    """The shared control layer and the LAN address this computer reaches `device_id` from."""
    from control_tv.adapters import PyChromecastTransport
    from control_tv.service import ControlService

    control = ControlService(PyChromecastTransport())
    for device in control.discover_devices():
        if str(device.id) == device_id:
            return control, lan_address_towards(device.host)
    raise InvalidStreamConfiguration("that TV was not found on the home network")


_POSITIONS = {
    "left": Position.LEFT,
    "right": Position.RIGHT,
    "above": Position.ABOVE,
    "below": Position.BELOW,
}


def _size(value: str) -> tuple[int, int]:
    width, separator, height = value.partition("x")
    if not separator or not width.isdigit() or not height.isdigit():
        raise argparse.ArgumentTypeError("a size is WIDTHxHEIGHT, for example 1280x720")
    return int(width), int(height)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--list-screens", action="store_true", help="list the X11 outputs")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--no-tv", action="store_true", help="loopback only, print the URL")
    target.add_argument("--device", help="the TV's device id (owner authorization required)")
    picture = parser.add_mutually_exclusive_group()
    picture.add_argument("--screen", help="duplicate this X11 output (see --list-screens)")
    picture.add_argument(
        "--extend", action="store_true", help="stream a new virtual monitor (changes the display)"
    )
    parser.add_argument("--size", type=_size, default=(1920, 1080), help="encoded WIDTHxHEIGHT")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--refresh", type=int, default=60, help="extended screen: 60 or 50 Hz")
    parser.add_argument(
        "--position",
        choices=sorted(_POSITIONS),
        default="right",
        help="extended screen: where it sits next to the primary screen",
    )
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--encoder", choices=sorted(_ENCODERS), default="software")
    parser.add_argument(
        "--yes-change-display", action="store_true", help="consent to --extend's display change"
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.list_screens:
            for output in X11Capture().outputs():
                primary = " (primary)" if output.primary else ""
                print(
                    f"{output.name}: {output.width}x{output.height}+{output.x}+{output.y}{primary}"
                )
            return 0
        if not arguments.no_tv and arguments.device is None:
            parser.error("choose --no-tv or --device")
        mode = Mode.SYNTHETIC
        if arguments.extend:
            mode = Mode.EXTEND
        elif arguments.screen:
            mode = Mode.DUPLICATE
        width, height = arguments.size
        chosen = Picture(
            mode,
            arguments.screen,
            width,
            height,
            arguments.fps,
            consent=arguments.yes_change_display,
            refresh=arguments.refresh,
            position=_POSITIONS[arguments.position],
        )
        if arguments.no_tv:
            receiver: StreamReceiver = NoReceiver(lambda uri: print(f"playlist: {uri}", flush=True))
            address = "127.0.0.1"
        else:  # pragma: no cover - a real TV, with the owner's authorization
            control, address = tv_and_route(arguments.device)
            receiver = CastHlsReceiver(control)
        session = assemble(
            seconds=arguments.seconds,
            bind_address=address,
            receiver=receiver,
            device_id=arguments.device or "no-tv",
            preference=_ENCODERS[arguments.encoder],
            picture=chosen,
        )
        signal.signal(signal.SIGTERM, _interrupt)  # a polite kill stops everything too
        reason = session.run()
    except ScreenStreamError as error:
        print(f"error: {error.message}", file=sys.stderr)
        return 1
    print(f"session ended: {reason}; everything stopped")
    return 0


def _interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
