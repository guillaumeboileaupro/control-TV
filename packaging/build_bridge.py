#!/usr/bin/env python3
"""Build the platform-native PyInstaller onedir consumed by Tauri.

All PyInstaller state is repository-local. The command deliberately does not use
``--clean``: PyInstaller's global/shared caches are never touched by this build.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGING_ROOT = REPO_ROOT / "packaging"
PYINSTALLER_ROOT = PACKAGING_ROOT / ".pyinstaller"
CONFIG_DIR = PYINSTALLER_ROOT / "config"
WORK_DIR = PYINSTALLER_ROOT / "work"
STAGING_DIR = PYINSTALLER_ROOT / "dist"
ENTRY_POINT = PACKAGING_ROOT / "bridge_entry.py"
OUTPUT_DIR = REPO_ROOT / "dist" / "python-bridge"


def venv_python(root: Path = REPO_ROOT) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return root / ".venv" / scripts / executable


def _remove_generated(path: Path) -> None:
    """Remove one known build output, never an arbitrary caller-supplied path."""
    resolved_root = REPO_ROOT.resolve()
    resolved = path.resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise RuntimeError(f"refusing to remove non-project path: {resolved}")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def build() -> Path:
    python = venv_python()
    if not python.is_file():
        raise RuntimeError(".venv is missing; run `uv sync --locked --group packaging`")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    _remove_generated(STAGING_DIR / "control-tv-bridge")

    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(CONFIG_DIR)
    command = [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--name",
        "control-tv-bridge",
        "--distpath",
        str(STAGING_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(PYINSTALLER_ROOT),
        str(ENTRY_POINT),
    ]
    subprocess.run(command, cwd=REPO_ROOT, env=environment, check=True)

    built = STAGING_DIR / "control-tv-bridge"
    executable = built / ("control-tv-bridge.exe" if os.name == "nt" else "control-tv-bridge")
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not produce {executable}")

    OUTPUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    _remove_generated(OUTPUT_DIR)
    shutil.copytree(built, OUTPUT_DIR, symlinks=True)
    return OUTPUT_DIR


def main() -> int:
    try:
        output = build()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
