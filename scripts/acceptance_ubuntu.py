"""Real Ubuntu acceptance adapter, isolated behind an injectable host boundary.

The build under test is identified by its provenance manifest (commit, version, package, binary
and payload SHA-256, see `acceptance_identity`), never by its version alone: every package is
0.1.0. A real validation is refused when that identity cannot be established, and an installed
package is validated only when both its application binary and dpkg's record of its files
(`md5sums`, which covers the frozen Python bridge too) are the artifact's.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from acceptance import Context, Outcome, Result, Scenario, make_result
from acceptance_identity import (
    DebProvenance,
    IdentityError,
    deb_packaged_files,
    installed_file_problems,
    package_leftovers,
    read_deb_provenance,
    sha256_file,
    verify_deb,
)
from acceptance_platforms import configured_artifact

PACKAGE = "control-tv"
APPLICATION = Path("/usr/bin/control-tv")
BRIDGE = Path("/usr/lib/control-tv/python-bridge/control-tv-bridge")
# dpkg's copy of the installed package's md5sums: every installed file and its digest.
INSTALLED_PAYLOAD = Path("/var/lib/dpkg/info/control-tv.md5sums")
WATCHER_DESTINATION = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
TRAY_PATH = "/org/ayatana/NotificationItem/tray_icon_tray_app_control_tv"
RECEIPT = "ubuntu-install-receipt.json"
RECEIPT_SCHEMA = 2
PROVENANCE = "provenance.json"


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

    def file_sha256(self, path: Path) -> str | None:
        """The SHA-256 of an installed file, or None when it cannot be read."""
        ...

    def installed_file_problems(self) -> list[str] | None:
        """Installed files that are missing or differ from dpkg's record of the package, or
        None when they cannot be checked."""
        ...

    def package_leftovers(self, packaged: Sequence[str]) -> list[str] | None:
        """Package files present without a dpkg record, or None when they cannot be checked."""
        ...


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

    def installed_file_problems(self) -> list[str] | None:
        try:
            record = INSTALLED_PAYLOAD.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return installed_file_problems(Path("/"), record)

    def package_leftovers(self, packaged: Sequence[str]) -> list[str] | None:
        try:
            return package_leftovers(Path("/"), packaged)
        except IdentityError:
            return None

    def file_sha256(self, path: Path) -> str | None:
        try:
            return sha256_file(path)
        except IdentityError:
            return None


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


def _read_receipt(path: Path) -> Mapping[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _provenance_path(context: Context, artifact: Path) -> Path:
    configured = context.environment.artifacts.get("deb-provenance")
    return Path(configured) if configured else artifact.parent / PROVENANCE


def _write_receipt(path: Path, *, provenance: DebProvenance, state: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schemaVersion": RECEIPT_SCHEMA,
                "package": PACKAGE,
                "commit": provenance.commit,
                "artifactSha256": provenance.deb_sha256,
                "binarySha256": provenance.binary_sha256,
                "payloadSha256": provenance.payload_sha256,
                "version": provenance.version,
                "state": state,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class DpkgState(StrEnum):
    ABSENT = "absent"
    INSTALLED = "installed"
    NON_CLEAN = "non-clean"
    UNKNOWN = "unknown"


def _dpkg_state(completed: subprocess.CompletedProcess[str]) -> tuple[DpkgState, str | None]:
    """Classify dpkg-query without treating an arbitrary command error as package absence.

    dpkg-query documents exit 1 for an unmatched package. Other non-zero results are failures to
    establish state. A successful query must contain a status abbreviation and version.
    """
    if completed.returncode == 1:
        return DpkgState.ABSENT, None
    if completed.returncode != 0:
        return DpkgState.UNKNOWN, None
    fields = completed.stdout.strip().split(maxsplit=1)
    if len(fields) != 2:
        return DpkgState.UNKNOWN, None
    if fields[0] != "ii":
        return DpkgState.NON_CLEAN, None
    return DpkgState.INSTALLED, fields[1]


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

    provenance_path = _provenance_path(context, artifact)
    try:
        provenance = read_deb_provenance(provenance_path)
        problems = verify_deb(artifact, provenance)
        packaged = deb_packaged_files(artifact)
    except IdentityError as error:
        return _result(
            scenario,
            Outcome.BLOCKED,
            f"the build identity cannot be established: {error}",
            started,
            logs,
            observed="no package mutation or validation attempted",
            proof=str(provenance_path),
        )
    if problems or provenance.version != version or provenance.architecture != architecture:
        return _result(
            scenario,
            Outcome.FAIL,
            "the package does not match its provenance: "
            + "; ".join(problems or ["the dpkg-deb metadata differs from the provenance"]),
            started,
            logs,
            observed=f"commit={provenance.commit}; deb={provenance.deb_sha256}",
            proof=f"{provenance_path}; recomputed SHA-256 of the package and its binary",
        )
    identity = (
        f"commit={provenance.commit}; deb={provenance.deb_sha256}; "
        f"binary={provenance.binary_sha256}; payload={provenance.payload_sha256}"
    )

    receipt_path = _receipt_path(context)
    receipt_present = receipt_path.exists()
    receipt = _read_receipt(receipt_path)
    matching_receipt = receipt is not None and all(
        (
            receipt.get("schemaVersion") == RECEIPT_SCHEMA,
            receipt.get("package") == PACKAGE,
            receipt.get("commit") == provenance.commit,
            receipt.get("artifactSha256") == provenance.deb_sha256,
            receipt.get("binarySha256") == provenance.binary_sha256,
            receipt.get("payloadSha256") == provenance.payload_sha256,
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
    dpkg_state, current_version = _dpkg_state(installed)
    installation_origin = "new recipe installation"
    skip_install = False
    if dpkg_state is DpkgState.INSTALLED:
        assert current_version is not None
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
        installed_binary = active_host.file_sha256(APPLICATION)
        if installed_binary is None:
            return _result(
                scenario,
                Outcome.BLOCKED,
                "the installed application binary cannot be read: its build is unknown",
                started,
                logs,
                observed=f"{installation_origin}: {APPLICATION} unreadable",
                proof="installed binary SHA-256",
            )
        installed_payload = active_host.file_sha256(INSTALLED_PAYLOAD)
        if installed_payload is None:
            return _result(
                scenario,
                Outcome.BLOCKED,
                "dpkg's file list of the installed package cannot be read: its build is unknown",
                started,
                logs,
                observed=f"{installation_origin}: {INSTALLED_PAYLOAD} unreadable",
                proof="installed md5sums SHA-256",
            )
        if (
            installed_binary != provenance.binary_sha256
            or installed_payload != provenance.payload_sha256
        ):
            return _result(
                scenario,
                Outcome.BLOCKED,
                f"the installed control-tv {version} is another build than the artifact",
                started,
                logs,
                observed=(
                    f"{installation_origin}: installed binary={installed_binary}, "
                    f"payload={installed_payload}; artifact binary={provenance.binary_sha256}, "
                    f"payload={provenance.payload_sha256}"
                ),
                proof="installed binary and md5sums SHA-256 against the provenance",
            )
        file_problems = active_host.installed_file_problems()
        if file_problems is None or file_problems:
            return _result(
                scenario,
                Outcome.BLOCKED,
                "the installed files cannot be checked"
                if file_problems is None
                else "the installed files differ from the package (modified or partial)",
                started,
                logs,
                observed=f"{installation_origin}: {'; '.join((file_problems or [])[:5])}",
                proof="every installed file against dpkg's md5sums record",
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
    elif dpkg_state is DpkgState.NON_CLEAN:
        return _result(
            scenario,
            Outcome.BLOCKED,
            "control-tv has a non-clean package state; manual recovery is required",
            started,
            logs,
            observed=installed.stdout.strip() or installed.stderr.strip(),
            proof="dpkg-query; no package mutation attempted",
        )
    elif dpkg_state is DpkgState.UNKNOWN:
        return _result(
            scenario,
            Outcome.BLOCKED,
            "the installed package state cannot be established; manual review is required",
            started,
            logs,
            observed=installed.stderr.strip() or installed.stdout.strip() or "dpkg-query failed",
            proof="dpkg-query; no APT or package mutation attempted",
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
    elif dpkg_state is DpkgState.ABSENT:
        leftovers = active_host.package_leftovers(packaged)
        if leftovers is None or leftovers:
            return _result(
                scenario,
                Outcome.BLOCKED,
                "the absence of a prior control-tv installation cannot be established"
                if leftovers is None
                else "dpkg has no control-tv record but package files remain",
                started,
                logs,
                observed="leftover check unavailable"
                if leftovers is None
                else "; ".join(leftovers[:5]),
                proof="dpkg-query and package file inventory; no APT or package mutation attempted",
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

        _write_receipt(receipt_path, provenance=provenance, state="installing")
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
        installed_state, installed_version_value = _dpkg_state(installed_version)
        if installed_state is not DpkgState.INSTALLED or installed_version_value != version:
            return _result(
                scenario,
                Outcome.FAIL,
                "installed package version does not match the artifact; recovery receipt retained",
                started,
                logs,
                observed=installed_version.stdout.strip(),
                proof=str(receipt_path),
            )
        if (
            active_host.file_sha256(APPLICATION) != provenance.binary_sha256
            or active_host.file_sha256(INSTALLED_PAYLOAD) != provenance.payload_sha256
        ):
            return _result(
                scenario,
                Outcome.FAIL,
                "the installed binary or files are not the artifact's; recovery receipt retained",
                started,
                logs,
                observed=identity,
                proof=f"installed binary SHA-256; {receipt_path}",
            )
        file_problems = active_host.installed_file_problems()
        if file_problems is None or file_problems:
            return _result(
                scenario,
                Outcome.FAIL,
                "the installed files do not match the package; recovery receipt retained",
                started,
                logs,
                observed="; ".join((file_problems or ["the files cannot be checked"])[:5]),
                proof=f"every installed file against dpkg's md5sums record; {receipt_path}",
            )
        _write_receipt(receipt_path, provenance=provenance, state="installed")

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
                            f"version={version}; {identity}; origin={installation_origin}; "
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
