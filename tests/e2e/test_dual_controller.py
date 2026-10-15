"""Two-controller acceptance scenario declarations."""

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
        "dual-controller.convergence",
        "dual-controller",
        "Run R1-R4 and explicitly authorized C1-C7",
        infrastructure_only,
        frozenset({Capability.EXECUTE, Capability.TV_COMMAND, Capability.ADB}),
        ("adb",),
    ),
)


def test_dual_controller_declares_all_device_permissions() -> None:
    assert SCENARIOS[0].capabilities == frozenset(
        {Capability.EXECUTE, Capability.TV_COMMAND, Capability.ADB}
    )
