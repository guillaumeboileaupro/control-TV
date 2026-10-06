#!/usr/bin/env python3
"""Prepare the Python packages the Android app embeds, for an offline Chaquopy install.

Run with the development environment's interpreter after `uv sync --locked --group android`
(`python3 scripts/dev.py android-python` does both). It leaves in `dist/android-python/`:

- `wheels/`: every package of the control layer's locked runtime dependencies as a
  pure-Python wheel, plus control-TV's own wheel;
- `requirements.txt`: the exact versions, which Chaquopy installs with `--no-index` from
  `wheels/` only, so nothing is resolved or downloaded while the APK is built.

Every downloaded file is checked against its SHA-256 in `uv.lock`. zeroconf publishes no
pure-Python wheel; its locked sdist is built with its optional Cython extensions disabled
(`SKIP_CYTHON=1`), checked to contain no compiled module, and retagged `py3-none-any`.
A dependency that has no pure-Python form fails the preparation: nothing native is
silently substituted.

Network boundary: the only network access is `uv sync --locked --group android` (run by
`dev.py android-python`, hash-checked by uv) and `download`, which fetches exactly the
locked URLs and refuses a hash mismatch. The two wheel builds (zeroconf and control-tv)
run offline (`pip_wheel_command`: no index, no dependency resolution, no build isolation,
no shared cache), with the build backends installed from the `android` group
(`BUILD_BACKENDS`, checked against `uv.lock` before building), so no build dependency is
resolved at build time. The builds and the retag run with a fixed `SOURCE_DATE_EPOCH`
(`ZIP_EPOCH`, unless the caller sets one), which made two consecutive preparations on the
same machine produce byte-identical wheels; reproducibility across machines or toolchains
is not measured.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK = REPO_ROOT / "uv.lock"
OUTPUT = REPO_ROOT / "dist" / "android-python"
WHEELS = OUTPUT / "wheels"
REQUIREMENTS = OUTPUT / "requirements.txt"
PROJECT = "control-tv"
# Built from source as pure Python: the only runtime dependency without a pure wheel.
FROM_SDIST = {"zeroconf"}
COMPILED_SUFFIXES = (".so", ".pyd", ".dll", ".dylib")
# The build backends of the two wheels built here, installed from the `android` group:
# setuptools for control-tv (and for the distutils that zeroconf's build script imports),
# poetry-core for zeroconf. Cython is not needed: SKIP_CYTHON returns before it is imported.
BUILD_BACKENDS = ("setuptools", "poetry-core")
# Timestamp written into the built wheels' zip entries (1980-01-01, the earliest zip date),
# so a rebuild of the same inputs does not differ by build time.
ZIP_EPOCH = "315532800"


def normalized(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def runtime_packages(
    lock: Mapping[str, Any], project: str = PROJECT
) -> dict[str, Mapping[str, Any]]:
    """The locked packages the project needs at run time (its dependencies, transitively)."""
    packages = {normalized(p["name"]): p for p in lock["package"]}
    needed: dict[str, Mapping[str, Any]] = {}
    pending = [normalized(d["name"]) for d in packages[normalized(project)].get("dependencies", [])]
    while pending:
        name = pending.pop()
        if name in needed:
            continue
        needed[name] = packages[name]
        pending += [normalized(d["name"]) for d in packages[name].get("dependencies", [])]
    return dict(sorted(needed.items()))


def pure_wheel(package: Mapping[str, Any]) -> Mapping[str, Any] | None:
    wheels: list[Mapping[str, Any]] = package.get("wheels", [])
    for wheel in wheels:
        if str(wheel["url"]).endswith("-none-any.whl"):
            return wheel
    return None


def download(url: str, sha256: str, destination: Path) -> Path:
    """Download `url` to `destination`, refusing it unless its SHA-256 matches."""
    with urllib.request.urlopen(url) as response:
        data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise RuntimeError(f"{url}: SHA-256 {actual} does not match uv.lock {sha256}")
    destination.write_bytes(data)
    return destination


def expected_hash(entry: Mapping[str, Any]) -> str:
    algorithm, _, digest = str(entry["hash"]).partition(":")
    if algorithm != "sha256" or not digest:
        raise RuntimeError(f"uv.lock entry without a SHA-256: {entry}")
    return digest


def compiled_members(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        return [name for name in archive.namelist() if name.endswith(COMPILED_SUFFIXES)]


def pip_wheel_command(source: Path, out_dir: Path) -> list[str]:
    """Build one wheel offline with the build backends already installed in this environment.

    `--isolated` ignores pip's environment variables and configuration files, `--no-index`
    forbids any index, `--no-deps` resolves nothing, `--no-build-isolation` uses the
    installed backends instead of fetching the source's `build-system.requires`, and
    `--no-cache-dir` neither reuses nor stores a wheel in pip's shared user cache.
    """
    return [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--isolated",
        "--no-index",
        "--no-deps",
        "--no-build-isolation",
        "--no-cache-dir",
        "--wheel-dir",
        str(out_dir),
        str(source),
    ]


def check_build_backends(
    lock: Mapping[str, Any],
    installed: Callable[[str], str] = importlib.metadata.version,
) -> None:
    """Refuse to build unless every build backend is installed at its `uv.lock` version."""
    locked = {normalized(p["name"]): str(p["version"]) for p in lock["package"]}
    for name in BUILD_BACKENDS:
        if name not in locked:
            raise RuntimeError(f"build backend {name} is not in uv.lock")
        try:
            version = installed(name)
        except importlib.metadata.PackageNotFoundError as error:
            raise RuntimeError(
                f"build backend {name} is not installed: run `uv sync --locked --group android`"
            ) from error
        if version != locked[name]:
            raise RuntimeError(f"build backend {name} {version} is not the locked {locked[name]}")


def build_pure_from_sdist(sdist: Path, destination: Path) -> Path:
    """Build `sdist` offline without optional C extensions and retag it as pure Python."""
    with tempfile.TemporaryDirectory(prefix="control-tv-sdist-") as scratch:
        environment = {**_environment(), "SKIP_CYTHON": "1"}
        subprocess.run(pip_wheel_command(sdist, Path(scratch)), check=True, env=environment)
        [built] = Path(scratch).glob("*.whl")
        compiled = compiled_members(built)
        if compiled:
            raise RuntimeError(f"{built.name} still contains compiled modules: {compiled}")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "wheel",
                "tags",
                "--remove",
                "--python-tag",
                "py3",
                "--abi-tag",
                "none",
                "--platform-tag",
                "any",
                str(built),
            ],
            check=True,
            env=_environment(),
        )
        [retagged] = Path(scratch).glob("*-py3-none-any.whl")
        return Path(shutil.copy2(retagged, destination / retagged.name))


def build_project_wheel(destination: Path) -> Path:
    before = set(destination.glob("*.whl"))
    subprocess.run(
        pip_wheel_command(REPO_ROOT, destination),
        cwd=REPO_ROOT,
        check=True,
        env=_environment(),
    )
    [built] = set(destination.glob("*.whl")) - before
    if not built.name.endswith("-py3-none-any.whl") or compiled_members(built):
        raise RuntimeError(f"{built.name} is not a pure-Python wheel")
    return built


def _environment() -> dict[str, str]:
    import os

    return {"SOURCE_DATE_EPOCH": ZIP_EPOCH, **os.environ}


def requirements_lines(
    packages: Mapping[str, Mapping[str, Any]], project_version: str
) -> list[str]:
    lines = [f"{PROJECT}=={project_version}"]
    lines += [f"{package['name']}=={package['version']}" for package in packages.values()]
    return lines


def prepare(output: Path = OUTPUT) -> Path:
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    check_build_backends(lock)
    packages = runtime_packages(lock)
    if output.exists():
        shutil.rmtree(output)
    wheels = output / "wheels"
    wheels.mkdir(parents=True)

    for name, package in packages.items():
        if name in FROM_SDIST:
            sdist = package.get("sdist")
            if sdist is None:
                raise RuntimeError(f"{name} has no locked sdist to build from")
            with tempfile.TemporaryDirectory(prefix="control-tv-sdist-") as scratch:
                archive = download(
                    sdist["url"],
                    expected_hash(sdist),
                    Path(scratch) / sdist["url"].rsplit("/", 1)[1],
                )
                build_pure_from_sdist(archive, wheels)
            continue
        wheel = pure_wheel(package)
        if wheel is None:
            raise RuntimeError(f"{name} {package['version']} has no pure-Python wheel in uv.lock")
        download(wheel["url"], expected_hash(wheel), wheels / wheel["url"].rsplit("/", 1)[1])

    project = build_project_wheel(wheels)
    project_version = project.name.split("-")[1]
    (output / "requirements.txt").write_text(
        "\n".join(requirements_lines(packages, project_version)) + "\n", encoding="utf-8"
    )
    return output


def main(argv: Sequence[str] | None = None) -> int:
    if list(sys.argv[1:] if argv is None else argv):
        print("usage: android_python.py", file=sys.stderr)
        return 2
    try:
        print(prepare())
    except (OSError, RuntimeError, subprocess.SubprocessError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
