"""Ubuntu real-adapter tests: every host operation uses an in-memory double."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from deb_fixtures import COMMIT, make_deb, payload_sha256, sha256

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
if "acceptance_identity" not in sys.modules:
    load("acceptance_identity")
ubuntu = load("acceptance_ubuntu")
identity = sys.modules["acceptance_identity"]
BINARY = b"control-tv binary of the tested build"
acceptance = sys.modules["acceptance"]


class FakeProcess:
    def __init__(self, *, exit_code: int | None = None, hangs_on_stop: bool = False) -> None:
        self.exit_code = exit_code
        self.hangs_on_stop = hangs_on_stop
        self.terminated = False
        self.killed = False
        self.pid = 4242

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
        installed_binary: str | None = sha256(BINARY),
        binary_after_install: str | None = sha256(BINARY),
        installed_payload: str | None = payload_sha256(BINARY),
        payload_after_install: str | None = payload_sha256(BINARY),
    ) -> None:
        self.installed_binary = installed_binary
        self.binary_after_install = binary_after_install
        self.installed_payload = installed_payload
        self.payload_after_install = payload_after_install
        self.hashed: list[Path] = []
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

    def file_sha256(self, path: Path) -> str | None:
        self.hashed.append(path)
        installed = any(
            command[:4] == ("sudo", "-n", "apt-get", "install") for command, _ in self.calls
        )
        if path == ubuntu.INSTALLED_PAYLOAD:
            return self.payload_after_install if installed else self.installed_payload
        return self.binary_after_install if installed else self.installed_binary


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


def context(
    tmp_path: Path,
    *,
    authorized: bool = True,
    allow_existing: bool = False,
    provenance: bool = True,
) -> Any:
    package = tmp_path / "control-tv.deb"
    if not package.exists():
        make_deb(package, binary=BINARY)
        if provenance:
            (tmp_path / "provenance.json").write_text(
                identity.deb_provenance_for(package, commit=COMMIT, run_id=1).to_json(),
                encoding="utf-8",
            )
    environment = acceptance.Environment(
        "Linux",
        "x86_64",
        "3.12",
        "sha",
        {},
        {"deb": str(package)},
        {},
    )
    policy = acceptance.Policy(
        execute=authorized,
        install_deb=authorized,
        validate_existing_deb=allow_existing,
    )
    return acceptance.Context(
        policy, environment, 10, acceptance.SubprocessRunner(), tmp_path / "reports"
    )


def metadata() -> subprocess.CompletedProcess[str]:
    return completed(
        stdout=(
            "Package: control-tv\nArchitecture: amd64\nVersion: 0.1.0\n"
            "Depends: libwebkit2gtk-4.1-0\n"
        )
    )


def successful_replies() -> list[subprocess.CompletedProcess[str]]:
    return [
        metadata(),
        completed(1),
        completed(stdout="Inst control-tv"),
        completed(stdout="(@as [],)"),
        completed(),
        completed(stdout="installed"),
        completed(stdout="ii  0.1.0"),
        completed(stdout='{"id":1,"result":{"status":"ready"}}\n'),
        completed(
            stdout="(@as [':1.9/org/ayatana/NotificationItem/tray_icon_tray_app_control_tv'],)"
        ),
        completed(stdout="(uint32 4242,)"),
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
    host = FakeHost([metadata(), completed(stdout="ii  0.1.0")])
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome is acceptance.Outcome.BLOCKED
    assert "pre-existing installation" in result.observed
    assert len(host.calls) == 2
    assert not any("apt-get" in command for command, _ in host.calls)


def test_absent_receipt_cannot_resume_preinstalled_package_without_consent(
    tmp_path: Path,
) -> None:
    current = context(tmp_path)
    assert (current.state_dir / ubuntu.RECEIPT).exists() is False
    host = FakeHost([metadata(), completed(stdout="ii  0.1.0")])
    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)
    assert result.outcome is acceptance.Outcome.BLOCKED
    assert result.observed == "pre-existing installation"
    assert len(host.calls) == 2


def test_explicit_consent_validates_preexisting_package_without_install(tmp_path: Path) -> None:
    host = FakeHost(
        [
            metadata(),
            completed(stdout="ii  0.1.0"),
            completed(stdout="already satisfied"),
            completed(stdout="(@as [],)"),
            completed(stdout='{"id":1,"result":{"status":"ready"}}\n'),
            completed(
                stdout=(
                    "(@as [':1.9/org/ayatana/NotificationItem/tray_icon_tray_app_control_tv'],)"
                )
            ),
            completed(stdout="(uint32 4242,)"),
        ]
    )
    result = ubuntu.run_ubuntu_acceptance(
        context(tmp_path, allow_existing=True), scenario(), host=host
    )
    assert result.outcome is acceptance.Outcome.PASS
    assert "origin=pre-existing installation" in result.observed
    assert not any(command[:3] == ("sudo", "-n", "apt-get") for command, _ in host.calls)


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
            metadata(),
            completed(1),
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
    assert "StatusNotifier identity" in result.proof
    assert (context(tmp_path).state_dir / ubuntu.RECEIPT).exists() is False


def test_bridge_id_mismatch_fails_and_process_is_cleaned(tmp_path: Path) -> None:
    replies = successful_replies()
    replies[7] = completed(stdout='{"id":2,"result":{"status":"ready"}}\n')
    host = FakeHost(replies)
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome is acceptance.Outcome.FAIL
    assert "correlated" in result.reason
    assert host.process.terminated is True


def test_unrelated_new_tray_item_is_not_a_control_tv_false_positive(tmp_path: Path) -> None:
    replies = successful_replies()
    replies[-2] = completed(stdout="(@as [':1.8/org/ayatana/NotificationItem/other_app'],)")
    host = FakeHost(replies)
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome is acceptance.Outcome.FAIL
    assert "identity" in result.reason
    assert len(host.replies) == 1


def test_spoofed_tray_path_owned_by_another_process_fails(tmp_path: Path) -> None:
    replies = successful_replies()
    replies[-1] = completed(stdout="(uint32 9999,)")
    host = FakeHost(replies)
    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)
    assert result.outcome is acceptance.Outcome.FAIL
    assert "not owned" in result.reason


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


def test_interrupted_recipe_requires_extra_consent_and_is_identified(tmp_path: Path) -> None:
    current = context(tmp_path)
    artifact = Path(current.environment.artifacts["deb"])
    receipt = current.state_dir / ubuntu.RECEIPT
    ubuntu._write_receipt(
        receipt,
        provenance=identity.read_deb_provenance(artifact.parent / "provenance.json"),
        state="installed",
    )
    host = FakeHost([metadata(), completed(stdout="ii  0.1.0")])
    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)
    assert result.outcome is acceptance.Outcome.BLOCKED
    assert result.observed == "interrupted acceptance installation"
    assert receipt.is_file()


def test_malformed_receipt_with_no_installed_package_blocks_new_install(tmp_path: Path) -> None:
    current = context(tmp_path)
    receipt = current.state_dir / ubuntu.RECEIPT
    receipt.parent.mkdir(parents=True)
    receipt.write_text("not json", encoding="utf-8")
    host = FakeHost([metadata(), completed(1)])
    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)
    assert result.outcome is acceptance.Outcome.BLOCKED
    assert "receipt" in result.reason
    assert not any("apt-get" in command for command, _ in host.calls)


@pytest.mark.parametrize(
    "receipt_fields",
    [
        {"artifactSha256": "stale", "version": "0.1.0", "state": "installed"},
        {"artifactSha256": None, "version": "0.1.0", "state": "unexpected"},
    ],
    ids=["stale", "inconsistent"],
)
def test_stale_or_inconsistent_receipt_blocks_existing_package_validation(
    tmp_path: Path, receipt_fields: dict[str, str | None]
) -> None:
    current = context(tmp_path, allow_existing=True)
    receipt = current.state_dir / ubuntu.RECEIPT
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "schemaVersion": ubuntu.RECEIPT_SCHEMA,
                "package": ubuntu.PACKAGE,
                "commit": COMMIT,
                "binarySha256": sha256(BINARY),
                "payloadSha256": payload_sha256(BINARY),
                **receipt_fields,
            }
        ),
        encoding="utf-8",
    )
    host = FakeHost([metadata()])
    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)
    assert result.outcome is acceptance.Outcome.BLOCKED
    assert "stale or inconsistent" in result.reason
    assert len(host.calls) == 1
    assert host.launched == []


def test_explicit_interrupted_recipe_resume_never_reinstalls_and_clears_receipt(
    tmp_path: Path,
) -> None:
    current = context(tmp_path, allow_existing=True)
    artifact = Path(current.environment.artifacts["deb"])
    receipt = current.state_dir / ubuntu.RECEIPT
    ubuntu._write_receipt(
        receipt,
        provenance=identity.read_deb_provenance(artifact.parent / "provenance.json"),
        state="installed",
    )
    host = FakeHost(
        [
            metadata(),
            completed(stdout="ii  0.1.0"),
            completed(stdout="already satisfied"),
            completed(stdout="(@as [],)"),
            completed(stdout='{"id":1,"result":{"status":"ready"}}\n'),
            completed(
                stdout=(
                    "(@as [':1.9/org/ayatana/NotificationItem/tray_icon_tray_app_control_tv'],)"
                )
            ),
            completed(stdout="(uint32 4242,)"),
        ]
    )
    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)
    assert result.outcome is acceptance.Outcome.PASS
    assert "origin=interrupted acceptance installation" in result.observed
    assert not any(command[:3] == ("sudo", "-n", "apt-get") for command, _ in host.calls)
    assert receipt.exists() is False


# Build identity (B2): the version alone never identifies a build -----------------------------


def test_a_package_without_provenance_is_never_validated(tmp_path: Path) -> None:
    host = FakeHost([metadata()])
    result = ubuntu.run_ubuntu_acceptance(
        context(tmp_path, provenance=False, allow_existing=True), scenario(), host=host
    )
    assert result.outcome is acceptance.Outcome.BLOCKED
    assert "build identity cannot be established: no provenance manifest" in result.reason
    assert len(host.calls) == 1 and host.launched == [] and host.hashed == []


def test_a_package_that_contradicts_its_provenance_fails(tmp_path: Path) -> None:
    current = context(tmp_path, allow_existing=True)
    make_deb(Path(current.environment.artifacts["deb"]), binary=b"another build")
    host = FakeHost([metadata()])

    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)

    assert result.outcome is acceptance.Outcome.FAIL
    assert "package SHA-256 differs" in result.reason
    assert "application binary SHA-256 differs" in result.reason
    assert host.launched == []


def test_dpkg_metadata_that_contradicts_the_provenance_fails(tmp_path: Path) -> None:
    current = context(tmp_path, allow_existing=True)
    host = FakeHost(
        [completed(stdout="Package: control-tv\nArchitecture: amd64\nVersion: 0.1.1\nDepends: x\n")]
    )

    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)

    assert result.outcome is acceptance.Outcome.FAIL
    assert "dpkg-deb metadata differs" in result.reason


def test_a_configured_provenance_path_is_used(tmp_path: Path) -> None:
    current = context(tmp_path, provenance=False, allow_existing=True)
    elsewhere = tmp_path / "elsewhere.json"
    package = Path(current.environment.artifacts["deb"])
    elsewhere.write_text(
        identity.deb_provenance_for(package, commit=COMMIT, run_id=None).to_json(),
        encoding="utf-8",
    )
    current.environment.artifacts["deb-provenance"] = str(elsewhere)
    host = FakeHost([metadata(), completed(stdout="ii  0.1.0")], installed_binary="0" * 64)

    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)

    assert "another build" in result.reason  # it got past the identity step


def test_another_build_of_the_same_version_is_never_validated(tmp_path: Path) -> None:
    host = FakeHost([metadata(), completed(stdout="ii  0.1.0")], installed_binary="0" * 64)

    result = ubuntu.run_ubuntu_acceptance(
        context(tmp_path, allow_existing=True), scenario(), host=host
    )

    assert result.outcome is acceptance.Outcome.BLOCKED
    assert result.reason == "the installed control-tv 0.1.0 is another build than the artifact"
    assert f"artifact binary={sha256(BINARY)}" in result.observed
    assert host.launched == []
    assert host.hashed == [ubuntu.APPLICATION, ubuntu.INSTALLED_PAYLOAD]
    assert not any("apt-get" in command for command, _ in host.calls)


def test_an_unreadable_installed_binary_is_never_validated(tmp_path: Path) -> None:
    host = FakeHost([metadata(), completed(stdout="ii  0.1.0")], installed_binary=None)

    result = ubuntu.run_ubuntu_acceptance(
        context(tmp_path, allow_existing=True), scenario(), host=host
    )

    assert result.outcome is acceptance.Outcome.BLOCKED
    assert "cannot be read" in result.reason and host.launched == []


def test_an_install_that_leaves_another_binary_fails_and_keeps_the_receipt(
    tmp_path: Path,
) -> None:
    replies = successful_replies()[:7]
    host = FakeHost(replies, binary_after_install="f" * 64)
    current = context(tmp_path)

    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)

    assert result.outcome is acceptance.Outcome.FAIL
    assert "installed binary or files are not the artifact's" in result.reason
    assert host.launched == []
    assert (current.state_dir / ubuntu.RECEIPT).is_file()


def test_a_pass_names_the_commit_and_both_digests(tmp_path: Path) -> None:
    current = context(tmp_path)
    host = FakeHost(successful_replies())

    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host, startup_seconds=0.01)

    assert result.outcome is acceptance.Outcome.PASS
    package = Path(current.environment.artifacts["deb"])
    assert f"commit={COMMIT}" in result.observed
    assert f"deb={sha256(package.read_bytes())}" in result.observed
    assert f"binary={sha256(BINARY)}" in result.observed
    assert f"payload={payload_sha256(BINARY)}" in result.observed


def test_a_receipt_from_the_version_only_schema_is_stale(tmp_path: Path) -> None:
    current = context(tmp_path, allow_existing=True)
    receipt = current.state_dir / ubuntu.RECEIPT
    receipt.parent.mkdir(parents=True)
    package = Path(current.environment.artifacts["deb"])
    receipt.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "package": ubuntu.PACKAGE,
                "artifactSha256": sha256(package.read_bytes()),
                "version": "0.1.0",
                "state": "installed",
            }
        ),
        encoding="utf-8",
    )
    host = FakeHost([metadata()])

    result = ubuntu.run_ubuntu_acceptance(current, scenario(), host=host)

    assert result.outcome is acceptance.Outcome.BLOCKED
    assert "stale or inconsistent" in result.reason


def test_the_real_host_hashes_a_file_or_reports_it_unreadable(tmp_path: Path) -> None:
    present = tmp_path / "binary"
    present.write_bytes(BINARY)
    host = ubuntu.SubprocessUbuntuHost()

    assert host.file_sha256(present) == sha256(BINARY)
    assert host.file_sha256(tmp_path / "absent") is None


def test_an_installed_package_with_the_same_binary_but_another_bridge_is_not_this_build(
    tmp_path: Path,
) -> None:
    # The Rust binary is reproducible; a Python-only change shows in dpkg's md5sums only.
    host = FakeHost(
        [metadata(), completed(stdout="ii  0.1.0")],
        installed_payload=payload_sha256(BINARY, b"another bridge"),
    )

    result = ubuntu.run_ubuntu_acceptance(
        context(tmp_path, allow_existing=True), scenario(), host=host
    )

    assert result.outcome is acceptance.Outcome.BLOCKED
    assert "another build" in result.reason and host.launched == []


def test_an_unreadable_dpkg_file_list_is_never_validated(tmp_path: Path) -> None:
    host = FakeHost([metadata(), completed(stdout="ii  0.1.0")], installed_payload=None)

    result = ubuntu.run_ubuntu_acceptance(
        context(tmp_path, allow_existing=True), scenario(), host=host
    )

    assert result.outcome is acceptance.Outcome.BLOCKED
    assert "file list of the installed package cannot be read" in result.reason


def test_an_install_that_leaves_other_files_fails(tmp_path: Path) -> None:
    host = FakeHost(successful_replies()[:7], payload_after_install="e" * 64)

    result = ubuntu.run_ubuntu_acceptance(context(tmp_path), scenario(), host=host)

    assert result.outcome is acceptance.Outcome.FAIL and host.launched == []
