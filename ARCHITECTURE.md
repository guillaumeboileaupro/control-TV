# Architecture

## Product shape

control-TV is a lightweight standalone TV controller targeting Android, Windows and Debian/Ubuntu. Manual control must work without ChatGPT, MCP, an OpenAI API key or a cloud dependency. ChatGPT control is provided externally through MCP.

## Architecture baseline

Python is part of the architecture and must not be removed from it. The Chromecast integration can use the mature Python Chromecast ecosystem, including `pychromecast`, for discovery, connection and Cast control. Rust remains available for native/performance-sensitive components where it provides a concrete benefit; it is not required to replace Python Chromecast integration.

Tauri 2 provides the cross-platform application/native packaging shell. The UI may use HTML/CSS and JavaScript or TypeScript as appropriate. TypeScript is not prohibited. React, npm, FastAPI, Electron or other technologies are not globally prohibited either; they are introduced only when an actual implementation requirement justifies them. Go was explicitly rejected for this project and is not introduced unless the owner revisits that choice.

No agent may convert an exploratory technology comparison or its own preference into a project-wide prohibition.

## Functional boundaries

```text
Manual UI
   |
   v
Application / Tauri boundary
   |
   +------> shared control/domain interface <------ MCP adapter
                         |
                         +------> Python Chromecast integration (`pychromecast` / required modules)
                         |
                         +------> Rust/native components where justified
                         |
                         v
                 Chromecast / Google TV
```

GUI and MCP must use the same authoritative control/domain behavior. Do not create two divergent control engines. Technology boundaries must remain explicit and testable.

## Current implementation

What exists in the code today (the tray does not exist yet; Android: see "Android"; MCP: see "MCP server"):

```text
ui/ (vanilla TypeScript + Vite)
   |  Tauri command (invoke)
   v
src-tauri/ (Rust): async commands, blocking call on a worker thread, bounded by a timeout, no retry
   |  line-delimited JSON over stdin/stdout of a long-lived child process
   v
control_tv.bridge (Python): validates parameters, forwards to the same-named method
   |
   v
ControlService (TvControl): input validation, sent-versus-confirmed verification
   |
   v
CastTransport -> PyChromecastTransport (the only module that imports pychromecast)
```

- **Bridge protocol:** one JSON request per line (`id`, `method`, `params`), one response per line (`ok` with `result`, or `error` with `code` and `message`). Exposed methods: `ping`, `discover_devices`, `get_status`, `play`, `pause`, `stop`, `seek`, `set_volume`, `set_muted`. A device is always addressed by its stable id, never by its display name. An unexpected exception becomes an `internal_error` response instead of stopping the process. The module docstring of `src/control_tv/bridge.py` is the reference.
- **Sent versus confirmed:** `ok: true` on a command means only that it was sent. `confirmation` is `confirmed` (a status read from the TV shows the requested state), `unconfirmed` (sent, but not shown in time; `detail` says what the TV reported) or `not_checked` (verification disabled). Play, pause, stop and seek confirmation is bound to the media observed before the command; all reads share one confirmation deadline. A failed command is an error; its delivery is certain only where the control layer proves it (a refused argument, an unknown device, an unsupported operation: not sent). A delivery timeout, `device_unavailable` (a socket write can fail after the command started to leave) and `command_rejected` (it also covers a refusal by the Cast library before anything left) are ambiguous, and the window and MCP word them as "may or may not have reached the TV". Nothing is ever resent automatically by the service, the bridge, Rust or the UI. `confirmed` means the receiver *reported* the expected state for the same identified media; it is not an independent proof of what the screen shows (a real receiver has reported a seek its picture did not perform), so hardware validation records the physical effect separately.
- **Unknown is not zero:** a field the TV did not report is `null`, never a default value, and the UI shows it as not reported.
- **Python runtime:** a debug build starts `python -m control_tv.bridge` from the repository `.venv`. A release build starts only the frozen bridge (PyInstaller `--onedir`, its own embedded Python runtime) bundled as the `python-bridge/` Tauri resource, with no fallback to another Python and with `PYTHONPATH`/`PYTHONHOME` withheld; a missing bundle leaves the backend unavailable (`resolve_bridge_program()` in `src-tauri/src/lib.rs`, `packaging/build_bridge.py`). Both run the same `control_tv.bridge` code. Releases embed exactly the CPython in `packaging/release-python-version` (3.12.15), built with uv 0.12.23. The Linux release `.deb` has been installed for real on Ubuntu 22.04: the installed application was launched with the checkout, its `.venv`, uv's Python store and the build CPython hidden and the system `python3` unusable. It was given deliberately poisoned `PYTHONPATH`/`PYTHONHOME` values, which the runtime removes before spawning the bundled bridge: the bridge's environment contained neither variable. The bundled bridge answered the application's `ping` (0.1.0) before the package was purged. The `linux-release` CI job installs the package, pings the installed bridge directly from outside the checkout and purges it; it does not launch the GUI. No Cast device was used for this validation. Windows is not built, and public distribution still waits for open license items (`DEVELOPMENT_PLAN.md`, Phase 7; `docs/PACKAGING_LICENSES.md`).

