"""Ubuntu real-adapter tests: every host operation uses an in-memory double."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if "acceptance" not in sys.modules:
    load("acceptance")
if "acceptance_platforms" not in sys.modules:
    load("acceptance_platforms")
ubuntu = load("acceptance_ubuntu")
acceptance = sys.modules["acceptance"]


class FakeProcess:
    def __init__(self, *, exit_code: int | None = None, hangs_on_stop: bool = False) -> None:
        self.exit_code = exit_code
        self.hangs_on_stop = hangs_on_stop
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.hangs_on_stop = False

    def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
        del timeout
        if self.hangs_on_stop:
            raise subprocess.TimeoutExpired(["control-tv"], 1)
        return "tauri stdout", "tauri stderr"


class FakeHost:
    def __init__(
        self,
        replies: list[subprocess.CompletedProcess[str]],
        *,
        process: FakeProcess | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.replies = replies
        self.process = process or FakeProcess()
        self.environ = environ or {
            "XDG_CURRENT_DESKTOP": "ubuntu:GNOME",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            "WAYLAND_DISPLAY": "wayland-0",
        }
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.launched: list[tuple[str, ...]] = []
        self.sleeps: list[float] = []

    def run(
        self,
        args: Any,
        *,
        timeout: float,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        self.calls.append((tuple(args), input_text))
        return self.replies.pop(0)

    def launch(self, args: Any) -> FakeProcess:
        self.launched.append(tuple(args))
        return self.process

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def completed(
    code: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["fake"], code, stdout, stderr)


def scenario() -> Any:
    return acceptance.Scenario(
        "ubuntu.real.launch-tray-logs",
        "ubuntu",
        "real",
        lambda context: None,
        evidence=acceptance.Evidence.REAL,
    )


def context(tmp_path: Path, *, authorized: bool = True) -> Any:
    package = tmp_path / "control-tv.deb"
    package.write_bytes(b"deb")
    environment = acceptance.Environment(
        "Linux",
        "x86_64",
        "3.12",
        "sha",
        {},
        {"deb": str(package)},
        {},
    )
    policy = acceptance.Policy(execute=authorized, install_deb=authorized)
    return acceptance.Context(policy, environment, 10, acceptance.SubprocessRunner())


def successful_replies() -> list[subprocess.CompletedProcess[str]]:
    return [
        completed(1),
        completed(
            stdout=(
                "Package: control-tv\nArchitecture: amd64\nVersion: 0.1.0\n"
                "Depends: libwebkit2gtk-4.1-0\n"
            )
        ),
        completed(stdout="Inst control-tv"),
        completed(stdout="(@as [],)"),
        completed(),
        completed(stdout="installed"),
        completed(stdout="ii  0.1.0"),
        completed(stdout='{"id":1,"result":{"status":"ready"}}\n'),
        completed(stdout="(@as ['/StatusNotifierItem/control_tv'],)"),
    ]


def test_missing_authorization_performs_no_host_action(tmp_path: Path) -> None:
    host = FakeHost([])
    result = ubuntu.run_ubuntu_acceptance(
        context(tmp_path, authorized=False), scenario(), host=host
    )
    assert result.outcome is acceptance.Outcome.BLOCKED
    assert host.calls == []
    assert host.launched == []


def test_preinstalled_package_is_never_replaced_or_removed(tmp_path: Path) -> None:
    host = FakeHost([completed(stdout="ii  0.1.0")])
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome is acceptance.Outcome.BLOCKED
    assert len(host.calls) == 1
    assert not any("apt-get" in command for command, _ in host.calls)


@pytest.mark.parametrize(
    ("environ", "outcome"),
    [
        ({"XDG_CURRENT_DESKTOP": "KDE"}, "SKIP"),
        ({"XDG_CURRENT_DESKTOP": "GNOME"}, "BLOCKED"),
    ],
)
def test_desktop_preflight_stops_before_install(
    tmp_path: Path, environ: dict[str, str], outcome: str
) -> None:
    host = FakeHost([], environ=environ)
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome.value == outcome
    assert host.calls == []


def test_unresolvable_dependencies_block_before_install(tmp_path: Path) -> None:
    host = FakeHost(
        [
            completed(1),
            completed(
                stdout=(
                    "Package: control-tv\nArchitecture: amd64\nVersion: 0.1.0\n"
                    "Depends: libwebkit2gtk-4.1-0\n"
                )
            ),
            completed(100, stderr="unmet dependencies"),
        ]
    )
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome is acceptance.Outcome.BLOCKED
    assert not any(command[:3] == ("sudo", "-n", "apt-get") for command, _ in host.calls)


def test_success_validates_install_launch_bridge_and_tray(tmp_path: Path) -> None:
    host = FakeHost(successful_replies())
    result = ubuntu.run_ubuntu_acceptance(
        context(tmp_path), scenario(), host=host, startup_seconds=0.01
    )
    assert result.outcome is acceptance.Outcome.PASS
    assert host.launched == [("/usr/bin/control-tv",)]
    assert host.process.terminated is True
    install = next(command for command, _ in host.calls if command[:3] == ("sudo", "-n", "apt-get"))
    assert install[3:6] == ("install", "--no-install-recommends", "-y")
    bridge = next(item for item in host.calls if item[0] == (str(ubuntu.BRIDGE),))
    assert '"method":"ping"' in (bridge[1] or "")
    assert "gdbus" in result.proof


def test_bridge_id_mismatch_fails_and_process_is_cleaned(tmp_path: Path) -> None:
    replies = successful_replies()
    replies[7] = completed(stdout='{"id":2,"result":{"status":"ready"}}\n')
    host = FakeHost(replies)
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome is acceptance.Outcome.FAIL
    assert "correlated" in result.reason
    assert host.process.terminated is True


def test_missing_new_tray_item_fails(tmp_path: Path) -> None:
    replies = successful_replies()
    replies[-1] = completed(stdout="(@as [],)")
    host = FakeHost(replies)
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome is acceptance.Outcome.FAIL
    assert "tray" in result.reason


def test_hung_process_is_killed_during_bounded_cleanup(tmp_path: Path) -> None:
    process = FakeProcess(hangs_on_stop=True)
    host = FakeHost(successful_replies(), process=process)
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome is acceptance.Outcome.PASS
    assert process.terminated is True
    assert process.killed is True


def test_no_tv_or_adb_command_exists_in_real_ubuntu_path(tmp_path: Path) -> None:
    host = FakeHost(successful_replies())
    ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    flattened = [part for command, _ in host.calls for part in command]
    assert "adb" not in flattened
    assert not any("cast" in part.casefold() for part in flattened)
