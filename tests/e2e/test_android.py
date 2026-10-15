"""Android acceptance checks, explicitly split by evidence level."""

from __future__ import annotations

import time
from pathlib import Path

from acceptance import Capability, Context, Evidence, Outcome, Result, Scenario, make_result
from acceptance_platforms import (
    always_success,
    apk_archive_check,
    command_check,
    configured_artifact,
    csp_check,
    real_adapter_blocked,
    source_check,
)

ROOT = Path(__file__).resolve().parents[2]


def apk_archive(context: Context) -> Result:
    scenario = SCENARIOS[0]
    started = time.monotonic()
    artifact = configured_artifact(context, scenario, "apk")
    if isinstance(artifact, Result):
        return artifact
    valid, reason = apk_archive_check(artifact)
    return make_result(scenario, Outcome.PASS if valid else Outcome.FAIL, reason, started=started)


def apk_metadata(context: Context) -> Result:
    scenario = SCENARIOS[1]
    artifact = configured_artifact(context, scenario, "apk")
    if isinstance(artifact, Result):
        return artifact

    def validate(output: str) -> tuple[bool, str]:
        required = (
            "package: name='io.github.guillaumeboileaupro.controltv'",
            "uses-permission: name='android.permission.INTERNET'",
            "uses-permission: name='android.permission.CHANGE_WIFI_MULTICAST_STATE'",
        )
        missing = [line for line in required if line not in output]
        return (
            not missing,
            "APK identity and permissions present"
            if not missing
            else "missing metadata: " + ", ".join(missing),
        )

    return command_check(context, scenario, ["aapt", "dump", "badging", str(artifact)], validate)


def android_sources(context: Context) -> Result:
    del context
    return source_check(
        ROOT,
        SCENARIOS[2],
        (
            (
                Path("src-tauri/gen/android/app/src/main/AndroidManifest.xml"),
                (
                    "android.permission.INTERNET",
                    "android.permission.CHANGE_WIFI_MULTICAST_STATE",
                    "WidgetTapReceiver",
                    'android:exported="false"',
                ),
            ),
            (
                Path("src-tauri/src/android.rs"),
                ("EmbeddedBridge", "PythonBridge::in_process", "control-bridge"),
            ),
            (
                Path(
                    "src-tauri/gen/android/app/src/main/java/"
                    "io/github/guillaumeboileaupro/controltv/EmbeddedBridge.kt"
                ),
                ("Python", "bridge"),
            ),
            (
                Path(
                    "src-tauri/gen/android/app/src/main/java/"
                    "io/github/guillaumeboileaupro/controltv/ControlTvWidget.kt"
                ),
                ("redrawsAfter", "WidgetAction"),
            ),
        ),
    )


def csp(context: Context) -> Result:
    del context
    return csp_check(ROOT, SCENARIOS[3])


def simulated(context: Context) -> Result:
    return command_check(
        context,
        SCENARIOS[4],
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "pytest",
            "-q",
            str(ROOT / "tests/test_android_python.py"),
            str(ROOT / "tests/test_android_widget_sources.py"),
            str(ROOT / "tests/bridge/test_embedded.py"),
        ],
        always_success,
    )


def real(context: Context) -> Result:
    return real_adapter_blocked(context, SCENARIOS[5])


SCENARIOS = (
    Scenario(
        "android.static.apk-archive",
        "android",
        "Inspect APK archive, arm64 ABI and embedded Python bridge",
        apk_archive,
        frozenset(),
        (),
        Evidence.STATIC,
    ),
    Scenario(
        "android.static.apk-metadata",
        "android",
        "Inspect APK identity and permissions with aapt",
        apk_metadata,
        frozenset(),
        ("aapt",),
        Evidence.STATIC,
    ),
    Scenario(
        "android.static.bridge-widget",
        "android",
        "Verify permissions, embedded bridge, IPC and widget sources",
        android_sources,
        frozenset(),
        (),
        Evidence.STATIC,
    ),
    Scenario(
        "android.static.csp",
        "android",
        "Verify production Tauri CSP",
        csp,
        frozenset(),
        (),
        Evidence.STATIC,
    ),
    Scenario(
        "android.simulated.bridge-widget-startup",
        "android",
        "Exercise embedded bridge, widget and startup-error contracts",
        simulated,
        frozenset({Capability.EXECUTE}),
        (),
        Evidence.SIMULATED,
    ),
    Scenario(
        "android.real.install-launch-logs",
        "android",
        "Install, launch and collect explicitly authorized device logs",
        real,
        frozenset({Capability.EXECUTE, Capability.ADB, Capability.INSTALL_APK}),
        ("adb",),
        Evidence.REAL,
    ),
)


def test_android_evidence_and_permissions_are_separated() -> None:
    assert all(not scenario.capabilities for scenario in SCENARIOS[:4])
    assert SCENARIOS[4].capabilities == frozenset({Capability.EXECUTE})
    assert SCENARIOS[5].capabilities == frozenset(
        {Capability.EXECUTE, Capability.ADB, Capability.INSTALL_APK}
    )
    assert SCENARIOS[5].evidence is Evidence.REAL
