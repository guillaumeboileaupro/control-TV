#!/usr/bin/env python3
"""Build the Linux release `.deb` around the frozen bridge and record what it ships.

Run with the development environment's interpreter after `build_bridge.py build` and `smoke`
(`python3 scripts/dev.py release-deb` runs all three):

1. write the license notices of every Rust crate linked into the application, and of the
   Rust standard library, to `dist/rust-licenses/`;
2. build the release `.deb` with the release overlay (`src-tauri/tauri.release.conf.json`),
   remapping the build machine's Cargo, rustup and checkout directories in compiled paths
   (`--remap-path-prefix`, stable rustc) so the binary does not name them;
3. unpack the `.deb` and refuse it if any file or archive member still names a build-machine
   path, or if the application, the bundled bridge, `libpython` or the notices are missing or
   not executable where they must be;
4. copy the `.deb` to `dist/` and write `dist/SHA256SUMS` for it and the bridge inventory.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import build_bridge as bridge

REPO_ROOT = bridge.REPO_ROOT
SRC_TAURI = REPO_ROOT / "src-tauri"
RELEASE_CONFIG = SRC_TAURI / "tauri.release.conf.json"
DEB_DIR = SRC_TAURI / "target" / "release" / "bundle" / "deb"
DIST = REPO_ROOT / "dist"
RUST_NOTICES_DIR = DIST / "rust-licenses"
CHECKSUMS = DIST / "SHA256SUMS"
APPLICATION = "control-tv"
INSTALL_ROOT = Path("usr/lib") / APPLICATION
# Mode each of these must have in the package; a missing one fails the release.
REQUIRED_ENTRIES = {
    f"usr/bin/{APPLICATION}": 0o755,
    f"{INSTALL_ROOT}/python-bridge/{bridge.NAME}": 0o755,
    f"{INSTALL_ROOT}/python-bridge/{bridge.LICENSES_DIR}/{bridge.NOTICES_FILE}": None,
    f"usr/share/doc/{APPLICATION}/copyright": None,
    f"usr/share/doc/{APPLICATION}/rust-licenses/{bridge.NOTICES_FILE}": None,
}
RUST_LICENSE_HINTS = ("LICEN", "COPYING", "NOTICE", "UNLICENSE", "COPYRIGHT")
# Permission bits in `ls -l` order, as `dpkg-deb -c` prints them.
_MODE_BITS = (0o400, 0o200, 0o100, 0o040, 0o020, 0o010, 0o004, 0o002, 0o001)


def cargo_home() -> Path:
    return Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")


def rustup_home() -> Path:
    return Path(os.environ.get("RUSTUP_HOME") or Path.home() / ".rustup")


def remapped_prefixes() -> list[tuple[Path, str]]:
    """Build-machine directories that compiled paths may name, and their neutral names.

    rustc applies the last matching remapping, so the checkout (most specific) comes last.
    """
    return [(rustup_home(), "/rustup"), (cargo_home(), "/cargo"), (REPO_ROOT, "/control-tv")]


def encoded_rustflags(environment: Mapping[str, str], prefixes: Sequence[tuple[Path, str]]) -> str:
    """`CARGO_ENCODED_RUSTFLAGS` keeping the caller's flags and adding the remappings."""
    if environment.get("CARGO_ENCODED_RUSTFLAGS"):
        flags = environment["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")
    else:
        flags = environment.get("RUSTFLAGS", "").split()
    flags += [f"--remap-path-prefix={path}={neutral}" for path, neutral in prefixes]
    return "\x1f".join(flags)


def linked_crates(metadata: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Crates compiled into the application binary: normal dependencies, no proc-macros."""
    packages = {package["id"]: package for package in metadata["packages"]}
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}

    def proc_macro(package_id: str) -> bool:
        return any("proc-macro" in t["kind"] for t in packages[package_id]["targets"])

    seen: set[str] = set()
    pending = [metadata["resolve"]["root"]]
    while pending:
        for dependency in nodes[pending.pop()]["deps"]:
            linked = any(kind["kind"] is None for kind in dependency["dep_kinds"])
            package_id = dependency["pkg"]
            if linked and package_id not in seen and not proc_macro(package_id):
                seen.add(package_id)
                pending.append(package_id)
    return sorted(
        (packages[package_id] for package_id in seen), key=lambda p: (p["name"], p["version"])
    )


def _crate_license_files(package: Mapping[str, Any]) -> list[Path]:
    directory = Path(package["manifest_path"]).parent
    found = {
        path
        for path in directory.iterdir()
        if path.is_file() and path.name.upper().startswith(RUST_LICENSE_HINTS)
    }
    if package.get("license_file"):
        declared = directory / package["license_file"]
        if declared.is_file():
            found.add(declared)
    return sorted(found, key=lambda path: path.name)


def write_rust_notices(
    crates: Iterable[Mapping[str, Any]], std_docs: Path | None, destination: Path
) -> tuple[Path, list[str]]:
    """Copy each linked crate's license files; return the notices and crates without any."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    lines = [
        "Third-party Rust software in the control-TV application",
        "=" * 55,
        "",
        "License texts of the crates compiled into the application binary.",
        "It is an engineering inventory, not a legal review.",
        "",
    ]
    if std_docs is not None:
        std = destination / "rust-std"
        std.mkdir()
        sources = [std_docs / "COPYRIGHT-library.html"]
        sources += [std_docs / "licenses" / name for name in ("MIT.txt", "Apache-2.0.txt")]
        for source in sources:
            if source.is_file():
                shutil.copyfile(source, std / source.name)
        lines += ["Rust standard library", "  License: MIT OR Apache-2.0", "  Text: rust-std/", ""]
    without: list[str] = []
    for crate in crates:
        folder = f"{crate['name']}-{crate['version']}"
        files = _crate_license_files(crate)
        lines += [f"{crate['name']} {crate['version']}", f"  License: {crate.get('license')}"]
        if files:
            (destination / folder).mkdir()
            for source in files:
                shutil.copyfile(source, destination / folder / source.name)
                lines.append(f"  Text: {folder}/{source.name}")
        else:
            lines.append("  Text: MISSING (the published crate ships no license file)")
            without.append(f"{crate['name']} {crate['version']} ({crate.get('license')})")
        lines.append("")
    notices = destination / bridge.NOTICES_FILE
    notices.write_text("\n".join(lines), encoding="utf-8")
    return notices, without


