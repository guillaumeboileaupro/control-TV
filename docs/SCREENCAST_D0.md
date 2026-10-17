# Screen-cast foundation (D0) — local synthetic encode only

Package: `src/control_tv/screencast/` (issue #80). Architecture and hardware findings:
[`REMOTE_DISPLAY_STUDY.md`](REMOTE_DISPLAY_STUDY.md) and `docs/D0_VAAPI_ARCHITECTURE.md` (PR #79).

**What it does:** encodes FFmpeg's generated test picture (`lavfi`, e.g. `testsrc2`) to an H.264
file in a private temporary directory, with the GPU (VA-API, constant QP) or, when the caller
explicitly allows it, with the CPU (`libx264`).

**What it does not do:** no desktop capture, no network listener or stream, no Cast, TV, ADB or
receiver command, no X11 or kernel change, no UI. The display source, publisher and receiver of
D1/D2 are future ports, not implemented here.

## Usage (Python, inside the project's environment)

```python
from control_tv.screencast import (
    EncoderPreference, FfmpegCapabilityProbe, ScreenCastService, SubprocessRunner,
    SyntheticEncodeRequest, SyntheticSource, VideoProfile,
)

request = SyntheticEncodeRequest(
    SyntheticSource("testsrc2"),
    VideoProfile(width=1920, height=1080, fps=30, duration_seconds=10),
    EncoderPreference.VAAPI_THEN_SOFTWARE,   # or VAAPI_ONLY / SOFTWARE_ONLY
    qp=24,                                   # VA-API CQP quality
    crf=23,                                  # libx264 quality, used only by the fallback
)
with ScreenCastService(SubprocessRunner(), FfmpegCapabilityProbe()) as cast:
    choice = cast.start_synthetic(request)   # choice.fallback_reason says why if CPU was used
    result = cast.wait(timeout=60)           # EncodeResult: encoder, output, size, elapsed
# leaving the block stops the encoder and deletes the private directory and file
```

Running this encodes on the local machine; it is **not** run by the test suite or CI.

## Design

| Module            | Responsibility                                                                                                                                                                                                                                                                                |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model.py`        | validated values (render node `/dev/dri/renderD<n>`, allowed test patterns, even size 16–4096, 1–60 fps, 1–600 s, QP 1–51, CRF 0–51, x264 presets), encoder plans `VaapiCqp` / `SoftwareX264`, states, errors                                                                                 |
| `ffmpeg.py`       | pure argument vectors; VA-API always `-rc_mode CQP -qp N` and **never** `-b:v`; parsing of `ffmpeg -encoders`                                                                                                                                                                                 |
| `capabilities.py` | `CapabilityProbe` port; FFmpeg probe (encoder list, render node access); `select_encoder` policy — the software fallback only with `VAAPI_THEN_SOFTWARE`, with its reason                                                                                                                     |
| `process.py`      | `ProcessRunner` / `ManagedProcess` ports; `SubprocessRunner` (argument vector, no shell, no stdin, stderr to a private file, own process group); `stop_process` (SIGTERM to the group, then SIGKILL)                                                                                          |
| `service.py`      | `ScreenCastService` state machine `idle → starting → running → (stopping →) stopped / failed`; private 0700 working directory per session; timeout stops the encoder; `close()` / `with` always stops and cleans; an `atexit` hook stops a running encode when the interpreter exits normally |

Failures never pass silently: an unavailable encoder, a process that cannot start, a non-zero exit
(with the end of FFmpeg's stderr), an empty output or a timeout all end in `failed` with a message,
and leave no process or directory behind.

## Verified vs not verified

| Claim                                                                                  | Status                                                                             |
| -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Synthetic H.264 VA-API CQP encode, 1920×1080@30, QP 24, on the owner's Intel UHD (iHD) | **verified manually** (D0, PR #79): 300 frames, 4.1× real time                     |
| Bitrate mode (`-b:v 5M`) on that driver                                                | **fails** (driver supports CQP only) — never used                                  |
| Command construction, validation, selection, state machine, cleanup                    | **automated tests** (fakes; real processes are the test interpreter, never FFmpeg) |
| This package running FFmpeg on the owner's machine                                     | **not run** in this PR                                                             |
| `libx264` fallback encode                                                              | not run on hardware                                                                |
| Live desktop capture, streaming, TV playback, latency                                  | **not implemented, not tested** (D1+)                                              |

## Limitations

- A process killed with SIGKILL cannot stop its child; the encode is then bounded by its duration
  (at most 600 s). Normal exit, exceptions and `close()` stop it.
- One session per service; a new session replaces the last one's files.
- `h264_qsv` is listed by the host's FFmpeg but not used (VA-API is the measured path).
- Not wired to the bridge or the UI (deferred until D1, per the issue).
