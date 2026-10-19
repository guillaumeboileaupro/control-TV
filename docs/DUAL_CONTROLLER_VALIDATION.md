# Ubuntu and Android remotes on one TV: hardware validation protocol

Issue #37, phase 1, item R of `DEVELOPMENT_PLAN.md`. The contract below is pinned by automated tests on a simulated TV shared by two independent `ControlService` instances (`tests/shared_tv.py`, `tests/test_dual_controller.py`). Those tests are simulator evidence only. This protocol is how the owner checks the same contract on a real TV with the real Ubuntu application and the real Android app, **by hand**: the acceptance runner has no adapter for these steps and reports them `BLOCKED` (session order and record template: [MANUAL_ACCEPTANCE_PROTOCOL.md](MANUAL_ACCEPTANCE_PROTOCOL.md)). It has **not been run**. Nothing in it may be marked validated before it has been, step by step, with the evidence recorded.

## Contract under test

1. Each remote finds the TV itself and keeps no copy of its state: after the other remote acts, its next status read (Refresh status, a new selection, or the confirmation of its own next command) shows what the TV reports now.
2. Each command is attempted once, by the remote that sent it, and is never resent automatically. When delivery is uncertain, the TV may have received it zero or one time. Commands from both remotes race at the TV; the last one actually delivered wins.
3. A remote says a command is confirmed only from what the TV reports for the same media session; a change made meanwhile by the other remote leaves it unconfirmed.

Not expected: synchronization between the two remotes, a periodic refresh (neither remote refreshes on its own; this is a known open item), or a remote showing the other's change before its own next read.

## Equipment and builds

