"""Reusable, fail-closed acceptance-test orchestration.

This module contains no product logic and sends no device command. Concrete scenarios are
registered by ``run_acceptance.py`` and must declare every sensitive capability they need.
"""

from __future__ import annotations

import html
import json
import os
import platform
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    BLOCKED = "BLOCKED"


class Evidence(StrEnum):
    STATIC = "static"
    SIMULATED = "simulated"
    REAL = "real"


class Capability(StrEnum):
    EXECUTE = "execute"
    TV_COMMAND = "tv-command"
    INSTALL_DEB = "install-deb"
    INSTALL_APK = "install-apk"
    ADB = "adb"


@dataclass(frozen=True)
class Policy:
    execute: bool = False
    tv_commands: bool = False
    install_deb: bool = False
    install_apk: bool = False
    adb: bool = False

    def permits(self, capabilities: frozenset[Capability]) -> tuple[bool, str]:
        enabled = {
            Capability.EXECUTE: self.execute,
            Capability.TV_COMMAND: self.execute and self.tv_commands,
            Capability.INSTALL_DEB: self.execute and self.install_deb,
            Capability.INSTALL_APK: self.execute and self.install_apk,
            Capability.ADB: self.execute and self.adb,
        }
        missing = sorted(capability.value for capability in capabilities if not enabled[capability])
        if missing:
            return False, "authorization missing: " + ", ".join(missing)
        return True, "authorized"


@dataclass(frozen=True)
class Environment:
    system: str
    machine: str
    python: str
    commit: str | None
    tools: Mapping[str, str | None]
    artifacts: Mapping[str, str | None]
    devices: Mapping[str, str]


@dataclass(frozen=True)
class Result:
    scenario_id: str
    category: str
    outcome: Outcome
    reason: str
    duration_seconds: float
    started_at: str
    log: str = ""
    evidence: Evidence = Evidence.STATIC


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    description: str
    run: Callable[[Context], Result]
    capabilities: frozenset[Capability] = frozenset({Capability.EXECUTE})
    dependencies: tuple[str, ...] = ()
    evidence: Evidence = Evidence.STATIC


@dataclass(frozen=True)
class Context:
    policy: Policy
    environment: Environment
    timeout_seconds: float
    command: CommandRunner


class CommandRunner(Protocol):
    def run(self, args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]: ...


class SubprocessRunner:
    """Runs a bounded local process without a shell."""

    def run(self, args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(args), capture_output=True, text=True, check=False, timeout=timeout, shell=False
        )


