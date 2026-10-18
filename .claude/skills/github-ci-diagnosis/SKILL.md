---
name: github-ci-diagnosis
description: Diagnose GitHub Actions failures from workflow metadata, annotations, job steps, and logs. Use when CI is failing, flaky, cancelled, unexpectedly slow, or inconsistent across jobs.
---

# GitHub CI Diagnosis

Find the first actionable cause, not the last cascade error.

1. Identify the failing commit, workflow, job, attempt, and exact failing step.
2. Separate independent jobs such as shared code, API, frontend, packaging, and deployment.
3. Read annotations and the smallest relevant log window around the first failure.
4. Classify the cause: product code, test, dependency, environment, secret, permission, cache, artifact, timeout, or infrastructure.
5. Reproduce locally with the same command and versions when practical.
6. Propose the smallest correction and add a regression check when applicable.
7. Re-run only failed jobs when the cause was transient. Do not retry repeatedly without new evidence.

Never expose secrets from logs. Do not change tests, thresholds, or branch protection merely to obtain a green result.

For exported GitHub job JSON, run `python scripts/summarize_jobs.py jobs.json` to produce a compact failure summary.