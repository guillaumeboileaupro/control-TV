---
name: github-ci-diagnosis
description: Diagnose GitHub Actions failures from workflow metadata, annotations, job steps and logs. Use when CI is failing, flaky, cancelled, unexpectedly slow or inconsistent across jobs.
---

# GitHub CI diagnosis

Find the first actionable cause, not the last cascade error.

1. Identify the failing commit, workflow, job, attempt and exact step.
2. Separate the independent jobs (here: lint/type-check/tests/coverage, Tauri shell, Linux
   package, Android APK).
3. Read the annotations and the smallest relevant log window around the first failure.
4. Classify the cause: product code, test, dependency, environment, secret, permission, cache,
   artifact, timeout or infrastructure.
5. Reproduce locally with the same command and versions when practical.
6. Apply the smallest correction, with a regression check when applicable.
7. Re-run only the failed jobs, and only when the evidence points to a transient cause; never
   retry repeatedly without new evidence.

Never expose secrets from logs. Never change tests, thresholds or branch protection merely to get
a green result. CI runs only for pushes to `main` and PRs into `main`.
