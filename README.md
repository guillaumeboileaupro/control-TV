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
MCP client -> MCP adapter ----+
```

The UI and the MCP adapter are thin. Device discovery, validation and state belong to the shared control layer, which reaches the TV through a focused Python Chromecast adapter. A command that was sent is not proof that the TV reached the requested state, so sent and confirmed state are kept distinct. See [ARCHITECTURE.md](ARCHITECTURE.md) for the decisions and [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) for the phased roadmap.

## Development

Prerequisite: Python 3.11 or newer. The commands below use only the standard library to bootstrap and behave the same on Linux, Windows and macOS. Only Linux has been exercised so far.

```bash
python3 scripts/dev.py setup        # create .venv and install the project with dev dependencies
python3 scripts/dev.py check        # ruff, ruff format --check, mypy --strict, pytest
python3 scripts/dev.py lint         # lint and format check only
python3 scripts/dev.py typecheck    # mypy only
python3 scripts/dev.py test         # pytest only
```

On Windows use `py -3.12 scripts/dev.py <command>` (or any Python 3.11+).

`setup` creates `.venv` with the interpreter that runs it. All other quality commands run through `.venv`, so run `setup` first.

### Generated output and disk usage

Build output and caches are disposable and excluded from Git. Cleanup is limited to a fixed allowlist of project-owned paths; it never touches shared caches (Cargo, Gradle, Android SDK/NDK, pip, uv) or anything outside the repository.

```bash
python3 scripts/dev.py disk-usage             # free disk and size of each generated path
python3 scripts/dev.py clean [--dry-run]      # caches, bytecode, packaging metadata, Rust/Tauri/Android build output, scratch dirs
python3 scripts/dev.py dist-clean [--dry-run] # clean, plus .venv and dist/
```

`clean` keeps `.venv` and `dist/`. `dist-clean` removes everything reproducible, so `setup` must be run again afterwards. Run `disk-usage` before and after build-heavy work.

## Repository layout

```text
src/control_tv/   Python package (shared control layer, to be built out)
tests/            deterministic unit tests
scripts/dev.py    development, cleanup and disk-usage commands
assets/           project assets
```

## Development principles

- Keep the dependency set small and justified.
- Keep generated files and build outputs out of Git.
- Build only the target currently being developed or tested.
- Do not duplicate Cast logic between the UI and MCP.
- Do not claim hardware support until it has been tested on a real compatible device.
- Treat media/content resolution as separate from Cast transport.

## Status

Phase 0: repository and development tooling. The package is an empty skeleton: there is no Cast discovery, no control code, no UI, no MCP adapter and no packaging yet. Nothing has been validated on a real Chromecast or Google TV device.
