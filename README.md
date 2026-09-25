# control-TV

Lightweight cross-platform application for controlling Chromecast / Google Cast devices from a standalone interface, with an optional MCP adapter built around the same Rust core.

## Goals

- Standalone manual TV control without requiring ChatGPT.
- Shared Rust core for discovery, device state and Cast commands.
- Thin Tauri 2 shell for desktop and Android packaging.
- Thin frontend using HTML, CSS and native JavaScript.
- Optional MCP adapter that calls the same Rust core instead of duplicating device logic.
- Target packages: Android APK, Windows executable/package and Debian package.
- Strict control of build artifacts, temporary files and disk usage.

## Architecture

```text
Standalone UI -> Tauri adapter -> Rust Cast core -> Chromecast / Google TV
MCP client    -> MCP adapter   -> Rust Cast core -> Chromecast / Google TV
```

Tauri and MCP are adapters. Device discovery, protocol handling, validation and state belong to the Rust core.

## Development principles

- Keep the dependency set small and justified.
- Keep generated files and build outputs out of Git.
- Build only the target currently being developed or tested.
- Do not duplicate Cast logic between UI and MCP.
- Do not claim hardware support until it has been tested on a real compatible device.
- Treat media/content resolution as separate from Cast transport.

## Status

Architecture definition phase. Cast dependencies and protocol implementation must be validated before the first device-control implementation is selected.
