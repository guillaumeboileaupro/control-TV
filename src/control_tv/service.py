"""The authoritative control behavior shared by the manual UI and the MCP adapter.

`ControlService` implements `TvControl` over a `CastTransport`. For every command it:

1. validates arguments before anything is sent;
2. sends the command, letting a `ControlError` from the transport propagate;
3. then reads the TV status, within a bounded time, until the TV shows the expected state.

Step 3 is what separates "command sent" from "TV state confirmed" (see `Confirmation`). A
failure to read the status after the command was sent never turns into an exception: the
command *was* sent, so the result is `UNCONFIRMED` and says why.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from control_tv.command_trace import CommandTrace, ConfirmationGate, HoldRequest, TraceRecorder
from control_tv.domain import (
    AppLaunchResult,
    AppListing,
    Command,
    CommandRejectedError,
    CommandResult,
    Confirmation,
    ConnectionState,
    ControlError,
    Device,
    DeviceId,
    DeviceStatus,
    InvalidArgumentError,
    MediaRequest,
    MediaStatus,
    OperationTimeoutError,
    PlaybackState,
    RemoteKey,
    RemoteKeyResult,
    RemoteObservation,
    RemoteTextResult,
    TvApp,
    UnsupportedOperationError,
    VolumeControlType,
)
from control_tv.ports import AppLister, AppMemory, CastTransport, TvApps, TvRemote
from control_tv.tv_channels import TvChannels


@dataclass(frozen=True, slots=True)
class Observation:
    """What was actually seen on the TV for one command, regardless of what it expected.

    `description` is always a plain, factual statement of what was observed - never a
    restatement of what was hoped for, and never invented when the TV reported nothing:
    the absence of a media session is reported as exactly that, not as an idle/stopped state.
    """

    matched: bool
    description: str


ExpectedState = Callable[[DeviceStatus], Observation]

DEFAULT_DISCOVERY_TIMEOUT = 5.0
DEFAULT_CONFIRM_TIMEOUT = 5.0
DEFAULT_STATUS_TIMEOUT = 5.0
DEFAULT_POLL_INTERVAL = 0.25
DEFAULT_SEEK_TOLERANCE = 2.0
DEFAULT_VOLUME_TOLERANCE = 0.01

_LOADED_STATES = (PlaybackState.PLAYING, PlaybackState.BUFFERING, PlaybackState.PAUSED)


def _require(condition: bool, message: str, device_id: str | None = None) -> None:
    if not condition:
        raise InvalidArgumentError(message, device_id=device_id)


def _is_finite(value: float) -> bool:
    return math.isfinite(value)


def _number(value: object, name: str, device_id: str | None = None) -> float:
    """`value` as a float when it is a real, finite number.

    A bool is not a number here (`True` would otherwise mean 1), and neither is a string or an
    integer too large for a float; each is an `InvalidArgumentError`, not a crash.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidArgumentError(f"{name} must be a number: {value!r}", device_id=device_id)
    try:
        number = float(value)
    except OverflowError:
        raise InvalidArgumentError(f"{name} is too large: {value!r}", device_id=device_id) from None
    _require(_is_finite(number), f"{name} must be a finite number: {value!r}", device_id)
    return number


def _checked_id(device_id: DeviceId) -> DeviceId:
    _require(bool(device_id.strip()), "device id must not be blank")
    return device_id


def _usable_content_id(content_id: str | None) -> str | None:
    return content_id if content_id is not None and content_id.strip() else None


@dataclass(frozen=True, slots=True)
class _MediaIdentity:
    """What a pre-command status read established about the media being controlled.

    `content_id` names the media item and is required. `session_id` (the receiver's
    mediaSessionId) names the playback session, which can hold several queued items, and
    `item_id` (currentItemId) names the active item within that session's queue, so two items
    with the same content id can still differ. Both can only narrow a match on `content_id`,
    never establish one on their own.
    """

    content_id: str
    session_id: int | None
    item_id: int | None


@dataclass(frozen=True, slots=True)
class _PlaybackTarget:
    """Receiver identity captured before sending a playback command."""

    identity: _MediaIdentity | None
    media_session_id: int
    media: MediaStatus


