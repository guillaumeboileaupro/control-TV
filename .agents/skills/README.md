# Agent skills

One directory per skill, `<name>/SKILL.md` with `name` and `description` frontmatter. Codex reads
them here; Claude reads `.claude/skills/<name>`, a relative link to the same directory, so both
agents read one text. Skills neither agent should discover live in `../inactive-skills/`. Usage
and the consistency check: `docs/AGENT_INSTRUCTIONS.md`, `python3 scripts/agent_config.py check`.
