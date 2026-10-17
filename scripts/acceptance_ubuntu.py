"""Real Ubuntu acceptance adapter, isolated behind an injectable host boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from acceptance import Context, Outcome, Result, Scenario, make_result
from acceptance_platforms import configured_artifact

PACKAGE = "control-tv"
APPLICATION = Path("/usr/bin/control-tv")
BRIDGE = Path("/usr/lib/control-tv/python-bridge/control-tv-bridge")
WATCHER_DESTINATION = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
TRAY_PATH = "/org/ayatana/NotificationItem/tray_icon_tray_app_control_tv"
RECEIPT = "ubuntu-install-receipt.json"


class ManagedProcess(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def communicate(self, *, timeout: float | None = None) -> tuple[str, str]: ...


class UbuntuHost(Protocol):
    """All host effects used by the adapter; tests provide a side-effect-free double."""

    @property
    def environ(self) -> Mapping[str, str]: ...

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]: ...

    def launch(self, args: Sequence[str]) -> ManagedProcess: ...

    def sleep(self, seconds: float) -> None: ...


class SubprocessUbuntuHost:
    """Concrete local Ubuntu process adapter. It never uses a shell."""

    @property
    def environ(self) -> Mapping[str, str]:
        return os.environ

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(args),
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            shell=False,
        )

    def launch(self, args: Sequence[str]) -> ManagedProcess:
        return subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _completed_log(label: str, completed: subprocess.CompletedProcess[str]) -> str:
    return (
        f"$ {label}\nexit={completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    ).rstrip()


def _result(
    scenario: Scenario,
    outcome: Outcome,
    reason: str,
    started: float,
    logs: list[str],
    *,
    observed: str,
    proof: str,
) -> Result:
    return make_result(
        scenario,
        outcome,
        reason,
        started=started,
        log="\n\n".join(logs),
        observed=observed,
        proof=proof,
    )


def _watcher_items(output: str) -> frozenset[str]:
    return frozenset(re.findall(r"(?:[:A-Za-z0-9_.-]+)?/[A-Za-z0-9_./-]+", output))


def _watcher(host: UbuntuHost, timeout: float) -> subprocess.CompletedProcess[str]:
    return host.run(
        (
            "gdbus",
            "call",
            "--session",
            "--dest",
            WATCHER_DESTINATION,
            "--object-path",
            WATCHER_PATH,
            "--method",
            "org.freedesktop.DBus.Properties.Get",
            WATCHER_DESTINATION,
            "RegisteredStatusNotifierItems",
        ),
        timeout=timeout,
    )


def _tray_owner_pid(
    host: UbuntuHost, item: str, timeout: float
) -> subprocess.CompletedProcess[str]:
    bus_name = item.split("/", 1)[0]
    return host.run(
        (
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.DBus",
            "--object-path",
            "/org/freedesktop/DBus",
            "--method",
            "org.freedesktop.DBus.GetConnectionUnixProcessID",
            bus_name,
        ),
        timeout=timeout,
    )


def _receipt_path(context: Context) -> Path:
    return context.state_dir / RECEIPT


def _artifact_digest(artifact: Path) -> str:
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_receipt(path: Path) -> Mapping[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _write_receipt(path: Path, *, digest: str, version: str, state: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "package": PACKAGE,
                "artifactSha256": digest,
                "version": version,
                "state": state,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _installed_version(completed: subprocess.CompletedProcess[str]) -> str | None:
    fields = completed.stdout.strip().split(maxsplit=1)
    if completed.returncode != 0 or len(fields) != 2 or fields[0] != "ii":
        return None
    return fields[1]


def _stop(process: ManagedProcess, timeout: float, logs: list[str]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=timeout)
        logs.append("application did not terminate in time and was killed")
    logs.append(f"application stdout:\n{stdout}\napplication stderr:\n{stderr}".rstrip())


def run_ubuntu_acceptance(
    context: Context,
    scenario: Scenario,
    *,
    host: UbuntuHost | None = None,
    startup_seconds: float = 3.0,
) -> Result:
    """Install and validate the Ubuntu package after all fail-closed preflights pass."""
    started = time.monotonic()
    logs: list[str] = []
    if not (context.policy.execute and context.policy.install_deb):
        return _result(
            scenario,
            Outcome.BLOCKED,
            "authorization missing: execute, install-deb",
            started,
            logs,
            observed="no host action attempted",
            proof="authorization policy",
        )
    artifact = configured_artifact(context, scenario, "deb")
    if isinstance(artifact, Result):
        return artifact
    if context.environment.system != "Linux" or context.environment.machine not in {
        "x86_64",
        "amd64",
    }:
        return _result(
            scenario,
            Outcome.SKIP,
            "real Ubuntu adapter requires Linux amd64",
            started,
            logs,
            observed=f"{context.environment.system} {context.environment.machine}",
            proof="detected host platform",
        )

    active_host = SubprocessUbuntuHost() if host is None else host
    desktop = active_host.environ.get("XDG_CURRENT_DESKTOP", "")
    if "gnome" not in desktop.casefold():
        return _result(
            scenario,
            Outcome.SKIP,
            "GNOME desktop session is required for tray validation",
            started,
            logs,
            observed=desktop or "XDG_CURRENT_DESKTOP is absent",
            proof="desktop environment",
        )
    if not active_host.environ.get("DBUS_SESSION_BUS_ADDRESS") or not (
        active_host.environ.get("DISPLAY") or active_host.environ.get("WAYLAND_DISPLAY")
    ):
        return _result(
            scenario,
            Outcome.BLOCKED,
            "an interactive display and session DBus are required",
            started,
            logs,
            observed="desktop session is incomplete",
            proof="session environment",
        )

    timeout = context.timeout_seconds
    metadata = active_host.run(
        ("dpkg-deb", "-f", str(artifact), "Package", "Architecture", "Version", "Depends"),
        timeout=timeout,
    )
    logs.append(_completed_log("dpkg-deb package metadata", metadata))
    metadata_fields = {
        key.strip(): value.strip()
        for line in metadata.stdout.splitlines()
        if ":" in line
        for key, value in (line.split(":", 1),)
    }
    if metadata.returncode != 0 or not {"Package", "Architecture", "Version", "Depends"} <= (
        metadata_fields.keys()
    ):
        return _result(
            scenario,
            Outcome.FAIL,
            "unable to read complete Debian package metadata",
            started,
            logs,
            observed="metadata check failed",
            proof="dpkg-deb output",
        )
    package = metadata_fields["Package"]
    architecture = metadata_fields["Architecture"]
    version = metadata_fields["Version"]
    if package != PACKAGE or architecture != "amd64" or not version.strip():
        return _result(
            scenario,
            Outcome.FAIL,
            "Debian package identity, architecture or version is invalid",
            started,
            logs,
            observed=f"package={package!r}, architecture={architecture!r}, version={version!r}",
            proof="dpkg-deb metadata",
        )

    artifact_digest = _artifact_digest(artifact)
    receipt_path = _receipt_path(context)
    receipt_present = receipt_path.exists()
    receipt = _read_receipt(receipt_path)
    matching_receipt = receipt is not None and all(
        (
            receipt.get("schemaVersion") == 1,
            receipt.get("package") == PACKAGE,
            receipt.get("artifactSha256") == artifact_digest,
            receipt.get("version") == version,
            receipt.get("state") in {"installing", "installed"},
        )
    )
    if receipt_present and not matching_receipt:
        return _result(
            scenario,
            Outcome.BLOCKED,
            "the installation receipt is stale or inconsistent; manual review is required",
            started,
            logs,
            observed="receipt does not match the configured artifact and package metadata",
            proof=f"{receipt_path}; no package mutation or validation attempted",
        )
    installed = active_host.run(
        ("dpkg-query", "-W", "-f=${db:Status-Abbrev} ${Version}", PACKAGE), timeout=timeout
    )
    logs.append(_completed_log("dpkg-query installed control-tv", installed))
    current_version = _installed_version(installed)
    installed_cleanly = current_version is not None
    known_to_dpkg = installed.returncode == 0
    installation_origin = "new recipe installation"
    skip_install = False
    if installed_cleanly:
        installation_origin = (
            "interrupted acceptance installation"
            if matching_receipt
            else "pre-existing installation"
        )
        if current_version != version:
            return _result(
                scenario,
                Outcome.BLOCKED,
                "installed control-tv version differs from the configured artifact",
                started,
                logs,
                observed=f"{installation_origin}: {installed.stdout.strip()}",
                proof="dpkg-query and acceptance receipt",
            )
        if not context.policy.validate_existing_deb:
            return _result(
                scenario,
                Outcome.BLOCKED,
                "existing control-tv validation needs --allow-existing-deb-validation",
                started,
                logs,
                observed=installation_origin,
                proof="dpkg-query and acceptance receipt",
            )
        skip_install = True
    elif known_to_dpkg:
        return _result(
            scenario,
            Outcome.BLOCKED,
            "control-tv has a non-clean package state; manual recovery is required",
            started,
            logs,
            observed=installed.stdout.strip() or installed.stderr.strip(),
            proof="dpkg-query; no package mutation attempted",
        )
    elif matching_receipt:
        return _result(
            scenario,
            Outcome.BLOCKED,
            "an interrupted-install receipt exists but no clean installation can be identified",
            started,
            logs,
            observed="manual receipt and package-state review required",
            proof=str(receipt_path),
        )

    dependency_probe = active_host.run(
        ("apt-get", "-s", "install", "--no-install-recommends", str(artifact.resolve())),
        timeout=timeout,
    )
    logs.append(_completed_log("apt-get dependency simulation", dependency_probe))
    if dependency_probe.returncode != 0:
        return _result(
            scenario,
            Outcome.BLOCKED,
            "package dependencies are not resolvable without changing the system",
            started,
            logs,
            observed="APT simulation failed",
            proof="apt-get -s",
        )

    watcher_before = _watcher(active_host, timeout)
    logs.append(_completed_log("GNOME tray watcher before launch", watcher_before))
    if watcher_before.returncode != 0:
        return _result(
            scenario,
            Outcome.BLOCKED,
            "GNOME StatusNotifier watcher is unavailable",
            started,
            logs,
            observed="tray watcher query failed",
            proof="gdbus session query",
        )

    if not skip_install:
        privilege = active_host.run(("sudo", "-n", "true"), timeout=timeout)
        logs.append(_completed_log("non-interactive installation authorization", privilege))
        if privilege.returncode != 0:
            return _result(
                scenario,
                Outcome.BLOCKED,
                "non-interactive sudo authorization is required for installation",
                started,
                logs,
                observed="sudo -n refused",
                proof="sudo preflight",
            )

        _write_receipt(receipt_path, digest=artifact_digest, version=version, state="installing")
        install = active_host.run(
            (
                "sudo",
                "-n",
                "apt-get",
                "install",
                "--no-install-recommends",
                "-y",
                str(artifact.resolve()),
            ),
            timeout=timeout,
        )
        logs.append(_completed_log("authorized Debian package installation", install))
        if install.returncode != 0:
            return _result(
                scenario,
                Outcome.FAIL,
                "Debian package installation failed; recovery receipt retained",
                started,
                logs,
                observed="APT install failed",
                proof=str(receipt_path),
            )

        installed_version = active_host.run(
            ("dpkg-query", "-W", "-f=${db:Status-Abbrev} ${Version}", PACKAGE), timeout=timeout
        )
        logs.append(_completed_log("dpkg-query installed version", installed_version))
        if installed_version.returncode != 0 or _installed_version(installed_version) != version:
            return _result(
                scenario,
                Outcome.FAIL,
                "installed package version does not match the artifact; recovery receipt retained",
                started,
                logs,
                observed=installed_version.stdout.strip(),
                proof=str(receipt_path),
            )
        _write_receipt(receipt_path, digest=artifact_digest, version=version, state="installed")

    process = active_host.launch((str(APPLICATION),))
    outcome = Outcome.FAIL
    reason = "Tauri application validation did not complete"
    observed = "validation interrupted"
    proof = "application logs"
    try:
        active_host.sleep(min(startup_seconds, timeout))
        if process.poll() is not None:
            reason = "Tauri application exited during startup"
            observed = f"exit={process.poll()}"
            proof = "application process state and captured logs"
        else:
            bridge = active_host.run(
                (str(BRIDGE),),
                timeout=timeout,
                input_text='{"id":1,"method":"ping","params":{}}\n',
            )
            logs.append(_completed_log("installed Python bridge ping", bridge))
            try:
                response = json.loads(bridge.stdout.splitlines()[0])
                bridge_ready = (
                    bridge.returncode == 0
                    and response.get("id") == 1
                    and response.get("result", {}).get("status") == "ready"
                )
            except (IndexError, json.JSONDecodeError, AttributeError):
                bridge_ready = False
            if not bridge_ready:
                reason = "installed Python bridge did not answer its correlated ping"
                observed = "bridge ping failed"
                proof = "installed bridge stdout/stderr and application logs"
            else:
                watcher_after = _watcher(active_host, timeout)
                logs.append(_completed_log("GNOME tray watcher after launch", watcher_after))
                new_items = _watcher_items(watcher_after.stdout) - _watcher_items(
                    watcher_before.stdout
                )
                matching_items = sorted(
                    item for item in new_items if item.endswith(TRAY_PATH) and "/" in item
                )
                if watcher_after.returncode != 0 or len(matching_items) != 1:
                    reason = "control-TV tray identity was not registered exactly once"
                    observed = f"new tray items: {sorted(new_items)}"
                    proof = "exact StatusNotifier path and before/after gdbus queries"
                else:
                    owner = _tray_owner_pid(active_host, matching_items[0], timeout)
                    logs.append(_completed_log("control-TV tray DBus owner PID", owner))
                    owner_match = re.search(r"uint32\s+(\d+)", owner.stdout)
                    if (
                        owner.returncode != 0
                        or owner_match is None
                        or int(owner_match.group(1)) != process.pid
                    ):
                        reason = "control-TV tray item is not owned by the launched Tauri process"
                        observed = (
                            f"item={matching_items[0]!r}; owner={owner.stdout.strip()!r}; "
                            f"tauri_pid={process.pid}"
                        )
                        proof = "StatusNotifier identity and DBus owner PID"
                    else:
                        outcome = Outcome.PASS
                        reason = (
                            "installed Tauri app, Python bridge and identified GNOME tray "
                            "are operational"
                        )
                        observed = (
                            f"version={version}; origin={installation_origin}; "
                            f"tray={matching_items[0]}; pid={process.pid}"
                        )
                        proof = (
                            "dpkg-query, process state, bridge ping, exact StatusNotifier "
                            "identity, DBus owner PID and application logs"
                        )
    finally:
        _stop(process, min(timeout, 5.0), logs)
    if outcome is Outcome.PASS and (matching_receipt or not skip_install):
        receipt_path.unlink(missing_ok=True)
    return _result(
        scenario,
        outcome,
        reason,
        started,
        logs,
        observed=observed,
        proof=proof,
    )
