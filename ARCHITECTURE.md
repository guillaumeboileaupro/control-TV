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

What exists in the code today (the MCP adapter and the tray do not exist yet; Android is a spike, see "Android" below):

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
- **Sent versus confirmed:** `ok: true` on a command means only that it was sent. `confirmation` is `confirmed` (a status read from the TV shows the requested state), `unconfirmed` (sent, but not shown in time; `detail` says what the TV reported) or `not_checked` (verification disabled). Play, pause, stop and seek confirmation is bound to the media observed before the command; all reads share one confirmation deadline. A command that could not be sent is an error, and a delivery timeout is ambiguous. Nothing is ever resent automatically by the service, the bridge, Rust or the UI. `confirmed` means the receiver *reported* the expected state for the same identified media; it is not an independent proof of what the screen shows (a real receiver has reported a seek its picture did not perform), so hardware validation records the physical effect separately.
- **Unknown is not zero:** a field the TV did not report is `null`, never a default value, and the UI shows it as not reported.
- **Python runtime:** a debug build starts `python -m control_tv.bridge` from the repository `.venv`. A release build starts only the frozen bridge (PyInstaller `--onedir`, its own embedded Python runtime) bundled as the `python-bridge/` Tauri resource, with no fallback to another Python and with `PYTHONPATH`/`PYTHONHOME` withheld; a missing bundle leaves the backend unavailable (`resolve_bridge_program()` in `src-tauri/src/lib.rs`, `packaging/build_bridge.py`). Both run the same `control_tv.bridge` code. Releases embed exactly the CPython in `packaging/release-python-version` (3.12.15), built with uv 0.12.23. The Linux release `.deb` has been installed for real on Ubuntu 22.04: the installed application was launched with the checkout, its `.venv`, uv's Python store and the build CPython hidden and the system `python3` unusable. It was given deliberately poisoned `PYTHONPATH`/`PYTHONHOME` values, which the runtime removes before spawning the bundled bridge: the bridge's environment contained neither variable. The bundled bridge answered the application's `ping` (0.1.0) before the package was purged. The `linux-release` CI job installs the package, pings the installed bridge directly from outside the checkout and purges it; it does not launch the GUI. No Cast device was used for this validation. Windows is not built, and public distribution still waits for open license items (`DEVELOPMENT_PLAN.md`, Phase 7; `docs/PACKAGING_LICENSES.md`).

## Android (candidate architecture / spike)

Owner decision of 2026-10-04, **a candidate architecture under a spike** until it is validated on a real phone (`DEVELOPMENT_PLAN.md`, Phase 7b); it becomes the validated Android MVP architecture only then. Partial real-device evidence (2026-10-06, one physical arm64 phone, PR #30 not merged): the APK installed and launched, the embedded CPython started, the multicast lock was held and the app discovered receivers on the real local network, with no Cast control command sent. The explicit `control_tv` import and ping evidence is still pending, so the architecture stays a candidate.

```text
ui/ (same TypeScript UI, Android WebView)
   | invoke (same Tauri commands)
   v
src-tauri/ (Rust, same call_bridge: claim, timeouts, no replay)
   | BridgeChannel::InProcess -> run_mobile_plugin("handle", line)
   v
ControlBridgePlugin (Kotlin, src-tauri/gen/android): one worker thread, multicast lock
   | Chaquopy: control_tv.embedded.handle(line)
   v
control_tv.bridge.handle_line -> ControlService -> PyChromecastTransport (CPython 3.12 in the app)
```

- **One engine:** Android runs the same `control_tv` package and the same `handle_line` as the desktop bridge process; only the transport of the request line differs (an in-process call instead of stdio), so no Cast, validation or confirmation logic is duplicated in Kotlin or Rust.
- **Runtime:** CPython 3.12 from Chaquopy 17.0.0, `arm64-v8a` only, `minSdk` 24. The embedded packages are the control layer's locked runtime dependencies as pure-Python wheels, hash-checked against `uv.lock` and installed offline (`packaging/android_python.py`); protobuf uses its pure-Python implementation and zeroconf is built without its optional Cython extensions. The APK carries its own Python: it does not use the checkout, a `.venv` or a host Python.
- **Threads:** Tauri delivers plugin commands on the Android main thread; the plugin runs Python on its own single worker thread, so the shared layer stays single-flight as on the desktop and the window never blocks on a discovery or a command.
- **Discovery:** the app holds Wi-Fi's multicast lock while in the foreground (mDNS replies are otherwise often filtered) and releases it in the background.
- **Startup diagnostic:** when the plugin loads, the worker runs `control_tv.embedded.startup_diagnostic()` once: a `ping` through the same `handle` path, logged under the logcat tag `control-tv` with versions only (no device data, no network, no Cast message, no retry).
- **Limitation:** Python lives in the app process, so the desktop recovery (kill and relaunch the bridge process, PR #29) has no equivalent; a request stuck in Python leaves later requests `bridge_busy` until the app restarts.

## Planned native integrations

- **Desktop tray (Phase 4b):** a second view over the same Tauri -> bridge -> `ControlService` chain, with no Cast logic in Rust and the same confirmation semantics as the window; structured so a Windows equivalent can follow.
- **Android home-screen widget (Phase 7b):** after the MCP adapter, over the same embedded control layer (see "Android").
- **MCP adapter (Phase 6, next after the Android APK MVP):** planned as a separate local stdio process (`python -m control_tv.mcp_server`) holding its own `ControlService` over the same `PyChromecastTransport`, and calling the same `control_tv.bridge.dispatch` as the window's bridge, so validation, confirmation and the no-retry rule stay in one place; it does not depend on Tauri, Rust or the UI. UI-only protections such as the volume raise limit do not apply to it and must be decided for MCP separately.

## Chromecast capabilities

Develop incrementally around LAN discovery, device identity, connection/status/recovery, media load/play, pause/resume, stop, seek where supported, volume/mute and receiver/media state. A command successfully sent is not proof that the requested TV state was reached; APIs, tests and real-device validation must preserve sent-versus-confirmed state.

## Media and service resolution

Media/service resolution is a separate concern from low-level Cast transport where practical. Service-specific metadata or URL resolution may use the technology and libraries best suited to the actual service requirement. Do not impose a language ban on this layer.

## MCP and AI independence

MCP exposes a small typed tool surface over the same product control capabilities used by the manual application. The standalone application does not call the OpenAI API and does not require an OpenAI API key or OpenAI billing. ChatGPT or another compatible MCP client is external to the standalone application.

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

