"""The TV's own channels beside Cast: its remote (keys, text, pairing) and its app list.

Separate from Cast: a fixed Cast volume does not change what the owner hears, the TV's own
volume does; and Cast cannot list the apps installed on the TV. Each TV is reached at the
address of its last discovery, which `ControlService` owns and hands in as `discovered`: this
class depends on that lookup, not on the Cast service. A key, a text or an app launch is sent
once; a result is `CONFIRMED` only when the TV's own report shows the requested change.
"""

from __future__ import annotations

from collections.abc import Callable

from control_tv.domain import (
    AppLaunchResult,
    Confirmation,
    Device,
    DeviceId,
    DeviceKind,
    DeviceNotFoundError,
    InvalidArgumentError,
    KeyPress,
    RemoteKey,
    RemoteKeyResult,
    RemoteObservation,
    RemoteTextResult,
    TvApp,
    UnsupportedOperationError,
)
from control_tv.ports import TvApps, TvRemote


def _key_outcome(press: KeyPress) -> tuple[Confirmation, str]:
    """Whether the TV's own reports show the effect of the key that was sent."""
    key, before, after = press.key, press.before.volume, press.after.volume
    if key is RemoteKey.POWER:
        return _power_outcome(press.before.is_on, press.after.is_on)
    if key not in (RemoteKey.VOLUME_UP, RemoteKey.VOLUME_DOWN, RemoteKey.VOLUME_MUTE):
        return Confirmation.NOT_CHECKED, "sent; the TV does not report the effect of this key"
    if before is None or after is None:
        reported = "whether its sound is muted" if key is RemoteKey.VOLUME_MUTE else "its volume"
        return Confirmation.UNCONFIRMED, f"sent; the TV did not report {reported}"
    if key is RemoteKey.VOLUME_MUTE:
        now = "muted" if after.muted else "not muted"
        if after.muted != before.muted:
            return Confirmation.CONFIRMED, f"the TV reports its sound {now}"
        return Confirmation.UNCONFIRMED, f"sent; the TV still reports its sound {now}"
    level = f"{after.level}/{after.maximum}"
    if key is RemoteKey.VOLUME_UP and after.level > before.level:
        return Confirmation.CONFIRMED, f"the TV reports volume {level}"
    if key is RemoteKey.VOLUME_DOWN and after.level < before.level:
        return Confirmation.CONFIRMED, f"the TV reports volume {level}"
    return Confirmation.UNCONFIRMED, f"sent; the TV still reports volume {level}"


def _power_outcome(before: bool | None, after: bool | None) -> tuple[Confirmation, str]:
    """Power toggles: confirmed only when the TV's own report changed. A TV in deep standby
    does not answer at all (the connection fails before anything is sent)."""
    if after is None:
        return Confirmation.UNCONFIRMED, "sent; the TV did not report whether it is on"
    now = "on" if after else "off"
    if before is not None and after != before:
        return Confirmation.CONFIRMED, f"the TV reports it is {now}"
    return Confirmation.UNCONFIRMED, f"sent; the TV still reports it is {now}"


