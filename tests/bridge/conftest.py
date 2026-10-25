"""Bridge test fixtures: whether `adb` is on PATH, so `ping`'s `tvApps` never depends on the
host."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


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
    return adb


@pytest.fixture
def no_adb_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PATH holding no `adb` at all."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))


@pytest.fixture
def no_app_backend(no_adb_on_path: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither the `adb` program nor the pure-Python `adb-shell`."""
    from control_tv.adapters import tv_apps_adb_shell

    monkeypatch.setattr(tv_apps_adb_shell, "adb_shell_installed", lambda: False)
