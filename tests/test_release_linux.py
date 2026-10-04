"""The Linux release script (`packaging/release_linux.py`).

Nothing here builds a package: Cargo metadata, the Tauri CLI and `dpkg-deb` are replaced by
small fixtures, and every output goes under `tmp_path`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"


def load_release_module() -> ModuleType:
    sys.path.insert(0, str(PACKAGING))
    try:
        spec = importlib.util.spec_from_file_location(
            "control_tv_release_linux", PACKAGING / "release_linux.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(PACKAGING))


release = load_release_module()


def test_the_remappings_hide_cargo_rustup_and_the_checkout_most_specific_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CARGO_HOME", "/home/someone/.cargo")
    monkeypatch.setenv("RUSTUP_HOME", "/home/someone/.rustup")

    prefixes = release.remapped_prefixes()

    assert [(str(path), neutral) for path, neutral in prefixes] == [
        ("/home/someone/.rustup", "/rustup"),
        ("/home/someone/.cargo", "/cargo"),
        (str(release.REPO_ROOT), "/control-tv"),
    ]


def test_cargo_and_rustup_default_to_the_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CARGO_HOME", raising=False)
    monkeypatch.delenv("RUSTUP_HOME", raising=False)
    monkeypatch.setattr(release.Path, "home", lambda: tmp_path)

    assert release.cargo_home() == tmp_path / ".cargo"
    assert release.rustup_home() == tmp_path / ".rustup"


def test_the_remappings_are_added_to_the_callers_flags() -> None:
    prefixes = [(Path("/a b/.cargo"), "/cargo")]

    from_plain = release.encoded_rustflags({"RUSTFLAGS": "-C debuginfo=0"}, prefixes)
    from_encoded = release.encoded_rustflags(
        {"CARGO_ENCODED_RUSTFLAGS": "-Cx\x1f-Cy", "RUSTFLAGS": "ignored"}, prefixes
    )

    # Encoded flags keep a path with a space in one argument.
    assert from_plain.split("\x1f") == [
        "-C",
        "debuginfo=0",
        "--remap-path-prefix=/a b/.cargo=/cargo",
    ]
    assert from_encoded.split("\x1f") == ["-Cx", "-Cy", "--remap-path-prefix=/a b/.cargo=/cargo"]
    assert release.encoded_rustflags({}, prefixes) == "--remap-path-prefix=/a b/.cargo=/cargo"


def crate(tmp_path: Path, name: str, license_name: str, files: tuple[str, ...]) -> dict[str, Any]:
    directory = tmp_path / "registry" / f"{name}-1.0.0"
    directory.mkdir(parents=True)
    (directory / "Cargo.toml").write_text("[package]", encoding="utf-8")
    for file in files:
        (directory / file).write_text(f"{name} {file}", encoding="utf-8")
    return {
        "id": name,
        "name": name,
        "version": "1.0.0",
        "license": license_name,
        "license_file": None,
        "manifest_path": str(directory / "Cargo.toml"),
        "targets": [{"kind": ["proc-macro"] if name.endswith("_derive") else ["lib"]}],
    }


def dependency(package: str, kind: str | None = None) -> dict[str, Any]:
    return {"pkg": package, "dep_kinds": [{"kind": kind, "target": None}]}


def test_only_crates_linked_into_the_binary_are_listed(tmp_path: Path) -> None:
    packages = [
        crate(tmp_path, "app", "GPL-3.0", ()),
        crate(tmp_path, "serde", "MIT OR Apache-2.0", ("LICENSE-MIT",)),
        crate(tmp_path, "serde_derive", "MIT OR Apache-2.0", ("LICENSE-MIT",)),
        crate(tmp_path, "syn", "MIT OR Apache-2.0", ("LICENSE-MIT",)),
        crate(tmp_path, "cc", "MIT OR Apache-2.0", ("LICENSE-MIT",)),
        crate(tmp_path, "itoa", "MIT OR Apache-2.0", ("LICENSE-MIT",)),
    ]
    metadata = {
        "packages": packages,
        "resolve": {
            "root": "app",
            "nodes": [
                {"id": "app", "deps": [dependency("serde"), dependency("cc", "build")]},
                {"id": "serde", "deps": [dependency("serde_derive"), dependency("itoa")]},
                {"id": "serde_derive", "deps": [dependency("syn")]},
                {"id": "syn", "deps": []},
                {"id": "cc", "deps": []},
                {"id": "itoa", "deps": []},
            ],
        },
    }

    linked = release.linked_crates(metadata)

    assert [c["name"] for c in linked] == ["itoa", "serde"]


def test_rust_notices_copy_license_files_and_name_crates_without_any(tmp_path: Path) -> None:
    with_files = crate(tmp_path, "serde", "MIT OR Apache-2.0", ("LICENSE-MIT", "LICENSE-APACHE"))
    without = crate(tmp_path, "unic-common", "MIT/Apache-2.0", ("README.md",))
    std = tmp_path / "std-docs"
    (std / "licenses").mkdir(parents=True)
    (std / "COPYRIGHT-library.html").write_text("std", encoding="utf-8")
    (std / "licenses" / "MIT.txt").write_text("MIT", encoding="utf-8")

    notices, missing = release.write_rust_notices([with_files, without], std, tmp_path / "out")

    text = notices.read_text(encoding="utf-8")
    assert (tmp_path / "out" / "serde-1.0.0" / "LICENSE-APACHE").is_file()
    assert (tmp_path / "out" / "rust-std" / "COPYRIGHT-library.html").is_file()
    assert not (tmp_path / "out" / "unic-common-1.0.0").exists()
    assert missing == ["unic-common 1.0.0 (MIT/Apache-2.0)"]
    assert "serde 1.0.0\n  License: MIT OR Apache-2.0\n  Text: serde-1.0.0/LICENSE-APACHE" in text
    assert "unic-common 1.0.0\n  License: MIT/Apache-2.0\n  Text: MISSING" in text
    assert "Rust standard library" in text and "not a legal review" in text


def test_a_declared_license_file_is_copied(tmp_path: Path) -> None:
    package = crate(tmp_path, "ring", "Apache-2.0 AND ISC", ())
    Path(package["manifest_path"]).with_name("THIRD-PARTY.txt").write_text("x", encoding="utf-8")
    package["license_file"] = "THIRD-PARTY.txt"

    _, missing = release.write_rust_notices([package], None, tmp_path / "out")

    assert missing == []
    assert (tmp_path / "out" / "ring-1.0.0" / "THIRD-PARTY.txt").is_file()


BRIDGE = "./usr/lib/control-tv/python-bridge"
DOC = "./usr/share/doc/control-tv"
LISTING = "\n".join(
    f"{mode} root/root 1 2026-10-04 13:09 {name}"
    for mode, name in [
        ("drwxr-xr-x", "./usr/bin/"),
        ("-rwxr-xr-x", "./usr/bin/control-tv"),
        ("-rwxr-xr-x", f"{BRIDGE}/control-tv-bridge"),
        ("-rwxr-xr-x", f"{BRIDGE}/_internal/libpython3.12.so.1.0"),
        ("-rw-r--r--", f"{BRIDGE}/licenses/THIRD_PARTY_NOTICES.txt"),
        ("-rw-r--r--", f"{DOC}/copyright"),
        ("-rw-r--r--", f"{DOC}/rust-licenses/THIRD_PARTY_NOTICES.txt"),
        ("lrwxrwxrwx", "./usr/lib/control-tv/link -> python-bridge"),
    ]
)


def test_the_package_listing_is_parsed_with_modes_and_without_directories() -> None:
    entries = release.parse_deb_listing(LISTING)

    assert entries["usr/bin/control-tv"] == 0o755
    assert entries["usr/share/doc/control-tv/copyright"] == 0o644
    assert entries["usr/lib/control-tv/link"] == 0o777
    assert "usr/bin/" not in entries


def test_a_complete_package_passes_the_content_check() -> None:
    release.check_deb_contents(release.parse_deb_listing(LISTING))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda e: e.pop("usr/lib/control-tv/python-bridge/control-tv-bridge"), "does not contain"),
        (
            lambda e: e.update({"usr/lib/control-tv/python-bridge/control-tv-bridge": 0o644}),
            "has mode 0644, expected 0755",
        ),
        (
            lambda e: e.pop("usr/lib/control-tv/python-bridge/_internal/libpython3.12.so.1.0"),
            "does not embed libpython",
        ),
        (
            lambda e: e.pop("usr/share/doc/control-tv/rust-licenses/THIRD_PARTY_NOTICES.txt"),
            "does not contain",
        ),
    ],
    ids=["no-bridge", "bridge-not-executable", "no-libpython", "no-rust-notices"],
)
def test_an_incomplete_package_is_refused(change: Any, message: str) -> None:
    entries = release.parse_deb_listing(LISTING)
    change(entries)

    with pytest.raises(RuntimeError, match=message):
        release.check_deb_contents(entries)


def test_checksums_are_sha256sum_lines_sorted_by_name(tmp_path: Path) -> None:
    deb = tmp_path / "control-tv_0.1.0_amd64.deb"
    inventory = tmp_path / "python-bridge.inventory.tsv"
    deb.write_bytes(b"deb")
    inventory.write_bytes(b"inventory")

    release.write_checksums([inventory, deb], tmp_path / "SHA256SUMS")

    assert (tmp_path / "SHA256SUMS").read_text().splitlines() == [
        f"{hashlib.sha256(b'deb').hexdigest()}  control-tv_0.1.0_amd64.deb",
        f"{hashlib.sha256(b'inventory').hexdigest()}  python-bridge.inventory.tsv",
    ]


def test_the_release_needs_a_frozen_bridge_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(release.bridge, "OUTPUT_DIR", tmp_path / "python-bridge")

    with pytest.raises(RuntimeError, match="frozen bridge is missing"):
        release.release()


def test_the_release_overlay_ships_the_bridge_the_notices_and_the_project_license() -> None:
    overlay = json.loads((ROOT / "src-tauri" / "tauri.release.conf.json").read_text())

    assert overlay["bundle"]["resources"] == {"../dist/python-bridge/": "python-bridge/"}
    assert overlay["bundle"]["linux"]["deb"]["files"] == {
        "/usr/share/doc/control-tv/copyright": "../LICENSE",
        "/usr/share/doc/control-tv/rust-licenses": "../dist/rust-licenses",
    }


def test_arguments_are_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert release.main(["--fast"]) == 2
    assert "usage" in capsys.readouterr().err
