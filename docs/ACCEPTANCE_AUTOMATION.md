# Acceptance automation

The acceptance runner is reusable infrastructure for the Ubuntu, Android, Google TV,
dual-controller and latency protocols. It does **not** replace hardware evidence. The first
iteration deliberately provides the policy, detection, scenario catalog, bounded execution,
checkpoint and report layers only; hardware adapters remain blocked until they are implemented
and individually authorized.

## Architecture

- `scripts/acceptance.py`: typed policy, environment model, bounded process port, runner,
  checkpoint/restart logic and JSON, HTML and JUnit writers.
- `scripts/run_acceptance.py`: composition root and command-line interface.
- `tests/e2e/test_*.py`: scenario declarations grouped by target. These files also contain
  static contract tests and never contact hardware during `pytest`.
- `tests/test_acceptance.py`: deterministic unit tests using simulated environments and process
  doubles.
- `reports/acceptance/`: ignored generated reports; only `.gitkeep` is tracked.

Python remains the only authoritative product implementation. The runner orchestrates evidence;
it does not duplicate `ControlService`, Cast transport, Rust IPC or Android behavior.

## Safe commands

The default is a dry run. It detects the host, configured artifact paths, tool presence and
configured device hints, then records every executable scenario as `BLOCKED`:

```bash
python3 scripts/run_acceptance.py
python3 scripts/run_acceptance.py --category ubuntu
python3 scripts/run_acceptance.py --resume
```

Reports are written as `acceptance.json`, `acceptance.html` and `acceptance.junit.xml` under
`reports/acceptance/`. Set `CONTROL_TV_DEB`, `CONTROL_TV_APK`, `CONTROL_TV_GOOGLE_TV` and
`CONTROL_TV_ANDROID_SERIAL` only when preparing the matching adapter. Device hints are read as
configuration; detection never invokes ADB or contacts a TV.

`--execute` leaves dry-run mode but grants no sensitive capability. Future hardware adapters
must additionally require the relevant explicit flag: `--allow-tv-commands`,
`--allow-deb-install`, `--allow-apk-install`, or `--enable-adb`. Flags without `--execute` grant
nothing. This infrastructure iteration must not be run with those flags against real devices.

## Outcomes and restart

- `PASS`: the scenario executed and its assertions succeeded.
- `FAIL`: an executed scenario failed, raised, or timed out.
- `SKIP`: a declared prerequisite is unavailable.
- `BLOCKED`: authorization or an implementation adapter is absent.

An unexecuted scenario can therefore never become `PASS`. The JSON report is rewritten after
every scenario. `--resume` retains prior `PASS` results and retries `FAIL`, `SKIP` and `BLOCKED`
results, allowing newly available dependencies or permissions to unblock an interrupted campaign.

The existing manual evidence rules remain authoritative in
`docs/CAST_HARDWARE_VALIDATION.md` and `docs/DUAL_CONTROLLER_VALIDATION.md`.
