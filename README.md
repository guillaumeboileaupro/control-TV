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

`clean` keeps `.venv` and `dist/`. `dist-clean` removes everything reproducible, so `setup` must be run again afterwards. Run `disk-usage` before and after build-heavy work.

## Repository layout

```text
src/control_tv/domain/     typed models, errors and command results (pure Python)
src/control_tv/ports.py    CastTransport and TvControl interfaces
src/control_tv/service.py  ControlService: validation and sent-versus-confirmed verification
src/control_tv/adapters/    focused external-library adapters (PyChromecast)
tests/                     deterministic unit tests (in-memory fake TV, fake clock)
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

Phase 1 (shared control foundation) is in progress. The domain vocabulary and the control service exist and are tested against an in-memory fake TV. The PyChromecast transport now implements discovery, bounded UUID-based connection recovery, status and media/volume commands behind the shared interface. There is still no UI, no MCP adapter or packaging. Nothing has been validated on a real Chromecast or Google TV device.