def cargo_metadata() -> Mapping[str, Any]:
    host = (
        subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True)
        .stdout.split("host: ")[1]
        .split()[0]
    )
    completed = subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--locked", "--filter-platform", host],
        cwd=SRC_TAURI,
        capture_output=True,
        text=True,
        check=True,
    )
    metadata: Mapping[str, Any] = json.loads(completed.stdout)
    return metadata


def std_docs() -> Path | None:
    sysroot = subprocess.run(
        ["rustc", "--print", "sysroot"], capture_output=True, text=True, check=True
    ).stdout.strip()
    docs = Path(sysroot) / "share" / "doc" / "rust"
    return docs if docs.is_dir() else None


def deb_entries(deb: Path) -> dict[str, int]:
    """Every file in the package with its mode (`dpkg-deb -c`), directories excluded."""
    listing = subprocess.run(
        ["dpkg-deb", "-c", str(deb)], capture_output=True, text=True, check=True
    ).stdout
    return parse_deb_listing(listing)


def parse_deb_listing(listing: str) -> dict[str, int]:
    entries: dict[str, int] = {}
    for line in listing.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 6 or fields[0].startswith("d"):
            continue
        permissions, name = fields[0], fields[5].split(" -> ")[0]
        mode = 0
        for bit, flag in zip(permissions[1:10], _MODE_BITS, strict=True):
            mode |= flag if bit != "-" else 0
        entries[name.removeprefix("./")] = mode
    return entries


def check_deb_contents(entries: Mapping[str, int]) -> None:
    for name, mode in REQUIRED_ENTRIES.items():
        if name not in entries:
            raise RuntimeError(f"the package does not contain {name}")
        if mode is not None and entries[name] != mode:
            raise RuntimeError(f"{name} has mode {entries[name]:04o}, expected {mode:04o}")
    libpython = [
        n for n in entries if n.startswith(f"{INSTALL_ROOT}/python-bridge/_internal/libpython")
    ]
    if not libpython:
        raise RuntimeError("the package does not embed libpython")


def write_checksums(files: Sequence[Path], destination: Path) -> Path:
    """`sha256sum`-compatible lines, sorted by name, for files in the destination directory."""
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
        for path in sorted(files, key=lambda p: p.name)
    ]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def release() -> Path:
    version = bridge.project_version()
    if (
        not (bridge.OUTPUT_DIR / bridge.executable_name()).is_file()
        or not bridge.INVENTORY.is_file()
    ):
        raise RuntimeError(
            "the frozen bridge is missing; run `python3 scripts/dev.py bridge-build`"
        )

    notices, without = write_rust_notices(
        linked_crates(cargo_metadata()), std_docs(), RUST_NOTICES_DIR
    )
    print(f"wrote {notices} ({len(without)} crates ship no license file)")

    environment = dict(os.environ)
    environment["CARGO_ENCODED_RUSTFLAGS"] = encoded_rustflags(environment, remapped_prefixes())
    environment.pop("RUSTFLAGS", None)
    subprocess.run(
        [
            "npx",
            "--prefix",
            "ui",
            "tauri",
            "build",
            "--config",
            str(RELEASE_CONFIG),
            "--bundles",
            "deb",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
    )
    built = sorted(DEB_DIR.glob(f"{APPLICATION}_{version}_*.deb"))
    if len(built) != 1:
        raise RuntimeError(
            f"expected one {APPLICATION} {version} package in {DEB_DIR}, found {built}"
        )
    deb = built[0]

    check_deb_contents(deb_entries(deb))
    with tempfile.TemporaryDirectory(prefix="control-tv-deb-") as scratch:
        subprocess.run(["dpkg-deb", "-x", str(deb), scratch], check=True)
        needles = [str(REPO_ROOT), str(Path.home()), str(cargo_home()), str(rustup_home())]
        installed_bridge = Path(scratch) / INSTALL_ROOT / "python-bridge" / bridge.NAME
        leaks = bridge.find_build_paths(Path(scratch), needles, [installed_bridge])
    if leaks:
        raise RuntimeError("the package names build-machine paths:\n  " + "\n  ".join(leaks))

    shipped = DIST / deb.name
    shutil.copyfile(deb, shipped)
    write_checksums([shipped, bridge.INVENTORY], CHECKSUMS)
    return shipped


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("usage: release_linux.py", file=sys.stderr)
        return 2
    try:
        print(release())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
