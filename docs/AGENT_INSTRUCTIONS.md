# Agent instructions and skills (Claude and Codex)

How the two coding agents get their rules and procedures, so prompts stay short. The files
themselves are **machine-local and git-ignored** (`.gitignore`, "Machine-local development
metadata"): this document and `scripts/agent_config.py` are the only tracked parts.

## Where things live

| What                                                                                                  | File              | Read by                                    |
| ----------------------------------------------------------------------------------------------------- | ----------------- | ------------------------------------------ |
| Mandatory common rules (14, each marked `[rule:…]`), scope, architecture, cleanup, definition of done | `AGENTS.md`       | Codex directly; Claude through `CLAUDE.md` |
| Claude-only notes, importing the rules with `@AGENTS.md`                                              | `CLAUDE.md`       | Claude Code                                |
| Procedures (skills): `<name>/SKILL.md` with `name` and `description` frontmatter                      | `.agents/skills/` | Codex                                      |
| The same skills for Claude: links to `.agents/skills/<name>` (one source), or older copies            | `.claude/skills/` | Claude Code (Skill tool)                   |
| Backups of earlier versions, private evidence                                                         | `.ai-private/`    | owner                                      |

Rules are written once (in `AGENTS.md`); a skill holds a procedure and never restates the rules;
a prompt names the mission and the skills, never re-pastes either.

## Skills

| Skill                                                                                                                            | Use it for                                                                             |
| -------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `git-worktree-pr-workflow`                                                                                                       | any branch, worktree, merge, push or PR; preserving other agents' and uncommitted work |
| `independent-code-review`                                                                                                        | reviewing a PR at an exact SHA; labelling a self-review                                |
| `hardware-safety`                                                                                                                | anything that could touch the TV, ADB, the phone, the system, the network, a capture   |
| `development-reporting`                                                                                                          | the end-of-mission report: local vs CI numbers, risks, time, remaining estimate        |
| `agent-token-efficiency`                                                                                                         | every mission: targeted reads, filtered output, short prompts                          |
| `python-project-quality`, `solid-code-design`, `typescript-no-any`, `conventional-commits-pr`                                    | quality gates and commits                                                              |
| disk hygiene, CI diagnosis, dependency review, release audit, multiplatform release, MCP/device bridge, bootstrap, accessibility | their domain                                                                           |

## Worktrees

A worktree created with `git worktree add` does not contain the ignored files. Make it see the main
checkout's configuration (nothing is overwritten; git keeps ignoring it):

```bash
python3 scripts/agent_config.py link --from ~/Documents/Tools/control-TV
```

`.agents/` and `.claude/` are created as real directories holding a `skills` link: a link named
`.agents` itself would not match the `/.[a]gents/` ignore pattern and could be committed.

## Checking the configuration

```bash
python3 scripts/agent_config.py check      # 0 coherent, 1 errors, 2 nothing here (CI)
```

Errors: a rule marked zero or several times or unknown, `CLAUDE.md` not importing `AGENTS.md` or
restating rules, a required skill missing for either agent or with bad frontmatter, a skill named
in the rules that does not exist, configuration tracked by git or not ignored. Warnings: Claude and
Codex copies that drifted apart, skills present for one agent only, skills over 200 lines.

To add or change a skill: edit `.agents/skills/<name>/SKILL.md`, link it once with
`ln -s ../../.agents/skills/<name> .claude/skills/<name>`, then run `check`.

## A short prompt

```text
Mission : corriger les deux P2 de la revue Codex sur #83 (fedda7f).
Branche : celle de #83, worktree dédié. Ne pas toucher #84/#85.
Skills : git-worktree-pr-workflow, python-project-quality, solid-code-design, development-reporting.
Livrable : rapport standard, SHA, CI. Pas de fusion.
```

Everything else - no merge without authorization, hardware safety, coverage, cleanup, Conventional
Commits, the report format, the remaining estimate - comes from `AGENTS.md` and the skills.
