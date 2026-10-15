"""Google TV acceptance scenario declarations."""

from __future__ import annotations

import time

from acceptance import Capability, Context, Outcome, Result, Scenario, make_result


def infrastructure_only(context: Context) -> Result:
    del context
    scenario = next(item for item in SCENARIOS if item.run is infrastructure_only)
    return make_result(
        scenario,
        Outcome.BLOCKED,
        "hardware adapter is intentionally absent in the infrastructure-only phase",
        started=time.monotonic(),
    )


SCENARIOS = (
    Scenario(
        "google-tv.control",
        "google-tv",
        "Validate explicitly authorized Google TV operations",
        infrastructure_only,
        frozenset({Capability.EXECUTE, Capability.TV_COMMAND}),
    ),
)


def test_google_tv_requires_explicit_command_authorization() -> None:
    assert Capability.TV_COMMAND in SCENARIOS[0].capabilities
