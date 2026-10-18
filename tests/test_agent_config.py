"""The local agent configuration check and worktree link (temporary repositories only)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_config", ROOT / "scripts/agent_config.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_config"] = module
    spec.loader.exec_module(module)
    return module


config = load()


def skill(directory: Path, name: str, body: str = "Do the thing.\n") -> None:
    (directory / name).mkdir(parents=True, exist_ok=True)
    (directory / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use for {name}.\n---\n\n# {name}\n\n{body}",
        encoding="utf-8",
    )


def repository(tmp_path: Path) -> Path:
    """A git repository with the project's .gitignore and a coherent local configuration."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text((ROOT / ".gitignore").read_text(), encoding="utf-8")
    rules = "\n".join(f"{n}. [rule:{rule}] text" for n, rule in enumerate(config.RULES, 1))
    (root / "AGENTS.md").write_text(
        f"# Rules\n\n{rules}\n\nUse skill `git-worktree-pr-workflow`.\n", encoding="utf-8"
    )
    (root / "CLAUDE.md").write_text("# Claude\n\n@AGENTS.md\n", encoding="utf-8")
    for name in config.REQUIRED_SKILLS:
        skill(root / ".agents/skills", name)
        (root / ".claude/skills").mkdir(parents=True, exist_ok=True)
        (root / ".claude/skills" / name).symlink_to(root / ".agents/skills" / name)
    return root


def errors(root: Path) -> list[str]:
    report = config.check(root)
    assert report is not None
    return [str(error) for error in report.errors]


