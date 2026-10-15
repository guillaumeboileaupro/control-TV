#!/usr/bin/env python3
"""Cross-platform development commands for control-TV.

Usage: python scripts/dev.py <command>

Commands:
  setup        create .venv from uv.lock (exact, reproducible versions)
  lock         regenerate uv.lock from pyproject.toml after a dependency change
  lint         ruff check and ruff format --check
  typecheck    mypy (strict)
  test         pytest
  coverage     pytest with coverage of src/control_tv, enforcing a minimum
  depcheck     verify every installed package's declared requirements are met
  check        lint, typecheck, test and depcheck (the Python quality gate)
  rust-check   cargo fmt --check, clippy -D warnings and cargo test (src-tauri/)
  ui-check     tsc --noEmit, prettier --check and UI model tests (ui/, after `npm install`)
  npm-audit    npm audit of ui/ (needs the network): a high or critical advisory fails unless
               ui/npm-audit-waivers.json waives it until a date
  python-audit pip-audit of every locked Python package (network): any advisory fails unless
               python-audit-waivers.json waives it until a date
  rust-audit   cargo audit of src-tauri/ (network, needs cargo-audit): any vulnerability fails
               unless src-tauri/cargo-audit-waivers.json waives it; warnings are printed
  bridge-build freeze the Python bridge release packages ship into dist/python-bridge/
  bridge-smoke run that frozen bridge outside the checkout (ping, no network, end of input)
  release-deb  bridge-build, the strict isolated smoke, then the release .deb, its notices and
               dist/SHA256SUMS
  android-python  the hash-checked pure-Python wheels the Android app embeds (dist/android-python/)
  android-apk     android-python, then a debug arm64 APK (Tauri Android + embedded Python)
  disk-usage   free disk space and size of project-owned generated output
  clean        remove disposable generated output (keeps .venv, ui/node_modules and dist/)
  dist-clean   remove all reproducible project-owned generated output

`setup` and `lock` shell out to `uv` (https://docs.astral.sh/uv/) so that every install
is pinned to the committed lockfile; every other Python command only needs the resulting
`.venv` and otherwise uses the standard library only. `rust-check` needs `cargo`
(https://rustup.rs/); `ui-check` needs `npm` and `ui/node_modules` (run `npm install`
in `ui/` first) - neither is installed or invoked by this script. `bridge-build` rebuilds
`.venv` on the exact CPython release bridges embed (`packaging/release-python-version`, which
needs a uv that knows that version) with the locked `packaging` group (PyInstaller); `setup`
removes the group again.

Cleanup only ever deletes paths from the fixed allowlist below, resolved inside the
repository. Shared caches (Cargo, Gradle, Android SDK/NDK, pip, uv) are never touched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories scanned for Python bytecode caches. `.venv` is deliberately not listed.
PYCACHE_SCAN_DIRS = ("src", "tests", "scripts", "packaging")
# Directories scanned (one level deep) for packaging metadata such as `*.egg-info`.
EGG_INFO_SCAN_DIRS = (".", "src")

CLEAN = "clean"
DIST_CLEAN = "dist-clean"
# Measured at 99% when this was introduced (one defensive, since-removed unreachable
# line); leaves headroom for new code without weakening the gate. Raise deliberately,
# never lower it to make a failing build pass.
COVERAGE_MIN = 95


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
    Target("packaging/.pyinstaller", CLEAN, "project-local PyInstaller work and cache"),
    Target("build", CLEAN, "Python packaging staging"),
    Target("tmp", CLEAN, "project scratch directory"),
    Target("temp", CLEAN, "project scratch directory"),
    Target("target", CLEAN, "Rust build output"),
    Target("src-tauri/target", CLEAN, "Tauri/Rust build output"),
    Target("src-tauri/gen/schemas", CLEAN, "Tauri capability schema (auto-generated)"),
    Target("src-tauri/gen/android/.gradle", CLEAN, "Android project-local Gradle state"),
    Target("src-tauri/gen/android/build", CLEAN, "Android build output"),
    Target("src-tauri/gen/android/app/build", CLEAN, "Android app build output"),
    Target("src-tauri/gen/android/buildSrc/build", CLEAN, "Android build plugin output"),
    Target("src-tauri/gen/android/buildSrc/.gradle", CLEAN, "Android build plugin Gradle state"),
    Target("src-tauri/gen/android/app/src/main/jniLibs", CLEAN, "Android Rust libraries (copied)"),
    Target(".gradle", CLEAN, "project-local Gradle state"),
    Target("ui/dist", CLEAN, "frontend build output"),
    Target(".venv", DIST_CLEAN, "development virtual environment"),
    Target("ui/node_modules", DIST_CLEAN, "frontend npm dependencies"),
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
    """Create/update .venv to exactly match uv.lock, with the optional extras: the MCP server
    and the Android TV Remote channel.

    Fails if the lock is out of date. The packaging and Android commands sync their own
    exact environments: the frozen bridge and the Android app with the TV remote extra only.
    """
    return _run_uv(root, ["sync", "--locked", "--extra", "mcp", "--extra", "tvremote"])


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


def cmd_coverage(root: Path) -> int:
    """Coverage of the shared control/domain package only, not of tests or scripts/dev.py."""
    return _run(
        root,
        [
            "-m",
            "pytest",
            "--cov=control_tv",
            "--cov-report=term-missing",
            f"--cov-fail-under={COVERAGE_MIN}",
        ],
    )


def cmd_depcheck(root: Path) -> int:
    """Every installed package's declared requirements are actually satisfied."""
    return _run_uv(root, ["pip", "check"])


