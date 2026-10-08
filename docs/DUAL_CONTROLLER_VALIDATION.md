# Ubuntu and Android remotes on one TV: hardware validation protocol

Issue #37, phase 1, item R of `DEVELOPMENT_PLAN.md`. The contract below is pinned by automated tests on a simulated TV shared by two independent `ControlService` instances (`tests/shared_tv.py`, `tests/test_dual_controller.py`). Those tests are simulator evidence only. This protocol is how the owner checks the same contract on a real TV with the real Ubuntu application and the real Android app. It has **not been run**. Nothing in it may be marked validated before it has been, step by step, with the evidence recorded.

## Contract under test

1. Each remote finds the TV itself and keeps no copy of its state: after the other remote acts, its next status read (Refresh status, a new selection, or the confirmation of its own next command) shows what the TV reports now.
2. Each command is sent once, by the remote that sent it, and is never resent automatically, even when its delivery is uncertain or the other remote changes the TV meanwhile. Commands from both remotes race at the TV; the last one delivered wins.
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
| C5 | Both at once: Pause on Ubuntu and Play on Android, pressed together | each remote sent its command once; at least one may say "not confirmed" | after both refresh, both show the TV's actual state (the last command that reached it); record which one won |
| C6 | While Ubuntu's command is being confirmed, change media on the TV from the sender app (start another video) | Ubuntu's command is not shown as confirmed for the new video | both show the new video after a refresh |
| C7 | Stop the Wi-Fi of one remote right after pressing a command (only if the owner agrees to try it) | "may or may not have reached the TV", never a resend | after the remote reconnects and both refresh, both show the TV's actual state; the TV received the command at most once |

## What to record for each step

Date and time; remote (Ubuntu or Android) and build (commit); action; the acting remote's wording (sent, confirmed, not confirmed, may or may not have reached the TV, refused) and its state shown; what was seen and heard on the TV (physical effect: yes, no, not checked); the other remote's state before and after its refresh; any second command observed on the TV (expected: none). Classify each step as **PASS**, **FAIL** (contract broken: a resend, a remote showing a state the TV did not report after its refresh, a confirmation for another session) or **NOT RUN**.

## After the protocol

- Record the outcome per step in `DEVELOPMENT_PLAN.md` (item R), keeping simulator evidence and hardware results apart.
- A FAIL becomes a defect with its exact step, before any other phase 1 work.
- The physical-effect column feeds phase 2 (the TV's audible volume, power and navigation according to its real capabilities).
