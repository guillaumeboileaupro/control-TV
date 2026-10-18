from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_scope.py"
ALL = {"tauri": "true", "linux_release": "true", "android": "true"}
FAST_ONLY = {"tauri": "false", "linux_release": "false", "android": "false"}


def scope(tmp_path: Path, paths: list[str], *, event: str = "pull_request") -> dict[str, str]:
    output = tmp_path / "github-output"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--event", event, "--github-output", str(output)],
        input="".join(f"{path}\n" for path in paths),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


def test_pushes_always_run_every_heavy_job(tmp_path: Path) -> None:
    assert scope(tmp_path, ["docs/only.md"], event="push") == ALL


def test_documentation_and_agent_rules_need_only_the_fast_quality_job(tmp_path: Path) -> None:
    assert (
        scope(
            tmp_path,
            ["docs/guide.md", "README.md", "AGENTS.md", ".agents/skills/example/SKILL.md"],
        )
        == FAST_ONLY
    )


def test_python_changes_keep_both_packages_and_the_bridge_validation(tmp_path: Path) -> None:
    assert scope(tmp_path, ["src/control_tv/service.py"]) == ALL
    assert scope(tmp_path, ["pyproject.toml"]) == ALL


def test_rust_and_ui_changes_keep_every_packaged_platform(tmp_path: Path) -> None:
    assert scope(tmp_path, ["src-tauri/src/lib.rs"]) == ALL
    assert scope(tmp_path, ["ui/src/main.ts"]) == ALL


def test_kotlin_change_runs_only_the_android_build(tmp_path: Path) -> None:
    assert scope(tmp_path, ["src-tauri/gen/android/app/src/main/java/example.kt"]) == {
        "tauri": "false",
        "linux_release": "false",
        "android": "true",
    }


def test_test_only_change_uses_quality_except_for_linux_packaging_contracts(
    tmp_path: Path,
) -> None:
    assert scope(tmp_path, ["tests/control/test_service.py"]) == FAST_ONLY
    assert scope(tmp_path, ["tests/test_release_linux.py"]) == {
        "tauri": "false",
        "linux_release": "true",
        "android": "false",
    }


def test_workflow_and_unknown_files_fail_safe_to_every_job(tmp_path: Path) -> None:
    assert scope(tmp_path, [".github/workflows/ci.yml"]) == ALL
    assert scope(tmp_path, ["unexpected.file"]) == ALL


def test_empty_pull_request_scope_is_fast_only(tmp_path: Path) -> None:
    assert scope(tmp_path, []) == FAST_ONLY