def _toml_section_version(path: Path, section: str) -> str | None:
    """The `version = "..."` of one TOML table, read without a TOML library (dev.py also runs
    on interpreters older than tomllib)."""
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line.strip("[]").strip()
        elif current == section and line.startswith("version") and "=" in line:
            key, _, value = line.partition("=")
            if key.strip() == "version":
                return value.strip().strip('"').strip("'")
    return None


def _python_package_version(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__") and "=" in line:
            return line.partition("=")[2].strip().strip('"').strip("'")
    return None


def declared_versions(root: Path) -> dict[str, str | None]:
    """Every place that declares the application version. They must all agree."""
    package = json.loads((root / "ui" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "ui" / "package-lock.json").read_text(encoding="utf-8"))
    tauri = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    versions: dict[str, str | None] = {
        "pyproject.toml [project]": _toml_section_version(root / "pyproject.toml", "project"),
        "src/control_tv/__init__.py __version__": _python_package_version(
            root / "src" / "control_tv" / "__init__.py"
        ),
        "src-tauri/Cargo.toml [package]": _toml_section_version(
            root / "src-tauri" / "Cargo.toml", "package"
        ),
        "ui/package.json": package.get("version"),
        "ui/package-lock.json": lock.get("version"),
        'ui/package-lock.json packages[""]': lock.get("packages", {}).get("", {}).get("version"),
    }
    # Without its own "version", Tauri uses the Cargo package version: nothing to compare.
    if "version" in tauri:
        versions["src-tauri/tauri.conf.json"] = tauri["version"]
    return versions


def cmd_version_check(root: Path) -> int:
    versions = declared_versions(root)
    distinct = set(versions.values())
    if len(distinct) == 1 and None not in distinct:
        print(f"version {distinct.pop()} declared consistently in {len(versions)} places")
        return 0
    print("error: the application version is not declared consistently:", file=sys.stderr)
    for where, version in versions.items():
        print(f"  {where}: {version}", file=sys.stderr)
    return 1


# TypeScript has no compiler switch against `any` / `unknown`; Python's explicit `Any` is refused by
# mypy (`disallow_any_explicit`). The owner's rule: neither type in the application code; tests
# may keep them. Comments and string contents are blanked before the words are looked for; the
# `${...}` expressions of template literals are code and stay.
_FORBIDDEN_TS_WORD = re.compile(r"\b(any|unknown)\b")


def _blank(character: str) -> str:
    # Line breaks stay, so that line numbers stay right.
    return character if character == "\n" else " "


def typescript_code_only(source: str) -> str:
    """`source` with comments and string contents blanked; template expressions kept."""
    out: list[str] = []
    mode = "code"  # code, line comment, block comment, a quote, or "`" for template text
    expressions: list[int] = []  # open `${` expressions: their nesting depth of `{`
    i, size = 0, len(source)
    while i < size:
        char, following = source[i], source[i + 1 : i + 2]
        if mode == "code":
            if char == "/" and following in ("/", "*"):
                mode = "//" if following == "/" else "/*"
                out.append("  ")
                i += 2
                continue
            if char in "\"'`":
                mode = char
            elif expressions and char == "{":
                expressions[-1] += 1
            elif expressions and char == "}":
                if expressions[-1] == 0:
                    expressions.pop()
                    mode = "`"  # back to the template text this expression was in
                else:
                    expressions[-1] -= 1
            out.append(char)
            i += 1
        elif mode == "//":
            if char == "\n":
                mode = "code"
            out.append(_blank(char))
            i += 1
        elif mode == "/*":
            if char == "*" and following == "/":
                mode = "code"
                out.append("  ")
                i += 2
            else:
                out.append(_blank(char))
                i += 1
        elif char == "\\":
            out.append(" " + _blank(following))
            i += 2
        elif char == mode:  # the closing quote or backtick
            mode = "code"
            out.append(char)
            i += 1
        elif mode == "`" and char == "$" and following == "{":
            expressions.append(0)
            mode = "code"
            out.append("${")
            i += 2
        else:
            out.append(_blank(char))
            i += 1
    return "".join(out)


def forbidden_ts_words(source: str) -> list[tuple[int, str]]:
    """(line, word) of every `any` or `unknown` in code: not in comments or string contents."""
    code = typescript_code_only(source)
    return [
        (code.count("\n", 0, found.start()) + 1, found.group(1))
        for found in _FORBIDDEN_TS_WORD.finditer(code)
    ]


def cmd_type_rules(root: Path) -> int:
    """No `any` / `unknown` in the window's TypeScript outside its tests."""
    found = [
        (path.relative_to(root), line, word)
        for path in sorted((root / "ui" / "src").rglob("*.ts"))
        if not path.name.endswith(".test.ts")
        for line, word in forbidden_ts_words(path.read_text(encoding="utf-8"))
    ]
    if not found:
        print("no any or unknown in the application TypeScript")
        return 0
    for path, line, word in found:
        print(f"error: {path}:{line}: `{word}` in application code", file=sys.stderr)
    return 1


def cmd_check(root: Path) -> int:
    for step in (
        cmd_version_check,
        cmd_type_rules,
        cmd_lint,
        cmd_typecheck,
        cmd_test,
        cmd_depcheck,
    ):
        code = step(root)
        if code != 0:
            return code
    return 0


def _missing_tool(name: str, hint: str) -> int:
    print(f"error: {name} is required for this command (see {hint})", file=sys.stderr)
    return 2


def _run_in(cwd: Path, root: Path, args: Sequence[str]) -> int:
    print("+", " ".join(args), f"(in {cwd.relative_to(root)})")
    return subprocess.call(list(args), cwd=cwd)


def cmd_rust_check(root: Path) -> int:
    """cargo fmt --check, clippy (deny warnings) and test for the Tauri shell crate."""
    if shutil.which("cargo") is None:
        return _missing_tool("cargo", "https://rustup.rs/")
    cwd = root / "src-tauri"
    steps = (
        ["cargo", "fmt", "--check"],
        ["cargo", "clippy", "--all-targets", "--", "-D", "warnings"],
        ["cargo", "test"],
    )
    for args in steps:
        code = _run_in(cwd, root, args)
        if code != 0:
            return code
    return 0


def cmd_ui_check(root: Path) -> int:
    """TypeScript typecheck, Prettier format check and model tests for the frontend."""
    if shutil.which("npm") is None:
        return _missing_tool("npm", "https://nodejs.org/")
    cwd = root / "ui"
    if not (cwd / "node_modules").exists():
        print(
            "error: ui/node_modules is missing; run `npm install` in ui/ first",
            file=sys.stderr,
        )
        return 2
    for script in ("typecheck", "format:check", "test"):
        code = _run_in(cwd, root, ["npm", "run", script])
        if code != 0:
            return code
    return 0


# Dependency advisories (npm, Python, Rust): what blocks, and the dated waivers that may lift a
# block for a while. Parsing and policy are pure (report, waivers and today in, verdict out);
# only the `*_report` readers run a tool, and each parser fails closed on what it cannot read.
BLOCKING_SEVERITIES = ("high", "critical")  # npm; pip-audit and cargo audit: every advisory
NPM_AUDIT_WAIVERS = Path("ui") / "npm-audit-waivers.json"
PYTHON_AUDIT_WAIVERS = Path("python-audit-waivers.json")
RUST_AUDIT_WAIVERS = Path("src-tauri") / "cargo-audit-waivers.json"
PIP_AUDIT = "pip-audit==2.10.1"


@dataclass(frozen=True)
class AuditFinding:
    package: str
    severity: str
    advisory: str  # the advisory's URL (npm) or id (PYSEC-..., GHSA-..., RUSTSEC-...)


@dataclass(frozen=True)
class AuditWaiver:
    advisory: str
    reason: str
    expires: date


@dataclass(frozen=True)
class AuditVerdict:
    blocking: tuple[AuditFinding, ...]
    reported: tuple[AuditFinding, ...]  # below the policy: shown, never blocking
    waived: tuple[AuditFinding, ...]
    expired: tuple[AuditWaiver, ...]

    @property
    def passed(self) -> bool:
        return not self.blocking and not self.expired


def npm_audit_findings(report: object) -> list[AuditFinding]:
    """Every advisory of an `npm audit --json` report (format 2), once per package and URL.

    The gate fails closed: a report in another format, or an entry it cannot read, raises
    instead of being skipped, so a change in npm's output can never pass as a clean audit.
    """
    if not isinstance(report, dict) or report.get("auditReportVersion") != 2:
        raise ValueError("not an npm audit report in format 2 (`auditReportVersion` != 2)")
    vulnerabilities = report.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        raise ValueError("the npm audit report has no `vulnerabilities` object")
    found: dict[tuple[str, str], AuditFinding] = {}
    for package, entry in vulnerabilities.items():
        via = entry.get("via") if isinstance(entry, dict) else None
        if not isinstance(via, list) or not via:
            raise ValueError(f"unreadable npm audit entry for {package}: no `via` list")
        for item in via:
            # A string names the vulnerable dependency it comes through; objects are advisories.
            if isinstance(item, str):
                continue
            url = item.get("url") if isinstance(item, dict) else None
            severity = item.get("severity") if isinstance(item, dict) else None
            if not (isinstance(url, str) and url and isinstance(severity, str) and severity):
                raise ValueError(f"unreadable npm advisory for {package}: no `url` or `severity`")
            found[(str(package), url)] = AuditFinding(str(package), severity, url)
    return sorted(found.values(), key=lambda f: (f.package, f.advisory))


def pip_audit_findings(report: object) -> list[AuditFinding]:
    """Every advisory of a `pip-audit -f json` report. A dependency pip-audit skipped (it could
    not check it) fails the gate rather than pass unchecked."""
    dependencies = report.get("dependencies") if isinstance(report, dict) else None
    if not isinstance(dependencies, list):
        raise ValueError("not a pip-audit report (no `dependencies` list)")
    found: dict[tuple[str, str], AuditFinding] = {}
    for dependency in dependencies:
        name = dependency.get("name") if isinstance(dependency, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError("unreadable pip-audit dependency: no `name`")
        if dependency.get("skip_reason") is not None:
            raise ValueError(f"pip-audit could not check {name}: {dependency['skip_reason']}")
        vulns = dependency.get("vulns")
        if not isinstance(vulns, list):
            raise ValueError(f"unreadable pip-audit dependency {name}: no `vulns` list")
        for vuln in vulns:
            advisory = vuln.get("id") if isinstance(vuln, dict) else None
            if not isinstance(advisory, str) or not advisory:
                raise ValueError(f"unreadable pip-audit advisory for {name}: no `id`")
            # The export can list one package twice (one line per environment marker).
            found[(name, advisory)] = AuditFinding(name, "advisory", advisory)
    return sorted(found.values(), key=lambda f: (f.package, f.advisory))


def cargo_audit_findings(report: object) -> list[AuditFinding]:
    """Every vulnerability of a `cargo audit --json` report (its warnings are not findings)."""
    vulnerabilities = report.get("vulnerabilities") if isinstance(report, dict) else None
    listed = vulnerabilities.get("list") if isinstance(vulnerabilities, dict) else None
    if not isinstance(listed, list):
        raise ValueError("not a cargo audit report (no `vulnerabilities.list`)")
    found: list[AuditFinding] = []
    for item in listed:
        advisory = item.get("advisory") if isinstance(item, dict) else None
        package = item.get("package") if isinstance(item, dict) else None
        advisory_id = advisory.get("id") if isinstance(advisory, dict) else None
        name = package.get("name") if isinstance(package, dict) else None
        if not (isinstance(advisory_id, str) and advisory_id and isinstance(name, str) and name):
            raise ValueError("unreadable cargo audit vulnerability: no advisory id or package")
        found.append(AuditFinding(name, "vulnerability", advisory_id))
    return sorted(found, key=lambda f: (f.package, f.advisory))


def cargo_audit_warnings(report: object) -> list[str]:
    """cargo audit's warnings (unmaintained, unsound, yanked...), as `kind: package` lines."""
    warnings = report.get("warnings") if isinstance(report, dict) else None
    if not isinstance(warnings, dict):
        return []
    lines: set[str] = set()
    for kind, items in warnings.items():
        for item in items if isinstance(items, list) else []:
            package = item.get("package") if isinstance(item, dict) else None
            name = package.get("name") if isinstance(package, dict) else None
            lines.add(f"{kind}: {name if isinstance(name, str) else '?'}")
    return sorted(lines)


def audit_waivers(document: object) -> list[AuditWaiver]:
    """A waivers file: {"waivers": [{"advisory", "reason", "expires": "YYYY-MM-DD"}]}."""
    waivers = document.get("waivers") if isinstance(document, dict) else None
    if not isinstance(waivers, list):
        raise ValueError("the waivers file needs a `waivers` list")
    parsed: list[AuditWaiver] = []
    for item in waivers:
        advisory = item.get("advisory") if isinstance(item, dict) else None
        reason = item.get("reason") if isinstance(item, dict) else None
        expires = item.get("expires") if isinstance(item, dict) else None
        if not (isinstance(advisory, str) and advisory and isinstance(reason, str) and reason):
            raise ValueError("each waiver needs an `advisory` and a `reason`")
        if not isinstance(expires, str):
            raise ValueError(f"the waiver of {advisory} needs an `expires` date")
        parsed.append(AuditWaiver(advisory, reason, date.fromisoformat(expires)))
    return parsed


def audit_verdict(
    findings: Sequence[AuditFinding],
    waivers: Sequence[AuditWaiver],
    today: date,
    blocking_severities: Sequence[str] | None = BLOCKING_SEVERITIES,
) -> AuditVerdict:
    """Findings of a blocking severity (all of them when `blocking_severities` is None) block
    unless a waiver still in date names them; an expired waiver blocks by itself, so a waiver
    is never forgotten."""
    valid = {w.advisory for w in waivers if w.expires >= today}
    blocking: list[AuditFinding] = []
    reported: list[AuditFinding] = []
    waived: list[AuditFinding] = []
    for finding in findings:
        if blocking_severities is not None and finding.severity not in blocking_severities:
            reported.append(finding)
        elif finding.advisory in valid:
            waived.append(finding)
        else:
            blocking.append(finding)
    expired = tuple(w for w in waivers if w.expires < today)
    return AuditVerdict(tuple(blocking), tuple(reported), tuple(waived), expired)


def _json_output(args: Sequence[str], cwd: Path) -> object:
    """The JSON a tool prints (audit tools exit non-zero when they find something: the JSON
    says what)."""
    print("+", " ".join(args))
    result = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, check=False)
    report: object = json.loads(result.stdout)
    return report


def _npm_audit_report(root: Path) -> object:
    return _json_output(["npm", "audit", "--json"], root / "ui")


def _python_audit_report(root: Path) -> object:
    """pip-audit of every locked package (all groups and extras), hash-checked, without pip."""
    exported = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--all-extras",
            "--all-groups",
            "--no-emit-project",
            "--format",
            "requirements-txt",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="control-tv-audit-") as scratch:
        requirements = Path(scratch) / "requirements.txt"
        requirements.write_text(exported, encoding="utf-8")
        return _json_output(
            [
                "uvx",
                "--from",
                PIP_AUDIT,
                "pip-audit",
                "--disable-pip",
                "--require-hashes",
                "-r",
                str(requirements),
                "-f",
                "json",
            ],
            root,
        )


