---
name: dashboard-maintenance
description: Mandatory Control-TV dashboard read at every prompt start and evidence-based update at every iteration end; preserves the full backlog and coordinates AGENTS.md, TODO.md and DEVELOPMENT_PLAN.md.
---

# Dashboard maintenance

## Trigger
Apply to **every** Claude or Codex mission, including review, code, test, audit, or handoff. Load at the start and use the end-of-iteration checklist even for short tasks.

## Start of iteration
1. Read `AGENTS.md`, `CLAUDE.md` when using Claude, `DASHBOARD.md`, `TODO.md`, and `DEVELOPMENT_PLAN.md`.
2. Verify the active branch, HEAD, open PRs, relevant CI and latest task handoff. Distinguish a reported status from one checked against GitHub or hardware logs.
3. Find the current task in the dashboard, its prerequisites, owner, other blocked work and the complete global backlog. **Do not drop unrelated items**.
4. Respect one active agent and at most one open PR globally. Never change another agent's branch.

## End of each prompt / iteration
1. Record the exact code/doc changes, SHA and PR, CI/tests, actual hardware proof (or NOT RUN), blockers, cleanup and next step.
2. Update affected rows in `DASHBOARD.md`, global indicators, task sequence and dated update log. Preserve every unfinished row and its history. Add newly found tasks; do not declare implemented, merged or hardware validated interchangeably.
3. Update `TODO.md` and `DEVELOPMENT_PLAN.md` in the same mission branch/PR for any project code change. Cross-check D1 duplication, D2 extension, D3 UI, D4 robustness, receiver, audio, latency, Android widget, GNOME, pairing, discovery, launch, voice, security, releases and both TVs: none is silently omitted.
4. Keep `Temps restant (min / max / méd.)` as **one** column in hours. Only the coordinator creates/revises estimates; agents preserve prior figures and report evidence affecting them. Never measure elapsed duration or invent estimates.
5. Preserve private evidence, credentials, signatures, unmerged branches/stashes and user data. Clean only verified temporary artifacts; report retained footprint.
6. If read-only, another agent owns the active PR, or GitHub protection blocks the write, **report a precise pending dashboard delta** for the next authorized PR. Never claim a change was pushed or merged when it was not.
7. End with a concise handoff: action, SHA/PR, local vs CI, hardware status, cleanup, blockers, next task. The coordinator's global report shows the full dashboard and remaining-time table.

## Status vocabulary
- IMPLEMENTED: code exists but may not be integrated.
- CI PASS: automated checks only.
- MERGED: changes in default branch.
- HARDWARE PASS / FAIL / PARTIAL: actual authorized-device observations.
- NOT RUN / NOT IMPLEMENTED: no evidence, or feature missing.

Never convert an automated test into a real-device PASS. Never equate a command sent with a TV-confirmed effect.

## Ownership
This skill **implements**, but does not supersede, `AGENTS.md` and `CLAUDE.md`. The owner approves merges, installs and device operations. The coordinator decides priorities and time estimates.