def _identity_of(media: MediaStatus | None) -> _MediaIdentity | None:
    content_id = _usable_content_id(media.content_id if media is not None else None)
    if media is None or content_id is None:
        return None
    return _MediaIdentity(content_id, media.media_session_id, media.current_item_id)


def _identity_mismatch(expected: _MediaIdentity | None, media: MediaStatus) -> str | None:
    """Why this one read cannot be shown to show the media identified before the command.

    The content id must be the same, and a session reported before the command must be
    reported by the read (a missing one is no evidence). An explicitly different content id or
    session is a contradiction, which `_IdentityCheck` keeps for the whole attempt. A session
    not reported before the command leaves the content id as the only proof, as it was before
    sessions were read.
    """
    if expected is None:
        return "media identity was not reported before the command"
    if media.content_id != expected.content_id:
        return f"loaded content changed to {media.content_id!r} from {expected.content_id!r}"
    if expected.session_id is not None and media.media_session_id is None:
        return "media session was not reported after the command"
    if expected.item_id is not None and media.current_item_id is None:
        return "media queue item was not reported after the command"
    return None


def _identity_contradiction(expected: _MediaIdentity, media: MediaStatus) -> str | None:
    """An identity other than the pre-command one, explicitly reported by this read, or None.

    Only a reported value can contradict: a usable content id other than the expected one
    (another queue item keeps the session but changes it), or a session or queue item other
    than the one reported before the command (two queue items can share the content id and the
    session). An absent, empty or blank content id and a missing session or item are no
    evidence either way.
    """
    content_id = _usable_content_id(media.content_id)
    if content_id is not None and content_id != expected.content_id:
        return f"loaded content changed to {content_id!r} from {expected.content_id!r}"
    session_id = media.media_session_id
    if (
        expected.session_id is not None
        and session_id is not None
        and session_id != expected.session_id
    ):
        return (
            f"media session changed to {session_id} from {expected.session_id} during confirmation"
        )
    item_id = media.current_item_id
    if expected.item_id is not None and item_id is not None and item_id != expected.item_id:
        return f"media queue item changed to {item_id} from {expected.item_id} during confirmation"
    return None


class _IdentityCheck:
    """Checks the reads of ONE confirmation attempt against the pre-command identity.

    It remembers the first explicit identity contradiction across those reads: once a read
    reports another content id (X -> Y), another session (A -> B) or another queue item
    (101 -> 102), that attempt can no longer confirm, because a later read back in the
    original identity cannot show which change the command caused (X -> Y -> X,
    A -> B -> A, 101 -> 102 -> 101). A read that reports no content id, no session or no item
    is rejected on its own but contradicts nothing. A new instance is built with each command's
    predicate, so nothing carries over to another command.
    """

    __slots__ = ("_contradiction", "_expected")

    def __init__(self, expected: _MediaIdentity | None) -> None:
        self._expected = expected
        self._contradiction: str | None = None

    @property
    def contradiction(self) -> str | None:
        """The first explicit identity contradiction seen in this attempt, if there is one."""
        return self._contradiction

    def mismatch(self, media: MediaStatus) -> str | None:
        expected = self._expected
        if self._contradiction is None and expected is not None:
            self._contradiction = _identity_contradiction(expected, media)
        if self._contradiction is not None:
            return self._contradiction
        return _identity_mismatch(expected, media)


def _playback_in(expected: _MediaIdentity | None, *states: PlaybackState) -> ExpectedState:
    return _playback_in_check(_IdentityCheck(expected), *states)


def _playback_in_check(identity: _IdentityCheck, *states: PlaybackState) -> ExpectedState:
    def check(status: DeviceStatus) -> Observation:
        media = status.media
        if media is None:
            return Observation(False, "no active media session")
        mismatch = identity.mismatch(media)
        if mismatch is not None:
            return Observation(False, mismatch)
        return Observation(media.playback_state in states, f"playback is {media.playback_state}")

    return check


