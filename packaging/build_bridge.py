#!/usr/bin/env python3
"""Build and smoke-test the frozen Python bridge that release packages ship.

Run with the development environment's interpreter after
`uv sync --locked --group packaging --python <release version>` (`python3 scripts/dev.py
bridge-build` and `bridge-smoke` do both). Releases embed exactly the CPython version in
`packaging/release-python-version`; the build refuses any other interpreter.

  build  freeze `packaging/bridge_entry.py` with PyInstaller (`--onedir`) into
         `dist/python-bridge/` with its third-party license notices under `licenses/`, refuse
         the result if it still names a build-machine path, and write a deterministic
         inventory (SHA-256, size, mode, path) to `dist/python-bridge.inventory.tsv`
  smoke  copy `dist/python-bridge/` outside the repository and talk to it with an empty
         environment: `ping` (which must report this checkout's version), a `get_status` that
         needs no network, then end of input. With `--strict` (releases, CI) it runs inside a
         Linux user/mount namespace in which the checkout and the build Python are empty, and
         fails when that isolation cannot be set up or verified; without it, the isolation is
         used when available and the result says whether it was

All PyInstaller state (configuration, cache, work and staging directories) stays under
`packaging/.pyinstaller/`. The build never passes `--clean`, so no shared or global cache is
ever touched.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGING_ROOT = REPO_ROOT / "packaging"
PYINSTALLER_ROOT = PACKAGING_ROOT / ".pyinstaller"
CONFIG_DIR = PYINSTALLER_ROOT / "config"
WORK_DIR = PYINSTALLER_ROOT / "work"
STAGING_DIR = PYINSTALLER_ROOT / "dist"
SANITIZED_DIR = PYINSTALLER_ROOT / "sanitized"
ENTRY_POINT = PACKAGING_ROOT / "bridge_entry.py"
OUTPUT_DIR = REPO_ROOT / "dist" / "python-bridge"
INVENTORY = REPO_ROOT / "dist" / "python-bridge.inventory.tsv"
VERSION_FILE = REPO_ROOT / "src" / "control_tv" / "__init__.py"
RELEASE_PYTHON_FILE = PACKAGING_ROOT / "release-python-version"
NAME = "control-tv-bridge"
LICENSES_DIR = "licenses"
NOTICES_FILE = "THIRD_PARTY_NOTICES.txt"
LICENSE_FILE_HINTS = ("LICEN", "COPYING", "NOTICE", "AUTHORS")

# Collected only because PyInstaller's setuptools hook pulls them in; the bridge imports none of
# them (132 of 525 frozen modules before they were excluded).
EXCLUDED_MODULES = ("setuptools", "pkg_resources", "_distutils_hack")

# What the bundled `_sysconfigdata` module reports as the Python installation prefix. uv writes
# the build machine's own install path there (a home directory, so a user name); the frozen
# bridge never reads those paths, so they are replaced by the neutral prefix the
# python-build-standalone distributions are built with.
NEUTRAL_PREFIX = "/install"

SMOKE_REQUESTS = (
    {"id": 1, "method": "ping", "params": {}},
    # Rejected by the shared control layer before any network access.
    {"id": 2, "method": "get_status", "params": {"deviceId": "never-discovered"}},
)
SMOKE_TIMEOUT_SECONDS = 30

# Libraries python-build-standalone links statically into the bundled `libpython` (identified
# from the interpreter's built-in modules and version strings). uv's Python install ships no
# license text for them, so the notices name them and say their texts are still missing.
STATICALLY_LINKED_INTO_LIBPYTHON = (
    "OpenSSL (Apache-2.0)",
    "SQLite (public domain)",
    "zlib (Zlib)",
    "libedit (BSD-3-Clause)",
    "libffi (MIT)",
    "xz/liblzma (0BSD)",
    "bzip2 (bzip2-1.0.6)",
    "ncurses/terminfo (X11)",
    "mpdecimal (BSD-2-Clause)",
    "expat (MIT)",
    "util-linux libuuid (BSD-3-Clause)",
    "HACL* (MIT or Apache-2.0)",
)

# Debian packages whose shared libraries PyInstaller copies from the build system, with the
# copyright file that carries their license (GPL-3.0 with the GCC Runtime Library Exception).
SYSTEM_RUNTIME_COPYRIGHTS = {
    "libstdc++.so.6": Path("/usr/share/doc/libstdc++6/copyright"),
    "libgcc_s.so.1": Path("/usr/share/doc/libgcc-s1/copyright"),
}


def executable_name(windows: bool = os.name == "nt") -> str:
    return f"{NAME}.exe" if windows else NAME


def project_version(version_file: Path = VERSION_FILE) -> str:
    """The version this checkout declares in `control_tv.__version__`."""
    match = re.search(r'^__version__ = "([^"]+)"$', version_file.read_text(encoding="utf-8"), re.M)
    if match is None:
        raise RuntimeError(f"{version_file} declares no __version__")
    return match.group(1)


def release_python_version(path: Path = RELEASE_PYTHON_FILE) -> str:
    """The exact CPython version (major.minor.patch) release bridges embed."""
    version = path.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise RuntimeError(f"{path} must hold one exact CPython version, not {version!r}")
    return version


def venv_python(root: Path = REPO_ROOT) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return root / ".venv" / scripts / executable


def _remove_generated(path: Path) -> None:
    """Remove one known build output, never an arbitrary caller-supplied path."""
    resolved_root = REPO_ROOT.resolve()
    resolved = path.resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise RuntimeError(f"refusing to remove non-project path: {resolved}")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def build_python_facts(python: Path) -> dict[str, str | None]:
    """The build interpreter's install prefix and `_sysconfigdata` module, if it has one."""
    script = (
        "import importlib.util, json, sys, sysconfig\n"
        "get = getattr(sysconfig, '_get_sysconfigdata_name', None)\n"
        "name = get() if get else None\n"
        "spec = importlib.util.find_spec(name) if name else None\n"
        "print(json.dumps({'prefix': sys.base_prefix, 'name': name if spec else None,"
        " 'origin': spec.origin if spec else None,"
        " 'version': '.'.join(map(str, sys.version_info[:3]))}))\n"
    )
    completed = subprocess.run(
        [str(python), "-c", script], capture_output=True, text=True, check=True
    )
    facts: dict[str, str | None] = json.loads(completed.stdout)
    return facts


