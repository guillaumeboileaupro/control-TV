#!/usr/bin/env python3
"""Build and smoke-test the frozen Python bridge that release packages ship.

Run with the development environment's interpreter after `uv sync --locked --group packaging`
(`python3 scripts/dev.py bridge-build` and `bridge-smoke` do both):

  build  freeze `packaging/bridge_entry.py` with PyInstaller (`--onedir`) into
         `dist/python-bridge/`, then refuse the result if it still names a build-machine path
  smoke  copy `dist/python-bridge/` outside the repository and talk to it with an empty
         environment: `ping`, a `get_status` that needs no network, then end of input

All PyInstaller state (configuration, cache, work and staging directories) stays under
`packaging/.pyinstaller/`. The build never passes `--clean`, so no shared or global cache is
ever touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable, Sequence
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
NAME = "control-tv-bridge"

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


def executable_name(windows: bool = os.name == "nt") -> str:
    return f"{NAME}.exe" if windows else NAME


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
        " 'origin': spec.origin if spec else None}))\n"
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


def _archive_members(path: Path) -> Iterable[tuple[str, bytes]]:
    """Every member of a zip or PyInstaller archive at `path`, decompressed; none otherwise."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                yield name, archive.read(name)
        return
    try:
        from PyInstaller.archive.readers import CArchiveReader

        carchive = CArchiveReader(str(path))
    except Exception:
        return
    for name in carchive.toc:
        try:
            yield name, carchive.extract(name)
        except Exception:
            continue
    if "PYZ.pyz" in carchive.toc:
        pyz = carchive.open_embedded_archive("PYZ.pyz")
        for name in pyz.toc:
            data = pyz.extract(name, raw=True)
            if data is not None:
                yield f"PYZ:{name}", data


def find_build_paths(root: Path, needles: Iterable[str]) -> list[str]:
    """Where a build-machine path still appears in `root`, inside archives included."""
    patterns = [needle.encode() for needle in needles if needle]
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        blobs = [(relative, path.read_bytes())]
        blobs += [(f"{relative}!{name}", data) for name, data in _archive_members(path)]
        for where, data in blobs:
            found += [f"{where}: {p.decode()}" for p in patterns if p in data]
    return found


def build() -> Path:
    python = venv_python()
    if not python.is_file():
        raise RuntimeError(".venv is missing; run `python3 scripts/dev.py bridge-build`")

    for directory in (CONFIG_DIR, WORK_DIR, STAGING_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    _remove_generated(STAGING_DIR / NAME)
    _remove_generated(SANITIZED_DIR)

    facts = build_python_facts(python)
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
    leaks = find_build_paths(built, [str(REPO_ROOT), str(Path.home()), prefix])
    if leaks:
        raise RuntimeError("the frozen bridge names build-machine paths:\n  " + "\n  ".join(leaks))

    OUTPUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    _remove_generated(OUTPUT_DIR)
    shutil.copytree(built, OUTPUT_DIR, symlinks=True)
    return OUTPUT_DIR


def check_smoke_output(stdout: str) -> None:
    """The frozen bridge answered both smoke requests exactly as the shared layer defines."""
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    if len(responses) != len(SMOKE_REQUESTS):
        raise RuntimeError(f"expected {len(SMOKE_REQUESTS)} responses, got: {stdout!r}")
    ping, status = responses
    if ping.get("id") != 1 or ping.get("result", {}).get("status") != "ready":
        raise RuntimeError(f"unexpected ping response: {ping}")
    if status.get("id") != 2 or status.get("error", {}).get("code") != "device_not_found":
        raise RuntimeError(f"unexpected get_status response: {status}")


def isolation_wrapper(hidden: Sequence[Path]) -> list[str] | None:
    """A Linux user/mount namespace in which `hidden` directories look empty, if available.

    Proves the bridge needs neither the checkout nor the build interpreter; returns None where
    unprivileged namespaces are unavailable, so the smoke still runs, without that proof.
    """
    unshare, mount = shutil.which("unshare"), shutil.which("mount")
    if sys.platform != "linux" or unshare is None or mount is None:
        return None
    probe = subprocess.run(
        [unshare, "--user", "--map-root-user", "--mount", "true"], capture_output=True
    )
    if probe.returncode != 0:
        return None
    mounts = " && ".join(f"{mount} -t tmpfs none '{path}'" for path in hidden if path.is_dir())
    script = f'{mounts} && exec "$@"' if mounts else 'exec "$@"'
    shell = shutil.which("sh") or "/bin/sh"
    return [unshare, "--user", "--map-root-user", "--mount", shell, "-c", script, "sh"]


def smoke(source: Path = OUTPUT_DIR) -> str:
    executable = source / executable_name()
    if not executable.is_file():
        raise RuntimeError(f"{executable} is missing; run `python3 scripts/dev.py bridge-build`")
    requests = "".join(json.dumps(request) + "\n" for request in SMOKE_REQUESTS)
    prefix = build_python_facts(venv_python())["prefix"]
    with tempfile.TemporaryDirectory(prefix="control-tv-bridge-smoke-") as scratch:
        copy = Path(scratch) / "python-bridge"
        shutil.copytree(source, copy, symlinks=True)
        hidden = [REPO_ROOT] + ([Path(prefix)] if prefix else [])
        wrapper = isolation_wrapper(hidden) or []
        completed = subprocess.run(
            [*wrapper, str(copy / executable_name())],
            input=requests,
            capture_output=True,
            text=True,
            cwd=scratch,
            env={"PATH": os.devnull},
            timeout=SMOKE_TIMEOUT_SECONDS,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"the frozen bridge exited with {completed.returncode}: {completed.stderr.strip()}"
        )
    check_smoke_output(completed.stdout)
    isolation = "checkout and build Python hidden" if wrapper else "no namespace isolation"
    return f"frozen bridge answered outside the checkout ({isolation}) and exited on end of input"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in (["build"], ["smoke"]):
        print("usage: build_bridge.py build|smoke", file=sys.stderr)
        return 2
    try:
        print(build() if args == ["build"] else smoke())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