class TvChannels:
    """The remote channel and the app list of the discovered TVs."""

    def __init__(
        self,
        discovered: Callable[[DeviceId], Device | None],
        *,
        remote: TvRemote | None = None,
        apps: TvApps | None = None,
    ) -> None:
        self._discovered = discovered
        self._remote = remote
        self._apps = apps

    def get_remote_status(self, device_id: DeviceId) -> RemoteObservation:
        remote, host = self._remote_target(device_id)
        return remote.observe(host)

    def start_remote_pairing(self, device_id: DeviceId) -> None:
        remote, host = self._remote_target(device_id)
        remote.start_pairing(host)

    def finish_remote_pairing(self, device_id: DeviceId, code: str) -> None:
        if not isinstance(code, str):
            raise InvalidArgumentError("the pairing code must be text", device_id=device_id)
        remote, host = self._remote_target(device_id)
        remote.finish_pairing(host, code)

    def press_remote_key(self, device_id: DeviceId, key: RemoteKey) -> RemoteKeyResult:
        if not isinstance(key, RemoteKey):
            raise InvalidArgumentError(f"not an allowed key: {key!r}", device_id=device_id)
        remote, host = self._remote_target(device_id)
        press = remote.press(host, key)
        confirmation, detail = _key_outcome(press)
        return RemoteKeyResult(
            key=key,
            device_id=device_id,
            confirmation=confirmation,
            before=press.before,
            after=press.after,
            detail=detail,
        )

    def type_remote_text(self, device_id: DeviceId, text: str) -> RemoteTextResult:
        """Send text once only when the TV reports an active field. The TV never echoes it,
        so the result is `NOT_CHECKED`; the detail says whether anything was sent."""
        if not isinstance(text, str):
            raise InvalidArgumentError("the text to type must be a string", device_id=device_id)
        remote, host = self._remote_target(device_id)
        entry = remote.type_text(host, text)
        detail = (
            "sent to the TV's active text field; check the TV"
            if entry.field_reported
            else "not sent because the TV did not report an active text field"
        )
        return RemoteTextResult(
            device_id=device_id,
            confirmation=Confirmation.NOT_CHECKED,
            detail=detail,
            field_reported=entry.field_reported,
            observed=entry.observed,
        )

    def list_tv_apps(self, device_id: DeviceId) -> list[TvApp]:
        """The apps installed on the TV that its home screen can launch, as the TV lists them."""
        host = self._tv_host(device_id)
        if self._apps is None:
            raise UnsupportedOperationError(
                "this application cannot list the TV's apps", device_id=device_id
            )
        return list(self._apps.list_apps(host))

    def launch_tv_app(self, device_id: DeviceId, package: str) -> AppLaunchResult:
        """Open an installed app on the TV, once. `CONFIRMED` only when the TV then reports
        that app in front; `NOT_CHECKED` when it does not say."""
        if not isinstance(package, str):
            raise InvalidArgumentError("the app must be a package name", device_id=device_id)
        host = self._tv_host(device_id)
        if self._apps is None:
            raise UnsupportedOperationError(
                "this application cannot open the TV's apps", device_id=device_id
            )
        launch = self._apps.launch_app(host, package)
        front = launch.front_app
        if front == launch.package:
            confirmation, detail = Confirmation.CONFIRMED, "the TV reports the app in front"
        elif front is not None:
            confirmation = Confirmation.UNCONFIRMED
            detail = f"sent; the TV still reports {front} in front"
        else:
            confirmation = Confirmation.NOT_CHECKED
            detail = "sent; the TV did not report the app in front; check the TV"
        return AppLaunchResult(
            package=launch.package,
            device_id=device_id,
            confirmation=confirmation,
            detail=detail,
            front_app=front,
        )

    def pair_tv_apps(self, device_id: DeviceId, pairing_endpoint: str, code: str) -> None:
        """Pair the selected TV's Wireless debugging service using its temporary code."""
        if not isinstance(pairing_endpoint, str) or not isinstance(code, str):
            raise InvalidArgumentError("the pairing address and code must be text")
        host = self._tv_host(device_id)
        if self._apps is None:
            raise UnsupportedOperationError(
                "this application cannot pair with the TV's debugging service",
                device_id=device_id,
            )
        self._apps.pair(host, pairing_endpoint, code)

    def open_tv_apps_port(self, device_id: DeviceId) -> None:
        """Have the selected TV accept the phone's app list on port 5555 (until it restarts)."""
        host = self._tv_host(device_id)
        if self._apps is None:
            raise UnsupportedOperationError(
                "this application cannot reach the TV's debugging service", device_id=device_id
            )
        self._apps.open_classic_port(host)

    def _remote_target(self, device_id: DeviceId) -> tuple[TvRemote, str]:
        if self._remote is None:
            raise UnsupportedOperationError(
                "this application has no TV remote channel", device_id=device_id
            )
        return self._remote, self._tv_host(device_id, remote=True)

    def _tv_host(self, device_id: DeviceId, *, remote: bool = False) -> str:
        """The address of a TV found by the last discovery, never a group or a guess.

        `remote`: the TV's own remote channel is needed, so a TV whose discovery saw that it
        does not offer it is refused. The apps go through the TV's debugging, a separate
        service, so they do not depend on what discovery saw of the remote.
        """
        device = self._discovered(device_id)
        if device is None:
            raise DeviceNotFoundError(
                "this TV was not found by the last discovery", device_id=device_id
            )
        if device.kind is DeviceKind.GROUP:
            raise UnsupportedOperationError(
                "a Cast group is not a TV of its own", device_id=device_id
            )
        if remote and device.android_tv_remote is False:
            raise UnsupportedOperationError(
                "this TV does not offer its own remote channel", device_id=device_id
            )
        return device.host
