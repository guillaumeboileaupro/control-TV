"""Preparing the Python packages the Android app embeds (`packaging/android_python.py`).

Nothing is downloaded or built here: the network and `uv build` are replaced by fixtures.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "control_tv_android_python", ROOT / "packaging" / "android_python.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


android = load_module()


def package(name: str, deps: tuple[str, ...] = (), wheels: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "name": name,
        "version": "1.0",
        "dependencies": [{"name": dep} for dep in deps],
        "wheels": [{"url": f"https://files/{w}", "hash": "sha256:00"} for w in wheels],
    }


def test_only_the_projects_transitive_runtime_dependencies_are_embedded() -> None:
    lock = {
        "package": [
            package("control-tv", ("pychromecast",)),
            package("pychromecast", ("zeroconf", "protobuf")),
            package("zeroconf", ("ifaddr",)),
            package("protobuf"),
            package("ifaddr"),
            package("pytest"),  # a development tool, not a runtime dependency
        ]
    }

    assert list(android.runtime_packages(lock)) == [
        "ifaddr",
        "protobuf",
        "pychromecast",
        "zeroconf",
    ]


def test_the_real_lock_embeds_exactly_the_cast_stack() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    names = set(android.runtime_packages(lock))

    assert {"pychromecast", "zeroconf", "protobuf", "ifaddr", "casttube"} <= names
    assert not names & {"pyinstaller", "pytest", "mypy", "ruff", "pip", "wheel", "setuptools"}


def test_every_embedded_package_except_zeroconf_has_a_pure_wheel_in_the_lock() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    missing = [
        name
        for name, entry in android.runtime_packages(lock).items()
        if name not in android.FROM_SDIST and android.pure_wheel(entry) is None
    ]

    assert missing == []


def test_a_pure_wheel_is_preferred_and_a_native_only_package_has_none() -> None:
    pure = package("x", wheels=("x-1.0-cp312-manylinux.whl", "x-1.0-py3-none-any.whl"))
    native = package("y", wheels=("y-1.0-cp312-cp312-manylinux_2_17_x86_64.whl",))

    assert android.pure_wheel(pure)["url"].endswith("x-1.0-py3-none-any.whl")
    assert android.pure_wheel(native) is None


def test_a_download_whose_hash_differs_from_the_lock_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"tampered"

    monkeypatch.setattr(android.urllib.request, "urlopen", lambda url: Response())
    good = hashlib.sha256(b"tampered").hexdigest()

    with pytest.raises(RuntimeError, match=r"does not match uv\.lock"):
        android.download("https://files/x.whl", "0" * 64, tmp_path / "x.whl")
    assert not (tmp_path / "x.whl").exists()
    assert (
        android.download("https://files/x.whl", good, tmp_path / "x.whl").read_bytes()
        == b"tampered"
    )


def test_a_lock_entry_without_a_sha256_is_refused() -> None:
    with pytest.raises(RuntimeError, match="without a SHA-256"):
        android.expected_hash({"hash": "md5:abc"})


def test_compiled_modules_are_detected_in_a_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "x-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("x/__init__.py", "")
        archive.writestr("x/_speed.cpython-312-x86_64-linux-gnu.so", b"")
        archive.writestr("x/_types.pxd", "")

    assert android.compiled_members(wheel) == ["x/_speed.cpython-312-x86_64-linux-gnu.so"]


def test_requirements_pin_the_project_and_every_dependency_exactly() -> None:
    packages = {"zeroconf": {"name": "zeroconf", "version": "0.151.3"}}

    assert android.requirements_lines(packages, "0.1.0") == [
        "control-tv==0.1.0",
        "zeroconf==0.151.3",
    ]


# --- Offline wheel builds with the locked build backends ---------------------------------

OFFLINE_FLAGS = ["--isolated", "--no-index", "--no-deps", "--no-build-isolation", "--no-cache-dir"]


def test_a_wheel_is_built_by_pip_offline_with_the_installed_backends(tmp_path: Path) -> None:
    command = android.pip_wheel_command(tmp_path / "x.tar.gz", tmp_path / "out")

    assert command[:4] == [sys.executable, "-m", "pip", "wheel"]
    assert all(flag in command for flag in OFFLINE_FLAGS)
    assert command[-3:] == ["--wheel-dir", str(tmp_path / "out"), str(tmp_path / "x.tar.gz")]
    assert not {"uv", "build", "--index-url", "--extra-index-url", "--find-links"} & set(command)


def _write_wheel(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def test_zeroconf_is_built_offline_without_cython_and_retagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def run(command: list[str], check: bool, env: dict[str, str] | None = None) -> None:
        calls.append((command, env))
        if command[2:4] == ["pip", "wheel"]:
            out = Path(command[command.index("--wheel-dir") + 1])
            _write_wheel(out / "zeroconf-1.0-cp312-cp312-linux_x86_64.whl", {"zeroconf/a.py": b""})
        else:  # wheel tags
            built = Path(command[-1])
            shutil.copy(built, built.with_name("zeroconf-1.0-py3-none-any.whl"))

    monkeypatch.setattr(android.subprocess, "run", run)
    destination = tmp_path / "wheels"
    destination.mkdir()

    built = android.build_pure_from_sdist(tmp_path / "zeroconf-1.0.tar.gz", destination)

    assert built == destination / "zeroconf-1.0-py3-none-any.whl"
    (build, build_env), (retag, retag_env) = calls
    assert build == android.pip_wheel_command(tmp_path / "zeroconf-1.0.tar.gz", Path(build[-2]))
    assert build_env is not None and build_env["SKIP_CYTHON"] == "1"
    assert retag[2:5] == ["wheel", "tags", "--remove"]
    for env in (build_env, retag_env):
        assert env is not None and env["SOURCE_DATE_EPOCH"] == android.ZIP_EPOCH
    assert len(calls) == 2


def test_a_zeroconf_build_that_still_compiles_modules_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(command: list[str], check: bool, env: dict[str, str] | None = None) -> None:
        out = Path(command[command.index("--wheel-dir") + 1])
        _write_wheel(out / "zeroconf-1.0-cp312.whl", {"zeroconf/_cache.cpython-312.so": b""})

    monkeypatch.setattr(android.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="compiled modules"):
        android.build_pure_from_sdist(tmp_path / "zeroconf-1.0.tar.gz", tmp_path)


def test_the_project_wheel_is_built_offline_from_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], cwd: Path, check: bool, env: dict[str, str]) -> None:
        assert env["SOURCE_DATE_EPOCH"] == android.ZIP_EPOCH
        calls.append(command)
        _write_wheel(
            tmp_path / "control_tv-0.1.0-py3-none-any.whl", {"control_tv/__init__.py": b""}
        )

    monkeypatch.setattr(android.subprocess, "run", run)

    built = android.build_project_wheel(tmp_path)

    assert built.name == "control_tv-0.1.0-py3-none-any.whl"
    assert calls == [android.pip_wheel_command(android.REPO_ROOT, tmp_path)]


def _lock_with(**versions: str) -> dict[str, Any]:
    return {"package": [{"name": name, "version": version} for name, version in versions.items()]}


def test_the_build_backends_must_be_installed_at_their_locked_versions() -> None:
    lock = _lock_with(setuptools="84.0.0", **{"poetry-core": "2.5.0"})
    installed = {"setuptools": "84.0.0", "poetry-core": "2.5.0"}

    android.check_build_backends(lock, installed=installed.__getitem__)

    installed["poetry-core"] = "2.6.0"
    with pytest.raises(RuntimeError, match=r"poetry-core 2\.6\.0 is not the locked 2\.5\.0"):
        android.check_build_backends(lock, installed=installed.__getitem__)


def test_a_missing_build_backend_is_refused_before_any_build() -> None:
    lock = _lock_with(setuptools="84.0.0", **{"poetry-core": "2.5.0"})

    def installed(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    with pytest.raises(RuntimeError, match="uv sync --locked --group android"):
        android.check_build_backends(lock, installed=installed)
    with pytest.raises(RuntimeError, match=r"not in uv\.lock"):
        android.check_build_backends(_lock_with(setuptools="84.0.0"), installed=lambda n: "84.0.0")


def test_the_android_group_pins_each_build_backend_to_its_locked_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    pins = dict(requirement.split("==") for requirement in project["dependency-groups"]["android"])
    locked = {android.normalized(p["name"]): p["version"] for p in lock["package"]}

    for backend in android.BUILD_BACKENDS:
        assert pins[backend] == locked[backend]