def _rust_audit_report(root: Path) -> object:
    return _json_output(["cargo", "audit", "--json"], root / "src-tauri")


def _audit(
    name: str,
    read: Callable[[], object],
    parse: Callable[[object], list[AuditFinding]],
    waivers_path: Path,
    blocking_severities: Sequence[str] | None,
    notes: Callable[[object], list[str]] = lambda report: [],
) -> int:
    """Read one tool's report, apply the policy and the waivers, print the verdict."""
    try:
        report = read()
        findings = parse(report)
        waivers = audit_waivers(json.loads(waivers_path.read_text(encoding="utf-8")))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"error: {name} could not be evaluated: {error}", file=sys.stderr)
        return 2
    verdict = audit_verdict(findings, waivers, date.today(), blocking_severities)
    for line in notes(report):
        print(f"note: {line}")
    for finding in verdict.reported:
        print(f"note: {finding.package}: {finding.severity} {finding.advisory} (below the policy)")
    for finding in verdict.waived:
        print(f"waived: {finding.package}: {finding.severity} {finding.advisory}")
    for finding in verdict.blocking:
        print(f"error: {finding.package}: {finding.severity} {finding.advisory}", file=sys.stderr)
    for waiver in verdict.expired:
        print(
            f"error: the waiver of {waiver.advisory} expired on {waiver.expires.isoformat()}",
            file=sys.stderr,
        )
    if verdict.passed:
        print(f"{name}: no unwaived blocking advisory")
        return 0
    return 1


