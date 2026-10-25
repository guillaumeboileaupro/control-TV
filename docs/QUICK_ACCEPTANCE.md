# Quick real acceptance (#111)

A short, ordered run of the real trials on the owner's devices, each step only with the owner's
go-ahead (`AGENTS.md` [rule:hardware-safety]). Green CI and automated tests do not validate a step:
only what the owner sees on the device does. The detailed checks stay in
`docs/MANUAL_ACCEPTANCE_PROTOCOL.md` (remotes) and `docs/SCREENCAST_TV_TEST_PROCEDURE.md` (screen
to TV). Record each result (OK / KO, time, what was seen), without device identifiers or
addresses, kept apart from CI results.

## Before

- The three builds come from the CI run of the #111 commit under test (7-day artifacts):
  `control-tv-linux-release` (`.deb`), `control-tv-android-arm64-debug` (phone remote) and
  `control-tv-android-tv-receiver-debug` (TV receiver). Compare each SHA-256 with the run summary.
- Phone, TV and Ubuntu computer on the same local network; WebRTC trials only there (the
  RUSTSEC-2026-0387 / RUSTSEC-2025-0141 waivers allow local-network trials only, until
  2026-11-30, see `SECURITY.md`).
- A debug APK signed with another key cannot update the installed one: the previous app must be
  uninstalled first, which drops its pairing (the owner decides).

## Ubuntu

- [ ] **Install and window:** install the `.deb`, start control-TV. The window opens at
  1280x820 with the TV choice, playback and sound on the left and the remote in the middle
  (the TV's apps beside it on a wide window), not a phone column.
- [ ] **TV and pairing:** find the TV, select it, pair with the code the TV shows.
- [ ] **Directional pad:** press each quarter near its edge, near its corners and next to OK, then
  OK itself: each press moves the TV's focus once, in the direction pressed; no press is lost
  between two arrows.
- [ ] **Remote keys:** Back, Home, volume and mute, Power, then Google Assistant, Input, Settings,
  Info, Guide, channel down and up, Subtitles, Audio, the four colour keys, the media keys and
  the numbers: each acts once on the TV. Note any key the TV ignores.
- [ ] **TV apps:** list the TV's apps; names and logos as expected; open one.
- [ ] **Screen to TV, HLS:** duplicate (D1), then extend (D2), one minute each, then Stop. Picture on
  the TV; the sound plays on the TV only, and the computer's speakers come back after Stop; the
  desktop layout is back as before.
- [ ] **Screen to TV, WebRTC:** with Liberté boîte, then the Chromecast: WebRTC is offered as
  confirmed, picture and sound reach the TV, a short network cut shows a reconnection, Stop is
  clean.

## Phone (Android remote)

- [ ] **Install:** the app appears on the main profile's home screen.
- [ ] **Pairing and remote:** pair with the TV, then the pad by touch (edges and corners of each
  quarter, OK) and the keys as on Ubuntu.
- [ ] **TV apps:** the TV's apps are listed from the phone (classic network debugging, port 5555);
  open one.
- [ ] **Widget:** add it (or enlarge the existing one to 4x4 to show its remote rows); use play/pause
  once, then Mute, Volume - and +, each arrow, OK, Back, Home, Power and Assistant once each: the TV
  acts once per tap and the widget says which key was sent.
- [ ] **Two remotes:** Ubuntu and the phone in turn on the same TV: each keeps its own state, nothing
  is sent twice.

## Each TV (Liberté boîte, Chromecast)

- [ ] **Receiver:** "ControlTV Récepteur" with its logo in the TV's apps.
- [ ] **Screens:** opened by hand, it shows "En attente de ControlTV" and how to start a stream;
  during a stream "Diffusion en cours" and the TV does not go to sleep; after a network cut
  "Connexion interrompue", then the picture returns; after Stop or an error, how to restart.
- [ ] **No ghost session:** stop and restart a stream twice, leave the receiver with Home, come
  back: only the new stream plays, and the receiver waits again when none runs.

## Known limits (not to be counted as failures)

- **Microphone:** the Assistant key opens the TV's Assistant; the phone's voice is not sent yet
  (the Android capture exists, the voice session in the control layer is to be wired with Codex).
- **App logos:** RMC BFM Play, Aktu Free, M6+, OQEE, TFOU MAX and Diagnostic Freebox keep their
  initial (no logo with a shippable licence).
