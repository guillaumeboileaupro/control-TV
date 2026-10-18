# Agent instructions - control-TV (Claude and Codex)

The single source of the rules every agent follows here. `CLAUDE.md` imports this file; Codex
reads it directly. Procedures live in skills (`.agents/skills/`, linked into `.claude/skills/`).
Versioned with the skills; local preferences stay in git-ignored files (`CLAUDE.local.md`,
`AGENTS.override.md`, `.claude/settings.local.json`, `.codex/`, `.ai-private/`).

## Mission and scope

Two standalone remotes - the Ubuntu Tauri app and the Android app - control the owner's Google TV
(a Freebox under Android TV) in parallel, with no permanent server. MCP, ChatGPT Voice and Windows
were abandoned on 2026-10-12. Remote display (Ubuntu screen to the TV, phases D0-D4) is planned
work. `DEVELOPMENT_PLAN.md` is the source of truth for scope and progress; read it, `README.md`,
`ARCHITECTURE.md`, the relevant skills and the latest PR/handoff before changing code.

## Architecture

- One authoritative Python control/domain layer (`ControlService`); Tauri/Rust, TypeScript and
  Android hosts call it through the bridge and never duplicate control logic.
- Python includes the Cast ecosystem (`pychromecast`) where it fits; Rust only for a concrete
  native or performance benefit; Tauri 2 is the shell; the UI is HTML/CSS/TypeScript.
- Media/service resolution stays separate from the low-level Cast transport.
- A command sent is not a state confirmed by the TV: keep sent / receiver-confirmed / physically
  observed distinct.
- Go is rejected by the owner. Architecture follows owner decisions and validated constraints,
  not exploratory comparisons.

## Mandatory common rules

1. [rule:one-mission] One active mission per agent; finish or hand it off before another.
2. [rule:separate-worktrees] Each mission in its own worktree and branch, outside `/tmp`
   (skill `git-worktree-pr-workflow`).
3. [rule:other-agent-branches] Never modify, rebase, push or close another agent's branch,
   worktree or PR; record conflicts instead of building a competing implementation.
4. [rule:independent-review] A PR is merged only after an independent review by the other agent
   (skill `independent-code-review`); a self-review is labelled as such.
5. [rule:no-merge] No merge, release or history rewrite without the owner's explicit
   authorization.
6. [rule:hardware-safety] No TV/Freebox/Cast command, ADB, installation, sudo, system change,
   real capture or network stream without the owner's authorization for that step
   (skill `hardware-safety`). No private device data in output, commits or PRs.
7. [rule:quality-gates] SOLID (skill `solid-code-design`), strict typing with no explicit `Any`
   in production code, tests for every change and error path, total coverage kept at or above
   98 % (the CI gate is 95 %), checks via `python3 scripts/dev.py check` and `coverage`
   (skill `python-project-quality`).
8. [rule:conventional-commits] Conventional Commits with the configured owner identity and no AI
   attribution (skill `conventional-commits-pr`); never commit secrets, local preferences or
   evidence (`.ai-private/`, `.codex/`, handoffs, `*.local.*`, `AGENTS.override.md`), private
   device data or paths, or generated build trees.
9. [rule:interruptions] Code handles interruptions safely: cleanup continues past failures and
   `KeyboardInterrupt`/`SystemExit`, unreleased resources stay tracked, nothing is resent
   automatically.
10. [rule:uncommitted-work] Preserve uncommitted changes: no `reset --hard`, forced checkout or
    `clean -fd` over work; stash or commit first.
11. [rule:temporary-cleanup] Cleanup is a blocking definition-of-done item (next section);
    delete only verified project-owned paths.
12. [rule:context-minimization] Keep model context small: targeted reads, filtered output, no
    re-pasted rules (skill `agent-token-efficiency`).
13. [rule:final-report] End every mission with the standard report (skill
    `development-reporting`), local and CI results kept apart.
14. [rule:time-reporting] Do not ask agents to measure elapsed time or estimate remaining hours.
    Project-wide time estimates are managed by the coordinator, not by coding agents.
15. [rule:progress-docs] For every project change, update `TODO.md` and
    `DEVELOPMENT_PLAN.md` as part of the same branch/PR, before handoff. Keep changes
    limited to the affected tasks and D0-D4 phases; mark complete only work actually done,
    add newly discovered issues and unfinished work, and correct stale dependencies or
    statements. Clearly distinguish implementation, automated checks/CI and real hardware
    validation. Preserve relevant history and never silently remove unfinished tasks. Verify
    that both files reflect the delivered change even when no checkbox changes are needed.
    For read-only audits, report necessary follow-up edits without modifying other PRs.

## Temporary and build cleanup (blocking)

Same priority as compilation and tests, after successful, failed and interrupted builds:

1. before build-heavy work, inspect free disk and project-owned generated directories;
2. after each build/test/package cycle, identify new project-owned output and delete obsolete
   output at once (`target/`, `src-tauri/target/`, Tauri build/generated dirs, Android `build/`,
   project `.gradle/`, Python caches and packaging output, staging, logs, test output, scratch
   dirs, extracted archives);
3. keep only explicitly useful artifacts; release deliverables go to `dist/` first;
4. measure and report the remaining project-owned footprint; a failed cleanup leaves the
   iteration incomplete and the handoff names path, size and reason.
   Never touch shared caches (Cargo, Gradle, Android SDK/NDK, Python), `$HOME` or `/tmp` globally
   without an owner-directed maintenance task. Avoid duplicate build trees. `python3 scripts/dev.py
clean` and `dist-clean` stay limited to reproducible project-owned output (skill
   `rust-tauri-disk-hygiene`).

## Skills

Project skills: one source, `.agents/skills/<name>/SKILL.md` (Codex), linked as
`.claude/skills/<name>` for Claude. Mission skills: `git-worktree-pr-workflow`,
`independent-code-review`, `hardware-safety`, `development-reporting`, `agent-token-efficiency`;
quality: `python-project-quality`, `solid-code-design`, `typescript-no-any`,
`conventional-commits-pr`; domain: disk hygiene, multiplatform release (`.deb`, `.apk`), CI
diagnosis, dependency review, release audit, accessibility. Inactive (discovered by neither
agent): `.agents/inactive-skills/`. Usage: `docs/AGENT_INSTRUCTIONS.md`; consistency check:
`python3 scripts/agent_config.py check`.

## Handoff and definition of done

Scope complete; relevant checks run; hardware claims classified (simulated, CI, hardware);
cleanup done and footprint recorded; `TODO.md` and `DEVELOPMENT_PLAN.md` synchronized under
[rule:progress-docs]; PR workflow respected; the report of skill `development-reporting`
gives the other agent
everything needed to continue (objective, branch/PR, files, interfaces, decisions, dependencies,
checks, validation, artifacts kept and cleaned, risks, assumptions, next task, files to read).
