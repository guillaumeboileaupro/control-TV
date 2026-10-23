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
from pychromecast.const import MESSAGE_TYPE
from pychromecast.controllers.media import (
    MEDIA_PLAYER_STATE_BUFFERING,
    MEDIA_PLAYER_STATE_IDLE,
    MEDIA_PLAYER_STATE_PAUSED,
    MEDIA_PLAYER_STATE_PLAYING,
    TYPE_GET_STATUS,
    TYPE_MEDIA_STATUS,
    TYPE_PAUSE,
    TYPE_PLAY,
    TYPE_SEEK,
    TYPE_STOP,
)
from pychromecast.controllers.media import (
    MediaStatus as PyMediaStatus,
)
from pychromecast.error import (
    ChromecastConnectionError,
    NotConnected,
    PyChromecastError,
    RequestFailed,
    UnsupportedNamespace,
)
from pychromecast.response_handler import WaitResponse

from control_tv.adapters.cast_replies import (
    checked_media_entry,
    reported_details,
    reported_muted,
    reported_standby,
    reported_volume_control_type,
    reported_volume_level,
)
from control_tv.adapters.remote_detection import (
    NoRemoteDetection,
    RemoteDetector,
    ZeroconfRemoteDetector,
)
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
    MetadataType,
    OperationTimeoutError,
    PlaybackState,
    ReceiverStatus,
    StreamType,
)

Discoverer = Callable[[float], tuple[list[Chromecast], object]]
T = TypeVar("T")