def cmd_npm_audit(root: Path) -> int:
    """Fail on a high or critical npm advisory that no in-date waiver covers."""
    if shutil.which("npm") is None:
        return _missing_tool("npm", "https://nodejs.org/")
    return _audit(
        "npm audit",
        lambda: _npm_audit_report(root),
        npm_audit_findings,
        root / NPM_AUDIT_WAIVERS,
        BLOCKING_SEVERITIES,
    )


def cmd_python_audit(root: Path) -> int:
    """Fail on any advisory of a locked Python package that no in-date waiver covers."""
    for tool in ("uv", "uvx"):
        if shutil.which(tool) is None:
            return _missing_tool(tool, "https://docs.astral.sh/uv/")
    return _audit(
        "python audit",
        lambda: _python_audit_report(root),
        pip_audit_findings,
        root / PYTHON_AUDIT_WAIVERS,
        None,
    )


def cmd_rust_audit(root: Path) -> int:
    """Fail on any RustSec vulnerability of the Tauri crate that no in-date waiver covers;
    cargo audit's warnings (unmaintained, unsound, yanked) are printed, not blocking."""
    if shutil.which("cargo") is None:
        return _missing_tool("cargo", "https://rustup.rs/")
    return _audit(
        "rust audit",
        lambda: _rust_audit_report(root),
        cargo_audit_findings,
        root / RUST_AUDIT_WAIVERS,
        None,
        cargo_audit_warnings,
    )


