"""Ubuntu acceptance checks, explicitly split by evidence level."""

from __future__ import annotations

from pathlib import Path

from acceptance import Capability, Context, Evidence, Result, Scenario
from acceptance_platforms import (
    always_success,
    command_check,
    configured_artifact,
    csp_check,
    deb_contents_check,
    source_check,
)
from acceptance_ubuntu import run_ubuntu_acceptance

ROOT = Path(__file__).resolve().parents[2]


def deb_metadata(context: Context) -> Result:
    scenario = SCENARIOS[0]
    artifact = configured_artifact(context, scenario, "deb")
    if isinstance(artifact, Result):
        return artifact

    def validate(output: str) -> tuple[bool, str]:
        fields = dict(line.split(":", 1) for line in output.splitlines() if ":" in line)
        architecture = fields.get(" Architecture", fields.get("Architecture", "")).strip()
        package = fields.get(" Package", fields.get("Package", "")).strip()
        dependencies = fields.get(" Depends", fields.get("Depends", ""))
        required = (
            "ffmpeg",
            "libwebkit2gtk-4.1-0",
            "libayatana-appindicator3-1",
            "x11-xserver-utils",
        )
        missing = [name for name in required if name not in dependencies]
        valid = architecture == "amd64" and package == "control-tv" and not missing
        reason = (
            "amd64 metadata and system dependencies present"
            if valid
            else (
                f"metadata mismatch: architecture={architecture!r}, package={package!r}, "
                f"missing_dependencies={missing}"
            )
        )
        return valid, reason

    return command_check(context, scenario, ["dpkg-deb", "-f", str(artifact)], validate)


def deb_contents(context: Context) -> Result:
    scenario = SCENARIOS[1]
    artifact = configured_artifact(context, scenario, "deb")
    if isinstance(artifact, Result):
        return artifact

    return command_check(context, scenario, ["dpkg-deb", "-c", str(artifact)], deb_contents_check)


def csp(context: Context) -> Result:
    del context
    return csp_check(ROOT, SCENARIOS[2])


def tray(context: Context) -> Result:
    del context
    return source_check(
        ROOT,
        SCENARIOS[3],
        (
            (
                Path("src-tauri/src/tray.rs"),
                (
                    "play_pause",
                    "stop",
                    "tv_mute",
                    "tv_volume_down",
                    "tv_volume_up",
                    "TrayIconBuilder",
                ),
            ),
            (Path("ui/src/tray.ts"), ("trayRequest",)),
        ),
    )


def simulated(context: Context) -> Result:
    return command_check(
        context,
        SCENARIOS[4],
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "pytest",
            "-q",
            str(ROOT / "tests/bridge"),
            str(ROOT / "tests/test_release_linux.py"),
        ],
        always_success,
    )


def real(context: Context) -> Result:
    return run_ubuntu_acceptance(context, SCENARIOS[5])


SCENARIOS = (
    Scenario(
        "ubuntu.static.deb-metadata",
        "ubuntu",
        "Inspect amd64 Debian metadata and dependencies",
        deb_metadata,
        frozenset(),
        ("dpkg-deb",),
        Evidence.STATIC,
    ),
    Scenario(
        "ubuntu.static.deb-contents",
        "ubuntu",
        "Inspect packaged bridge, executable and icon",
        deb_contents,
        frozenset(),
        ("dpkg-deb",),
        Evidence.STATIC,
    ),
    Scenario(
        "ubuntu.static.csp-ipc",
        "ubuntu",
        "Verify production CSP and IPC allowance",
        csp,
        frozenset(),
        (),
        Evidence.STATIC,
    ),
    Scenario(
        "ubuntu.static.tray",
        "ubuntu",
        "Verify GNOME tray icon and action wiring",
        tray,
        frozenset(),
        (),
        Evidence.STATIC,
    ),
    Scenario(
        "ubuntu.simulated.launch-ipc",
        "ubuntu",
        "Exercise bridge, IPC, startup failures and package contracts",
        simulated,
        frozenset({Capability.EXECUTE}),
        (),
        Evidence.SIMULATED,
    ),
    Scenario(
        "ubuntu.real.launch-tray-logs",
        "ubuntu",
        "Launch installed Tauri app and capture tray/log evidence",
        real,
        frozenset({Capability.EXECUTE, Capability.INSTALL_DEB}),
        ("apt-get", "dpkg-deb", "dpkg-query", "gdbus", "sudo"),
        Evidence.REAL,
    ),
)


def test_ubuntu_evidence_and_permissions_are_separated() -> None:
    assert all(not scenario.capabilities for scenario in SCENARIOS[:4])
    assert SCENARIOS[4].capabilities == frozenset({Capability.EXECUTE})
    assert Capability.INSTALL_DEB in SCENARIOS[5].capabilities
    assert SCENARIOS[5].evidence is Evidence.REAL
