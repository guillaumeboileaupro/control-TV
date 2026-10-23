"""ADB app inventory tests use a command double and never contact a device."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from control_tv.adapters.tv_apps import (
    LAUNCHER_QUERY,
    AdbAppInventory,
    apps_installed,
    parse_devices,
    parse_front_app,
    parse_launcher_apps,
    parse_mdns_services,
)
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
Answers = dict[tuple[str, ...], list[subprocess.CompletedProcess[str]]]
TLS_SERIAL = "adb-serial-ABC._adb-tls-connect._tcp"
DEVICES_TLS = f"List of devices attached\n{TLS_SERIAL} device product:tv model:TV\n"
DEVICES_TCP = f"List of devices attached\n{HOST}:5555 device product:tv model:TV\n"
MDNS_TLS = (
    f"adb-serial-ABC\t_adb-tls-connect._tcp.\t{HOST}:37123\n"
    f"adb-serial-ABC\t_adb-tls-pairing._tcp.\t{HOST}:40211\n"
)
ADB_VERSION = "Android Debug Bridge version 1.0.41\nVersion 30.0.0-0000000\n"
QUERY_OUTPUT = """com.netflix.ninja/.MainActivity
com.example.video/com.example.video.Main
com.netflix.ninja/.OtherActivity
not a component
"""
FRONT_NETFLIX = "topResumedActivity=ActivityRecord{a u0 com.netflix.ninja/.MainActivity t1}"


def done(stdout: str = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["adb"], code, stdout, stderr)


class FakeRunner:
    def __init__(
        self, answers: dict[tuple[str, ...], list[subprocess.CompletedProcess[str]]]
    ) -> None:
        self.answers = answers
        self.calls: list[tuple[tuple[str, ...], str | None, float]] = []

    def __call__(
        self, command: Sequence[str], stdin: str | None, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        key = tuple(command[1:])
        self.calls.append((tuple(command), stdin, timeout))
        answers = self.answers.get(key)
        if not answers:
            raise AssertionError(f"unexpected command: {key}")
        return answers.pop(0)


def inventory(runner: FakeRunner, **kwargs: Any) -> AdbAppInventory:
    return AdbAppInventory(Path("unused"), run=runner, sleep=lambda _: None, **kwargs)


def listing_answers(
    devices: str = DEVICES_TLS, mdns: str = MDNS_TLS
) -> dict[tuple[str, ...], list[subprocess.CompletedProcess[str]]]:
    return {
        ("devices", "-l"): [done(devices)],
        ("mdns", "services"): [done(mdns)],
        ("-s", TLS_SERIAL if devices == DEVICES_TLS else f"{HOST}:5555", "shell", LAUNCHER_QUERY): [
            done(QUERY_OUTPUT)
        ],
    }


def test_parsers_cover_tcp_tls_mdns_and_launcher_apps() -> None:
    assert parse_devices(DEVICES_TCP)[0].serial == f"{HOST}:5555"
    assert parse_devices(DEVICES_TLS)[0].state == "device"
    assert parse_mdns_services(MDNS_TLS)[0].serial == TLS_SERIAL
    assert parse_launcher_apps(QUERY_OUTPUT) == (
        TvApp("com.example.video", None),
        TvApp("com.netflix.ninja", "Netflix"),
    )


@pytest.mark.parametrize("devices", [DEVICES_TCP, DEVICES_TLS])
def test_selected_tv_resolves_to_classic_or_tls_device(devices: str) -> None:
    runner = FakeRunner(listing_answers(devices))
    assert [a.package for a in inventory(runner).list_apps(HOST)] == [
        "com.example.video",
        "com.netflix.ninja",
    ]
    shell = runner.calls[-1][0]
    assert shell[1:3] == ("-s", TLS_SERIAL if devices == DEVICES_TLS else f"{HOST}:5555")


def test_dynamic_connect_port_is_reconnected_then_used() -> None:
    answers = listing_answers()
    answers[("devices", "-l")] = [done("List of devices attached\n"), done(DEVICES_TLS)]
    answers[("connect", f"{HOST}:37123")] = [done(f"connected to {HOST}:37123")]
    runner = FakeRunner(answers)
    inventory(runner).list_apps(HOST)
    assert ("adb", "connect", f"{HOST}:37123") in [call[0] for call in runner.calls]


def test_classic_tcp_device_is_reconnected_after_a_fresh_adb_server() -> None:
    answers = listing_answers(devices=DEVICES_TCP, mdns="")
    answers[("devices", "-l")] = [done("List of devices attached\n"), done(DEVICES_TCP)]
    answers[("connect", f"{HOST}:5555")] = [done(f"connected to {HOST}:5555")]
    runner = FakeRunner(answers)

    inventory(runner).list_apps(HOST)

    assert [call[0] for call in runner.calls].count(("adb", "connect", f"{HOST}:5555")) == 1


def test_classic_tcp_still_works_when_adb_has_no_mdns_command() -> None:
    answers = listing_answers(devices=DEVICES_TCP, mdns="")
    answers[("mdns", "services")] = [done("", "adb: unknown command mdns", 1)]
    runner = FakeRunner(answers)

    assert [app.package for app in inventory(runner).list_apps(HOST)] == [
        "com.example.video",
        "com.netflix.ninja",
    ]


def test_fresh_classic_tcp_reconnects_when_adb_has_no_mdns_command() -> None:
    answers = listing_answers(devices=DEVICES_TCP, mdns="")
    answers[("devices", "-l")] = [done("List of devices attached\n"), done(DEVICES_TCP)]
    answers[("mdns", "services")] = [done("", "adb: unknown command mdns", 1)]
    answers[("connect", f"{HOST}:5555")] = [done(f"connected to {HOST}:5555")]
    runner = FakeRunner(answers)

    inventory(runner).list_apps(HOST)

    assert [call[0] for call in runner.calls].count(("adb", "connect", f"{HOST}:5555")) == 1


def test_old_adb_without_classic_target_names_the_required_platform_tools() -> None:
    runner = FakeRunner(
        {
            ("devices", "-l"): [done("List of devices attached\n")],
            ("mdns", "services"): [done("", "adb: unknown command mdns", 1)],
            ("connect", f"{HOST}:5555"): [done("failed to connect", code=1)],
        }
    )

    with pytest.raises(UnsupportedOperationError, match=r"Platform Tools 30\.0\.0"):
        inventory(runner).list_apps(HOST)

    assert not any("shell" in call[0] for call in runner.calls)


def test_multiple_devices_never_select_the_first_or_wrong_host() -> None:
    devices = "List of devices attached\n192.0.2.99:5555 device\n" + DEVICES_TLS.split("\n", 1)[1]
    answers = listing_answers(devices)
    answers[("-s", TLS_SERIAL, "shell", LAUNCHER_QUERY)] = [done(QUERY_OUTPUT)]
    runner = FakeRunner(answers)
    inventory(runner).list_apps(HOST)
    assert runner.calls[-1][0][2] == TLS_SERIAL


def test_ambiguous_matches_are_refused_before_shell() -> None:
    devices = DEVICES_TLS + f"{HOST}:5555 device\n"
    runner = FakeRunner(
        {("devices", "-l"): [done(devices)], ("mdns", "services"): [done(MDNS_TLS)]}
    )
    with pytest.raises(DeviceUnavailableError, match="more than one"):
        inventory(runner).list_apps(HOST)


def test_pairing_service_is_not_misreported_as_debugging_off() -> None:
    runner = FakeRunner(
        {
            ("devices", "-l"): [done("List of devices attached\n")],
            ("mdns", "services"): [done(MDNS_TLS.replace("_adb-tls-connect", "_ignored"))],
        }
    )
    with pytest.raises(PairingRequiredError, match="pair it in control-TV"):
        inventory(runner).list_apps(HOST)


def test_no_matching_service_explains_wireless_debugging() -> None:
    runner = FakeRunner(
        {
            ("devices", "-l"): [done("List of devices attached\n")],
            ("mdns", "services"): [done("")],
            ("connect", f"{HOST}:5555"): [done("failed to connect", code=1)],
        }
    )
    with pytest.raises(DebuggingOffError, match="Wireless debugging"):
        inventory(runner).list_apps(HOST)


def test_pairing_code_uses_stdin_and_is_never_an_argument() -> None:
    answers: Answers = {
        ("version",): [done(ADB_VERSION)],
        ("help",): [done("adb pair HOST[:PORT] [PAIRING CODE]")],
        ("pair", f"{HOST}:40211"): [done("Successfully paired")],
        ("devices", "-l"): [done(DEVICES_TLS)],
        ("mdns", "services"): [done(MDNS_TLS)],
    }
    runner = FakeRunner(answers)
    inventory(runner).pair(HOST, f"{HOST}:40211", "123456")
    pair_call = next(call for call in runner.calls if call[0][1] == "pair")
    assert pair_call[0] == ("adb", "pair", f"{HOST}:40211")
    assert pair_call[1] == "123456\n"


def test_pairing_is_never_replayed_while_waiting_for_the_connect_service() -> None:
    pairing_only = MDNS_TLS.replace("_adb-tls-connect", "_ignored")
    answers: Answers = {
        ("version",): [done(ADB_VERSION)],
        ("help",): [done("adb pair HOST[:PORT] [PAIRING CODE]")],
        ("pair", f"{HOST}:40211"): [done("Successfully paired")],
        ("devices", "-l"): [done("List of devices attached\n"), done(DEVICES_TLS)],
        ("mdns", "services"): [done(pairing_only), done(MDNS_TLS)],
    }
    runner = FakeRunner(answers)

    inventory(runner).pair(HOST, f"{HOST}:40211", "123456")

    commands = [call[0][1:] for call in runner.calls]
    assert commands.count(("pair", f"{HOST}:40211")) == 1
    assert commands.count(("mdns", "services")) == 2


@pytest.mark.parametrize("code", ["", "12345", "abcdef", "1234567"])
def test_invalid_or_expired_pairing_is_rejected(code: str) -> None:
    runner = FakeRunner({})
    with pytest.raises(InvalidArgumentError):
        inventory(runner).pair(HOST, f"{HOST}:40211", code)
    assert runner.calls == []


def test_refused_pairing_does_not_retain_or_echo_code() -> None:
    runner = FakeRunner(
        {
            ("version",): [done(ADB_VERSION)],
            ("help",): [done("adb pair HOST[:PORT] [PAIRING CODE]")],
            ("pair", f"{HOST}:40211"): [done("", "failed", 1)],
        }
    )
    with pytest.raises(CommandRejectedError) as error:
        inventory(runner).pair(HOST, f"{HOST}:40211", "123456")
    assert "123456" not in str(error.value)


def test_old_adb_explains_that_wireless_pairing_is_unavailable() -> None:
    runner = FakeRunner(
        {
            ("version",): [done(ADB_VERSION)],
            ("help",): [done("adb connect HOST[:PORT]")],
        }
    )
    with pytest.raises(UnsupportedOperationError, match=r"does not support.*pairing"):
        inventory(runner).pair(HOST, f"{HOST}:40211", "123456")
    assert all("pair" not in call[0][1:] for call in runner.calls)


@pytest.mark.parametrize(
    ("version_output", "returncode", "message"),
    [
        (
            "Android Debug Bridge version 1.0.41\nVersion 29.0.6-debian\n",
            0,
            "30.0.0",
        ),
        ("Android Debug Bridge version unknown\n", 0, "could not be verified"),
        ("", 2, "not functional"),
    ],
)
def test_pairing_rejects_old_invalid_or_nonfunctional_adb_before_help_or_pair(
    version_output: str, returncode: int, message: str
) -> None:
    runner = FakeRunner({("version",): [done(version_output, code=returncode)]})

    with pytest.raises(UnsupportedOperationError, match=message):
        inventory(runner).pair(HOST, f"{HOST}:40211", "123456")

    assert [call[0][1:] for call in runner.calls] == [("version",)]


def test_pairing_version_probe_timeout_never_reaches_help_or_pair() -> None:
    calls: list[tuple[str, ...]] = []

    def timeout(
        command: Sequence[str], stdin: str | None, seconds: float
    ) -> subprocess.CompletedProcess[str]:
        del stdin
        calls.append(tuple(command[1:]))
        raise subprocess.TimeoutExpired(command, seconds)

    with pytest.raises(OperationTimeoutError, match="ADB did not answer"):
        AdbAppInventory(run=timeout).pair(HOST, f"{HOST}:40211", "123456")

    assert calls == [("version",)]


def test_missing_adb_executable_never_reaches_help_or_pair() -> None:
    calls: list[tuple[str, ...]] = []

    def missing(
        command: Sequence[str], stdin: str | None, seconds: float
    ) -> subprocess.CompletedProcess[str]:
        del stdin, seconds
        calls.append(tuple(command[1:]))
        raise FileNotFoundError

    with pytest.raises(UnsupportedOperationError, match="not installed"):
        AdbAppInventory(run=missing).pair(HOST, f"{HOST}:40211", "123456")

    assert calls == [("version",)]


def test_post_pair_wait_can_be_cancelled_without_replaying_pair() -> None:
    cancelled = [False]
    answers: Answers = {
        ("version",): [done(ADB_VERSION)],
        ("help",): [done("adb pair HOST[:PORT] [PAIRING CODE]")],
        ("pair", f"{HOST}:40211"): [done("Successfully paired")],
        ("devices", "-l"): [done("List of devices attached\n")],
        ("mdns", "services"): [done(MDNS_TLS.replace("_adb-tls-connect", "_ignored"))],
    }
    runner = FakeRunner(answers)

    def cancel_during_wait(_: float) -> None:
        cancelled[0] = True

    apps = AdbAppInventory(
        run=runner,
        sleep=cancel_during_wait,
        pairing_wait_cancelled=lambda: cancelled[0],
    )
    with pytest.raises(OperationTimeoutError, match=r"waiting.*cancelled"):
        apps.pair(HOST, f"{HOST}:40211", "123456")

    commands = [call[0][1:] for call in runner.calls]
    assert commands.count(("pair", f"{HOST}:40211")) == 1
    assert commands.count(("mdns", "services")) == 1


def test_pairing_cannot_target_another_device() -> None:
    runner = FakeRunner({})
    with pytest.raises(InvalidArgumentError, match="selected TV"):
        inventory(runner).pair(HOST, "192.0.2.99:40000", "123456")


def test_launch_is_sent_once_and_confirmed_from_front_app() -> None:
    answers = {
        ("devices", "-l"): [done(DEVICES_TLS)],
        ("mdns", "services"): [done(MDNS_TLS)],
        (
            "-s",
            TLS_SERIAL,
            "shell",
            "monkey -p com.netflix.ninja -c android.intent.category.LEANBACK_LAUNCHER 1",
        ): [done("Events injected: 1")],
        ("-s", TLS_SERIAL, "shell", "dumpsys activity activities"): [done(FRONT_NETFLIX)],
    }
    runner = FakeRunner(answers)
    assert inventory(runner).launch_app(HOST, "com.netflix.ninja").front_app == "com.netflix.ninja"
    assert len([c for c in runner.calls if "monkey" in c[0][-1]]) == 1


def test_missing_activity_and_lost_connection_have_distinct_errors() -> None:
    base: Answers = {
        ("devices", "-l"): [done(DEVICES_TLS)],
        ("mdns", "services"): [done(MDNS_TLS)],
    }
    command = (
        "-s",
        TLS_SERIAL,
        "shell",
        "monkey -p com.example.service -c android.intent.category.LEANBACK_LAUNCHER 1",
    )
    runner = FakeRunner({**base, command: [done("No activities found; monkey aborted")]})
    with pytest.raises(CommandRejectedError):
        inventory(runner).launch_app(HOST, "com.example.service")
    base = {
        ("devices", "-l"): [done(DEVICES_TLS)],
        ("mdns", "services"): [done(MDNS_TLS)],
    }
    runner = FakeRunner({**base, command: [done("", "device offline", 1)]})
    with pytest.raises(DeviceUnavailableError, match="lost"):
        inventory(runner).launch_app(HOST, "com.example.service")


def test_invalid_package_never_invokes_adb() -> None:
    runner = FakeRunner({})
    with pytest.raises(InvalidArgumentError):
        inventory(runner).launch_app(HOST, "com.example;reboot")
    assert runner.calls == []


def test_timeout_missing_binary_and_apps_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(
        command: Sequence[str], stdin: str | None, seconds: float
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, seconds)

    with pytest.raises(OperationTimeoutError):
        AdbAppInventory(run=timeout).list_apps(HOST)

    def missing(
        command: Sequence[str], stdin: str | None, seconds: float
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    with pytest.raises(UnsupportedOperationError):
        AdbAppInventory(run=missing).list_apps(HOST)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert apps_installed() is False


def test_front_parser_only_accepts_resumed_activity() -> None:
    assert parse_front_app(FRONT_NETFLIX) == "com.netflix.ninja"
    assert parse_front_app("mFocusedApp=com.netflix.ninja") is None


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("device unauthorized", PairingRequiredError),
        ("permission denied", CommandRejectedError),
        ("protocol fault", DeviceUnavailableError),
    ],
)
def test_shell_failures_are_classified(stderr: str, expected: type[Exception]) -> None:
    answers = listing_answers()
    answers[("-s", TLS_SERIAL, "shell", LAUNCHER_QUERY)] = [done("", stderr, 1)]
    with pytest.raises(expected):
        inventory(FakeRunner(answers)).list_apps(HOST)


def test_adb_inventory_failure_and_os_failure_are_plain_control_errors() -> None:
    runner = FakeRunner({("devices", "-l"): [done("", "server failed", 1)]})
    with pytest.raises(DeviceUnavailableError, match="could not list"):
        inventory(runner).list_apps(HOST)

    def broken(
        command: Sequence[str], stdin: str | None, seconds: float
    ) -> subprocess.CompletedProcess[str]:
        raise OSError

    with pytest.raises(DeviceUnavailableError, match="local ADB server"):
        AdbAppInventory(run=broken).list_apps(HOST)


@pytest.mark.parametrize("host", ["tv.local", "", None])
def test_invalid_tv_address_never_invokes_adb(host: Any) -> None:
    runner = FakeRunner({})
    with pytest.raises(InvalidArgumentError):
        inventory(runner).list_apps(host)
    assert runner.calls == []


@pytest.mark.parametrize("endpoint", ["bad", f"{HOST}:0", f"{HOST}:99999"])
def test_invalid_pairing_endpoint_never_invokes_adb(endpoint: str) -> None:
    runner = FakeRunner({})
    with pytest.raises(InvalidArgumentError):
        inventory(runner).pair(HOST, endpoint, "123456")
    assert runner.calls == []


def test_multiple_tls_services_for_selected_tv_are_refused() -> None:
    mdns = MDNS_TLS + f"adb-other\t_adb-tls-connect._tcp.\t{HOST}:37124\n"
    runner = FakeRunner(
        {
            ("devices", "-l"): [done("List of devices attached\n")],
            ("mdns", "services"): [done(mdns)],
        }
    )
    with pytest.raises(DeviceUnavailableError, match="more than one Wireless"):
        inventory(runner).list_apps(HOST)
