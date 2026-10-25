"""The TV's apps over its classic network debugging, in pure Python (`adb-shell`).

The Ubuntu app lists and opens the TV's apps through the `adb` program (`tv_apps`), which also
pairs with "Wireless debugging". A phone has no `adb` program: there the embedded app uses this
adapter, which speaks the classic ADB protocol itself on the TV's port 5555 (the TV's "USB
debugging" when it also accepts the local network). It does not speak "Wireless debugging"
pairing; `pair` says so. The first connection makes the TV ask whether to allow control-TV's
key; the owner accepts on the TV. It sends only the same three fixed commands as `tv_apps`: the
list of launchable apps and the app in front (both read-only), and one launch event for an app
the person chose. It never installs, removes or changes anything.

Rules this adapter keeps:
- The key that identifies control-TV to the TV lives in the same private directory as the
  remote channel's credentials (0700), its private half 0600.
- One short connection per listing or launch, always closed; a launch is sent once.
- Error messages never contain a TV address.
"""

from __future__ import annotations

import importlib.util
import os
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from control_tv.adapters.tv_apps import (
    DEFAULT_FRONT_INTERVAL,
    DEFAULT_FRONT_READS,
    DEFAULT_LAUNCH_BUDGET,
    FRONT_QUERY,
    LAUNCH_COMMAND,
    LAUNCHER_QUERY,
    _checked_host,
    checked_package,
    parse_front_app,
    parse_launcher_apps,
)
from control_tv.domain import (
    AppLaunch,
    CommandRejectedError,
    DebuggingOffError,
    DeviceUnavailableError,
    OperationTimeoutError,
    PairingRequiredError,
    TvApp,
    UnsupportedOperationError,
)

T = TypeVar("T")

ADB_PORT = 5555
KEY_FILE = "adbkey"
DEFAULT_CONNECT_TIMEOUT = 5.0
# How long the owner has to accept control-TV's key on the TV the first time.
DEFAULT_AUTH_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 10.0


def adb_shell_installed() -> bool:
    try:
        return importlib.util.find_spec("adb_shell") is not None
    except (ImportError, ValueError):
        return False


class AdbShellDevice(Protocol):
    """The part of adb-shell's `AdbDeviceTcp` this module uses."""

    def connect(self, rsa_keys: list[object], auth_timeout_s: float) -> bool: ...

    def shell(self, command: str, read_timeout_s: float) -> object: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class AdbErrors:
    """adb-shell's exception classes, loaded with the optional library."""

    device_auth: type[Exception]
    adb_timeout: type[Exception]
    tcp_timeout: type[Exception]
    adb_connection: type[Exception]
    invalid_response: type[Exception]


# The signer is opaque here: adb-shell builds it from the key file and uses it itself.
DeviceFactory = Callable[[str, int, float], AdbShellDevice]
SignerFactory = Callable[[str], object]
KeyGenerator = Callable[[str], None]


def _library() -> AdbErrors:
    try:
        import adb_shell.exceptions as exceptions
    except ImportError:
        raise UnsupportedOperationError(
            "listing the TV's apps is not installed (control-tv[tvremote])"
        ) from None
    return AdbErrors(
        device_auth=exceptions.DeviceAuthError,
        adb_timeout=exceptions.AdbTimeoutError,
        tcp_timeout=exceptions.TcpTimeoutException,
        adb_connection=exceptions.AdbConnectionError,
        invalid_response=exceptions.InvalidResponseError,
    )


def _default_device(host: str, port: int, timeout: float) -> AdbShellDevice:
    from adb_shell.adb_device import AdbDeviceTcp

    device: AdbShellDevice = AdbDeviceTcp(host, port, default_transport_timeout_s=timeout)
    return device


def _default_signer(key_path: str) -> object:
    from adb_shell.auth.sign_cryptography import CryptographySigner

    return CryptographySigner(key_path)


def _default_keygen(key_path: str) -> None:
    from adb_shell.auth.keygen import keygen

    keygen(key_path)


