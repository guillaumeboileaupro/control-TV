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

