"""Tests for the project-owned cleanup logic in scripts/dev.py.

Every test runs against a fake repository under `tmp_path`; the real repository is
never touched.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType

import pytest

DEV_PATH = Path(__file__).resolve().parent.parent / "scripts" / "dev.py"


def _load_dev() -> ModuleType:
    spec = importlib.util.spec_from_file_location("control_tv_dev_script", DEV_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dev = _load_dev()


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for tracked in (
        "pyproject.toml",
        "README.md",
        "src/control_tv/__init__.py",
        "tests/test_x.py",
        "scripts/dev.py",
        ".git/config",
        "src-tauri/Cargo.toml",
        "src-tauri/gen/android/settings.gradle",
        "ui/package.json",
        "packaging/bridge_entry.py",
        "packaging/build_bridge.py",
    ):
        _write(root / tracked)
    return root


def _generate(root: Path) -> None:
    for generated in (
        ".pytest_cache/CACHEDIR.TAG",
        ".mypy_cache/meta.json",
        ".ruff_cache/cache",
        "htmlcov/index.html",
        "build/lib/mod.py",
        "tmp/scratch.txt",
        "target/debug/app",
        "src-tauri/target/release/app",
        "src-tauri/gen/android/build/out.apk",
        "src-tauri/gen/android/app/build/out.apk",
        "src-tauri/gen/android/.gradle/state",
        "src-tauri/gen/schemas/desktop-schema.json",
        ".gradle/state",
        "ui/dist/index.html",
        "ui/node_modules/pkg/index.js",
        "src/control_tv/__pycache__/__init__.cpython-312.pyc",
        "tests/__pycache__/test_x.cpython-312.pyc",
        "src/control_tv.egg-info/PKG-INFO",
        "control_tv.egg-info/PKG-INFO",
        "run.log",
        ".coverage",
        ".venv/lib/site.py",
        ".venv/lib/__pycache__/site.cpython-312.pyc",
        "dist/control-tv.deb",
        "dist/python-bridge/control-tv-bridge",
        "packaging/.pyinstaller/work/control-tv-bridge/base_library.zip",
        "packaging/.pyinstaller/sanitized/_sysconfigdata__linux_x86_64-linux-gnu.py",
        "packaging/__pycache__/build_bridge.cpython-312.pyc",
    ):
        _write(root / generated)


def _remaining(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


TRACKED = {
    "pyproject.toml",
    "README.md",
    "src/control_tv/__init__.py",
    "tests/test_x.py",
    "scripts/dev.py",
    ".git/config",
    "src-tauri/Cargo.toml",
    "src-tauri/gen/android/settings.gradle",
    "ui/package.json",
    "packaging/bridge_entry.py",
    "packaging/build_bridge.py",
}


def test_clean_removes_disposable_output_only(repo: Path) -> None:
    _generate(repo)

    assert dev.main(["clean"], root=repo) == 0

    assert _remaining(repo) == TRACKED | {
        ".venv/lib/site.py",
        ".venv/lib/__pycache__/site.cpython-312.pyc",
        "dist/control-tv.deb",
        "dist/python-bridge/control-tv-bridge",
        "ui/node_modules/pkg/index.js",
    }


def test_dist_clean_removes_all_reproducible_output(repo: Path) -> None:
    _generate(repo)

    assert dev.main(["dist-clean"], root=repo) == 0

    assert _remaining(repo) == TRACKED
    assert not (repo / ".venv").exists()
    assert not (repo / "dist").exists()
    assert not (repo / "ui" / "node_modules").exists()


def test_dry_run_removes_nothing(repo: Path) -> None:
    _generate(repo)
    before = _remaining(repo)

    assert dev.main(["dist-clean", "--dry-run"], root=repo) == 0

    assert _remaining(repo) == before


def test_clean_on_repo_without_generated_output_is_a_noop(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert dev.main(["clean"], root=repo) == 0

    assert _remaining(repo) == TRACKED
    assert "0 project-owned path(s)" in capsys.readouterr().out


def test_symlink_target_is_unlinked_not_followed(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "shared-cache"
    keep = _write(outside / "registry.bin")
    (repo / "target").symlink_to(outside, target_is_directory=True)

    assert dev.main(["clean"], root=repo) == 0

    assert not (repo / "target").exists()
    assert not (repo / "target").is_symlink()
    assert keep.exists()


def test_symlinked_scan_directory_is_not_descended(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    keep = _write(outside / "__pycache__" / "x.pyc")
    (repo / "tests").rename(repo / "tests_real")
    (repo / "tests").symlink_to(outside, target_is_directory=True)

    assert dev.main(["clean"], root=repo) == 0

    assert keep.exists()


def test_paths_outside_repository_are_refused(repo: Path, tmp_path: Path) -> None:
    victim = _write(tmp_path / "victim" / "data.txt")

    with pytest.raises(dev.UnsafeTargetError):
        dev.remove_paths([victim.parent], repo)

    assert victim.exists()


def test_repository_root_and_git_directory_are_refused(repo: Path) -> None:
    with pytest.raises(dev.UnsafeTargetError):
        dev.remove_paths([repo], repo)
    with pytest.raises(dev.UnsafeTargetError):
        dev.remove_paths([repo / ".git"], repo)
    with pytest.raises(dev.UnsafeTargetError):
        dev.remove_paths([repo / ".git" / "config"], repo)

    assert (repo / ".git" / "config").exists()


def test_unsafe_target_aborts_before_any_deletion(repo: Path, tmp_path: Path) -> None:
    _generate(repo)
    outside = _write(tmp_path / "victim" / "data.txt").parent

    with pytest.raises(dev.UnsafeTargetError):
        dev.remove_paths([repo / "target", outside], repo)

    assert (repo / "target").exists()


def test_disk_usage_reports_sizes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo / ".venv" / "big.bin", "x" * 2048)
    _write(repo / ".pytest_cache" / "a", "y" * 10)

    assert dev.main(["disk-usage"], root=repo) == 0

    out = capsys.readouterr().out
    assert "Free disk on" in out
    assert ".venv" in out
    assert "2.0 KiB" in out
    assert ".pytest_cache" in out
    assert "Total project-owned generated output:" in out


def test_path_size_counts_nested_files_without_following_symlinks(
    repo: Path, tmp_path: Path
) -> None:
    _write(repo / "target" / "a.bin", "x" * 100)
    _write(repo / "target" / "sub" / "b.bin", "x" * 50)
    big = _write(tmp_path / "outside" / "huge.bin", "x" * 100_000)
    (repo / "target" / "link").symlink_to(big.parent, target_is_directory=True)

    size = dev.path_size(repo / "target")

    assert 150 <= size < 100_000


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (5 * 1024**2, "5.0 MiB"),
        (3 * 1024**3, "3.0 GiB"),
    ],
)
def test_format_size(size: int, expected: str) -> None:
    assert dev.format_size(size) == expected


def test_quality_commands_require_setup(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert dev.main(["test"], root=repo) == 2
    assert "setup" in capsys.readouterr().err


def _no_tool_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point PATH at an empty directory so no external tool can be found."""
    empty = tmp_path / "empty-path"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))


