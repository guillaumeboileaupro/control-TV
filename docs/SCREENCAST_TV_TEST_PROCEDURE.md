# Screen to TV (D1–D4): commands and TV test procedure

Status: **software complete, not run on the TV.** Everything below was validated by automated
tests (fakes, loopback) and, for the capture, on a private Xvfb display; nothing was sent to a
TV, no GPU encode was tried and the owner's desktop was never captured or changed. This document
is the procedure for the owner-authorized hardware run; it must not be started without that
authorization, step by step (skill `hardware-safety`).

## What is delivered

| Phase | Delivered | Validated by |
| ----- | --------- | ------------ |
| D1 duplication | X11 output capture (`x11grab`, scaled to 1280x720 or 1920x1080, 25/30 fps, with the system sound in AAC) → D0 live encoder (libx264, VA-API optional) → HLS publisher (the TV is loaded once four 2 s segments exist) → Cast default media receiver | automated tests; real FFmpeg on loopback (synthetic picture and private Xvfb output) |
| D2 extension | virtual monitor `control-tv-virtual` added to the right of the X screen with `xrandr` (runtime only, no configuration file), streamed, then removed and the screen size restored | automated tests with a fake `xrandr`; refusals on Xvfb |
| D3 interface | "Screen to TV" card in the Ubuntu window: Duplicate a screen / Extend the desktop, screen, resolution, frame rate, consent step, Stop/Dismiss, state and error text | TypeScript and Rust unit tests; Python bridge tests |
| D4 protections | consent required by the bridge for every start; one stream at a time; one-hour bound; stop on Stop, on failure and when the application quits; LAN-only publication with a random port and token | automated tests |

The card is shown only where the screens can be listed (an X11 desktop session). On Android and
on Wayland it stays hidden; the command below reports the reason.

## Commands

From the repository root with the project environment (`python3 scripts/dev.py setup`):

```sh
# Read only: the screens that can be duplicated.
.venv/bin/python -m control_tv.screen_streaming.d1 --list-screens

# No TV: a real HLS stream served on loopback (synthetic picture, or one screen).
.venv/bin/python -m control_tv.screen_streaming.d1 --no-tv --seconds 30
.venv/bin/python -m control_tv.screen_streaming.d1 --no-tv --screen OUTPUT --size 1280x720 --seconds 30

# TV (authorization required): duplicate one screen, or extend the desktop onto the TV.
.venv/bin/python -m control_tv.screen_streaming.d1 --device DEVICE_ID --screen OUTPUT \
    --size 1920x1080 --fps 30 --seconds 120
.venv/bin/python -m control_tv.screen_streaming.d1 --device DEVICE_ID --extend \
    --yes-change-display --size 1920x1080 --refresh 60 --position right --seconds 120
```

**Extension prerequisite (once per boot, root, owner's decision):** GNOME ignores a video output
reported "disconnected", so a free connector must first be reported connected with no screen
behind it. Find it with `ls /sys/class/drm/`, then
`echo on-digital | sudo tee /sys/class/drm/card1-HDMI-A-1/status` (undo:
`echo detect | sudo tee …`, or reboot). Without it the card says the extension needs a free
video output and nothing is changed. Measured on the owner's desktop: 1280×720 and 1920×1080 at
60 or 50 Hz in every position; 1920×1080 at 30 Hz was refused (not offered).

`--encoder software|vaapi|auto` chooses the encoder (`software` by default; VA-API uses the GPU
and needs its own authorization). The command stops at `--seconds`, on Ctrl-C or SIGTERM, or as
soon as FFmpeg ends; it prints no address, URL or token.

Application (Ubuntu, development build): `npx --prefix ui tauri dev`, choose the TV, then the
"Screen to TV" card. The application's bridge methods are `list_screens`,
`start_screen_stream` (`deviceId`, `mode` duplicate|extend, `screen`, `width`, `height`, `fps`,
`consent`), `stop_screen_stream` and `get_screen_stream`.

## Preconditions for the TV run

1. Owner authorization for: Cast commands to the chosen TV, a capture of the named X11 output,
   and (for D2) a runtime change of the X screen size.
2. Ubuntu X11 session (`echo $XDG_SESSION_TYPE` prints `x11`), FFmpeg with libx264, the TV on
   the same home network, the remote and the Android app working (for RD7).
3. A window with nothing private on the screen to capture.

## Procedure (results recorded per RD criterion of `docs/REMOTE_DISPLAY_STUDY.md`)

1. **RD1 duplication** — in the card: Duplicate a screen, choose the screen, 1920×1080, 30 fps,
   "Duplicate on the TV", read the consent text, "Show on the TV". Expected: state "Streaming to
   the TV."; the picture on the TV within 10 s. Record the time from the click.
2. **RD2 latency** — film a clock or pointer on the screen and the TV together; expected ≤ 6 s
   (HLS path A).
3. **RD3 fluidity/resolution** — keep the stream 10 minutes with video in a window; note stalls
   and the resolution the TV reports. Repeat once at 1280×720 and once at 25 fps.
4. **RD5 extension** — with the prerequisite done: Stop, then Extend the desktop, choose the
   resolution, refresh rate and arrangement, consent. Expected: a new screen on the chosen side
   of the desktop; a window moved onto it shows on the TV only; the main screen stays usable.
   Stop: the screen disappears and the desktop is restored (`xrandr --listmonitors`, no
   `control-tv-` mode in `xrandr --query`).
5. **RD6 stop and cleanup** — after Stop, after quitting the application while streaming, and
   after the one-hour bound (or `--seconds` in the command): no `ffmpeg` process
   (`pgrep -a ffmpeg`), no listening port of the stream (`ss -ltnp`), no extended screen and no
   `control-tv-` mode, the TV back to its previous screen or idle.
6. **RD7 coexistence** — while streaming, use the remote keys and the Android app; both keep
   working.
7. **RD8 privacy** — from another LAN host, the stream's port without the token answers 404;
   nothing is reachable from outside the home network (no router change was made).
8. **Error states** — start without a TV selected (refused: "Choose a TV first."), the card offers no
   second start while a stream runs (one stream per application), switch the TV off during a stream
   (state "The stream stopped." with the reason; Dismiss returns to the choice).

## Known limits

- If the application is killed (SIGKILL), its bridge cannot clean up: FFmpeg ends at its own
  bound (one hour), and a leftover extension mode is removed by the next Extend.
- Latency: about 9–11 s measured (HLS path A with the TV's default Cast receiver, which starts
  three segments behind the live edge); 1 s segments and 60 fps were not played smoothly by the
  Freebox's receiver. A strong reduction needs another path (Cast mirroring or a dedicated
  receiver).
- The TV's Cast receiver stays open after a stream; the next stream can then play without
  showing (seen twice); closing the receiver (or restarting the player) restores it.
- Only X11: Wayland sessions are refused, and only local displays are captured.
