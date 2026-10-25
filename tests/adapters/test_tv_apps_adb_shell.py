"""The TV's installed apps over classic network debugging in pure Python, the phone's path
(`control_tv.adapters.tv_apps_adb_shell`), restored after #99 kept only the `adb` program.

The ADB device, the signer and the key generator are fakes: no network, no real key. The rules
pinned: only launchable apps, by package, sorted and unique; a private key; one connection,
always closed; each failure becomes the control error that tells the owner what to do; no TV
address in any message; nothing but the one read-only query is sent.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import adb_shell.exceptions as adb
import pytest

from control_tv.adapters import tv_apps_adb_shell as tv_apps
from control_tv.adapters.tv_apps import (
    LAUNCHER_QUERY,
    LEGACY_LAUNCHER_QUERY,
    parse_front_app,
    parse_launcher_apps,
)
from control_tv.adapters.tv_apps_adb_shell import KEY_FILE
from control_tv.adapters.tv_apps_adb_shell import AdbShellAppInventory as AdbAppInventory
from control_tv.adapters.tv_apps_adb_shell import adb_shell_installed as apps_installed
from control_tv.domain import (
    CommandRejectedError,
    DebuggingOffError,
    DeviceUnavailableError,
    InvalidArgumentError,
    OperationTimeoutError,
    PairingRequiredError,
    TvApp,
    UnsupportedOperationError,
)

HOST = "192.0.2.10"
QUERY_OUTPUT = """priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false
  com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity
priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false
  com.netflix.ninja/.MainActivity
  com.example.unknown/com.example.unknown.Main$Launcher
  com.netflix.ninja/.OtherActivity
