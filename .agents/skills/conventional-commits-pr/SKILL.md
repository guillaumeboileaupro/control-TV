---
name: conventional-commits-pr
description: Apply Conventional Commits 1.0.0 and prepare clear pull requests for Git repositories.
---

# Conventional Commits and Pull Requests

Produce an explicit, reviewable Git history and a focused pull request.

## Before Git changes
Inspect status, branch, recent commits and repository instructions. Preserve unrelated user changes and use a feature branch when PR workflow requires it.

## Commits
Keep commits logically coherent, stage only relevant changes, run relevant checks, review staged diff, and use `<type>[optional scope][optional !]: <description>` with appropriate `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`, or `revert` type.

## Pull requests
Confirm intended commits/base, rerun checks, use a concise Conventional Commit title, document what changed and why, verification actually performed, visible UI evidence where relevant, migration notes and remaining limitations. Use the owner's configured Git identity and project attribution rules.