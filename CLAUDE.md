# Claude - control-TV

@AGENTS.md

All shared rules, scope and definition of done are in `AGENTS.md` (imported above); this file only
adds what is specific to Claude Code. Previous version: `.ai-private/agent-config-backups/`.

## Permanent dashboard - mandatory on every prompt

Read `DASHBOARD.md` at the start of every prompt or resumed task, in addition to the
shared rules imported from `AGENTS.md`. At the end of each work iteration, update
the affected status, PR/CI evidence, blockers, next actions and changelog in
`DASHBOARD.md`. Preserve the complete global list of unfinished work, including
D1 duplication and D2 extension, even when the current task concerns another area.
Synchronize `TODO.md` and `DEVELOPMENT_PLAN.md` for code changes.

Never edit main directly or another agent's branch in order to update this dashboard:
include changes in the active authorized PR, or report the pending update when blocked.
Never fabricate hardware results or time estimates; only the coordinator sets the
single min/max/median remaining-time column. Follow the owner's one-agent,
one-open-PR rules. This dashboard complements, not replaces, `AGENTS.md`.

## Mandatory progress updates

The mandatory documentation-update and time-reporting policies are inherited from
`AGENTS.md`. Apply them on every mission without another owner reminder.

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
- Graphify: the rules are in `AGENTS.md` (Code knowledge graph); `/graphify` loads the skill
  `graphify`. The optional `graphify hook-guard` hooks that `graphify claude install --project`
  writes to `.claude/settings.json` stay local (git-ignored) and also write under
  `graphify-out/`.
