# Manual acceptance protocol (scenarios without an automated adapter)

The acceptance runner (`scripts/run_acceptance.py`) automates only the static and simulated
checks and **one** real scenario, the Ubuntu install/launch/tray check. Every other real
scenario has **no automated adapter**: the runner reports it `BLOCKED` with the reason "no
automated adapter exists … run it by hand per docs/MANUAL_ACCEPTANCE_PROTOCOL.md", whatever flags
are given, and never discovers, pairs, installs, uses ADB or sends a command for it. This file is
how those scenarios are run by hand, reproducibly.

| Runner scenario                                   | What it covers                                           | Step definitions                                                         |
| ------------------------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------ |
| `android.real.install-launch-logs`                | install, launch, display, widget, CSP, logs on the phone | below, session 2                                                         |
| `R1`–`R4`, `dual-controller.convergence`          | two remotes, read-only                                   | [DUAL_CONTROLLER_VALIDATION.md](DUAL_CONTROLLER_VALIDATION.md)           |
| `C1`–`C7`                                         | two remotes, commands (C6/C7 need instrumentation)       | same                                                                     |
| `K1`–`K3`, `latency.remote-keys`                  | key latency, filmed                                      | same                                                                     |
| `TV-POWER`, `TV-KEYBOARD`, `TV-APPS`, `TV-ERRORS` | power, keyboard, apps, error reads                       | [GOOGLE_TV_ACCEPTANCE_AUTOMATION.md](GOOGLE_TV_ACCEPTANCE_AUTOMATION.md) |

## Rules (all sessions)

1. **One authorization per step.** Before each step the owner says "go" for that step; the
   operator writes the time and the step id in the record. Read-only steps (R1–R4, error reads)
   need it too. No step runs on a general approval.
2. **No command is ever resent.** An uncertain result is followed by Refresh status on both
   remotes, never by the command again.
3. **Private evidence only** in `.ai-private/hardware/acceptance-<date>/` (git-ignored): no TV
   name, address, device id or media title is committed. Only outcome classes go into
   `DEVELOPMENT_PLAN.md`.
4. **Classification:** PASS only when every PASS criterion was observed; FAIL when a FAIL
   criterion was observed; NOT RUN otherwise (including a missing authorization, a missing
   video for K1–K3, or missing instrumentation for C6/C7). Simulated or CI results never become
   hardware results.

## Before session 1: identify the builds (no device involved)

Use one pair of artifacts, from one CI run of `main`, for the whole campaign.

```bash
# Ubuntu package: the provenance binds commit, run, version, .deb, binary and payload SHA-256;
# the installed package is reported as not installed (no dpkg record and no file of the
# package), this build (dpkg status "install ok installed", binary, dpkg record and every
# installed file), another build, or BLOCKED (any other dpkg status - half-configured, unpacked,
# config-files... - leftovers, unreadable files). --expect-commit and --expect-run-id are required.
.venv/bin/python scripts/acceptance_identity.py check-deb \
  --deb dist/acceptance-<sha>/linux/control-tv_0.1.0_amd64.deb \
  --expect-commit <run head SHA> --expect-run-id <run id>
# Android APK: exactly the APK its provenance names, from the expected commit and run, with the
# digest the CI run published in its log (mandatory), signed by the recorded (or pinned)
# certificate.
.venv/bin/python scripts/android_test_signing.py check-apk \
  --apk dist/acceptance-<sha>/android/app-universal-debug.apk \
  --apksigner "$ANDROID_HOME/build-tools/<version>/apksigner" \
  --expect-commit <run head SHA> --expect-run-id <run id> --expect-apk-sha256 <digest from the CI log>
```

Both commands exit 0 (identified), 1 **FAIL** (a contradiction: another package or APK, commit,
run, key or installed build) or 2 **BLOCKED** (the identity cannot be established: no or
unreadable provenance, no published APK digest, unreadable pinned certificate, a package dpkg has
not finished installing or removing, apksigner unavailable). Only 0 lets the campaign
start; after session 1, `check-deb ... --require-installed` must also report "installed: this
build". Record in the private record: CI run id, commit, `.deb` SHA-256, binary and payload
SHA-256, APK SHA-256, APK signer certificate and identity (`pinned-test-key` or
`ephemeral-debug-key`).