def _run_bridge_packaging(root: Path, *action: str) -> int:
    return _run(root, [str(root / "packaging" / "build_bridge.py"), *action])


def release_python_version(root: Path) -> str:
    """The exact CPython version release bridges embed (`packaging/release-python-version`)."""
    return (root / "packaging" / "release-python-version").read_text(encoding="utf-8").strip()


def cmd_bridge_build(root: Path) -> int:
    """Freeze the bridge with the locked PyInstaller and the release CPython, in the repo."""
    version = release_python_version(root)
    # The frozen bridge embeds the TV's own remote channel (the `tvremote` extra), never MCP.
    code = _run_uv(
        root,
        ["sync", "--locked", "--group", "packaging", "--extra", "tvremote", "--python", version],
    )
    return code or _run_bridge_packaging(root, "build")


def cmd_bridge_smoke(root: Path) -> int:
    """Informative local smoke: isolated when the system allows it, and says whether it was."""
    return _run_bridge_packaging(root, "smoke")


def cmd_release_deb(root: Path) -> int:
    """The Linux release package, built only around a frozen bridge that passed its smoke."""
    code = cmd_bridge_build(root)
    # A release requires the isolated smoke: no isolation is a failure, never a fallback.
    code = code or _run_bridge_packaging(root, "smoke", "--strict")
    return code or _run(root, [str(root / "packaging" / "release_linux.py")])


