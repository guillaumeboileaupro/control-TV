"""The D1 Cast HLS receiver through the real ControlService and the fake transport: no device."""

from __future__ import annotations

import dataclasses
import math
import threading

import pytest

from control_tv.domain import (
    CommandResult,
    Confirmation,
    DeviceId,
    DeviceStatus,
    DeviceUnavailableError,
    MediaRequest,
)
from control_tv.screen_streaming import (
    PublishedStream,
    ReceiverSession,
    ReceiverTarget,
    StreamCleanupError,
    StreamStartError,
)
from control_tv.screen_streaming.cast_receiver import HLS_CONTENT_TYPE, CastHlsReceiver
from control_tv.service import ControlService
from fakes import DEVICE_ID, MOVIE_URL, FakeClock, FakeTransport

STREAM_URL = "http://192.168.1.20:41234/token-0123456789abcdefghij/index.m3u8"
PUBLISHED = PublishedStream(STREAM_URL)
TARGET = ReceiverTarget(str(DEVICE_ID))


def rig(**options: object) -> tuple[CastHlsReceiver, FakeTransport]:
    clock = FakeClock()
    transport = FakeTransport(clock=clock, **options)  # type: ignore[arg-type]
    service = ControlService(transport, clock=clock.monotonic, sleep=clock.sleep)
    return CastHlsReceiver(service), transport


def commands(transport: FakeTransport) -> list[str]:
    return [name for name in transport.attempted()]


def held(receiver: CastHlsReceiver) -> tuple[str, ...]:
    return receiver.unreleased


def test_start_loads_the_publication_as_hls_and_returns_a_confirmed_session() -> None:
    receiver, transport = rig()

    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)

    loaded = [call for call in transport.calls if call[0] == "load_media"]
    assert len(loaded) == 1
    request = loaded[0][1][1]
    assert isinstance(request, MediaRequest)
    assert request.url == STREAM_URL and request.content_type == HLS_CONTENT_TYPE
    assert transport.tv.content_id == STREAM_URL and held(receiver) == (session.handle,)


def test_stop_stops_this_stream_once_and_is_idempotent() -> None:
    receiver, transport = rig()
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)

    receiver.stop(session)
    receiver.stop(session)  # already released: nothing more is sent

    assert commands(transport) == ["load_media", "stop", "close_app"]
    assert held(receiver) == ()
    with pytest.raises(StreamCleanupError, match="owns no such session"):
        receiver.stop(ReceiverSession("cast-hls-never"))


def test_content_started_since_is_never_stopped() -> None:
    receiver, transport = rig()
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)
    transport.tv.content_id = MOVIE_URL  # the person started something else on the TV

    receiver.stop(session)

    assert commands(transport) == ["load_media"] and held(receiver) == ()


def test_no_media_but_same_receiver_session_closes_the_application() -> None:
    receiver, transport = rig()
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)
    transport.tv.has_media = False

    receiver.stop(session)
    assert commands(transport) == ["load_media", "close_app"] and held(receiver) == ()


def test_no_media_from_a_replaced_receiver_session_is_never_closed() -> None:
    receiver, transport = rig()
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)
    transport.tv.has_media = False
    transport.tv.app_session_id = "receiver-session-replacement"

    receiver.stop(session)

    assert commands(transport) == ["load_media"] and held(receiver) == ()


def test_a_receiver_application_close_failure_is_retained_for_retry() -> None:
    receiver, transport = rig()
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)
    transport.tv.has_media = False
    transport.fail_commands_with = DeviceUnavailableError("link lost")

    with pytest.raises(StreamCleanupError, match="application could not be closed"):
        receiver.stop(session)
    assert held(receiver) == (session.handle,)

    transport.fail_commands_with = None
    receiver.stop(session)
    assert commands(transport) == ["load_media", "close_app", "close_app"]
    assert held(receiver) == ()


def test_an_unconfirmed_receiver_application_close_is_retained_for_retry() -> None:
    receiver, transport = rig()
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)
    transport.tv.has_media = False
    transport.ignore_commands = True

    with pytest.raises(StreamCleanupError, match="application close was not confirmed"):
        receiver.stop(session)

    assert commands(transport) == ["load_media", "close_app"]
    assert held(receiver) == (session.handle,)


def test_repeated_sessions_close_only_their_captured_receiver_session() -> None:
    receiver, transport = rig()
    captured: list[str] = []

    for _ in range(3):
        session = receiver.start(TARGET, PUBLISHED, timeout=10.0)
        assert transport.tv.app_session_id is not None
        captured.append(transport.tv.app_session_id)
        receiver.stop(session)

    close_targets = [str(args[1]) for name, args in transport.calls if name == "close_app"]
    assert close_targets == captured
    assert len(set(close_targets)) == 3 and held(receiver) == ()


def test_a_confirmed_load_without_a_receiver_session_is_cleaned_and_refused() -> None:
    receiver, transport = rig()
    transport.status_effects.append(lambda: setattr(transport.tv, "app_session_id", None))

    with pytest.raises(StreamStartError, match="did not report its application session"):
        receiver.start(TARGET, PUBLISHED, timeout=10.0)

    assert commands(transport) == ["load_media", "stop"] and held(receiver) == ()


def test_a_load_that_cannot_be_sent_is_a_start_error_and_owns_nothing() -> None:
    receiver, transport = rig(fail_commands_with=DeviceUnavailableError("no route"))

    with pytest.raises(StreamStartError, match=r"could not load the stream \(device_unavailable\)"):
        receiver.start(TARGET, PUBLISHED, timeout=10.0)
    assert held(receiver) == () and commands(transport) == ["load_media"]