not a component line
"""


class FakeDevice:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    def connect(self, rsa_keys: list[Any], auth_timeout_s: float) -> bool:
        self.record["connect"] = (rsa_keys, auth_timeout_s)
        if self.record.get("connect_error") is not None:
            raise self.record["connect_error"]
        return bool(self.record.get("connected", True))

    def shell(self, command: str, read_timeout_s: float) -> Any:
        self.record.setdefault("commands", []).append(command)
        if self.record.get("shell_error") is not None:
            raise self.record["shell_error"]
        return self.record.get("output", QUERY_OUTPUT)

    def close(self) -> None:
        self.record["closed"] = self.record.get("closed", 0) + 1


def inventory(tmp_path: Path, record: dict[str, Any]) -> AdbAppInventory:
    def device(host: str, port: int, timeout: float) -> FakeDevice:
        record["device"] = (host, port, timeout)
        return FakeDevice(record)

    def keygen(path: str) -> None:
        record["keygen"] = record.get("keygen", 0) + 1
        Path(path).write_bytes(b"private")
        Path(path + ".pub").write_bytes(b"public")

    return AdbAppInventory(
        tmp_path / "remote",
        device_factory=device,
        signer_factory=lambda key: ("signer", key),
        keygen=keygen,
    )


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_only_launchable_apps_are_listed_by_package_sorted_and_unique() -> None:
    assert parse_launcher_apps(QUERY_OUTPUT) == (
        TvApp("com.example.unknown", None),
        TvApp("com.google.android.youtube.tv", "YouTube"),
        TvApp("com.netflix.ninja", "Netflix"),
    )
    assert parse_launcher_apps("") == ()


def test_the_list_is_the_tvs_answer_to_one_read_only_query(tmp_path: Path) -> None:
    record: dict[str, Any] = {}

    apps = inventory(tmp_path, record).list_apps(HOST)

    assert [app.package for app in apps] == [
        "com.example.unknown",
        "com.google.android.youtube.tv",
        "com.netflix.ninja",
    ]
    assert record["device"] == (HOST, 5555, 5.0)
    assert record["commands"] == [LAUNCHER_QUERY]
    assert record["closed"] == 1
    assert record["connect"][1] == 30.0  # time for the owner to allow control-TV on the TV


def test_an_older_tv_uses_the_legacy_read_only_launcher_query(tmp_path: Path) -> None:
    record: dict[str, Any] = {"output": "Unknown command: query-activities"}

    assert inventory(tmp_path, record).list_apps(HOST) == ()

    assert record["commands"] == [LAUNCHER_QUERY, LEGACY_LAUNCHER_QUERY]


def test_the_query_only_reads_the_launcher_activities() -> None:
    assert LAUNCHER_QUERY.startswith("cmd package query-activities --brief ")
    assert "LEANBACK_LAUNCHER" in LAUNCHER_QUERY
    for word in ("install", "uninstall", "am start", "pm ", "rm ", ";", "&&", "|"):
        assert word not in LAUNCHER_QUERY


def test_the_key_is_created_once_and_kept_private(tmp_path: Path) -> None:
    record: dict[str, Any] = {}
    apps = inventory(tmp_path, record)

    apps.list_apps(HOST)
    apps.list_apps(HOST)

    folder = tmp_path / "remote"
    assert record["keygen"] == 1
    assert mode(folder) == 0o700
    assert mode(folder / KEY_FILE) == 0o600
    assert record["connect"][0] == [("signer", str(folder / KEY_FILE))]


@pytest.mark.parametrize(
    ("where", "raised", "expected"),
    [
        ("connect_error", ConnectionRefusedError(f"refused by {HOST}"), DebuggingOffError),
        ("connect_error", adb.TcpTimeoutException(f"{HOST}:5555"), PairingRequiredError),
        ("connect_error", adb.DeviceAuthError("not allowed"), PairingRequiredError),
        ("connect_error", adb.AdbTimeoutError("no answer to AUTH"), PairingRequiredError),
        ("connect_error", OSError(f"unreachable {HOST}"), DeviceUnavailableError),
        ("shell_error", adb.AdbTimeoutError("slow"), OperationTimeoutError),
        ("shell_error", adb.TcpTimeoutException("slow read"), OperationTimeoutError),
        ("shell_error", adb.AdbConnectionError("closed"), DeviceUnavailableError),
        ("shell_error", adb.InvalidResponseError("garbage"), DeviceUnavailableError),
    ],
)
def test_each_failure_says_what_to_do_without_the_tv_address(
    tmp_path: Path, where: str, raised: Exception, expected: type[Exception]
) -> None:
    record: dict[str, Any] = {where: raised}

    with pytest.raises(expected) as excinfo:
        inventory(tmp_path, record).list_apps(HOST)

    assert HOST not in str(excinfo.value)
    assert record["closed"] == 1


def test_debugging_off_explains_how_to_turn_it_on(tmp_path: Path) -> None:
    with pytest.raises(DebuggingOffError, match="developer options"):
        inventory(tmp_path, {"connect_error": ConnectionRefusedError()}).list_apps(HOST)


def test_a_connection_the_tv_did_not_allow_asks_to_allow_it(tmp_path: Path) -> None:
    with pytest.raises(PairingRequiredError, match="allow control-TV on the TV"):
        inventory(tmp_path, {"connected": False}).list_apps(HOST)


def test_an_answer_that_is_not_text_is_not_a_list(tmp_path: Path) -> None:
    with pytest.raises(DeviceUnavailableError):
        inventory(tmp_path, {"output": None}).list_apps(HOST)


@pytest.mark.parametrize("host", ["tv.local", "", None])
def test_a_tv_is_reached_by_ip_address_only(tmp_path: Path, host: Any) -> None:
    record: dict[str, Any] = {}

    with pytest.raises(InvalidArgumentError):
        inventory(tmp_path, record).list_apps(host)

    assert "device" not in record


def test_without_the_library_the_list_says_it_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "adb_shell.exceptions", None)

    with pytest.raises(UnsupportedOperationError, match="not installed"):
        inventory(tmp_path, {}).list_apps(HOST)


def test_the_library_is_reported_installed_without_importing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    assert apps_installed() is True
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert apps_installed() is False

    def broken(name: str) -> None:
        raise ValueError("broken")

    monkeypatch.setattr(importlib.util, "find_spec", broken)
    assert apps_installed() is False


def test_the_real_library_objects_are_built_offline(tmp_path: Path) -> None:
    key = tmp_path / "adbkey"

    tv_apps._default_keygen(str(key))
    signer: Any = tv_apps._default_signer(str(key))
    device: Any = tv_apps._default_device(HOST, 5555, 1.0)

    assert signer.public_key.startswith(b"QAAAA")  # Android's RSA public key encoding
    assert len(signer.Sign(b"x" * 20)) == 256
    assert device.available is False  # built, never connected


# Opening an app --------------------------------------------------------------------------------

FRONT_NETFLIX = "  topResumedActivity=ActivityRecord{2b4c u0 com.netflix.ninja/.MainActivity t12}\n"
FRONT_HOME = (
    "  mResumedActivity: ActivityRecord{6f8 u0 com.google.android.apps.tv.launcherx/"
    ".home.HomeActivity t3}\n"
)


class LaunchDevice(FakeDevice):
    def shell(self, command: str, read_timeout_s: float) -> Any:
        self.record.setdefault("commands", []).append(command)
        if command.startswith("monkey"):
            return self.record.get("monkey_output", "Events injected: 1")
        fronts = self.record.setdefault("fronts", [FRONT_NETFLIX])
        return fronts.pop(0) if len(fronts) > 1 else fronts[0]


def launcher(tmp_path: Path, record: dict[str, Any]) -> AdbAppInventory:
    def device(host: str, port: int, timeout: float) -> LaunchDevice:
        return LaunchDevice(record)

    def keygen(path: str) -> None:
        Path(path).write_bytes(b"private")
        Path(path + ".pub").write_bytes(b"public")

    return AdbAppInventory(
        tmp_path / "remote",
        device_factory=device,
        signer_factory=lambda key: key,
        keygen=keygen,
        sleep=lambda seconds: record.setdefault("sleeps", []).append(seconds),
    )


def test_an_app_is_opened_once_by_its_home_screen_activity_then_read_in_front(
    tmp_path: Path,
) -> None:
    record: dict[str, Any] = {}

    launch = launcher(tmp_path, record).launch_app(HOST, "com.netflix.ninja")

    assert record["commands"] == [
        "monkey -p com.netflix.ninja -c android.intent.category.LEANBACK_LAUNCHER 1",
        "dumpsys activity activities",
    ]
    assert launch.front_app == "com.netflix.ninja"
    assert record["closed"] == 1


def test_the_app_in_front_is_read_again_within_a_short_budget(tmp_path: Path) -> None:
    record: dict[str, Any] = {"fronts": [FRONT_HOME, FRONT_HOME, FRONT_NETFLIX]}

    launch = launcher(tmp_path, record).launch_app(HOST, "com.netflix.ninja")

    assert launch.front_app == "com.netflix.ninja"
    assert [c for c in record["commands"] if c.startswith("monkey")] == [
        "monkey -p com.netflix.ninja -c android.intent.category.LEANBACK_LAUNCHER 1"
    ]
    assert record["sleeps"] == [0.75, 0.75]


def test_an_app_never_reported_in_front_returns_what_the_tv_said_last(tmp_path: Path) -> None:
    record: dict[str, Any] = {"fronts": [FRONT_HOME]}

    launch = launcher(tmp_path, record).launch_app(HOST, "com.netflix.ninja")

    assert launch.front_app == "com.google.android.apps.tv.launcherx"
    assert len([c for c in record["commands"] if c.startswith("dumpsys")]) == 4


def test_a_tv_that_does_not_say_what_is_in_front_is_unknown(tmp_path: Path) -> None:
    record: dict[str, Any] = {"fronts": ["no activity lines here"]}

    assert launcher(tmp_path, record).launch_app(HOST, "com.netflix.ninja").front_app is None


def test_an_app_without_a_home_screen_activity_is_refused_by_the_tv(tmp_path: Path) -> None:
    record: dict[str, Any] = {"monkey_output": "** No activities found to run, monkey aborted."}

    with pytest.raises(CommandRejectedError):
        launcher(tmp_path, record).launch_app(HOST, "com.example.service")

    assert [c for c in record["commands"] if c.startswith("dumpsys")] == []


@pytest.mark.parametrize(
    "package",
    ["", "youtube", "com.example;reboot", "com.example && rm", "1com.example", "a" * 256, None],
)
def test_only_a_package_name_reaches_the_launch_command(tmp_path: Path, package: Any) -> None:
    record: dict[str, Any] = {}

    with pytest.raises(InvalidArgumentError):
        launcher(tmp_path, record).launch_app(HOST, package)

    assert "commands" not in record


def test_the_front_app_parser_reads_only_a_resumed_activity_record() -> None:
    assert parse_front_app(FRONT_NETFLIX) == "com.netflix.ninja"
    assert parse_front_app(FRONT_HOME) == "com.google.android.apps.tv.launcherx"
    assert parse_front_app("mFocusedApp=com.netflix.ninja") is None
    assert parse_front_app(None) is None


def test_a_launch_and_its_checks_share_one_budget(tmp_path: Path) -> None:
    record: dict[str, Any] = {"fronts": [FRONT_HOME]}
    now = [100.0]

    def device(host: str, port: int, timeout: float) -> LaunchDevice:
        class Slow(LaunchDevice):
            def shell(self, command: str, read_timeout_s: float) -> Any:
                record.setdefault("timeouts", []).append(read_timeout_s)
                now[0] += 6.0  # each read takes 6 s on this slow TV
                return super().shell(command, read_timeout_s)

        return Slow(record)

    def keygen(path: str) -> None:
        Path(path).write_bytes(b"private")
        Path(path + ".pub").write_bytes(b"public")

    apps = AdbAppInventory(
        tmp_path / "remote",
        device_factory=device,
        signer_factory=lambda key: key,
        keygen=keygen,
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        clock=lambda: now[0],
    )

    launch = apps.launch_app(HOST, "com.netflix.ninja")

    assert launch.front_app == "com.google.android.apps.tv.launcherx"
    assert now[0] - 100.0 <= 15.0 + 6.0  # never starts a read past the budget
    assert record["timeouts"][0] == 10.0
    assert all(t <= 10.0 for t in record["timeouts"])
    assert len(record["timeouts"]) < 1 + 4  # the slow TV cut the checks short


def test_a_tv_without_the_classic_debugging_port_names_what_is_not_supported(
    tmp_path: Path,
) -> None:
    with pytest.raises(DebuggingOffError, match="Wireless debugging") as excinfo:
        inventory(tmp_path, {"connect_error": ConnectionRefusedError()}).list_apps(HOST)

    assert "port 5555" in str(excinfo.value)


def test_wireless_debugging_pairing_is_refused_with_what_to_do_instead(tmp_path: Path) -> None:
    inventory = AdbAppInventory(tmp_path)

    with pytest.raises(UnsupportedOperationError, match="port 5555"):
        inventory.pair(HOST, "192.0.2.10:37000", "123456")
