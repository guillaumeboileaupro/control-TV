---
name: agent-token-efficiency
description: Keep model context and tool output small - targeted reads, filtered command output, reuse of earlier results, short prompts that name skills. Use on every mission, especially long sessions, large files, CI logs and multi-agent work.
---

# Token efficiency

- Read only what is needed: `grep -n` to locate, then a line range (`sed -n 'a,bp'`); never print
  whole large files, lockfiles, generated files or full logs.
- Filter command output at the source (`| grep -E 'passed|failed|error'`, `| tail -n 5`,
  `-q` flags); for CI read the job log through `gh api .../jobs/<id>/logs | grep`.
- Do not re-read a file you just wrote or re-run a check whose result is already known; reuse
  results reported earlier in the session.
- Batch independent commands in one call; wait for long jobs with one bounded loop, not polling.
- Prompts to agents stay short: mission, PR/branch/SHA, constraints that differ from the rules,
  and the skills to apply (rules live in `AGENTS.md`, procedures in skills - never re-paste them).
- Spawn sub-agents only when asked. Keep reports to the `development-reporting` sections.
