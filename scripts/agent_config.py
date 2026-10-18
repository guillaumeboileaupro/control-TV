"""Check and share the agent configuration (AGENTS.md, CLAUDE.md, skills).

The shared configuration is versioned, so a fresh clone has it; local preferences and private
evidence stay in git-ignored files (`LOCAL_ONLY`). This tool:

- `check`: verifies the configuration is present and coherent - every mandatory rule marked once
  in AGENTS.md, CLAUDE.md importing AGENTS.md without restating the rules, every required skill
  present for Codex (`.agents/skills/`) and Claude (`.claude/skills/`) with valid frontmatter,
  the referenced skills existing, both agents discovering the same active skills with identical
  content, inactive skills (`.agents/inactive-skills/`, discovered by neither) valid and not
  also active, skill links relative and inside the repository, no private data (home paths,
  addresses, identifiers, e-mail addresses, keys) in the shared files, and no local-only path
  tracked or left unignored. A Claude copy instead of a link, oversized skills and a
  configuration this checkout does not version are warnings.
- `link`: lets a worktree of a branch older than the versioned configuration borrow the main
  checkout's, without overwriting anything: file links for AGENTS.md and CLAUDE.md, real
  `.agents/` and `.claude/` directories holding a `skills` link.

Usage:
    python3 scripts/agent_config.py check [--root DIR]
    python3 scripts/agent_config.py link --from MAIN_CHECKOUT [--root DIR]

`check` exits 0 (coherent, warnings allowed), 1 (errors) or 2 (no local configuration here, as in
CI: nothing to check).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

RULES = (
    "one-mission",
    "separate-worktrees",
    "other-agent-branches",
    "independent-review",
    "no-merge",
    "hardware-safety",
    "quality-gates",
    "conventional-commits",
    "interruptions",
    "uncommitted-work",
    "temporary-cleanup",
    "context-minimization",
    "final-report",
    "remaining-estimate",
)
REQUIRED_SKILLS = (
    "git-worktree-pr-workflow",
    "independent-code-review",
    "hardware-safety",
    "development-reporting",
    "agent-token-efficiency",
    "python-project-quality",
    "solid-code-design",
    "conventional-commits-pr",
)
VERSIONED = ("AGENTS.md", "CLAUDE.md", ".agents/skills", ".claude/skills")
LOCAL_ONLY = (  # a trailing "/" marks a directory
    ".ai-private/",
    ".codex/",
    ".agents/handoffs/",
    ".claude/handoffs/",
    ".claude/settings.local.json",
    "CLAUDE.local.md",
    "AGENTS.override.md",
)
CODEX_SKILLS = Path(".agents/skills")
CLAUDE_SKILLS = Path(".claude/skills")
INACTIVE_SKILLS = Path(".agents/inactive-skills")  # versioned, discovered by neither agent
MAX_SKILL_LINES = 200
MAX_DESCRIPTION = 1024
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SKILL_REFERENCE = re.compile(r"skill `([a-z0-9-]+)`")
# What must never be published in the shared configuration; a finding names its kind and line,
# never the matched text.
_PRIVATE = (
    ("a home directory path", re.compile(r"(?:/home/|/Users/|~/)[A-Za-z0-9._-]+")),
    ("an IPv4 address", re.compile(r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("a MAC address", re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")),
    ("a UUID", re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")),
    ("an e-mail address", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an access token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})")),
)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def frontmatter(text: str) -> dict[str, str]:
    """The `key: value` fields of a SKILL.md frontmatter block (empty when there is none)."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def _check_skill(path: Path, name: str, owner: str, report: Report) -> None:
    """Validate one SKILL.md."""
    skill = path / "SKILL.md"
    try:
        text = skill.read_text(encoding="utf-8")
    except OSError:
        report.errors.append(f"{owner} skill {name}: no readable SKILL.md")
        return
    fields = frontmatter(text)
    if fields.get("name") != name:
        report.errors.append(f"{owner} skill {name}: frontmatter name is {fields.get('name')!r}")
    description = fields.get("description", "")
    if not description or len(description) > MAX_DESCRIPTION:
        report.errors.append(f"{owner} skill {name}: description missing or too long")
    if text.count("\n") > MAX_SKILL_LINES:
        report.warnings.append(f"{owner} skill {name}: over {MAX_SKILL_LINES} lines")


def _git_tracked(root: Path, paths: Sequence[str]) -> list[str]:
    try:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", *paths],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line for line in listed.splitlines() if line]


