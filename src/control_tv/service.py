"""The authoritative control behavior shared by the manual UI and the MCP adapter.

`ControlService` implements `TvControl` over a `CastTransport`. For every command it:

1. validates arguments before anything is sent;
2. sends the command, letting any `ControlError` from the transport propagate;
3. then reads the TV status, within a bounded time, until the TV shows the expected state.

Step 3 is what separates "command sent" from "TV state confirmed" (see `Confirmation`). A
failure to read the status after the command was sent never turns into an exception: the
command *was* sent, so the result is `UNCONFIRMED` and says why.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from control_tv.domain import (
    Command,
    CommandResult,
    Confirmation,
    ConnectionState,
    ControlError,
    Device,
    DeviceId,
    DeviceStatus,
    InvalidArgumentError,
    MediaRequest,
    PlaybackState,
    UnsupportedOperationError,
)
from control_tv.ports import CastTransport

ExpectedState = Callable[[DeviceStatus], bool]

DEFAULT_DISCOVERY_TIMEOUT = 5.0
DEFAULT_CONFIRM_TIMEOUT = 5.0
DEFAULT_POLL_INTERVAL = 0.25
DEFAULT_SEEK_TOLERANCE = 2.0
DEFAULT_VOLUME_TOLERANCE = 0.01

_LOADED_STATES = (PlaybackState.PLAYING, PlaybackState.BUFFERING, PlaybackState.PAUSED)


def _require(condition: bool, message: str, device_id: str | None = None) -> None:
    if not condition:
        raise InvalidArgumentError(message, device_id=device_id)


def _is_finite(value: float) -> bool:
    return math.isfinite(value)


def _checked_id(device_id: DeviceId) -> DeviceId:
    _require(bool(device_id.strip()), "device id must not be blank")
    return device_id


def _playback_in(*states: PlaybackState) -> ExpectedState:
    def check(status: DeviceStatus) -> bool:
        return status.media is not None and status.media.playback_state in states

    return check


def _loaded(url: str) -> ExpectedState:
    def check(status: DeviceStatus) -> bool:
        media = status.media
        return (
            media is not None and media.content_id == url and media.playback_state in _LOADED_STATES
        )

    return check


def _position_near(target: float, tolerance: float) -> ExpectedState:
    def check(status: DeviceStatus) -> bool:
        media = status.media
        return (
            media is not None
            and media.position_seconds is not None
            and abs(media.position_seconds - target) <= tolerance
        )

    return check


def _volume_near(level: float, tolerance: float) -> ExpectedState:
    def check(status: DeviceStatus) -> bool:
        receiver = status.receiver
        return (
            receiver is not None
            and receiver.volume_level is not None
            and abs(receiver.volume_level - level) <= tolerance
        )

    return check


def _muted_is(muted: bool) -> ExpectedState:
    def check(status: DeviceStatus) -> bool:
        return status.receiver is not None and status.receiver.muted is muted

    return check


class ControlService:
    """Authoritative implementation of `TvControl`.

    `confirm_timeout=None` disables verification: commands then return `NOT_CHECKED`.
    `clock` and `sleep` are injectable so confirmation timing is deterministic in tests.
    """

    def __init__(
        self,
        transport: CastTransport,
        *,
        confirm_timeout: float | None = DEFAULT_CONFIRM_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        seek_tolerance: float = DEFAULT_SEEK_TOLERANCE,
        volume_tolerance: float = DEFAULT_VOLUME_TOLERANCE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        _require(
            confirm_timeout is None or (_is_finite(confirm_timeout) and confirm_timeout >= 0),
            f"confirm_timeout must be None or a finite number >= 0: {confirm_timeout}",
        )
        _require(
            _is_finite(poll_interval) and poll_interval > 0,
            f"poll_interval must be a finite number > 0: {poll_interval}",
        )
        _require(
            _is_finite(seek_tolerance) and seek_tolerance >= 0,
            f"seek_tolerance must be a finite number >= 0: {seek_tolerance}",
        )
        _require(
            _is_finite(volume_tolerance) and volume_tolerance >= 0,
            f"volume_tolerance must be a finite number >= 0: {volume_tolerance}",
        )
        self._transport = transport
        self._confirm_timeout = confirm_timeout
        self._poll_interval = poll_interval
        self._seek_tolerance = seek_tolerance
        self._volume_tolerance = volume_tolerance
        self._clock = clock
        self._sleep = sleep

    def discover_devices(self, *, timeout: float = DEFAULT_DISCOVERY_TIMEOUT) -> list[Device]:
        _require(
            _is_finite(timeout) and timeout > 0,
            f"discovery timeout must be a finite number > 0: {timeout}",
        )
        return list(self._transport.discover(timeout=timeout))

    def get_status(self, device_id: DeviceId) -> DeviceStatus:
        return self._transport.get_status(_checked_id(device_id))

    def load_media(self, device_id: DeviceId, request: MediaRequest) -> CommandResult:
        device_id = _checked_id(device_id)
        self._transport.load_media(device_id, request)
        return self._verify(Command.LOAD_MEDIA, device_id, _loaded(request.url), "media loaded")

    def play(self, device_id: DeviceId) -> CommandResult:
        device_id = _checked_id(device_id)
        self._transport.play(device_id)
        return self._verify(
            Command.PLAY, device_id, _playback_in(PlaybackState.PLAYING), "playback playing"
        )

    def pause(self, device_id: DeviceId) -> CommandResult:
        device_id = _checked_id(device_id)
        self._transport.pause(device_id)
        return self._verify(
            Command.PAUSE, device_id, _playback_in(PlaybackState.PAUSED), "playback paused"
        )

    def stop(self, device_id: DeviceId) -> CommandResult:
        device_id = _checked_id(device_id)
        self._transport.stop(device_id)
        return self._verify(
            Command.STOP, device_id, _playback_in(PlaybackState.IDLE), "playback stopped"
        )

    def seek(self, device_id: DeviceId, position_seconds: float) -> CommandResult:
        device_id = _checked_id(device_id)
        _require(
            _is_finite(position_seconds) and position_seconds >= 0,
            f"seek position must be a finite number >= 0: {position_seconds}",
            device_id,
        )
        media = self._transport.get_status(device_id).media
        if media is not None and media.supports_seek is False:
            raise UnsupportedOperationError(
                "the current media does not support seeking", device_id=device_id
            )
        self._transport.seek(device_id, position_seconds)
        return self._verify(
            Command.SEEK,
            device_id,
            _position_near(position_seconds, self._seek_tolerance),
            f"position near {position_seconds:g}s",
        )

    def set_volume(self, device_id: DeviceId, level: float) -> CommandResult:
        device_id = _checked_id(device_id)
        _require(
            _is_finite(level) and 0.0 <= level <= 1.0,
            f"volume level must be within 0.0-1.0: {level}",
            device_id,
        )
        self._transport.set_volume(device_id, level)
        return self._verify(
            Command.SET_VOLUME,
            device_id,
            _volume_near(level, self._volume_tolerance),
            f"volume near {level:g}",
        )

    def set_muted(self, device_id: DeviceId, muted: bool) -> CommandResult:
        device_id = _checked_id(device_id)
        self._transport.set_muted(device_id, muted)
        return self._verify(
            Command.SET_MUTED, device_id, _muted_is(muted), "muted" if muted else "unmuted"
        )

    def _verify(
        self,
        command: Command,
        device_id: DeviceId,
        expected: ExpectedState,
        description: str,
    ) -> CommandResult:
        """Read the TV status until `expected` holds or the time budget is spent."""
        timeout = self._confirm_timeout
        if timeout is None:
            return CommandResult(
                command=command, device_id=device_id, confirmation=Confirmation.NOT_CHECKED
            )

        deadline = self._clock() + timeout
        last_status: DeviceStatus | None = None
        last_error: ControlError | None = None
        while True:
            try:
                status = self._transport.get_status(device_id)
            except ControlError as error:
                last_error = error
            else:
                last_status, last_error = status, None
                if status.connection is ConnectionState.CONNECTED and expected(status):
                    return CommandResult(
                        command=command,
                        device_id=device_id,
                        confirmation=Confirmation.CONFIRMED,
                        observed=status,
                    )
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._sleep(min(self._poll_interval, remaining))

        if last_error is not None:
            detail = f"command sent; could not read the TV status: {last_error.message}"
        else:
            detail = f"command sent; the TV did not show {description} within {timeout:g}s"
        return CommandResult(
            command=command,
            device_id=device_id,
            confirmation=Confirmation.UNCONFIRMED,
            observed=last_status,
            detail=detail,
        )
