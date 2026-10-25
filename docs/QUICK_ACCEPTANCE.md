# Quick real acceptance (#111)

A short, ordered run of the real trials on the owner's devices, each step only with the owner's
go-ahead (`AGENTS.md` [rule:hardware-safety]). The detailed checks stay in
`docs/MANUAL_ACCEPTANCE_PROTOCOL.md` (remotes) and `docs/SCREENCAST_TV_TEST_PROCEDURE.md` (screen
to TV). Record results as hardware evidence, kept apart from CI results.

## Before

- The three builds come from the CI run of the #111 commit under test (7-day artifacts):
  `control-tv-linux-release` (`.deb`), `control-tv-android-arm64-debug` (phone remote) and
  `control-tv-android-tv-receiver-debug` (TV receiver). Compare each SHA-256 with the run summary.
- Phone, TV and Ubuntu computer on the same local network; WebRTC trials only there (the
  RUSTSEC-2026-0387 / RUSTSEC-2025-0141 waivers allow local-network trials only, until
  2026-11-30, see `SECURITY.md`).

## Steps

1. **Ubuntu:** install the `.deb` (`sudo apt install ./control-tv_*_amd64.deb`), start control-TV,
   find the TV, select it. Expected: the TV is listed and its state is read.
2. **Ubuntu remote:** pair, then the five pad zones, Back, Home, volume and mute, Power. Expected:
   each key acts once on the TV; a key whose effect the TV does not report says so.
3. **Android remote:** install the phone APK, repeat steps 1-2 from the phone, then the widget.
   Both remotes used in turn must keep their own state and never resend a command.
4. **TV apps:** list the TV's apps and open one (USB or wireless debugging as the TV offers).
5. **Screen to TV, HLS:** duplicate (D1) then extend (D2) for one minute each, then Stop.
   Expected: picture and sound on the TV; after Stop the desktop layout is back as before.
6. **Screen to TV, WebRTC:** install the receiver APK on the TV, start a WebRTC stream from the
   Ubuntu card, interrupt the network briefly, then Stop. Expected: picture and sound, a visible
   reconnection, a clean stop.
7. **After:** uninstall or keep the builds as the owner decides; note every failure with its step,
   time and the trace file, without device identifiers or addresses.
