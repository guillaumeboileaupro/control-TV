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


def test_tv_receiver_change_runs_only_the_android_build(tmp_path: Path) -> None:
    assert scope(tmp_path, ["android-tv-receiver/app/src/main/java/Receiver.kt"]) == {
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


# The real changed-file detection: a temporary Git repository and the workflow's own command ---

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
DEPENDENCY_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "dependency-audit.yml"
)
DIFF_COMMAND = 'git diff --name-only --no-renames "$BASE_SHA...$HEAD_SHA"'


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@invalid",
            "HOME": str(repository),
            "PATH": "/usr/bin:/bin",
        },
    ).stdout


def repository(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose base commit holds Python, Rust, Kotlin and documentation files."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    files = {
        "src/control_tv/service.py": "VALUE = 1\nOTHER = 2\nTHIRD = 3\n",
        "src-tauri/src/bridge.rs": "pub fn bridge() {}\npub fn other() {}\n",
        "src-tauri/gen/android/app/src/main/java/Widget.kt": "class Widget\n",
        "docs/guide.md": "# Guide\n",
    }
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    base = git(root, "rev-parse", "HEAD").strip()
    git(root, "switch", "-q", "-c", "pull-request")
    return root, base


def pull_request_scope(tmp_path: Path, root: Path, base: str) -> dict[str, str]:
    """What the workflow's scope step selects: its diff command piped into ci_scope.py."""
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change")
    head = git(root, "rev-parse", "HEAD").strip()
    changed = git(root, "diff", "--name-only", "--no-renames", f"{base}...{head}")
    return scope(tmp_path, changed.splitlines())


def test_the_workflow_lists_changes_with_the_tested_command_and_no_filter() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert DIFF_COMMAND in text
    assert "--diff-filter" not in text


def test_ci_exercises_both_new_streaming_components_without_duplicate_build_jobs() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "cargo clippy --all-targets -- -D warnings" in workflow
    assert "cargo test --all-targets" in workflow
    assert "working-directory: webrtc-engine" in workflow
    assert "--project-dir android-tv-receiver test" in workflow
    assert workflow.count("name: Tauri shell (Rust and frontend)") == 1
    assert workflow.count("name: Android APK (arm64 debug build and JVM tests)") == 1


def test_osv_audits_the_two_component_locks_and_fails_on_findings() -> None:
    workflow = DEPENDENCY_WORKFLOW.read_text(encoding="utf-8")
    assert "--lockfile=./webrtc-engine/Cargo.lock" in workflow
    assert "--lockfile=./android-tv-receiver/app/gradle.lockfile" in workflow
    assert "fail-on-vuln: true" in workflow
    assert "@b1d267aea5211fa648d9d442fa64b90dbdcbdd36" in workflow


def test_deleting_python_and_rust_sources_runs_every_build(tmp_path: Path) -> None:
    """Regression (review of 9f392e9): --diff-filter=ACMR dropped deletions, so a pull request
    removing sources listed nothing and skipped every platform build."""
    root, base = repository(tmp_path)
    git(root, "rm", "-q", "src/control_tv/service.py", "src-tauri/src/bridge.rs")

    assert pull_request_scope(tmp_path, root, base) == ALL


def test_deleting_only_a_kotlin_source_still_runs_the_android_build(tmp_path: Path) -> None:
    root, base = repository(tmp_path)
    git(root, "rm", "-q", "src-tauri/gen/android/app/src/main/java/Widget.kt")

    assert pull_request_scope(tmp_path, root, base)["android"] == "true"


def test_moving_a_source_into_docs_runs_every_build(tmp_path: Path) -> None:
    """Regression: a rename was reported under its new path only, so moving a source into docs/
    looked like a documentation change and skipped every build."""
    root, base = repository(tmp_path)
    git(root, "mv", "src/control_tv/service.py", "docs/service.py")

    assert pull_request_scope(tmp_path, root, base) == ALL


def test_a_modified_source_runs_every_build(tmp_path: Path) -> None:
    root, base = repository(tmp_path)
    (root / "src/control_tv/service.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert pull_request_scope(tmp_path, root, base) == ALL


def test_a_documentation_change_stays_fast_even_after_main_moved(tmp_path: Path) -> None:
    """Only the pull request's own changes count (merge base): a source change merged to main
    meanwhile was validated by main's own complete run."""
    root, base = repository(tmp_path)
    (root / "docs/guide.md").write_text("# Guide\n\nMore.\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "docs")
    head = git(root, "rev-parse", "HEAD").strip()
    git(root, "switch", "-q", "main")
    (root / "src/control_tv/service.py").write_text("VALUE = 9\n", encoding="utf-8")
    git(root, "commit", "-q", "-am", "main moved")
    moved_base = git(root, "rev-parse", "HEAD").strip()

    changed = git(root, "diff", "--name-only", "--no-renames", f"{moved_base}...{head}")
    assert changed.splitlines() == ["docs/guide.md"]
    assert scope(tmp_path, changed.splitlines()) == FAST_ONLY
    assert base != moved_base