def test_an_unconfirmed_load_is_a_start_error_and_stops_nothing_else() -> None:
    receiver, transport = rig(ignore_commands=True)  # the TV keeps playing the film

    with pytest.raises(StreamStartError, match=r"did not confirm the stream \(unconfirmed\)"):
        receiver.start(TARGET, PUBLISHED, timeout=10.0)
    assert commands(transport) == ["load_media"] and transport.tv.content_id == MOVIE_URL
    assert held(receiver) == ()


class LateConfirmation(ControlService):
    """The receiver plays the stream, but the confirmation came too late to count;
    `then_fail` makes every later command fail (the guarded stop, here)."""

    def __init__(self, transport: FakeTransport, clock: FakeClock, *, then_fail: bool) -> None:
        super().__init__(transport, clock=clock.monotonic, sleep=clock.sleep)
        self.fake = transport
        self.then_fail = then_fail

    def load_media(self, device_id: DeviceId, request: MediaRequest) -> CommandResult:
        result = super().load_media(device_id, request)
        if self.then_fail:
            self.fake.fail_commands_with = DeviceUnavailableError("gone")
        return dataclasses.replace(result, confirmation=Confirmation.UNCONFIRMED, observed=None)


def test_an_unconfirmed_load_that_is_playing_anyway_is_stopped() -> None:
    for then_fail in (False, True):
        clock = FakeClock()
        transport = FakeTransport(clock=clock)
        receiver = CastHlsReceiver(LateConfirmation(transport, clock, then_fail=then_fail))

        # The guarded stop's own failure is not raised: the start error is.
        with pytest.raises(StreamStartError, match="did not confirm"):
            receiver.start(TARGET, PUBLISHED, timeout=10.0)
        assert commands(transport) == ["load_media", "stop"] and held(receiver) == ()


def test_a_status_that_cannot_be_read_keeps_the_session_for_a_retry() -> None:
    receiver, transport = rig()
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)
    transport.status_errors.extend([DeviceUnavailableError("link lost")] * 10)

    with pytest.raises(StreamCleanupError, match=r"could not be read \(device_unavailable\)"):
        receiver.stop(session)
    assert held(receiver) == (session.handle,)

    transport.status_errors.clear()
    receiver.stop(session)
    assert held(receiver) == () and commands(transport) == ["load_media", "stop", "close_app"]


def test_a_stop_that_fails_or_is_not_confirmed_keeps_the_session() -> None:
    receiver, transport = rig()
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)

    transport.fail_commands_with = DeviceUnavailableError("no route")
    with pytest.raises(StreamCleanupError, match=r"could not be stopped \(device_unavailable\)"):
        receiver.stop(session)
    transport.fail_commands_with = None
    transport.ignore_commands = True
    with pytest.raises(StreamCleanupError, match=r"not confirmed \(unconfirmed\)"):
        receiver.stop(session)
    assert held(receiver) == (session.handle,)

    transport.ignore_commands = False
    receiver.stop(session)
    assert held(receiver) == ()


@pytest.mark.parametrize("timeout", [0, -1.0, math.nan, math.inf, True])
def test_an_invalid_timeout_is_refused_before_any_command(timeout: float) -> None:
    receiver, transport = rig()
    with pytest.raises(StreamStartError, match="timeout"):
        receiver.start(TARGET, PUBLISHED, timeout=timeout)
    assert transport.calls == []


def test_a_timeout_that_is_not_a_number_is_refused() -> None:
    receiver, _ = rig()
    with pytest.raises(StreamStartError, match="number of seconds"):
        receiver.start(TARGET, PUBLISHED, timeout="10")  # type: ignore[arg-type]


def test_a_non_http_publication_is_refused_before_any_command() -> None:
    receiver, transport = rig()
    with pytest.raises(StreamStartError, match="HTTP"):
        receiver.start(TARGET, PublishedStream("cast-streaming://receiver/session"), timeout=5.0)
    assert transport.calls == []


def test_concurrent_stops_send_one_stop() -> None:
    receiver, transport = rig()
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)
    errors: list[BaseException] = []

    def stop() -> None:
        try:
            receiver.stop(session)
        except BaseException as error:  # pragma: no cover - reported below
            errors.append(error)

    workers = [threading.Thread(target=stop) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    assert errors == [] and commands(transport).count("stop") == 1
    assert commands(transport).count("close_app") == 1 and held(receiver) == ()


class BlockingStatus(ControlService):
    """The first status read after `armed` is set waits until `release` is set."""

    def __init__(self, transport: FakeTransport, clock: FakeClock) -> None:
        super().__init__(transport, clock=clock.monotonic, sleep=clock.sleep)
        self.armed = threading.Event()
        self.reading = threading.Event()
        self.release = threading.Event()

    def get_status(self, device_id: DeviceId) -> DeviceStatus:
        if self.armed.is_set() and not self.reading.is_set():
            self.reading.set()
            assert self.release.wait(5)
        return super().get_status(device_id)


def test_a_stop_waiting_behind_another_confirms_its_release() -> None:
    clock = FakeClock()
    transport = FakeTransport(clock=clock)
    control = BlockingStatus(transport, clock)
    receiver = CastHlsReceiver(control)
    session = receiver.start(TARGET, PUBLISHED, timeout=10.0)
    control.armed.set()

    first = threading.Thread(target=receiver.stop, args=(session,))
    first.start()
    assert control.reading.wait(5)
    second = threading.Thread(target=receiver.stop, args=(session,))
    second.start()  # waits for the first stop of the same session, then confirms
    control.release.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive() and not second.is_alive()
    assert commands(transport).count("stop") == 1
    assert commands(transport).count("close_app") == 1 and held(receiver) == ()
