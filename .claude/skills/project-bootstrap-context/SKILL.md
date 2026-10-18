---
name: project-bootstrap-context
description: Bootstrap agent-ready software projects with concise product context, architecture, planning, and contributor documentation. Use when starting or restructuring a repository for Codex and Claude collaboration.
---

# Project Bootstrap Context

Create only documentation supported by the actual project and user decisions.

- Inspect existing code and preserve repository-specific instructions.
- Define product purpose, users, supported platforms, scope, exclusions, privacy constraints, and acceptance criteria.
- Document the real architecture, module boundaries, data flow, persistence, external dependencies, and build targets.
- Create `AGENTS.md` for shared agent rules and `CLAUDE.md` only for Claude-specific differences.
- Maintain a prioritized roadmap and TODO with measurable completion conditions.
- Keep the README operational: prerequisites, setup, launch, stop, tests, build, install, uninstall, limitations, and license.
- Do not claim code, tests, packages, or platforms exist before verifying them.

Run `python scripts/check_context.py <project-root>` to report missing context files without modifying the project.