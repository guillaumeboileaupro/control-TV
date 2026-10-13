# control-TV

Control the TV by voice from the ChatGPT app, and with a standalone graphical remote, both over one control layer.

## Goals

- **By voice, in the existing ChatGPT app:** you speak in ChatGPT's voice mode ("lance-moi ce documentaire sur la TV") and ChatGPT carries the request out on the TV through control-TV's tools (MCP), including starting the requested content on the TV. Whether ChatGPT's voice mode can call these tools on your ChatGPT app is not yet established and is to be verified first.
- **control-TV, a graphical TV remote** (Linux desktop and Android): discover and select TVs, show the TV's applications and launch them on the TV, power on and off, the TV's audible volume and mute, playback and navigation, each where the TV supports it. It has no microphone, voice assistant, search interface or video player.
- Standalone manual TV control that works without ChatGPT, MCP, an OpenAI API key or any cloud dependency.
- One authoritative control/domain layer shared by the manual application and the MCP adapter.
- Python for the Chromecast integration (`pychromecast` where it matches the required Cast capabilities); Rust only for native or performance-sensitive components that justify it.
- Tauri 2 as the cross-platform application and packaging shell, with an HTML/CSS UI written in JavaScript or TypeScript as the implementation requires.
- Target packages: Android APK (personal sideloading only, no Google Play publication), and Debian/Ubuntu package (Windows was abandoned on 2026-10-12).
- Strict control of build artifacts, temporary files and disk usage.

## Architecture

```text
Manual UI  -> Tauri boundary -+
                              +-> shared control/domain layer -> Chromecast / Google TV
MCP client -> MCP adapter ----+   (MCP adapter: local stdio server, python -m control_tv.mcp_server)
```

Today the manual UI is a vanilla TypeScript page in a Tauri 2 window. Its Rust side only forwards requests, on a worker thread with a time bound, to a long-lived Python process (`python -m control_tv.bridge`) that speaks line-delimited JSON over stdin/stdout and calls `ControlService`, which drives the TV through `PyChromecastTransport`. No Cast logic lives in Rust or TypeScript.

The UI and the MCP adapter are thin. Device discovery, validation and state belong to the shared control layer, which reaches the TV through a focused Python Chromecast adapter. A command that was sent is not proof that the TV reached the requested state, and a state the receiver reports is not proof of what you see or hear, so sent, receiver-confirmed and physically observed are kept distinct; the Cast receiver's volume and mute are not always the TV's sound. See [ARCHITECTURE.md](ARCHITECTURE.md) for the decisions, [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) for the phased roadmap and [docs/REQUIREMENTS_TRACEABILITY.md](docs/REQUIREMENTS_TRACEABILITY.md) for each requirement's status.

Priority (owner, issue #37, 2026-10-08): the standalone Ubuntu and Android remotes, usable in parallel on the same TV; MCP, ChatGPT Voice and Windows were abandoned on 2026-10-12.

Today: Cast playback control and the receiver's volume and mute are implemented and automation-validated in the window and the local MCP server. From the window, some commands were sent to a real TV (see the plan); through MCP, only read-only calls were made with a real client, and no MCP control command has been validated on hardware. When a TV's Cast volume is fixed, the window says so, says that Mute may not silence it, and names the TV's own remote-control channel (Android TV Remote) when discovery finds it; control-TV does not use that channel yet. ChatGPT is not connected yet, and the TV's applications, power, audible sound and navigation are not implemented (they depend on what each TV exposes, to be checked on real hardware).

## Development

Prerequisite: Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). `uv` resolves and pins every dependency (including its own managed Python) into `uv.lock`, so `setup` reproduces the exact same `.venv` on Linux, Windows and macOS. Only Linux has been exercised so far.

```bash
python3 scripts/dev.py setup        # create .venv from uv.lock, with the optional extras (uv sync --locked --extra mcp --extra tvremote)
python3 scripts/dev.py lock         # regenerate uv.lock after changing a dependency
python3 scripts/dev.py check        # ruff, ruff format --check, mypy --strict, pytest, dependency check
python3 scripts/dev.py type-rules   # no `any` / `unknown` in the window's TypeScript (also part of `check`); mypy refuses explicit `Any` in Python
python3 scripts/dev.py coverage     # pytest coverage for the shared control package (minimum enforced)
python3 scripts/dev.py depcheck     # verify installed dependency consistency
python3 scripts/dev.py lint         # lint and format check only
python3 scripts/dev.py typecheck    # mypy only
python3 scripts/dev.py test         # pytest only
```