def test_a_coherent_configuration_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(tmp_path)

    assert errors(root) == []
    assert config.main(["check", "--root", str(root)]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_no_configuration_means_nothing_to_check(tmp_path: Path) -> None:
    assert config.check(tmp_path) is None
    assert config.main(["check", "--root", str(tmp_path)]) == 2


def test_every_rule_is_marked_exactly_once_and_only_known_rules(tmp_path: Path) -> None:
    root = repository(tmp_path)
    agents = root / "AGENTS.md"
    text = agents.read_text()
    agents.write_text(
        text.replace("[rule:no-merge] text", "") + "\n[rule:one-mission] again\n[rule:invented] x\n"
    )

    found = errors(root)
    assert "AGENTS.md marks rule no-merge 0 times (expected once)" in found
    assert "AGENTS.md marks rule one-mission 2 times (expected once)" in found
    assert "AGENTS.md marks an unknown rule invented" in found


def test_claude_md_must_import_agents_md_and_not_restate_rules(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "CLAUDE.md").write_text("# Claude\n\n1. [rule:no-merge] copied\n")

    found = errors(root)
    assert "CLAUDE.md does not import AGENTS.md (@AGENTS.md)" in found
    assert "CLAUDE.md restates rules: they belong in AGENTS.md only" in found


def test_missing_files_and_skills_are_errors(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "CLAUDE.md").unlink()
    (root / ".claude/skills/hardware-safety").unlink()
    (root / ".claude/skills/development-reporting").unlink()
    import shutil

    shutil.rmtree(root / ".agents/skills/development-reporting")

    found = errors(root)
    assert "CLAUDE.md is missing or empty" in found
    assert "Claude skill hardware-safety is missing (.claude/skills/hardware-safety)" in found
    assert any("Codex skill development-reporting is missing" in error for error in found)


def test_invalid_skill_frontmatter_is_an_error(tmp_path: Path) -> None:
    root = repository(tmp_path)
    target = root / ".agents/skills/hardware-safety/SKILL.md"
    target.write_text("---\nname: other\ndescription:\n---\n\nbody\n")
    (root / ".agents/skills/solid-code-design/SKILL.md").write_text("no frontmatter\n")
    (root / ".agents/skills/agent-token-efficiency/SKILL.md").unlink()

    found = errors(root)
    assert "Codex skill hardware-safety: frontmatter name is 'other'" in found
    assert "Claude skill hardware-safety: description missing or too long" in found
    assert "Codex skill solid-code-design: frontmatter name is None" in found
    assert "Codex skill agent-token-efficiency: no readable SKILL.md" in found


def test_drift_one_sided_skills_and_size_are_warnings(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".claude/skills/conventional-commits-pr").unlink()
    skill(root / ".claude/skills", "conventional-commits-pr", "An older copy.\n")
    skill(root / ".agents/skills", "codex-extra")
    skill(root / ".claude/skills", "claude-extra")
    skill(root / ".agents/skills", "python-project-quality", "line\n" * 250)

    report = config.check(root)
    assert report is not None and report.errors == []
    assert set(report.warnings) >= {
        "skill conventional-commits-pr: the Claude and Codex copies differ",
        "skill codex-extra exists only in .agents/skills",
        "skill claude-extra exists only in .claude/skills",
        "Codex skill python-project-quality: over 200 lines",
    }


def test_a_referenced_skill_must_exist(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "AGENTS.md").write_text(
        (root / "AGENTS.md").read_text() + "See skill `does-not-exist`.\n"
    )
    assert "a referenced skill does not exist: does-not-exist" in errors(root)


def test_configuration_that_git_would_commit_is_an_error(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".gitignore").write_text("# no machine-local entries\n")

    found = errors(root)
    assert "local-only configuration is not git-ignored: AGENTS.md" in found
    assert "local-only configuration is not git-ignored: .agents" in found

    subprocess.run(["git", "-C", str(root), "add", "-f", "AGENTS.md"], check=True)
    assert "local-only configuration is tracked by git: AGENTS.md" in errors(root)


def test_outside_a_git_repository_only_the_content_is_checked(tmp_path: Path) -> None:
    root = repository(tmp_path)
    import shutil

    shutil.rmtree(root / ".git")
    assert errors(root) == []
    assert config._git_ignored(tmp_path / "nowhere", "x") is None


# Linking a worktree ---------------------------------------------------------------------------


def worktree(tmp_path: Path) -> Path:
    other = tmp_path / "worktree"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    (other / ".gitignore").write_text((ROOT / ".gitignore").read_text(), encoding="utf-8")
    return other


def test_link_shares_the_configuration_and_git_still_ignores_it(tmp_path: Path) -> None:
    source, target = repository(tmp_path), worktree(tmp_path)

    actions = config.link(source, target)

    assert actions == [
        "linked AGENTS.md",
        "linked CLAUDE.md",
        "linked .agents/skills",
        "linked .claude/skills",
    ]
    assert (target / ".agents").is_dir() and not (target / ".agents").is_symlink()
    assert (target / ".claude/skills/hardware-safety/SKILL.md").is_file()
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status.strip() == "?? .gitignore"  # nothing of the configuration shows up
    assert errors(target) == []


def test_link_never_overwrites_and_is_idempotent(tmp_path: Path) -> None:
    source, target = repository(tmp_path), worktree(tmp_path)
    (target / "AGENTS.md").write_text("the worktree's own file\n")
    (target / ".claude").write_text("a file, not a directory\n")

    actions = config.link(source, target)
    again = config.link(source, target)

    assert "kept AGENTS.md (already present)" in actions
    assert "kept .claude (not a directory: left as it is)" in actions
    assert (target / "AGENTS.md").read_text() == "the worktree's own file\n"
    assert "kept .agents/skills (already present)" in again


def test_link_refuses_a_bad_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = repository(tmp_path)
    with pytest.raises(ValueError, match="same directory"):
        config.link(source, source)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert config.main(["link", "--from", str(empty), "--root", str(tmp_path)]) == 1
    assert "holds no AGENTS.md" in capsys.readouterr().err

    target = worktree(tmp_path)
    assert config.main(["link", "--from", str(source), "--root", str(target)]) == 0
    assert "linked AGENTS.md" in capsys.readouterr().out


def test_check_reports_errors_with_status_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(tmp_path)
    (root / "CLAUDE.md").write_text("# no import\n")

    assert config.main(["check", "--root", str(root)]) == 1
    assert "error: CLAUDE.md does not import AGENTS.md" in capsys.readouterr().out


def test_an_empty_agents_md_a_warning_line_and_git_absence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repository(tmp_path)
    (root / "AGENTS.md").write_text("")
    skill(root / ".agents/skills", "codex-extra")

    assert config.main(["check", "--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "error: AGENTS.md is missing or empty" in out
    assert "warning: skill codex-extra exists only in .agents/skills" in out

    def no_git(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(config.subprocess, "run", no_git)
    assert config._git_tracked(root, ["AGENTS.md"]) == []
    assert config._git_ignored(root, "AGENTS.md") is None


def test_link_skips_a_tool_the_source_does_not_configure(tmp_path: Path) -> None:
    source, target = repository(tmp_path), worktree(tmp_path)
    import shutil

    shutil.rmtree(source / ".claude")

    assert "linked .claude/skills" not in config.link(source, target)
    assert not (target / ".claude").exists()
