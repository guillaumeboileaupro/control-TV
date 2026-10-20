"""D3/D4: the window's screen stream controller and its bridge methods, on fakes only (fake FFmpeg,
fake xrandr, fake Cast transport, the real publisher on loopback)."""

from __future__ import annotations

import io
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from control_tv import bridge
from control_tv.json_types import JsonObject, JsonValue, as_object
from control_tv.ports import TvControl
from control_tv.screen_streaming import (
    InvalidStreamConfiguration,
    StreamCleanupError,
    d1,
)
from control_tv.screen_streaming.controller import (
    ScreenRequest,
    ScreenStreamController,
    StreamState,
)
from control_tv.screen_streaming.ports import StreamReceiver
from control_tv.screen_streaming.virtual_display import Position, XrandrVirtualMonitor
from control_tv.screen_streaming.x11_capture import X11Capture
from control_tv.service import ControlService
from fakes import DEVICE_ID, FakeClock, FakeTransport
from live_encoder_fakes import FakeRunner, encoder
from x11_fakes import X11, FakeXrandr

DUPLICATE = ScreenRequest(str(DEVICE_ID), d1.Mode.DUPLICATE, "eDP-1", 1280, 720, 30, True)


@pytest.fixture(autouse=True)
def no_stream_left() -> Iterator[None]:
    yield
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        thread.name in ("control-tv-screen", "control-tv-hls") for thread in threading.enumerate()
    ):
        time.sleep(0.01)
    left = [t.name for t in threading.enumerate() if t.name.startswith("control-tv-")]
    assert left == []


class Rig:
    def __init__(self, tmp_path: Path, **transport: object) -> None:
        clock = FakeClock()
        self.transport = FakeTransport(clock=clock, **transport)  # type: ignore[arg-type]
        self.control = ControlService(self.transport, clock=clock.monotonic, sleep=clock.sleep)
        self.xrandr = FakeXrandr()
        self.runner = FakeRunner()
        self.tmp_path = tmp_path
        self.sessions: list[d1.D1Session] = []

    def connect(self, device_id: str) -> tuple[TvControl, str]:
        assert device_id == str(DEVICE_ID)
        return self.control, "127.0.0.1"

    def capture(self) -> X11Capture:
        return X11Capture(X11, self.xrandr)

    def build(
        self,
        *,
        seconds: int,
        bind_address: str,
        receiver: StreamReceiver,
        device_id: str,
        picture: d1.Picture | None = None,
        capture: X11Capture | None = None,
    ) -> d1.D1Session:
        live = encoder(self.tmp_path, self.runner)
        assert capture is not None
        live._capture_region = capture.region
        session = d1.assemble(
            seconds=seconds,
            bind_address=bind_address,
            receiver=receiver,
            device_id=device_id,
            picture=picture,
            encoder=live,
            capture=capture,
            virtual=XrandrVirtualMonitor(X11, self.xrandr),
        )
        self.sessions.append(session)
        return session

    def controller(self, **options: object) -> ScreenStreamController:
        return ScreenStreamController(
            connect=self.connect,
            capture=self.capture,
            build=self.build,
            extension=lambda: XrandrVirtualMonitor(X11, self.xrandr),
            sleep=lambda seconds: time.sleep(0.01),
            **options,  # type: ignore[arg-type]
        )


def wait_for(controller: ScreenStreamController, *states: StreamState) -> StreamState:
    deadline = time.monotonic() + 10
    while controller.status().state not in states and time.monotonic() < deadline:
        time.sleep(0.01)
    return controller.status().state


