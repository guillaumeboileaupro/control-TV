"""The frozen-bridge build and smoke script (`packaging/build_bridge.py`).

No test runs PyInstaller or touches the real `dist/`: PyInstaller is replaced by a fake that
writes a tiny tree, and every output directory is redirected under `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "build_bridge.py"


def load_build_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("control_tv_build_bridge", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = load_build_module()


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every build location at a fake repository under `tmp_path`."""
    root = tmp_path / "repo"
    pyinstaller = root / "packaging" / ".pyinstaller"
    for name, value in {
        "REPO_ROOT": root,
        "PYINSTALLER_ROOT": pyinstaller,
        "CONFIG_DIR": pyinstaller / "config",
        "WORK_DIR": pyinstaller / "work",
        "STAGING_DIR": pyinstaller / "dist",
        "SANITIZED_DIR": pyinstaller / "sanitized",
        "OUTPUT_DIR": root / "dist" / "python-bridge",
    }.items():
        monkeypatch.setattr(build, name, value)
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(build, "venv_python", lambda root=root: python)
    return root


def fake_pyinstaller(
    monkeypatch: pytest.MonkeyPatch,
    content: bytes = b"frozen",
    facts: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """Replace the interpreter query and PyInstaller; record each PyInstaller call."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        build,
        "build_python_facts",
        lambda python: facts or {"prefix": "/opt/build-python", "name": None, "origin": None},
    )

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        built = build.STAGING_DIR / build.NAME
        built.mkdir(parents=True)
        (built / build.executable_name()).write_bytes(content)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build.subprocess, "run", run)
    return calls


def test_frozen_bridge_entry_calls_the_shared_bridge_main() -> None:
    source = (ROOT / "packaging" / "bridge_entry.py").read_text(encoding="utf-8")

    assert "from control_tv.bridge import main" in source
    assert 'if __name__ == "__main__":\n    main()' in source


def test_pyinstaller_state_and_outputs_are_project_local() -> None:
    for path in (
        build.PYINSTALLER_ROOT,
        build.CONFIG_DIR,
        build.WORK_DIR,
        build.STAGING_DIR,
        build.SANITIZED_DIR,
        build.OUTPUT_DIR,
    ):
        assert path.resolve().is_relative_to(ROOT.resolve())


@pytest.mark.parametrize("directory", ["WORK_DIR", "SANITIZED_DIR", "OUTPUT_DIR"])
def test_generated_packaging_output_is_ignored_by_git(directory: str) -> None:
    probe = getattr(build, directory) / "anything"
    ignored = subprocess.run(["git", "check-ignore", "-q", str(probe)], cwd=ROOT, check=False)

    assert ignored.returncode == 0


def test_tauri_release_overlay_bundles_the_complete_onedir_tree() -> None:
    overlay = json.loads(
        (ROOT / "src-tauri" / "tauri.release.conf.json").read_text(encoding="utf-8")
    )

    assert overlay["bundle"]["resources"] == {"../dist/python-bridge/": "python-bridge/"}


def test_the_command_is_onedir_without_cache_cleaning_and_excludes_setuptools() -> None:
    command = build.pyinstaller_command(Path("/py"), [Path("/sanitized")])

    assert command[:3] == ["/py", "-m", "PyInstaller"]
    assert "--onedir" in command
    assert "--clean" not in command
    assert command[command.index("--paths") + 1] == "/sanitized"
    excluded = [command[i + 1] for i, arg in enumerate(command) if arg == "--exclude-module"]
    assert excluded == ["setuptools", "pkg_resources", "_distutils_hack"]
    assert command[-1] == str(build.ENTRY_POINT)


def test_the_environment_uses_the_local_config_and_drops_the_callers_python_path() -> None:
    environment = build.pyinstaller_environment({"PYTHONPATH": "/elsewhere", "HOME": "/h"})

    assert environment == {"PYINSTALLER_CONFIG_DIR": str(build.CONFIG_DIR), "HOME": "/h"}


def test_sysconfigdata_is_copied_with_a_neutral_prefix(tmp_path: Path) -> None:
    origin = tmp_path / "_sysconfigdata_x.py"
    origin.write_text(
        "build_time_vars = {'prefix': '/home/someone/python', "
        "'LIBDIR': '/home/someone/python/lib', 'CC': 'gcc'}\n",
        encoding="utf-8",
    )

    sanitized = build.write_sanitized_sysconfigdata(
        origin, "_sysconfigdata_x", "/home/someone/python", tmp_path / "out"
    )

    assert sanitized == tmp_path / "out" / "_sysconfigdata_x.py"
    text = sanitized.read_text(encoding="utf-8")
    assert "/home/someone" not in text
    assert "'prefix': '/install'" in text and "'LIBDIR': '/install/lib'" in text
    assert "'CC': 'gcc'" in text


@pytest.mark.parametrize("prefix", ["", "/not/mentioned"])
def test_sysconfigdata_without_the_build_prefix_is_refused(tmp_path: Path, prefix: str) -> None:
    origin = tmp_path / "_sysconfigdata_x.py"
    origin.write_text("build_time_vars = {'prefix': '/install'}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="nothing to neutralize"):
        build.write_sanitized_sysconfigdata(origin, "_sysconfigdata_x", prefix, tmp_path)


def test_build_paths_are_found_in_files_and_inside_compressed_archives(tmp_path: Path) -> None:
    (tmp_path / "clean.so").write_bytes(b"\x7fELF nothing here")
    (tmp_path / "raw.txt").write_bytes(b"prefix=/home/builder/python")
    with zipfile.ZipFile(tmp_path / "base_library.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("module.pyc", b"x" * 64 + b"/srv/checkout" + b"y" * 64)

    found = build.find_build_paths(tmp_path, ["/home/builder", "/srv/checkout", ""])

    assert found == [
        "base_library.zip!module.pyc: /srv/checkout",
        "raw.txt: /home/builder",
    ]


def test_a_clean_tree_has_no_build_paths(tmp_path: Path) -> None:
    (tmp_path / "bridge").write_bytes(b"frozen")

    assert build.find_build_paths(tmp_path, ["/home/builder"]) == []


def test_the_real_frozen_archive_reader_is_used_for_pyinstaller_executables(
    tmp_path: Path,
) -> None:
    pytest.importorskip("PyInstaller")
    from PyInstaller.archive.writers import CArchiveWriter

    executable = tmp_path / "control-tv-bridge"
    source = tmp_path / "payload.txt"
    source.write_bytes(b"compiled from /srv/checkout/src")
    CArchiveWriter(str(executable), [("payload.txt", str(source), True, "x")], "")

    found = build.find_build_paths(tmp_path, ["/srv/checkout"])

    assert any(hit.startswith("control-tv-bridge!payload.txt") for hit in found), found


def test_build_freezes_into_dist_with_the_sanitized_module_on_the_search_path(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = project / "_sysconfigdata_x.py"
    origin.write_text("build_time_vars = {'prefix': '/opt/build-python'}\n", encoding="utf-8")
    calls = fake_pyinstaller(
        monkeypatch,
        facts={"prefix": "/opt/build-python", "name": "_sysconfigdata_x", "origin": str(origin)},
    )

    output = build.build()

    assert output == project / "dist" / "python-bridge"
    assert (output / build.executable_name()).read_bytes() == b"frozen"
    [call] = calls
    assert call["cwd"] == project
    assert call["env"]["PYINSTALLER_CONFIG_DIR"] == str(build.CONFIG_DIR)
    command = call["command"]
    assert command[command.index("--paths") + 1] == str(build.SANITIZED_DIR)
    assert "/opt/build-python" not in (build.SANITIZED_DIR / "_sysconfigdata_x.py").read_text()


def test_build_without_a_sysconfigdata_module_adds_no_search_path(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_pyinstaller(monkeypatch)

    build.build()

    assert "--paths" not in calls[0]["command"]


def test_build_refuses_a_bridge_that_still_names_a_build_path(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_pyinstaller(monkeypatch, content=b"lib=/opt/build-python/lib")

    with pytest.raises(RuntimeError, match="names build-machine paths"):
        build.build()

    assert not build.OUTPUT_DIR.exists()


def test_build_requires_the_development_environment(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "venv_python", lambda root=project: project / "missing")

    with pytest.raises(RuntimeError, match=r"\.venv is missing"):
        build.build()


def test_removal_is_refused_outside_the_repository(project: Path, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="refusing to remove"):
        build._remove_generated(tmp_path / "elsewhere")


PING = {"id": 1, "ok": True, "result": {"status": "ready", "controlTvVersion": "0.1.0"}}
NOT_FOUND = {"id": 2, "ok": False, "error": {"code": "device_not_found", "message": "x"}}


def test_the_expected_smoke_answers_are_accepted() -> None:
    build.check_smoke_output(json.dumps(PING) + "\n" + json.dumps(NOT_FOUND) + "\n")


@pytest.mark.parametrize(
    "answers",
    [
        [PING],
        [PING, {**NOT_FOUND, "error": {"code": "device_unavailable", "message": "x"}}],
        [{**PING, "result": {"status": "starting"}}, NOT_FOUND],
        [NOT_FOUND, PING],
    ],
    ids=["missing-answer", "network-touched", "not-ready", "out-of-order"],
)
def test_unexpected_smoke_answers_are_refused(answers: list[dict[str, Any]]) -> None:
    with pytest.raises(RuntimeError):
        build.check_smoke_output("".join(json.dumps(a) + "\n" for a in answers))


def fake_frozen_bridge(project: Path, script: str) -> Path:
    output = project / "dist" / "python-bridge"
    output.mkdir(parents=True)
    executable = output / build.executable_name()
    executable.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return output


@pytest.mark.skipif(sys.platform == "win32", reason="the fake bridge is a POSIX shell script")
def test_smoke_runs_a_copy_outside_the_repository_with_an_empty_environment(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = fake_frozen_bridge(
        project,
        'pwd > "$0.cwd"\nenv > "$0.env"\n'
        f"while read -r line; do :; done\necho '{json.dumps(PING)}'\n"
        f"echo '{json.dumps(NOT_FOUND)}'\n",
    )
    monkeypatch.setattr(build, "build_python_facts", lambda python: {"prefix": None})
    monkeypatch.setattr(build, "isolation_wrapper", lambda hidden: None)
    seen: dict[str, str] = {}
    real_copytree = build.shutil.copytree

    def copytree(source: Path, destination: Path, **kwargs: Any) -> Path:
        seen["destination"] = str(destination)
        return Path(real_copytree(source, destination, **kwargs))

    monkeypatch.setattr(build.shutil, "copytree", copytree)

    message = build.smoke(output)

    assert "outside the checkout" in message and "no namespace isolation" in message
    assert not Path(seen["destination"]).is_relative_to(project)
    assert not Path(seen["destination"]).exists()  # the copy is removed afterwards


@pytest.mark.skipif(sys.platform == "win32", reason="the fake bridge is a POSIX shell script")
def test_smoke_reports_a_bridge_that_fails(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = fake_frozen_bridge(project, "echo broken >&2\nexit 3\n")
    monkeypatch.setattr(build, "build_python_facts", lambda python: {"prefix": None})
    monkeypatch.setattr(build, "isolation_wrapper", lambda hidden: None)

    with pytest.raises(RuntimeError, match="exited with 3: broken"):
        build.smoke(output)


def test_smoke_requires_a_built_bridge(project: Path) -> None:
    with pytest.raises(RuntimeError, match="is missing"):
        build.smoke(project / "dist" / "python-bridge")


def test_without_unshare_the_smoke_runs_without_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(build.shutil, "which", lambda name: None)

    assert build.isolation_wrapper([Path("/srv/checkout")]) is None


def test_the_isolation_hides_each_directory_with_an_empty_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(build.sys, "platform", "linux")
    monkeypatch.setattr(build.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        build.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0)
    )

    wrapper = build.isolation_wrapper([tmp_path, tmp_path / "absent"])

    assert wrapper is not None
    assert wrapper[:4] == ["/usr/bin/unshare", "--user", "--map-root-user", "--mount"]
    script = wrapper[wrapper.index("-c") + 1]
    assert script == f"/usr/bin/mount -t tmpfs none '{tmp_path}' && exec \"$@\""


def test_an_unknown_action_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert build.main(["deploy"]) == 2
    assert "usage" in capsys.readouterr().err


def test_a_failed_action_is_reported_without_a_traceback(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert build.main(["smoke"]) == 1
    assert capsys.readouterr().err.startswith("error: ")
