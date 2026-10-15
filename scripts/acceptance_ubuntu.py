"""Real Ubuntu acceptance adapter, isolated behind an injectable host boundary."""

from __future__ import annotations

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


class ManagedProcess(Protocol):
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
    return frozenset(re.findall(r"(?:org\.[A-Za-z0-9_.-]+|/[A-Za-z0-9_./-]+)", output))


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
    installed = active_host.run(
        ("dpkg-query", "-W", "-f=${db:Status-Abbrev} ${Version}", PACKAGE), timeout=timeout
    )
    logs.append(_completed_log("dpkg-query installed control-tv", installed))
    if installed.returncode == 0 and installed.stdout.startswith("ii "):
        return _result(
            scenario,
            Outcome.BLOCKED,
            "control-tv is already installed; replacement/removal needs separate authorization",
            started,
            logs,
            observed=installed.stdout.strip(),
            proof="dpkg-query",
        )

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
            "Debian package installation failed",
            started,
            logs,
            observed="APT install failed",
            proof="apt-get install",
        )

    installed_version = active_host.run(
        ("dpkg-query", "-W", "-f=${db:Status-Abbrev} ${Version}", PACKAGE), timeout=timeout
    )
    logs.append(_completed_log("dpkg-query installed version", installed_version))
    if (
        installed_version.returncode != 0
        or not installed_version.stdout.startswith("ii ")
        or version not in installed_version.stdout
    ):
        return _result(
            scenario,
            Outcome.FAIL,
            "installed package version does not match the artifact",
            started,
            logs,
            observed=installed_version.stdout.strip(),
            proof="post-install dpkg-query",
        )

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
                if watcher_after.returncode != 0 or not new_items:
                    reason = "no new tray item was registered by the running application"
                    observed = f"new tray items: {sorted(new_items)}"
                    proof = "before/after gdbus watcher queries and application logs"
                else:
                    outcome = Outcome.PASS
                    reason = "installed Tauri app, Python bridge and GNOME tray are operational"
                    observed = f"version={version}; new tray items={sorted(new_items)}"
                    proof = "dpkg-query, process state, bridge ping, gdbus and application logs"
    finally:
        _stop(process, min(timeout, 5.0), logs)
    return _result(
        scenario,
        outcome,
        reason,
        started,
        logs,
        observed=observed,
        proof=proof,
    )
