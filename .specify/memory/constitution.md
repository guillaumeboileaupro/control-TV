# Control-TV Constitution

This constitution adapts Spec Kit to Control-TV. It does not restate the project rules:
[`AGENTS.md`](../../AGENTS.md) is their single source, and wins over this file, over any Spec Kit
template and over any generated artifact. Each principle below names the `AGENTS.md` rule a plan
must satisfy in its Constitution Check.

## Core Principles

### I. One Authoritative Control Layer

All control and domain logic lives in the Python `ControlService`; the Tauri/Rust, TypeScript and
Android hosts call it through the bridge and never duplicate it. Rust only where a concrete native
or performance benefit exists. A plan that adds logic to a host, or a new language or framework,
is rejected unless the owner decided it (`AGENTS.md`, Architecture).

### II. Sent, Confirmed and Observed Stay Distinct

A command sent is not a state the TV confirmed, and neither is a physical effect. Specifications,
acceptance criteria and task completion keep the three apart, and an automated test or green CI
is never a hardware result (`AGENTS.md`, Architecture; [rule:hardware-safety]).

### III. Hardware Safety and Privacy (NON-NEGOTIABLE)

No TV, Cast, ADB, installation, `sudo`, system change, real capture or network stream without the
owner's authorization for that step; no device names, addresses, identifiers or credentials in
specs, plans, tasks, commits or PRs ([rule:hardware-safety], skill `hardware-safety`).

### IV. Quality Gates

SOLID design, strict typing with no explicit `Any` in production code, tests for every change and
error path, coverage kept at or above 98 %, checks through `python3 scripts/dev.py check`
([rule:quality-gates]). Code handles interruptions safely and nothing is resent automatically
([rule:interruptions]).

### V. Proportionate Specification

Spec Kit serves a new feature or a substantial change the owner asks to specify; it is never a
gate on every mission. No retroactive specifications for existing features, no diagnostic-only or
test-only deliverables, and no artifact that nobody will read. The optional commands
(`clarify`, `analyze`, `checklist`, `converge`, `taskstoissues`) run only on request. A plan
reuses existing code and documents before proposing anything new ([rule:context-minimization]).

## Project Records and Workflow

- `DASHBOARD.md`, `TODO.md` and `DEVELOPMENT_PLAN.md` stay the records of scope, progress and
  priorities. A feature's `specs/<feature>/tasks.md` is a working list: its outcome is carried into
  those three files in the same branch ([rule:progress-docs]). Only the coordinator estimates time
  ([rule:remaining-estimate]).
- One mission per agent, in its own worktree and branch outside `/tmp`; at most one open PR
  repository-wide; independent review by the other agent; no merge without the owner's
  authorization ([rule:one-mission], [rule:separate-worktrees], [rule:independent-review],
  [rule:no-merge]). Spec Kit never creates, switches or deletes branches here: the feature
  directory is chosen on the mission's existing branch.
- Conventional Commits with the owner identity and no AI attribution; evidence and local
  preferences are never committed ([rule:conventional-commits]). Temporary and build output is
  cleaned at the end of every iteration ([rule:temporary-cleanup]).

## Governance

`AGENTS.md` prevails over this constitution; a conflict is resolved by following `AGENTS.md` and
reporting the conflict. Amendments to this file need the owner's approval, follow semantic
versioning and keep the references above in step with `AGENTS.md`. Usage of Spec Kit in this
repository is documented in [`docs/SPEC_KIT.md`](../../docs/SPEC_KIT.md).

**Version**: 1.0.0 | **Ratified**: 2026-11-03 | **Last Amended**: 2026-11-03
