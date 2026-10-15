"""Interfaces between the control layer, its callers and the Cast transport.

Both interfaces are synchronous: the Python Chromecast ecosystem is synchronous, and an async
caller (MCP, the Tauri boundary) can wrap the core with `asyncio.to_thread`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from control_tv.domain import (
    AppLaunch,
    AppLaunchResult,
    CommandResult,
    Device,
    DeviceId,
    DeviceStatus,
    KeyPress,
    MediaRequest,
    RemoteKey,
    RemoteKeyResult,
    RemoteObservation,
    RemoteTextResult,
    TextEntry,
    TvApp,
)


class CastTransport(Protocol):
    """Low-level Cast operations, implemented once per Cast library.

    Implementations must:
    - translate library failures into `ControlError` subclasses;
    - return from a command method only when the command was delivered, never when it was
      merely requested locally;
    - report in `get_status` only what was observed on the TV;
    - never let a network wait inside `get_status` run longer than the `timeout` it was
      given: raise (typically `OperationTimeoutError` or `DeviceUnavailableError`) rather
      than exceed it, so that a caller enforcing its own deadline (see
      `ControlService._verify`) can bound every single read by its own remaining budget
      and a slow or hung device can never itself blow past that budget.

    A command method says nothing about whether the TV reached the requested state; that is
    verified by reading `get_status`.
    """

    def discover(self, *, timeout: float) -> Sequence[Device]: ...

    def get_status(self, device_id: DeviceId, *, timeout: float) -> DeviceStatus: ...

    def load_media(self, device_id: DeviceId, request: MediaRequest) -> None: ...

    def play(self, device_id: DeviceId, media_session_id: int) -> None: ...

    def pause(self, device_id: DeviceId, media_session_id: int) -> None: ...

    def stop(self, device_id: DeviceId, media_session_id: int) -> None: ...

    def seek(self, device_id: DeviceId, position_seconds: float, media_session_id: int) -> None: ...

    def set_volume(self, device_id: DeviceId, level: float) -> None: ...

    def set_muted(self, device_id: DeviceId, muted: bool) -> None: ...


class TvRemote(Protocol):
    """A TV's own remote-control channel, reached by the TV's IP address.

    Implementations must translate failures into `ControlError` subclasses, send a key at
    most once, and report only what the TV itself reported.
    """

    def start_pairing(self, host: str) -> None: ...

    def finish_pairing(self, host: str, code: str) -> None: ...

    def observe(self, host: str) -> RemoteObservation: ...

    def press(self, host: str, key: RemoteKey) -> KeyPress: ...

    def type_text(self, host: str, text: str) -> TextEntry: ...


class TvApps(Protocol):
    """The apps installed on a TV, reached by the TV's IP address.

    Implementations must translate failures into `ControlError` subclasses, list only what the
    TV itself reported, launch an app at most once per call, and report the app in front only
    as the TV reported it.
    """

    def list_apps(self, host: str) -> tuple[TvApp, ...]: ...

    def launch_app(self, host: str, package: str) -> AppLaunch: ...


class TvControl(Protocol):
    """The authoritative control surface. The bridge (window, Android app) and the MCP adapter
    call this, never a concrete service.

    Every command returns a `CommandResult` once the command was sent, and raises a
    `ControlError` when it could not be sent. `CommandResult.confirmation` says whether the TV
    was then observed in the requested state.
    """

    def discover_devices(self, *, timeout: float = ...) -> list[Device]: ...

    def get_status(self, device_id: DeviceId) -> DeviceStatus: ...

    def load_media(self, device_id: DeviceId, request: MediaRequest) -> CommandResult: ...

    def play(self, device_id: DeviceId) -> CommandResult: ...

    def pause(self, device_id: DeviceId) -> CommandResult: ...

    def stop(self, device_id: DeviceId) -> CommandResult: ...

    def seek(self, device_id: DeviceId, position_seconds: float) -> CommandResult: ...

    def set_volume(self, device_id: DeviceId, level: float) -> CommandResult: ...

    def set_muted(self, device_id: DeviceId, muted: bool) -> CommandResult: ...

    # The TV's own remote channel and its installed apps, beside Cast.

    def get_remote_status(self, device_id: DeviceId) -> RemoteObservation: ...

    def start_remote_pairing(self, device_id: DeviceId) -> None: ...

    def finish_remote_pairing(self, device_id: DeviceId, code: str) -> None: ...

    def press_remote_key(self, device_id: DeviceId, key: RemoteKey) -> RemoteKeyResult: ...

    def type_remote_text(self, device_id: DeviceId, text: str) -> RemoteTextResult: ...

    def list_tv_apps(self, device_id: DeviceId) -> list[TvApp]: ...

    def launch_tv_app(self, device_id: DeviceId, package: str) -> AppLaunchResult: ...
