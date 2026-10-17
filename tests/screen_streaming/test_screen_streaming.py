"""Deterministic D1 lifecycle tests: no process, display, socket or receiver is used."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

import pytest

from control_tv.screen_streaming import (
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
    SessionState,
    StreamCleanupError,
    StreamingSessionService,
    StreamRequest,
    StreamStartError,
    SyntheticPattern,
    SyntheticVideoSource,
)
from control_tv.screen_streaming.model import VideoSource


@dataclass
class FakeEncoder:
    events: list[str]
    start_error: BaseException | None = None
    stop_error: BaseException | None = None
    gate: threading.Event | None = None
    entered: threading.Event = field(default_factory=threading.Event)
    active: set[str] = field(default_factory=set)

    def start(self, source: VideoSource, profile: EncodingProfile) -> EncodedStream:
        self.events.append("encoder.start")
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.start_error is not None:
            raise self.start_error
        stream = EncodedStream("encoded-1")
        self.active.add(stream.handle)
        return stream

    def stop(self, stream: EncodedStream) -> None:
        self.events.append("encoder.stop")
        if self.stop_error is not None:
            raise self.stop_error
        self.active.discard(stream.handle)


@dataclass
class FakePublisher:
    events: list[str]
    start_error: BaseException | None = None
    stop_error: BaseException | None = None
    active: set[str] = field(default_factory=set)

    def start(self, stream: EncodedStream, config: PublicationConfig) -> PublishedStream:
        self.events.append("publisher.start")
        if self.start_error is not None:
            raise self.start_error
        publication = PublishedStream("http://127.0.0.1/session/opaque-token/index.m3u8")
        self.active.add(publication.uri)
        return publication

    def stop(self, publication: PublishedStream) -> None:
        self.events.append("publisher.stop")
        if self.stop_error is not None:
            raise self.stop_error
        self.active.discard(publication.uri)


@dataclass
class FakeReceiver:
    events: list[str]
    start_error: BaseException | None = None
    stop_error: BaseException | None = None
    active: set[str] = field(default_factory=set)
    timeout: float | None = None

    def start(
        self,
        target: ReceiverTarget,
        publication: PublishedStream,
        *,
        timeout: float,
    ) -> ReceiverSession:
        self.events.append("receiver.start")
        self.timeout = timeout
        if self.start_error is not None:
            raise self.start_error
        session = ReceiverSession("receiver-1")
        self.active.add(session.handle)
        return session

    def stop(self, session: ReceiverSession) -> None:
        self.events.append("receiver.stop")
        if self.stop_error is not None:
            raise self.stop_error
        self.active.discard(session.handle)


@dataclass
class Rig:
    events: list[str] = field(default_factory=list)
    encoder: FakeEncoder = field(init=False)
    publisher: FakePublisher = field(init=False)
    receiver: FakeReceiver = field(init=False)
    service: StreamingSessionService = field(init=False)

    def __post_init__(self) -> None:
        self.encoder = FakeEncoder(self.events)
        self.publisher = FakePublisher(self.events)
        self.receiver = FakeReceiver(self.events)
        self.service = StreamingSessionService(self.encoder, self.publisher, self.receiver)

    def assert_no_resources(self) -> None:
        assert self.encoder.active == set()
        assert self.publisher.active == set()
        assert self.receiver.active == set()


REQUEST = StreamRequest(
    SyntheticVideoSource(),
    EncodingProfile(),
    PublicationConfig(PublicationKind.HLS),
    ReceiverTarget("receiver-id"),
    receiver_timeout=3.0,
)


def test_nothing_starts_at_construction() -> None:
    rig = Rig()

    assert rig.service.state is SessionState.IDLE
    assert rig.events == []
    rig.assert_no_resources()


def test_explicit_start_acquires_each_resource_in_order() -> None:
    rig = Rig()

    rig.service.start(REQUEST)

    assert rig.service.state is SessionState.RUNNING
    assert rig.events == ["encoder.start", "publisher.start", "receiver.start"]
    assert rig.receiver.timeout == 3.0

    with pytest.raises(StreamStartError, match="cannot start while running"):
        rig.service.start(REQUEST)
    rig.service.stop()


def test_stop_releases_every_resource_in_reverse_order_and_is_idempotent() -> None:
    rig = Rig()
    rig.service.start(REQUEST)

    rig.service.stop()
    rig.service.stop()
    rig.service.close()

    assert rig.events[-3:] == ["receiver.stop", "publisher.stop", "encoder.stop"]
    assert rig.service.state is SessionState.STOPPED
    rig.assert_no_resources()


def test_publication_failure_stops_the_encoder() -> None:
    rig = Rig()
    rig.publisher.start_error = ConnectionError("simulated")

    with pytest.raises(StreamStartError, match="ConnectionError"):
        rig.service.start(REQUEST)

    assert rig.events == ["encoder.start", "publisher.start", "encoder.stop"]
    assert rig.service.state is SessionState.FAILED
    rig.assert_no_resources()


def test_explicit_port_error_is_preserved() -> None:
    rig = Rig()
    rig.publisher.start_error = StreamStartError("publisher rejected the stream")

    with pytest.raises(StreamStartError, match="publisher rejected the stream"):
        rig.service.start(REQUEST)

    rig.assert_no_resources()


def test_receiver_connection_failure_stops_publication_then_encoder() -> None:
    rig = Rig()
    rig.receiver.start_error = TimeoutError("simulated")

    with pytest.raises(StreamStartError, match="TimeoutError"):
        rig.service.start(REQUEST)

    assert rig.events[-2:] == ["publisher.stop", "encoder.stop"]
    assert rig.service.state is SessionState.FAILED
    rig.assert_no_resources()


@pytest.mark.parametrize("where", ["encoder", "publisher", "receiver"])
def test_interruption_cleans_every_resource_already_owned_and_is_re_raised(where: str) -> None:
    rig = Rig()
    getattr(rig, where).start_error = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        rig.service.start(REQUEST)

    assert rig.service.state is SessionState.FAILED
    rig.assert_no_resources()


def test_every_cleanup_is_attempted_when_one_step_fails() -> None:
    rig = Rig()
    rig.receiver.stop_error = RuntimeError("receiver")
    rig.publisher.stop_error = OSError("publisher")
    rig.service.start(REQUEST)

    with pytest.raises(StreamCleanupError) as raised:
        rig.service.stop()

    assert rig.events[-3:] == ["receiver.stop", "publisher.stop", "encoder.stop"]
    assert "receiver cleanup failed" in raised.value.message
    assert "publisher cleanup failed" in raised.value.message
    assert rig.service.state is SessionState.FAILED
    assert rig.receiver.active and rig.publisher.active and not rig.encoder.active

    with pytest.raises(StreamStartError, match="earlier resources"):
        rig.service.start(REQUEST)
    rig.receiver.stop_error = None
    rig.publisher.stop_error = None
    rig.service.close()
    rig.assert_no_resources()


def test_cleanup_failures_are_preserved_beside_a_start_failure() -> None:
    rig = Rig()
    rig.receiver.start_error = TimeoutError("simulated")
    rig.publisher.stop_error = OSError("simulated cleanup")

    with pytest.raises(StreamStartError) as raised:
        rig.service.start(REQUEST)

    assert "TimeoutError" in raised.value.message
    assert "publisher cleanup failed: OSError" in raised.value.message
    assert rig.events[-2:] == ["publisher.stop", "encoder.stop"]
    assert rig.publisher.active
    rig.publisher.stop_error = None
    rig.service.close()
    rig.assert_no_resources()


def test_context_manager_cleans_after_a_caller_error() -> None:
    rig = Rig()

    with pytest.raises(ValueError), rig.service:
        rig.service.start(REQUEST)
        raise ValueError("caller")

    rig.assert_no_resources()
    assert rig.service.state is SessionState.STOPPED


def test_concurrent_lifecycle_call_is_refused_without_touching_resources() -> None:
    rig = Rig()
    gate = threading.Event()
    rig.encoder.gate = gate
    failures: list[BaseException] = []

    def start() -> None:
        try:
            rig.service.start(REQUEST)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=start)
    worker.start()
    assert rig.encoder.entered.wait(5.0)
    try:
        with pytest.raises(StreamStartError, match="another stream lifecycle call"):
            rig.service.stop()
    finally:
        gate.set()
        worker.join(5.0)

    assert failures == [] and rig.service.state is SessionState.RUNNING
    rig.service.stop()
    rig.assert_no_resources()


@pytest.mark.parametrize(
    "profile",
    [
        EncodingProfile(width=16, height=16, fps=1),
        EncodingProfile(width=4096, height=4096, fps=60),
    ],
)
def test_encoding_profile_accepts_its_resource_boundaries(profile: EncodingProfile) -> None:
    assert profile.width % 2 == 0 and profile.height % 2 == 0


@pytest.mark.parametrize(
    ("width", "height", "fps"),
    [
        (15, 1080, 30),
        (4097, 1080, 30),
        (17, 1080, 30),
        (1920, 15, 30),
        (1920, 17, 30),
        (1920, 1080, 0),
        (1920, 1080, 61),
        (1920, 1080, True),
    ],
)
def test_invalid_encoding_profiles_are_rejected(width: int, height: int, fps: int) -> None:
    with pytest.raises(InvalidStreamConfiguration):
        EncodingProfile(width, height, fps)


@pytest.mark.parametrize("value", [0.0, -1.0, math.nan, math.inf, -math.inf, True, 3601.0])
def test_invalid_session_limits_are_rejected(value: float) -> None:
    with pytest.raises(InvalidStreamConfiguration, match="max_session_seconds"):
        PublicationConfig(PublicationKind.HLS, value)


def test_maximum_session_limit_is_accepted() -> None:
    assert (
        PublicationConfig(PublicationKind.CAST_STREAMING, MAX_SESSION_SECONDS).max_session_seconds
        == MAX_SESSION_SECONDS
    )


@pytest.mark.parametrize("value", [0.0, -1.0, math.nan, math.inf, -math.inf, True, 61.0])
def test_invalid_receiver_timeouts_are_rejected(value: float) -> None:
    with pytest.raises(InvalidStreamConfiguration, match="receiver_timeout"):
        StreamRequest(
            SyntheticVideoSource(),
            EncodingProfile(),
            PublicationConfig(PublicationKind.HLS),
            ReceiverTarget("receiver-id"),
            receiver_timeout=value,
        )


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "file:///tmp/video.mp4",
        "ftp://host/video",
        "http://user:secret@host/video",
        "http://host/video#fragment",
        "http:///missing-host",
    ],
)
def test_unsafe_or_unsupported_publication_uris_are_rejected(uri: str) -> None:
    with pytest.raises(InvalidStreamConfiguration):
        PublishedStream(uri)


def test_source_is_a_typed_specification_not_encoder_arguments() -> None:
    source = SyntheticVideoSource(SyntheticPattern.SMPTE_BARS)

    assert source.pattern is SyntheticPattern.SMPTE_BARS
    assert not hasattr(source, "input_arguments")


def test_request_is_revalidated_before_any_adapter_is_called() -> None:
    rig = Rig()
    forged = StreamRequest(
        SyntheticVideoSource(),
        EncodingProfile(),
        PublicationConfig(PublicationKind.HLS),
        ReceiverTarget("receiver-id"),
    )
    object.__setattr__(forged.profile, "fps", 1000)

    with pytest.raises(InvalidStreamConfiguration, match="fps"):
        rig.service.start(forged)

    assert rig.events == []
    rig.assert_no_resources()


def test_malformed_request_shape_is_rejected_before_any_adapter() -> None:
    rig = Rig()
    forged = StreamRequest(
        SyntheticVideoSource(),
        EncodingProfile(),
        PublicationConfig(PublicationKind.HLS),
        ReceiverTarget("receiver-id"),
    )
    object.__setattr__(forged, "profile", object())

    with pytest.raises(InvalidStreamConfiguration, match="malformed"):
        rig.service.start(forged)
    with pytest.raises(InvalidStreamConfiguration, match="exact StreamRequest"):
        rig.service.start(object())  # type: ignore[arg-type]

    assert rig.events == []


@pytest.mark.parametrize("field", ["source", "profile", "publication", "receiver"])
def test_request_requires_exact_typed_values(field: str) -> None:
    values: dict[str, object] = {
        "source": SyntheticVideoSource(),
        "profile": EncodingProfile(),
        "publication": PublicationConfig(PublicationKind.HLS),
        "receiver": ReceiverTarget("receiver-id"),
    }
    values[field] = object()

    with pytest.raises(InvalidStreamConfiguration):
        StreamRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "build",
    [
        lambda: SyntheticVideoSource("x11grab"),  # type: ignore[arg-type]
        lambda: PublicationConfig("hls"),  # type: ignore[arg-type]
        lambda: DisplaySelection(""),
        lambda: CaptureHandle(""),
    ],
)
def test_other_invalid_typed_values_are_rejected(build: object) -> None:
    with pytest.raises(InvalidStreamConfiguration):
        build()  # type: ignore[operator]


def test_capture_values_are_opaque_and_have_no_side_effect() -> None:
    capture = CapturedVideo(CaptureHandle("capture-1"))

    assert capture.handle.value == "capture-1"
    assert DisplaySelection("HDMI-1").output_id == "HDMI-1"


def state(service: StreamingSessionService) -> SessionState:
    """Reads the state through a call, so each check sees the current value."""
    return service.state


def unreleased(service: StreamingSessionService) -> tuple[str, ...]:
    return service.unreleased


# P2-1: a concurrent close() is never lost ----------------------------------------------------


def run_start_in_thread(rig: Rig) -> tuple[threading.Thread, list[BaseException], threading.Event]:
    gate = threading.Event()
    rig.encoder.gate = gate
    failures: list[BaseException] = []

    def start() -> None:
        try:
            rig.service.start(REQUEST)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=start)
    worker.start()
    assert rig.encoder.entered.wait(5.0)
    return worker, failures, gate


def test_a_close_during_start_is_never_lost() -> None:
    """Regression: close() returned at once while start() held the lock, and the start then
    finished RUNNING with every resource active after shutdown was reported complete."""
    rig = Rig()
    worker, _, gate = run_start_in_thread(rig)
    closer = threading.Thread(target=rig.service.close)
    closer.start()
    threading.Timer(0.05, gate.set).start()

    closer.join(5.0)
    worker.join(5.0)

    assert not closer.is_alive() and not worker.is_alive()
    assert state(rig.service) is SessionState.STOPPED
    rig.assert_no_resources()


def test_a_close_that_cannot_wait_is_completed_by_the_interrupted_start() -> None:
    events: list[str] = []
    encoder, publisher, receiver = FakeEncoder(events), FakePublisher(events), FakeReceiver(events)
    gate = threading.Event()
    encoder.gate = gate
    service = StreamingSessionService(encoder, publisher, receiver, close_timeout=0.01)
    failures: list[BaseException] = []

    def start() -> None:
        try:
            service.start(REQUEST)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=start)
    worker.start()
    assert encoder.entered.wait(5.0)

    service.close()  # cannot wait: the request is recorded, never dropped
    assert service.last_error is not None and "close() is pending" in service.last_error
    gate.set()
    worker.join(5.0)

    assert len(failures) == 1 and isinstance(failures[0], StreamStartError)
    assert "cancelled by close()" in failures[0].message
    assert events == ["encoder.start", "encoder.stop"]  # nothing further was started
    assert state(service) is SessionState.STOPPED and encoder.active == set()


def test_no_start_is_accepted_after_close() -> None:
    rig = Rig()
    rig.service.close()

    with pytest.raises(StreamStartError, match="closed"):
        rig.service.start(REQUEST)
    assert rig.events == []


def test_close_is_idempotent_and_never_releases_twice() -> None:
    rig = Rig()
    rig.service.start(REQUEST)

    rig.service.close()
    rig.service.close()
    rig.service.stop()

    assert rig.events.count("receiver.stop") == 1
    assert rig.events.count("publisher.stop") == 1
    assert rig.events.count("encoder.stop") == 1
    rig.assert_no_resources()


def test_a_stop_during_start_is_still_refused_not_queued() -> None:
    rig = Rig()
    worker, failures, gate = run_start_in_thread(rig)
    try:
        with pytest.raises(StreamStartError, match="another stream lifecycle call"):
            rig.service.stop()
    finally:
        gate.set()
        worker.join(5.0)
    assert failures == [] and state(rig.service) is SessionState.RUNNING
    rig.service.close()
    rig.assert_no_resources()


# P2-2: an interruption during cleanup never abandons a resource ------------------------------


def test_an_interruption_during_stop_finishes_cleanup_then_is_re_raised() -> None:
    """Regression: an interruption in the receiver's stop skipped the publisher and the encoder
    and left the service STOPPING."""
    rig = Rig()
    rig.service.start(REQUEST)
    rig.receiver.stop_error = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        rig.service.stop()

    assert rig.events[-3:] == ["receiver.stop", "publisher.stop", "encoder.stop"]
    assert state(rig.service) is SessionState.FAILED
    assert unreleased(rig.service) == ("receiver",)
    assert (
        rig.service.last_error is not None
        and "receiver cleanup interrupted" in rig.service.last_error
    )
    assert not rig.publisher.active and not rig.encoder.active and rig.receiver.active

    rig.receiver.stop_error = None  # retry: only what is still owned is stopped again
    rig.service.stop()
    assert state(rig.service) is SessionState.STOPPED and unreleased(rig.service) == ()
    assert rig.events.count("publisher.stop") == 1 and rig.events.count("encoder.stop") == 1
    rig.assert_no_resources()


def test_an_interruption_during_start_cleanup_keeps_what_it_could_not_stop() -> None:
    rig = Rig()
    rig.receiver.start_error = RuntimeError("receiver unavailable")
    rig.publisher.stop_error = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        rig.service.start(REQUEST)

    assert rig.events[-2:] == ["publisher.stop", "encoder.stop"]
    assert state(rig.service) is SessionState.FAILED
    assert unreleased(rig.service) == ("publisher",)
    rig.publisher.stop_error = None
    rig.service.close()
    rig.assert_no_resources()
    assert unreleased(rig.service) == ()


def test_a_failure_and_an_interruption_are_both_kept() -> None:
    rig = Rig()
    rig.service.start(REQUEST)
    rig.receiver.stop_error = OSError("receiver")
    rig.encoder.stop_error = SystemExit(3)

    with pytest.raises(SystemExit):
        rig.service.close()

    message = rig.service.last_error or ""
    assert "receiver cleanup failed: OSError" in message
    assert "encoder cleanup interrupted: SystemExit" in message
    assert unreleased(rig.service) == ("receiver", "encoder")
    assert state(rig.service) is SessionState.FAILED


def cancelled_start(
    stop_error: BaseException,
) -> tuple[StreamingSessionService, FakeEncoder, list[BaseException]]:
    """A start cancelled by a close that could not wait, whose encoder stop then fails."""
    events: list[str] = []
    encoder, publisher, receiver = FakeEncoder(events), FakePublisher(events), FakeReceiver(events)
    gate = threading.Event()
    encoder.gate = gate
    encoder.stop_error = stop_error
    service = StreamingSessionService(encoder, publisher, receiver, close_timeout=0.01)
    failures: list[BaseException] = []

    def start() -> None:
        try:
            service.start(REQUEST)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=start)
    worker.start()
    assert encoder.entered.wait(5.0)
    service.close()
    gate.set()
    worker.join(5.0)
    return service, encoder, failures


def test_a_cancelled_start_whose_cleanup_fails_is_failed_not_starting() -> None:
    service, encoder, failures = cancelled_start(OSError("encoder stuck"))

    assert len(failures) == 1 and isinstance(failures[0], StreamStartError)
    assert state(service) is SessionState.FAILED and unreleased(service) == ("encoder",)
    assert "start cancelled by close(); encoder cleanup failed: OSError" in (
        service.last_error or ""
    )
    encoder.stop_error = None
    service.close()  # the retry releases it once
    assert unreleased(service) == () and encoder.active == set()


def test_an_interruption_while_cancelling_a_start_is_re_raised_after_recording() -> None:
    service, _, failures = cancelled_start(KeyboardInterrupt())

    assert len(failures) == 1 and isinstance(failures[0], KeyboardInterrupt)
    assert state(service) is SessionState.FAILED and unreleased(service) == ("encoder",)


@pytest.mark.parametrize("value", [0, -1, math.inf, math.nan, True])
def test_an_invalid_close_timeout_is_refused(value: float) -> None:
    rig = Rig()
    with pytest.raises(InvalidStreamConfiguration, match="close_timeout"):
        StreamingSessionService(rig.encoder, rig.publisher, rig.receiver, close_timeout=value)


def test_closed_reports_the_definitive_shutdown() -> None:
    rig = Rig()
    assert rig.service.closed is False
    rig.service.close()
    assert rig.service.closed is True
