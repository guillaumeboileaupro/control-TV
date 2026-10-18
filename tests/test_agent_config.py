"""The local agent configuration check and worktree link (temporary repositories only)."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
# The ignore rules of a branch older than the versioned configuration (what `link` is for).
LEGACY_GITIGNORE = (
    "/[A]GENTS.md\n/[C]LAUDE.md\n/.[a]gents/\n/.[c]laude/\n/.[c]odex/\n/.ai-private/\n"
)


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
        (root / ".claude/skills" / name).symlink_to(Path("../../.agents/skills") / name)
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


def test_drift_and_one_sided_skills_are_errors(tmp_path: Path) -> None:
    """Both agents must discover the same active skills and read the same text."""
    root = repository(tmp_path)
    (root / ".claude/skills/conventional-commits-pr").unlink()
    skill(root / ".claude/skills", "conventional-commits-pr", "An older copy.\n")
    skill(root / ".agents/skills", "codex-extra")
    skill(root / ".claude/skills", "claude-extra")

    found = errors(root)
    assert (
        "skill conventional-commits-pr: the Claude and Codex copies differ (link "
        ".claude/skills/conventional-commits-pr to ../../.agents/skills/conventional-commits-pr)"
    ) in found
    assert (
        "skill codex-extra exists only in .agents/skills: both agents must discover the same skills"
    ) in found
    assert (
        "skill claude-extra exists only in .claude/skills: both agents must discover the same "
        "skills"
    ) in found


def test_an_identical_copy_and_an_oversized_skill_are_warnings(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".claude/skills/conventional-commits-pr").unlink()
    shutil.copytree(
        root / ".agents/skills/conventional-commits-pr",
        root / ".claude/skills/conventional-commits-pr",
    )
    skill(root / ".agents/skills", "python-project-quality", "line\n" * 250)

    report = config.check(root)
    assert report is not None and report.errors == []
    assert set(report.warnings) >= {
        "Claude skill conventional-commits-pr is a copy: link it to "
        "../../.agents/skills/conventional-commits-pr",
        "Codex skill python-project-quality: over 200 lines",
    }


def test_inactive_skills_are_valid_versioned_and_never_also_active(tmp_path: Path) -> None:
    root = repository(tmp_path)
    inactive = root / ".agents/inactive-skills"
    skill(inactive, "archived-bridge")
    assert errors(root) == []

    skill(inactive, "hardware-safety")  # also active for both agents
    (inactive / "broken").mkdir()
    (inactive / "notes.md").write_text("an index, not a skill\n")
    (inactive / "archived-bridge/SKILL.md").write_text(
        "---\nname: archived-bridge\ndescription: x\n---\n\nsee /home/someone\n"
    )

    found = errors(root)
    assert "skill hardware-safety is both inactive and in .agents/skills" in found
    assert "skill hardware-safety is both inactive and in .claude/skills" in found
    assert "Inactive skill broken: no readable SKILL.md" in found
    assert any(
        error.startswith(".agents/inactive-skills/archived-bridge/SKILL.md:6 contains a home")
        for error in found
    )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "?? .agents/inactive-skills/archived-bridge/SKILL.md" in status  # versioned, not ignored


def test_a_referenced_skill_must_exist(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "AGENTS.md").write_text(
        (root / "AGENTS.md").read_text() + "See skill `does-not-exist`.\n"
    )
    assert "a referenced skill does not exist: does-not-exist" in errors(root)


def test_local_preferences_that_git_would_commit_are_errors(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / ".gitignore").write_text("# no machine-local entries\n")
    (root / "CLAUDE.local.md").write_text("my preferences\n")

    found = errors(root)
    assert "local-only path is not git-ignored: .ai-private/" in found
    assert "local-only path is not git-ignored: .claude/settings.local.json" in found
    assert "local-only path is not git-ignored: CLAUDE.local.md" in found

    subprocess.run(["git", "-C", str(root), "add", "-f", "CLAUDE.local.md"], check=True)
    assert "local-only file is tracked by git: CLAUDE.local.md" in errors(root)


def test_the_project_ignores_local_preferences_but_versions_the_shared_configuration(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    for local in (".agents/handoffs/h.md", ".claude/settings.local.json", ".codex/config.toml"):
        (root / local).parent.mkdir(parents=True, exist_ok=True)
        (root / local).write_text("local\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    staged = subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    assert {"AGENTS.md", "CLAUDE.md", ".agents/skills/hardware-safety/SKILL.md"} <= set(staged)
    assert ".claude/skills/hardware-safety" in staged  # the link itself, resolved in any clone
    assert not [
        name for name in staged if "handoffs" in name or "local" in name or ".codex" in name
    ]
    report = config.check(root)
    assert report is not None and report.errors == []
    assert not [warning for warning in report.warnings if "not versioned" in warning]


def test_a_checkout_that_does_not_version_the_configuration_is_warned(tmp_path: Path) -> None:
    report = config.check(repository(tmp_path))
    assert report is not None and report.errors == []
    assert "AGENTS.md is not versioned in this checkout: a fresh clone would lack it" in (
        report.warnings
    )


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("see /home/someone/project", "a home directory path"),
        ("run it from ~/Documents/x", "a home directory path"),
        ("the TV at 192.168.1.20", "an IPv4 address"),
        ("adapter 00:1A:2b:3C:4d:5E", "a MAC address"),
        ("device 123e4567-e89b-12d3-a456-426614174000", "a UUID"),
        ("write to someone@example.org", "an e-mail address"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----", "a private key"),
        ("token ghp_" + "a" * 36, "an access token"),
    ],
)
def test_private_data_in_the_shared_configuration_is_an_error(
    tmp_path: Path, text: str, kind: str
) -> None:
    root = repository(tmp_path)
    target = root / ".agents/skills/hardware-safety/SKILL.md"
    target.write_text(target.read_text() + text + "\n")

    found = [error for error in errors(root) if "contains" in error]
    assert found == [
        f".agents/skills/hardware-safety/SKILL.md:9 contains {kind}: keep it out of the shared "
        "configuration"
    ]  # reported once although Claude reaches the same file through its link
    assert text not in " ".join(found)


def test_loopback_addresses_and_unreadable_shared_files(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "AGENTS.md").write_text((root / "AGENTS.md").read_text() + "bind 127.0.0.1, 0.0.0.0\n")
    (root / ".agents/skills/hardware-safety/notes.md").write_bytes(b"\xff\xfe")

    found = errors(root)
    assert not [error for error in found if "IPv4" in error]
    assert "unreadable shared file: .agents/skills/hardware-safety/notes.md" in found
    report = config.check(root)
    assert report is not None
    # The Claude link reaches the same directory: one copy, no drift.
    assert not [error for error in report.errors if "copies differ" in error]


def test_skill_links_must_stay_inside_the_repository(tmp_path: Path) -> None:
    root = repository(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    skill(elsewhere, "hardware-safety")
    (root / ".claude/skills/hardware-safety").unlink()
    (root / ".claude/skills/hardware-safety").symlink_to(elsewhere / "hardware-safety")
    (root / ".claude/skills/solid-code-design").unlink()
    (root / ".claude/skills/solid-code-design").symlink_to(Path("../../.agents/skills/gone"))

    found = errors(root)
    assert ".claude/skills/hardware-safety links outside the repository" in found
    assert ".claude/skills/solid-code-design is a broken link" in found
    assert config._drift(tmp_path) == []  # no skill directories: nothing to compare
    assert config._link_problems(tmp_path) == []


def test_outside_a_git_repository_only_the_content_is_checked(tmp_path: Path) -> None:
    root = repository(tmp_path)
    shutil.rmtree(root / ".git")
    assert errors(root) == []
    assert config._git_ignored(tmp_path / "nowhere", "x") is None


# Linking a worktree ---------------------------------------------------------------------------


def worktree(tmp_path: Path) -> Path:
    """A worktree of a branch older than the versioned configuration."""
    other = tmp_path / "worktree"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    (other / ".gitignore").write_text(LEGACY_GITIGNORE, encoding="utf-8")
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
    # The old branch's rules predate the new local files; nothing else is wrong.
    assert errors(target) == [
        "local-only path is not git-ignored: CLAUDE.local.md",
        "local-only path is not git-ignored: AGENTS.override.md",
    ]


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
    assert "warning: AGENTS.md is not versioned in this checkout" in out

    def no_git(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("git")

    monkeypatch.setattr(config.subprocess, "run", no_git)
    assert config._git_tracked(root, ["AGENTS.md"]) == []
    assert config._git_ignored(root, "AGENTS.md") is None


def test_link_skips_a_tool_the_source_does_not_configure(tmp_path: Path) -> None:
    source, target = repository(tmp_path), worktree(tmp_path)
    shutil.rmtree(source / ".claude")

    assert "linked .claude/skills" not in config.link(source, target)
    assert not (target / ".claude").exists()


# A fresh clone -----------------------------------------------------------------------------


def test_a_fresh_clone_has_the_whole_agent_configuration(tmp_path: Path) -> None:
    """Clone this repository's HEAD: both agents must discover the same, complete skill set."""
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    if head.returncode != 0:
        pytest.skip("not a git checkout")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(ROOT), str(clone)], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "checkout", "-q", "--detach", head.stdout.strip()], check=True
    )

    report = config.check(clone)

    assert report is not None and report.errors == [] and report.warnings == []
    codex = {path.parent.name for path in (clone / ".agents/skills").glob("*/SKILL.md")}
    claude = {path.parent.name for path in (clone / ".claude/skills").glob("*/SKILL.md")}
    assert set(config.REQUIRED_SKILLS) <= codex and codex == claude
    # Every Claude skill is a relative link to the one common copy.
    entries = [entry for entry in (clone / ".claude/skills").iterdir() if entry.name != "README.md"]
    assert entries and all(entry.is_symlink() for entry in entries)
    assert all(
        (entry / "SKILL.md").read_bytes()
        == (clone / ".agents/skills" / entry.name / "SKILL.md").read_bytes()
        for entry in entries
    )
    # The inactive skills are versioned but discovered by neither agent.
    inactive = {path.parent.name for path in (clone / ".agents/inactive-skills").glob("*/SKILL.md")}
    assert inactive == {"local-device-mcp-bridge", "project-bootstrap-context"}
    assert not inactive & (codex | claude)
    assert not [path for path in config.LOCAL_ONLY if (clone / path.rstrip("/")).exists()]