def detect_environment(
    root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Environment:
    """Inspect local prerequisites only; never contacts a device."""
    values = os.environ if environ is None else environ
    tools = {
        name: which(name) for name in ("aapt", "adb", "cargo", "dpkg-deb", "git", "npm", "python3")
    }
    artifacts = {
        "deb": values.get("CONTROL_TV_DEB"),
        "apk": values.get("CONTROL_TV_APK"),
    }
    commit: str | None = None
    git_head = root / ".git"
    if git_head.exists() and tools["git"]:
        completed = subprocess.run(
            [tools["git"], "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if completed.returncode == 0:
            commit = completed.stdout.strip()
    # Device values are configuration hints only. Detection commands belong to explicit,
    # authorized scenario adapters; in particular, adb is never started here.
    devices = {
        key: value
        for key, value in {
            "google_tv": values.get("CONTROL_TV_GOOGLE_TV"),
            "android": values.get("CONTROL_TV_ANDROID_SERIAL"),
        }.items()
        if value
    }
    return Environment(
        system=platform.system(),
        machine=platform.machine(),
        python=platform.python_version(),
        commit=commit,
        tools=tools,
        artifacts=artifacts,
        devices=devices,
    )


def make_result(
    scenario: Scenario,
    outcome: Outcome,
    reason: str,
    *,
    started: float,
    log: str = "",
) -> Result:
    return Result(
        scenario_id=scenario.id,
        category=scenario.category,
        outcome=outcome,
        reason=reason,
        duration_seconds=round(time.monotonic() - started, 6),
        started_at=datetime.now(UTC).isoformat(),
        log=log,
        evidence=scenario.evidence,
    )


@dataclass
class AcceptanceRunner:
    root: Path
    report_dir: Path
    policy: Policy
    environment: Environment
    timeout_seconds: float = 30.0
    command: CommandRunner = field(default_factory=SubprocessRunner)

    @property
    def checkpoint_path(self) -> Path:
        return self.report_dir / "acceptance.json"

    def _prior_results(self, resume: bool) -> dict[str, Result]:
        if not resume or not self.checkpoint_path.is_file():
            return {}
        raw = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        return {
            item["scenario_id"]: Result(
                **{
                    **item,
                    "outcome": Outcome(item["outcome"]),
                    "evidence": Evidence(item.get("evidence", Evidence.STATIC.value)),
                }
            )
            for item in raw.get("results", [])
            if item.get("outcome") == Outcome.PASS.value
        }

    def run(self, scenarios: Sequence[Scenario], *, resume: bool = False) -> list[Result]:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        prior = self._prior_results(resume)
        results: list[Result] = []
        context = Context(self.policy, self.environment, self.timeout_seconds, self.command)
        for scenario in scenarios:
            if scenario.id in prior:
                result = prior[scenario.id]
            else:
                started = time.monotonic()
                permitted, reason = self.policy.permits(scenario.capabilities)
                missing = [
                    name for name in scenario.dependencies if not self.environment.tools.get(name)
                ]
                if not permitted:
                    result = make_result(scenario, Outcome.BLOCKED, reason, started=started)
                elif missing:
                    result = make_result(
                        scenario,
                        Outcome.SKIP,
                        "dependencies unavailable: " + ", ".join(missing),
                        started=started,
                    )
                else:
                    try:
                        result = scenario.run(context)
                    except subprocess.TimeoutExpired:
                        result = make_result(
                            scenario,
                            Outcome.FAIL,
                            f"timeout after {self.timeout_seconds:g}s",
                            started=started,
                        )
                    except Exception as exc:  # scenario boundary: record, then continue
                        result = make_result(
                            scenario,
                            Outcome.FAIL,
                            f"{type(exc).__name__}: {exc}",
                            started=started,
                        )
            results.append(result)
            self.write_reports(results)
        return results

    def write_reports(self, results: Sequence[Result]) -> None:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "dry_run": not self.policy.execute,
            "environment": asdict(self.environment),
            "results": [
                {
                    **asdict(result),
                    "outcome": result.outcome.value,
                    "evidence": result.evidence.value,
                }
                for result in results
            ],
        }
        self.checkpoint_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self._write_html(results)
        self._write_junit(results)

    def _write_html(self, results: Sequence[Result]) -> None:
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(result.scenario_id)}</td>"
            f"<td>{html.escape(result.category)}</td>"
            f"<td>{result.evidence.value}</td>"
            f"<td>{result.outcome.value}</td>"
            f"<td>{html.escape(result.reason)}</td>"
            f"<td>{result.duration_seconds:.3f}</td>"
            "</tr>"
            for result in results
        )
        document = (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            "<title>control-TV acceptance</title><h1>Acceptance results</h1>"
            "<table><thead><tr><th>Scenario</th><th>Category</th><th>Evidence</th><th>Outcome</th>"
            f"<th>Reason</th><th>Seconds</th></tr></thead><tbody>{rows}</tbody></table></html>\n"
        )
        (self.report_dir / "acceptance.html").write_text(document, encoding="utf-8")

    def _write_junit(self, results: Sequence[Result]) -> None:
        suite = ET.Element(
            "testsuite",
            name="control-tv-acceptance",
            tests=str(len(results)),
            failures=str(sum(result.outcome is Outcome.FAIL for result in results)),
            skipped=str(
                sum(result.outcome in {Outcome.SKIP, Outcome.BLOCKED} for result in results)
            ),
        )
        for result in results:
            case = ET.SubElement(
                suite,
                "testcase",
                classname=f"acceptance.{result.evidence.value}.{result.category}",
                name=result.scenario_id,
                time=f"{result.duration_seconds:.6f}",
            )
            if result.outcome is Outcome.FAIL:
                ET.SubElement(case, "failure", message=result.reason).text = result.log
            elif result.outcome in {Outcome.SKIP, Outcome.BLOCKED}:
                ET.SubElement(case, "skipped", message=result.reason)
            if result.log:
                ET.SubElement(case, "system-out").text = result.log
        tree = ET.ElementTree(suite)
        ET.indent(tree)
        tree.write(
            self.report_dir / "acceptance.junit.xml",
            encoding="unicode",
            xml_declaration=True,
        )


def exit_code(results: Sequence[Result]) -> int:
    """Failures fail CI; blocked/skipped scenarios remain visible without becoming PASS."""
    return 1 if any(result.outcome is Outcome.FAIL for result in results) else 0
