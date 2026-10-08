# Ubuntu and Android remotes on one TV: hardware validation protocol

Issue #37, phase 1, item R of `DEVELOPMENT_PLAN.md`. The contract below is pinned by automated tests on a simulated TV shared by two independent `ControlService` instances (`tests/shared_tv.py`, `tests/test_dual_controller.py`). Those tests are simulator evidence only. This protocol is how the owner checks the same contract on a real TV with the real Ubuntu application and the real Android app. It has **not been run**. Nothing in it may be marked validated before it has been, step by step, with the evidence recorded.

## Contract under test

1. Each remote finds the TV itself and keeps no copy of its state: after the other remote acts, its next status read (Refresh status, a new selection, or the confirmation of its own next command) shows what the TV reports now.
2. Each command is attempted once, by the remote that sent it, and is never resent automatically. When delivery is uncertain, the TV may have received it zero or one time. Commands from both remotes race at the TV; the last one actually delivered wins.
3. A remote says a command is confirmed only from what the TV reports for the same media session; a change made meanwhile by the other remote leaves it unconfirmed.

Not expected: synchronization between the two remotes, a periodic refresh (neither remote refreshes on its own; this is a known open item), or a remote showing the other's change before its own next read.

## Equipment and builds

- The Ubuntu application from `main` (development mode `npx --prefix ui tauri dev`, or the installed release `.deb`), and the Android app (APK) built from `main` (`python3 scripts/dev.py android-apk`): **not** the home-screen widget of PR #33, which is not merged and is not part of this protocol.
- One TV (Chromecast or Google TV), the computer and the phone on the same Wi-Fi network. For playback steps, media playing on the TV, started from any usual sender app (for example YouTube on the phone), and its name noted only in the private record.
- Optional, for the evidence: `adb logcat -s control-tv` on the Android side, and the Ubuntu application's terminal output in development mode.

## Rules

- **Read-only steps (R1-R4) send no command to the TV.** Each command step (C1-C7) is run only with the owner's explicit go-ahead, one command at a time.
- A remote never resends a command. If a command's result is uncertain ("may or may not have reached the TV", "not confirmed"), use Refresh status on both remotes, never the command again, and record what they show.
- The record is private (for example `.ai-private/hardware/dual-controller/`, git-ignored): no TV name, address, device identifier or media identifier is committed. Only the outcome classes below go into `DEVELOPMENT_PLAN.md`.

## Read-only steps

| Step | Action | Expected |
|---|---|---|
| R1 | Discover on Ubuntu, then on Android | each lists the TV; the TV appears in each remote only after its own discovery |
| R2 | Select the same TV on both | both show it connected, with the same playback state, volume and mute (each as reported, or "not reported") |
| R3 | Change the TV with its own remote control or the sender app (pause, volume); then Refresh status on Ubuntu, then on Android | before the refresh each shows the old state; after its refresh each shows the new state, the same on both |
| R4 | Refresh several times on both | no command reaches the TV (nothing changes on screen or in sound because of a refresh) |

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

C7 is **NOT RUN for no-replay evidence** without the send-attempt instrumentation. Visible TV
state and refreshed status may still be recorded as recovery evidence, but identical commands
are not countable from their final state. App-side instrumentation proves one local send
attempt; only packet or receiver-side capture can count actual deliveries to the receiver.

## What to record for each step

Date and time; remote (Ubuntu or Android) and build (commit); action; the acting remote's wording (sent, confirmed, not confirmed, may or may not have reached the TV, refused) and its state shown; what was seen and heard on the TV (physical effect: yes, no, not checked); the other remote's state before and after its refresh; any second command observed on the TV; and, for C6/C7, the required instrumentation records. Classify each step as **PASS**, **FAIL** (contract broken: instrumentation shows a second send attempt, a remote shows a state the TV did not report after refresh, or a confirmation uses another proven session) or **NOT RUN**. Do not infer a send or delivery count from final TV state alone.

## After the protocol

- Record the outcome per step in `DEVELOPMENT_PLAN.md` (item R), keeping simulator evidence and hardware results apart.
- A FAIL becomes a defect with its exact step, before any other phase 1 work.
- The physical-effect column feeds phase 2 (the TV's audible volume, power and navigation according to its real capabilities).
