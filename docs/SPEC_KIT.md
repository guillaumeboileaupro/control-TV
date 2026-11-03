# Spec Kit in Control-TV (Claude and Codex)

[GitHub Spec Kit](https://github.com/github/spec-kit) adds a spec → plan → tasks → implement flow
for a **new feature or substantial change the owner asks to specify**. It does not replace the
project rules or records: [`AGENTS.md`](../AGENTS.md) stays the single source of rules, and
`DASHBOARD.md`, `TODO.md` and `DEVELOPMENT_PLAN.md` stay the records of scope and progress.

## What is installed

| What | Where | Versioned |
| --- | --- | --- |
| Constitution adapted to Control-TV (points to `AGENTS.md`, never restates it) | `.specify/memory/constitution.md` | yes |
| Templates (spec, plan, tasks, checklist, constitution) | `.specify/templates/` | yes |
| Helper scripts (feature directory, prerequisites); no `git` command, no branch | `.specify/scripts/bash/` | yes |
| Install record (version, integrations, file hashes) | `.specify/init-options.json`, `.specify/integration.json`, `.specify/integrations/*.manifest.json` | yes |
| Machine-local state (current feature pointer, local overrides) | listed in `.specify/.gitignore` | no |
| Ten `speckit-*` skills, one source | `.agents/skills/speckit-*/SKILL.md` | yes |
| The same skills for Claude Code | `.claude/skills/speckit-*` → relative links to the above | yes |
| A feature's documents, created on first use only | `specs/<NNN-feature>/` | yes |

Installed from the official release `specify-cli` **v1.2.6** (2026-11-02), run with Python 3.12 by
`uv`, without a global install:

```bash
uvx --python 3.12 --from git+https://github.com/github/spec-kit.git@v1.2.6 specify init --here \
  --integration claude --script sh --non-interactive --force --ignore-agent-tools
uvx --python 3.12 --from git+https://github.com/github/spec-kit.git@v1.2.6 specify \
  integration install codex --integration-options="--skills"
```

Neither command changed an existing file. Spec Kit writes a Claude copy and a Codex copy of each
skill; they differ only in the Claude front matter (`argument-hint`, `user-invocable`,
`disable-model-invocation`) and in how the text names another command (`/speckit-plan` for Claude,
`$speckit-plan` for Codex). To keep the project's single-source rule
(`python3 scripts/agent_config.py check`), the Claude version is the one source in
`.agents/skills/`; Codex ignores the extra front matter keys. In Codex, read every `/speckit-…`
mentioned in a skill as `$speckit-…`.

## Commands

| Claude Code | Codex | Purpose |
| --- | --- | --- |
| `/speckit-constitution` | `$speckit-constitution` | Amend the constitution (owner approval needed) |
| `/speckit-specify <description>` | `$speckit-specify <description>` | Write `specs/<NNN-feature>/spec.md` |
| `/speckit-plan` | `$speckit-plan` | Write the plan, checked against the constitution |
| `/speckit-tasks` | `$speckit-tasks` | Write `tasks.md` from the plan |
| `/speckit-implement` | `$speckit-implement` | Implement the tasks |
| `/speckit-clarify`, `/speckit-analyze`, `/speckit-checklist`, `/speckit-converge`, `/speckit-taskstoissues` | same with `$` | Optional: only when asked |

## How to use it here

1. Only for a feature the owner wants specified. Missions that fix a known defect, review a PR or
   run hardware acceptance do not use Spec Kit. No retroactive specs for existing features.
2. Start in the mission's own worktree and branch ([rule:separate-worktrees]); Spec Kit does not
   create or switch branches (its optional `git` extension is not installed).
3. `specify` → `plan` → `tasks` → `implement`. Skip the optional commands unless asked.
4. Carry the outcome into `DASHBOARD.md`, `TODO.md` and `DEVELOPMENT_PLAN.md` in the same branch;
   `tasks.md` is a working list, not a second dashboard. Hardware claims stay classified
   (implemented / CI / hardware) and hardware steps still need the owner's authorization.
5. Clean up as for any mission; never commit `.specify/feature.json` or other files listed in
   `.specify/.gitignore`.

## Maintenance

- Check: `uvx --python 3.12 --from git+https://github.com/github/spec-kit.git@v1.2.6 specify
  integration status` (read-only, verified). Expected result: `WARNING` with
  `managed-files-modified (codex)` for the 10 unified skills; this is the single-source choice
  above, not a fault. Any other finding needs a look.
- Upgrading means choosing a new release tag and re-running the two commands above in a dedicated
  branch, then restoring the single source: copy each `.claude/skills/speckit-*/SKILL.md` over
  `.agents/skills/speckit-*/SKILL.md`, replace the Claude directories with relative links and run
  `python3 scripts/agent_config.py check`. Review the whole diff before committing, and keep
  `.specify/memory/constitution.md` (project content, not a template). The behaviour of
  `specify integration upgrade` on the unified files has not been tried: never run it with
  `--force` without that review.