## Android (spike validated on a physical phone)

Owner decision of 2026-10-04, explored as a spike (`DEVELOPMENT_PLAN.md`, Phase 7b). On one physical arm64 phone (2026-10-06; PR #30, merged into `main` `3bcb516`) the spike passed all its gates: the APK installed and launched, the embedded CPython 3.12 started, `control_tv` imported and answered its ping (0.1.0), the multicast lock was held and the app discovered receivers on the real local network, with no Cast control command sent. It is the architecture of the Android APK MVP, complete at its defined level (PR #30). Further Android hardware validation is separate and open: receiver status, the control commands, display cutout and rotation, broader responsive and touch use.

```text
ui/ (same TypeScript UI, Android WebView)
   | invoke (same Tauri commands)
   v
src-tauri/ (Rust, same call_bridge: claim, timeouts, no replay)
   | BridgeChannel::InProcess -> run_mobile_plugin("handle", line)
   v
ControlBridgePlugin (Kotlin, src-tauri/gen/android): multicast lock     home-screen widget tap
   |                                                                   | WidgetTapReceiver -> one WorkManager job
   +---------------> EmbeddedBridge: one relay, one worker thread <----+ (WidgetActionWorker, WidgetActionRunner)
                        | Chaquopy: control_tv.embedded.handle(line)
                        v
control_tv.bridge.handle_line -> ControlService -> PyChromecastTransport (CPython 3.12 in the app)
```

- **One engine:** Android runs the same `control_tv` package and the same `handle_line` as the desktop bridge process; only the transport of the request line differs (an in-process call instead of stdio), so no Cast, validation or confirmation logic is duplicated in Kotlin or Rust.
- **Runtime:** CPython 3.12 from Chaquopy 17.0.0, `arm64-v8a` only, `minSdk` 24. The embedded packages are the control layer's locked runtime dependencies as pure-Python wheels, hash-checked against `uv.lock` and installed offline (`packaging/android_python.py`); protobuf uses its pure-Python implementation and zeroconf is built without its optional Cython extensions. The two wheels built locally (zeroconf, control-tv) are built offline with build backends locked in `uv.lock` (no build dependency is resolved at build time). The APK carries its own Python: it does not use the checkout, a `.venv` or a host Python.
- **Failure boundary:** a failure before `control_tv.embedded.handle` is entered (Python not starting, `control_tv.embedded` not importing) is `backend_unavailable`, certainly not sent; a failure while or after `handle` runs is the ambiguous `bridge_transport`; nothing is retried (`EmbeddedHandler`).
- **Threads:** Tauri delivers plugin commands on the Android main thread; Python runs on one worker thread for the whole process (`EmbeddedBridge`), shared by the window and the home-screen widget, so the shared layer stays single-flight as on the desktop and the window never blocks on a discovery or a command.
- **Home-screen widget (Phase 7b):** a tap reaches the same `control_tv.embedded.handle` request lines as the window, without Tauri or the window (Chaquopy starts Python from the application context). The exported widget provider only draws; taps go to a non-exported receiver that queues one unique WorkManager job (a tap while one runs is ignored), never retried. The job reads the status first and sends at most one command chosen from the observed state (Play/Pause the opposite of the reported playback, Mute the opposite of the reported mute state, Volume -/+ 10 points from the reported level, the window's raise limit); a tap id stored durably before the send keeps a re-run after a process death from sending twice, and a job starting more than 60 s after its tap sends nothing. The TV it controls is the one the window last used successfully (recorded from the window's own bridge traffic, never chosen by the widget); with none, or one no longer found, nothing is sent.
- **System bars:** the app draws edge to edge; `MainActivity` pads the content container holding the WebView by the window insets the system reports (system bars, display cutout) and consumes them, so the page needs no safe-area CSS and is never padded twice. The launcher icon is generated from `assets/logo.svg` (`packaging/android_icon.py`).
- **Discovery:** the app holds Wi-Fi's multicast lock while in the foreground (mDNS replies are otherwise often filtered) and releases it in the background.
- **Startup diagnostic:** when the plugin loads, the worker runs `control_tv.embedded.startup_diagnostic()` once: a `ping` through the same `handle` path, logged under the logcat tag `control-tv` with versions only (no device data, no network, no Cast message, no retry).
- **Limitation:** Python lives in the app process, so the desktop recovery (kill and relaunch the bridge process, PR #29) has no equivalent; a request stuck in Python leaves later requests `bridge_busy` until the app restarts.

## Planned native integrations

- **Desktop tray (Phase 4b):** a second view over the same Tauri -> bridge -> `ControlService` chain, with no Cast logic in Rust and the same confirmation semantics as the window; structured so a Windows equivalent can follow.

## Chromecast capabilities

Develop incrementally around LAN discovery, device identity, connection/status/recovery, media load/play, pause/resume, stop, seek where supported, volume/mute and receiver/media state. A command successfully sent is not proof that the requested TV state was reached; APIs, tests and real-device validation must preserve sent-versus-confirmed state.

## Media and service resolution

Media/service resolution is a separate concern from low-level Cast transport where practical. Service-specific metadata or URL resolution may use the technology and libraries best suited to the actual service requirement. Do not impose a language ban on this layer.

## MCP and AI independence

MCP exposes a small typed tool surface over the same product control capabilities used by the manual application. The standalone application does not call the OpenAI API and does not require an OpenAI API key or OpenAI billing. ChatGPT or another compatible MCP client is external to the standalone application.

## MCP server (Phase 6; automated tests, and read-only with a real MCP client)

```text
MCP client (Codex CLI, Claude Desktop, ...) launches the server as a subprocess
   |  MCP over stdio (JSON-RPC on stdin/stdout; logs on stderr)
   v
control_tv.mcp_server (Python, optional extra control-tv[mcp], official SDK mcp==1.30.0)
   |  one tool call at a time; privacy filtering; delivery wording
   v
control_tv.bridge.dispatch -> ControlService -> PyChromecastTransport (the window's own path)
```

- **One engine:** every tool is the same-named bridge method run through `bridge.dispatch`, so the MCP server has exactly the window's validation, confirmation, media-identity guards, shared deadline and error codes; the SDK's schema check is disabled so the shared layer stays the only validator. The server owns its own `ControlService` and discovery cache (a separate process from the window's bridge). It does not depend on Tauri, Rust, the Android plugin or the UI, and neither the bridge, the frozen desktop bridge nor the Android app contains it.
- **Transport:** local stdio only (spawned by the client; no port, no network listener). ChatGPT web cannot launch a local stdio server; reaching it from ChatGPT (OpenAI's outbound Secure MCP Tunnel or a public HTTPS endpoint) is out of scope for this foundation (owner decision) and would need its own security review. The tools take no transport-specific input, so another binding can be added without changing them.
- **Outcomes:** a sent command keeps `confirmation` (`confirmed` / `unconfirmed` / `not_checked`); an error carries its code and a `delivery` that is never stronger than the shared layer proves: `not_sent` only for refusals before any send, otherwise `unknown` (`timeout`, `device_unavailable`, `command_rejected`, `internal_error`), worded "may or may not have reached the TV; do not resend it automatically; call get_status". Nothing is retried. Calls are serialized; a cancelled call that already started completes, so its command may have been sent.
- **Privacy:** no device address, no media `contentId`, quoted values hidden in command details, fixed error messages per code (except `invalid_argument`), no exception text; device names, ids and media titles are visible to the client and its model. UI-only protections such as the volume raise limit do not apply to MCP (owner decision: `set_volume` is an absolute level 0-1), and `contentId` stays hidden (owner decision).

## Cross-platform targets

- Android: APK for personal sideloading (no Google Play publication).
- Windows: EXE or appropriate Windows installer artifact.
- Debian/Ubuntu: DEB (the release `.deb` with the frozen bridge is built, installed and launched on Ubuntu 22.04; not yet released).

A package merely building is not sufficient to claim platform support. Installation and launch must be validated on the target platform.

## CRITICAL - temporary/build cleanup

Disk cleanup is a blocking engineering requirement, with the same completion priority as tests and compilation. It applies after successful, failed and interrupted builds/tests/package attempts.

Before build-heavy work, inspect free disk and relevant project-owned generated directories. After each cycle, identify newly generated output and remove obsolete project-owned temporary/build artifacts immediately. Inspect applicable Rust/Tauri `target/`, Tauri generated output, Android `build/`, project-local `.gradle/`, Python caches/temporary packaging output, logs, test output, staging/scratch directories and extracted archives.

Retain only explicitly useful artifacts. Release deliverables should be copied to a controlled `dist/` or release location, after which disposable staging/build trees are cleaned. Measure and report the remaining project-owned generated footprint at the end of each iteration.

Never blindly delete global/shared caches or unrelated data: `$CARGO_HOME`, Cargo registry/git caches, `~/.gradle`, Android SDK/NDK, global Python environments/caches, the user's home directory or system `/tmp`. Recursive deletion targets must first be verified as project-owned or explicitly known project-local temporary paths. Global/shared cache cleanup requires explicit owner instruction.

Once build tooling exists, the project must provide documented `clean` and `dist-clean` commands. `clean` removes normal project-owned generated output. `dist-clean` removes all reproducible project-owned generated output while still preserving shared/global caches. If cleanup cannot be completed, the iteration is not complete and the exact remaining path, size and reason must be recorded.