- The Ubuntu application from `main` (development mode `npx --prefix ui tauri dev`, or the installed release `.deb`), and the Android app (APK) built from `main` (`python3 scripts/dev.py android-apk`, or the CI artifact), each identified by its commit and SHA-256 (see [MANUAL_ACCEPTANCE_PROTOCOL.md](MANUAL_ACCEPTANCE_PROTOCOL.md), "identify the builds"). The Android home-screen widget (PR #33, merged into `main` on 2026-10-13) is part of the APK; R1-R4 and C1-C7 are run from the two **apps**, and the widget's own commands are checked separately (MANUAL_ACCEPTANCE_PROTOCOL.md, sessions 3, 4d and 6).
- One TV (Chromecast or Google TV), the computer and the phone on the same Wi-Fi network. For playback steps, media playing on the TV, started from any usual sender app (for example YouTube on the phone), and its name noted only in the private record.
- Optional, for the evidence: `adb logcat -s control-tv` on the Android side, and the Ubuntu application's terminal output in development mode.

## Rules

- **R1-R4 send no command from control-TV:** in both apps only Find devices, the TV selection and Refresh status are used, which only discover and read. They still need the owner's authorization before being run. In R3 the owner changes the TV's state on purpose with the TV's own remote control or another app (for example the sender app that plays the video); that is the owner's action, not a control-TV command. Pressing any control-TV playback or sound control during R1-R4 makes the step NOT RUN.
- Each command step (C1-C7) is run only with the owner's explicit go-ahead, one command at a time.
- A remote never resends a command. If a command's result is uncertain ("may or may not have reached the TV", "not confirmed"), use Refresh status on both remotes, never the command again, and record what they show.
- The record is private (for example `.ai-private/hardware/dual-controller/`, git-ignored): no TV name, address, device identifier or media identifier is committed. Only the outcome classes below go into `DEVELOPMENT_PLAN.md`.

## Read-only steps

What R1-R4 can and cannot show:

- **No command emitted by control-TV:** established by design and by the code path (Find devices, selection and Refresh status call only discovery and `get_status`; a refresh never calls a command), not by instrumentation: neither app logs each read today.
- **No change observed:** what the owner sees and hears on the TV, and the state each app reports, before and after the reads.
- **No command received by the TV:** **not verifiable** in R1-R4. It would need a receiver-side or network capture, which this protocol does not use; no step claims it.

Common to every step:

- **Read error:** when a discovery or a read fails (the app says it could not find or reach the TV, or that it timed out; the code is in the Details disclosure), record the wording and the code, then retry the read, at most three times in total; a read is safe to retry, a command never is. If the read still fails, the step is **NOT RUN** (a reliability finding for the phase 1 hardening, not a contract failure). A read error is never classified PASS or FAIL by itself.
- **Evidence to keep, privately** (`.ai-private/hardware/dual-controller/`, git-ignored): date and time of each action, both builds (commit, Ubuntu version, Android version), what each app shows (screenshots allowed in the private record), what was seen and heard on the TV, read errors with their codes, and the classification with its reason. No TV name, address, device identifier or media identifier is committed.
- **Classification:** **PASS** only when every PASS criterion of the step was observed; **FAIL** when a FAIL criterion was observed; **NOT RUN** otherwise. A step that was not executed is never PASS.

### R1 - Each remote discovers the TV on its own

- **Objective:** both apps find the same TV independently, without any link between them.
- **Prerequisites:** owner's authorization; both apps installed from `main` and opened; the TV on and on the same Wi-Fi network as the computer and the phone.
- **Procedure:** on Ubuntu, Find devices; then on Android, Find devices. Do not select anything yet.
- **Expected observations:** each app lists the TV; the TV appears in an app only after that app's own discovery.
- **PASS:** both apps list the TV after their own discovery, with the same name.
- **FAIL:** an app lists the TV without having discovered (state carried over from the other app), or both apps discovered successfully but only one of them lists the TV the other found.
- **NOT RUN:** a discovery fails after three attempts, the TV is off or on another network, or the authorization is missing.
- **Evidence:** both device lists (name only in the private record), times, any error codes.

### R2 - Both remotes read the same state

- **Objective:** after selecting the same TV, both apps show the same reported state.
- **Prerequisites:** R1 PASS; the TV in a known state (playing or paused, the volume not changing).
- **Procedure:** select the TV on Ubuntu, then on Android; read what each shows (connection, playback state, volume, mute); press Refresh status on both and read again.
- **Expected observations:** both show the TV connected with the same playback state, volume and mute, each as reported or "not reported". The playback position is not compared (it moves).
- **PASS:** after the refresh on both, the connection, playback state, volume and mute shown are the same on both and match what the owner sees on the TV.
- **FAIL:** after a successful refresh on both, the two apps show different playback states, volume or mute while the TV did not change in between, or an app shows a state the TV visibly contradicts.
- **NOT RUN:** a read fails after three attempts, or the TV's state changed during the step (start the step again).
- **Evidence:** what each app shows before and after the refresh, the owner's observation of the TV, times.

### R3 - Both remotes converge after an external change

- **Objective:** a change made outside control-TV appears in each app at its own next read, and only then.
- **Prerequisites:** R2 PASS; the owner ready to change the TV with its own remote control or another app (for example pause the video from the sender app, or change the volume with the TV remote).
- **Procedure:** without touching control-TV, the owner changes one thing on the TV (pause, or the volume); without refreshing, read both apps; then press Refresh status on Ubuntu, read it, and press Refresh status on Android, read it.
- **Expected observations:** before its refresh each app still shows the old state (there is no periodic refresh); after its refresh each shows the new state; after both refreshes they agree.
- **PASS:** each app shows the new state after its own refresh, and both then show the same state, matching the TV.
- **FAIL:** after a successful refresh an app still shows the old state, or the two apps disagree after both refreshed while the TV did not change again.
- **NOT RUN:** a read fails after three attempts; a control-TV control was pressed during the step; or the external change was not seen on the TV (for example a volume change on a receiver reporting a fixed volume: record it and retry with a pause).
- **Evidence:** the external action and its time, both apps before and after their refresh, the owner's observation of the TV.

### R4 - Repeated reads change nothing

- **Objective:** repeated refreshes from both apps, including at the same time, leave the TV unchanged and give stable results.
- **Prerequisites:** R2 PASS; the TV playing or paused, with nobody changing it.
- **Procedure:** press Refresh status about ten times on each app, alternating, then a few times on both at once.
- **Expected observations:** nothing changes on screen or in sound because of a refresh; both apps keep showing the same playback state, volume and mute (only the position moves when playing).
- **PASS:** no visible or audible change during the refreshes, and the states shown stay the same on both apps across the refreshes.
- **FAIL:** a visible or audible change appears in step with a refresh while nobody else acted on the TV, or the states shown change between refreshes while the TV did not.
- **NOT RUN:** a read fails after three attempts, or someone changed the TV during the step.
- **Evidence:** the number and times of refreshes on each app, what each showed, the owner's observation. A PASS records "no change observed" and "no command emitted by design"; it never records "no command received by the TV", which R4 cannot verify.

## Command steps (each with the owner's go-ahead)

| Step | Action | Expected on the acting remote | Expected on the other remote, after Refresh status |
|---|---|---|---|
| C1 | Ubuntu: Pause | Paused, confirmed or "not confirmed" (on a receiver that reports no media identity, such as some YouTube sessions, "not confirmed" is expected by design), with the TV visibly paused | Paused |
| C2 | Android: Play | Playing, confirmed or "not confirmed" as above, the TV visibly playing | Playing |
| C3 | Ubuntu: lower the volume a little with the slider (or Mute when the volume is reported fixed) | the reported level or mute state, confirmed or not; whether the sound actually changed is recorded separately (a fixed Cast volume may not change the audible sound) | the same reported level or mute state |
| C4 | Android: the opposite (raise the volume a little, or Unmute) | as C3 | as C3 |
| C5 | Both at once: Pause on Ubuntu and Play on Android, pressed together | each remote initiates one command; record each result without inferring delivery count; at least one may say "not confirmed" | after both refresh, both show the TV's actual state (the last command that reached it); record which state won, not an unobservable delivery order |
| C6 | Instrumented timing test only: begin with a receiver reporting a usable content, media-session and item identity; pause Ubuntu after its pre-command identity read and command dispatch but before its first confirmation read; start another video from the sender app; use an independent read-only receiver trace to prove the new session id is reported; only then release Ubuntu's confirmation read | Ubuntu must not confirm its command from the new session | both show the new session after a refresh; the record contains the ordered instrumentation events and old/new session ids |
| C7 | Instrumented no-replay test only: enable app-side instrumentation that counts entry into the low-level send operation for this request, then stop Wi-Fi immediately after pressing one command (only with owner agreement) | delivery remains "may or may not have reached the TV"; the instrumentation records exactly one send attempt and no second invocation | after reconnection both refresh and show the actual state; record reception as zero-or-one, never claim how many deliveries occurred unless packet or receiver-side capture can count them |

C6 is **NOT RUN**, not a failure or pass, if the baseline identity is absent, the first
confirmation read can occur before the session switch, or the ordered events and distinct
session ids are not captured. Human timing alone cannot validate the media-session guard.

C6 covers a session switch after the command was sent. A switch between Ubuntu's pre-command
read and its send is a known gap (PR #38 review P3-3, pinned in `tests/test_dual_controller.py`):
the command is then sent once and acts on the new session, without being confirmed or resent.
It is not a C6 FAIL; preventing it needs a transport change outside PR #38.

C7 is **NOT RUN for no-replay evidence** without the send-attempt instrumentation. Visible TV
state and refreshed status may still be recorded as recovery evidence, but identical commands
are not countable from their final state. App-side instrumentation proves one local send
attempt; only packet or receiver-side capture can count actual deliveries to the receiver.

## Key latency steps (TV remote channel, each with the owner's go-ahead)

Each key opens its own short connection to the TV's remote channel (connect, send once, close),
so that both remotes can use it and no connection stays open; the cost of that choice is a
delay per key, measured here. These steps send navigation keys only: run them on the TV's home
screen, alternating → and ← so that every key that reaches the TV moves the focus by one.

**How a press is handled.** A remote runs one request at a time. While a key request is in
flight, the pressed key shows a loading indicator and every control of the TV remote panel is marked busy; a
press made then is **refused** by the remote: nothing is sent and nothing is queued. A press
made while the remote is idle is **sent**: its key shows the loading indicator at once. The
**effect** is the focus moving on the TV. The application keeps no request log, so these three
are told apart on video.

**How to measure.** Film the remote's screen and the TV together (a phone camera at 60 frames
per second gives about 17 ms per frame). For each sent press, count the frames:

- *send to effect*: from the first frame where the key shows its loading indicator to the first
  frame where the focus has moved on the TV;
- *send to ready*: from that same frame to the first frame where the remote is idle again (the
  loading indicator gone), which sets how fast the owner can navigate.

**Classifying K1 and K2.** Read, for each press, whether the remote was idle, whether the key
showed its loading indicator, how many times the focus moved, whether an outcome was shown (and
whether it was "may or may not have reached the TV"), and the three frame numbers; write them to
a private record (under `.ai-private/hardware/`, never committed), then:

```bash
python3 scripts/acceptance_latency.py key-step --fps 60 .ai-private/hardware/k1.json
```

It applies the rules below and prints the verdict (exit 0 PASS, 1 FAIL, 2 NOT RUN), the
median and slowest *send to effect* and *send to ready* in seconds, and "measured, no criterion
adopted" unless an adopted criterion is passed (`--effect-median`, `--effect-slowest`,
`--ready-median`, all three). Nothing in it touches a device.

