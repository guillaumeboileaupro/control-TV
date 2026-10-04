"""The frozen-bridge build and smoke script (`packaging/build_bridge.py`).

No test runs PyInstaller or touches the real `dist/`: PyInstaller is replaced by a fake that
writes a tiny tree, and every output directory is redirected under `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
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
        "INVENTORY": root / "dist" / "python-bridge.inventory.tsv",
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
        lambda python: (
            facts
            or {
                "prefix": "/opt/build-python",
                "name": None,
                "origin": None,
                "version": build.release_python_version(),
            }
        ),
    )
    # The fake executable is not a PyInstaller archive; the archive scan is tested on its own.
    monkeypatch.setattr(build, "pyinstaller_members", lambda executable: iter(()))
    monkeypatch.setattr(build, "pyinstaller_version", lambda: "6.22.3")

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        built = build.STAGING_DIR / build.NAME
        built.mkdir(parents=True)
        executable = built / build.executable_name()
        executable.write_bytes(content)
        executable.chmod(0o755)
        return subprocess.CompletedProcess(command, 0)

    def notices(bundle: Path, prefix: str) -> Path:
        notices = bundle / "licenses" / "THIRD_PARTY_NOTICES.txt"
        notices.parent.mkdir()
        notices.write_text("notices", encoding="utf-8")
        return notices

    monkeypatch.setattr(build.subprocess, "run", run)
    monkeypatch.setattr(build, "write_bundle_notices", notices)
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


def require_pyinstaller() -> None:
    """Skip without PyInstaller, except where it must be present (the release CI job)."""
    if os.environ.get("CONTROL_TV_REQUIRE_PYINSTALLER") == "1":
        import PyInstaller  # noqa: F401
    else:
        pytest.importorskip("PyInstaller")


def frozen_executable(
    directory: Path,
    modules: dict[str, str],
    *,
    namespaces: tuple[str, ...] = ("google",),
    with_pyz: bool = True,
) -> Path:
    """A real PyInstaller archive: a PKG holding an entry script and a PYZ of `modules`."""
    from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter

    sources = directory / "sources"
    sources.mkdir(parents=True)
    entries: list[tuple[str, str | None, str]] = []
    code = {}
    for name, text in modules.items():
        source = sources / f"{name}.py"
        source.write_text(text, encoding="utf-8")
        code[name] = compile(text, str(source), "exec")
        entries.append((name, str(source), "PYMODULE"))
    entries += [(name, None, "PYMODULE") for name in namespaces]
    pyz = sources / "PYZ.pyz"
    ZlibArchiveWriter(str(pyz), entries, code)
    entry = sources / "bridge_entry.py"
    entry.write_text("main()\n", encoding="utf-8")
    members = [("bridge_entry", str(entry), True, "s")]
    if with_pyz:
        members.append(("PYZ.pyz", str(pyz), False, "z"))
    bundle = directory / "bundle"
    bundle.mkdir()
    executable = bundle / "control-tv-bridge"
    CArchiveWriter(str(executable), members, "")
    return executable


def test_a_clean_frozen_archive_passes_the_scan_with_every_member_read(tmp_path: Path) -> None:
    require_pyinstaller()
    executable = frozen_executable(tmp_path, {"zeroconf": "X = 1\n", "certifi": "Y = 2\n"})

    names = [name for name, _ in build.pyinstaller_members(executable)]

    assert names == ["bridge_entry", "PYZ.pyz", "PYZ:zeroconf", "PYZ:certifi"]
    assert build.find_build_paths(executable.parent, ["/srv/checkout"], [executable]) == []


def test_a_build_path_inside_a_frozen_module_is_found(tmp_path: Path) -> None:
    require_pyinstaller()
    executable = frozen_executable(tmp_path, {"leaky": "PREFIX = '/srv/checkout/src'\n"})

    found = build.find_build_paths(executable.parent, ["/srv/checkout"], [executable])

    assert found == ["control-tv-bridge!PYZ:leaky: /srv/checkout"]


def test_an_executable_that_is_not_a_pyinstaller_archive_fails_the_scan(tmp_path: Path) -> None:
    require_pyinstaller()
    executable = tmp_path / "control-tv-bridge"
    executable.write_bytes(b"\x7fELF but no archive")

    with pytest.raises(RuntimeError, match="cannot be read as a PyInstaller archive"):
        build.find_build_paths(tmp_path, ["/srv/checkout"], [executable])


def test_a_missing_expected_executable_fails_the_scan(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"PyInstaller executable .* is missing"):
        build.find_build_paths(tmp_path, ["/srv/checkout"], [tmp_path / "control-tv-bridge"])


def test_a_member_that_cannot_be_extracted_fails_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_pyinstaller()
    from PyInstaller.archive.readers import CArchiveReader

    executable = frozen_executable(tmp_path, {"zeroconf": "X = 1\n"})
    real_extract = CArchiveReader.extract

    def extract(self: Any, name: str) -> Any:
        if name == "bridge_entry":
            raise ValueError("corrupt member")
        return real_extract(self, name)

    monkeypatch.setattr(CArchiveReader, "extract", extract)

    with pytest.raises(RuntimeError, match="cannot extract 'bridge_entry'"):
        build.find_build_paths(executable.parent, ["/srv/checkout"], [executable])


def test_a_partly_readable_pyz_fails_even_when_the_readable_part_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_pyinstaller()
    from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader

    # The unreadable module is the one that names the build path: skipping it would pass.
    executable = frozen_executable(
        tmp_path, {"clean": "X = 1\n", "leaky": "PREFIX = '/srv/checkout/src'\n"}
    )
    real_extract = ZlibArchiveReader.extract

    def extract(self: Any, name: str, raw: bool = False) -> Any:
        if name == "leaky":
            raise OSError("truncated module")
        return real_extract(self, name, raw=raw)

    monkeypatch.setattr(ZlibArchiveReader, "extract", extract)

    with pytest.raises(RuntimeError, match="cannot extract PYZ module 'leaky'"):
        build.find_build_paths(executable.parent, ["/srv/checkout"], [executable])


def test_a_pyz_module_without_data_fails_but_a_namespace_package_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_pyinstaller()
    from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader

    executable = frozen_executable(tmp_path, {"zeroconf": "X = 1\n"}, namespaces=("google",))
    assert "PYZ:google" not in [name for name, _ in build.pyinstaller_members(executable)]
    real_extract = ZlibArchiveReader.extract

    def extract(self: Any, name: str, raw: bool = False) -> Any:
        return None if name == "zeroconf" else real_extract(self, name, raw=raw)

    monkeypatch.setattr(ZlibArchiveReader, "extract", extract)

    with pytest.raises(RuntimeError, match=r"PYZ module 'zeroconf' .* has no data"):
        list(build.pyinstaller_members(executable))


def test_an_archive_without_its_pyz_fails_the_scan(tmp_path: Path) -> None:
    require_pyinstaller()
    executable = frozen_executable(tmp_path, {"zeroconf": "X = 1\n"}, with_pyz=False)

    with pytest.raises(RuntimeError, match="has no PYZ archive"):
        build.find_build_paths(executable.parent, ["/srv/checkout"], [executable])


def test_the_license_inventory_fails_with_the_archive_it_cannot_read(tmp_path: Path) -> None:
    require_pyinstaller()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "control-tv-bridge").write_bytes(b"not an archive")

    with pytest.raises(RuntimeError, match="cannot be read as a PyInstaller archive"):
        build.frozen_top_level_names(bundle)


def test_build_freezes_into_dist_with_the_sanitized_module_on_the_search_path(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = project / "_sysconfigdata_x.py"
    origin.write_text("build_time_vars = {'prefix': '/opt/build-python'}\n", encoding="utf-8")
    calls = fake_pyinstaller(
        monkeypatch,
        facts={
            "prefix": "/opt/build-python",
            "name": "_sysconfigdata_x",
            "origin": str(origin),
            "version": build.release_python_version(),
        },
    )

    output = build.build()

    assert output == project / "dist" / "python-bridge"
    assert (output / build.executable_name()).read_bytes() == b"frozen"
    assert (output / "licenses" / "THIRD_PARTY_NOTICES.txt").is_file()
    lines = build.INVENTORY.read_text().splitlines()
    assert lines[0] == (
        f"# control-tv-bridge {build.project_version()}, "
        f"CPython {build.release_python_version()}, PyInstaller 6.22.3"
    )
    inventory = [line.split("\t") for line in lines if not line.startswith("#")]
    assert [entry[3] for entry in inventory] == [
        "control-tv-bridge",
        "licenses/THIRD_PARTY_NOTICES.txt",
    ]
    assert inventory[0][2] == "0755"
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
    build.check_smoke_output(json.dumps(PING) + "\n" + json.dumps(NOT_FOUND) + "\n", "0.1.0")


def test_a_frozen_bridge_reporting_another_version_is_refused() -> None:
    with pytest.raises(RuntimeError, match=r"expected version 0\.2\.0"):
        build.check_smoke_output(json.dumps(PING) + "\n" + json.dumps(NOT_FOUND) + "\n", "0.2.0")


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
        build.check_smoke_output("".join(json.dumps(a) + "\n" for a in answers), "0.1.0")


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
    monkeypatch.setattr(build, "project_version", lambda: "0.1.0")

    message = build.smoke(output)

    assert "frozen bridge 0.1.0 answered outside the checkout" in message
    assert "no namespace isolation" in message
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
    assert script == (
        f"/usr/bin/mount -t tmpfs none '{tmp_path}' && "
        f"for entry in '{tmp_path}'/* '{tmp_path}'/.[!.]*; do "
        f'[ -e "$entry" ] && exit {build.ISOLATION_FAILED}; done; true && '
        'exec "$@"'
    )


def test_an_unknown_action_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert build.main(["deploy"]) == 2
    assert "usage" in capsys.readouterr().err


def test_a_failed_action_is_reported_without_a_traceback(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert build.main(["smoke"]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_the_project_version_is_read_from_the_package(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text('"""Doc."""\n\n__version__ = "0.1.0"\n', encoding="utf-8")

    assert build.project_version(init) == "0.1.0"
    assert build.project_version() == build.project_version(ROOT / "src/control_tv/__init__.py")


def test_a_package_without_a_version_is_refused(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text('"""Doc."""\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="declares no __version__"):
        build.project_version(init)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and symlinks")
def test_the_inventory_is_sorted_and_records_hash_size_mode_and_links(tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "lib.so").write_bytes(b"abc")
    (tmp_path / "b" / "lib.so").chmod(0o755)
    (tmp_path / "a.txt").write_bytes(b"")
    (tmp_path / "a.txt").chmod(0o644)
    (tmp_path / "link").symlink_to("b/lib.so")

    lines = build.inventory_lines(tmp_path)

    assert lines == [
        "# sha256\tsize\tmode\tpath",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\t0\t0644\ta.txt",
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad\t3\t0755\tb/lib.so",
        "-\t-\tlink\tlink -> b/lib.so",
    ]
    assert build.inventory_lines(tmp_path) == lines


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable bit")
def test_an_inventory_is_refused_when_the_bridge_is_not_executable(tmp_path: Path) -> None:
    (tmp_path / build.executable_name()).write_bytes(b"frozen")
    (tmp_path / build.executable_name()).chmod(0o644)

    with pytest.raises(RuntimeError, match="is not executable"):
        build.write_inventory(tmp_path, tmp_path.parent / "inventory.tsv")


class FakeDistribution:
    """Just what the notices read from an installed distribution."""

    def __init__(self, root: Path, name: str, version: str, metadata: dict[str, str]) -> None:
        self.root = root / f"{name}.dist-info"
        self.root.mkdir(parents=True)
        (self.root / "LICENSE").write_text(f"{name} license", encoding="utf-8")
        (self.root / "METADATA").write_text("meta", encoding="utf-8")
        self.version = version
        self.metadata = _Metadata({"Name": name, **metadata})
        self.files = [_File("LICENSE", self.root), _File("METADATA", self.root)]

    def locate_file(self, path: Any) -> Path:
        return Path(str(path))


class _File:
    def __init__(self, name: str, root: Path) -> None:
        self.name = name
        self.path = root / name

    def __str__(self) -> str:
        return str(self.path)


class _Metadata(dict[str, str]):
    def get_all(self, key: str) -> list[str] | None:
        value = self.get(key)
        return [value] if value else None


def test_the_notices_copy_every_license_text_and_name_what_is_missing(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "_internal").mkdir(parents=True)
    (bundle / "_internal" / "libstdc++.so.6").write_bytes(b"lib")
    cpython = tmp_path / "LICENSE.txt"
    cpython.write_text("PSF", encoding="utf-8")
    zeroconf = FakeDistribution(
        tmp_path, "zeroconf", "0.151.3", {"License-Expression": "LGPL-2.1-or-later"}
    )
    certifi = FakeDistribution(tmp_path, "certifi", "2026.7.22", {"License": "MPL-2.0"})
    pyinstaller = FakeDistribution(tmp_path, "pyinstaller", "6.22.3", {})

    notices = build.write_python_notices(
        bundle,
        [certifi, zeroconf],
        cpython,
        build.release_python_version(),
        pyinstaller,
    )

    text = notices.read_text(encoding="utf-8")
    licenses = bundle / "licenses"
    cpython_dir = f"CPython-{build.release_python_version()}"
    assert (licenses / cpython_dir / "LICENSE.txt").read_text() == "PSF"
    assert (licenses / "zeroconf-0.151.3" / "LICENSE").read_text() == "zeroconf license"
    assert (licenses / "certifi-2026.7.22" / "LICENSE").is_file()
    assert (licenses / "PyInstaller-6.22.3" / "LICENSE").is_file()
    assert not (licenses / "certifi-2026.7.22" / "METADATA").exists()
    assert "zeroconf 0.151.3\n  License: LGPL-2.1-or-later" in text
    assert "certifi 2026.7.22\n  License: MPL-2.0" in text
    assert "OpenSSL (Apache-2.0)" in text and "Texts: MISSING" in text
    assert "libstdc++.so.6 (copied from the build system)" in text
    assert "libgcc_s.so.1" not in text  # not in this bundle
    assert "not a legal review" in text


def test_frozen_distributions_follow_the_frozen_top_level_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "_internal" / "certifi").mkdir(parents=True)
    (bundle / "_internal" / "libpython3.12.so.1.0").write_bytes(b"")
    monkeypatch.setattr(
        build, "pyinstaller_members", lambda executable: iter([("PYZ:certifi.core", b"")])
    )

    found = build.frozen_distributions(bundle, {"certifi": ["certifi"], "pytest": ["pytest"]})

    assert [d.metadata["Name"] for d in found] == ["certifi"]


def test_the_release_python_is_one_exact_version() -> None:
    version = build.release_python_version()

    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert version == (ROOT / "packaging" / "release-python-version").read_text().strip()


@pytest.mark.parametrize("content", ["3.12", "3.12.x", "", "3.12.15\n3.12.13"])
def test_a_release_python_that_is_not_one_exact_version_is_refused(
    tmp_path: Path, content: str
) -> None:
    pin = tmp_path / "release-python-version"
    pin.write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeError, match="must hold one exact CPython version"):
        build.release_python_version(pin)


def test_the_build_refuses_any_other_cpython_than_the_release_one(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_pyinstaller(
        monkeypatch,
        facts={"prefix": "/opt/build-python", "name": None, "origin": None, "version": "3.12.13"},
    )

    with pytest.raises(RuntimeError, match=r"runs CPython 3\.12\.13, but releases embed"):
        build.build()

    assert calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="the fake bridge is a POSIX shell script")
def test_the_strict_smoke_fails_when_no_isolation_can_be_set_up(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = fake_frozen_bridge(
        project,
        f"while read -r line; do :; done\necho '{json.dumps(PING)}'\n"
        f"echo '{json.dumps(NOT_FOUND)}'\n",
    )
    monkeypatch.setattr(build, "build_python_facts", lambda python: {"prefix": None})
    monkeypatch.setattr(build, "isolation_wrapper", lambda hidden: None)
    monkeypatch.setattr(build, "project_version", lambda: "0.1.0")

    with pytest.raises(RuntimeError, match="strict smoke needs a Linux user/mount namespace"):
        build.smoke(output, strict=True)
    assert "no namespace isolation" in build.smoke(output)


@pytest.mark.skipif(sys.platform == "win32", reason="the fake bridge is a POSIX shell script")
def test_a_namespace_that_still_shows_a_hidden_directory_fails_the_smoke(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = fake_frozen_bridge(project, "exit 0\n")
    monkeypatch.setattr(build, "build_python_facts", lambda python: {"prefix": None})
    # Stands for the namespace's own check finding the checkout still visible.
    wrapper = ["/bin/sh", "-c", f"exit {build.ISOLATION_FAILED}", "sh"]
    monkeypatch.setattr(build, "isolation_wrapper", lambda hidden: wrapper)

    with pytest.raises(RuntimeError, match="still visible in the namespace"):
        build.smoke(output, strict=True)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux user and mount namespaces")
def test_the_namespace_check_detects_a_directory_left_visible(tmp_path: Path) -> None:
    """The real check, run without the mounts: a visible entry exits with ISOLATION_FAILED."""
    (tmp_path / ".hidden-file").write_text("x", encoding="utf-8")
    wrapper = build.isolation_wrapper([tmp_path])
    if wrapper is None:
        pytest.skip("unprivileged user namespaces are not available here")
    script = wrapper[wrapper.index("-c") + 1]
    checks_only = script.split(" && ", 1)[1]

    visible = subprocess.run(["/bin/sh", "-c", checks_only, "sh", "true"], check=False)
    (tmp_path / ".hidden-file").unlink()
    empty = subprocess.run(["/bin/sh", "-c", checks_only, "sh", "true"], check=False)

    assert visible.returncode == build.ISOLATION_FAILED
    assert empty.returncode == 0


def test_the_strict_flag_is_the_only_smoke_option(capsys: pytest.CaptureFixture[str]) -> None:
    assert build.main(["smoke", "--lax"]) == 2
    assert "smoke [--strict]" in capsys.readouterr().err
