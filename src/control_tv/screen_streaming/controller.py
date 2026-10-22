"""D3/D4: the window's screen stream — one session at a time, started and stopped on request.

The bridge owns one controller. `start` validates the request (the owner's explicit consent is
required: the desktop becomes visible on the TV, and "Étendre" changes the display), then a
background thread assembles D1 (`d1.assemble`), starts it and waits until it is stopped, ends by
itself or reaches the session bound (one hour); everything is then stopped. The stream uses its
own control layer (`d1.tv_and_route`: its own discovery, the LAN address this computer reaches
the TV from), so it never shares the Cast transport with the remote's commands. `close` stops the
session when the bridge exits (the application quits). No address, URL or token is reported.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from control_tv.domain import DeviceId
from control_tv.ports import TvControl
from control_tv.screen_streaming.cast_receiver import CastHlsReceiver
from control_tv.screen_streaming.d1 import (
    MAX_D1_SECONDS,
    D1Session,
    Mode,
    Picture,
    assemble,
    tv_and_route,
)
from control_tv.screen_streaming.model import (
    InvalidStreamConfiguration,
    ScreenStreamError,
    StreamCleanupError,
)
from control_tv.screen_streaming.ports import StreamReceiver
from control_tv.screen_streaming.virtual_display import (
    REFRESH_RATES,
    Position,
    XrandrVirtualMonitor,
)
from control_tv.screen_streaming.x11_capture import X11Capture, X11Output

SIZES = ((1280, 720), (1920, 1080))
# 60 is not offered: the TV's Cast receiver did not keep up with 1080p60 (hardware, 2026-10-20).
RATES = (25, 30)
_POLL_SECONDS = 0.5
_CLOSE_SECONDS = 20.0
WEBRTC_RECEIVER_PACKAGE = "io.github.guillaumeboileaupro.controltv.receiver"


class StreamState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True)
class ScreenRequest:
    """What the window asks for; checked by `checked()` before anything is touched."""

    device_id: str
    mode: Mode
    screen: str | None
    width: int
    height: int
    fps: int
    consent: bool
    refresh: int = 60  # the extended screen (EXTEND)
    position: Position = Position.RIGHT  # the extended screen (EXTEND)
    x: int | None = None
    y: int | None = None

    def checked(self) -> ScreenRequest:
        if self.consent is not True:
            raise InvalidStreamConfiguration(
                "streaming the screen needs the owner's explicit consent"
            )
        if not isinstance(self.device_id, str) or not self.device_id.strip():
            raise InvalidStreamConfiguration("choose the TV to stream to")
        if self.mode not in (Mode.DUPLICATE, Mode.EXTEND):
            raise InvalidStreamConfiguration("the mode is duplicate or extend")
        if self.mode is Mode.DUPLICATE and not self.screen:
            raise InvalidStreamConfiguration("choose the screen to duplicate")
        if (self.width, self.height) not in SIZES:
            raise InvalidStreamConfiguration("the resolution is 1280x720 or 1920x1080")
        if self.fps not in RATES:
            raise InvalidStreamConfiguration("the frame rate is 25 or 30")
        if self.mode is Mode.EXTEND and self.refresh not in REFRESH_RATES:
            raise InvalidStreamConfiguration("the extended screen refreshes at 60 or 50 Hz")
        if type(self.position) is not Position:
            raise InvalidStreamConfiguration("the extended screen is left, right, above or below")
        if (self.x is None) != (self.y is None) or any(
            isinstance(value, bool) or not isinstance(value, int) or not -32768 <= value <= 32767
            for value in (self.x, self.y)
            if value is not None
        ):
            raise InvalidStreamConfiguration("the extended screen coordinates are invalid")
        return self


@dataclass(frozen=True)
class ScreenStatus:
    state: StreamState
    mode: Mode | None = None
    screen: str | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class WebRtcCaptureLease:
    """Capture geometry owned by this controller until `release_webrtc_capture`.

    The lease contains no receiver credential. The Rust host consumes only the selected local
    route and X11 region; this controller remains responsible for restoring a D2 extension.
    """

    bind_address: str
    display: str
    offset_x: int
    offset_y: int
    source_width: int
    source_height: int


Connect = Callable[[str], tuple[TvControl, str]]


class Assemble(Protocol):
    def __call__(
        self,
        *,
        seconds: int,
        bind_address: str,
        receiver: StreamReceiver,
        device_id: str,
        picture: Picture | None = None,
        capture: X11Capture | None = None,
    ) -> D1Session: ...


class ScreenStreamController:
    def __init__(
        self,
        *,
        connect: Connect = tv_and_route,
        capture: Callable[[], X11Capture] = X11Capture,
        extension: Callable[[], XrandrVirtualMonitor] = XrandrVirtualMonitor,
        build: Assemble = assemble,
        seconds: int = MAX_D1_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._connect = connect
        self._capture = capture
        self._extension = extension
        self._build = build
        self._seconds = seconds
        self._clock = clock
        self._sleep = sleep
        self._status = ScreenStatus(StreamState.IDLE)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._webrtc_lease: WebRtcCaptureLease | None = None
        self._webrtc_extension: XrandrVirtualMonitor | None = None
        self._lock = threading.Lock()

    def screens(self) -> tuple[X11Output, ...]:
        """The outputs that can be duplicated (read only)."""
        return self._capture().outputs()

    def extension_output(self) -> str | None:
        """The free output "Étendre" would use (read only), or None when none is enabled."""
        return self._extension().free_output()

    def status(self) -> ScreenStatus:
        with self._lock:
            return self._status

    def start(self, request: ScreenRequest) -> ScreenStatus:
        checked = request.checked()
        with self._lock:
            if self._status.state in (
                StreamState.STARTING,
                StreamState.RUNNING,
                StreamState.STOPPING,
            ):
                raise InvalidStreamConfiguration(
                    "a screen stream is already running: stop it first"
                )
            self._stop.clear()
            self._status = ScreenStatus(
                StreamState.STARTING,
                checked.mode,
                checked.screen if checked.mode is Mode.DUPLICATE else None,
                checked.width,
                checked.height,
                checked.fps,
            )
            self._thread = threading.Thread(
                target=self._serve, args=(checked,), name="control-tv-screen", daemon=True
            )
            self._thread.start()
            return self._status

    def stop(self) -> ScreenStatus:
        """Ask the session to stop (everything is stopped by its thread); idempotent."""
        with self._lock:
            if self._status.state in (StreamState.STARTING, StreamState.RUNNING):
                self._status = replace(self._status, state=StreamState.STOPPING)
            elif self._status.state is StreamState.FAILED:
                self._status = ScreenStatus(StreamState.IDLE)
            self._stop.set()
            return self._status

    def close(self) -> None:
        """Stop the session and wait for its cleanup (the bridge is exiting)."""
        self.stop()
        thread = self._thread
        if thread is not None:
            thread.join(_CLOSE_SECONDS)
        self.release_webrtc_capture()

    def prepare_webrtc_capture(self, request: ScreenRequest) -> WebRtcCaptureLease:
        """Reserve the existing D1/D2 source for the Rust engine, never a parallel display.

        Receiver installation is rechecked at acquisition time so an explicit WebRTC start
        fails closed even if a prior capability result became stale.
        """
        checked = request.checked()
        with self._lock:
            if self._status.state is not StreamState.IDLE or self._webrtc_lease is not None:
                raise InvalidStreamConfiguration(
                    "a screen stream is already running: stop it first"
                )
            self._status = ScreenStatus(
                StreamState.STARTING,
                checked.mode,
                checked.screen if checked.mode is Mode.DUPLICATE else None,
                checked.width,
                checked.height,
                checked.fps,
            )
        extension: XrandrVirtualMonitor | None = None
        try:
            control, address = self._connect(checked.device_id)
            installed = control.list_tv_apps(DeviceId(checked.device_id))
            if not any(app.package == WEBRTC_RECEIVER_PACKAGE for app in installed):
                raise InvalidStreamConfiguration(
                    "ControlTV Receiver is not installed or accessible on the selected TV"
                )
            capture = self._capture()
            output: X11Output | None
            if checked.mode is Mode.EXTEND:
                extension = self._extension()
                output = extension.create(
                    checked.width,
                    checked.height,
                    consent=checked.consent,
                    refresh=checked.refresh,
                    position=checked.position,
                    x=checked.x,
                    y=checked.y,
                )
            else:
                output = next(
                    (candidate for candidate in capture.outputs() if candidate.name == checked.screen),
                    None,
                )
                if output is None:
                    raise InvalidStreamConfiguration("that screen output is not connected")
            assert output is not None
            lease = WebRtcCaptureLease(
                address,
                capture.display,
                output.x,
                output.y,
                output.width - output.width % 2,
                output.height - output.height % 2,
            )
            with self._lock:
                self._webrtc_lease = lease
                self._webrtc_extension = extension
            return lease
        except BaseException as caught:
            cleanup_error: ScreenStreamError | None = None
            if extension is not None:
                try:
                    extension.remove()
                except ScreenStreamError as cleanup:
                    cleanup_error = cleanup
            with self._lock:
                detail = "WebRTC preparation failed"
                if cleanup_error is not None:
                    detail += f"; the extended screen was not restored: {cleanup_error.message}"
                self._status = ScreenStatus(StreamState.FAILED, error=detail)
            if cleanup_error is not None:
                raise StreamCleanupError(detail) from caught
            raise

    def activate_webrtc_capture(self) -> ScreenStatus:
        with self._lock:
            if self._webrtc_lease is None or self._status.state is not StreamState.STARTING:
                raise InvalidStreamConfiguration("no WebRTC capture is prepared")
            self._status = replace(self._status, state=StreamState.RUNNING)
            return self._status

    def release_webrtc_capture(self, error: str | None = None) -> ScreenStatus:
        with self._lock:
            if self._webrtc_lease is None and self._webrtc_extension is None:
                return self._status
            extension = self._webrtc_extension
            self._webrtc_extension = None
            self._webrtc_lease = None
        cleanup_error: str | None = None
        if extension is not None:
            try:
                extension.remove()
            except ScreenStreamError as caught:
                cleanup_error = f"the extended screen was not restored: {caught.message}"
        with self._lock:
            failure = error or cleanup_error
            self._status = (
                ScreenStatus(StreamState.IDLE)
                if failure is None
                else ScreenStatus(StreamState.FAILED, error=failure)
            )
            return self._status

    def _serve(self, request: ScreenRequest) -> None:
        session: D1Session | None = None
        error: str | None = None
        try:
            control, address = self._connect(request.device_id)
            session = self._build(
                seconds=self._seconds,
                bind_address=address,
                receiver=CastHlsReceiver(control),
                device_id=request.device_id,
                picture=Picture(
                    request.mode,
                    request.screen,
                    request.width,
                    request.height,
                    request.fps,
                    consent=request.consent,
                    refresh=request.refresh,
                    position=request.position,
                    x=request.x,
                    y=request.y,
                ),
                capture=self._capture(),
            )
            if not self._stop.is_set():
                session.start()
                with self._lock:
                    if self._status.state is StreamState.STARTING:
                        self._status = replace(self._status, state=StreamState.RUNNING)
                deadline = self._clock() + self._seconds
                while not self._stop.is_set() and self._clock() < deadline:
                    if session.ended():
                        error = "the stream ended by itself"
                        break
                    self._sleep(_POLL_SECONDS)
        except ScreenStreamError as caught:
            error = caught.message
        except Exception as caught:  # a host thread must report, never die silently
            error = f"the screen stream failed: {type(caught).__name__}"
        finally:
            if session is not None:
                try:
                    session.stop()
                except ScreenStreamError as caught:
                    error = error or f"the screen stream did not stop cleanly: {caught.message}"
            with self._lock:
                if error is None:
                    self._status = ScreenStatus(StreamState.IDLE)
                else:
                    self._status = replace(self._status, state=StreamState.FAILED, error=error)