| Step | Action | Record |
|---|---|---|
| K1 | Ubuntu: ten presses, alternating → and ←, each made only once the remote is idle again | per press: sent or refused, the focus move, *send to effect*, *send to ready*; then the median and the slowest of each |
| K2 | Android: the same ten presses from the phone | as K1 |
| K3 | Ubuntu: five presses on → as fast as possible, the first while the remote is idle, the others while it may still be busy | presses made, presses sent (loading indicator shown), presses refused (made while busy), focus moves |

**K1 and K2 outcome.**

- **PASS:** every press made while idle is sent; each sent press moves the focus exactly once;
  the remote shows an outcome for each; and, once the owner has adopted a latency criterion
  (below), the median and slowest delays are within it.
- **FAIL:** a focus move with no sent press, or more moves than sent presses (a key sent
  twice); a press made while idle that is not sent; a sent press reported as sent while the
  focus never moves (navigation does not work); or, once adopted, a delay outside the
  criterion.
- **NOT RUN:** no video, or the video does not show both the remote's key and the TV.
- A press answered "may or may not have reached the TV" is recorded with whether the focus
  moved; on its own it is neither PASS nor FAIL.

**K3 outcome.** Five presses do not mean five commands: the presses made while the remote is
busy are refused by design, so fewer moves than presses is expected.

