# D1 receiver and publication spike — no capture, network or device

Status: **automated prototype only**. This work does not capture the desktop, encode media, open a
listener, contact the Freebox or prove that any receiver path works on that hardware. It prepares
the boundary that will consume D0 after PR #81 is merged and reviewed.

## Evidence and comparison

The comparison deliberately separates protocol documentation from Freebox validation. The box is
known to expose a Cast receiver and to run Android TV, but none of the three paths below has been
exercised on it for screen streaming.

| Path                               | What primary documentation establishes                                                                                                                                                                                                                                                                   | Expected engineering properties                                                                                                                                                                                                                                                                                                                                                              | Freebox status                                                                                                                                                                                                                                   |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **A — HLS through Cast**           | Google's Web Receiver supports live HLS and H.264-compatible media. The Default Media Receiver is appropriate when the format is supported and no custom logic is required. HLS playlists and segments must be reachable by the receiver; Cast requires appropriate CORS behavior.                       | Lowest integration cost: reuse `CastTransport.load_media` after a publisher exists. Segment production, playlist polling and receiver buffering make latency structurally higher than real-time mirroring. Hardware H.264 lowers host CPU load; software x264 raises it. A session-scoped HTTP listener is required while streaming, but no permanent service or TV application is required. | **Not demonstrated.** The Freebox's exact live-HLS behavior, minimum stable segment duration, CORS behavior, buffering, resolution and sustained playback must be measured.                                                                      |
| **B — Cast Streaming / mirroring** | Chromium Open Screen's `libcast` implements Cast Streaming sessions and ships example sender/receiver programs. The published protocol covers mirroring/remoting and negotiates codecs; compliant video receivers implement at least VP8, while H.264 capability must be negotiated rather than assumed. | Designed for real-time transport, so potentially much lower latency than segmented HLS. Integration cost is highest: native sender/session, certificates, negotiation, congestion, feedback and packet lifetime. It creates an active sender only for the session and requires no separately installed receiver application when the built-in Cast implementation is compatible.             | **Not demonstrated.** A Chrome mirror working would not by itself prove that an independent Open Screen sender is accepted by this Freebox. Compatibility, H.264 negotiation, stability and achievable latency need a time-boxed hardware spike. |
| **C — Sunshine + Moonlight**       | Sunshine is a self-hosted low-latency host for Moonlight with AMD, Intel and Nvidia hardware encoding plus software fallback. Moonlight Android officially supports Sunshine and full-desktop streaming; setup includes installing the client and pairing it with the host.                              | Mature low-latency path, broad resolution/frame-rate support and hardware encoding. It requires Sunshine running during the session, several LAN ports, pairing and a Moonlight Android TV application. It is therefore operationally heavier and does not use the built-in Cast receiver.                                                                                                   | **Not demonstrated.** Moonlight availability, decoder behavior, input latency and Freebox performance have not been checked. Installation and pairing require separate owner authorization.                                                      |

Primary references:

- [Google Cast — Web Receiver streaming protocols](https://developers.google.com/cast/docs/media/streaming_protocols)
- [Google Cast — supported media](https://developers.google.com/cast/docs/media)
- [Google Cast — receiver selection](https://developers.google.com/cast/docs/overview)
- [Chromium Open Screen — Cast implementation](https://github.com/chromium/openscreen/blob/main/cast/README.md)
- [Open Screen — Cast Streaming protocol](https://chromium.googlesource.com/openscreen/+/refs/heads/main/cast/protocol/streaming_session_protocol.md)
- [Sunshine documentation](https://docs.lizardbyte.dev/projects/sunshine/latest/)
- [Moonlight Android](https://github.com/moonlight-stream/moonlight-android/blob/master/README.md)

### Provisional decision

Keep all three behind one `StreamPublisher`/`StreamReceiver` boundary. Test A first because it
reuses the existing Cast media path and needs no TV installation. Time-box B because it best fits
interactive mirroring without a TV application but has the greatest protocol risk. C remains the
mature fallback if installing and pairing Moonlight is acceptable. No final receiver is selected
until Freebox evidence exists.

No numeric latency or success threshold is asserted by this spike. Latency, fluidity, host load,
resolution and ten-minute stability belong to the owner-authorized RD1–RD8 hardware protocol.

## Architecture

```text
explicit UI action (future)
          |
          v
ScreenCastService (future composition root)
  |-- ScreenCapture         selected X11 output / future virtual output
  |-- D0 ScreenEncoder      capture or synthetic source -> encoded stream
  `-- StreamingSessionService (this spike)
        |-- StreamPublisher encoded stream -> receiver-facing publication
        `-- StreamReceiver  publication -> receiver session
```

Responsibilities:

- **ScreenCapture** owns selection and lifetime of a real display capture. The port exists, but no
  implementation is present and this prototype never calls it.
- **ScreenEncoder** owns one encoder and returns an opaque `EncodedStream`. Its future adapter will
  compose the D0 implementation; D1 contains no FFmpeg command and no second encoding engine.
- **StreamPublisher** owns only publication lifetime: HLS listener and segments, Cast Streaming
  sender, or an external-host session.
- **StreamReceiver** starts/stops receiver playback. Starting is not physical confirmation that a
  picture appeared.
- **StreamingSessionService** owns ordering and cleanup across encoder, publisher and receiver.
  The later product-level `ScreenCastService` will add capture selection, confirmation and UI/bridge
  policy without moving transport logic into Rust or TypeScript.

All ports are small Python `Protocol`s. Model values reject booleans, non-finite durations,
out-of-range or odd H.264 dimensions, unsafe URI schemes, URI credentials and fragments. Source
specifications contain no command-line arguments.

## Prototype behavior

`control_tv.screen_streaming` provides typed contracts and an orchestration state machine only:

1. construction has no side effect;
2. `start()` must be called explicitly;
3. encoder, publisher and receiver are acquired in that order;
4. cleanup always attempts receiver, publisher and encoder in reverse order;
5. a resource whose stop fails **or is interrupted** remains owned (`unreleased`) and is retried
   by `stop()`/`close()`; a released resource is never stopped twice;
6. a new session is refused until earlier resources are released;
7. an interruption (`KeyboardInterrupt`, `SystemExit`) during start or cleanup does not abort
   cleanup: every remaining step is attempted, the state is recorded (`failed`, never left
   `stopping`), then the interruption is re-raised;
8. `start()` and `stop()` refuse to run beside another lifecycle call;
9. `close()` is a definitive shutdown: it is recorded first (no later `start()` is accepted),
   then waits, bounded by `close_timeout` (90 s by default), for an in-progress call; a start
   that sees the recorded close between its steps or before reporting running stops what it
   acquired and fails as cancelled. A close that could not wait is deferred, never lost
   (`last_error` says it is pending): the call holding the lock (a `start()`, a `stop()` or a
   first `close()`) carries it out before releasing the lock, whatever its own outcome, with
   one cleanup attempt of what is still owned; a close whose wait expires just as that call
   ends carries itself out. A persistent failure keeps the resource in `unreleased` (one
   attempt per deferred close, no loop, nothing released twice); an interruption of that
   attempt is re-raised after the lock is released and is never swallowed by an ordinary
   error.

The tests use in-memory fakes. Their `http://127.0.0.1/...` value is data only; no socket is
created. There is no concrete publisher, receiver, capture or encoder adapter in this PR.

## Security boundary

- no startup hook or automatic launch;
- no subprocess and no shell command;
- no X11 access or display mutation;
- no socket, listener, LAN exposure or media request;
- no Cast, Android TV, ADB or Freebox call;
- no package or system dependency;
- bounded dimensions, frame rate, receiver timeout and requested session lifetime;
- endpoint credentials are forbidden in serialized stream URIs.

A future HLS adapter must bind only to the selected LAN interface, use an unguessable per-session
path, implement the receiver's CORS requirements, enforce the lifetime, and close the listener and
delete segments on every exit path. Those properties are requirements, not claims about this spike.

## Dependency on D0 and remaining work

PR #81 (D0) is merged. Its pieces and the D1 ports fit as follows; the D1 encoder adapter is a
translation layer, not a second engine:

| D1 (`screen_streaming`)                                               | D0 (`screencast`) to reuse                                                                                                                                     | Gap                                                                                                                                       |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `SyntheticVideoSource` / `SyntheticPattern` (`testsrc2`, `smptebars`) | `SyntheticSource`, `source_arguments`                                                                                                                          | none: every D1 pattern is a D0 pattern (contract test)                                                                                    |
| `EncodingProfile` (16–4096 even, 1–60 fps)                            | `VideoProfile` (same bounds, plus a duration)                                                                                                                  | D1 has no duration: a live session is bounded by `PublicationConfig.max_session_seconds` (≤ 3600 s), D0 by `MAX_DURATION_SECONDS` (120 s) |
| `ScreenEncoder.start/stop`                                            | `select_encoder`, `encoder_arguments` (VA-API CQP, explicit x264 fallback), `SubprocessRunner` / `stop_process` (whole process group, unstoppable groups kept) | D0's `encode_command` writes a bounded MP4; a live output (HLS segments) needs a new muxer argument builder beside it                     |
| `ScreenEncoder.stop` failing                                          | D0's kept, unreaped group (`unstoppable_pids`)                                                                                                                 | maps to D1's `unreleased` encoder: retried by `stop()`/`close()`                                                                          |

Remaining:

1. write the D1 `ScreenEncoder` adapter on top of those D0 pieces (no copy of FFmpeg construction
   or process management), with a live muxer builder and the D1 session bound;
2. decide whether D0 must evolve from a bounded file encode to a supervised live H.264 output;
3. implement one publisher/receiver pair only after the owner chooses an authorized hardware spike;
4. add bridge/UI integration only after the chosen path has receiver evidence;
5. run RD1–RD8 with explicit authorization and keep receiver-started, receiver-confirmed and
   physically observed results distinct.

Estimated remaining D1 effort after D0 integration: HLS MVP **12–20 hours** if the Freebox accepts
the live playlist; Cast Streaming sender **20–40 hours** for a time-boxed native prototype and
hardening; Sunshine/Moonlight integration **8–16 hours** excluding installation approval and
hardware acceptance. These alternatives are not additive: implementation follows the selected
path.
