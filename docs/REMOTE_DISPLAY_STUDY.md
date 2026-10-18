# Remote display study — Ubuntu (X11) to the Freebox (Google TV)

Status: **study only** (2026-10-17). No capture, no stream and no receiver has been started; nothing
in this document is validated on hardware. Development starts after the remote-control hardware
acceptance ([`HARDWARE_ACCEPTANCE_PLAN.md`](HARDWARE_ACCEPTANCE_PLAN.md)) unless the owner decides
otherwise. Points marked _to check_ must be measured on the owner's machine and TV before any
choice is final.

The receiver comparison and isolated lifecycle prototype are refined in
[`SCREENCAST_D1_RECEIVER_SPIKE.md`](SCREENCAST_D1_RECEIVER_SPIKE.md). That prototype contains no
capture, encoder implementation, listener or receiver adapter and does not demonstrate Freebox
compatibility.

## 1. Goals and terms

- **Duplication (mirroring):** the TV shows what one existing Ubuntu monitor shows.
- **Extension:** the TV becomes an additional monitor of the Ubuntu desktop: windows can be moved
  to it, and it shows content the local screens do not show. This needs a **real or virtual
  display output** that the X server and the window manager treat as a monitor — capturing a
  region of an existing screen is not an extension.
- Both stream the picture (and optionally the sound) from Ubuntu to the TV over the LAN, started
  and stopped only by an explicit user action.

Environment facts: the Ubuntu session is X11 (`XDG_SESSION_TYPE=x11`); the TV is a Freebox running
Android TV with Chromecast built in; control-TV already drives the TV's Cast receiver
(`PyChromecastTransport`) and its Android TV remote channel.

## 2. Options for the TV side (receiver)

| Option                                             | Receiver on the TV                                                             | Latency (expected)        | Audio   | New TV software                                                           | Fit                                                                                                                            |
| -------------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------- | ------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **A. Live HLS/DASH to the Default Media Receiver** | built-in Cast receiver (`load_media`, already implemented)                     | high: ~2–10 s (segmented) | yes     | none                                                                      | duplication or extension of slow-changing content (slides, documents, video); not for pointer work                             |
| **B. Cast mirroring protocol (Cast Streaming)**    | built-in mirroring receiver (the one Chrome's "Cast screen" uses)              | low: ~100–300 ms          | yes     | none                                                                      | best fit if a Linux sender can be built (Open Screen's Cast Streaming sender, C++); protocol and certificate handling to check |
| **C. Custom Cast web receiver + WebRTC**           | own receiver page (Cast app id registered with Google, page hosted over HTTPS) | low: ~100–300 ms          | yes     | none on the TV, but an Internet-hosted receiver and a Google registration | conflicts with "no permanent server"; kept as a fallback                                                                       |
| **D. Sunshine (Ubuntu) + Moonlight (TV app)**      | Moonlight, an existing Android TV app                                          | very low: ~20–60 ms       | yes     | install Moonlight on the TV                                               | mature low-latency path; needs a host daemon while streaming and an app on the TV                                              |
| **E. VNC/RDP viewer on the TV**                    | a viewer app                                                                   | medium                    | VNC: no | install a viewer                                                          | remote control of the desktop rather than a display; not preferred                                                             |

Miracast is not offered by Chromecast/Google TV devices; whether the Freebox player accepts it is
_to check_, and it is not assumed.

**Recommendation for D0 (spike):** measure A first (no new TV software, reuses the existing Cast
path) and evaluate B in parallel (lowest latency without installing anything on the TV); keep D
as the low-latency alternative if B proves impractical. C only if A, B and D all fail.

## 3. Ubuntu side: capture and encoding

- **Capture:** X11 screen capture of one output or of a rectangle (`ffmpeg -f x11grab`, or
  GStreamer `ximagesrc`); the pointer can be drawn or hidden.
- **Encoding:** H.264 (the TV decodes it in hardware). Hardware encoders (VA-API, NVENC) if the GPU
  offers them — _to check_ (`vainfo`, GPU model); otherwise x264 `veryfast`/`zerolatency`, CPU
  cost to measure at 1080p30.
- **Audio:** the PulseAudio/PipeWire monitor of the chosen output, AAC or Opus.
- **Transport:** A — HLS segments served by a local HTTP server bound to the LAN interface facing
  the TV, with a random path token; B/D — their own protocols.

## 4. Extension: creating a virtual monitor under X11

