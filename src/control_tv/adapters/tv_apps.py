"""The apps installed on a Google TV, listed over its network debugging (ADB).

The TV's remote channel can open an app but cannot list them. The only way found to list the
apps really installed is the TV's own debugging service, which the owner turns on in the TV's
developer options ("USB debugging" on a Google TV also accepts connections on the local
network, port 5555). The first connection makes the TV ask whether to allow control-TV's key;
the owner accepts on the TV. This module only lists: it runs one read-only query and never
installs, starts or changes anything through ADB (apps are opened through the remote channel).

Rules this adapter keeps:

- The key that identifies control-TV to the TV lives in the same private directory as the
  remote channel's credentials (0700), its private half 0600.
- One short connection per listing, always closed.
- Only the apps the TV's home screen can launch (the Leanback launcher activities) are
  listed, by package name, sorted, without duplicates.
- Error messages never contain a TV address.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import os
import re
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from control_tv.domain import (
    DeviceUnavailableError,
    InvalidArgumentError,
    OperationTimeoutError,
    PairingRequiredError,
    TvApp,
    UnsupportedOperationError,
)

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

DeviceFactory = Callable[[str, int, float], Any]
SignerFactory = Callable[[str], Any]
KeyGenerator = Callable[[str], None]


def parse_launcher_apps(output: str) -> tuple[TvApp, ...]:
    """The launchable apps in the output of `LAUNCHER_QUERY`, sorted by package, unique."""
    packages = set()
    for line in output.splitlines():
        match = _COMPONENT.match(line.strip())
        if match:
            packages.add(match.group(1))
    return tuple(TvApp(package=p, name=KNOWN_NAMES.get(p)) for p in sorted(packages))


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


def _library() -> Any:
    try:
        import adb_shell.exceptions as exceptions
    except ImportError:
        raise UnsupportedOperationError(
            "listing the TV's apps is not installed (control-tv[tvremote])"
        ) from None
    return exceptions


def _default_device(host: str, port: int, timeout: float) -> Any:
    from adb_shell.adb_device import AdbDeviceTcp

    return AdbDeviceTcp(host, port, default_transport_timeout_s=timeout)


def _default_signer(key_path: str) -> Any:
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
    ) -> None:
        self._dir = Path(credentials_dir)
        self._port = port
        self._connect_timeout = connect_timeout
        self._auth_timeout = auth_timeout
        self._read_timeout = read_timeout
        self._device_factory = device_factory
        self._signer_factory = signer_factory
        self._keygen = keygen

    def list_apps(self, host: str) -> tuple[TvApp, ...]:
        """The TV's launchable apps. Raises a control error with a plain reason otherwise."""
        host = _checked_host(host)
        errors = _library()
        signer = self._signer_factory(str(self._key()))
        device = self._device_factory(host, self._port, self._connect_timeout)
        allow = "allow control-TV on the TV when it asks, then list the apps again"
        try:
            try:
                # A TV that asks the owner to allow the key waits up to `auth_timeout`; no
                # answer in time is the owner not having allowed it (yet), not a dead TV.
                if not device.connect(rsa_keys=[signer], auth_timeout_s=self._auth_timeout):
                    raise PairingRequiredError(allow)
            except (errors.DeviceAuthError, errors.AdbTimeoutError):
                raise PairingRequiredError(allow) from None
            output = device.shell(LAUNCHER_QUERY, read_timeout_s=self._read_timeout)
        except (ConnectionRefusedError, errors.TcpTimeoutException):
            raise UnsupportedOperationError(
                "the TV's network debugging is off: turn on USB debugging in the TV's "
                "developer options to list its apps"
            ) from None
        except errors.AdbTimeoutError:
            raise OperationTimeoutError("the TV did not answer in time") from None
        except (errors.AdbConnectionError, errors.InvalidResponseError, OSError):
            raise DeviceUnavailableError(
                "the TV's debugging service could not be reached"
            ) from None
        finally:
            with suppress(OSError):
                device.close()
        if not isinstance(output, str):
            raise DeviceUnavailableError("the TV returned no list of apps")
        return parse_launcher_apps(output)

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
