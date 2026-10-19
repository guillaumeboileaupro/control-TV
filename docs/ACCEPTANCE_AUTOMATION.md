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
session DBus, a configured `.deb` **with its provenance manifest**, resolvable dependencies,
non-interactive `sudo`, and both `--execute` and `--allow-deb-install`.

**Build identity.** Every package is version 0.1.0, so the version never identifies a build. The
adapter reads `provenance.json` next to the `.deb` (or `CONTROL_TV_DEB_PROVENANCE`), written by CI
or by `scripts/acceptance_identity.py deb-provenance`; it binds commit, CI run, version,
architecture, `.deb` SHA-256, the SHA-256 of `usr/bin/control-tv` and the **payload** SHA-256 (of
the package's `md5sums`, which lists every packaged file, the frozen Python bridge included: the
binary alone is reproducible and stays identical when only the bridge changes). The adapter
recomputes the digests: a missing or invalid manifest is `BLOCKED` (identity cannot be
established), a contradiction is `FAIL`, an installed package whose binary or dpkg file list
(`/var/lib/dpkg/info/control-tv.md5sums`) is another build's, or whose installed files differ
from that record (modified or partial), is `BLOCKED`, and an installation that leaves any of these
is `FAIL`. Its receipt (schema 2) records the commit and the digests; a
receipt of the earlier version-only schema is stale. For a pull-request build, CI's commit is
GitHub's merge commit (what was built); acceptance uses push builds of `main`. It never replaces or removes an existing `control-tv`
installation. After preflights it installs the package, checks the exact installed version,
launches `/usr/bin/control-tv`, pings the installed frozen Python bridge, compares GNOME
StatusNotifier registrations before/after launch, captures stdout/stderr, and terminates only the
process it launched. Before installation it writes an artifact-hash/version receipt under the
report directory, updates it after installation, and removes it only after the complete scenario
passes. This distinguishes a package left by an interrupted recipe from an unrelated pre-existing
installation. It leaves every installed package installed because removal is never authorized.

Run the real Ubuntu recipe only on the intended disposable or explicitly prepared host:

```bash
sudo -v
CONTROL_TV_DEB=/absolute/path/control-tv_0.1.0_amd64.deb \
  python3 scripts/run_acceptance.py --category ubuntu --execute --allow-deb-install
```

The first command grants a short-lived sudo credential; the adapter itself accepts only
non-interactive `sudo -n`. The recipe mutates the host by installing the package. It never sends a
TV command and never uses ADB. The bridge ping proves the packaged Python service starts and correlates its response;
it does not replace a separate end-to-end UI interaction check. The Android install/launch/log
check has **no automated adapter**: it is `BLOCKED` whatever flags are given and is run by hand
per [MANUAL_ACCEPTANCE_PROTOCOL.md](MANUAL_ACCEPTANCE_PROTOCOL.md); the runner never enables
Android developer options or ADB itself.

If a clean package of the same version **and the same binary** is already installed, the adapter
reports whether a matching receipt
identifies an interrupted acceptance installation or whether the installation is pre-existing. It
returns `BLOCKED` in both cases unless the owner gives the additional, non-mutating consent:

```bash
CONTROL_TV_DEB=/absolute/path/control-tv_0.1.0_amd64.deb \
  python3 scripts/run_acceptance.py --category ubuntu --execute --allow-deb-install \
  --allow-existing-deb-validation
```

That flag validates and launches the matching installed version; it never invokes package
installation, replacement or removal. A version mismatch, partial dpkg state, stale/mismatched
receipt, or receipt without a clean installed package remains `BLOCKED` for manual review.

The non-destructive recovery procedure is:

1. Read the package state with `dpkg-query -W -f='${db:Status-Abbrev} ${Version}\n' control-tv`.
2. Inspect `reports/acceptance/ubuntu-install-receipt.json` when present and compare the configured
   artifact with `sha256sum "$CONTROL_TV_DEB"`; do not edit the receipt to force a match.
3. If dpkg reports a clean matching version, decide explicitly whether to run the validation-only
   command above. The report records `interrupted acceptance installation` or
   `pre-existing installation` as its origin.
4. If dpkg reports a partial state, the version differs, or receipt and artifact differ, stop for
   manual review. The orchestrator will not install, reinstall, repair, replace, or remove anything.

The Google TV and two-controller catalog, including the mandatory C6/C7 trace contracts, is
documented in [GOOGLE_TV_ACCEPTANCE_AUTOMATION.md](GOOGLE_TV_ACCEPTANCE_AUTOMATION.md).
K1-K3 timing phases, statistics and report provenance are documented in
[ACCEPTANCE_PERFORMANCE.md](ACCEPTANCE_PERFORMANCE.md).
