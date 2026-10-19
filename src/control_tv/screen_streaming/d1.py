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
import socket
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from control_tv.ports import TvControl
from control_tv.screen_streaming.cast_receiver import CastHlsReceiver
from control_tv.screen_streaming.h264_encoder import D0LiveEncoder, LiveEncoderSettings
from control_tv.screen_streaming.hls_publisher import HlsPublisher, home_network_address
from control_tv.screen_streaming.model import (
    MAX_SESSION_SECONDS,
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


@dataclass
class D1Session:
    """One assembled path A session; `run` starts it, waits, then stops everything."""

    encoder: D0LiveEncoder
    publisher: HlsPublisher
    service: StreamingSessionService
    request: StreamRequest
    seconds: int

    def run(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> str:
        """Why the session ended: "duration", "encoder ended" or "interrupted". Everything is
        stopped in every case; a cleanup failure is raised after the interruption is noted."""
        self.service.start(self.request)
        reason = "duration"
        try:
            deadline = clock() + self.seconds
            while clock() < deadline:
                streams = self.encoder.unreleased
                if not streams or self.encoder.ended(EncodedStream(streams[0])):
                    reason = "encoder ended"
                    break
                sleep(_POLL_SECONDS)
        except KeyboardInterrupt:
            reason = "interrupted"
        finally:
            self.service.close()
        return reason


def assemble(
    *,
    seconds: int,
    bind_address: str,
    receiver: StreamReceiver,
    device_id: str,
    preference: EncoderPreference = EncoderPreference.SOFTWARE_ONLY,
    profile: EncodingProfile | None = None,
    encoder: D0LiveEncoder | None = None,
) -> D1Session:
    """The composition root: one duration for the encoder and (with a margin) the publication."""
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, int)
        or not 1 <= seconds <= MAX_D1_SECONDS
    ):
        raise InvalidStreamConfiguration(f"seconds must be an integer from 1 to {MAX_D1_SECONDS}")
    live = encoder or D0LiveEncoder(
        SubprocessRunner(),
        FfmpegCapabilityProbe(),
        LiveEncoderSettings(preference=preference, max_session_seconds=seconds),
    )
    publisher = HlsPublisher(live.output, bind_address=bind_address)
    publication = PublicationConfig(
        PublicationKind.HLS, max_session_seconds=float(seconds + PUBLICATION_MARGIN_SECONDS)
    )
    request = StreamRequest(
        SyntheticVideoSource(),
        profile or EncodingProfile(),
        publication,
        ReceiverTarget(device_id),
        receiver_timeout=30.0,
    )
    return D1Session(
        live, publisher, StreamingSessionService(live, publisher, receiver), request, seconds
    )


def _tv(device_id: str) -> tuple[TvControl, str]:  # pragma: no cover - discovers a real TV
    from control_tv.adapters import PyChromecastTransport
    from control_tv.service import ControlService

    control = ControlService(PyChromecastTransport())
    for device in control.discover_devices():
        if str(device.id) == device_id:
            return control, device.host
    raise InvalidStreamConfiguration("that TV was not found on the home network")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--no-tv", action="store_true", help="loopback only, print the URL")
    target.add_argument("--device", help="the TV's device id (owner authorization required)")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--encoder", choices=sorted(_ENCODERS), default="software")
    arguments = parser.parse_args(argv)
    try:
        if arguments.no_tv:
            receiver: StreamReceiver = NoReceiver(lambda uri: print(f"playlist: {uri}", flush=True))
            session = assemble(
                seconds=arguments.seconds,
                bind_address="127.0.0.1",
                receiver=receiver,
                device_id="no-tv",
                preference=_ENCODERS[arguments.encoder],
            )
        else:  # pragma: no cover - a real TV, with the owner's authorization
            control, host = _tv(arguments.device)
            session = assemble(
                seconds=arguments.seconds,
                bind_address=lan_address_towards(host),
                receiver=CastHlsReceiver(control),
                device_id=arguments.device,
                preference=_ENCODERS[arguments.encoder],
            )
        reason = session.run()
    except ScreenStreamError as error:
        print(f"error: {error.message}", file=sys.stderr)
        return 1
    print(f"session ended: {reason}; everything stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
