"""Two-controller acceptance scenario declarations."""

from __future__ import annotations

from acceptance import Capability, Context, Result, Scenario, manual_only


def infrastructure_only(context: Context) -> Result:
    del context
    return manual_only(SCENARIOS[0], "the two-remote steps R1-R4 and C1-C7")


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