def _stopped(expected: _MediaIdentity | None) -> ExpectedState:
    """Stop is shown either by IDLE on the same media, or by the media session ending.

    A receiver commonly ends the media session on stop instead of reporting IDLE. That fresh
    absence confirms the stop only when the media was identified before the command and no
    read of this attempt explicitly reported another content, session or queue item: a
    replacement followed by an absence says nothing about what the stop did. Nothing is
    invented: the observed status keeps `media` as None, never a fabricated IDLE.
    """
    identity = _IdentityCheck(expected)
    playback = _playback_in_check(identity, PlaybackState.IDLE)

    def check(status: DeviceStatus) -> Observation:
        if status.media is not None:
            return playback(status)
        if expected is None:
            return Observation(False, "no active media session")
        if identity.contradiction is not None:
            return Observation(False, identity.contradiction)
        return Observation(True, "the media session ended")

    return check


def _loaded(url: str) -> ExpectedState:
    def check(status: DeviceStatus) -> Observation:
        media = status.media
        if media is None:
            return Observation(False, "no active media session")
        if media.content_id != url:
            return Observation(False, f"loaded content is {media.content_id!r}, not {url!r}")
        matched = media.playback_state in _LOADED_STATES
        return Observation(matched, f"content is loaded but playback is {media.playback_state}")

    return check


def _receiver_session_ended(receiver_session_id: str) -> ExpectedState:
    def check(status: DeviceStatus) -> Observation:
        receiver = status.receiver
        current = None if receiver is None else receiver.app_session_id
        return Observation(
            current != receiver_session_id,
            "the receiver application ended"
            if current != receiver_session_id
            else "the same receiver application is still running",
        )

    return check


def _position_near(
    target: float, tolerance: float, expected: _MediaIdentity | None
) -> ExpectedState:
    identity = _IdentityCheck(expected)

    def check(status: DeviceStatus) -> Observation:
        media = status.media
        if media is None:
            return Observation(False, "no active media session")
        mismatch = identity.mismatch(media)
        if mismatch is not None:
            return Observation(False, mismatch)
        if media.position_seconds is None:
            return Observation(False, "position was not reported")
        matched = abs(media.position_seconds - target) <= tolerance
        return Observation(matched, f"position is {media.position_seconds:g}s")

    return check


def _volume_near(level: float, tolerance: float) -> ExpectedState:
    def check(status: DeviceStatus) -> Observation:
        receiver = status.receiver
        if receiver is None:
            return Observation(False, "no receiver status")
        if receiver.volume_level is None:
            return Observation(False, "volume level was not reported")
        matched = abs(receiver.volume_level - level) <= tolerance
        return Observation(matched, f"volume is {receiver.volume_level:g}")

    return check