- **PASS:** focus moves = presses sent, and every refused press sent nothing (no move without a
  loading indicator before it).
- **FAIL:** more focus moves than presses sent (a refused press was sent, or a key was sent
  twice).
- **NOT RUN:** without a video showing both the loading indicator and the TV.

**Latency criterion: proposed, not adopted.** Until the owner approves a criterion, K1 and K2
record their delays and are classified on the functional rules above only, with the latency
noted as "measured, no criterion adopted". Proposed for approval, from what is measurable here:
*send to effect* median ≤ 0.5 s and slowest ≤ 1.0 s, and *send to ready* median ≤ 1.0 s, on each
of Ubuntu and Android. These values are a proposal, not a requirement of the project.

## What to record for each step

Date and time; remote (Ubuntu or Android) and build (commit); action; the acting remote's wording (sent, confirmed, not confirmed, may or may not have reached the TV, refused) and its state shown; what was seen and heard on the TV (physical effect: yes, no, not checked); the other remote's state before and after its refresh; any second command observed on the TV; and, for C6/C7, the required instrumentation records. Classify each step as **PASS**, **FAIL** (contract broken: instrumentation shows a second send attempt, a remote shows a state the TV did not report after refresh, or a confirmation uses another proven session) or **NOT RUN**. Do not infer a send or delivery count from final TV state alone.

## After the protocol

- Record the outcome per step in `DEVELOPMENT_PLAN.md` (item R), keeping simulator evidence and hardware results apart.
- A FAIL becomes a defect with its exact step, before any other phase 1 work.
- The physical-effect column feeds phase 2 (the TV's audible volume, power and navigation according to its real capabilities).
