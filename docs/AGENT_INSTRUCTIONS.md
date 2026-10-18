# Agent instructions and skills (Claude and Codex)

How the two coding agents get their rules and procedures, so prompts stay short. The shared
configuration is **versioned**: a fresh clone and every worktree have it. Local preferences and
private evidence stay in git-ignored files (`.gitignore`, "Machine-local agent preferences").

## Where things live

| What                                                                                                         | File                                                                              | Read by                                    | Versioned |
| ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- | ------------------------------------------ | --------- |
| Mandatory common rules (14, each marked `[rule:…]`), scope, architecture, cleanup, definition of done        | `AGENTS.md`                                                                       | Codex directly; Claude through `CLAUDE.md` | yes       |
| Claude-only notes, importing the rules with `@AGENTS.md`                                                     | `CLAUDE.md`                                                                       | Claude Code                                | yes       |
| Skills: `<name>/SKILL.md` with `name` and `description` frontmatter                                          | `.agents/skills/`                                                                 | Codex                                      | yes       |
| The same skills for Claude: relative links to `.agents/skills/<name>`, or Claude variants not yet reconciled | `.claude/skills/`                                                                 | Claude Code (Skill tool)                   | yes       |
| Personal settings                                                                                            | `CLAUDE.local.md`, `.claude/settings.local.json`, `AGENTS.override.md`, `.codex/` | each agent                                 | no        |
| Handoffs, backups, hardware evidence                                                                         | `.agents/handoffs/`, `.claude/handoffs/`, `.ai-private/`                          | owner                                      | no        |

Rules are written once (in `AGENTS.md`); a skill holds a procedure and never restates the rules;
a prompt names the mission and the skills, never re-pastes either. Nothing private goes into the
shared files: no home paths, addresses, device identifiers, e-mail addresses, keys or tokens
(`check` refuses them).

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

`git worktree add` checks the configuration out with the branch. Only a branch older than the
versioned configuration lacks it; borrow the main checkout's there (nothing is overwritten, and
that branch's ignore rules keep it out of commits):

```bash
python3 scripts/agent_config.py link --from <main checkout>
```

## Checking the configuration

```bash
python3 scripts/agent_config.py check      # 0 coherent, 1 errors, 2 nothing here (CI)
```

Errors: a rule marked zero or several times or unknown, `CLAUDE.md` not importing `AGENTS.md` or
restating rules, a required skill missing for either agent or with bad frontmatter, a skill named
in the rules that does not exist, a skill link that is absolute, broken or leaves the repository,
private data in a shared file, a local-only file tracked or not ignored. Warnings: Claude and
Codex copies that differ, skills present for one agent only, skills over 200 lines, a checkout
that does not version the configuration. A test clones the repository and checks that both
agents discover the same complete skill set.

To add or change a skill: edit `.agents/skills/<name>/SKILL.md`, link it once with
`ln -s ../../.agents/skills/<name> .claude/skills/<name>`, then run `check`. To reconcile a Claude
variant, the owner chooses the text, then the variant is replaced by that link.

## A short prompt

```text
Mission : corriger les deux P2 de la revue Codex sur #83 (fedda7f).
Branche : celle de #83, worktree dédié. Ne pas toucher #84/#85.
Skills : git-worktree-pr-workflow, python-project-quality, solid-code-design, development-reporting.
Livrable : rapport standard, SHA, CI. Pas de fusion.
```

Everything else - no merge without authorization, hardware safety, coverage, cleanup, Conventional
Commits, the report format, the remaining estimate - comes from `AGENTS.md` and the skills.
