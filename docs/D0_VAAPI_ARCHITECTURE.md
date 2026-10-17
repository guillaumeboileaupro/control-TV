# D0 — Intel VA-API findings and screen-sharing pipeline

Status: **hardware inventory and synthetic H.264 VA-API CQP encode validated; live X11 capture and TV reception untested** (2026-10-17). This complements [REMOTE_DISPLAY_STUDY.md](REMOTE_DISPLAY_STUDY.md); it does not replace the proposed receiver evaluation or authorize TV access.

## Measured host environment

- Dell Ubuntu X11, Intel Comet Lake-H GT2 UHD Graphics, kernel driver `i915`.
- `xrandr`: `eDP-1` connected, primary, 1920×1080 at 60 Hz; HDMI/DP outputs disconnected; framebuffer limit 16384×16384. This **does not** prove virtual-monitor support.
- DRM render node: `/dev/dri/renderD128`.
- VA-API 1.14, Intel `iHD` driver 22.3.1. The inspected `vainfo` output was reported to
  advertise H.264 Main/High encoding through `VAEntrypointEncSliceLP` (the low-power slice
  entrypoint); H.265 encoding was not reported, only decoding. The repository does not retain the
  complete `vainfo` output, the exact FFmpeg version or a verbose FFmpeg log, so these advertised
  capabilities do not establish which entrypoint FFmpeg selected for the successful command below.
- FFmpeg lists `h264_vaapi`, `h264_qsv`, `libx264`. Listing an encoder is not evidence that an encode succeeds.
- GStreamer executable resolves to Anaconda, not necessarily the Ubuntu system installation.

## First local encode attempt: FAILED

Command (synthetic test pattern only, no desktop capture, network or TV):

```bash
ffmpeg -hide_banner -vaapi_device /dev/dri/renderD128 \
  -f lavfi -i testsrc2=size=1920x1080:rate=30 -t 10 \
  -vf 'format=nv12,hwupload' -c:v h264_vaapi -b:v 5M \
  -y /tmp/control-tv-vaapi-test.mp4
```

FFmpeg reported:

```text
Driver does not support any RC mode compatible with selected options (supported modes: CQP).
Error initializing output stream
Conversion failed!
```

The output MP4 is invalid/incomplete (`ffprobe`: `moov atom not found`). **Do not mark H.264 hardware encoding as passing.** The driver advertises only CQP for this configuration; the bitrate-based invocation requested an incompatible rate-control mode.

## Second local-only experiment: PASSED

```bash
ffmpeg -hide_banner -vaapi_device /dev/dri/renderD128 \
  -f lavfi -i testsrc2=size=1920x1080:rate=30 -t 10 \
  -vf 'format=nv12,hwupload' -c:v h264_vaapi \
  -rc_mode CQP -qp 24 \
  -y /tmp/control-tv-vaapi-test.mp4
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate \
  -of default=noprint_wrappers=1 /tmp/control-tv-vaapi-test.mp4
```

Observed output: 300 frames, 1920×1080 at 30 fps, H.264 High, approximately 12 MB in 10 seconds, 9.9 Mbit/s, encoder speed 4.1× real time; `ffprobe` confirmed `codec_name=h264`, `width=1920`, `height=1080`, `r_frame_rate=30/1`. This validates the **synthetic-source hardware encode** only, not live X11 capture, sustained latency, network transmission, playback on the TV or use of the low-power entrypoint. `-rc_mode CQP` selects constant-quantizer rate control and `-qp 24` supplies its quantizer; neither option selects `VAEntrypointEncSliceLP`. The recorded command did not request `-low_power 1`, and no retained verbose log proves that FFmpeg used that entrypoint.

Do not add `-low_power 1` from the capability listing alone: first verify that the installed FFmpeg
exposes the option and record the entrypoint it actually selects. A future, separately authorized
local measurement must retain `ffmpeg -version`, the complete relevant `vainfo` entries, verbose
FFmpeg output, exit code, a full decode/frame count, elapsed time and CPU/GPU use. If another FFmpeg
build rejects `-rc_mode CQP`, inspect `ffmpeg -hide_banner -h encoder=h264_vaapi` and adapt to its
documented options; do not silently fall back to a bitrate mode. Only after the local synthetic test
passes, design a **separately authorized** desktop-capture experiment.

## Proposed pipeline and boundaries

```text
control-TV UI (explicit Start / Stop; visible active state)
    -> existing bridge -> ScreenCastService (future product service)
        -> DisplaySelection (configuration value: existing output in D1, virtual output in D2)
        -> ScreenCapture (selected X11 pixels and pointer policy)
        -> StreamingSessionService (D1 lifecycle orchestration)
            -> ScreenEncoder (adapter over the D0 engine)
            -> StreamPublisher (session-scoped publication)
            -> StreamReceiver (receiver start/stop)
        -> product outcome: started / receiver-confirmed / physically observed remain distinct
```

PR #81 implements only local, synthetic-source encoding command construction and process lifecycle
with mocked adapters and CI tests. Its VA-API-to-libx264 fallback is used only when the caller
explicitly authorizes that preference; it is never implicit. PR #83 defines `DisplaySelection`,
`ScreenCapture`, `ScreenEncoder`, `StreamPublisher`, `StreamReceiver` and the
`StreamingSessionService` orchestration shown above. It deliberately provides no concrete capture,
D0 encoder adapter, publisher or receiver. `StreamReceiver.start()` means that receiver startup
returned successfully; it does not prove receiver confirmation or a picture physically displayed on
the TV.

Keep receiver choices (Cast HLS, Cast Streaming, Sunshine/Moonlight) open until real compatibility
and latency are measured with separate owner authorization. The Freebox compatibility of every path
remains unproven. Never equate a Cast media receiver with support for arbitrary screen-mirroring
protocols.

### D1 duplication

Capture the existing `eDP-1` 1920×1080 output, encode, publish and display on the TV **only after owner consent for the actual session**. Avoid writing captured desktop frames to persistent disk; explicit stop, crash and app-exit cleanup are mandatory.

### D2 true extension

Require a display output that GNOME/X11 recognizes as an independent monitor. `xrandr --setmonitor` alone may not provide a usable virtual output; `evdi`/Xorg changes require separate approval. No kernel module or Xorg change in D0/D1.

## Next verification gates

1. CQP synthetic encode and `ffprobe` **PASS**; full decode, sustained throughput and CPU/GPU usage still to measure.
2. Record the missing FFmpeg/VA-API evidence before deciding whether low-power mode is required;
   keep CQP selection separate from entrypoint selection.
3. Adapt the D0 engine to #83's `ScreenEncoder` port without copying its process management; add a
   concrete `ScreenCapture` only in an owner-authorized D1 iteration.
4. Obtain separate owner approval for each actual desktop capture, stream, receiver test, R1–R4/C1–C7, ADB, software installation, and system graphics changes.
5. Preserve RD1–RD8 as **proposed** criteria pending owner adoption; keep D1 duplication and D2 extension separate.
