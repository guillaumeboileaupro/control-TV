"""Android acceptance scenario declarations."""

from __future__ import annotations

import time

from acceptance import Capability, Context, Outcome, Result, Scenario, make_result


def infrastructure_only(context: Context) -> Result:
    del context
    return make_result(
        SCENARIOS[0],
        Outcome.BLOCKED,
        "hardware adapter is intentionally absent in the infrastructure-only phase",
        started=time.monotonic(),
    )


SCENARIOS = (
    Scenario(
        "android.application",
        "android",
        "Validate the Android application on an authorized device",
        infrastructure_only,
        frozenset({Capability.EXECUTE, Capability.ADB}),
        ("adb",),
    ),
)


def test_android_requires_explicit_adb_but_not_install_yet() -> None:
    assert Capability.ADB in SCENARIOS[0].capabilities
    assert Capability.INSTALL_APK not in SCENARIOS[0].capabilities