| Technique                                                                    | How                                                                                           | Window manager sees a monitor                      | Notes                                                               |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------- |
| **evdi** (DisplayLink's Extensible Virtual Display Interface, kernel module) | a virtual connector the X server drives like a real output; capture it like any output        | yes                                                | needs a DKMS kernel module (root, Secure Boot signing) — _to check_ |
| **Driver virtual heads**                                                     | Intel `xf86-video-intel` `VirtualHeads`, NVIDIA `ConnectedMonitor`/custom EDID                | yes                                                | depends on the GPU and driver in use — _to check_                   |
| **`xrandr --setmonitor` on an enlarged framebuffer**                         | grow the screen with `xrandr --fb`, declare a monitor on the new area, capture that rectangle | depends on the WM (GNOME/Mutter on X11 _to check_) | no kernel module; may not be honoured by GNOME                      |
| **Separate X server** (Xvfb, `xf86-video-dummy`)                             | a second display                                                                              | no (a different desktop)                           | not an extension of the user's desktop; excluded                    |

**Recommendation:** D2 starts with a read-only inventory (GPU, driver, outputs, `xrandr`, Secure
Boot state), then tries `--setmonitor` (no system change), then evdi (system change: needs the
owner's go-ahead).

## 5. Architecture and interfaces

One engine, as for the remote: the window, the tray and later the widget call the same Python
control layer through the bridge; no capture or Cast logic in Rust or TypeScript.

```text
UI (Dupliquer / Étendre / Arrêter)  ->  bridge  ->  ScreenCastService
                                                     ├─ DisplaySource  (which picture: an output, a virtual monitor)
                                                     ├─ StreamEncoder  (capture + encode, child process, bounded lifetime)
                                                     ├─ StreamPublisher (how the TV gets it: HLS server | Cast Streaming | external host)
                                                     └─ CastTransport.load_media / receiver launch (existing)
```

Proposed ports (Python `Protocol`s, typed, no `Any`):

- `DisplaySource`: `list_outputs() -> tuple[DisplayOutput, ...]`, `create_virtual(spec) -> DisplayOutput`, `remove_virtual(output)`.
- `StreamEncoder`: `start(output, profile) -> EncoderHandle`, `stop(handle)`, `health(handle)`.
- `StreamPublisher`: `publish(handle) -> StreamUrl`, `revoke(url)`.
- `ScreenCastService`: `start_mirror(device_id, output, profile)`, `start_extension(device_id, spec, profile)`, `stop(device_id)`, `status(device_id)`; results distinguish _started_, _receiver playing_ (confirmed from the receiver status) and _seen on the TV_ (owner).

Bridge methods (new): `list_display_outputs`, `start_screen_mirror`, `start_screen_extension`,
`stop_screen_cast`, `get_screen_cast_status`. Every start needs an explicit user action; nothing
starts on launch; `stop` and application exit always stop the encoder, revoke the URL, remove a
created virtual monitor and close the server.

## 6. Security and privacy

- Capture only after an explicit action; a visible indicator while streaming (window and tray).
- Stream server bound to the LAN interface facing the TV, random unguessable path, stopped with the
  session; no port forwarding, no Internet exposure, no relay.
- No recording to disk; segments in a private temporary directory removed on stop.
- The CSP of the window is unchanged (the window does not play the stream).
- Virtual monitors and kernel modules only with the owner's go-ahead; removal documented.

## 7. Tests and acceptance criteria

Automated (CI, no hardware): service state machine (start/stop/crash/exit), cleanup guarantees,
URL token and binding, encoder command construction, bridge contracts, UI states; fakes for the
three ports.

Hardware (owner present, each run authorized):

| Id  | Check                                          | Criterion (proposed, to be adopted by the owner)                                     |
| --- | ---------------------------------------------- | ------------------------------------------------------------------------------------ |
| RD1 | Duplication starts and shows the chosen output | picture on the TV within 10 s (A) / 3 s (B, D)                                       |
| RD2 | Latency                                        | A ≤ 6 s; B/D ≤ 300 ms (pointer move to TV, filmed)                                   |
| RD3 | Fluidity and resolution                        | 1080p, ≥ 25 fps without frequent stalls over 10 min                                  |
| RD4 | Audio (if enabled)                             | in sync within 200 ms (B, D)                                                         |
| RD5 | Extension                                      | the TV is a monitor in display settings; a window moved to it shows only on the TV   |
| RD6 | Stop and cleanup                               | stop, app exit and kill leave no encoder, server, open port or virtual monitor       |
| RD7 | Coexistence                                    | the remote and the Android app keep working while streaming                          |
| RD8 | Privacy                                        | no stream reachable from another LAN host without the token; nothing outside the LAN |

## 8. Risks

| Risk                                                     | Impact                     | Mitigation                                                      |
| -------------------------------------------------------- | -------------------------- | --------------------------------------------------------------- |
| HLS latency too high for desktop use                     | A unusable for interaction | B or D for interactive use; A for passive content               |
| Cast Streaming sender complex or unstable outside Chrome | B delayed                  | time-boxed spike; fallback D                                    |
| GNOME on X11 ignores `--setmonitor`                      | no extension without evdi  | evdi with owner's go-ahead; or extension declared not supported |
| evdi needs root and Secure Boot signing                  | system change              | explicit go-ahead, documented removal                           |
| CPU load of software encoding                            | stutter, fan noise         | hardware encoder if available; lower fps/resolution profiles    |
| Freebox receiver limits (codec, resolution)              | playback failure           | probe in D0 with a short test stream                            |

## 9. Phases and estimates

| Phase | Content                                                                                     | Estimate                       |
| ----- | ------------------------------------------------------------------------------------------- | ------------------------------ |
| D0    | read-only inventory (GPU, encoders, outputs, Secure Boot) + spikes A and B on a test stream | 6–10 h                         |
| D1    | duplication MVP behind the ports (chosen publisher), automated tests                        | 12–20 h                        |
| D2    | extension (`--setmonitor` then evdi), cleanup guarantees                                    | 12–24 h (GPU/driver dependent) |
| D3    | UI: Dupliquer / Étendre / Arrêter, output, resolution, fps, indicator                       | 8–12 h                         |
| D4    | security review, hardware RD1–RD8, documentation                                            | 8–12 h                         |

Total ≈ 46–78 h, after the remote-control acceptance.
