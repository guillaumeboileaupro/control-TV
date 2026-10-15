#!/usr/bin/env python3
"""Run the control-TV acceptance scenario catalog (dry-run by default)."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from acceptance import AcceptanceRunner, Policy, Scenario, detect_environment, exit_code

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_FILES = (
    "test_ubuntu.py",
    "test_android.py",
    "test_google_tv.py",
    "test_dual_controller.py",
    "test_latency.py",
)


def load_scenarios(root: Path = ROOT) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for filename in SCENARIO_FILES:
        path = root / "tests" / "e2e" / filename
        spec = importlib.util.spec_from_file_location(f"control_tv_e2e_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load scenario catalog: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        typed_module: ModuleType = module
        declared = getattr(typed_module, "SCENARIOS", None)
        if not isinstance(declared, tuple) or not all(
            isinstance(item, Scenario) for item in declared
        ):
            raise TypeError(f"{path} must expose tuple[Scenario, ...] as SCENARIOS")
        scenarios.extend(declared)
    ids = [scenario.id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario identifiers must be unique")
    return scenarios


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--category", action="append", help="run only this category")
    result.add_argument("--report-dir", type=Path, default=ROOT / "reports" / "acceptance")
    result.add_argument("--timeout", type=float, default=30.0)
    result.add_argument("--resume", action="store_true")
    result.add_argument("--execute", action="store_true", help="leave dry-run mode")
    result.add_argument("--allow-tv-commands", action="store_true")
    result.add_argument("--allow-deb-install", action="store_true")
    result.add_argument("--allow-apk-install", action="store_true")
    result.add_argument("--enable-adb", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.timeout <= 0:
        parser().error("--timeout must be positive")
    scenarios = load_scenarios()
    if args.category:
        requested = set(args.category)
        scenarios = [scenario for scenario in scenarios if scenario.category in requested]
        unknown = requested - {scenario.category for scenario in load_scenarios()}
        if unknown:
            parser().error("unknown categories: " + ", ".join(sorted(unknown)))
    policy = Policy(
        execute=args.execute,
        tv_commands=args.allow_tv_commands,
        install_deb=args.allow_deb_install,
        install_apk=args.allow_apk_install,
        adb=args.enable_adb,
    )
    runner = AcceptanceRunner(ROOT, args.report_dir, policy, detect_environment(ROOT), args.timeout)
    results = runner.run(scenarios, resume=args.resume)
    for result in results:
        print(f"{result.outcome.value:7} {result.scenario_id}: {result.reason}")
    print(f"reports: {args.report_dir}")
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
