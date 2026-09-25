#!/usr/bin/env python3
"""Cross-platform development commands for control-TV.

Usage: python scripts/dev.py <command>

Commands:
  setup        create .venv from uv.lock (exact, reproducible versions)
  lock         regenerate uv.lock from pyproject.toml after a dependency change
  lint         ruff check and ruff format --check
  typecheck    mypy (strict)
  test         pytest
  check        lint, typecheck and test
  disk-usage   free disk space and size of project-owned generated output
  clean        remove disposable generated output (keeps .venv and dist/)
  dist-clean   remove all reproducible project-owned generated output

`setup` and `lock` shell out to `uv` (https://docs.astral.sh/uv/) so that every install
is pinned to the committed lockfile; every other command only needs the resulting
`.venv` and otherwise uses the standard library only.

Cleanup only ever deletes paths from the fixed allowlist below, resolved inside the
repository. Shared caches (Cargo, Gradle, Android SDK/NDK, pip, uv) are never touched.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories scanned for Python bytecode caches. `.venv` is deliberately not listed.
PYCACHE_SCAN_DIRS = ("src", "tests", "scripts")
# Directories scanned (one level deep) for packaging metadata such as `*.egg-info`.
EGG_INFO_SCAN_DIRS = (".", "src")

CLEAN = "clean"
DIST_CLEAN = "dist-clean"


@dataclass(frozen=True)
class Target:
    """A fixed, project-owned generated path relative to the repository root."""

    rel: str
    level: str
    reason: str


TARGETS: tuple[Target, ...] = (
    Target(".pytest_cache", CLEAN, "pytest cache"),
    Target(".mypy_cache", CLEAN, "mypy cache"),
    Target(".ruff_cache", CLEAN, "ruff cache"),
    Target(".coverage", CLEAN, "coverage data"),
    Target("htmlcov", CLEAN, "coverage report"),
    Target("build", CLEAN, "Python packaging staging"),
    Target("tmp", CLEAN, "project scratch directory"),
    Target("temp", CLEAN, "project scratch directory"),
    Target("target", CLEAN, "Rust build output"),
    Target("src-tauri/target", CLEAN, "Tauri/Rust build output"),
    Target("src-tauri/gen/android/.gradle", CLEAN, "Android project-local Gradle state"),
    Target("src-tauri/gen/android/build", CLEAN, "Android build output"),
    Target("src-tauri/gen/android/app/build", CLEAN, "Android app build output"),
    Target(".gradle", CLEAN, "project-local Gradle state"),
    Target(".venv", DIST_CLEAN, "development virtual environment"),
    Target("dist", DIST_CLEAN, "release deliverables (reproducible)"),
)


class UnsafeTargetError(RuntimeError):
    """Raised when a deletion target is not a verified project-owned path."""


def _scan_pycache(root: Path) -> Iterator[Path]:
    for name in PYCACHE_SCAN_DIRS:
        base = root / name
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, dirnames, _ in os.walk(base, followlinks=False):
            for dirname in list(dirnames):
                if dirname == "__pycache__":
                    dirnames.remove(dirname)
                    yield Path(dirpath) / dirname


def _scan_root_files(root: Path) -> Iterator[Path]:
    for base_name in EGG_INFO_SCAN_DIRS:
        base = root / base_name
        if not base.is_dir() or base.is_symlink():
            continue
        yield from sorted(base.glob("*.egg-info"))
    yield from sorted(root.glob("*.log"))


def collect_targets(root: Path, levels: Sequence[str]) -> list[Path]:
    """Return existing allowlisted generated paths for the given cleanup levels."""
    found: list[Path] = []
    for target in TARGETS:
        path = root / target.rel
        if target.level in levels and (path.exists() or path.is_symlink()):
            found.append(path)
    if CLEAN in levels:
        found.extend(_scan_pycache(root))
        found.extend(_scan_root_files(root))
    return found


def verify_project_owned(path: Path, root: Path) -> None:
    """Refuse anything that is not strictly inside `root` (symlinks are judged as links)."""
    root_resolved = root.resolve()
    # Resolve the parent only: a symlink target is removed as a link, never followed.
    candidate = path.parent.resolve() / path.name
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafeTargetError(f"{path} is outside the repository {root_resolved}") from exc
    if not relative.parts:
        raise UnsafeTargetError("refusing to delete the repository root")
    if ".git" in relative.parts:
        raise UnsafeTargetError(f"refusing to delete inside .git: {path}")


def path_size(path: Path) -> int:
    """Size in bytes without following symlinks."""
    try:
        if path.is_symlink() or path.is_file():
            return path.lstat().st_size
        total = 0
        for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
            for name in (*dirnames, *filenames):
                try:
                    total += (Path(dirpath) / name).lstat().st_size
                except OSError:
                    continue
        return total
    except OSError:
        return 0


def format_size(size: float) -> str:
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KiB", "MiB", "GiB"):
        size /= 1024
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
    raise AssertionError("unreachable")


def remove_paths(paths: Sequence[Path], root: Path, *, dry_run: bool = False) -> int:
    """Delete verified project-owned paths and return the number of bytes reclaimed."""
    for path in paths:
        verify_project_owned(path, root)
    reclaimed = 0
    for path in paths:
        size = path_size(path)
        action = "would remove" if dry_run else "remove"
        print(f"  {action}: {path.relative_to(root)}  ({format_size(size)})")
        if dry_run:
            reclaimed += size
            continue
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        reclaimed += size
    return reclaimed


def cmd_disk_usage(root: Path) -> int:
    usage = shutil.disk_usage(root)
    print(
        f"Free disk on {root}: {format_size(usage.free)} of {format_size(usage.total)} "
        f"({usage.used / usage.total:.0%} used)"
    )
    total = 0
    for level in (CLEAN, DIST_CLEAN):
        paths = collect_targets(root, (level,))
        print(f"Generated output removed by `{level}`:")
        if not paths:
            print("  (none)")
        for path in paths:
            size = path_size(path)
            total += size
            print(f"  {path.relative_to(root)!s:40} {format_size(size)}")
    print(f"Total project-owned generated output: {format_size(total)}")
    return 0


def cmd_clean(root: Path, level: str, *, dry_run: bool) -> int:
    levels = (CLEAN, DIST_CLEAN) if level == DIST_CLEAN else (CLEAN,)
    paths = collect_targets(root, levels)
    label = f"`{level}`" + (" (dry run)" if dry_run else "")
    print(f"{label}: {len(paths)} project-owned path(s)")
    reclaimed = remove_paths(paths, root, dry_run=dry_run)
    verb = "Would reclaim" if dry_run else "Reclaimed"
    print(f"{verb} {format_size(reclaimed)}")
    return 0


def venv_python(root: Path) -> Path:
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    exe = "python.exe" if os.name == "nt" else "python"
    return root / ".venv" / scripts_dir / exe


def _run(root: Path, args: Sequence[str]) -> int:
    python = venv_python(root)
    if not python.exists():
        print("error: .venv is missing; run `python scripts/dev.py setup` first", file=sys.stderr)
        return 2
    print("+", " ".join(args))
    return subprocess.call([str(python), *args], cwd=root)


def uv_executable() -> str | None:
    return shutil.which("uv")


def _run_uv(root: Path, args: Sequence[str]) -> int:
    uv = uv_executable()
    if uv is None:
        print(
            "error: uv is required for this command "
            "(see https://docs.astral.sh/uv/getting-started/installation/)",
            file=sys.stderr,
        )
        return 2
    print("+", "uv", " ".join(args))
    return subprocess.call([uv, *args], cwd=root)


def cmd_setup(root: Path) -> int:
    """Create/update .venv to exactly match uv.lock. Fails if the lock is out of date."""
    return _run_uv(root, ["sync", "--locked"])


def cmd_lock(root: Path) -> int:
    """Regenerate uv.lock from pyproject.toml. Run this after changing a dependency."""
    return _run_uv(root, ["lock"])


def cmd_lint(root: Path) -> int:
    code = _run(root, ["-m", "ruff", "check", "."])
    return code or _run(root, ["-m", "ruff", "format", "--check", "."])


def cmd_typecheck(root: Path) -> int:
    return _run(root, ["-m", "mypy"])


def cmd_test(root: Path) -> int:
    return _run(root, ["-m", "pytest"])


def cmd_check(root: Path) -> int:
    for step in (cmd_lint, cmd_typecheck, cmd_test):
        code = step(root)
        if code != 0:
            return code
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev.py", description="control-TV development commands")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "lock", "lint", "typecheck", "test", "check", "disk-usage"):
        sub.add_parser(name)
    for name in (CLEAN, DIST_CLEAN):
        cleaner = sub.add_parser(name)
        cleaner.add_argument("--dry-run", action="store_true", help="list what would be removed")
    return parser


def main(argv: Sequence[str] | None = None, root: Path = REPO_ROOT) -> int:
    args = build_parser().parse_args(argv)
    command: str = args.command
    if command in (CLEAN, DIST_CLEAN):
        return cmd_clean(root, command, dry_run=args.dry_run)
    handlers = {
        "setup": cmd_setup,
        "lock": cmd_lock,
        "lint": cmd_lint,
        "typecheck": cmd_typecheck,
        "test": cmd_test,
        "check": cmd_check,
        "disk-usage": cmd_disk_usage,
    }
    return handlers[command](root)


if __name__ == "__main__":
    sys.exit(main())
