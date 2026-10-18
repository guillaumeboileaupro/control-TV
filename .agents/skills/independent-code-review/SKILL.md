---
name: independent-code-review
description: Review another agent's pull request at an exact SHA without modifying it, verify claims by reproduction, classify findings P0-P3 and give APPROVED or CHANGES REQUESTED. Use for any review, audit or "revue indépendante" request, and to label a self-review honestly.
---

# Independent code review

- The reviewer is not the author. A review of your own work is a **self-review** and must say so;
  it never replaces the independent one before a merge.
- Review the exact SHA: `git worktree add --detach <scratchpad>/review <SHA>`; read the diff, the
  PR's review threads (`gh api repos/<o>/<r>/pulls/<n>/comments`) and the relevant docs. Remove
  the worktree afterwards. No branch, commit, push or PR edit.
- Verify, do not assume: run the targeted tests and the repository checks on that SHA; reproduce
  each suspected defect with a probe script kept in the scratchpad (never in the repository).
- Look for false PASS first (evidence that could pass while the claim is false), then lost
  resources, races, interruption paths, error swallowing, privacy leaks, missing tests and
  coverage of new error paths.
- Classify: P0 unsafe or data loss, P1 wrong result or false evidence, P2 robustness or
  contract gap, P3 quality. Each finding: file:line, failure scenario, evidence, requested fix.
- Verdict: APPROVED only with no P0/P1/P2 open; otherwise CHANGES REQUESTED with the minimal
  corrections and an estimate. State what was not verifiable (hardware, CI absent).