def _git_ignored(root: Path, path: str) -> bool | None:
    """Whether git ignores `path` (None outside a git repository)."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", path],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode == 128:
        return None
    return completed.returncode == 0


def _skill_files(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _drift(root: Path) -> list[str]:
    """Every active skill whose Claude and Codex copies differ: both must read the same text."""
    codex, claude = root / CODEX_SKILLS, root / CLAUDE_SKILLS
    if not codex.is_dir() or not claude.is_dir():
        return []
    common = sorted(
        entry.name for entry in codex.iterdir() if entry.is_dir() and (claude / entry.name).is_dir()
    )
    return [
        f"skill {name}: the Claude and Codex copies differ (link {CLAUDE_SKILLS}/{name} to "
        f"../../{CODEX_SKILLS}/{name})"
        for name in common
        if _skill_files(codex / name) != _skill_files(claude / name)
    ]


def _inactive_problems(root: Path, report: Report) -> list[str]:
    """Inactive skills stay valid, so they can be reactivated, and are not also active."""
    inactive = root / INACTIVE_SKILLS
    if not inactive.is_dir():
        return []
    problems: list[str] = []
    for entry in sorted(inactive.iterdir()):
        if not entry.is_dir():
            continue
        _check_skill(entry, entry.name, "Inactive", report)
        for directory in (CODEX_SKILLS, CLAUDE_SKILLS):
            if os.path.lexists(root / directory / entry.name):
                problems.append(f"skill {entry.name} is both inactive and in {directory}")
    return problems


def _shared_files(root: Path) -> list[Path]:
    """AGENTS.md, CLAUDE.md and every file of both skill directories, each real file once."""
    files = [root / "AGENTS.md", root / "CLAUDE.md"]
    for directory in (CODEX_SKILLS, CLAUDE_SKILLS, INACTIVE_SKILLS):
        if (root / directory).is_dir():
            files += sorted(path for path in (root / directory).rglob("*") if path.is_file())
    unique: dict[Path, Path] = {}
    for path in files:
        if path.is_file():
            unique.setdefault(path.resolve(), path)
    return list(unique.values())


def _privacy_problems(root: Path) -> list[str]:
    problems: list[str] = []
    for path in _shared_files(root):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            problems.append(f"unreadable shared file: {path.relative_to(root)}")
            continue
        for number, line in enumerate(lines, 1):
            for kind, pattern in _PRIVATE:
                if pattern.search(line):
                    problems.append(
                        f"{path.relative_to(root)}:{number} contains {kind}: keep it out of the "
                        "shared configuration"
                    )
    return problems


def _link_problems(root: Path) -> list[str]:
    """Skill links a fresh clone could not resolve: absolute, or leaving the repository."""
    problems: list[str] = []
    for directory in (CODEX_SKILLS, CLAUDE_SKILLS):
        holder = root / directory
        if not holder.is_dir():
            continue
        repository = holder.resolve().parent.parent
        for entry in sorted(holder.iterdir()):
            if not entry.is_symlink():
                continue
            target = entry.readlink()
            resolved = entry.resolve()
            if target.is_absolute() or not resolved.is_relative_to(repository):
                problems.append(f"{directory}/{entry.name} links outside the repository")
            elif not resolved.exists():
                problems.append(f"{directory}/{entry.name} is a broken link")
    return problems


def check(root: Path) -> Report | None:
    """The configuration report for `root`, or None when no local configuration exists."""
    agents, claude = root / "AGENTS.md", root / "CLAUDE.md"
    if not agents.exists() and not claude.exists():
        return None
    report = Report()
    agents_text = agents.read_text(encoding="utf-8") if agents.is_file() else ""
    claude_text = claude.read_text(encoding="utf-8") if claude.is_file() else ""
    if not agents_text:
        report.errors.append("AGENTS.md is missing or empty")
    if not claude_text:
        report.errors.append("CLAUDE.md is missing or empty")
    for rule in RULES:
        count = agents_text.count(f"[rule:{rule}]")
        if count != 1:
            report.errors.append(f"AGENTS.md marks rule {rule} {count} times (expected once)")
    known = set(RULES)
    for rule in re.findall(r"\[rule:([a-z0-9-]+)\]", agents_text):
        if rule not in known:
            report.errors.append(f"AGENTS.md marks an unknown rule {rule}")
    if claude_text and "@AGENTS.md" not in claude_text:
        report.errors.append("CLAUDE.md does not import AGENTS.md (@AGENTS.md)")
    if "[rule:" in claude_text:
        report.errors.append("CLAUDE.md restates rules: they belong in AGENTS.md only")

    for name in REQUIRED_SKILLS:
        codex = root / CODEX_SKILLS / name
        if codex.exists():
            _check_skill(codex, name, "Codex", report)
        else:
            report.errors.append(f"Codex skill {name} is missing ({CODEX_SKILLS}/{name})")
        claude_skill = root / CLAUDE_SKILLS / name
        if not claude_skill.exists():
            report.errors.append(f"Claude skill {name} is missing ({CLAUDE_SKILLS}/{name})")
            continue
        _check_skill(claude_skill, name, "Claude", report)

    report.errors += _drift(root)
    for directory, other in ((CODEX_SKILLS, CLAUDE_SKILLS), (CLAUDE_SKILLS, CODEX_SKILLS)):
        if (root / directory).is_dir():
            for entry in sorted((root / directory).iterdir()):
                if entry.is_dir() and not (root / other / entry.name).exists():
                    report.errors.append(
                        f"skill {entry.name} exists only in {directory}: both agents must "
                        "discover the same skills"
                    )
    if (root / CLAUDE_SKILLS).is_dir():
        for entry in sorted((root / CLAUDE_SKILLS).iterdir()):
            if (
                entry.is_dir()
                and not entry.is_symlink()
                and (root / CODEX_SKILLS / entry.name).is_dir()
            ):
                report.warnings.append(
                    f"Claude skill {entry.name} is a copy: link it to "
                    f"../../{CODEX_SKILLS}/{entry.name}"
                )
    report.errors += _inactive_problems(root, report)

    for referenced in sorted(set(_SKILL_REFERENCE.findall(agents_text + claude_text))):
        if not (root / CODEX_SKILLS / referenced).exists():
            report.errors.append(f"a referenced skill does not exist: {referenced}")

    report.errors += _link_problems(root)
    report.errors += _privacy_problems(root)

    for path in _git_tracked(root, [path.rstrip("/") for path in LOCAL_ONLY]):
        report.errors.append(f"local-only file is tracked by git: {path}")
    for path in LOCAL_ONLY:
        probe = f"{path}probe" if path.endswith("/") else path
        if _git_ignored(root, probe) is False:
            report.errors.append(f"local-only path is not git-ignored: {path}")
    if _git_ignored(root, "AGENTS.md") is not None:
        tracked = set(_git_tracked(root, VERSIONED))
        for path in VERSIONED:
            if not any(name == path or name.startswith(f"{path}/") for name in tracked):
                report.warnings.append(
                    f"{path} is not versioned in this checkout: a fresh clone would lack it"
                )
    return report


def link(source: Path, root: Path) -> list[str]:
    """Make `root` (a worktree) see `source`'s configuration; never overwrite. Returns actions."""
    source, root = source.resolve(), root.resolve()
    if source == root:
        raise ValueError("the source and the worktree are the same directory")
    if not (source / "AGENTS.md").is_file():
        raise ValueError(f"{source} holds no AGENTS.md")
    actions: list[str] = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        target = root / name
        if target.exists() or target.is_symlink():
            actions.append(f"kept {name} (already present)")
        elif (source / name).is_file():
            target.symlink_to(source / name)
            actions.append(f"linked {name}")
    for directory in (".agents", ".claude"):
        if not (source / directory / "skills").is_dir():
            continue
        holder = root / directory
        if holder.is_symlink() or (holder.exists() and not holder.is_dir()):
            actions.append(f"kept {directory} (not a directory: left as it is)")
            continue
        holder.mkdir(exist_ok=True)
        skills = holder / "skills"
        if skills.exists() or skills.is_symlink():
            actions.append(f"kept {directory}/skills (already present)")
        else:
            skills.symlink_to(source / directory / "skills")
            actions.append(f"linked {directory}/skills")
    return actions


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser("check", help="verify the local agent configuration")
    checking.add_argument("--root", type=Path, default=Path.cwd())
    linking = commands.add_parser("link", help="share the main checkout's configuration")
    linking.add_argument("--from", dest="source", type=Path, required=True)
    linking.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    if arguments.command == "link":
        try:
            for action in link(arguments.source, arguments.root):
                print(action)
        except (ValueError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        return 0
    report = check(arguments.root)
    if report is None:
        print("no local agent configuration here (AGENTS.md/CLAUDE.md): nothing to check")
        return 2
    for warning in report.warnings:
        print(f"warning: {warning}")
    for problem in report.errors:
        print(f"error: {problem}")
    print(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return 1 if report.errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