`setup` fails loudly if `uv.lock` does not match `pyproject.toml`, instead of silently resolving new versions; run `lock` deliberately whenever a dependency changes, review the resulting `uv.lock` diff, then commit both files together. Every other command only needs the `.venv` that `setup` created, so run `setup` first.

### Generated output and disk usage

Build output and caches are disposable and excluded from Git; `uv.lock` and `.python-version` are the exception, since they are what makes `setup` reproducible and are committed like any other source file. Cleanup is limited to a fixed allowlist of project-owned paths; it never touches shared caches (Cargo, Gradle, Android SDK/NDK, pip, uv) or anything outside the repository.

```bash
python3 scripts/dev.py disk-usage             # free disk and size of each generated path
python3 scripts/dev.py clean [--dry-run]      # caches, bytecode, packaging metadata, Rust/Tauri/Android build output, scratch dirs
python3 scripts/dev.py dist-clean [--dry-run] # clean, plus .venv and dist/
```

`clean` keeps `.venv`, `ui/node_modules` and `dist/`. `dist-clean` removes everything reproducible, so `setup` (and `npm install` in `ui/`) must be run again afterwards. Run `disk-usage` before and after build-heavy work.

### Tauri application shell

Prerequisites: a Rust toolchain ([rustup](https://rustup.rs/)) and Node.js 20+, plus [Tauri 2's Linux dependencies](https://v2.tauri.app/start/prerequisites/) if building on Linux. Run `python3 scripts/dev.py setup` first: `cargo test` spawns the real Python bridge process to verify the Rust/Python boundary, not just that each side compiles.

```bash
npm install --prefix ui                       # install frontend dependencies (once)
python3 scripts/dev.py rust-check             # cargo fmt --check, clippy -D warnings, cargo test
python3 scripts/dev.py ui-check               # tsc --noEmit, prettier --check, UI model tests (node --test)
npx --prefix ui tauri dev                     # run the app (from the repository root)
npx --prefix ui tauri build --debug --no-bundle     # debug binary only: src-tauri/target/debug/control-tv
npx --prefix ui tauri build --debug --bundles deb   # debug .deb: still runs the bridge from this checkout's .venv
```

The Tauri CLI comes from `ui/node_modules` (`npx --prefix ui tauri ...`); the separate `cargo tauri` subcommand is not required. `tauri dev` starts the Vite dev server on port 1420 (`src-tauri/tauri.conf.json`), so only one dev instance can run at a time on a machine. A build leaves several GiB in `src-tauri/target`: run `python3 scripts/dev.py disk-usage` and `clean` afterwards.

A debug build starts the bridge as `python -m control_tv.bridge` from this checkout's `.venv`, so it only runs on the machine that built it. A release build starts only the frozen bridge bundled as the `python-bridge/` resource, which carries its own Python runtime; it never falls back to a `.venv`, `python`/`python3`, `PATH` or `PYTHONPATH`, and a missing bundle shows the backend as unavailable:

```bash
python3 scripts/dev.py bridge-build   # freeze the bridge (PyInstaller --onedir) into dist/python-bridge/
python3 scripts/dev.py bridge-smoke   # informative: ping it outside the checkout (release-deb runs the strict, isolated smoke)
python3 scripts/dev.py release-deb    # both, then the release .deb, its notices, inventory and SHA256SUMS in dist/
npx --prefix ui tauri build --config src-tauri/tauri.release.conf.json --no-bundle   # release binary + python-bridge/ only
```

`bridge-build` rebuilds `.venv` on the exact CPython release bridges embed (`packaging/release-python-version`, 3.12.15, which needs a uv that knows it: 0.12.23 in CI) with the locked `packaging` dependency group (PyInstaller) and keeps all PyInstaller state under `packaging/.pyinstaller/` (removed by `clean`); `setup` removes the group again. It writes the bridge's third-party license notices and refuses a frozen bridge that still names a build-machine path. `release-deb` runs the strict smoke (it fails unless the checkout and the build Python are verifiably hidden), builds the Rust binary with `--remap-path-prefix` so it does not name the build machine's directories either, checks the package content and modes, and writes `dist/python-bridge.inventory.tsv` (SHA-256, size and mode of every bridge file) and `dist/SHA256SUMS`. Only Linux (Debian/Ubuntu) has been built, installed and launched so far: the release `.deb` was installed and launched on Ubuntu 22.04 with the checkout, the `.venv` and the system Python unavailable, but public distribution is blocked by open license items (see `docs/PACKAGING_LICENSES.md` and `DEVELOPMENT_PLAN.md`, Phase 7).

Android (`DEVELOPMENT_PLAN.md` Phase 7b; needs the Android SDK, NDK 27, JDK 17 and the `aarch64-linux-android` Rust target, with `ANDROID_HOME`/`NDK_HOME` set):

```bash
python3 scripts/dev.py android-python   # wheels the app embeds (pure, hash-checked against uv.lock; native ones pinned from Chaquopy), in dist/android-python/
python3 scripts/dev.py android-apk      # both, then a debug arm64 APK: src-tauri/gen/android/app/build/outputs/apk/universal/debug/
python3 packaging/android_icon.py       # after a logo change: regenerate the launcher icon from assets/logo.svg (needs Inkscape)
```

The APK runs the same `control_tv` package in CPython embedded by Chaquopy (arm64-v8a phones, Android 7.0 / API 24 and later). It is a debug build signed with the debug key, for personal sideloading (`adb install`); there is no Google Play publication objective.

Home-screen widget: long-press the home screen, choose Widgets, then control-TV (4 x 2 cells, resizable). Tap the TV name on the widget ("Tap here to choose a TV" at first) to pick or change its TV from a short list, without opening the app; choosing a TV in the app also sets it. The logo opens the app. Each tap reads the TV's state, then sends at most one command (Play/Pause, Stop, Mute, Volume -/+ by 10 points); ↻ only reads the state. While the app itself is talking to the TV, a tap is turned down ("control-TV was busy"), never queued. It never retries a command: when it cannot tell whether a command arrived, it says so, its play/pause and mute buttons turn neutral, and ↻ checks. Its first phone test failed (the taps never started the background work); the fix passed a phone re-test of ↻, and the TV picker passed a phone test. Its redesigned build failed on the phone because the widget, drawn by the previous version, was not redrawn after the update; the widget now redraws itself after every app update, which passed a phone re-test with the redesigned picker, changing the TV from the widget and ↻. Its first real command, a Mute, was confirmed by the receiver but did not change the sound: this TV's Cast volume is fixed, so the Cast mute is only logical and the widget says “Muted (Cast) · TV sound may not change” (the TV's audible mute goes through its own remote channel, in the app's TV remote panel); the other commands have not been tried. For a command, the log also shows what the TV reported before it, the value requested and whether the TV then reported it. `adb logcat -s control-tv` shows each step of a tap (no device, address or token is logged).

The widget does not run a permanent server or require Ubuntu, MCP, ChatGPT, an OpenAI key or the main window. Android starts the embedded shared control layer only when the app/widget work needs it. `WidgetActionWorker` still needs phone tests after the latest merge: start a tap with the app process absent; destroy and recreate the process before and after the durable command claim; verify delayed work under battery restrictions expires without a late command; lose and restore Wi-Fi during a read and during a command; update the APK with a pending widget and an existing selection; and verify the multicast lock and foreground notification are released after success and failure. These checks must record the widget wording, the single local send attempt and the physically observed TV effect separately. An APK build or JVM test is not evidence for those behaviours.

Android TV Remote channel (`DEVELOPMENT_PLAN.md` phase 2C, in progress): `control_tv.adapters.tv_remote` speaks the Google TV's own remote protocol (the audible volume, mute, power and navigation keys a fixed Cast volume cannot change) through the optional `control-tv[tvremote]` extra (`androidtvremote2==0.3.2`, Apache-2.0), which `setup` installs for development. It pairs explicitly with the code the TV shows, keeps its client certificate and key private (directory 0700, files 0600), sends only an allowlist of keys, once each, over one short connection per operation, and never resends after an ambiguous failure. The shared `ControlService` and the bridge expose it (`get_remote_status`, `start_remote_pairing`, `finish_remote_pairing`, `press_remote_key`), and the window shows a **TV remote** panel for a TV that advertises it: Vol −/+, Mute, Power, arrows, OK, Back, Home and "Read TV volume", with explicit pairing when the TV asks for it. A key is confirmed only when the TV reports the expected volume or mute change. Validated from Ubuntu on a real Google TV (pairing, Volume +, Mute; the TV does not report its volume on this channel, so a key is "sent, not confirmed" and only the person can check). The frozen desktop bridge and the Android app embed it: `bridge-build` syncs `--extra tvremote`, and `android-python` adds the extra with Chaquopy's arm64 builds of its native packages (`cryptography` 42.0.8, `cffi` 1.17.1, `chaquopy-libffi` 3.3), pinned by SHA-256 in `packaging/android_python.py`. `ping` reports `tvRemote`, which the frozen bridge smoke requires.

The TV's installed apps (`DEVELOPMENT_PLAN.md` 2E, implemented, not yet tried on a TV): the **TV apps** list shows the apps the TV's home screen can launch, as the TV lists them over its network debugging (ADB, `control_tv.adapters.tv_apps`, `adb-shell==0.4.4` in the `tvremote` extra). The owner turns it on (TV: Settings › System › About, select "Android TV OS build" 7 times, then Developer options › USB debugging) and allows control-TV on the TV the first time; control-TV keeps its ADB key private (0600 in the same 0700 directory) and sends only fixed commands: the launchable-app list and the app in front (read-only), and one launch event (`monkey`, the app's home-screen activity) for an app the person picks, built from a checked package name. Off, the list explains how to turn it on. Supported: TVs whose USB debugging accepts the classic network connection on port 5555; TVs (Android 13 and later) that only offer Wireless debugging (pairing code, TLS) are not supported yet. An app is opened once and is confirmed only when the TV then reports it in front (`dumpsys activity`). The remote channel's `market://launch?id=` links are not used: the pinned androidtvremote2 documents them as unreliable for many apps.

Status-area icon (Ubuntu, `DEVELOPMENT_PLAN.md` Phase 4b, not yet seen on a real desktop): Open control-TV, Play / Pause, Stop, Mute the TV, TV volume − and +, Quit. Each TV action runs as the window's own button (same selected TV, one request at a time, the same wording of what the TV confirmed); when it cannot run, the window comes to the front. Closing the window quits the application. The `.deb` depends on `libayatana-appindicator3-1`; a stock GNOME shows the icon with the AppIndicator extension (installed by default on Ubuntu).

MCP server (`DEVELOPMENT_PLAN.md` Phase 6): a local stdio MCP server over the same control layer, for an MCP client such as Codex CLI. `setup` installs its optional dependency for development (`uv sync --locked --extra mcp`: `control-tv[mcp]`, the official MCP Python SDK `mcp==1.30.0`). The SDK stays optional at run time: the frozen desktop bridge (`bridge-build`) and the Android app (`android-python`) are built from their own environments without it. The client starts it; for Codex, in `~/.codex/config.toml`:

```toml
[mcp_servers.control-tv]
command = "/path/to/control-TV/.venv/bin/python"
args = ["-m", "control_tv.mcp_server"]
```

Tools: `discover_devices`, `get_status`, `play`, `pause`, `stop`, `seek`, `set_volume`, `set_muted`, addressed by the stable `deviceId` from `discover_devices`. Every call goes through the same `bridge.dispatch` and `ControlService` as the window, one at a time and never retried; a command answer keeps "sent" apart from "confirmed", and a failed command says it was not sent only when the shared layer proves it; otherwise (`timeout`, `device_unavailable`, `command_rejected`, `internal_error`) it says the command may or may not have reached the TV and must not be resent automatically. Device names, ids and media titles are visible to the client and its model; addresses, media content ids and error details are not returned. ChatGPT web cannot start a local stdio server (see Phase 6 for the open options); the server listens on no network port.

## Repository layout

```text
src/control_tv/domain/     typed models, errors and command results (pure Python)
src/control_tv/ports.py    CastTransport and TvControl interfaces
src/control_tv/service.py  ControlService: validation and sent-versus-confirmed verification
src/control_tv/adapters/   focused external-library adapters (PyChromecast)
src/control_tv/bridge.py   stdio JSON bridge exposing ControlService to the Tauri shell
src/control_tv/embedded.py the same bridge called in-process by the Android app
src/control_tv/mcp_server.py the local MCP server (stdio) over the same bridge dispatch
tests/                     deterministic unit tests (in-memory fake TV, fake clock)
src-tauri/                 Tauri 2 application shell (Rust); no Cast/control logic
src-tauri/gen/android/     Android project (Gradle, Chaquopy, Kotlin bridge plugin)
ui/                        frontend (vanilla TypeScript + Vite); src/model.ts, src/playback.ts, src/sound.ts and src/interaction.ts hold the tested state, control, wording and slider-settling logic
scripts/dev.py             development, cleanup and disk-usage commands
assets/                    project assets
```

## Development principles

- Keep the dependency set small and justified.
- Keep generated files and build outputs out of Git.
- Build only the target currently being developed or tested.
- Do not duplicate Cast logic between the UI and MCP.
- Do not claim hardware support until it has been tested on a real compatible device.
- Treat media/content resolution as separate from Cast transport.

## Status

The shared control foundation and PyChromecast transport are implemented and covered by deterministic tests. Discovery, UUID selection, bounded status/recovery, media commands, input validation and connection cleanup are automated-test validated. Read-only physical validation of discovery, UUID selection and status is done (see `DEVELOPMENT_PLAN.md`). Real commands have also been sent one at a time to a YouTube session (see `docs/CAST_HARDWARE_VALIDATION.md`): Pause and Play had a physical effect but stayed `UNCONFIRMED` because the session reported no usable content id, and a Seek had no physical effect although the receiver reported the target position, so playback-command hardware validation remains incomplete and `CONFIRMED` is documented as receiver-reported, not as proof of the picture. Follow `docs/CAST_HARDWARE_VALIDATION.md` before claiming confirmed Chromecast or Google TV command behavior. The local MCP server is validated by automated tests and read-only with a real MCP client (Codex CLI: tool list, discovery and one status on a real network); no control command has been sent through it. There is still no media-loading action in the application (it controls media already playing), no system tray, no Windows build (abandoned), and no published release package (the release `.deb` with the frozen bridge has been installed and launched locally on Ubuntu 22.04, and installed and pinged in CI, and its public distribution waits for license blockers).

Phase 4 (Tauri UI) has an initial application shell: a Tauri 2 Rust crate that spawns the shared control layer as a Python subprocess over a stdio JSON bridge, and a one-page frontend with device discovery, selection by stable device id and a read-only view of the selected device's receiver/media status. It has been built, installed as a real `.deb` and launched on Debian/Ubuntu (Ubuntu 22.04). Android (personal sideloading, arm64-v8a, PR #30): a debug APK embedding the same Python control layer was installed and launched on a physical phone, where the embedded CPython started, `control_tv` imported and answered its ping (0.1.0), the multicast lock was held and real TVs were discovered; no Cast command was sent from the phone, and receiver status and commands are not validated on Android yet. Windows is abandoned. Play, pause, stop, seek, volume and mute controls are built (one volume gesture can raise the level by at most 10 points; lowering is not limited) and are validated by automated tests and runs of the real application against a fake TV. In the desktop window's real-hardware campaign on `main`, Pause and Play were each sent once to a YouTube session and had a physical effect but returned `UNCONFIRMED` (no usable content id), a Seek returned `UNCONFIRMED` and had no physical effect, and `Stop`, volume and mute were not sent. Separately, on PR #33 (the Android home-screen widget, open and not merged into `main`), mute and unmute were tested from a phone against a real TV: the receiver's mute state was confirmed, the Cast volume is `fixed`, and no change in the physical sound was observed. The window shows the media's title, its artist when the receiver names one, the application, the state, the position and length (`Live` for a live stream) and only the controls the receiver says the media supports. The application also discovers devices, selects one by its stable id and reads and refreshes status, including after rediscovery. Open items and planned work (MCP, the Android home-screen widget, tray, packaging) are tracked in `DEVELOPMENT_PLAN.md`.

## Contributing, security and license

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Report security vulnerabilities privately as described in [SECURITY.md](SECURITY.md), never in a public issue. control-TV is distributed under the GNU General Public License version 3 ([LICENSE](LICENSE)).
