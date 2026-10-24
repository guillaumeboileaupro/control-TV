"""TV applications reached through the owner's existing ADB server.

Platform tools own pairing keys, TLS and mDNS. This adapter never picks the first connected
device: it resolves the selected TV's current address to exactly one ADB serial per operation.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from control_tv.domain import (
    AppLaunch,
    CommandRejectedError,
    DebuggingOffError,
    DeviceUnavailableError,
    InvalidArgumentError,
    OperationTimeoutError,
    PairingRequiredError,
    TvApp,
    UnsupportedOperationError,
)

DEFAULT_TIMEOUT = 10.0
DEFAULT_FRONT_READS = 4
DEFAULT_FRONT_INTERVAL = 0.75
DEFAULT_LAUNCH_BUDGET = 15.0
DEFAULT_PAIR_CONNECT_ATTEMPTS = 4
DEFAULT_PAIR_CONNECT_INTERVAL = 0.5
LAUNCHER_QUERY = (
    "cmd package query-activities --brief -a android.intent.action.MAIN "
    "-c android.intent.category.LEANBACK_LAUNCHER"
)
LAUNCH_COMMAND = "monkey -p {package} -c android.intent.category.LEANBACK_LAUNCHER 1"
FRONT_QUERY = "dumpsys activity activities"
PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+")
_COMPONENT = re.compile(r"^([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+$")
_FRONT = re.compile(
    r"(?:topResumedActivity|mResumedActivity|ResumedActivity)[:=]\s*"
    r"ActivityRecord\{\S+ \S+ ([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/"
)
_ENDPOINT = re.compile(r"^(\[[0-9A-Fa-f:]+\]|[^:\s]+):(\d{1,5})$")
_PAIRING_CODE = re.compile(r"\d{6}")
_ADB_VERSION = re.compile(r"(?m)^Version (\d+)\.(\d+)\.(\d+)(?:[-\s]|$)")
MIN_WIRELESS_PAIRING_VERSION = (30, 0, 0)

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
    "com.cbs.ca": "Paramount+",
    "com.google.android.videos": "Google TV",
    "com.wbd.stream": "HBO Max",
    "fr.francetv.zouzous": "Okoo",
    "fr.m6.m6replay": "M6+",
    "fr.tf1.mytf1.free": "TF1+",
    "fr.tfou.max": "TFOU MAX",
    "tv.twitch.android.app": "Twitch",
}


@dataclass(frozen=True, slots=True)
class AdbDevice:
    serial: str
    state: str


@dataclass(frozen=True, slots=True)
class MdnsService:
    serial: str
    kind: str
    endpoint: str


Completed = subprocess.CompletedProcess[str]
Runner = Callable[[Sequence[str], str | None, float], Completed]


def _run_adb(command: Sequence[str], stdin: str | None, timeout: float) -> Completed:
    return subprocess.run(
        command, input=stdin, text=True, capture_output=True, check=False, timeout=timeout
    )


def parse_launcher_apps(output: str) -> tuple[TvApp, ...]:
    packages = {
        m.group(1)
        for line in output.splitlines()
        if (m := _COMPONENT.match(line.strip())) is not None
    }
    return tuple(TvApp(package=p, name=KNOWN_NAMES.get(p)) for p in sorted(packages))


def checked_package(package: object) -> str:
    if not isinstance(package, str) or len(package) > 255 or not PACKAGE_NAME.fullmatch(package):
        raise InvalidArgumentError("not an Android package name")
    return package


def parse_front_app(output: object) -> str | None:
    if not isinstance(output, str):
        return None
    match = _FRONT.search(output)
    return match.group(1) if match else None


def parse_devices(output: str) -> tuple[AdbDevice, ...]:
    found = []
    for raw in output.splitlines():
        fields = raw.strip().split()
        if len(fields) >= 2 and not raw.startswith(("List of devices", "*")):
            found.append(AdbDevice(fields[0], fields[1]))
    return tuple(found)


def parse_mdns_services(output: str) -> tuple[MdnsService, ...]:
    found = []
    for raw in output.splitlines():
        fields = raw.split()
        if len(fields) < 3 or not fields[-2].startswith("_adb-tls-"):
            continue
        kind, endpoint = fields[-2].rstrip("."), fields[-1]
        if _ENDPOINT.fullmatch(endpoint):
            instance = fields[-3].rstrip(".")
            found.append(MdnsService(f"{instance}.{kind}", kind, endpoint))
    return tuple(found)


def apps_installed() -> bool:
    return shutil.which("adb") is not None


def _checked_host(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host))
    except (TypeError, ValueError):
        raise InvalidArgumentError("the TV address is not an IP address") from None


def _endpoint_host(endpoint: str) -> str | None:
    match = _ENDPOINT.fullmatch(endpoint)
    if match is None:
        return None
    try:
        return str(ipaddress.ip_address(match.group(1).strip("[]")))
    except ValueError:
        return None


class AdbAppInventory:
    """Lists and opens apps through one unambiguous device of the local ADB server."""

    def __init__(
        self,
        credentials_dir: Path | None = None,
        *,
        adb: str = "adb",
        timeout: float = DEFAULT_TIMEOUT,
        run: Runner = _run_adb,
        front_reads: int = DEFAULT_FRONT_READS,
        front_interval: float = DEFAULT_FRONT_INTERVAL,
        sleep: Callable[[float], None] = time.sleep,
        launch_budget: float = DEFAULT_LAUNCH_BUDGET,
        clock: Callable[[], float] = time.monotonic,
        pair_connect_attempts: int = DEFAULT_PAIR_CONNECT_ATTEMPTS,
        pair_connect_interval: float = DEFAULT_PAIR_CONNECT_INTERVAL,
    ) -> None:
        del credentials_dir  # Platform tools own and preserve ~/.android/adbkey.
        self._adb, self._timeout, self._run = adb, timeout, run
        self._front_reads, self._front_interval = max(1, front_reads), front_interval
        self._sleep, self._launch_budget, self._clock = sleep, launch_budget, clock
        self._pair_connect_attempts = max(1, pair_connect_attempts)
        self._pair_connect_interval = max(0.0, pair_connect_interval)

    def list_apps(self, host: str) -> tuple[TvApp, ...]:
        return parse_launcher_apps(self._shell(self._resolve(host), LAUNCHER_QUERY).stdout)

    def launch_app(self, host: str, package: str) -> AppLaunch:
        package, serial = checked_package(package), self._resolve(host)
        deadline = self._clock() + self._launch_budget
        output = self._shell(serial, LAUNCH_COMMAND.format(package=package)).stdout
        if "No activities found" in output or "monkey aborted" in output:
            raise CommandRejectedError("the TV has no home-screen activity for this app")
        front = None
        for attempt in range(self._front_reads):
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            if attempt:
                if remaining <= self._front_interval:
                    break
                self._sleep(self._front_interval)
            front = parse_front_app(self._shell(serial, FRONT_QUERY, timeout=remaining).stdout)
            if front == package:
                break
        return AppLaunch(package=package, front_app=front)

    def pair(self, host: str, pairing_endpoint: str, code: str) -> None:
        """Pair once without putting the temporary code in argv, logs or retained state."""
        host = _checked_host(host)
        endpoint = self._checked_endpoint(pairing_endpoint, expected_host=host)
        if not isinstance(code, str) or _PAIRING_CODE.fullmatch(code) is None:
            raise InvalidArgumentError("the Wireless debugging pairing code must have 6 digits")
        self._require_wireless_pairing_version()
        help_result = self._command(["help"])
        if help_result.returncode != 0 or not re.search(
            r"(?m)^\s*adb pair\b|^\s*pair\s+HOST", help_result.stdout + help_result.stderr
        ):
            raise UnsupportedOperationError(
                "this adb does not support Wireless debugging pairing; "
                "install current Android SDK Platform Tools"
            )
        result = self._command(["pair", endpoint], stdin=f"{code}\n")
        if result.returncode != 0 or "Successfully paired" not in result.stdout:
            raise CommandRejectedError(
                "the TV refused or expired the Wireless debugging pairing code"
            )
        # Pairing and the separate connect service are asynchronous on Android. Never replay the
        # one-time pairing operation: wait a small, bounded number of times for the connection
        # service instead.
        last_error: DebuggingOffError | PairingRequiredError | UnsupportedOperationError | None = (
            None
        )
        for attempt in range(self._pair_connect_attempts):
            try:
                self._resolve(host)
                return
            except (DebuggingOffError, PairingRequiredError, UnsupportedOperationError) as error:
                last_error = error
                if attempt + 1 < self._pair_connect_attempts:
                    self._sleep(self._pair_connect_interval)
        raise DeviceUnavailableError(
            "Wireless debugging paired, but its connection service was not advertised in time"
        ) from last_error

    def _require_wireless_pairing_version(self) -> None:
        result = self._command(["version"])
        if result.returncode != 0:
            raise UnsupportedOperationError("the installed adb executable is not functional")
        match = _ADB_VERSION.search(result.stdout + "\n" + result.stderr)
        if match is None:
            raise UnsupportedOperationError(
                "the installed adb version could not be verified for Wireless debugging"
            )
        version = tuple(int(part) for part in match.groups())
        if version < MIN_WIRELESS_PAIRING_VERSION:
            raise UnsupportedOperationError(
                "Wireless debugging pairing requires Android SDK Platform Tools 30.0.0 or newer"
            )

    def _resolve(self, host: str) -> str:
        host = _checked_host(host)
        devices = parse_devices(self._required(["devices", "-l"]).stdout)
        services_result = self._command(["mdns", "services"])
        mdns_available = services_result.returncode == 0
        services = parse_mdns_services(services_result.stdout) if mdns_available else ()
        serials = self._matching_serials(host, devices, services)
        if len(serials) == 1:
            return serials[0]
        if len(serials) > 1:
            raise DeviceUnavailableError(
                "more than one ADB connection matches the selected TV; "
                "disconnect the stale connection and refresh"
            )
        connect = [
            s
            for s in services
            if s.kind == "_adb-tls-connect._tcp" and _endpoint_host(s.endpoint) == host
        ]
        if len(connect) > 1:
            raise DeviceUnavailableError(
                "more than one Wireless debugging service matches the selected TV"
            )
        if len(connect) == 1:
            result = self._command(["connect", connect[0].endpoint])
            if result.returncode == 0 and "failed" not in result.stdout.lower():
                devices = parse_devices(self._required(["devices", "-l"]).stdout)
                serials = self._matching_serials(host, devices, services)
                if len(serials) == 1:
                    return serials[0]
        if any(
            s.kind == "_adb-tls-pairing._tcp" and _endpoint_host(s.endpoint) == host
            for s in services
        ):
            raise PairingRequiredError(
                "this TV offers Wireless debugging but is not connected; pair it in control-TV"
            )
        if any(
            s.kind.startswith("_adb-tls-") and _endpoint_host(s.endpoint) == host for s in services
        ):
            raise DeviceUnavailableError(
                "the selected TV advertised Wireless debugging, but its TLS connection failed"
            )
        # Classic network debugging does not advertise mDNS. A fresh/restarted ADB server has
        # forgotten it, so make one bounded connection attempt on its conventional port. Never
        # apply this fallback to a host that advertised a TLS service with a dynamic port.
        classic_endpoint = f"{host}:5555"
        result = self._command(["connect", classic_endpoint])
        if result.returncode == 0 and "failed" not in result.stdout.lower():
            devices = parse_devices(self._required(["devices", "-l"]).stdout)
            serials = self._matching_serials(host, devices, services)
            if len(serials) == 1:
                return serials[0]
        if not mdns_available:
            raise UnsupportedOperationError(
                "this adb cannot discover Wireless debugging; install Android SDK "
                "Platform Tools 30.0.0 or newer"
            )
        raise DebuggingOffError(
            "no ADB connection or Wireless debugging service matches the "
            "selected TV; turn on Wireless debugging (or classic network "
            "debugging) and refresh"
        )

    @staticmethod
    def _matching_serials(
        host: str, devices: tuple[AdbDevice, ...], services: tuple[MdnsService, ...]
    ) -> list[str]:
        connected = {d.serial for d in devices if d.state == "device"}
        return sorted(
            serial
            for serial in connected
            if _endpoint_host(serial) == host
            or any(
                s.kind == "_adb-tls-connect._tcp"
                and _endpoint_host(s.endpoint) == host
                and serial in {s.serial, s.serial + "."}
                for s in services
            )
        )

    def _shell(self, serial: str, command: str, *, timeout: float | None = None) -> Completed:
        result = self._command(["-s", serial, "shell", command], timeout=timeout)
        if result.returncode:
            detail = (result.stderr or result.stdout).lower()
            if "unauthorized" in detail:
                raise PairingRequiredError("the selected TV has not authorized this ADB server")
            if any(word in detail for word in ("not found", "offline", "closed")):
                raise DeviceUnavailableError("the selected TV's ADB connection was lost")
            if "permission denied" in detail:
                raise CommandRejectedError("the TV refused the ADB operation")
            raise DeviceUnavailableError("the selected TV did not accept the ADB operation")
        return result

    def _required(self, arguments: Sequence[str]) -> Completed:
        result = self._command(arguments)
        if result.returncode:
            raise DeviceUnavailableError("the local ADB server could not list its devices")
        return result

    def _command(
        self, arguments: Sequence[str], *, stdin: str | None = None, timeout: float | None = None
    ) -> Completed:
        try:
            return self._run(
                [self._adb, *arguments], stdin, min(timeout or self._timeout, self._timeout)
            )
        except FileNotFoundError:
            raise UnsupportedOperationError(
                "Android platform tools (adb) are not installed"
            ) from None
        except subprocess.TimeoutExpired:
            raise OperationTimeoutError("ADB did not answer in time") from None
        except OSError:
            raise DeviceUnavailableError("the local ADB server could not be reached") from None

    @staticmethod
    def _checked_endpoint(endpoint: str, *, expected_host: str) -> str:
        if not isinstance(endpoint, str) or _ENDPOINT.fullmatch(endpoint) is None:
            raise InvalidArgumentError("the pairing address must be an IP address and port")
        if _endpoint_host(endpoint) != expected_host:
            raise InvalidArgumentError("the pairing address is not the selected TV")
        if not 0 < int(endpoint.rsplit(":", 1)[1]) < 65536:
            raise InvalidArgumentError("the pairing port is out of range")
        return endpoint
