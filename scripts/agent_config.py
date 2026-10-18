"""Check and share the local agent configuration (AGENTS.md, CLAUDE.md, skills).

That configuration is machine-local and git-ignored (see `.gitignore`, "Machine-local development
metadata"): it lives in the main checkout only, so this tool never commits it. It:

- `check`: verifies the configuration is present, coherent and still local-only - every mandatory
  rule marked once in AGENTS.md, CLAUDE.md importing AGENTS.md without restating the rules, every
  required skill present for Codex (`.agents/skills/`) and Claude (`.claude/skills/`) with valid
  frontmatter, the referenced skills existing, and no configuration path tracked by git. Copies
  that drifted apart and oversized skills are warnings.
- `link`: makes a separate worktree see the main checkout's configuration, without overwriting
  anything: file links for AGENTS.md and CLAUDE.md, real `.agents/` and `.claude/` directories
  holding a `skills` link (a link named `.agents` would not be git-ignored).

Usage:
    python3 scripts/agent_config.py check [--root DIR]
    python3 scripts/agent_config.py link --from MAIN_CHECKOUT [--root DIR]

`check` exits 0 (coherent, warnings allowed), 1 (errors) or 2 (no local configuration here, as in
CI: nothing to check).
"""

from __future__ import annotations

import argparse
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
LOCAL_ONLY = ("AGENTS.md", "CLAUDE.md", ".agents", ".claude", ".codex", ".ai-private")
CODEX_SKILLS = Path(".agents/skills")
CLAUDE_SKILLS = Path(".claude/skills")
MAX_SKILL_LINES = 200
MAX_DESCRIPTION = 1024
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_SKILL_REFERENCE = re.compile(r"skill `([a-z0-9-]+)`")


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


def _check_skill(path: Path, name: str, owner: str, report: Report) -> str | None:
    """Validate one SKILL.md; returns its text when readable."""
    skill = path / "SKILL.md"
    try:
        text = skill.read_text(encoding="utf-8")
    except OSError:
        report.errors.append(f"{owner} skill {name}: no readable SKILL.md")
        return None
    fields = frontmatter(text)
    if fields.get("name") != name:
        report.errors.append(f"{owner} skill {name}: frontmatter name is {fields.get('name')!r}")
    description = fields.get("description", "")
    if not description or len(description) > MAX_DESCRIPTION:
        report.errors.append(f"{owner} skill {name}: description missing or too long")
    if text.count("\n") > MAX_SKILL_LINES:
        report.warnings.append(f"{owner} skill {name}: over {MAX_SKILL_LINES} lines")
    return text


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
        codex_text = _check_skill(codex, name, "Codex", report) if codex.exists() else None
        if not codex.exists():
            report.errors.append(f"Codex skill {name} is missing ({CODEX_SKILLS}/{name})")
        claude_skill = root / CLAUDE_SKILLS / name
        if not claude_skill.exists():
            report.errors.append(f"Claude skill {name} is missing ({CLAUDE_SKILLS}/{name})")
            continue
        claude_copy = _check_skill(claude_skill, name, "Claude", report)
        if codex_text is not None and claude_copy is not None and codex_text != claude_copy:
            report.warnings.append(f"skill {name}: the Claude and Codex copies differ")

    for directory, other in ((CODEX_SKILLS, CLAUDE_SKILLS), (CLAUDE_SKILLS, CODEX_SKILLS)):
        if (root / directory).is_dir():
            for entry in sorted((root / directory).iterdir()):
                if entry.is_dir() and not (root / other / entry.name).exists():
                    report.warnings.append(f"skill {entry.name} exists only in {directory}")

    for referenced in sorted(set(_SKILL_REFERENCE.findall(agents_text + claude_text))):
        if not (root / CODEX_SKILLS / referenced).exists():
            report.errors.append(f"a referenced skill does not exist: {referenced}")

    present = [path for path in LOCAL_ONLY if (root / path).exists()]
    for path in _git_tracked(root, present):
        report.errors.append(f"local-only configuration is tracked by git: {path}")
    for path in present:
        probe = path if (root / path).is_file() else f"{path}/probe"
        if _git_ignored(root, probe) is False:
            report.errors.append(f"local-only configuration is not git-ignored: {path}")
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
