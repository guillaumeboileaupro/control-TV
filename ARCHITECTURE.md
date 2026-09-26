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

What exists in the code today (the MCP adapter, the tray and Android do not exist yet):

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
- **Sent versus confirmed:** `ok: true` on a command means only that it was sent. `confirmation` is `confirmed` (a status read from the TV shows the requested state), `unconfirmed` (sent, but not shown in time; `detail` says what the TV reported) or `not_checked` (verification disabled). Play, pause, stop and seek confirmation is bound to the media observed before the command; all reads share one confirmation deadline. A command that could not be sent is an error, and a delivery timeout is ambiguous. Nothing is ever resent automatically by the service, the bridge, Rust or the UI.
- **Unknown is not zero:** a field the TV did not report is `null`, never a default value, and the UI shows it as not reported.
- **Python runtime:** during development the Rust shell starts the repository `.venv` (`resolve_python()` in `src-tauri/src/lib.rs`). A distributable runtime for packages is not designed yet (`DEVELOPMENT_PLAN.md`, Phase 7).

## Planned native integrations

- **Desktop tray (Phase 4b):** a second view over the same Tauri -> bridge -> `ControlService` chain, with no Cast logic in Rust and the same confirmation semantics as the window; structured so a Windows equivalent can follow.
- **Android and its home-screen widget (Phase 7b):** requires an owner architecture decision first on how the Python runtime and the shared control core run on Android, or how Android otherwise reuses that core, so that one authoritative control engine remains.
- **MCP adapter (Phase 6):** calls `TvControl` directly; UI-only protections such as the volume raise limit do not apply to it and must be decided for MCP separately.

## Chromecast capabilities

Develop incrementally around LAN discovery, device identity, connection/status/recovery, media load/play, pause/resume, stop, seek where supported, volume/mute and receiver/media state. A command successfully sent is not proof that the requested TV state was reached; APIs, tests and real-device validation must preserve sent-versus-confirmed state.

## Media and service resolution

Media/service resolution is a separate concern from low-level Cast transport where practical. Service-specific metadata or URL resolution may use the technology and libraries best suited to the actual service requirement. Do not impose a language ban on this layer.

## MCP and AI independence

MCP exposes a small typed tool surface over the same product control capabilities used by the manual application. The standalone application does not call the OpenAI API and does not require an OpenAI API key or OpenAI billing. ChatGPT or another compatible MCP client is external to the standalone application.

## Cross-platform targets

- Android: APK.
- Windows: EXE or appropriate Windows installer artifact.
- Debian/Ubuntu: DEB.

A package merely building is not sufficient to claim platform support. Installation and launch must be validated on the target platform.

## CRITICAL - temporary/build cleanup

Disk cleanup is a blocking engineering requirement, with the same completion priority as tests and compilation. It applies after successful, failed and interrupted builds/tests/package attempts.

Before build-heavy work, inspect free disk and relevant project-owned generated directories. After each cycle, identify newly generated output and remove obsolete project-owned temporary/build artifacts immediately. Inspect applicable Rust/Tauri `target/`, Tauri generated output, Android `build/`, project-local `.gradle/`, Python caches/temporary packaging output, logs, test output, staging/scratch directories and extracted archives.

Retain only explicitly useful artifacts. Release deliverables should be copied to a controlled `dist/` or release location, after which disposable staging/build trees are cleaned. Measure and report the remaining project-owned generated footprint at the end of each iteration.

Never blindly delete global/shared caches or unrelated data: `$CARGO_HOME`, Cargo registry/git caches, `~/.gradle`, Android SDK/NDK, global Python environments/caches, the user's home directory or system `/tmp`. Recursive deletion targets must first be verified as project-owned or explicitly known project-local temporary paths. Global/shared cache cleanup requires explicit owner instruction.

Once build tooling exists, the project must provide documented `clean` and `dist-clean` commands. `clean` removes normal project-owned generated output. `dist-clean` removes all reproducible project-owned generated output while still preserving shared/global caches. If cleanup cannot be completed, the iteration is not complete and the exact remaining path, size and reason must be recorded.

