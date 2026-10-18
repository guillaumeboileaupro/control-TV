---
name: github-ci-diagnosis
description: Diagnose GitHub Actions failures from workflow metadata, annotations, job steps, and logs.
---

# GitHub CI Diagnosis

Find the first actionable cause, not the last cascade error.

1. Identify failing commit, workflow, job, attempt and exact step.
2. Separate independent jobs.
3. Read annotations and the smallest relevant log window.
4. Classify product code, test, dependency, environment, secret, permission, cache, artifact, timeout or infrastructure causes.
5. Reproduce locally when practical.
6. Apply the smallest correction and regression check where applicable.
7. Re-run failed jobs only when evidence supports a transient cause.

Protect secrets and preserve quality gates.