# Hardware acceptance plan — Ubuntu, Android and the Freebox (Google TV)

Prepared 2026-10-17 from `main` `0e5b4ba`. Nothing here has been run: every step that touches a
device needs the owner's explicit go-ahead first (see "Authorizations"). Step definitions stay in
[`DUAL_CONTROLLER_VALIDATION.md`](DUAL_CONTROLLER_VALIDATION.md) (R1–R4, C1–C7, K1–K3) and
[`GOOGLE_TV_ACCEPTANCE_AUTOMATION.md`](GOOGLE_TV_ACCEPTANCE_AUTOMATION.md); the runner is
documented in [`ACCEPTANCE_AUTOMATION.md`](ACCEPTANCE_AUTOMATION.md).

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

## 3. Sessions (in order, each step only after its go-ahead)

All commands use the project's Python (`.venv/bin/python`: the system `python3` is 3.7 and lacks
`tomllib`). Start every session with a dry run.

**Session A — install and read-only (≈ 1.5 h)**

```bash
.venv/bin/python scripts/run_acceptance.py --mode dry-run
CONTROL_TV_DEB=dist/final-acceptance/control-tv-linux-release/<deb> \
  .venv/bin/python scripts/run_acceptance.py --category ubuntu --execute --allow-deb-install
CONTROL_TV_APK=dist/final-acceptance/control-tv-android-arm64-debug/<apk> \
  .venv/bin/python scripts/run_acceptance.py --category android --execute --allow-apk-install --enable-adb
.venv/bin/python scripts/run_acceptance.py --category google-tv --execute     # R1–R4, read-only
```

Pass criteria: R1–R4 as defined (same stable id on both remotes, same reported state, both
converge after an external change, repeated reads change nothing).

**Session B — remote keys and apps (≈ 2–3 h)** — with `--allow-tv-commands`: power, navigation,
keyboard (a search field open on the TV), app in front, app launch, from Ubuntu then Android;
K1–K3 filmed (remote screen and TV in the same frame).

**Session C — concurrency (≈ 2–3 h)** — C1–C7 with `--allow-tv-commands`; C6 and C7 only with the
instrumented trace (`TraceEvent`), otherwise NOT RUN.

**Session D — Android lifecycle and widget (≈ 1–2 h)** — widget commands, process death, battery
saver, network loss and return, APK update with pending work.

`--resume` keeps earlier PASS results and retries the others.

## 4. Authorizations needed (owner)

| Operation                                                                                 | Why                                                                |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Install the `.deb` (sudo)                                                                 | replaces the installed package                                     |
| Install the APK                                                                           | replaces the phone app; same signing key keeps the widget and data |
| Enable ADB (already enabled on the Freebox per 2026-10-17) and allow control-TV on the TV | app list and launch                                                |
| Pair the phone (code on the TV)                                                           | remote channel from Android                                        |
| R1–R4                                                                                     | read-only, but still needs consent (validation protocol)           |
| Each C1–C7 step and any other TV command                                                  | changes what the TV does                                           |
| Filming K1–K3                                                                             | latency evidence                                                   |
| Adopt the latency criterion                                                               | proposed, not adopted                                              |

## 5. Estimate

Sessions A–D: 6–10 h with the owner at the TV, plus 1–2 h of reporting and plan updates.
