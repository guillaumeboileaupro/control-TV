---
name: conventional-commits-pr
description: Apply Conventional Commits 1.0.0 and prepare clear pull requests for Git repositories. Use when creating commits, choosing commit messages, organizing changes into commits, opening a PR, or drafting a PR title and description.
---

# Conventional Commits and Pull Requests

Produce an explicit, reviewable Git history and a focused pull request. Follow repository-specific instructions when they are stricter than this skill.

## Before changing Git state

- Inspect `git status`, the current branch, recent commits, and repository instructions.
- Preserve unrelated user changes. Never include them in a commit or PR.
- Do not rewrite shared history, push, open a PR, merge, or delete a branch unless the user has authorized that action.
- If the current branch is the default branch and a PR is expected, create a short descriptive feature branch before committing.

## Organize commits

- Keep each commit logically coherent and independently understandable.
- Split unrelated changes into separate commits when practical.
- Stage only the files and hunks belonging to the commit.
- Run the relevant tests, linters, formatters, or builds before committing. Report checks that could not be run.
- Review the staged diff before creating the commit.

## Write Conventional Commit messages

Use Conventional Commits 1.0.0:

```text
<type>[optional scope][optional !]: <description>

[optional body]

[optional footer(s)]
```

Use `feat` for a new feature and `fix` for a bug fix. Common additional types are `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`, and `revert`.

Choose a short noun as the optional scope when it adds useful context. Write a concise imperative description without a trailing period. Use the body to explain motivation, tradeoffs, or non-obvious behavior rather than repeating the diff.

Mark breaking changes with `!` and/or a `BREAKING CHANGE:` footer. Use Git-style trailers for real issue references when relevant; never invent issue numbers.

## Prepare the pull request

- Confirm the branch contains only intended commits and compare it with the actual base branch.
- Re-run appropriate checks against final branch state.
- Use a concise PR title in Conventional Commit form.
- Describe what changed and why, implementation decisions, verification actually performed, UI evidence when applicable, and migration/breaking notes.
- Never claim tests passed unless they ran successfully.
- Return the PR link and remaining checks/limitations after opening.

## Source

Use Conventional Commits 1.0.0 as the authoritative specification for edge cases.