def test_duplicate_runs_until_stopped_then_everything_is_released(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    controller = rig.controller()

    started = controller.start(DUPLICATE)
    assert started.state is StreamState.STARTING and started.screen == "eDP-1"
    assert wait_for(controller, StreamState.RUNNING) is StreamState.RUNNING
    uri = rig.transport.tv.content_id
    assert uri is not None and uri.startswith("http://127.0.0.1:")

    assert controller.stop().state is StreamState.STOPPING
    assert wait_for(controller, StreamState.IDLE) is StreamState.IDLE
    assert rig.transport.attempted() == ["load_media", "stop", "close_app"]
    session = rig.sessions[0]
    assert session.publisher.unreleased == () and rig.runner.group.releases == 1
    controller.stop()  # idempotent
    assert controller.status().state is StreamState.IDLE


def test_extend_creates_and_removes_the_virtual_monitor(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    controller = rig.controller()
    request = ScreenRequest(
        str(DEVICE_ID), d1.Mode.EXTEND, None, 1920, 1080, 30, True, 50, Position.LEFT
    )

    controller.start(request)
    assert wait_for(controller, StreamState.RUNNING) is StreamState.RUNNING
    assert rig.xrandr.enabled is not None and controller.status().screen is None
    assert ("--output", "HDMI-2", "--mode", "control-tv-1920x1080-50", "--left-of", "eDP-1") in (
        rig.xrandr.calls
    )
    controller.close()

    assert rig.xrandr.enabled is None and controller.status().state is StreamState.IDLE


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"consent": False}, "explicit consent"),
        ({"device_id": " "}, "choose the TV"),
        ({"mode": d1.Mode.SYNTHETIC}, "duplicate or extend"),
        ({"screen": None}, "choose the screen"),
        ({"width": 1024, "height": 768}, "1280x720 or 1920x1080"),
        ({"fps": 60}, "25 or 30"),
        ({"mode": d1.Mode.EXTEND, "refresh": 30}, "60 or 50 Hz"),
        ({"position": "left"}, "left, right, above or below"),
    ],
)
def test_an_invalid_request_touches_nothing(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    from dataclasses import replace

    rig = Rig(tmp_path)
    controller = rig.controller()
    with pytest.raises(InvalidStreamConfiguration, match=message):
        controller.start(replace(DUPLICATE, **changes))  # type: ignore[arg-type]
    assert controller.status().state is StreamState.IDLE and rig.sessions == []


def test_one_stream_at_a_time(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    controller = rig.controller()
    controller.start(DUPLICATE)
    with pytest.raises(InvalidStreamConfiguration, match="already running"):
        controller.start(DUPLICATE)
    controller.close()


def test_a_tv_that_cannot_be_reached_fails_visibly_and_can_be_cleared(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def unreachable(device_id: str) -> tuple[TvControl, str]:
        raise InvalidStreamConfiguration("that TV was not found on the home network")

    controller = ScreenStreamController(connect=unreachable, capture=rig.capture, build=rig.build)
    controller.start(DUPLICATE)
    assert wait_for(controller, StreamState.FAILED) is StreamState.FAILED
    assert controller.status().error == "that TV was not found on the home network"
    assert controller.stop().state is StreamState.IDLE  # the failure is acknowledged


def test_a_receiver_failure_releases_everything_and_is_reported(tmp_path: Path) -> None:
    from control_tv.domain import DeviceUnavailableError

    rig = Rig(tmp_path, fail_commands_with=DeviceUnavailableError("no route"))
    controller = rig.controller()
    controller.start(DUPLICATE)

    assert wait_for(controller, StreamState.FAILED) is StreamState.FAILED
    assert "could not load the stream" in (controller.status().error or "")
    assert rig.sessions[0].publisher.unreleased == () and rig.runner.group.releases == 1


def test_a_stream_that_ends_by_itself_is_reported(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    controller = rig.controller()
    controller.start(DUPLICATE)
    assert wait_for(controller, StreamState.RUNNING) is StreamState.RUNNING
    rig.runner.group.ended = True  # FFmpeg exited

    assert wait_for(controller, StreamState.FAILED) is StreamState.FAILED
    assert controller.status().error == "the stream ended by itself"


def test_the_session_bound_ends_the_stream(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    now = [0.0]

    def tick(seconds: float) -> None:
        now[0] += 10

    controller = ScreenStreamController(
        connect=rig.connect,
        capture=rig.capture,
        build=rig.build,
        seconds=30,
        clock=lambda: now[0],
        sleep=tick,
    )
    controller.start(DUPLICATE)
    assert wait_for(controller, StreamState.IDLE) is StreamState.IDLE
    assert rig.transport.attempted() == ["load_media", "stop", "close_app"]


def test_a_stop_before_the_session_started_starts_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    started: list[str] = []
    original = rig.connect

    def slow(device_id: str) -> tuple[TvControl, str]:
        controller.stop()  # the person clicks Arrêter while the TV is being found
        started.append(device_id)
        return original(device_id)

    rig.connect = slow  # type: ignore[method-assign]
    controller = rig.controller()
    controller.start(DUPLICATE)
    assert wait_for(controller, StreamState.IDLE) is StreamState.IDLE
    assert started and rig.transport.attempted() == []


def test_an_unexpected_error_or_an_unclean_stop_is_reported(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def broken(**options: object) -> d1.D1Session:
        raise RuntimeError("bug")

    controller = ScreenStreamController(
        connect=rig.connect,
        capture=rig.capture,
        build=broken,
    )
    controller.start(DUPLICATE)
    assert wait_for(controller, StreamState.FAILED) is StreamState.FAILED
    assert controller.status().error == "the screen stream failed: RuntimeError"

    rig2 = Rig(tmp_path / "second")
    (tmp_path / "second").mkdir()
    unclean = rig2.controller()
    unclean.start(DUPLICATE)
    assert wait_for(unclean, StreamState.RUNNING) is StreamState.RUNNING
    session = rig2.sessions[0]

    real_stop = session.stop

    def fails() -> None:
        real_stop()  # everything is released, then one step reports a failure
        raise StreamCleanupError("stuck")

    session.stop = fails  # type: ignore[method-assign]
    unclean.stop()
    assert wait_for(unclean, StreamState.FAILED) is StreamState.FAILED
    assert unclean.status().error == "the screen stream did not stop cleanly: stuck"


# Bridge methods ---------------------------------------------------------------------------


@pytest.fixture
def screen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    rig = Rig(tmp_path)
    monkeypatch.setattr(bridge, "_screen_controller", rig.controller())
    return rig


def call(method: str, **params: JsonValue) -> JsonObject:
    return bridge.dispatch(None, {"id": 1, "method": method, "params": params})  # type: ignore[arg-type]


def answered(method: str, **params: JsonValue) -> JsonObject:
    answer = call(method, **params)
    assert answer["ok"] is True, answer
    return as_object(answer["result"])


def refused(method: str, **params: JsonValue) -> JsonObject:
    answer = call(method, **params)
    assert answer["ok"] is False, answer
    return as_object(answer["error"])


def stream(method: str, **params: JsonValue) -> JsonObject:
    return as_object(answered(method, **params)["stream"])


def test_the_bridge_lists_screens_and_runs_a_stream(screen: Rig) -> None:
    assert answered("list_screens") == {
        "screens": [
            {"name": "eDP-1", "width": 1920, "height": 1080, "primary": True},
            {"name": "HDMI-1", "width": 2561, "height": 1441, "primary": False},
        ],
        "extension": {
            "output": "HDMI-2",
            "sizes": [[1280, 720], [1920, 1080]],
            "refreshRates": [60, 50],
            "positions": ["left", "right", "above", "below"],
        },
    }
    started = stream(
        "start_screen_stream",
        deviceId=str(DEVICE_ID),
        mode="duplicate",
        screen="eDP-1",
        width=1280,
        height=720,
        fps=30,
        consent=True,
    )
    assert started["state"] == "starting"
    controller = bridge._screen()
    assert wait_for(controller, StreamState.RUNNING) is StreamState.RUNNING
    status = stream("get_screen_stream")
    assert status == {
        "state": "running",
        "mode": "duplicate",
        "screen": "eDP-1",
        "width": 1280,
        "height": 720,
        "fps": 30,
        "error": None,
    }
    assert stream("stop_screen_stream")["state"] == "stopping"
    assert wait_for(controller, StreamState.IDLE) is StreamState.IDLE


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"mode": "mirror"}, "mode must be duplicate or extend"),
        ({"screen": 3}, "screen must be a string"),
        ({"consent": "yes"}, "consent must be true or false"),
        ({"width": True}, "width must be an integer"),
        ({"consent": False}, "explicit consent"),
        ({"position": "behind"}, "position must be left, right, above or below"),
        ({"refresh": "60"}, "refresh must be an integer"),
        ({"mode": "extend", "refresh": 30}, "60 or 50 Hz"),
    ],
)
def test_the_bridge_refuses_a_malformed_or_unconsented_request(
    screen: Rig, params: dict[str, JsonValue], message: str
) -> None:
    request: dict[str, JsonValue] = {
        "deviceId": str(DEVICE_ID),
        "mode": "duplicate",
        "screen": "eDP-1",
        "width": 1280,
        "height": 720,
        "fps": 30,
        "consent": True,
    }
    request.update(params)
    error = refused("start_screen_stream", **request)
    assert error["code"] == "invalid_argument"
    assert message in str(error["message"])
    assert screen.sessions == []


def test_a_session_without_x11_is_reported_as_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wayland = ScreenStreamController(capture=lambda: X11Capture({"XDG_SESSION_TYPE": "wayland"}))
    monkeypatch.setattr(bridge, "_screen_controller", wayland)
    assert refused("list_screens")["code"] == "invalid_argument"

    def unreadable() -> X11Capture:
        return X11Capture(X11, FakeXrandr(fail="--listmonitors"))

    monkeypatch.setattr(bridge, "_screen_controller", ScreenStreamController(capture=unreadable))
    assert refused("list_screens")["code"] == "unsupported_operation"


def test_the_bridge_creates_one_controller_and_closes_it_when_it_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "_screen_controller", None)
    bridge.close_screen_stream()  # nothing was ever started: nothing to close
    first = bridge._screen()
    assert bridge._screen() is first
    closed: list[bool] = []
    monkeypatch.setattr(first, "close", lambda: closed.append(True))

    bridge.run(None, stdin=io.StringIO("\n"), stdout=io.StringIO())  # type: ignore[arg-type]

    assert closed == [True]


def test_stop_before_any_start_is_harmless() -> None:
    controller = ScreenStreamController()
    controller.close()
    assert controller.status().state is StreamState.IDLE
