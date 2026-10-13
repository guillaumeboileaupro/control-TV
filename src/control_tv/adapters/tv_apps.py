"""The apps installed on a Google TV, listed over its network debugging (ADB).

The TV's remote channel cannot list the apps, and its app links are unreliable for many apps.
The only way found to list and open the apps really installed is the TV's own debugging
service, which the owner turns on in the TV's developer options. Supported: TVs whose "USB
debugging" also accepts connections on the local network, port 5555 (the classic ADB
connection). Not supported yet: TVs (Android 13 and later) that only offer "Wireless
debugging", which pairs with a code on a changing port over TLS; the pinned `adb-shell` does
not speak that protocol. The first connection makes the TV ask whether to allow control-TV's
key; the owner accepts on the TV. This module sends only three fixed commands, built from a
checked package name where one is needed: the list of launchable apps and the app in front
(both read-only), and one launch event for an app the person chose. It never installs, removes
or changes anything.

Rules this adapter keeps:

- The key that identifies control-TV to the TV lives in the same private directory as the
  remote channel's credentials (0700), its private half 0600.
- One short connection per listing or launch, always closed; a launch is sent once.
- Only the apps the TV's home screen can launch (the Leanback launcher activities) are
  listed, by package name, sorted, without duplicates.
- Error messages never contain a TV address.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import os
import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from control_tv.domain import (
    AppLaunch,
    CommandRejectedError,
    DeviceUnavailableError,
    InvalidArgumentError,
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
# The activities the TV's home screen can launch: one line per activity, `package/activity`.
LAUNCHER_QUERY = (
    "cmd package query-activities --brief "
    "-a android.intent.action.MAIN -c android.intent.category.LEANBACK_LAUNCHER"
)
# Opens the app's home-screen activity, as the home screen does: one event, nothing else.
LAUNCH_COMMAND = "monkey -p {package} -c android.intent.category.LEANBACK_LAUNCHER 1"
# Which activity is in front (read-only).
FRONT_QUERY = "dumpsys activity activities"
DEFAULT_FRONT_READS = 4
# Everything a launch does once connected (the launch event and the reads of the app in front)
# shares this one budget, so that connect + allow + launch stays within the caller's bound.
DEFAULT_LAUNCH_BUDGET = 15.0
DEFAULT_FRONT_INTERVAL = 0.75
_FRONT = re.compile(
    r"(?:topResumedActivity|mResumedActivity|ResumedActivity)[:=]\s*ActivityRecord\{\S+ \S+ "
    r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/"
)
# An Android package name: dot-separated segments of letters, digits and underscores, the first
# starting with a letter. Anything else is refused before a command is built from it.
PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")
_COMPONENT = re.compile(r"^([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+$")

# Display names control-TV knows for common packages; any other app shows its package name.
KNOWN_NAMES = {
    "com.google.android.youtube.tv": "YouTube",
    "com.google.android.youtube.tvkids": "YouTube Kids",
    "com.google.android.youtube.tvmusic": "YouTube Music",
    "com.netflix.ninja": "Netflix",
    "com.amazon.amazonvideo.livingroom": "Prime Video",
    "com.disney.disneyplus": "Disney+",
    "com.spotify.tv.android": "Spotify",
    "com.plexapp.android": "Plex",
    "org.videolan.vlc": "VLC",
    "com.google.android.play.games": "Google Play Games",
    "com.android.vending": "Google Play Store",
    "com.google.android.apps.tv.launcherx": "Google TV home",
    "com.google.android.tvrecommendations": "Recommendations",
    "com.android.tv.settings": "Settings",
    "com.canal.android.canal": "CANAL+",
    "fr.francetv.pluzz": "france.tv",
    "fr.m6.m6replay.by": "M6+",
    "fr.tf1.mytf1": "TF1+",
    "tv.arte.plus7": "ARTE",
    "com.molotov.app": "Molotov",
    "com.apple.atve.androidtv.appletv": "Apple TV",
    "com.mubi": "MUBI",
}


class AdbDevice(Protocol):
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
DeviceFactory = Callable[[str, int, float], AdbDevice]
SignerFactory = Callable[[str], object]
KeyGenerator = Callable[[str], None]


def parse_launcher_apps(output: str) -> tuple[TvApp, ...]:
    """The launchable apps in the output of `LAUNCHER_QUERY`, sorted by package, unique."""
    packages = set()
    for line in output.splitlines():
        match = _COMPONENT.match(line.strip())
        if match:
            packages.add(match.group(1))
    return tuple(TvApp(package=p, name=KNOWN_NAMES.get(p)) for p in sorted(packages))


def checked_package(package: object) -> str:
    """A well-formed Android package name, never a free command or a shell fragment."""
    if not isinstance(package, str) or len(package) > 255 or not PACKAGE_NAME.fullmatch(package):
        raise InvalidArgumentError("not an Android package name")
    return package


def parse_front_app(output: object) -> str | None:
    """The package of the activity the TV reports in front, or None when it does not say."""
    if not isinstance(output, str):
        return None
    match = _FRONT.search(output)
    return match.group(1) if match else None


def apps_installed() -> bool:
    """Whether this build embeds the library that lists apps; checked without importing it."""
    try:
        return importlib.util.find_spec("adb_shell") is not None
    except (ImportError, ValueError):
        return False


def _checked_host(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host))
    except (TypeError, ValueError):
        raise InvalidArgumentError("the TV address is not an IP address") from None


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


def _default_device(host: str, port: int, timeout: float) -> AdbDevice:
    from adb_shell.adb_device import AdbDeviceTcp

    device: AdbDevice = AdbDeviceTcp(host, port, default_transport_timeout_s=timeout)
    return device


def _default_signer(key_path: str) -> object:
    from adb_shell.auth.sign_cryptography import CryptographySigner

    return CryptographySigner(key_path)


def _default_keygen(key_path: str) -> None:
    from adb_shell.auth.keygen import keygen

    keygen(key_path)


class AdbAppInventory:
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

        def query(device: AdbDevice) -> tuple[TvApp, ...]:
            output = device.shell(LAUNCHER_QUERY, read_timeout_s=self._read_timeout)
            if not isinstance(output, str):
                raise DeviceUnavailableError("the TV returned no list of apps")
            return parse_launcher_apps(output)

        return self._session(host, query)

    def launch_app(self, host: str, package: str) -> AppLaunch:
        """Open the app's home-screen activity once, then read which app the TV has in front.

        The launch is one `monkey` event for the Leanback launcher activity of `package`, the
        way the TV's own home screen opens it. The TV says when it has no such activity, and
        then nothing was opened. The app in front is read (read-only) a few times; the launch
        and those reads share one budget (`launch_budget`), and whatever the TV reported last
        is returned when it runs out.
        """
        package = checked_package(package)

        def launch(device: AdbDevice) -> AppLaunch:
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

    def _session(self, host: str, action: Callable[[AdbDevice], T]) -> T:
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
            raise UnsupportedOperationError(
                "the TV accepts no debugging connection on port 5555: turn on USB debugging "
                "in the TV's developer options; a TV that only offers Wireless debugging (with "
                "a pairing code) is not supported yet"
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
