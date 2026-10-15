# Acceptance automation

The acceptance runner is reusable infrastructure for the Ubuntu, Android, Google TV,
dual-controller and latency protocols. It does **not** replace hardware evidence. The first
iteration provides the policy, detection, scenario catalog, bounded execution, checkpoint and
report layers. The Ubuntu adapter is executable only after its two explicit authorizations;
other hardware adapters remain blocked until implemented and individually authorized.

## Architecture

- `scripts/acceptance.py`: typed policy, environment model, bounded process port, runner,
  checkpoint/restart logic and JSON, HTML and JUnit writers.
- `scripts/run_acceptance.py`: composition root and command-line interface.
- `scripts/acceptance_ubuntu.py`: Ubuntu host port and real `.deb`/launch/bridge/tray adapter.
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
python3 scripts/run_acceptance.py --mode dry-run
python3 scripts/run_acceptance.py --category ubuntu
python3 scripts/run_acceptance.py --resume
```

Reports are written as `acceptance.json`, `acceptance.html` and `acceptance.junit.xml` under
`reports/acceptance/`. Set `CONTROL_TV_DEB`, `CONTROL_TV_APK`, `CONTROL_TV_GOOGLE_TV` and
`CONTROL_TV_ANDROID_SERIAL` only when preparing the matching adapter. Device hints are read as
configuration; detection never invokes ADB or contacts a TV.

`--mode execute` (or its compatibility alias `--execute`) leaves dry-run mode but grants no
sensitive capability. Hardware adapters
must additionally require the relevant explicit flag: `--allow-tv-commands`,
`--allow-deb-install`, `--allow-apk-install`, or `--enable-adb`. Sensitive flags without execute
mode grant nothing.

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

## Ubuntu and Android categories

Every result carries an `evidence` field. `static` checks inspect source or a configured artifact,
`simulated` checks run existing contract suites with doubles, and `real` checks require a target
platform or device. Reports never combine those levels into one success claim.

Static inspection remains safe in the default mode:

```bash
CONTROL_TV_DEB=/path/control-tv_0.1.0_amd64.deb \
  python3 scripts/run_acceptance.py --category ubuntu
CONTROL_TV_APK=/path/app-universal-debug.apk \
  python3 scripts/run_acceptance.py --category android
```

Ubuntu verifies Debian identity, amd64 architecture, WebKit/AppIndicator dependencies, packaged
Tauri executable, frozen Python bridge, desktop icon, production CSP/IPC allowance, and tray
actions. Android verifies the APK archive and arm64 ABI, embedded Python, package identity and
permissions (when `aapt` is available), production CSP, embedded-bridge sources and widget
isolation/wiring.

The simulated suites require `--mode execute` or `--execute`; they exercise the existing bridge,
IPC, packaging,
widget and startup-error tests and include captured stdout/stderr in the report. They do not install
or launch an application and do not invoke ADB:

```bash
python3 scripts/run_acceptance.py --category ubuntu --execute
python3 scripts/run_acceptance.py --category android --execute
```

The real Ubuntu adapter is deliberately fail-closed. It requires an interactive GNOME session,
session DBus, a configured `.deb`, resolvable dependencies, non-interactive `sudo`, and both
`--execute` and `--allow-deb-install`. It refuses to replace or remove an existing `control-tv`
installation. After preflights it installs the package, checks the exact installed version,
launches `/usr/bin/control-tv`, pings the installed frozen Python bridge, compares GNOME
StatusNotifier registrations before/after launch, captures stdout/stderr, and terminates only the
process it launched. It leaves a newly installed package installed because package removal is not
authorized by these flags.

Run the real Ubuntu recipe only on the intended disposable or explicitly prepared host:

```bash
sudo -v
CONTROL_TV_DEB=/absolute/path/control-tv_0.1.0_amd64.deb \
  python3 scripts/run_acceptance.py --category ubuntu --execute --allow-deb-install
```

The first command grants a short-lived sudo credential; the adapter itself accepts only
non-interactive `sudo -n`. The recipe mutates the host by installing the package. It never sends a
TV command and never uses ADB. The bridge ping proves the packaged Python service starts and correlates its response;
it does not replace a separate end-to-end UI interaction check. Android install/launch/log checks
remain `BLOCKED`; their catalog entry requires both `--allow-apk-install` and `--enable-adb`, and
the runner never enables Android developer options or ADB itself.

The Google TV and two-controller catalog, including the mandatory C6/C7 trace contracts, is
documented in [GOOGLE_TV_ACCEPTANCE_AUTOMATION.md](GOOGLE_TV_ACCEPTANCE_AUTOMATION.md).
K1-K3 timing phases, statistics and report provenance are documented in
[ACCEPTANCE_PERFORMANCE.md](ACCEPTANCE_PERFORMANCE.md).
