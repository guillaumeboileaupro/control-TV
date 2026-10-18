"""Real scenarios without an automated adapter stay BLOCKED and are covered by the manual
protocol: the runner never claims it can drive the TV or the phone."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

# The only real scenario with an automated adapter (fail-closed, see test_acceptance_ubuntu.py).
AUTOMATED_REAL = {"ubuntu.real.launch-tray-logs"}
MANUAL = {
    *(f"R{n}" for n in range(1, 5)),
    *(f"C{n}" for n in range(1, 8)),
    *(f"K{n}" for n in range(1, 4)),
    "TV-POWER",
    "TV-KEYBOARD",
    "TV-APPS",
    "TV-ERRORS",
    "android.real.install-launch-logs",
    "dual-controller.convergence",
    "latency.remote-keys",
}


def load(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


acceptance = load("acceptance")
for _module in (
    "acceptance_identity",
    "acceptance_platforms",
    "acceptance_ubuntu",
    "acceptance_google_tv",
    "acceptance_latency",
):
    load(_module)
run_acceptance = load("run_acceptance")


def everything_authorized(tmp_path: Path) -> Any:
    policy = acceptance.Policy(
        execute=True,
        tv_commands=True,
        install_deb=True,
        validate_existing_deb=True,
        install_apk=True,
        adb=True,
    )
    environment = acceptance.Environment(
        "Linux", "x86_64", "3.12", "sha", {}, {}, {"google_tv": "configured", "android": "x"}
    )
    return acceptance.Context(
        policy, environment, 10, acceptance.SubprocessRunner(), tmp_path / "reports"
    )


def manual_scenarios() -> list[Any]:
    return [s for s in run_acceptance.load_scenarios() if s.id in MANUAL]


def test_every_real_scenario_is_either_automated_or_manual() -> None:
    real = {
        s.id
        for s in run_acceptance.load_scenarios()
        if s.evidence is acceptance.Evidence.REAL or s.id in MANUAL
    }
    assert real == AUTOMATED_REAL | MANUAL


@pytest.mark.parametrize("scenario_id", sorted(MANUAL))
def test_a_manual_scenario_stays_blocked_even_with_every_flag(
    tmp_path: Path, scenario_id: str
) -> None:
    scenario = next(s for s in manual_scenarios() if s.id == scenario_id)

    result = scenario.run(everything_authorized(tmp_path))

    assert result.outcome.value == "BLOCKED"
    assert "no automated adapter exists" in result.reason
    assert acceptance.MANUAL_PROTOCOL in result.reason
    assert "owner's explicit authorization" in result.reason
    assert result.observed == "scenario not executed"


def test_the_manual_protocol_covers_every_manual_scenario() -> None:
    protocol = (ROOT / acceptance.MANUAL_PROTOCOL).read_text(encoding="utf-8")

    missing = [
        scenario_id
        for scenario_id in sorted(MANUAL)
        if f"`{scenario_id}`" not in protocol
        # a range written `R1`-`R4` (en dash) covers its members
        and not any(
            f"`{scenario_id[0]}1`\u2013`{scenario_id[0]}{last}`" in protocol for last in "347"
        )
    ]
    assert missing == []


def test_no_plan_claims_the_tv_or_phone_steps_run_automatically() -> None:
    plan = (ROOT / "docs/HARDWARE_ACCEPTANCE_PLAN.md").read_text(encoding="utf-8")

    assert "--category google-tv --execute" not in plan
    assert "--category android --execute --allow-apk-install" not in plan
    assert "MANUAL_ACCEPTANCE_PROTOCOL.md" in plan