def _fake_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, log: Path) -> None:
    """Put a fake `name` on PATH that appends its argv (joined) as one line to `log`."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")


def _no_uv_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point PATH at an empty directory so `uv` cannot be found."""
    _no_tool_on_path(monkeypatch, tmp_path)


def _fake_uv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, log: Path) -> None:
    """Put a fake `uv` on PATH that appends its argv to `log` and exits 0."""
    _fake_tool(monkeypatch, tmp_path, "uv", log)


@pytest.mark.parametrize("command", ["setup", "lock"])
def test_setup_and_lock_require_uv(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    _no_uv_on_path(monkeypatch, tmp_path)

    assert dev.main([command], root=repo) == 2
    assert "uv is required" in capsys.readouterr().err


def test_setup_syncs_the_locked_environment(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "uv-calls.log"
    _fake_uv(monkeypatch, tmp_path, log)

    assert dev.main(["setup"], root=repo) == 0

    assert log.read_text().splitlines() == ["sync --locked --extra mcp --extra tvremote"]


def test_lock_regenerates_the_lockfile(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "uv-calls.log"
    _fake_uv(monkeypatch, tmp_path, log)

    assert dev.main(["lock"], root=repo) == 0

    assert log.read_text().splitlines() == ["lock"]


def test_coverage_runs_pytest_with_project_threshold(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, list[str]]] = []

    def fake_run(root: Path, args: Sequence[str]) -> int:
        calls.append((root, list(args)))
        return 0

    monkeypatch.setattr(dev, "_run", fake_run)

    assert dev.main(["coverage"], root=repo) == 0
    assert calls == [
        (
            repo,
            [
                "-m",
                "pytest",
                "--cov=control_tv",
                "--cov-report=term-missing",
                f"--cov-fail-under={dev.COVERAGE_MIN}",
            ],
        )
    ]


def test_depcheck_runs_uv_pip_check(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "uv-calls.log"
    _fake_uv(monkeypatch, tmp_path, log)

    assert dev.main(["depcheck"], root=repo) == 0

    assert log.read_text().splitlines() == ["pip check"]


def test_check_runs_every_quality_step_in_order(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def step(name: str) -> Callable[[Path], int]:
        def run(root: Path) -> int:
            assert root == repo
            calls.append(name)
            return 0

        return run

    for name in ("version_check", "type_rules", "lint", "typecheck", "test", "depcheck"):
        monkeypatch.setattr(dev, f"cmd_{name}", step(name))

    assert dev.main(["check"], root=repo) == 0
    assert calls == ["version_check", "type_rules", "lint", "typecheck", "test", "depcheck"]


def test_check_stops_after_first_failed_step(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def successful(name: str) -> Callable[[Path], int]:
        def run(root: Path) -> int:
            assert root == repo
            calls.append(name)
            return 0

        return run

    def fail_typecheck(root: Path) -> int:
        assert root == repo
        calls.append("typecheck")
        return 7

    monkeypatch.setattr(dev, "cmd_version_check", successful("version_check"))
    monkeypatch.setattr(dev, "cmd_type_rules", successful("type_rules"))
    monkeypatch.setattr(dev, "cmd_lint", successful("lint"))
    monkeypatch.setattr(dev, "cmd_typecheck", fail_typecheck)
    monkeypatch.setattr(dev, "cmd_test", successful("test"))
    monkeypatch.setattr(dev, "cmd_depcheck", successful("depcheck"))

    assert dev.main(["check"], root=repo) == 7
    assert calls == ["version_check", "type_rules", "lint", "typecheck"]


def test_rust_check_requires_cargo(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tool_on_path(monkeypatch, tmp_path)

    assert dev.main(["rust-check"], root=repo) == 2
    assert "cargo is required" in capsys.readouterr().err


def test_rust_check_runs_fmt_clippy_and_test_in_src_tauri(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "cargo-calls.log"
    _fake_tool(monkeypatch, tmp_path, "cargo", log)

    assert dev.main(["rust-check"], root=repo) == 0

    assert log.read_text().splitlines() == [
        "fmt --check",
        "clippy --all-targets -- -D warnings",
        "test",
    ]


def test_rust_check_stops_after_the_first_failure(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "cargo"
    # `fmt --check` fails; `clippy`/`test` must never run.
    script.write_text('#!/bin/sh\n[ "$1" = "fmt" ] && exit 1\nexit 0\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    assert dev.main(["rust-check"], root=repo) == 1


def test_ui_check_requires_npm(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tool_on_path(monkeypatch, tmp_path)

    assert dev.main(["ui-check"], root=repo) == 2
    assert "npm is required" in capsys.readouterr().err


def test_ui_check_requires_node_modules(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_tool(monkeypatch, tmp_path, "npm", tmp_path / "npm-calls.log")

    assert dev.main(["ui-check"], root=repo) == 2
    assert "ui/node_modules is missing" in capsys.readouterr().err


def test_ui_check_runs_typecheck_format_check_and_tests(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (repo / "ui" / "node_modules").mkdir(parents=True)
    log = tmp_path / "npm-calls.log"
    _fake_tool(monkeypatch, tmp_path, "npm", log)

    assert dev.main(["ui-check"], root=repo) == 0

    assert log.read_text().splitlines() == ["run typecheck", "run format:check", "run test"]


def write_versions(
    root: Path,
    *,
    python: str = "0.1.0",
    package: str = "0.1.0",
    cargo: str = "0.1.0",
    npm: str = "0.1.0",
    lock: str = "0.1.0",
    tauri: str | None = "0.1.0",
) -> None:
    (root / "src" / "control_tv").mkdir(parents=True, exist_ok=True)
    (root / "src-tauri").mkdir(parents=True, exist_ok=True)
    (root / "ui").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["x"]\n\n'
        f'[project]\nname = "control-tv"\nversion = "{python}"\n',
        encoding="utf-8",
    )
    (root / "src" / "control_tv" / "__init__.py").write_text(
        f'"""Doc."""\n\n__version__ = "{package}"\n', encoding="utf-8"
    )
    (root / "src-tauri" / "Cargo.toml").write_text(
        f'[package]\nname = "control-tv"\nversion = "{cargo}"\n\n'
        '[dependencies]\nserde = { version = "1" }\n',
        encoding="utf-8",
    )
    (root / "ui" / "package.json").write_text(
        json.dumps({"name": "ui", "version": npm}), encoding="utf-8"
    )
    (root / "ui" / "package-lock.json").write_text(
        json.dumps({"name": "ui", "version": lock, "packages": {"": {"version": lock}}}),
        encoding="utf-8",
    )
    conf: dict[str, object] = {"productName": "control-tv"}
    if tauri is not None:
        conf["version"] = tauri
    (root / "src-tauri" / "tauri.conf.json").write_text(json.dumps(conf), encoding="utf-8")


def test_version_check_passes_when_every_declaration_agrees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_versions(tmp_path)

    assert dev.main(["version-check"], root=tmp_path) == 0
    assert "version 0.1.0 declared consistently in 7 places" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("changes", "where"),
    [
        ({"python": "0.0.0"}, "pyproject.toml"),
        ({"package": "0.2.0"}, "__init__.py"),
        ({"cargo": "1.0.0"}, "Cargo.toml"),
        ({"npm": "0.1.1"}, "ui/package.json"),
        ({"lock": "0.0.9"}, "package-lock.json"),
        ({"tauri": "0.3.0"}, "tauri.conf.json"),
    ],
)
def test_version_check_fails_and_names_every_declaration_on_any_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], changes: dict[str, str], where: str
) -> None:
    write_versions(tmp_path, **changes)

    assert dev.main(["version-check"], root=tmp_path) == 1
    error = capsys.readouterr().err
    assert "not declared consistently" in error
    assert where in error


def test_a_tauri_config_without_its_own_version_uses_the_cargo_one(tmp_path: Path) -> None:
    write_versions(tmp_path, tauri=None)

    assert dev.main(["version-check"], root=tmp_path) == 0


def test_the_dependency_version_in_cargo_toml_is_not_taken_for_the_package_version(
    tmp_path: Path,
) -> None:
    """Only the [package] table counts; `serde = { version = "1" }` must be ignored."""
    write_versions(tmp_path)

    assert dev.declared_versions(tmp_path)["src-tauri/Cargo.toml [package]"] == "0.1.0"


def test_the_repository_declares_one_version() -> None:
    assert dev.main(["version-check"]) == 0


def test_bridge_build_syncs_the_packaging_group_then_freezes(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "uv-calls.log"
    _fake_uv(monkeypatch, tmp_path, log)
    calls: list[list[str]] = []

    def fake_run(root: Path, args: Sequence[str]) -> int:
        calls.append(list(args))
        return 0

    monkeypatch.setattr(dev, "_run", fake_run)
    _write(repo / "packaging" / "release-python-version", "3.12.15\n")

    assert dev.main(["bridge-build"], root=repo) == 0

    assert log.read_text().splitlines() == [
        "sync --locked --group packaging --extra tvremote --python 3.12.15"
    ]
    assert calls == [[str(repo / "packaging" / "build_bridge.py"), "build"]]


def test_bridge_build_does_not_freeze_when_the_sync_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    _write(repo / "packaging" / "release-python-version", "3.12.15\n")

    def record(root: Path, args: Sequence[str]) -> int:
        calls.append(list(args))
        return 0

    monkeypatch.setattr(dev, "_run_uv", lambda root, args: 1)
    monkeypatch.setattr(dev, "_run", record)

    assert dev.main(["bridge-build"], root=repo) == 1
    assert calls == []


def test_bridge_smoke_runs_the_smoke_with_the_development_interpreter(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def record(root: Path, args: Sequence[str]) -> int:
        calls.append(list(args))
        return 0

    monkeypatch.setattr(dev, "_run", record)

    assert dev.main(["bridge-smoke"], root=repo) == 0
    assert calls == [[str(repo / "packaging" / "build_bridge.py"), "smoke"]]


def test_release_deb_builds_and_smokes_the_bridge_before_the_package(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps: list[str] = []

    def step(name: str) -> Callable[[Path], int]:
        def run(root: Path) -> int:
            steps.append(name)
            return 0

        return run

    def record(root: Path, args: Sequence[str]) -> int:
        steps.append(" ".join(args))
        return 0

    monkeypatch.setattr(dev, "cmd_bridge_build", step("bridge-build"))
    monkeypatch.setattr(dev, "cmd_bridge_smoke", step("informative-smoke"))
    monkeypatch.setattr(dev, "_run", record)

    assert dev.main(["release-deb"], root=repo) == 0
    # The release runs the strict smoke, never the informative one that may skip isolation.
    assert steps == [
        "bridge-build",
        f"{repo / 'packaging' / 'build_bridge.py'} smoke --strict",
        str(repo / "packaging" / "release_linux.py"),
    ]


def test_release_deb_stops_when_the_smoke_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    released: list[Sequence[str]] = []
    monkeypatch.setattr(dev, "cmd_bridge_build", lambda root: 0)

    def record(root: Path, args: Sequence[str]) -> int:
        released.append(args)
        return 1 if "--strict" in args else 0

    monkeypatch.setattr(dev, "_run", record)

    assert dev.main(["release-deb"], root=repo) == 1
    assert [list(args)[1:] for args in released] == [["smoke", "--strict"]]


def test_android_python_syncs_the_android_group_then_prepares_the_wheels(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "uv-calls.log"
    _fake_uv(monkeypatch, tmp_path, log)
    calls: list[list[str]] = []

    def record(root: Path, args: Sequence[str]) -> int:
        calls.append(list(args))
        return 0

    monkeypatch.setattr(dev, "_run", record)

    assert dev.main(["android-python"], root=repo) == 0
    assert log.read_text().splitlines() == ["sync --locked --group android"]
    assert calls == [[str(repo / "packaging" / "android_python.py")]]


def test_android_apk_builds_a_debug_arm64_apk_after_the_wheels(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    steps: list[str] = []

    def prepare(root: Path) -> int:
        steps.append("android-python")
        return 0

    monkeypatch.setattr(dev, "cmd_android_python", prepare)
    monkeypatch.setattr(dev.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run_in(cwd: Path, root: Path, args: Sequence[str]) -> int:
        steps.append(" ".join(args))
        return 0

    monkeypatch.setattr(dev, "_run_in", run_in)

    assert dev.main(["android-apk"], root=repo) == 0
    assert steps == [
        "android-python",
        "npx --prefix ui tauri android build --apk --target aarch64 --debug",
    ]


def test_android_apk_stops_when_the_wheels_cannot_be_prepared(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Sequence[str]] = []
    monkeypatch.setattr(dev, "cmd_android_python", lambda root: 1)
    monkeypatch.setattr(dev.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run_in(cwd: Path, root: Path, args: Sequence[str]) -> int:
        built.append(args)
        return 0

    monkeypatch.setattr(dev, "_run_in", run_in)

    assert dev.main(["android-apk"], root=repo) == 1
    assert built == []


def test_type_rules_find_any_and_unknown_only_in_code() -> None:
    source = (
        "// any word in a comment is fine, unknown too\n"
        'const label = "any unknown"; /* any */\n'
        "let value: unknown = 1;\n"
        "function f(x: any): void {}\n"
        "const many = `unknown ${1}`;\n"
    )

    assert dev.forbidden_ts_words(source) == [(3, "unknown"), (4, "any")]


def test_type_rules_skip_tests_and_report_application_files(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = repo / "ui" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "ok.ts").write_text("export const x: number = 1; // any\n", encoding="utf-8")
    (src / "ok.test.ts").write_text("const y: any = 1;\n", encoding="utf-8")
    assert dev.cmd_type_rules(repo) == 0

    (src / "bad.ts").write_text("export const z: unknown = 1;\n", encoding="utf-8")
    assert dev.cmd_type_rules(repo) == 1
    assert "ui/src/bad.ts:1: `unknown`" in capsys.readouterr().err


def test_type_rules_scan_template_expressions_but_not_template_text() -> None:
    source = (
        "const a = `any text ${value as any} and unknown text`;\n"
        "const b = `${`nested ${x as unknown}`} ${'any'}`;\n"
        "const c = `${ { key: 1 }.key } fine`;\n"
        'const d = "escaped \\" any";\n'
        "/* a block\n   comment with any */ const e: number = 1;\n"
    )

    assert dev.forbidden_ts_words(source) == [(1, "any"), (2, "unknown")]


def test_type_rules_scan_subdirectories(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    nested = repo / "ui" / "src" / "panels"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "panel.test.ts").write_text("const t: any = 1;\n", encoding="utf-8")
    assert dev.cmd_type_rules(repo) == 0

    (nested / "panel.ts").write_text("export const p = (x: any): void => {};\n", encoding="utf-8")
    assert dev.cmd_type_rules(repo) == 1
    assert "ui/src/panels/panel.ts:1: `any`" in capsys.readouterr().err


GHSA = "https://github.com/advisories/GHSA-68fv-2mgg-jv7q"


def _report(*advisories: tuple[str, str, str]) -> dict[str, object]:
    vulnerabilities: dict[str, object] = {}
    for package, severity, url in advisories:
        entry = vulnerabilities.setdefault(package, {"via": []})
        assert isinstance(entry, dict)
        entry["via"].append({"url": url, "severity": severity, "title": "t"})
    # A package that is only vulnerable through another one names it as a string.
    vulnerabilities["postcss"] = {"via": ["source-map-js"]}
    return {"auditReportVersion": 2, "vulnerabilities": vulnerabilities}


def test_npm_audit_findings_keep_advisories_and_skip_pointers() -> None:
    report = _report(("source-map-js", "high", GHSA), ("source-map-js", "high", GHSA))

    assert dev.npm_audit_findings(report) == [dev.AuditFinding("source-map-js", "high", GHSA)]
    assert dev.npm_audit_findings({"auditReportVersion": 2, "vulnerabilities": {}}) == []


@pytest.mark.parametrize(
    "report",
    [
        {"error": {"code": "ENOAUDIT"}},
        {"auditReportVersion": 3, "vulnerabilities": {}},
        {"vulnerabilities": {}},
        {"auditReportVersion": 2},
        {"auditReportVersion": 2, "vulnerabilities": {"a": {}}},
        {"auditReportVersion": 2, "vulnerabilities": {"a": {"via": "b"}}},
        {"auditReportVersion": 2, "vulnerabilities": {"a": {"via": []}}},
        {"auditReportVersion": 2, "vulnerabilities": {"a": {"via": [{"url": GHSA}]}}},
        {"auditReportVersion": 2, "vulnerabilities": {"a": {"via": [{"severity": "high"}]}}},
        {"auditReportVersion": 2, "vulnerabilities": {"a": {"via": [42]}}},
    ],
)
def test_an_unreadable_npm_audit_report_fails_closed(report: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        dev.npm_audit_findings(report)


def test_npm_audit_blocks_high_and_critical_only() -> None:
    findings = dev.npm_audit_findings(
        _report(("a", "high", "u1"), ("b", "critical", "u2"), ("c", "moderate", "u3"))
    )

    verdict = dev.audit_verdict(findings, [], dev.date(2026, 10, 14))

    assert [f.package for f in verdict.blocking] == ["a", "b"]
    assert [f.package for f in verdict.reported] == ["c"]
    assert not verdict.passed


def test_an_in_date_waiver_lifts_the_block_and_an_expired_one_fails() -> None:
    findings = [dev.AuditFinding("source-map-js", "high", GHSA)]
    waiver = dev.audit_waivers(
        {"waivers": [{"advisory": GHSA, "reason": "build tooling only", "expires": "2026-10-31"}]}
    )

    in_date = dev.audit_verdict(findings, waiver, dev.date(2026, 10, 31))
    expired = dev.audit_verdict([], waiver, dev.date(2026, 11, 1))

    assert in_date.passed and in_date.waived == tuple(findings)
    assert not expired.passed and expired.expired == tuple(waiver)


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"waivers": [{"advisory": GHSA, "expires": "2026-10-31"}]},
        {"waivers": [{"advisory": GHSA, "reason": "r"}]},
        {"waivers": [{"advisory": GHSA, "reason": "r", "expires": "soon"}]},
    ],
)
def test_a_waiver_needs_an_advisory_a_reason_and_a_date(document: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        dev.audit_waivers(document)


def test_npm_audit_command_reports_and_fails_on_an_unwaived_advisory(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "ui").mkdir(exist_ok=True)
    (repo / "ui" / "npm-audit-waivers.json").write_text('{"waivers": []}', encoding="utf-8")
    monkeypatch.setattr(dev.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        dev, "_npm_audit_report", lambda root: _report(("source-map-js", "high", GHSA))
    )

    assert dev.cmd_npm_audit(repo) == 1
    assert f"source-map-js: high {GHSA}" in capsys.readouterr().err

    monkeypatch.setattr(dev, "_npm_audit_report", lambda root: _report())
    assert dev.cmd_npm_audit(repo) == 0


def _pip_report(*vulns: tuple[str, str]) -> dict[str, object]:
    dependencies: list[dict[str, object]] = [{"name": "requests", "version": "2.34.2", "vulns": []}]
    for name, advisory in vulns:
        dependencies.append({"name": name, "version": "1", "vulns": [{"id": advisory}]})
    return {"dependencies": dependencies, "fixes": []}


def _cargo_report(*vulns: tuple[str, str]) -> dict[str, object]:
    listed = [{"advisory": {"id": a, "title": "t"}, "package": {"name": n}} for n, a in vulns]
    return {
        "vulnerabilities": {"found": bool(listed), "count": len(listed), "list": listed},
        "warnings": {"unmaintained": [{"package": {"name": "proc-macro-error"}}]},
    }


def test_pip_audit_findings_read_every_advisory() -> None:
    report = _pip_report(
        ("pip", "PYSEC-2026-196"), ("wheel", "GHSA-8rrh-rw8j-w5fx"), ("pip", "PYSEC-2026-196")
    )

    assert dev.pip_audit_findings(report) == [
        dev.AuditFinding("pip", "advisory", "PYSEC-2026-196"),
        dev.AuditFinding("wheel", "advisory", "GHSA-8rrh-rw8j-w5fx"),
    ]
    assert dev.pip_audit_findings(_pip_report()) == []


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"dependencies": {}},
        {"dependencies": [{"version": "1", "vulns": []}]},
        {"dependencies": [{"name": "a", "skip_reason": "not on PyPI"}]},
        {"dependencies": [{"name": "a", "version": "1"}]},
        {"dependencies": [{"name": "a", "version": "1", "vulns": [{}]}]},
    ],
)
def test_an_unreadable_or_incomplete_pip_audit_report_fails_closed(
    report: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        dev.pip_audit_findings(report)


def test_cargo_audit_findings_are_vulnerabilities_and_warnings_are_notes() -> None:
    report = _cargo_report(("time", "RUSTSEC-2020-0071"))

    assert dev.cargo_audit_findings(report) == [
        dev.AuditFinding("time", "vulnerability", "RUSTSEC-2020-0071")
    ]
    assert dev.cargo_audit_warnings(report) == ["unmaintained: proc-macro-error"]
    assert dev.cargo_audit_findings(_cargo_report()) == []


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"vulnerabilities": {"count": 0}},
        {"vulnerabilities": {"list": [{"package": {"name": "time"}}]}},
        {"vulnerabilities": {"list": [{"advisory": {"id": "RUSTSEC-1"}}]}},
    ],
)
def test_an_unreadable_cargo_audit_report_fails_closed(report: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        dev.cargo_audit_findings(report)


def test_python_and_rust_audits_block_every_advisory_unless_waived() -> None:
    findings = [dev.AuditFinding("pip", "advisory", "PYSEC-2026-196")]
    waiver = dev.audit_waivers(
        {
            "waivers": [
                {"advisory": "PYSEC-2026-196", "reason": "build only", "expires": "2026-11-01"}
            ]
        }
    )

    assert not dev.audit_verdict(findings, [], dev.date(2026, 10, 15), None).passed
    assert dev.audit_verdict(findings, waiver, dev.date(2026, 10, 15), None).passed


@pytest.mark.parametrize(
    ("command", "reader", "waivers", "report", "advisory"),
    [
        (
            "cmd_python_audit",
            "_python_audit_report",
            "python-audit-waivers.json",
            _pip_report(("pip", "PYSEC-2026-196")),
            "PYSEC-2026-196",
        ),
        (
            "cmd_rust_audit",
            "_rust_audit_report",
            "src-tauri/cargo-audit-waivers.json",
            _cargo_report(("time", "RUSTSEC-2020-0071")),
            "RUSTSEC-2020-0071",
        ),
    ],
)
def test_python_and_rust_audit_commands_fail_on_an_unwaived_advisory(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    reader: str,
    waivers: str,
    report: dict[str, object],
    advisory: str,
) -> None:
    (repo / waivers).parent.mkdir(parents=True, exist_ok=True)
    (repo / waivers).write_text('{"waivers": []}', encoding="utf-8")
    monkeypatch.setattr(dev.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(dev, reader, lambda root: report)

    assert getattr(dev, command)(repo) == 1
    assert advisory in capsys.readouterr().err

    monkeypatch.setattr(
        dev, reader, lambda root: {"dependencies": []} if "python" in command else _cargo_report()
    )
    assert getattr(dev, command)(repo) == 0
