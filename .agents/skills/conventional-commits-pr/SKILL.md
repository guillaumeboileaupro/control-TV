---
name: conventional-commits-pr
description: Apply Conventional Commits 1.0.0 and prepare focused pull requests. Use when creating commits, choosing commit messages, splitting changes into commits, opening a PR, or drafting a PR title and description.
---

# Conventional Commits and pull requests

Produce a reviewable history and a focused PR. Identity, attribution, authorizations and
worktrees follow `AGENTS.md` and the `git-worktree-pr-workflow` skill, which win when stricter.

## Before committing

- Inspect `git status`, the branch and recent commits; never include unrelated changes.
- Keep each commit logically coherent; split unrelated changes; stage only the relevant files or
  hunks.
- Run the relevant checks and review the staged diff; report checks that could not run.

## Message

```text
<type>[optional scope][!]: <description>

[optional body]

[optional footer(s)]
```

- Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`, `revert`.
- A short noun as scope when it helps; an imperative description without a trailing period; the
  body explains motivation and non-obvious behavior, not the diff.
- Breaking change: `!` and/or a `BREAKING CHANGE:` footer. Reference real issues only; never
  invent an issue number.

## Pull request

- Confirm the branch holds only the intended commits against the actual base; re-run the checks
  on the final state.
- Title in Conventional Commit form. Body: what changed and why, decisions, verification actually
  performed, UI evidence when relevant, migration or breaking notes, remaining limitations.
- Never claim a check passed unless it ran and passed. Return the PR link.
- Edge cases: the Conventional Commits 1.0.0 specification.
