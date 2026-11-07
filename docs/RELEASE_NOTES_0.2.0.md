# control-TV 0.2.0

Two standalone remotes, the Ubuntu application and the Android application, for a Google TV
(Freebox under Android TV), with no permanent server.

## New

- **Screen to TV over WebRTC (Ubuntu):** Duplicate a screen or Extend the desktop onto the TV,
  through ControlTV Receiver on the TV. The WebRTC engine now uses `webrtc` 0.21.
- **Google Assistant:** the Assistant button opens the TV's Google Assistant; on Android, a
  push-to-talk microphone session sends your voice to it.
- **Apps on the TV:** opened through ControlTV Receiver, with the receiver's own answer as proof.
- **Known TVs:** a TV chosen once is remembered and found again on the network before anything
  is sent to it.
- **One pairing per device**, kept when switching between Ubuntu and the phone.
- **Command journal:** each command's phases are recorded with a correlation id.

## Checked on the owner's devices (2026-11-07)

- Duplicate and Extend from Ubuntu to the TV: the picture is shown full screen, with about one
  second of delay.
- Google Assistant opens on the TV with the Assistant key (`KEYCODE_SEARCH`).
- Pairing, discovery and opening apps from the phone (owner's observation).

Not yet checked on a device: Stop and the restoration of the screens and sound with a TV, the
phone's microphone session, the Android widget and the GNOME menu.

## Install

- **Ubuntu 22.04 (amd64):** `sudo dpkg -i control-tv_0.2.0_amd64.deb` (needs `adb`, `ffmpeg`,
  `libayatana-appindicator3-1`, `x11-xserver-utils`). Extend needs a free video output, enabled
  once per boot: `echo on-digital | sudo tee /sys/class/drm/card1-HDMI-A-1/status`.
- **Android phone (arm64):** `control-tv-phone-0.2.0.apk`, sideloaded.
- **TV:** `controltv-receiver-0.2.0.apk`, installed with ADB (`adb install -r`).

The APKs are signed with a development key, not a store key: Android warns about sideloading,
and they update an installation made with the same key only. Check every file against
`SHA256SUMS`.

## License

GPL-3.0-or-later. The source offer, with the source archives the licenses ask for, is in
`SOURCE-OFFER.md` and on this release page.
