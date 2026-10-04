# control-TV

Lightweight cross-platform application for controlling Chromecast / Google TV devices from a standalone interface, with an optional MCP adapter over the same control layer.

## Goals

- Standalone manual TV control that works without ChatGPT, MCP, an OpenAI API key or any cloud dependency.
- One authoritative control/domain layer shared by the manual application and the MCP adapter.
- Python for the Chromecast integration (`pychromecast` where it matches the required Cast capabilities); Rust only for native or performance-sensitive components that justify it.
- Tauri 2 as the cross-platform application and packaging shell, with an HTML/CSS UI written in JavaScript or TypeScript as the implementation requires.
- Target packages: Android APK, Windows executable/installer and Debian/Ubuntu package.
- Strict control of build artifacts, temporary files and disk usage.

## Architecture

```text
Manual UI  -> Tauri boundary -+
                              +-> shared control/domain layer -> Chromecast / Google TV
MCP client -> MCP adapter ----+   (MCP adapter: planned, not implemented)
```

Today the manual UI is a vanilla TypeScript page in a Tauri 2 window. Its Rust side only forwards requests, on a worker thread with a time bound, to a long-lived Python process (`python -m control_tv.bridge`) that speaks line-delimited JSON over stdin/stdout and calls `ControlService`, which drives the TV through `PyChromecastTransport`. No Cast logic lives in Rust or TypeScript.

The UI and the MCP adapter are thin. Device discovery, validation and state belong to the shared control layer, which reaches the TV through a focused Python Chromecast adapter. A command that was sent is not proof that the TV reached the requested state, so sent and confirmed state are kept distinct. See [ARCHITECTURE.md](ARCHITECTURE.md) for the decisions and [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) for the phased roadmap.

## Development

Prerequisite: Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). `uv` resolves and pins every dependency (including its own managed Python) into `uv.lock`, so `setup` reproduces the exact same `.venv` on Linux, Windows and macOS. Only Linux has been exercised so far.

```bash
python3 scripts/dev.py setup        # create .venv from uv.lock (uv sync --locked)
python3 scripts/dev.py lock         # regenerate uv.lock after changing a dependency
python3 scripts/dev.py check        # ruff, ruff format --check, mypy --strict, pytest, dependency check
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
python3 scripts/dev.py bridge-smoke   # ping that frozen bridge outside the checkout, without network
python3 scripts/dev.py release-deb    # both, then the release .deb, its notices, inventory and SHA256SUMS in dist/
npx --prefix ui tauri build --config src-tauri/tauri.release.conf.json --no-bundle   # release binary + python-bridge/ only
```

`bridge-build` rebuilds `.venv` on the exact CPython release bridges embed (`packaging/release-python-version`, 3.12.15, which needs a uv that knows it: 0.12.23 in CI) with the locked `packaging` dependency group (PyInstaller) and keeps all PyInstaller state under `packaging/.pyinstaller/` (removed by `clean`); `setup` removes the group again. It writes the bridge's third-party license notices and refuses a frozen bridge that still names a build-machine path. `release-deb` runs the strict smoke (it fails unless the checkout and the build Python are verifiably hidden), builds the Rust binary with `--remap-path-prefix` so it does not name the build machine's directories either, checks the package content and modes, and writes `dist/python-bridge.inventory.tsv` (SHA-256, size and mode of every bridge file) and `dist/SHA256SUMS`. Only Linux (Debian/Ubuntu) has been built, installed and launched so far: the release `.deb` was installed and launched on Ubuntu 22.04 with the checkout, the `.venv` and the system Python unavailable, but public distribution is blocked by open license items (see `docs/PACKAGING_LICENSES.md` and `DEVELOPMENT_PLAN.md`, Phase 7).

## Repository layout

```text
src/control_tv/domain/     typed models, errors and command results (pure Python)
src/control_tv/ports.py    CastTransport and TvControl interfaces
src/control_tv/service.py  ControlService: validation and sent-versus-confirmed verification
src/control_tv/adapters/   focused external-library adapters (PyChromecast)
src/control_tv/bridge.py   stdio JSON bridge exposing ControlService to the Tauri shell
tests/                     deterministic unit tests (in-memory fake TV, fake clock)
src-tauri/                 Tauri 2 application shell (Rust); no Cast/control logic
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

The shared control foundation and PyChromecast transport are implemented and covered by deterministic tests. Discovery, UUID selection, bounded status/recovery, media commands, input validation and connection cleanup are automated-test validated. Read-only physical validation of discovery, UUID selection and status is done (see `DEVELOPMENT_PLAN.md`). Real commands have also been sent one at a time to a YouTube session (see `docs/CAST_HARDWARE_VALIDATION.md`): Pause and Play had a physical effect but stayed `UNCONFIRMED` because the session reported no usable content id, and a Seek had no physical effect although the receiver reported the target position, so playback-command hardware validation remains incomplete and `CONFIRMED` is documented as receiver-reported, not as proof of the picture. Follow `docs/CAST_HARDWARE_VALIDATION.md` before claiming confirmed Chromecast or Google TV command behavior. There is still no MCP adapter, no media-loading action in the application (it controls media already playing), no system tray, no Android or Windows build, and no published release package (the release `.deb` with the frozen bridge has been installed and run once locally, and its public distribution waits for license blockers).

Phase 4 (Tauri UI) has an initial application shell: a Tauri 2 Rust crate that spawns the shared control layer as a Python subprocess over a stdio JSON bridge, and a one-page frontend with device discovery, selection by stable device id and a read-only view of the selected device's receiver/media status. It has been built, installed as a real `.deb` and launched on Debian/Ubuntu (Ubuntu 22.04); Windows and Android are untouched. Play, pause, stop, seek, volume and mute controls are built (one volume gesture can raise the level by at most 10 points; lowering is not limited) and are validated by automated tests and runs of the real application against a fake TV. On real hardware, Pause and Play were each sent once to a YouTube session and had a physical effect but returned `UNCONFIRMED` (no usable content id), a Seek returned `UNCONFIRMED` and had no physical effect, and `Stop`, volume and mute remain untested on real hardware. The window shows the media's title, its artist when the receiver names one, the application, the state, the position and length (`Live` for a live stream) and only the controls the receiver says the media supports. The application also discovers devices, selects one by its stable id and reads and refreshes status, including after rediscovery. Open items and planned work (tray, Android and its home-screen widget, MCP, packaging) are tracked in `DEVELOPMENT_PLAN.md`.

## Contributing, security and license

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Report security vulnerabilities privately as described in [SECURITY.md](SECURITY.md), never in a public issue. control-TV is distributed under the GNU General Public License version 3 ([LICENSE](LICENSE)).