def _muted_is(muted: bool) -> ExpectedState:
    def check(status: DeviceStatus) -> Observation:
        receiver = status.receiver
        if receiver is None:
            return Observation(False, "no receiver status")
        if receiver.muted is None:
            return Observation(False, "mute state was not reported")
        return Observation(receiver.muted is muted, f"mute is {'on' if receiver.muted else 'off'}")

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
        status_timeout: float = DEFAULT_STATUS_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        seek_tolerance: float = DEFAULT_SEEK_TOLERANCE,
        volume_tolerance: float = DEFAULT_VOLUME_TOLERANCE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        remote: TvRemote | None = None,
        apps: TvApps | None = None,
        app_memory: AppMemory | None = None,
        receiver_apps: AppLister | None = None,
        trace: TraceRecorder | None = None,
        gate: ConfirmationGate | None = None,
    ) -> None:
        _require(
            confirm_timeout is None or (_is_finite(confirm_timeout) and confirm_timeout >= 0),
            f"confirm_timeout must be None or a finite number >= 0: {confirm_timeout}",
        )
        _require(
            _is_finite(status_timeout) and status_timeout > 0,
            f"status_timeout must be a finite number > 0: {status_timeout}",
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
        self._status_timeout = status_timeout
        self._poll_interval = poll_interval
        self._seek_tolerance = seek_tolerance
        self._volume_tolerance = volume_tolerance
        self._clock = clock
        self._sleep = sleep
        self._trace = trace or TraceRecorder()
        # Acceptance only (C6): holds a sent playback command's confirmation until an
        # independent observer has recorded the session change. None in normal use.
        self._gate = gate
        self._channels = TvChannels(
            self._discovered,
            remote=remote,
            apps=apps,
            memory=app_memory,
            receiver_apps=receiver_apps,
        )
        self._devices: dict[DeviceId, Device] = {}

    def discover_devices(self, *, timeout: float = DEFAULT_DISCOVERY_TIMEOUT) -> list[Device]:
        timeout = _number(timeout, "discovery timeout")
        _require(timeout > 0, f"discovery timeout must be a finite number > 0: {timeout}")
        devices = list(self._transport.discover(timeout=timeout))
        self._devices = {device.id: device for device in devices}
        return devices

    def get_status(self, device_id: DeviceId) -> DeviceStatus:
        return self._transport.get_status(_checked_id(device_id), timeout=self._status_timeout)

    def load_media(self, device_id: DeviceId, request: MediaRequest) -> CommandResult:
        device_id = _checked_id(device_id)
        trace = self._trace.command(Command.LOAD_MEDIA)
        self._send(trace, lambda: self._transport.load_media(device_id, request))
        return self._verify(
            Command.LOAD_MEDIA, device_id, _loaded(request.url), "media loaded", trace=trace
        )

    def play(self, device_id: DeviceId) -> CommandResult:
        device_id = _checked_id(device_id)
        trace = self._trace.command(Command.PLAY)
        deadline = self._confirmation_deadline()
        target = self._playback_target(device_id, deadline, trace)
        trace.record("pre_status", media_session_id=target.media_session_id)
        expected = target.identity
        self._send(
            trace,
            lambda: self._transport.play(device_id, target.media_session_id),
            media_session_id=target.media_session_id,
        )
        deadline = self._hold(trace, target.media_session_id, deadline)
        return self._verify(
            Command.PLAY,
            device_id,
            _playback_in(expected, PlaybackState.PLAYING),
            "playback playing",
            deadline=deadline,
            trace=trace,
            target_session_id=target.media_session_id,
        )

    def pause(self, device_id: DeviceId) -> CommandResult:
        device_id = _checked_id(device_id)
        trace = self._trace.command(Command.PAUSE)
        deadline = self._confirmation_deadline()
        target = self._playback_target(device_id, deadline, trace)
        trace.record("pre_status", media_session_id=target.media_session_id)
        expected = target.identity
        self._send(
            trace,
            lambda: self._transport.pause(device_id, target.media_session_id),
            media_session_id=target.media_session_id,
        )
        deadline = self._hold(trace, target.media_session_id, deadline)
        return self._verify(
            Command.PAUSE,
            device_id,
            _playback_in(expected, PlaybackState.PAUSED),
            "playback paused",
            deadline=deadline,
            trace=trace,
            target_session_id=target.media_session_id,
        )

    def stop(self, device_id: DeviceId) -> CommandResult:
        device_id = _checked_id(device_id)
        trace = self._trace.command(Command.STOP)
        deadline = self._confirmation_deadline()
        target = self._playback_target(device_id, deadline, trace)
        trace.record("pre_status", media_session_id=target.media_session_id)
        expected = target.identity
        self._send(
            trace,
            lambda: self._transport.stop(device_id, target.media_session_id),
            media_session_id=target.media_session_id,
        )
        deadline = self._hold(trace, target.media_session_id, deadline)
        return self._verify(
            Command.STOP,
            device_id,
            _stopped(expected),
            "playback stopped",
            deadline=deadline,
            trace=trace,
            target_session_id=target.media_session_id,
        )

    def close_app(self, device_id: DeviceId, receiver_session_id: str) -> CommandResult:
        """Close one captured Cast application session without ever targeting the current cache."""
        device_id = _checked_id(device_id)
        _require(
            isinstance(receiver_session_id, str) and bool(receiver_session_id.strip()),
            "receiver app session id must be a non-blank string",
            device_id,
        )
        trace = self._trace.command(Command.CLOSE_APP)
        self._send(
            trace,
            lambda: self._transport.close_app(device_id, receiver_session_id),
        )
        return self._verify(
            Command.CLOSE_APP,
            device_id,
            _receiver_session_ended(receiver_session_id),
            "the receiver application to end",
            trace=trace,
        )

    def seek(self, device_id: DeviceId, position_seconds: float) -> CommandResult:
        device_id = _checked_id(device_id)
        position_seconds = _number(position_seconds, "seek position", device_id)
        _require(
            position_seconds >= 0,
            f"seek position must be a finite number >= 0: {position_seconds}",
            device_id,
        )
        trace = self._trace.command(Command.SEEK)
        deadline = self._confirmation_deadline()
        target = self._playback_target(device_id, deadline, trace)
        trace.record("pre_status", media_session_id=target.media_session_id)
        media = target.media
        expected = target.identity
        if media is not None and media.supports_seek is False:
            error = UnsupportedOperationError(
                "the current media does not support seeking", device_id=device_id
            )
            self._record_not_sent(trace, error, target.media_session_id)
            raise error
        self._send(
            trace,
            lambda: self._transport.seek(device_id, position_seconds, target.media_session_id),
            media_session_id=target.media_session_id,
        )
        deadline = self._hold(trace, target.media_session_id, deadline)
        return self._verify(
            Command.SEEK,
            device_id,
            _position_near(position_seconds, self._seek_tolerance, expected),
            f"position near {position_seconds:g}s",
            deadline=deadline,
            trace=trace,
            target_session_id=target.media_session_id,
        )

    def set_volume(self, device_id: DeviceId, level: float) -> CommandResult:
        device_id = _checked_id(device_id)
        level = _number(level, "volume level", device_id)
        _require(0.0 <= level <= 1.0, f"volume level must be within 0.0-1.0: {level}", device_id)
        trace = self._trace.command(Command.SET_VOLUME)
        deadline = self._confirmation_deadline()
        if self._volume_is_fixed(device_id, deadline):
            error = UnsupportedOperationError(
                "this device reports a fixed volume, which cannot be changed",
                device_id=device_id,
            )
            self._record_not_sent(trace, error)
            raise error
        self._send(trace, lambda: self._transport.set_volume(device_id, level))
        return self._verify(
            Command.SET_VOLUME,
            device_id,
            _volume_near(level, self._volume_tolerance),
            f"volume near {level:g}",
            deadline=deadline,
            trace=trace,
        )

    def set_muted(self, device_id: DeviceId, muted: bool) -> CommandResult:
        device_id = _checked_id(device_id)
        # An absolute state: only a real bool, never 0/1, a string or None.
        if not isinstance(muted, bool):
            raise InvalidArgumentError(f"muted must be a boolean: {muted!r}", device_id=device_id)
        trace = self._trace.command(Command.SET_MUTED)
        self._send(trace, lambda: self._transport.set_muted(device_id, muted))
        return self._verify(
            Command.SET_MUTED,
            device_id,
            _muted_is(muted),
            "mute on" if muted else "mute off",
            trace=trace,
        )

    # The TV's own channels (remote and apps) live in `TvChannels`; these methods keep the
    # one control surface the bridge and the MCP adapter call.

    def get_remote_status(self, device_id: DeviceId) -> RemoteObservation:
        return self._channels.get_remote_status(device_id)

    def start_remote_pairing(self, device_id: DeviceId) -> None:
        self._channels.start_remote_pairing(device_id)

    def finish_remote_pairing(self, device_id: DeviceId, code: str) -> None:
        self._channels.finish_remote_pairing(device_id, code)

    def press_remote_key(self, device_id: DeviceId, key: RemoteKey) -> RemoteKeyResult:
        return self._channels.press_remote_key(device_id, key)

    def type_remote_text(self, device_id: DeviceId, text: str) -> RemoteTextResult:
        return self._channels.type_remote_text(device_id, text)

    def list_tv_apps(self, device_id: DeviceId) -> list[TvApp]:
        return self._channels.list_tv_apps(device_id)

    def browse_tv_apps(self, device_id: DeviceId) -> AppListing:
        return self._channels.browse_tv_apps(device_id)

    def launch_tv_app(self, device_id: DeviceId, package: str) -> AppLaunchResult:
        return self._channels.launch_tv_app(device_id, package)

    def pair_tv_apps(self, device_id: DeviceId, pairing_endpoint: str, code: str) -> None:
        self._channels.pair_tv_apps(device_id, pairing_endpoint, code)

    def open_tv_apps_port(self, device_id: DeviceId) -> None:
        self._channels.open_tv_apps_port(device_id)

    def _discovered(self, device_id: DeviceId) -> Device | None:
        """The device of the last discovery with this id, or None."""
        return self._devices.get(_checked_id(device_id))

    def _confirmation_deadline(self) -> float | None:
        timeout = self._confirm_timeout
        return None if timeout is None else self._clock() + timeout

    def _volume_is_fixed(self, device_id: DeviceId, deadline: float | None) -> bool:
        """Whether a pre-command read explicitly reports a fixed volume.

        Only an explicit FIXED blocks the command: a type that is not reported, another type,
        or a read that fails or answers too late leaves the volume possibly adjustable, so the
        command is sent once as before. The read shares the command's single deadline.
        """
        timeout = self._status_timeout
        if deadline is not None:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return False
            timeout = min(timeout, remaining)
        try:
            status = self._transport.get_status(device_id, timeout=timeout)
        except ControlError:
            return False
        if deadline is not None and self._clock() >= deadline:
            return False
        receiver = status.receiver
        return (
            status.connection is ConnectionState.CONNECTED
            and receiver is not None
            and receiver.volume_control_type is VolumeControlType.FIXED
        )

    def _playback_target(
        self, device_id: DeviceId, deadline: float | None, trace: CommandTrace
    ) -> _PlaybackTarget:
        """Capture the one exact Cast session that a playback command may target.

        This read is mandatory even when post-command confirmation is disabled. A read error
        or absent session aborts before transport, because using PyChromecast's mutable cache
        could silently retarget a replacement session.
        """
        timeout = self._status_timeout
        if deadline is not None:
            remaining = deadline - self._clock()
            if remaining <= 0:
                error = OperationTimeoutError(
                    "media session could not be captured within the confirmation budget",
                    device_id=device_id,
                )
                error.delivery = "not_sent"
                self._record_not_sent(trace, error)
                raise error
            timeout = min(timeout, remaining)
        try:
            status = self._transport.get_status(device_id, timeout=timeout)
        except ControlError as error:
            error.delivery = "not_sent"
            self._record_not_sent(trace, error)
            raise
        media = status.media
        session_id = media.media_session_id if media is not None else None
        if (
            status.connection is not ConnectionState.CONNECTED
            or media is None
            or session_id is None
        ):
            unavailable_error = UnsupportedOperationError(
                "the current media did not report a media session id", device_id=device_id
            )
            unavailable_error.delivery = "not_sent"
            self._record_not_sent(trace, unavailable_error)
            raise unavailable_error
        return _PlaybackTarget(_identity_of(media), session_id, media)

    @staticmethod
    def _record_not_sent(
        trace: CommandTrace, error: ControlError, media_session_id: int | None = None
    ) -> None:
        trace.record("command_failed", media_session_id=media_session_id, error_code=error.code)
        trace.record("not_sent", media_session_id=media_session_id, error_code=error.code)

    def _hold(
        self, trace: CommandTrace, media_session_id: int, deadline: float | None
    ) -> float | None:
        """Acceptance only: wait (bounded by the gate) for the observer's release before any
        confirmation read. The command is never resent; a hold gives the confirmation a fresh
        budget, so the time spent held is not counted against it."""
        gate = self._gate
        if gate is None or self._confirm_timeout is None:
            return deadline
        trace.record("confirmation_held", media_session_id=media_session_id)
        try:
            outcome = gate.hold(HoldRequest(trace.request_id, trace.command, media_session_id))
        except Exception:
            trace.record("confirmation_hold_failed", media_session_id=media_session_id)
        else:
            trace.record(
                "confirmation_released" if outcome.released else "confirmation_hold_timeout",
                media_session_id=media_session_id,
                token=outcome.token,
            )
        return self._confirmation_deadline()

    @staticmethod
    def _send(
        trace: CommandTrace,
        operation: Callable[[], None],
        *,
        media_session_id: int | None = None,
    ) -> None:
        """Record one application-side transport invocation without ever retrying it.

        Its outcome: `transport_response`; `not_sent` when the error proves nothing left;
        `command_rejected` when the receiver answered with a rejection; otherwise, including an
        unexpected exception (recorded as `unexpected_error`, then re-raised unchanged),
        `ambiguous_result`: the command may or may not have reached the TV."""
        trace.record("send_attempt", media_session_id=media_session_id)
        try:
            operation()
        except ControlError as error:
            trace.record(
                "command_failed",
                media_session_id=media_session_id,
                error_code=error.code,
            )
            if error.delivery == "not_sent":
                outcome = "not_sent"
            elif isinstance(error, CommandRejectedError):
                outcome = "command_rejected"
            else:
                outcome = "ambiguous_result"
            trace.record(outcome, media_session_id=media_session_id, error_code=error.code)
            raise
        except Exception:
            trace.record("unexpected_error", media_session_id=media_session_id)
            trace.record("ambiguous_result", media_session_id=media_session_id)
            raise
        trace.record("transport_response", media_session_id=media_session_id)

    def _verify(
        self,
        command: Command,
        device_id: DeviceId,
        expected: ExpectedState,
        description: str,
        *,
        deadline: float | None = None,
        trace: CommandTrace,
        target_session_id: int | None = None,
    ) -> CommandResult:
        """Read the TV status until `expected` holds or the time budget is spent.

        The command was already sent by the caller; this only decides CONFIRMED versus
        UNCONFIRMED. `confirm_timeout` is a strict global budget: each status read is
        given only whatever remains of it (`CastTransport.get_status`'s own `timeout`
        contract requires it to never block longer than that), so a slow or hung read cannot
        consume more than the available budget. Playback commands pass a deadline created
        before their identity snapshot, so snapshot and confirmation share one window. A
        status that only arrives after the budget expired is never used to confirm. On
        UNCONFIRMED, `detail` states plainly what the TV last reported, or why nothing
        could be read at all - never a guess at what the missing command might have done.
        """
        timeout = self._confirm_timeout
        if timeout is None:
            trace.record("confirmation_skipped")
            return CommandResult(
                command=command, device_id=device_id, confirmation=Confirmation.NOT_CHECKED
            )

        if deadline is None:
            deadline = self._clock() + timeout
        last_status: DeviceStatus | None = None
        last_error: ControlError | None = None
        last_observation: Observation | None = None
        session_change_recorded = False
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            try:
                status = self._transport.get_status(device_id, timeout=remaining)
            except ControlError as error:
                last_error = error
                trace.record("confirmation_read_failed", error_code=error.code)
            except Exception:
                # Keep the existing behavior (the unexpected exception propagates unchanged),
                # but leave non-sensitive, fail-closed evidence of where the trace stopped.
                trace.record("confirmation_unexpected_error")
                raise
            else:
                last_status, last_error = status, None
                media = status.media
                observed_session_id = None if media is None else media.media_session_id
                if (
                    target_session_id is not None
                    and observed_session_id is not None
                    and observed_session_id != target_session_id
                    and not session_change_recorded
                ):
                    # What the product itself saw: never the independent C6 evidence.
                    trace.record(
                        "confirmation_session_changed", media_session_id=observed_session_id
                    )
                    session_change_recorded = True
                trace.record(
                    "confirmation_status",
                    media_session_id=observed_session_id,
                )
                if self._clock() >= deadline:
                    # The read itself succeeded, but only after the window closed: that
                    # evidence arrived too late to confirm anything.
                    last_observation = Observation(
                        False, "the device answered after the confirmation window expired"
                    )
                    break
                if status.connection is not ConnectionState.CONNECTED:
                    last_observation = Observation(False, f"connection is {status.connection}")
                else:
                    last_observation = expected(status)
                    if last_observation.matched:
                        trace.record(
                            "command_confirmed",
                            media_session_id=None if media is None else media.media_session_id,
                        )
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
        elif last_observation is not None:
            detail = f"command sent; expected {description}, but {last_observation.description}"
        else:
            detail = (
                f"command sent; the confirmation budget ({timeout:g}s) expired "
                "before a status could be read"
            )
        trace.record(
            "command_unconfirmed",
            media_session_id=(
                None
                if last_status is None or last_status.media is None
                else last_status.media.media_session_id
            ),
            error_code=None if last_error is None else last_error.code,
        )
        return CommandResult(
            command=command,
            device_id=device_id,
            confirmation=Confirmation.UNCONFIRMED,
            observed=last_status,
            detail=detail,
        )
