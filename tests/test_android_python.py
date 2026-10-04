"""Preparing the Python packages the Android app embeds (`packaging/android_python.py`).

Nothing is downloaded or built here: the network and `uv build` are replaced by fixtures.
"""

from __future__ import annotations

import hashlib
import importlib.util
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
