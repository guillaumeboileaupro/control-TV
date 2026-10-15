"""Ubuntu and Android acceptance adapters with no product or device-control logic."""

from __future__ import annotations

import json
import time
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

from acceptance import Context, Outcome, Result, Scenario, make_result


def configured_artifact(context: Context, scenario: Scenario, name: str) -> Path | Result:
    started = time.monotonic()
    configured = context.environment.artifacts.get(name)
    if not configured:
        return make_result(
            scenario,
            Outcome.SKIP,
            f"CONTROL_TV_{name.upper()} is not configured",
            started=started,
        )
    path = Path(configured).expanduser()
    if not path.is_file():
        return make_result(
            scenario, Outcome.FAIL, f"configured artifact does not exist: {path}", started=started
        )
    return path


def command_check(
    context: Context,
    scenario: Scenario,
    args: Sequence[str],
    validate: Callable[[str], tuple[bool, str]],
) -> Result:
    started = time.monotonic()
    completed = context.command.run(args, timeout=context.timeout_seconds)
    log = f"$ {' '.join(args)}\n{completed.stdout}{completed.stderr}".rstrip()
    if completed.returncode != 0:
        return make_result(
            scenario,
            Outcome.FAIL,
            f"command exited with {completed.returncode}",
            started=started,
            log=log,
        )
    valid, reason = validate(completed.stdout)
    return make_result(
        scenario, Outcome.PASS if valid else Outcome.FAIL, reason, started=started, log=log
    )


def source_check(
    root: Path, scenario: Scenario, checks: Sequence[tuple[Path, Sequence[str]]]
) -> Result:
    started = time.monotonic()
    missing: list[str] = []
    for relative, required in checks:
        text = (root / relative).read_text(encoding="utf-8")
        missing.extend(f"{relative}: {token}" for token in required if token not in text)
    if missing:
        return make_result(
            scenario,
            Outcome.FAIL,
            "required source contracts are missing",
            started=started,
            log="\n".join(missing),
        )
    return make_result(scenario, Outcome.PASS, "source contracts present", started=started)


def csp_check(root: Path, scenario: Scenario) -> Result:
    started = time.monotonic()
    document = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    csp = document.get("app", {}).get("security", {}).get("csp", {})
    expected = {
        "default-src": "'self'",
        "script-src": "'self'",
        "connect-src": "ipc: http://ipc.localhost",
        "object-src": "'none'",
        "base-uri": "'none'",
        "form-action": "'none'",
        "frame-ancestors": "'none'",
    }
    wrong = {key: csp.get(key) for key, value in expected.items() if csp.get(key) != value}
    if wrong:
        return make_result(
            scenario,
            Outcome.FAIL,
            "CSP differs from the restrictive production contract",
            started=started,
            log=json.dumps(wrong, sort_keys=True),
        )
    return make_result(
        scenario, Outcome.PASS, "restrictive production CSP present", started=started
    )


def apk_archive_check(path: Path) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        return False, f"invalid APK archive: {exc}"
    missing = sorted({"AndroidManifest.xml", "resources.arsc"} - names)
    abis = {
        name.split("/", 2)[1] for name in names if name.startswith("lib/") and name.count("/") >= 2
    }
    problems: list[str] = []
    if missing:
        problems.append("missing " + ", ".join(missing))
    if abis != {"arm64-v8a"}:
        problems.append(f"expected only arm64-v8a, found {sorted(abis)}")
    if not any("libpython3.12.so" in name for name in names):
        problems.append("embedded libpython3.12.so not found")
    if not any("control_tv" in name for name in names):
        problems.append("embedded control_tv package not found")
    reason = (
        "; ".join(problems) if problems else "APK metadata, arm64 ABI and Python bridge present"
    )
    return not problems, reason


def always_success(output: str) -> tuple[bool, str]:
    del output
    return True, "simulated contract suite passed"


def real_adapter_blocked(context: Context, scenario: Scenario) -> Result:
    del context
    return make_result(
        scenario,
        Outcome.BLOCKED,
        "real-device adapter is not configured; no install, launch or ADB action was attempted",
        started=time.monotonic(),
    )