class AdbShellAppInventory:
    """Lists a TV's launchable apps over its network debugging, one short connection each."""

    def __init__(
        self,
        credentials_dir: Path,
        *,
        port: int = ADB_PORT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        auth_timeout: float = DEFAULT_AUTH_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        device_factory: DeviceFactory = _default_device,
        signer_factory: SignerFactory = _default_signer,
        keygen: KeyGenerator = _default_keygen,
        front_reads: int = DEFAULT_FRONT_READS,
        front_interval: float = DEFAULT_FRONT_INTERVAL,
        sleep: Callable[[float], None] = time.sleep,
        launch_budget: float = DEFAULT_LAUNCH_BUDGET,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dir = Path(credentials_dir)
        self._port = port
        self._connect_timeout = connect_timeout
        self._auth_timeout = auth_timeout
        self._read_timeout = read_timeout
        self._device_factory = device_factory
        self._signer_factory = signer_factory
        self._keygen = keygen
        self._front_reads = max(1, front_reads)
        self._front_interval = front_interval
        self._sleep = sleep
        self._launch_budget = launch_budget
        self._clock = clock

    def list_apps(self, host: str) -> tuple[TvApp, ...]:
        """The TV's launchable apps. Raises a control error with a plain reason otherwise."""

        def query(device: AdbShellDevice) -> tuple[TvApp, ...]:
            output = device.shell(LAUNCHER_QUERY, read_timeout_s=self._read_timeout)
            if not isinstance(output, str):
                raise DeviceUnavailableError("the TV returned no list of apps")
            return parse_launcher_apps(output)

        return self._session(host, query)

    def launch_app(self, host: str, package: str) -> AppLaunch:
        """Open the app's home-screen activity once, then read which app the TV has in front;
        the launch and those reads share one budget (`launch_budget`)."""
        package = checked_package(package)

        def launch(device: AdbShellDevice) -> AppLaunch:
            deadline = self._clock() + self._launch_budget

            def budget() -> float:
                return min(self._read_timeout, deadline - self._clock())

            output = device.shell(
                LAUNCH_COMMAND.format(package=package), read_timeout_s=max(budget(), 0.1)
            )
            if isinstance(output, str) and "No activities found" in output:
                raise CommandRejectedError("the TV has no home-screen activity for this app")
            front: str | None = None
            for attempt in range(self._front_reads):
                if attempt:
                    if budget() <= self._front_interval:
                        break  # the launch was sent; what the TV said last is the answer
                    self._sleep(self._front_interval)
                if budget() <= 0:
                    break
                front = parse_front_app(device.shell(FRONT_QUERY, read_timeout_s=budget()))
                if front == package:
                    break
            return AppLaunch(package=package, front_app=front)

        return self._session(host, launch)

    def pair(self, host: str, pairing_endpoint: str, code: str) -> None:
        """Wireless debugging pairing needs the `adb` program, which this device lacks."""
        del host, pairing_endpoint, code
        raise UnsupportedOperationError(
            "pairing with Wireless debugging needs the computer: on this device, turn on the "
            "TV's USB debugging on the network (port 5555) instead"
        )

    def _session(self, host: str, action: Callable[[AdbShellDevice], T]) -> T:
        """One short connection: connect (the owner may have to allow control-TV), act, close."""
        host = _checked_host(host)
        errors = _library()
        signer = self._signer_factory(str(self._key()))
        device = self._device_factory(host, self._port, self._connect_timeout)
        allow = "allow control-TV on the TV when it asks, then try again"
        try:
            try:
                # A TV that asks the owner to allow the key waits up to `auth_timeout`; no
                # answer in time is the owner not having allowed it (yet), not a dead TV. Once
                # the socket is open, adb-shell reports a slow answer as TcpTimeoutException.
                if not device.connect(rsa_keys=[signer], auth_timeout_s=self._auth_timeout):
                    raise PairingRequiredError(allow)
            except (errors.device_auth, errors.adb_timeout, errors.tcp_timeout):
                raise PairingRequiredError(allow) from None
            return action(device)
        except ConnectionRefusedError:
            # Nothing listens on the port: the classic network debugging is off.
            raise DebuggingOffError(
                "the TV accepts no debugging connection on port 5555: turn on USB debugging "
                "in the TV's developer options; a TV that only offers Wireless debugging (with a "
                "pairing code) is reached from the computer, not from this phone"
            ) from None
        except (errors.adb_timeout, errors.tcp_timeout):
            # Connected and allowed, then a command was slow: for a launch it may have run.
            raise OperationTimeoutError("the TV did not answer in time") from None
        except (errors.adb_connection, errors.invalid_response, OSError):
            raise DeviceUnavailableError(
                "the TV's debugging service could not be reached"
            ) from None
        finally:
            with suppress(OSError):
                device.close()

    def _key(self) -> Path:
        """control-TV's ADB key, created once in the private directory."""
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)
        key = self._dir / KEY_FILE
        public = self._dir / (KEY_FILE + ".pub")
        if not (key.is_file() and public.is_file()):
            self._keygen(str(key))
        os.chmod(key, 0o600)
        return key
