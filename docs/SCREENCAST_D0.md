# Screen-cast foundation (D0) — local synthetic encode only

Package: `src/control_tv/screencast/` (issue #80). Architecture and hardware findings:
[`REMOTE_DISPLAY_STUDY.md`](REMOTE_DISPLAY_STUDY.md) and the D0 VA-API inventory of
[PR #79](https://github.com/guillaumeboileaupro/control-TV/pull/79) (not merged yet).

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
    EncodeRequest, SyntheticSource, VideoProfile,
)

request = EncodeRequest(
    SyntheticSource("testsrc2"),
    VideoProfile(width=1920, height=1080, fps=30, duration_seconds=10),  # at most 120 s
    EncoderPreference.VAAPI_THEN_SOFTWARE,   # or VAAPI_ONLY / SOFTWARE_ONLY
    qp=24,                                   # VA-API CQP quality, 1–51
    crf=23,                                  # libx264 quality, 0–51, used only by the fallback
)
with ScreenCastService(SubprocessRunner(), FfmpegCapabilityProbe()) as cast:
    choice = cast.start(request)             # choice.fallback_reason says why if CPU was used
    result = cast.wait(timeout=60)           # EncodeResult: encoder, output, size, elapsed
# leaving the block stops the encoder and deletes the private directory and file
```

Running this encodes on the local machine; it is **not** run by the test suite or CI.

## Design

| Module            | Responsibility                                                                                                                                                                                                                                                                                                                                      |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model.py`        | validated values (render node `/dev/dri/renderD<n>`, even size 16–4096, 1–60 fps, 1–120 s, QP 1–51, CRF 0–51, x264 presets); sources as typed **specifications** (`SyntheticSource`, the only `VideoSource` in D0, accepted by exact type); `EncodeRequest.revalidated()` rebuilds a request from plain values; plans, states, errors               |
| `ffmpeg.py`       | pure argument vectors; `source_arguments` alone turns a known source type into input arguments and refuses anything else; VA-API always `-rc_mode CQP -qp N` and **never** `-b:v`; every encode bounded by `-t` and `-fs 256 MiB`; parsing of `ffmpeg -encoders`                                                                                    |
| `capabilities.py` | `CapabilityProbe` port; FFmpeg probe (encoder list read as UTF-8 with replacement, render node access); `select_encoder` policy — the software fallback only with `VAAPI_THEN_SOFTWARE`, with its reason                                                                                                                                            |
| `process.py`      | `ProcessRunner` / `ManagedProcess` ports over a whole **process group**; `SubprocessRunner` (argument vector, no shell, no stdin, stderr to a private file, new session); the leader is reaped only once its group is empty, so the group id cannot be recycled while signals may reach it; `stop_process` (SIGTERM, then SIGKILL, to the group)    |
| `service.py`      | `ScreenCastService` state machine `idle → starting → running → (stopping →) stopped / failed`; D0 source policy on a rebuilt request before any probe; private 0700 working directory per session; one result check for `wait()` and `stop()`; kept groups and `reclaim()`; one call at a time; `close()` / `with` / `atexit` always stop and clean |

Failures never pass silently and only `ScreenCastError` subclasses escape (an interruption such as
Ctrl-C is cleaned, then re-raised): an unavailable encoder, a session directory that cannot be
created or made private, a process that cannot start or be waited for, unreadable output or stderr
and any internal error all end in `failed` with a message, and leave no directory behind.
`stopping` never outlives a call.

**What counts as a result** (same check whether `wait()` or `stop()` sees the end): the whole group
is gone, the leader exited 0, the output is a non-empty regular file (not a link) below 256 MiB
(reaching the limit means FFmpeg cut it), and FFmpeg logged nothing at `-loglevel error`.
Anything else is `failed`.

**Groups that survive SIGKILL** (for example blocked in a GPU driver) are **not** claimed stopped:
the session fails, the error names the PID, and the group is kept unreaped (its id stays
reserved) in `unstoppable_pids`. `reclaim()`, `start()` and `close()` poll it again, send SIGKILL
again and release it once the kernel lets it go. While one is kept, `start()` refuses a new encode.
If the kernel never lets it go, it stays listed until the application exits.

**`close()` never loses a process:** if stopping meets an internal error (not a
`ScreenCastError`), `close()` records it, then stops the session's group through the same group
stop; if that cannot be confirmed or fails too, the handle is kept in `unstoppable_pids`. Every
error met is kept in `last_error`, in order. After `close()`, each process the service started is
either stopped and reaped, or kept; repeated `close()` calls only poll the kept groups again.

**Concurrency:** one call at a time. A call made while another is running (another thread) is
refused with `ScreenCastStateError`; a refused `close()` does nothing and keeps the exit hook.

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

- A parent killed with SIGKILL cannot stop its child (no hook runs); the orphan encode still ends
  by itself after its duration (at most 120 s) and its file cannot exceed 256 MiB, in a
  `control-tv-screencast-*` directory under the system temporary directory that is then left
  behind. Normal exit, exceptions and `close()` stop the encoder and remove the directory.
- A descendant that leaves the group on purpose (`setsid`, `setpgid`) is not covered (FFmpeg does
  not do this); group membership is read from `/proc` (Linux).
- A process stuck in the kernel (uninterruptible GPU wait) survives SIGKILL; it is reported and
  kept, never hidden, and only the kernel can end it; no new encode starts meanwhile.
- With `-loglevel error`, any line FFmpeg writes fails the encode, even with exit status 0: strict
  on purpose for D0 (a false failure is visible, a false success is not).
- One session per service; a new session replaces the last one's files.
- `h264_qsv` is listed by the host's FFmpeg but not used (VA-API is the measured path).
- Not wired to the bridge or the UI (deferred until D1, per the issue).
