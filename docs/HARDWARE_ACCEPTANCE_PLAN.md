# Hardware acceptance plan — Ubuntu, Android and the Freebox (Google TV)

Prepared 2026-10-17 from `main` `0e5b4ba`, revised the same day for the build identity, the
Android signing and the manual steps. Nothing here has been run: every step that touches a
device needs the owner's explicit go-ahead first (see "Authorizations"). Step definitions stay in
[`DUAL_CONTROLLER_VALIDATION.md`](DUAL_CONTROLLER_VALIDATION.md) (R1–R4, C1–C7, K1–K3) and
[`GOOGLE_TV_ACCEPTANCE_AUTOMATION.md`](GOOGLE_TV_ACCEPTANCE_AUTOMATION.md); the runner is
documented in [`ACCEPTANCE_AUTOMATION.md`](ACCEPTANCE_AUTOMATION.md).

**What runs by hand.** The runner automates the static and simulated checks and one real
scenario (Ubuntu install, launch, bridge and tray). **Every TV, phone, two-remote and latency
step has no automated adapter**: the runner reports it `BLOCKED` whatever flags are given, and the
step is run by hand per [`MANUAL_ACCEPTANCE_PROTOCOL.md`](MANUAL_ACCEPTANCE_PROTOCOL.md), which
also holds the session order and the evidence record.

Every result is classified as **simulated PASS**, **CI PASS** or **hardware PASS / FAIL / NOT RUN**,
with its evidence (report, log, video or capture). A green CI is never a hardware result.

## 1. Feature audit

