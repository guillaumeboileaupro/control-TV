"""Latency acceptance scenario declarations."""

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
        "latency.remote-keys",
        "latency",
        "Capture K1-K3 timing evidence without inferring unobserved delivery",
        infrastructure_only,
        frozenset({Capability.EXECUTE, Capability.TV_COMMAND}),
    ),
)


def test_latency_never_runs_in_default_dry_run() -> None:
    assert Capability.EXECUTE in SCENARIOS[0].capabilities
    assert Capability.TV_COMMAND in SCENARIOS[0].capabilities
