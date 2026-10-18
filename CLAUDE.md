# Claude - control-TV

@AGENTS.md

All shared rules, scope and definition of done are in `AGENTS.md` (imported above); this file only
adds what is specific to Claude Code. Previous version: `.ai-private/agent-config-backups/`.

## Mandatory progress updates

Before completing any code or documentation change, follow [rule:progress-docs] in
`AGENTS.md`: update both `TODO.md` and `DEVELOPMENT_PLAN.md` in the same branch/PR, with
implementation, CI and hardware validation recorded separately. This is automatic for every
mission; the owner must not have to request it again. Do not report personal work durations
or estimate remaining hours.

## Claude Code specifics

- Skills: `.claude/skills/<name>` - relative links to the common copies in `.agents/skills/`
  (one source). Load a skill with the Skill tool before that class of work; do not re-paste skill
  text into prompts or reports.
- This configuration is versioned: every clone and worktree has it. Personal settings go in
  git-ignored `CLAUDE.local.md` or `.claude/settings.local.json`. A branch older than the
  versioned configuration can borrow it with `python3 scripts/agent_config.py link --from
  <main checkout>` (no overwrite).
- Claude's persistent memory (outside the repository) holds owner preferences and project
  context; it complements, never replaces, these rules.
- Development commands, including `npx --prefix ui tauri dev` from the repository root, are in
  `README.md`.
