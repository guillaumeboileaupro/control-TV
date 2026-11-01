"""Shared test fixtures.

Every test run, and every Python process a test starts, uses this checkout's own code, and
never the user's real control-TV state: the D2 and D4-5 restoration journals (and anything else
under `$XDG_STATE_HOME`) go to a private directory removed after the run, so no test can find a
real journal and replay it against the real display or sound server.

pytest's `pythonpath` puts `src/` first for the tests themselves; a test that starts `python -m
control_tv...` gets a new interpreter, which reads `PYTHONPATH` but not pytest's path, and would
otherwise import whatever the virtual environment installed (possibly another worktree's `src/`).
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

_SOURCE = str(Path(__file__).resolve().parents[1] / "src")
_INHERITED = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [_SOURCE, _INHERITED]))

_STATE = tempfile.mkdtemp(prefix="control-tv-test-state-")
os.environ["XDG_STATE_HOME"] = _STATE
atexit.register(shutil.rmtree, _STATE, ignore_errors=True)

# Whether `adb` is on PATH, so `ping`'s `tvApps` never depends on the host (bridge tests).


@pytest.fixture
def adb_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in `adb` first on PATH (never run: `ping` only looks it up); subprocesses
    inherit it."""
    tools = tmp_path / "tools"
    tools.mkdir()
    adb = tools / "adb"
    adb.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    adb.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    _no_sdk(tmp_path, monkeypatch)
    return adb


def _no_sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No Android SDK `adb` either: control-TV prefers one to `PATH` (find_adb)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    for variable in ("CONTROL_TV_ADB", "ANDROID_HOME", "ANDROID_SDK_ROOT"):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def no_adb_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PATH holding no `adb` at all."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    _no_sdk(tmp_path, monkeypatch)


@pytest.fixture
def no_app_backend(no_adb_on_path: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither the `adb` program nor the pure-Python `adb-shell`."""
    from control_tv.adapters import tv_apps_adb_shell

    monkeypatch.setattr(tv_apps_adb_shell, "adb_shell_installed", lambda: False)