def write_sanitized_sysconfigdata(origin: Path, name: str, prefix: str, destination: Path) -> Path:
    """Copy the `_sysconfigdata` module with the build install prefix made neutral.

    The copy shadows the original during analysis (`--paths`), so it is the one frozen.
    """
    source = origin.read_text(encoding="utf-8")
    if not prefix or prefix not in source:
        raise RuntimeError(f"{origin} does not mention the build prefix; nothing to neutralize")
    destination.mkdir(parents=True, exist_ok=True)
    sanitized = destination / f"{name}.py"
    sanitized.write_text(source.replace(prefix, NEUTRAL_PREFIX), encoding="utf-8")
    return sanitized


def pyinstaller_command(python: Path, extra_paths: Sequence[Path] = ()) -> list[str]:
    command = [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--name",
        NAME,
        "--distpath",
        str(STAGING_DIR),
        "--workpath",
        str(WORK_DIR),
        "--specpath",
        str(PYINSTALLER_ROOT),
    ]
    for path in extra_paths:
        command += ["--paths", str(path)]
    for module in EXCLUDED_MODULES:
        command += ["--exclude-module", module]
    command.append(str(ENTRY_POINT))
    return command


def pyinstaller_environment(base: dict[str, str]) -> dict[str, str]:
    environment = dict(base)
    environment["PYINSTALLER_CONFIG_DIR"] = str(CONFIG_DIR)
    # Analysis must see only the locked environment, never a caller's extra import path.
    environment.pop("PYTHONPATH", None)
    return environment