| Feature                                  | Ubuntu (window, tray, `.deb`)       | Android (app, widget, APK) | Hardware evidence so far                                                                                                     |
| ---------------------------------------- | ----------------------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Discovery and selection by stable id     | implemented                         | implemented                | Ubuntu and phone discovered the TV (2026-10-06/12)                                                                           |
| Status read (receiver, media)            | implemented                         | implemented                | Ubuntu, read-only (2026-09-26)                                                                                               |
| Cast playback: Play, Pause, Stop, Seek   | implemented; session-bound (#59)    | implemented                | Pause/Play had a physical effect but stayed unconfirmed (earlier campaign); Stop, Seek and the #59 session binding not tried |
| Cast volume and mute                     | implemented (fixed volume shown)    | implemented                | receiver-confirmed mute, no audible effect (fixed Cast volume)                                                               |
| TV remote channel: pairing               | implemented                         | implemented                | Ubuntu paired (2026-10-12); the phone is **not** paired                                                                      |
| Audible volume and mute (remote channel) | implemented                         | implemented                | **Ubuntu hardware PASS** (2026-10-12); Android not tried                                                                     |
| Power, arrows, OK, Back, Home            | implemented                         | implemented                | not tried                                                                                                                    |
| Remote keyboard (text, Backspace, Enter) | implemented                         | implemented                | not tried                                                                                                                    |
| App in front                             | implemented                         | implemented                | not tried                                                                                                                    |
| Installed apps: list                     | implemented (ADB)                   | implemented (ADB)          | **Ubuntu: real list of 28 apps shown** (screenshots 2026-10-17, debugging enabled on the Freebox)                            |
| Installed apps: launch                   | implemented (ADB)                   | implemented (ADB)          | not tried                                                                                                                    |
| App logos and names                      | 20 of 28 apps (#75–#77)             | same UI                    | Ubuntu: logos seen after installing #74's `.deb`; #75–#77 not yet installed                                                  |
| Status-area icon (tray)                  | implemented                         | —                          | not seen on a real desktop                                                                                                   |
| Home-screen widget                       | —                                   | implemented                | earlier builds: read-only paths PASS; commands not tried on the current build                                                |
| CSP (#61)                                | dev window and debug binary checked | —                          | installed `.deb` and APK not checked                                                                                         |
| Two remotes in parallel                  | contract tested (simulated)         | same                       | R1–R4 and C1–C7 not run                                                                                                      |

## 2. Missing hardware validations

1. Install the current `.deb` and APK (authorization), check logos, tray, CSP, logs.
2. Pair the phone with the TV (code shown on the TV).
3. R1–R4: both remotes read the same TV (read-only).
4. Remote keys, power, keyboard, app in front — from Ubuntu, then Android.
5. App launch from each remote (the list already works on Ubuntu).
6. C1–C7: concurrent commands; C6/C7 need the instrumented trace.
7. K1–K3: key latency on video (criterion still to be adopted by the owner).
8. #59 on the TV: Play/Pause/Stop/Seek with a media session id; refusal path when none.
9. Widget commands on the current APK; Android lifecycle (process death, battery, network loss,
   update with pending work).

## 3. Builds under test

One pair of artifacts from one CI run of `main` for the whole campaign, identified by more than
its version (every build is 0.1.0):

- **Ubuntu:** `provenance.json` next to the `.deb` binds commit, CI run, version, architecture,
  `.deb` SHA-256, the SHA-256 of `usr/bin/control-tv` and the payload SHA-256 (the package's
  `md5sums`, covering the Python bridge too). The runner recomputes them and refuses a real
  validation when the manifest is missing, contradicts the package, or the installed binary,
  dpkg file list or installed files are another build's (`scripts/acceptance_identity.py`,
  `check-deb` before a session). CI writes the manifest from now on; for an older artifact it is
  written from the run's recorded commit.
- **Android:** `apk-provenance.json` binds commit, run, APK SHA-256 and signer certificate;
  `android_test_signing.py check-apk` accepts an APK only when it is exactly the one its
  provenance and the expected CI run name (digest, published digest, commit, run, signer). A build
  signed with the stable test identity updates an installed one in place;
  an ephemeral one needs an uninstall ([`ANDROID_TEST_SIGNING.md`](ANDROID_TEST_SIGNING.md)).

Current candidate (downloaded 2026-10-17, kept under `dist/acceptance-de31c94/`, git-ignored):

|                     | Value                                                                                                                                                                                                                                                  |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| CI run / commit     | `39034444658` (push, `main`, success) / `de31c94dceb290f98bc8b0784ac17d3fd3d78727`                                                                                                                                                                     |
| `.deb`              | `control-tv` 0.1.0 amd64, SHA-256 `d7d2278a…0c381a`; binary `11d98c55…6d95475`; payload `120108f4…4901bb9`; `SHA256SUMS` verified                                                                                                                      |
| Installed on Ubuntu | binary, dpkg file list and every installed file equal to this build's: **this build** (`check-deb`)                                                                                                                                                    |
| APK                 | `io.github.guillaumeboileaupro.controltv` 0.1.0 (code 1000), arm64-v8a, SHA-256 `a2e9dac5…4aa22c` (= the CI log); signer `9193947f…921a78`, **ephemeral** CI debug key (scheme v2); local `apk-provenance.json` written from the run, `check-apk` PASS |
| Expiry on GitHub    | APK 2026-10-24, `.deb` 2026-10-31                                                                                                                                                                                                                      |

## 4. Sessions

Run in the order of [`MANUAL_ACCEPTANCE_PROTOCOL.md`](MANUAL_ACCEPTANCE_PROTOCOL.md) (sessions 1–7,
≈ 7.5–12 h, 6–9 h of them at the TV), each step after its own go-ahead. All commands use the
project's Python (`.venv/bin/python`: the system `python3` is 3.7 and lacks `tomllib`). Start
with a dry run; the only real step the runner executes is session 1:

```bash
.venv/bin/python scripts/run_acceptance.py --mode dry-run
sudo -v
CONTROL_TV_DEB=dist/acceptance-de31c94/linux/control-tv_0.1.0_amd64.deb \
  .venv/bin/python scripts/run_acceptance.py --category ubuntu --execute --allow-deb-install \
  --allow-existing-deb-validation    # the installed binary is already this build
```

The Android and Google TV categories may still be run for their static and simulated checks;
their real scenarios report `BLOCKED` (manual) by design. C6 and C7 use the command trace
([COMMAND_TRACE.md](COMMAND_TRACE.md)): C6 from the Ubuntu development mode with the independent
observer (the hold is refused in the packaged bridge), C7 from Ubuntu or the Android debug
build; without a trace the offline validator accepts, they are NOT RUN. `--resume` keeps
earlier PASS results and retries the others.

## 5. Authorizations needed (owner)

| Operation                                                                                 | Why                                                                                                                                                                                                                        |
| ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Install the `.deb` (sudo)                                                                 | replaces the installed package                                                                                                                                                                                             |
| Install the APK                                                                           | replaces the phone app; an update keeps data and widget only between builds with the same signer, so moving from the current (ephemeral) signer needs one uninstall ([`ANDROID_TEST_SIGNING.md`](ANDROID_TEST_SIGNING.md)) |
| Enable ADB (already enabled on the Freebox per 2026-10-17) and allow control-TV on the TV | app list and launch                                                                                                                                                                                                        |
| Pair the phone (code on the TV)                                                           | remote channel from Android                                                                                                                                                                                                |
| R1–R4                                                                                     | read-only, but still needs consent (validation protocol)                                                                                                                                                                   |
| Each C1–C7 step and any other TV command                                                  | changes what the TV does                                                                                                                                                                                                   |
| Filming K1–K3                                                                             | latency evidence                                                                                                                                                                                                           |
| Adopt the latency criterion                                                               | proposed, not adopted                                                                                                                                                                                                      |

| Create the stable Android test key and its CI secrets | once, by the owner only ([`ANDROID_TEST_SIGNING.md`](ANDROID_TEST_SIGNING.md)) |

## 6. Estimate

Sessions 1–7: ≈ 7.5–12 h, 6–9 h of them with the owner at the TV (see the protocol). The C6/C7
instrumentation exists ([COMMAND_TRACE.md](COMMAND_TRACE.md)); running C6 and C7 is part of the
sessions.
