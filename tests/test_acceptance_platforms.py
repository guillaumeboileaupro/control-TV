"""Platform acceptance adapters tested only with temporary artifacts and process doubles."""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if "acceptance" not in sys.modules:
    load("acceptance")
platforms = load("acceptance_platforms")


def make_apk(path: Path, names: tuple[str, ...]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"x")
    return path


def test_arm64_apk_with_embedded_python_and_product_package_passes(tmp_path: Path) -> None:
    apk = make_apk(
        tmp_path / "app.apk",
        (
            "AndroidManifest.xml",
            "resources.arsc",
            "lib/arm64-v8a/libpython3.12.so",
            "assets/chaquopy/requirements/control_tv/__init__.pyc",
        ),
    )

    valid, reason = platforms.apk_archive_check(apk)

    assert valid is True
    assert "arm64" in reason


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (("AndroidManifest.xml", "resources.arsc"), "arm64-v8a"),
        (
            (
                "AndroidManifest.xml",
                "resources.arsc",
                "lib/x86_64/libpython3.12.so",
                "control_tv/x.pyc",
            ),
            "x86_64",
        ),
        (
            ("AndroidManifest.xml", "resources.arsc", "lib/arm64-v8a/libapp.so", "control_tv/x"),
            "libpython",
        ),
    ],
)
def test_incomplete_or_wrong_architecture_apk_fails(
    tmp_path: Path, names: tuple[str, ...], expected: str
) -> None:
    valid, reason = platforms.apk_archive_check(make_apk(tmp_path / "bad.apk", names))
    assert valid is False
    assert expected in reason


def test_invalid_apk_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bad.apk"
    path.write_text("not a zip", encoding="utf-8")
    assert platforms.apk_archive_check(path)[0] is False


@pytest.mark.parametrize("prefix", ["", "./"])
def test_deb_inventory_accepts_both_dpkg_path_formats(prefix: str) -> None:
    inventory = "\n".join(
        f"-rwxr-xr-x root/root 1 {prefix}{path}"
        for path in (
            "usr/bin/control-tv",
            "usr/lib/control-tv/python-bridge/control-tv-bridge",
            "usr/lib/control-tv/python-bridge/_internal/libpython3.12.so.1.0",
            "usr/share/icons/hicolor/32x32/apps/control-tv.png",
            "usr/share/applications/control-tv.desktop",
        )
    )
    assert platforms.deb_contents_check(inventory)[0] is True


def test_deb_inventory_still_fails_when_the_bridge_is_absent() -> None:
    valid, reason = platforms.deb_contents_check(
        "usr/bin/control-tv\nusr/share/icons/hicolor/control-tv.png\n"
        "usr/share/applications/control-tv.desktop"
    )
    assert valid is False
    assert "python-bridge/control-tv-bridge" in reason


def test_source_check_reports_the_exact_missing_contract(tmp_path: Path) -> None:
    acceptance = sys.modules["acceptance"]
    source = tmp_path / "source.txt"
    source.write_text("bridge", encoding="utf-8")
    scenario = acceptance.Scenario("static", "android", "source", lambda context: None)

    result = platforms.source_check(
        tmp_path, scenario, ((Path("source.txt"), ("bridge", "widget")),)
    )

    assert result.outcome is acceptance.Outcome.FAIL
    assert "source.txt: widget" in result.log


def test_csp_check_accepts_repository_configuration() -> None:
    acceptance = sys.modules["acceptance"]
    scenario = acceptance.Scenario("csp", "ubuntu", "csp", lambda context: None)
    result = platforms.csp_check(SCRIPTS.parent, scenario)
    assert result.outcome is acceptance.Outcome.PASS
