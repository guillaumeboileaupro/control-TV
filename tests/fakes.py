"""Deterministic test doubles: a fake clock and an in-memory fake TV behind `CastTransport`."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from control_tv.domain import (
    ConnectionState,
    ControlError,
    Device,
    DeviceId,
    DeviceNotFoundError,
    DeviceStatus,
    MediaRequest,
    MediaStatus,
    OperationTimeoutError,
    PlaybackState,
    ReceiverStatus,
)

DEVICE_ID = DeviceId("uuid-1")
MOVIE_URL = "http://media.local/movie.mp4"
OBSERVED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class FakeClock:
    """Time only moves when `sleep` is called, so timing assertions are exact."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class TvState:
    """What the fake TV really is, independent of any command sent to it."""

    connection: ConnectionState = ConnectionState.CONNECTED
    playback: PlaybackState = PlaybackState.PLAYING
    content_id: str | None = MOVIE_URL
    position: float = 10.0
    supports_seek: bool | None = True
    volume: float = 0.5
    muted: bool = False
    has_media: bool = True
    """False mimics a receiver that reports no media session at all (as opposed to an
    explicit idle state) - the case a real adapter must not paper over with a fabricated
    `PlaybackState.IDLE`."""


@dataclass
class FakeTransport:
    """In-memory `CastTransport`.

    `effect_delay_polls=N`: the first N status reads after a command still show the old state.
    `ignore_commands`: commands are delivered but the TV never acts on them.
    `fail_commands_with`: commands cannot be delivered and raise this error.
    `discover_error`: discovery cannot run and raises this error.
    `status_errors`: errors raised, in order, by the next `get_status` calls.
    `status_effects`: deterministic external TV mutations applied before successive status
    snapshots, including pre-command reads.
    `clock`: when set, `get_status` can simulate the wall-clock cost of a real network read
    by advancing this clock (via `status_read_delay`/`hang_status_reads`) before returning -
    it should be the same `FakeClock` the `ControlService` under test uses, so both sides
    agree on how much of the confirmation budget a read actually consumed.
    `status_read_delay`: simulated seconds a *successful* `get_status` call takes.
    `status_read_delays`: per-read delays which take precedence over `status_read_delay`.
    `hang_status_reads`: when true, `get_status` always consumes exactly the `timeout` it
    was given and then raises `OperationTimeoutError` - a read that respects its bound but
    never completes usefully within it (as opposed to one that ignores its bound entirely,
    which a spec-compliant transport must never do).
    """

    device_id: DeviceId = DEVICE_ID
    effect_delay_polls: int = 0
    ignore_commands: bool = False
    clears_media_on_stop: bool = False
    fail_commands_with: ControlError | None = None
    discover_error: ControlError | None = None
    status_errors: list[ControlError] = field(default_factory=list)
    status_effects: list[Callable[[], None]] = field(default_factory=list)
    clock: FakeClock | None = None
    status_read_delay: float = 0.0
    hang_status_reads: bool = False
    status_read_delays: list[float] = field(default_factory=list)
    tv: TvState = field(default_factory=TvState)
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    command_attempts: list[str] = field(default_factory=list)
    _pending: tuple[int, Callable[[], None]] | None = None

    def sent(self) -> list[str]:
        """Names of the commands delivered to the TV (status reads excluded)."""
        return [name for name, _ in self.calls if name != "get_status"]

    def attempted(self) -> list[str]:
        """Names of command calls attempted, including calls that failed before delivery."""
        return self.command_attempts.copy()

    def status_reads(self) -> int:
        return sum(1 for name, _ in self.calls if name == "get_status")

    def discover(self, *, timeout: float) -> Sequence[Device]:
        self.calls.append(("discover", (timeout,)))
        if self.discover_error is not None:
            raise self.discover_error
        return [
            Device(id=self.device_id, friendly_name="Living room", host="192.168.1.20", port=8009)
        ]

    def get_status(self, device_id: DeviceId, *, timeout: float) -> DeviceStatus:
        self.calls.append(("get_status", (device_id, timeout)))
        self._require_known(device_id)
        if self.hang_status_reads:
            if self.clock is not None:
                self.clock.sleep(timeout)
            raise OperationTimeoutError(
                f"timed out reading status from {device_id} after {timeout:g}s",
                device_id=device_id,
            )
        if self.status_errors:
            raise self.status_errors.pop(0)
        read_delay = (
            self.status_read_delays.pop(0) if self.status_read_delays else self.status_read_delay
        )
        if self.clock is not None and read_delay:
            self.clock.sleep(read_delay)
        if self._pending is not None:
            polls_left, effect = self._pending
            if polls_left == 0:
                effect()
                self._pending = None
            else:
                self._pending = (polls_left - 1, effect)
        if self.status_effects:
            self.status_effects.pop(0)()
        tv = self.tv
        if tv.connection is not ConnectionState.CONNECTED:
            return DeviceStatus(
                device_id=device_id, connection=tv.connection, observed_at=OBSERVED_AT
            )
        media = (
            MediaStatus(
                playback_state=tv.playback,
                content_id=tv.content_id,
                position_seconds=tv.position,
                supports_seek=tv.supports_seek,
            )
            if tv.has_media
            else None
        )
        return DeviceStatus(
            device_id=device_id,
            connection=tv.connection,
            observed_at=OBSERVED_AT,
            receiver=ReceiverStatus(volume_level=tv.volume, muted=tv.muted),
            media=media,
        )

    def load_media(self, device_id: DeviceId, request: MediaRequest) -> None:
        def effect() -> None:
            self.tv.content_id = request.url
            self.tv.playback = PlaybackState.PLAYING
            self.tv.position = 0.0

        self._command("load_media", (device_id, request), effect)

    def play(self, device_id: DeviceId) -> None:
        self._command("play", (device_id,), self._set("playback", PlaybackState.PLAYING))

    def pause(self, device_id: DeviceId) -> None:
        self._command("pause", (device_id,), self._set("playback", PlaybackState.PAUSED))

    def stop(self, device_id: DeviceId) -> None:
        def effect() -> None:
            if self.clears_media_on_stop:
                self.tv.has_media = False
            else:
                self.tv.playback = PlaybackState.IDLE

        self._command("stop", (device_id,), effect)

    def seek(self, device_id: DeviceId, position_seconds: float) -> None:
        self._command(
            "seek", (device_id, position_seconds), self._set("position", position_seconds)
        )

    def set_volume(self, device_id: DeviceId, level: float) -> None:
        self._command("set_volume", (device_id, level), self._set("volume", level))

    def set_muted(self, device_id: DeviceId, muted: bool) -> None:
        self._command("set_muted", (device_id, muted), self._set("muted", muted))

    def _set(self, attribute: str, value: object) -> Callable[[], None]:
        return lambda: setattr(self.tv, attribute, value)

    def _require_known(self, device_id: DeviceId) -> None:
        if device_id != self.device_id:
            raise DeviceNotFoundError(f"unknown device {device_id}", device_id=device_id)

    def _command(self, name: str, args: tuple[object, ...], effect: Callable[[], None]) -> None:
        self._require_known(DeviceId(str(args[0])))
        self.command_attempts.append(name)
        if self.fail_commands_with is not None:
            raise self.fail_commands_with
        self.calls.append((name, args))
        if self.ignore_commands:
            return
        if self.effect_delay_polls == 0:
            effect()
        else:
            self._pending = (self.effect_delay_polls, effect)
