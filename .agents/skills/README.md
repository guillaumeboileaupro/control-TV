# Agent skills

One directory per skill, `<name>/SKILL.md` with `name` and `description` frontmatter. Codex reads
them here; Claude reads `.claude/skills/<name>`, a link to the same directory or, until the owner
reconciles them, a Claude variant. Usage and the consistency check: `docs/AGENT_INSTRUCTIONS.md`,
`python3 scripts/agent_config.py check`.
