---
name: git-worktree-pr-workflow
description: One mission per agent in its own git worktree and branch, preserving other agents' work and uncommitted changes, then a pull request without merging. Use before any code change, branch, rebase, merge, push or PR in this repository.
---

# Worktree and PR workflow

1. `git worktree list` and `git status --short` first. Never edit, switch, reset or push a branch
   checked out in another worktree, or another agent's branch or PR, unless the owner says so.
2. One mission, one worktree, one branch: `git worktree add -b <type>/<topic> ../control-TV-<topic> <base SHA>`.
   Not under `/tmp` (wiped on reboot). Start from the exact SHA the mission names.
3. The agent configuration is versioned, so every worktree has it. Only a branch older than it
   lacks it: run `python3 scripts/agent_config.py link --from <main checkout>` there.
4. Preserve uncommitted work: never `reset --hard`, `checkout -- .`, `clean -fd` or a forced
   switch over changes; use `git stash -u` and restore it, or commit first.
5. Update a shared branch by merging `origin/main` into it; no rebase, history rewrite or
   `push --force` without explicit owner approval.
6. Trial merges or reviews of another SHA: a detached throw-away worktree in the session
   scratchpad, removed afterwards; never a new branch.
7. Commits: Conventional Commits (skill `conventional-commits-pr`), the configured owner identity,
   no AI attribution. Never commit local preferences or evidence: `.ai-private/`, `.codex/`,
   handoffs, `.claude/settings.local.json`, `CLAUDE.local.md`, `AGENTS.override.md`.
8. PR: `gh api -X POST repos/<owner>/<repo>/pulls` if `gh pr create/edit` fails. CI runs only for
   pushes to `main` and PRs **into** `main`: a PR into another branch has no CI; say so.
9. Never merge without the owner's explicit authorization, and only after an independent review
   (skill `independent-code-review`).
10. Remove only your own worktrees, and only when their branch is pushed and clean.
