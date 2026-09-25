"""PyChromecast implementation of the low-level Cast transport boundary."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import TypeVar

import pychromecast
from pychromecast import Chromecast
from pychromecast.controllers.media import (
    MEDIA_PLAYER_STATE_BUFFERING,
    MEDIA_PLAYER_STATE_IDLE,
    MEDIA_PLAYER_STATE_PAUSED,
    MEDIA_PLAYER_STATE_PLAYING,
    MEDIA_PLAYER_STATE_UNKNOWN,
)
from pychromecast.controllers.media import (
    MediaStatus as PyMediaStatus,
)
from pychromecast.error import ChromecastConnectionError, NotConnected, PyChromecastError
from pychromecast.response_handler import WaitResponse

from control_tv.domain import (
    CommandRejectedError,
    ConnectionState,
    Device,
    DeviceId,
    DeviceKind,
    DeviceNotFoundError,
    DeviceStatus,
    DeviceUnavailableError,
    DiscoveryError,
    InvalidArgumentError,
    MediaRequest,
    MediaStatus,
    OperationTimeoutError,
    PlaybackState,
    ReceiverStatus,
)

Discoverer = Callable[[float], tuple[list[Chromecast], object]]
T = TypeVar("T")

_KIND_MAP = {
    "cast": DeviceKind.CAST,
    "audio": DeviceKind.AUDIO,
    "group": DeviceKind.GROUP,
}
_STATE_MAP = {
    MEDIA_PLAYER_STATE_IDLE: PlaybackState.IDLE,
    MEDIA_PLAYER_STATE_PLAYING: PlaybackState.PLAYING,
    MEDIA_PLAYER_STATE_PAUSED: PlaybackState.PAUSED,
    MEDIA_PLAYER_STATE_BUFFERING: PlaybackState.BUFFERING,
}


def _default_discoverer(timeout: float) -> tuple[list[Chromecast], object]:
    result = pychromecast.get_chromecasts(timeout=timeout)
    if not isinstance(result, tuple):  # blocking=True is the library default
        raise DiscoveryError("PyChromecast unexpectedly started non-blocking discovery")
    devices, browser = result
    return devices, browser


class PyChromecastTransport:
    """Translate PyChromecast devices, state and failures into the shared domain."""

    def __init__(
        self,
        *,
        connection_timeout: float = 10.0,
        request_timeout: float = 10.0,
        recovery_timeout: float = 5.0,
        discoverer: Discoverer = _default_discoverer,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, timeout in (
            ("connection_timeout", connection_timeout),
            ("request_timeout", request_timeout),
            ("recovery_timeout", recovery_timeout),
        ):
            if not math.isfinite(timeout) or timeout <= 0:
                raise InvalidArgumentError(f"{name} must be a finite number > 0: {timeout}")
        self._connection_timeout = connection_timeout
        self._request_timeout = request_timeout
        self._recovery_timeout = recovery_timeout
        self._discoverer = discoverer
        self._now = now
        self._clock = clock
        self._casts: dict[DeviceId, Chromecast] = {}

    def discover(self, *, timeout: float) -> list[Device]:
        browser: object | None = None
        try:
            casts, browser = self._discoverer(timeout)
            discovered = [self._device(cast_device) for cast_device in casts]
            for cast_device in casts:
                device_id = DeviceId(str(cast_device.uuid))
                previous = self._casts.get(device_id)
                if previous is not None and previous is not cast_device:
                    self._disconnect(previous)
                self._casts[device_id] = cast_device
            return discovered
        except DiscoveryError:
            raise
        except (PyChromecastError, OSError) as error:
            raise DiscoveryError(f"Cast discovery failed: {error}") from error
        finally:
            if browser is not None:
                stop = getattr(browser, "stop_discovery", None)
                if callable(stop):
                    stop()

    def get_status(self, device_id: DeviceId, *, timeout: float) -> DeviceStatus:
        """Read fresh status, never spending more than `timeout` seconds in total.

        `timeout` is the caller's entire remaining budget (typically what is left of
        `ControlService`'s `confirm_timeout`), not a per-request default: connecting (with
        at most one bounded same-UUID recovery attempt) and the receiver-status round trip
        together must fit within it, or this raises `OperationTimeoutError` /
        `DeviceUnavailableError` rather than exceed it.
        """
        if not math.isfinite(timeout) or timeout <= 0:
            raise InvalidArgumentError(
                f"status read timeout must be a finite number > 0: {timeout}"
            )
        deadline = self._clock() + timeout
        cast_device = self._ready_bounded(device_id, deadline)
        self._receiver_status_bounded(cast_device, device_id, deadline)
        receiver = cast_device.status
        media = cast_device.media_controller.status
        return DeviceStatus(
            device_id=device_id,
            connection=ConnectionState.CONNECTED,
            observed_at=self._now(),
            receiver=ReceiverStatus(
                app_id=receiver.app_id if receiver is not None else None,
                app_name=receiver.display_name if receiver is not None else None,
                volume_level=receiver.volume_level if receiver is not None else None,
                muted=receiver.volume_muted if receiver is not None else None,
                standby=receiver.is_stand_by if receiver is not None else None,
            ),
            media=self._media_status(media),
        )

    def load_media(self, device_id: DeviceId, request: MediaRequest) -> None:
        cast_device = self._ready(device_id)
        response = WaitResponse(self._request_timeout, "load media")
        self._command(
            device_id,
            "load media",
            lambda: cast_device.media_controller.play_media(
                request.url,
                request.content_type,
                title=request.title,
                callback_function=response.callback,
            ),
        )
        self._command(device_id, "load media", response.wait_response)

    def play(self, device_id: DeviceId) -> None:
        cast_device = self._ready(device_id)
        self._command(
            device_id,
            "play",
            lambda: cast_device.media_controller.play(timeout=self._request_timeout),
        )

    def pause(self, device_id: DeviceId) -> None:
        cast_device = self._ready(device_id)
        self._command(
            device_id,
            "pause",
            lambda: cast_device.media_controller.pause(timeout=self._request_timeout),
        )

    def stop(self, device_id: DeviceId) -> None:
        cast_device = self._ready(device_id)
        self._command(
            device_id,
            "stop",
            lambda: cast_device.media_controller.stop(timeout=self._request_timeout),
        )

    def seek(self, device_id: DeviceId, position_seconds: float) -> None:
        cast_device = self._ready(device_id)
        self._command(
            device_id,
            "seek",
            lambda: cast_device.media_controller.seek(
                position_seconds, timeout=self._request_timeout
            ),
        )

    def set_volume(self, device_id: DeviceId, level: float) -> None:
        cast_device = self._ready(device_id)
        self._command(
            device_id,
            "set volume",
            lambda: cast_device.set_volume(level, timeout=self._request_timeout),
        )

    def set_muted(self, device_id: DeviceId, muted: bool) -> None:
        cast_device = self._ready(device_id)
        self._command(
            device_id,
            "set mute",
            lambda: cast_device.set_volume_muted(muted, timeout=self._request_timeout),
        )

    def close(self) -> None:
        """Disconnect every cached socket worker owned by this adapter exactly once."""
        casts = list(self._casts.values())
        self._casts.clear()
        for cast_device in casts:
            self._disconnect(cast_device)

    @staticmethod
    def _device(cast_device: Chromecast) -> Device:
        info = cast_device.cast_info
        return Device(
            id=DeviceId(str(info.uuid)),
            friendly_name=info.friendly_name or str(info.uuid),
            host=info.host,
            port=info.port,
            kind=_KIND_MAP.get(info.cast_type or "", DeviceKind.UNKNOWN),
            model_name=info.model_name,
        )

    def _cast(self, device_id: DeviceId) -> Chromecast:
        try:
            return self._casts[device_id]
        except KeyError as error:
            raise DeviceNotFoundError(
                f"device {device_id} has not been discovered", device_id=device_id
            ) from error

    def _ready(self, device_id: DeviceId) -> Chromecast:
        cast_device = self._cast(device_id)
        try:
            return self._wait_ready(cast_device, device_id, timeout=self._connection_timeout)
        except (OperationTimeoutError, DeviceUnavailableError) as first_error:
            self._casts.pop(device_id, None)
            self._disconnect(cast_device)
            try:
                self.discover(timeout=self._recovery_timeout)
            except DiscoveryError as discovery_error:
                raise DeviceUnavailableError(
                    f"device {device_id} could not be rediscovered: {discovery_error.message}",
                    device_id=device_id,
                ) from discovery_error
            recovered = self._casts.get(device_id)
            if recovered is None:
                raise DeviceUnavailableError(
                    f"device {device_id} was not found during bounded rediscovery",
                    device_id=device_id,
                ) from first_error
            return self._wait_ready(recovered, device_id, timeout=self._connection_timeout)

    def _ready_bounded(self, device_id: DeviceId, deadline: float) -> Chromecast:
        """Like `_ready`, but every step spends at most what remains until `deadline`."""
        cast_device = self._cast(device_id)
        connect_budget = self._budget(self._connection_timeout, deadline, device_id)
        try:
            return self._wait_ready(cast_device, device_id, timeout=connect_budget)
        except (OperationTimeoutError, DeviceUnavailableError) as first_error:
            self._casts.pop(device_id, None)
            self._disconnect(cast_device)
            try:
                self.discover(timeout=self._budget(self._recovery_timeout, deadline, device_id))
            except DiscoveryError as discovery_error:
                raise DeviceUnavailableError(
                    f"device {device_id} could not be rediscovered: {discovery_error.message}",
                    device_id=device_id,
                ) from discovery_error
            recovered = self._casts.get(device_id)
            if recovered is None:
                raise DeviceUnavailableError(
                    f"device {device_id} was not found during bounded rediscovery",
                    device_id=device_id,
                ) from first_error
            reconnect_budget = self._budget(self._connection_timeout, deadline, device_id)
            return self._wait_ready(recovered, device_id, timeout=reconnect_budget)

    def _budget(self, preferred: float, deadline: float, device_id: DeviceId) -> float:
        """At most `preferred`, but never more than what is left until `deadline`.

        Raises rather than hand a non-positive timeout downstream: a remaining budget of
        zero (or less) means the confirmation window is already closed.
        """
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise OperationTimeoutError(
                f"status read budget exhausted for device {device_id}", device_id=device_id
            )
        return min(preferred, remaining)

    def _wait_ready(
        self, cast_device: Chromecast, device_id: DeviceId, *, timeout: float
    ) -> Chromecast:
        try:
            cast_device.wait(timeout=timeout)
        except pychromecast.RequestTimeout as error:
            raise OperationTimeoutError(
                f"timed out connecting to device {device_id}", device_id=device_id
            ) from error
        except (NotConnected, ChromecastConnectionError, OSError) as error:
            raise DeviceUnavailableError(
                f"device {device_id} is unavailable: {error}", device_id=device_id
            ) from error
        return cast_device

    def _disconnect(self, cast_device: Chromecast) -> None:
        with suppress(PyChromecastError, OSError):
            cast_device.disconnect(timeout=self._connection_timeout)

    def _receiver_status_bounded(
        self, cast_device: Chromecast, device_id: DeviceId, deadline: float
    ) -> None:
        """Like `_receiver_status`, bounded by what remains of the caller's `timeout`."""
        budget = self._budget(self._request_timeout, deadline, device_id)
        self._receiver_status_with_timeout(cast_device, device_id, budget)

    def _receiver_status_with_timeout(
        self, cast_device: Chromecast, device_id: DeviceId, timeout: float
    ) -> None:
        response = WaitResponse(timeout, "receiver status")
        try:
            cast_device.socket_client.receiver_controller.update_status(
                callback_function=response.callback
            )
            response.wait_response()
        except pychromecast.RequestTimeout as error:
            raise OperationTimeoutError(
                f"timed out reading status from device {device_id}", device_id=device_id
            ) from error
        except (NotConnected, ChromecastConnectionError, PyChromecastError, OSError) as error:
            raise DeviceUnavailableError(
                f"could not read status from device {device_id}: {error}", device_id=device_id
            ) from error

    def _command(self, device_id: DeviceId, action: str, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except pychromecast.RequestTimeout as error:
            raise OperationTimeoutError(
                f"timed out sending {action} to device {device_id}", device_id=device_id
            ) from error
        except (NotConnected, ChromecastConnectionError, OSError) as error:
            raise DeviceUnavailableError(
                f"device {device_id} is unavailable: {error}", device_id=device_id
            ) from error
        except PyChromecastError as error:
            raise CommandRejectedError(
                f"device {device_id} rejected {action}: {error}", device_id=device_id
            ) from error

    @staticmethod
    def _media_status(status: PyMediaStatus) -> MediaStatus | None:
        if status.content_id is None and status.player_state in (
            MEDIA_PLAYER_STATE_IDLE,
            MEDIA_PLAYER_STATE_UNKNOWN,
        ):
            return None
        playback_state = _STATE_MAP.get(status.player_state, PlaybackState.UNKNOWN)
        position = (
            status.adjusted_current_time
            if playback_state is PlaybackState.PLAYING
            else status.current_time
        )
        return MediaStatus(
            playback_state=playback_state,
            content_id=status.content_id,
            content_type=status.content_type,
            title=status.title,
            position_seconds=position,
            duration_seconds=status.duration,
            supports_seek=status.supports_seek,
        )