_KIND_MAP = {
    "cast": DeviceKind.CAST,
    "audio": DeviceKind.AUDIO,
    "group": DeviceKind.GROUP,
}
# The player state PyChromecast fills in when the receiver reports none.
_STATE_NOT_REPORTED = PyMediaStatus().player_state
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
        remote_detector: RemoteDetector | None = None,
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
        # The Android TV Remote service is looked for after real discovery only: a test
        # discoverer gets no network browse unless the test passes its own detector.
        if remote_detector is None:
            remote_detector = (
                ZeroconfRemoteDetector()
                if discoverer is _default_discoverer
                else NoRemoteDetection()
            )
        self._remote_detector = remote_detector
        self._now = now
        self._clock = clock
        self._casts: dict[DeviceId, Chromecast] = {}
        self._browser: object | None = None

    def discover(self, *, timeout: float) -> list[Device]:
        return self._discover(timeout=timeout)

    def _discover(self, *, timeout: float, cleanup_deadline: float | None = None) -> list[Device]:
        """Replace the discovery snapshot while keeping its zeroconf owner alive.

        PyChromecast gives every returned Chromecast the browser's zeroconf instance.
        The browser must therefore live for exactly as long as those cached objects.
        """
        if not math.isfinite(timeout) or timeout <= 0:
            raise InvalidArgumentError(f"discovery timeout must be a finite number > 0: {timeout}")
        browser: object | None = None
        casts: list[Chromecast] = []
        try:
            casts, browser = self._discoverer(timeout)
            # On the Cast browser's own zeroconf, after it found devices; never during the
            # recovery inside get_status, which runs under the caller's strict budget.
            remote_hosts = (
                self._remote_detector.detect(getattr(browser, "zc", None))
                if casts and cleanup_deadline is None
                else None
            )
            discovered = [self._device(cast_device, remote_hosts) for cast_device in casts]
            replacements = {DeviceId(str(cast_device.uuid)): cast_device for cast_device in casts}
            if not replacements:
                self._stop_browser(browser)
                browser = None
        except DiscoveryError:
            self._dispose_discovery(casts, browser)
            raise
        except (PyChromecastError, OSError) as error:
            self._dispose_discovery(casts, browser)
            raise DiscoveryError(f"Cast discovery failed: {error}") from error
        except Exception:
            self._dispose_discovery(casts, browser)
            raise

        previous_casts = self._casts
        previous_browser = self._browser
        self._casts = replacements
        self._browser = browser
        for previous in previous_casts.values():
            if all(previous is not current for current in replacements.values()):
                if cleanup_deadline is None:
                    self._disconnect(previous)
                else:
                    self._disconnect_bounded(previous, cleanup_deadline)
        if previous_browser is not browser:
            self._stop_browser(previous_browser)
        return discovered

    def _dispose_discovery(self, casts: list[Chromecast], browser: object | None) -> None:
        for cast_device in casts:
            self._disconnect(cast_device)
        self._stop_browser(browser)

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
        receiver_reply = self._receiver_status_bounded(cast_device, device_id, deadline)
        media = self._fresh_media_status(cast_device, device_id, deadline)
        # Defense in depth: a library callback that violated its own timeout must never
        # let an over-budget snapshot escape as a successful status read.
        self._budget(self._request_timeout, deadline, device_id)
        receiver = cast_device.status
        return DeviceStatus(
            device_id=device_id,
            connection=ConnectionState.CONNECTED,
            observed_at=self._now(),
            receiver=ReceiverStatus(
                app_id=receiver.app_id if receiver is not None else None,
                app_name=receiver.display_name if receiver is not None else None,
                app_session_id=receiver.session_id if receiver is not None else None,
                volume_level=reported_volume_level(receiver_reply),
                muted=reported_muted(receiver_reply),
                standby=reported_standby(receiver_reply),
                volume_control_type=reported_volume_control_type(receiver_reply),
            ),
            media=media,
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
        response_data = response.response
        response_type = response_data.get("type") if response_data is not None else None
        if response_type != TYPE_MEDIA_STATUS:
            detail = response_data.get("detailedErrorCode") if response_data is not None else None
            suffix = "" if detail is None else f" (detailed error {detail})"
            raise CommandRejectedError(
                f"device {device_id} rejected load media with terminal response "
                f"{response_type!r}{suffix}",
                device_id=device_id,
            )

    def play(self, device_id: DeviceId, media_session_id: int | None = None) -> None:
        self._media_session_command(device_id, "play", TYPE_PLAY, media_session_id)

    def pause(self, device_id: DeviceId, media_session_id: int | None = None) -> None:
        self._media_session_command(device_id, "pause", TYPE_PAUSE, media_session_id)

    def stop(self, device_id: DeviceId, media_session_id: int | None = None) -> None:
        self._media_session_command(device_id, "stop", TYPE_STOP, media_session_id)

    def close_app(self, device_id: DeviceId, receiver_session_id: str) -> None:
        """Stop exactly the receiver-app session captured by the caller, never the cached current
        application. A stale session id is safe: the receiver rejects it instead of retargeting."""
        if not isinstance(receiver_session_id, str) or not receiver_session_id.strip():
            raise InvalidArgumentError("receiver app session id is required", device_id=device_id)
        cast_device = self._ready(device_id)
        response = WaitResponse(self._request_timeout, "close receiver app")
        message: dict[str, object] = {
            MESSAGE_TYPE: "STOP",
            "sessionId": receiver_session_id,
        }
        self._command(
            device_id,
            "close receiver app",
            lambda: cast_device.socket_client.receiver_controller.send_message_nocheck(
                message, callback_function=response.callback
            ),
        )
        self._command(device_id, "close receiver app", response.wait_response)

    def seek(
        self,
        device_id: DeviceId,
        position_seconds: float,
        media_session_id: int | None = None,
    ) -> None:
        if not math.isfinite(position_seconds) or position_seconds < 0:
            raise InvalidArgumentError(
                f"seek position must be a finite number >= 0: {position_seconds}"
            )
        self._media_session_command(
            device_id,
            "seek",
            TYPE_SEEK,
            media_session_id,
            currentTime=position_seconds,
            resumeState="PLAYBACK_START",
        )

    def _media_session_command(
        self,
        device_id: DeviceId,
        name: str,
        message_type: str,
        media_session_id: int | None,
        **fields: object,
    ) -> None:
        """Send to the session captured by the service, never PyChromecast's current cache."""
        if media_session_id is None:
            raise InvalidArgumentError(
                "media session id is required for playback commands", device_id=device_id
            )
        cast_device = self._ready(device_id)
        response = WaitResponse(self._request_timeout, name)
        message: dict[str, object] = {
            MESSAGE_TYPE: message_type,
            "mediaSessionId": media_session_id,
            **fields,
        }
        self._command(
            device_id,
            name,
            lambda: cast_device.media_controller.send_message_nocheck(
                message, callback_function=response.callback
            ),
        )
        self._command(device_id, name, response.wait_response)

    def set_volume(self, device_id: DeviceId, level: float) -> None:
        if not math.isfinite(level) or not 0 <= level <= 1:
            raise InvalidArgumentError(f"volume must be a finite number from 0 to 1: {level}")
        cast_device = self._ready(device_id)
        self._command(
            device_id,
            "set volume",
            lambda: cast_device.set_volume(level, timeout=self._request_timeout),
        )

    def set_muted(self, device_id: DeviceId, muted: bool) -> None:
        if not isinstance(muted, bool):
            raise InvalidArgumentError(f"muted must be a boolean: {muted!r}")
        cast_device = self._ready(device_id)
        self._command(
            device_id,
            "set mute",
            lambda: cast_device.set_volume_muted(muted, timeout=self._request_timeout),
        )

    def close(self) -> None:
        """Release cached socket workers, discovery threads and zeroconf exactly once."""
        casts = list(self._casts.values())
        browser = self._browser
        self._casts.clear()
        self._browser = None
        for cast_device in casts:
            self._disconnect(cast_device)
        self._stop_browser(browser)

    @staticmethod
    def _device(cast_device: Chromecast, remote_hosts: frozenset[str] | None = None) -> Device:
        info = cast_device.cast_info
        kind = _KIND_MAP.get(info.cast_type or "", DeviceKind.OTHER)
        # A Cast group's address is one of its member TVs': matching it would credit the group
        # with that TV's remote channel, so a group's stays not reported.
        android_tv_remote = (
            None if remote_hosts is None or kind is DeviceKind.GROUP else info.host in remote_hosts
        )
        return Device(
            id=DeviceId(str(info.uuid)),
            friendly_name=info.friendly_name or str(info.uuid),
            host=info.host,
            port=info.port,
            kind=kind,
            model_name=info.model_name,
            android_tv_remote=android_tv_remote,
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
            self._disconnect_bounded(cast_device, deadline)
            try:
                self._discover(
                    timeout=self._budget(self._recovery_timeout, deadline, device_id),
                    cleanup_deadline=deadline,
                )
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

    def _disconnect(self, cast_device: Chromecast, *, timeout: float | None = None) -> None:
        wait_timeout = self._connection_timeout if timeout is None else timeout
        with suppress(PyChromecastError, OSError, RuntimeError):
            cast_device.disconnect(timeout=wait_timeout)

    @staticmethod
    def _stop_browser(browser: object | None) -> None:
        if browser is None:
            return
        stop = getattr(browser, "stop_discovery", None)
        if callable(stop):
            # stop_discovery can race HostBrowser.start() and join() during early cleanup.
            # The stop flag and zeroconf shutdown have already been requested in that case.
            with suppress(PyChromecastError, OSError, RuntimeError):
                stop()

    def _disconnect_bounded(self, cast_device: Chromecast, deadline: float) -> None:
        """Signal shutdown and wait no longer than the status budget still available."""
        remaining = max(0.0, deadline - self._clock())
        self._disconnect(cast_device, timeout=min(self._connection_timeout, remaining))

    def _receiver_status_bounded(
        self, cast_device: Chromecast, device_id: DeviceId, deadline: float
    ) -> dict[str, object] | None:
        """Like `_receiver_status`, bounded by what remains of the caller's `timeout`."""
        budget = self._budget(self._request_timeout, deadline, device_id)
        return self._receiver_status_with_timeout(cast_device, device_id, budget)

    def _receiver_status_with_timeout(
        self, cast_device: Chromecast, device_id: DeviceId, timeout: float
    ) -> dict[str, object] | None:
        """Refresh the receiver status and return the raw reply, when there is one."""
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
        return response.response

    def _fresh_media_status(
        self, cast_device: Chromecast, device_id: DeviceId, deadline: float
    ) -> MediaStatus | None:
        """Ask the receiver for its media status now and report only what that reply says.

        PyChromecast merges every MEDIA_STATUS into one cached status: an empty status list is
        ignored and a field a message omits keeps its previous value, so the cache can still
        describe a session that ended, or carry an old content id into a new state. The reply
        is parsed into a new `MediaStatus` instead, and a field the reply omits is reported as
        not reported rather than as that new object's default (a position of 0, no supported
        command). The request is sent with `send_message_nocheck`, which never launches an
        application: when the running application has no media namespace there is no media
        session to report.
        """
        budget = self._budget(self._request_timeout, deadline, device_id)
        response = WaitResponse(budget, "media status")
        try:
            cast_device.media_controller.send_message_nocheck(
                {MESSAGE_TYPE: TYPE_GET_STATUS}, callback_function=response.callback
            )
            response.wait_response()
        except UnsupportedNamespace:
            return None
        except pychromecast.RequestTimeout as error:
            raise OperationTimeoutError(
                f"timed out reading media status from device {device_id}", device_id=device_id
            ) from error
        except (NotConnected, ChromecastConnectionError, PyChromecastError, OSError) as error:
            raise DeviceUnavailableError(
                f"could not read media status from device {device_id}: {error}",
                device_id=device_id,
            ) from error
        reply = response.response
        reply_type = reply.get(MESSAGE_TYPE) if reply is not None else None
        if reply is None or reply_type != TYPE_MEDIA_STATUS:
            raise DeviceUnavailableError(
                f"device {device_id} answered the media status request with {reply_type!r}",
                device_id=device_id,
            )
        entry = checked_media_entry(reply, device_id)
        if entry is None:
            return None
        fresh = PyMediaStatus()
        fresh.update({"status": [entry]})
        # After the check an omitted, null or unusable field is absent or null: not reported.
        # PyChromecast 14.0.10 does not parse currentItemId: it is read from the checked entry.
        item = entry.get("currentItemId")
        artist, stream_type, metadata_type = reported_details(entry)
        return self._media_status(
            fresh,
            position_reported=entry.get("currentTime") is not None,
            commands_reported=entry.get("supportedMediaCommands") is not None,
            current_item_id=item if isinstance(item, int) and not isinstance(item, bool) else None,
            artist=artist,
            stream_type=stream_type,
            metadata_type=metadata_type,
        )

    def _command(self, device_id: DeviceId, action: str, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except pychromecast.RequestTimeout as error:
            raise OperationTimeoutError(
                f"timed out sending {action} to device {device_id}", device_id=device_id
            ) from error
        except (RequestFailed, NotConnected, ChromecastConnectionError, OSError) as error:
            raise DeviceUnavailableError(
                f"device {device_id} is unavailable: {error}", device_id=device_id
            ) from error
        except PyChromecastError as error:
            raise CommandRejectedError(
                f"device {device_id} rejected {action}: {error}", device_id=device_id
            ) from error

    @staticmethod
    def _media_status(
        status: PyMediaStatus,
        *,
        position_reported: bool = True,
        commands_reported: bool = True,
        current_item_id: int | None = None,
        artist: str | None = None,
        stream_type: StreamType | None = None,
        metadata_type: MetadataType | None = None,
    ) -> MediaStatus | None:
        """Map a PyChromecast status; a field marked as not reported is `None`."""
        if status.content_id is None and status.player_state in (
            MEDIA_PLAYER_STATE_IDLE,
            _STATE_NOT_REPORTED,
        ):
            return None
        playback_state = _STATE_MAP.get(status.player_state, PlaybackState.UNDETERMINED)
        # Extrapolating a playing position needs the playback rate; without one (a null
        # rate), the position is the one the receiver reported.
        extrapolate = playback_state is PlaybackState.PLAYING and isinstance(
            status.playback_rate, int | float
        )
        position = (
            None
            if not position_reported
            else status.adjusted_current_time
            if extrapolate
            else status.current_time
        )
        metadata = status.media_metadata
        return MediaStatus(
            playback_state=playback_state,
            content_id=status.content_id,
            media_session_id=status.media_session_id,
            current_item_id=current_item_id,
            content_type=status.content_type,
            title=metadata.get("title") if isinstance(metadata, dict) else None,
            position_seconds=position,
            duration_seconds=status.duration,
            artist=artist,
            stream_type=stream_type,
            metadata_type=metadata_type,
            supports_seek=status.supports_seek if commands_reported else None,
            supports_pause=status.supports_pause if commands_reported else None,
        )
