#!/usr/bin/env python3
"""Select the heavy CI jobs required by a pull request's changed files.

Pushes keep the complete release validation.  Pull requests may skip platform builds only when
their files cannot affect that platform; unknown paths deliberately select every heavy job.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class Scope:
    tauri: bool
    linux_release: bool
    android: bool


ALL = Scope(tauri=True, linux_release=True, android=True)
FAST_ONLY = Scope(tauri=False, linux_release=False, android=False)

_ROOT_DOCUMENTS = {
    "ARCHITECTURE.md",
    "DEVELOPMENT_CHECKLIST.md",
    "DEVELOPMENT_PLAN.md",
    "README.md",
    "TODO.md",
}
_LINUX_PACKAGING_TESTS = {
    "tests/test_package.py",
    "tests/test_packaging_build.py",
    "tests/test_release_linux.py",
}


def _under(path: PurePosixPath, directory: str) -> bool:
    return bool(path.parts) and path.parts[0] == directory


def select_scope(paths: Iterable[str], *, event: str) -> Scope:
    """Return the platform jobs needed for ``paths``; uncertainty fails safe to all jobs."""
    if event != "pull_request":
        return ALL

    tauri = linux = android = False
    for raw in paths:
        name = raw.strip()
        if not name:
            continue
        path = PurePosixPath(name)

        if _under(path, "docs") or name in _ROOT_DOCUMENTS:
            continue
        if _under(path, "tests"):
            if name in _LINUX_PACKAGING_TESTS:
                linux = True
            continue
        if _under(path, "src-tauri"):
            if len(path.parts) >= 3 and path.parts[1:3] == ("gen", "android"):
                android = True
            else:
                tauri = linux = android = True
            continue
        if _under(path, "android-tv-receiver"):
            android = True
            continue
        if _under(path, "ui"):
            tauri = linux = android = True
            continue
        if _under(path, "src") or _under(path, "packaging") or _under(path, "scripts"):
            # Python is embedded in both packages and exercised across the Rust bridge.
            tauri = linux = android = True
            continue
        if name in {"pyproject.toml", "uv.lock", "package.json", "package-lock.json"}:
            tauri = linux = android = True
            continue
        if (
            _under(path, ".agents")
            or _under(path, ".claude")
            or name
            in {
                "AGENTS.md",
                "CLAUDE.md",
            }
        ):
            continue
        if _under(path, ".github"):
            tauri = linux = android = True
            continue
        return ALL

    return Scope(tauri=tauri, linux_release=linux, android=android)


def _bool(value: bool) -> str:
    return "true" if value else "false"


def write_outputs(scope: Scope, output: Path) -> None:
    output.write_text(
        "\n".join(
            (
                f"tauri={_bool(scope.tauri)}",
                f"linux_release={_bool(scope.linux_release)}",
                f"android={_bool(scope.android)}",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    scope = select_scope(sys.stdin, event=arguments.event)
    write_outputs(scope, arguments.github_output)
    print(
        "heavy CI scope: "
        f"tauri={_bool(scope.tauri)}, linux={_bool(scope.linux_release)}, "
        f"android={_bool(scope.android)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
