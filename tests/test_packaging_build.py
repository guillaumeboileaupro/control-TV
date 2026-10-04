from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_build_module() -> ModuleType:
    path = ROOT / "packaging" / "build_bridge.py"
    spec = importlib.util.spec_from_file_location("control_tv_build_bridge", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_bridge_entry_calls_the_shared_bridge_main() -> None:
    source = (ROOT / "packaging" / "bridge_entry.py").read_text(encoding="utf-8")

    assert "from control_tv.bridge import main" in source
    assert "main()" in source
    assert 'if __name__ == "__main__"' in source


def test_pyinstaller_state_and_outputs_are_project_local() -> None:
    build = load_build_module()

    for path in (
        build.CONFIG_DIR,
        build.WORK_DIR,
        build.STAGING_DIR,
        build.OUTPUT_DIR,
    ):
        assert path.resolve().is_relative_to(ROOT.resolve())


def test_tauri_release_overlay_preserves_the_complete_onedir_tree() -> None:
    overlay = json.loads(
        (ROOT / "src-tauri" / "tauri.release.conf.json").read_text(encoding="utf-8")
    )

    assert overlay["bundle"]["resources"] == {"../dist/python-bridge/": "python-bridge/"}


def test_pyinstaller_command_is_onedir_without_global_cache_cleaning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = load_build_module()
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0

    def run(command: list[str], **kwargs: object) -> Completed:
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        built = build.STAGING_DIR / "control-tv-bridge"
        built.mkdir(parents=True)
        (built / "control-tv-bridge").touch()
        return Completed()

    monkeypatch.setattr(build, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(build, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(build, "STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(build, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(build, "PYINSTALLER_ROOT", tmp_path / "pyinstaller")
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(build, "venv_python", lambda: python)
    monkeypatch.setattr(build.subprocess, "run", run)

    output = build.build()
    command = cast(list[str], captured["command"])
    environment = cast(dict[str, str], captured["environment"])

    assert "--onedir" in command
    assert "--clean" not in command
    assert environment["PYINSTALLER_CONFIG_DIR"] == str(build.CONFIG_DIR)
    assert output == build.OUTPUT_DIR
