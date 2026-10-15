"""Ubuntu acceptance scenario declarations."""

from __future__ import annotations

import time

from acceptance import Capability, Context, Outcome, Result, Scenario, make_result


def _ubuntu_package(context: Context) -> Result:
    scenario = SCENARIOS[0]
    started = time.monotonic()
    package = context.environment.artifacts.get("deb")
    if not package:
        return make_result(
            scenario, Outcome.SKIP, "CONTROL_TV_DEB is not configured", started=started
        )
    return make_result(
        scenario,
        Outcome.BLOCKED,
        "package inspection adapter is not enabled in the infrastructure-only phase",
        started=started,
    )


SCENARIOS = (
    Scenario(
        "ubuntu.package",
        "ubuntu",
        "Inspect and exercise the Ubuntu package",
        _ubuntu_package,
        frozenset({Capability.EXECUTE}),
        ("dpkg-deb",),
    ),
)


def test_ubuntu_scenario_declares_no_implicit_install() -> None:
    assert Capability.INSTALL_DEB not in SCENARIOS[0].capabilities
