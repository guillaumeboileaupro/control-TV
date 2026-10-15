"""Unit tests for the acceptance orchestrator; all devices and processes are doubles."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


acceptance = _load("acceptance")
run_acceptance = _load("run_acceptance")


def environment(*, tools: dict[str, str | None] | None = None) -> Any:
    return acceptance.Environment("Linux", "x86_64", "3.12", "abc", tools or {}, {}, {})


def passing(scenario: Any) -> Any:
    def run(context: Any) -> Any:
        del context
        return acceptance.Result(
            scenario.id, scenario.category, acceptance.Outcome.PASS, "simulated", 0.01, "now"
        )

    return run


def test_default_policy_blocks_execution_and_writes_all_formats(tmp_path: Path) -> None:
    scenario = acceptance.Scenario("tv.pause", "google-tv", "pause", lambda context: None)
    runner = acceptance.AcceptanceRunner(
        tmp_path, tmp_path / "reports", acceptance.Policy(), environment()
    )

    results = runner.run([scenario])

    assert results[0].outcome is acceptance.Outcome.BLOCKED
    assert "authorization missing: execute" in results[0].reason
    payload = json.loads((tmp_path / "reports" / "acceptance.json").read_text())
    assert payload["dry_run"] is True
    assert payload["results"][0]["outcome"] == "BLOCKED"
    assert payload["results"][0]["evidence"] == "static"
    assert (tmp_path / "reports" / "acceptance.html").is_file()
    skipped = ET.parse(tmp_path / "reports" / "acceptance.junit.xml").find(".//skipped")
    assert skipped is not None


@pytest.mark.parametrize(
    ("capability", "policy"),
    [
        (acceptance.Capability.TV_COMMAND, acceptance.Policy(execute=True)),
        (acceptance.Capability.ADB, acceptance.Policy(execute=True)),
        (acceptance.Capability.INSTALL_DEB, acceptance.Policy(execute=True)),
        (acceptance.Capability.INSTALL_APK, acceptance.Policy(execute=True)),
    ],
)
def test_execute_does_not_imply_sensitive_permission(capability: Any, policy: Any) -> None:
    allowed, reason = policy.permits(frozenset({capability}))
    assert allowed is False
    assert capability.value in reason


def test_authorized_scenario_runs_once(tmp_path: Path) -> None:
    calls = 0
    scenario = acceptance.Scenario("local", "ubuntu", "local", lambda context: None)

    def run(context: Any) -> Any:
        nonlocal calls
        calls += 1
        return passing(scenario)(context)

    scenario = acceptance.Scenario("local", "ubuntu", "local", run)
    runner = acceptance.AcceptanceRunner(
        tmp_path, tmp_path / "reports", acceptance.Policy(execute=True), environment()
    )
    assert runner.run([scenario])[0].outcome is acceptance.Outcome.PASS
    assert calls == 1


def test_missing_dependency_is_skip_not_pass(tmp_path: Path) -> None:
    scenario = acceptance.Scenario(
        "android", "android", "device", lambda context: None, dependencies=("adb",)
    )
    runner = acceptance.AcceptanceRunner(
        tmp_path, tmp_path / "reports", acceptance.Policy(execute=True), environment()
    )
    assert runner.run([scenario])[0].outcome is acceptance.Outcome.SKIP


def test_timeout_and_exception_are_failures(tmp_path: Path) -> None:
    def timeout(context: Any) -> Any:
        raise subprocess.TimeoutExpired(["probe"], 2)

    def crash(context: Any) -> Any:
        raise RuntimeError("boom")

    scenarios = [
        acceptance.Scenario("timeout", "ubuntu", "timeout", timeout),
        acceptance.Scenario("crash", "ubuntu", "crash", crash),
    ]
    runner = acceptance.AcceptanceRunner(
        tmp_path, tmp_path / "reports", acceptance.Policy(execute=True), environment(), 2
    )
    results = runner.run(scenarios)
    assert [result.outcome for result in results] == [
        acceptance.Outcome.FAIL,
        acceptance.Outcome.FAIL,
    ]
    assert acceptance.exit_code(results) == 1


def test_resume_keeps_only_completed_pass(tmp_path: Path) -> None:
    report = tmp_path / "reports"
    calls: list[str] = []
    scenarios = []
    for scenario_id in ("pass", "skip", "blocked"):
        placeholder = acceptance.Scenario(scenario_id, "ubuntu", scenario_id, lambda context: None)

        def run(context: Any, *, current: Any = placeholder) -> Any:
            calls.append(current.id)
            return passing(current)(context)

        scenarios.append(acceptance.Scenario(scenario_id, "ubuntu", scenario_id, run))
    first = acceptance.AcceptanceRunner(
        tmp_path, report, acceptance.Policy(execute=True), environment()
    )
    first.write_reports(
        [
            acceptance.Result("pass", "ubuntu", acceptance.Outcome.PASS, "done", 1, "now"),
            acceptance.Result("skip", "ubuntu", acceptance.Outcome.SKIP, "missing", 0, "now"),
            acceptance.Result(
                "blocked", "ubuntu", acceptance.Outcome.BLOCKED, "needs permission", 0, "now"
            ),
        ]
    )
    results = first.run(scenarios, resume=True)
    assert [result.outcome for result in results] == [
        acceptance.Outcome.PASS,
        acceptance.Outcome.PASS,
        acceptance.Outcome.PASS,
    ]
    assert calls == ["skip", "blocked"]


def test_environment_detection_never_invokes_adb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def subprocess_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="abc\n", stderr="")

    monkeypatch.setattr(acceptance.subprocess, "run", subprocess_run)
    detected = acceptance.detect_environment(
        tmp_path, environ={"CONTROL_TV_ANDROID_SERIAL": "simulated"}, which=lambda name: f"/{name}"
    )
    assert detected.devices == {"android": "simulated"}
    assert all(command[0] != "/adb" for command in calls)


def test_catalog_is_unique_and_dry_run_runs_only_static_scenarios(tmp_path: Path) -> None:
    scenarios = run_acceptance.load_scenarios()
    assert len({scenario.id for scenario in scenarios}) == len(scenarios)
    results = acceptance.AcceptanceRunner(
        tmp_path, tmp_path / "reports", acceptance.Policy(), environment()
    ).run(scenarios)
    assert results
    assert all(
        result.evidence is acceptance.Evidence.STATIC
        or result.outcome is acceptance.Outcome.BLOCKED
        for result in results
    )
    assert not any(
        result.evidence is not acceptance.Evidence.STATIC
        and result.outcome is acceptance.Outcome.PASS
        for result in results
    )