def _zip_members(path: Path) -> Iterator[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            yield name, archive.read(name)


def pyinstaller_members(executable: Path) -> Iterator[tuple[str, bytes]]:
    """Every member of the PyInstaller archive in `executable`, its PYZ modules included.

    Fail-closed: an executable that cannot be opened as a PyInstaller archive, a member that
    cannot be extracted, a missing PYZ, or a PYZ entry without data (other than a namespace
    package, which has no code) raises instead of being skipped, so a partly inspected
    archive can never pass a path scan or yield an incomplete license inventory.
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
        from PyInstaller.loader.pyimod01_archive import PYZ_ITEM_NSPKG

        carchive = CArchiveReader(str(executable))
    except Exception as error:
        raise RuntimeError(
            f"{executable} cannot be read as a PyInstaller archive: {error}"
        ) from error
    for name in carchive.toc:
        try:
            data = carchive.extract(name)
        except Exception as error:
            raise RuntimeError(f"cannot extract {name!r} from {executable}: {error}") from error
        if data is None:
            raise RuntimeError(f"{name!r} in {executable} has no data")
        yield name, data
    if "PYZ.pyz" not in carchive.toc:
        raise RuntimeError(f"{executable} has no PYZ archive")
    try:
        pyz = carchive.open_embedded_archive("PYZ.pyz")
    except Exception as error:
        raise RuntimeError(f"cannot open the PYZ archive of {executable}: {error}") from error
    for name, entry in pyz.toc.items():
        try:
            data = pyz.extract(name, raw=True)
        except Exception as error:
            raise RuntimeError(f"cannot extract PYZ module {name!r}: {error}") from error
        if data is None:
            if entry[0] == PYZ_ITEM_NSPKG:
                continue
            raise RuntimeError(f"PYZ module {name!r} in {executable} has no data")
        yield f"PYZ:{name}", data


def find_build_paths(
    root: Path, needles: Iterable[str], pyinstaller_executables: Iterable[Path] = ()
) -> list[str]:
    """Where a build-machine path still appears in `root`, inside archives included.

    Every file is scanned raw and, when it is a zip, member by member. The PyInstaller
    executables named in `pyinstaller_executables` must exist and are scanned member by
    member through `pyinstaller_members`, which fails instead of skipping anything. Members
    are read one at a time, never all held at once.
    """
    patterns = [needle.encode() for needle in needles if needle]
    expected = {path.resolve() for path in pyinstaller_executables}
    for path in expected:
        if not path.is_file():
            raise RuntimeError(f"the PyInstaller executable {path} is missing")
    found: list[str] = []

    def check(where: str, data: bytes) -> None:
        found.extend(f"{where}: {p.decode()}" for p in patterns if p in data)

    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        check(relative, path.read_bytes())
        if path.resolve() in expected:
            for name, data in pyinstaller_members(path):
                check(f"{relative}!{name}", data)
        elif zipfile.is_zipfile(path):
            for name, data in _zip_members(path):
                check(f"{relative}!{name}", data)
    return found


def frozen_top_level_names(bundle: Path) -> set[str]:
    """Top-level module and package names frozen in the bundle (archive and `_internal/`)."""
    names: set[str] = set()
    internal = bundle / "_internal"
    if internal.is_dir():
        names |= {entry.name.split(".")[0] for entry in internal.iterdir()}
    for name, _ in _pyz_members(bundle / executable_name()):
        names.add(name.split(".")[0])
    return names


def _pyz_members(executable: Path) -> Iterable[tuple[str, bytes]]:
    for name, data in pyinstaller_members(executable):
        if name.startswith("PYZ:"):
            yield name[len("PYZ:") :], data


def frozen_distributions(
    bundle: Path, packages: Mapping[str, list[str]] | None = None
) -> list[importlib.metadata.Distribution]:
    """Installed distributions with at least one top-level name frozen in `bundle`."""
    owners = importlib.metadata.packages_distributions() if packages is None else packages
    names = sorted({d for top in frozen_top_level_names(bundle) for d in owners.get(top, [])})
    return [importlib.metadata.distribution(name) for name in names]


def _license_files(distribution: importlib.metadata.Distribution) -> list[Path]:
    files = distribution.files or []
    found = [
        Path(str(distribution.locate_file(f)))
        for f in files
        if any(hint in f.name.upper() for hint in LICENSE_FILE_HINTS)
    ]
    return sorted(found, key=lambda path: path.name)


def _declared_license(distribution: importlib.metadata.Distribution) -> str:
    metadata = distribution.metadata
    expression = metadata.get("License-Expression")
    if expression:
        return str(expression)
    declared = (metadata.get("License") or "").strip().splitlines()
    if declared and len(declared[0]) <= 80:
        return declared[0]
    classifiers = [
        c.split(" :: ")[-1] for c in metadata.get_all("Classifier") or [] if "License" in c
    ]
    return ", ".join(classifiers) or "see the license files"


def write_python_notices(
    bundle: Path,
    distributions: Sequence[importlib.metadata.Distribution],
    cpython_license: Path,
    python_version: str,
    pyinstaller: importlib.metadata.Distribution | None,
) -> Path:
    """Copy every license text the bundle needs under `licenses/` and summarize them."""
    root = bundle / LICENSES_DIR
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    lines = [
        "Third-party software in the control-TV Python bridge",
        "=" * 52,
        "",
        "This directory holds the license texts of the software frozen into the bridge.",
        "It is an engineering inventory, not a legal review.",
        "",
    ]

    def section(title: str, license_name: str, sources: Sequence[Path], folder: str) -> None:
        destination = root / folder
        destination.mkdir()
        copied = []
        for source in sources:
            shutil.copyfile(source, destination / source.name)
            copied.append(f"{folder}/{source.name}")
        lines.extend([f"{title}", f"  License: {license_name}"])
        lines.extend(f"  Text: {name}" for name in copied)
        if not copied:
            lines.append("  Text: MISSING")
        lines.append("")

    section(
        f"CPython {python_version} (libpython, standard library)",
        "PSF-2.0 (Python Software Foundation License)",
        [cpython_license],
        f"CPython-{python_version}",
    )
    lines.append("Libraries statically linked into libpython by python-build-standalone:")
    lines.extend(f"  - {name}" for name in STATICALLY_LINKED_INTO_LIBPYTHON)
    lines.extend(["  Texts: MISSING (not shipped by the uv Python install; still to collect)", ""])
    if pyinstaller is not None:
        section(
            f"PyInstaller {pyinstaller.version} bootloader",
            "GPL-2.0-or-later with the PyInstaller bootloader exception",
            _license_files(pyinstaller),
            f"PyInstaller-{pyinstaller.version}",
        )
    for library, copyright_file in SYSTEM_RUNTIME_COPYRIGHTS.items():
        if (bundle / "_internal" / library).exists():
            section(
                f"{library} (copied from the build system)",
                "GPL-3.0-or-later with the GCC Runtime Library Exception",
                [copyright_file] if copyright_file.is_file() else [],
                library,
            )
    for distribution in distributions:
        name, version = distribution.metadata["Name"], distribution.version
        section(
            f"{name} {version}",
            _declared_license(distribution),
            _license_files(distribution),
            f"{name}-{version}",
        )
    notices = root / NOTICES_FILE
    notices.write_text("\n".join(lines), encoding="utf-8")
    return notices


def inventory_lines(root: Path, title: str | None = None) -> list[str]:
    """One line per file or link under `root`: SHA-256, size, octal mode and relative path."""
    lines = [f"# {title}"] if title else []
    lines.append("# sha256\tsize\tmode\tpath")
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            lines.append(f"-\t-\tlink\t{relative} -> {os.readlink(path)}")
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            mode = stat.S_IMODE(path.stat().st_mode)
            lines.append(f"{digest}\t{path.stat().st_size}\t{mode:04o}\t{relative}")
    return lines


def write_inventory(root: Path, destination: Path, title: str | None = None) -> Path:
    executable = root / executable_name()
    if os.name != "nt" and not os.access(executable, os.X_OK):
        raise RuntimeError(f"{executable} is not executable")
    destination.write_text("\n".join(inventory_lines(root, title)) + "\n", encoding="utf-8")
    return destination


def write_bundle_notices(bundle: Path, prefix: str) -> Path:
    """The notices of this build: its interpreter, PyInstaller and the frozen distributions."""
    python_version = ".".join(map(str, sys.version_info[:3]))
    stdlib = Path(prefix) / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
    return write_python_notices(
        bundle,
        frozen_distributions(bundle),
        stdlib / "LICENSE.txt",
        python_version,
        importlib.metadata.distribution("pyinstaller"),
    )


def pyinstaller_version() -> str:
    return importlib.metadata.version("pyinstaller")


def build() -> Path:
    python = venv_python()
    if not python.is_file():
        raise RuntimeError(".venv is missing; run `python3 scripts/dev.py bridge-build`")

    for directory in (CONFIG_DIR, WORK_DIR, STAGING_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    _remove_generated(STAGING_DIR / NAME)
    _remove_generated(SANITIZED_DIR)

    facts = build_python_facts(python)
    release = release_python_version()
    if facts.get("version") != release:
        raise RuntimeError(
            f"the build environment runs CPython {facts.get('version')}, but releases embed "
            f"{release}; run `python3 scripts/dev.py bridge-build`"
        )
    prefix = facts["prefix"] or ""
    extra_paths: list[Path] = []
    if facts["name"] and facts["origin"]:
        write_sanitized_sysconfigdata(Path(facts["origin"]), facts["name"], prefix, SANITIZED_DIR)
        extra_paths.append(SANITIZED_DIR)

    subprocess.run(
        pyinstaller_command(python, extra_paths),
        cwd=REPO_ROOT,
        env=pyinstaller_environment(dict(os.environ)),
        check=True,
    )

    built = STAGING_DIR / NAME
    if not (built / executable_name()).is_file():
        raise RuntimeError(f"PyInstaller did not produce {built / executable_name()}")
    write_bundle_notices(built, prefix)
    leaks = find_build_paths(
        built, [str(REPO_ROOT), str(Path.home()), prefix], [built / executable_name()]
    )
    if leaks:
        raise RuntimeError("the frozen bridge names build-machine paths:\n  " + "\n  ".join(leaks))

    OUTPUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    _remove_generated(OUTPUT_DIR)
    shutil.copytree(built, OUTPUT_DIR, symlinks=True)
    pyinstaller = pyinstaller_version()
    write_inventory(
        OUTPUT_DIR,
        INVENTORY,
        f"{NAME} {project_version()}, CPython {release}, PyInstaller {pyinstaller}",
    )
    return OUTPUT_DIR


def check_smoke_output(stdout: str, version: str) -> None:
    """The frozen bridge answered both smoke requests exactly as the shared layer defines."""
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    if len(responses) != len(SMOKE_REQUESTS):
        raise RuntimeError(f"expected {len(SMOKE_REQUESTS)} responses, got: {stdout!r}")
    ping, status = responses
    if ping.get("id") != 1 or ping.get("result", {}).get("status") != "ready":
        raise RuntimeError(f"unexpected ping response: {ping}")
    if ping["result"].get("controlTvVersion") != version:
        raise RuntimeError(
            f"the frozen bridge reports {ping['result']}, expected version {version}"
        )
    if status.get("id") != 2 or status.get("error", {}).get("code") != "device_not_found":
        raise RuntimeError(f"unexpected get_status response: {status}")


ISOLATION_FAILED = 97


def isolation_wrapper(hidden: Sequence[Path]) -> list[str] | None:
    """A Linux user/mount namespace in which `hidden` directories look empty, if available.

    Proves the bridge needs neither the checkout nor the build interpreter: each directory is
    covered by an empty mount, then checked to be empty from inside the namespace (exit
    `ISOLATION_FAILED` otherwise) before the bridge starts. Returns None where unprivileged
    namespaces are unavailable.
    """
    unshare, mount = shutil.which("unshare"), shutil.which("mount")
    if sys.platform != "linux" or unshare is None or mount is None:
        return None
    probe = subprocess.run(
        [unshare, "--user", "--map-root-user", "--mount", "true"], capture_output=True
    )
    if probe.returncode != 0:
        return None
    directories = [path for path in hidden if path.is_dir()]
    steps = [f"{mount} -t tmpfs none '{path}'" for path in directories]
    # Shell built-ins only: the bridge environment has no PATH. A visible entry, hidden or
    # not, means the isolation did not take effect.
    steps += [
        f"for entry in '{path}'/* '{path}'/.[!.]*; do "
        f'[ -e "$entry" ] && exit {ISOLATION_FAILED}; done; true'
        for path in directories
    ]
    script = " && ".join([*steps, 'exec "$@"'])
    shell = shutil.which("sh") or "/bin/sh"
    return [unshare, "--user", "--map-root-user", "--mount", shell, "-c", script, "sh"]


def smoke(source: Path = OUTPUT_DIR, *, strict: bool = False) -> str:
    executable = source / executable_name()
    if not executable.is_file():
        raise RuntimeError(f"{executable} is missing; run `python3 scripts/dev.py bridge-build`")
    requests = "".join(json.dumps(request) + "\n" for request in SMOKE_REQUESTS)
    prefix = build_python_facts(venv_python())["prefix"]
    hidden = [REPO_ROOT] + ([Path(prefix)] if prefix else [])
    wrapper = isolation_wrapper(hidden)
    if wrapper is None and strict:
        raise RuntimeError(
            "the strict smoke needs a Linux user/mount namespace (unshare) to hide the "
            "checkout and the build Python, and none could be created"
        )
    with tempfile.TemporaryDirectory(prefix="control-tv-bridge-smoke-") as scratch:
        copy = Path(scratch) / "python-bridge"
        shutil.copytree(source, copy, symlinks=True)
        completed = subprocess.run(
            [*(wrapper or []), str(copy / executable_name())],
            input=requests,
            capture_output=True,
            text=True,
            cwd=scratch,
            env={"PATH": os.devnull},
            timeout=SMOKE_TIMEOUT_SECONDS,
        )
    if wrapper is not None and completed.returncode == ISOLATION_FAILED:
        raise RuntimeError("the checkout or the build Python was still visible in the namespace")
    if completed.returncode != 0:
        raise RuntimeError(
            f"the frozen bridge exited with {completed.returncode}: {completed.stderr.strip()}"
        )
    version = project_version()
    check_smoke_output(completed.stdout, version)
    if wrapper is None:
        isolation = "no namespace isolation"
    else:
        isolation = "isolated: " + ", ".join(f"{path} empty" for path in hidden if path.is_dir())
    return (
        f"frozen bridge {version} answered outside the checkout ({isolation}) "
        "and exited on end of input"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in (["build"], ["smoke"], ["smoke", "--strict"]):
        print("usage: build_bridge.py build | smoke [--strict]", file=sys.stderr)
        return 2
    try:
        print(build() if args == ["build"] else smoke(strict="--strict" in args))
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