A CI artifact from before this protocol has no `provenance.json` / `apk-provenance.json`: after
checking the downloaded archive's digest against the run's artifact digest, write them with
`scripts/acceptance_identity.py deb-provenance --commit <the run's head SHA> --run-id <id>` and
`scripts/android_test_signing.py write-apk-provenance --commit <the run's head SHA> --run-id <id>`
(the commit comes from the CI run, `gh api repos/<owner>/<repo>/actions/runs/<id>`); neither
command overwrites an existing manifest.

## Session order (fewest manipulations)

Each state of the TV serves several steps: the home screen serves K1–K3 and navigation, an open
search field the keyboard, an app launched from a remote the media of C1–C5 and the widget, and
power is last.

| #   | Session           | Steps                                                                                                                                                                                                                                                             | Owner authorizations  | Estimate |
| --- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- | -------- |
| 1   | Ubuntu install    | runner `ubuntu.real.launch-tray-logs` (`--execute --allow-deb-install`, plus `--allow-existing-deb-validation` when the installed binary is already the artifact's); tray, CSP, logs; Ubuntu logos while the app list shows                                       | sudo; ADB list        | 0.75 h   |
| 2   | Android install   | uninstall if the installed APK has another signer (see [ANDROID_TEST_SIGNING.md](ANDROID_TEST_SIGNING.md)), install the APK file, open the app, pair the remote channel (code on the TV), allow the phone's ADB key on the TV, place the widget and choose the TV | install; pairing; ADB | 0.75–1 h |
| 3   | Read-only         | R1 → R4; Android logos while the app list shows; widget Refresh; TV-ERRORS reads                                                                                                                                                                                  | each step             | 0.75–1 h |
| 4a  | Home screen       | K1 (Ubuntu, filmed: also the functional proof of arrows/OK), K2 (Android), K3; Back, Home                                                                                                                                                                         | TV commands; video    | 0.75 h   |
| 4b  | Search field open | keyboard from Ubuntu, then Android                                                                                                                                                                                                                                | TV commands           | 0.5 h    |
| 4c  | Apps              | launch an app from Ubuntu, another from Android (the launched media serves 4d)                                                                                                                                                                                    | TV commands; ADB      | 0.5 h    |
| 4d  | Media playing     | C1–C5; Stop and Seek bound to the media session (#59); audible volume from Android; widget Play/Pause/Volume                                                                                                                                                      | TV commands           | 1–1.5 h  |
| 4e  | End               | power off, then on                                                                                                                                                                                                                                                | TV command            | 0.25 h   |
| 5   | C6–C7             | only with the send/trace instrumentation (absent today); otherwise record NOT RUN                                                                                                                                                                                 | Wi-Fi cut (C7)        | 0–1.5 h  |
| 6   | Android lifecycle | process death, battery saver, network loss and return, TV not found; update with pending work (only between two builds with the same signer)                                                                                                                      | —                     | 1–1.5 h  |
| 7   | Report            | per-step classes into `DEVELOPMENT_PLAN.md` and `TODO.md`                                                                                                                                                                                                         | —                     | 1–2 h    |

## Record template (one entry per step)

```text
step: R2            authorized by owner at: 2026-10-18 10:42
builds: run=<id> commit=<sha> deb=<sha256> apk=<sha256> signer=<sha256> (<identity>)
acting remote: Ubuntu | Android      action: …
acting remote shows: sent | confirmed | not confirmed | may or may not have reached the TV | refused
other remote after Refresh: …
seen/heard on the TV: yes | no | not checked — …
read errors (code, attempts): …
classification: PASS | FAIL | NOT RUN — reason
evidence files: <private paths>
```
