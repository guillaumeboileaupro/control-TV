# Inactive agent skills

Skills that neither agent discovers in this repository (Codex reads `.agents/skills/`, Claude
`.claude/skills/`). To activate one, move it to `.agents/skills/` and link it from
`.claude/skills/`, then run `python3 scripts/agent_config.py check`.

| Skill                       | Status                                                              |
| --------------------------- | ------------------------------------------------------------------- |
| `local-device-mcp-bridge`   | archived: MCP was abandoned on 2026-10-12                           |
| `project-bootstrap-context` | reusable for other repositories; control-TV is already bootstrapped |

The divergent Claude and Codex versions that preceded the common skills (2026-10-18) stay in the
Git history: `git show 507e0528993d051de12be1ebdf6f07ba3d2d2183:.claude/skills/<name>/SKILL.md`
(or `.agents/skills/<name>/SKILL.md`).
