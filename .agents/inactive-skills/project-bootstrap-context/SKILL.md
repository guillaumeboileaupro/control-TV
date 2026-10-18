---
name: project-bootstrap-context
description: Bootstrap agent-ready software projects with concise product context, architecture, planning and contributor documentation. Use when starting or restructuring a repository for Codex and Claude collaboration.
---

# Project bootstrap context

Reusable for other repositories; inactive in control-TV, which is already bootstrapped (to use it
here, link it into `.agents/skills/` and `.claude/skills/`).

Create only documentation supported by the actual project and the owner's decisions.

- Inspect the existing code and preserve repository-specific instructions.
- Define product purpose, users, platforms, scope, exclusions, privacy constraints and acceptance
  criteria.
- Document the real architecture, module boundaries, data flow, persistence, external
  dependencies and build targets.
- `AGENTS.md` holds the shared agent rules; `CLAUDE.md` imports it and adds only what is specific
  to Claude.
- Keep a prioritized roadmap and TODO with measurable completion conditions.
- Keep the README operational: prerequisites, setup, launch, stop, tests, build, install,
  uninstall, limitations, license.
- Never claim code, tests, packages or platforms exist before verifying them.