def cmd_android_python(root: Path) -> int:
    """The embedded app's Python packages: pure wheels, hash-checked against uv.lock."""
    code = _run_uv(root, ["sync", "--locked", "--group", "android"])
    return code or _run(root, [str(root / "packaging" / "android_python.py")])


def cmd_android_apk(root: Path) -> int:
    """A debug APK for arm64 phones, signed with the debug key (installable for testing)."""
    if shutil.which("npx") is None:
        return _missing_tool("npx", "https://nodejs.org/")
    code = cmd_android_python(root)
    command = ["npx", "--prefix", "ui", "tauri", "android", "build"]
    return code or _run_in(root, root, [*command, "--apk", "--target", "aarch64", "--debug"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev.py", description="control-TV development commands")
    sub = parser.add_subparsers(dest="command", required=True)
    names = (
        "setup",
        "lock",
        "lint",
        "typecheck",
        "test",
        "coverage",
        "depcheck",
        "check",
        "version-check",
        "type-rules",
        "rust-check",
        "ui-check",
        "npm-audit",
        "python-audit",
        "rust-audit",
        "bridge-build",
        "bridge-smoke",
        "release-deb",
        "android-python",
        "android-apk",
        "disk-usage",
    )
    for name in names:
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
        "coverage": cmd_coverage,
        "depcheck": cmd_depcheck,
        "check": cmd_check,
        "version-check": cmd_version_check,
        "type-rules": cmd_type_rules,
        "rust-check": cmd_rust_check,
        "ui-check": cmd_ui_check,
        "npm-audit": cmd_npm_audit,
        "python-audit": cmd_python_audit,
        "rust-audit": cmd_rust_audit,
        "bridge-build": cmd_bridge_build,
        "bridge-smoke": cmd_bridge_smoke,
        "release-deb": cmd_release_deb,
        "android-python": cmd_android_python,
        "android-apk": cmd_android_apk,
        "disk-usage": cmd_disk_usage,
    }
    return handlers[command](root)


if __name__ == "__main__":
    sys.exit(main())
