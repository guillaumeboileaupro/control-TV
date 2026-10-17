"""Latency acceptance scenario declarations."""

from __future__ import annotations

from acceptance import Capability, Context, Result, Scenario, manual_only


def infrastructure_only(context: Context) -> Result:
    del context
    return manual_only(SCENARIOS[0], "the K1-K3 latency measurements")


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
