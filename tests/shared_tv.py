"""One simulated TV shared by several independent controllers.

`SharedTv` is the TV: its real state, a lock, and the log of every command it actually
received. Each controller (the desktop window, the Android app) talks to it through its own
`TvLink`, a `CastTransport` with its own discovery cache, exactly as two processes would each
own a `ControlService` and a PyChromecast transport. Nothing is shared between controllers
except the TV itself: no controller can see another's commands, only their effect on the TV.

Faults are per link and model what a real network can do:
- `lose_answer_after_delivery`: the command reaches the TV, then the answer is lost and the
  link raises `OperationTimeoutError` (delivery ambiguous for the caller);
- `fail_after_delivery`: the same, raising `DeviceUnavailableError`;
- `before_read`: callables run, one per status read, just before the TV is read: the other
  controller acting while this one is confirming its command.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from control_tv.domain import (
    ConnectionState,
    Device,
    DeviceId,
    DeviceNotFoundError,
    DeviceStatus,
    DeviceUnavailableError,
    MediaRequest,
    MediaStatus,
    OperationTimeoutError,
    PlaybackState,
    ReceiverStatus,
)
from fakes import DEVICE_ID, OBSERVED_AT, TvState


@dataclass(frozen=True)
class Delivery:
    """A command the TV received: who sent it, what, and with which arguments."""

    controller: str
    command: str
    args: tuple[object, ...]


class SharedTv:
    """The one TV both controllers reach; every access holds its lock."""

    def __init__(self, state: TvState | None = None, device_id: DeviceId = DEVICE_ID) -> None:
        self.device_id = device_id
        self.state = state or TvState(media_session_id=1, current_item_id=1)
        self.deliveries: list[Delivery] = []
        self.lock = threading.RLock()

    def link(self, controller: str) -> TvLink:
        return TvLink(self, controller)

    def received(self, controller: str | None = None) -> list[str]:
        """Commands the TV received, optionally from one controller only."""
        with self.lock:
            return [d.command for d in self.deliveries if controller in (None, d.controller)]

    def deliver(
        self, controller: str, command: str, args: tuple[object, ...], effect: Callable[[], None]
    ) -> None:
        with self.lock:
            self.deliveries.append(Delivery(controller, command, args))
            effect()

    def snapshot(self) -> DeviceStatus:
        with self.lock:
            tv = self.state
            if tv.connection is not ConnectionState.CONNECTED:
                return DeviceStatus(
                    device_id=self.device_id, connection=tv.connection, observed_at=OBSERVED_AT
                )
            media = (
                MediaStatus(
                    playback_state=tv.playback,
                    content_id=tv.content_id,
                    media_session_id=tv.media_session_id,
                    current_item_id=tv.current_item_id,
                    position_seconds=tv.position,
                    supports_seek=tv.supports_seek,
                )
                if tv.has_media
                else None
            )
            return DeviceStatus(
                device_id=self.device_id,
                connection=tv.connection,
                observed_at=OBSERVED_AT,
                receiver=ReceiverStatus(volume_level=tv.volume, muted=tv.muted),
                media=media,
            )

    def new_session(self, url: str) -> None:
        """A LOAD: a new media session (and item) playing `url` from the start."""
        tv = self.state
        tv.content_id = url
        tv.media_session_id = (tv.media_session_id or 0) + 1
        tv.current_item_id = (tv.current_item_id or 0) + 1
        tv.playback = PlaybackState.PLAYING
        tv.position = 0.0
        tv.has_media = True


@dataclass
class TvLink:
    """One controller's `CastTransport` to the shared TV, with its own discovery cache."""

    tv: SharedTv
    controller: str
    lose_answer_after_delivery: bool = False
    fail_after_delivery: bool = False
    before_read: list[Callable[[], object]] = field(default_factory=list)
    status_reads: int = 0
    _known: set[DeviceId] = field(default_factory=set)

    def discover(self, *, timeout: float) -> Sequence[Device]:
        self._known.add(self.tv.device_id)
        return [Device(id=self.tv.device_id, friendly_name="TV", host="192.168.1.20", port=8009)]

    def get_status(self, device_id: DeviceId, *, timeout: float) -> DeviceStatus:
        self._require_known(device_id)
        self.status_reads += 1
        if self.before_read:
            self.before_read.pop(0)()
        return self.tv.snapshot()

    def load_media(self, device_id: DeviceId, request: MediaRequest) -> None:
        self._send(
            device_id, "load_media", (request.url,), lambda: self.tv.new_session(request.url)
        )

    def play(self, device_id: DeviceId) -> None:
        self._send(device_id, "play", (), self._playback(PlaybackState.PLAYING))

    def pause(self, device_id: DeviceId) -> None:
        self._send(device_id, "pause", (), self._playback(PlaybackState.PAUSED))

    def stop(self, device_id: DeviceId) -> None:
        self._send(device_id, "stop", (), self._playback(PlaybackState.IDLE))

    def seek(self, device_id: DeviceId, position_seconds: float) -> None:
        self._send(device_id, "seek", (position_seconds,), self._set("position", position_seconds))

    def set_volume(self, device_id: DeviceId, level: float) -> None:
        self._send(device_id, "set_volume", (level,), self._set("volume", level))

    def set_muted(self, device_id: DeviceId, muted: bool) -> None:
        self._send(device_id, "set_muted", (muted,), self._set("muted", muted))

    def _playback(self, state: PlaybackState) -> Callable[[], None]:
        return self._set("playback", state)

    def _set(self, attribute: str, value: object) -> Callable[[], None]:
        return lambda: setattr(self.tv.state, attribute, value)

    def _require_known(self, device_id: DeviceId) -> None:
        # Each controller has its own cache: it must discover the TV itself first.
        if device_id not in self._known:
            raise DeviceNotFoundError(
                f"device {device_id} has not been discovered", device_id=device_id
            )

    def _send(
        self,
        device_id: DeviceId,
        command: str,
        args: tuple[object, ...],
        effect: Callable[[], None],
    ) -> None:
        self._require_known(device_id)
        self.tv.deliver(self.controller, command, args, effect)
        if self.lose_answer_after_delivery:
            raise OperationTimeoutError(f"no answer to {command}", device_id=device_id)
        if self.fail_after_delivery:
            raise DeviceUnavailableError(f"lost contact after {command}", device_id=device_id)
