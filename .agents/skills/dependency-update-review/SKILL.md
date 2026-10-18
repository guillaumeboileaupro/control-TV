---
name: dependency-update-review
description: Review dependency updates for compatibility, security, lockfile integrity, transitive impact, build changes and regression risk. Use for package upgrades, automated update PRs or dependency migrations.
---

# Dependency update review

Evaluate the resolved dependency graph, not only the requested version.

- Identify direct and transitive changes, the version range, release notes, deprecations, runtime
  requirements and license changes.
- Verify manifest and lockfile consistency; reject unexplained, unrelated lockfile churn.
- Check breaking API, configuration, build, platform, persistence and serialization changes.
- Run focused tests for the affected code plus the repository's full quality gates.
- Rebuild the distributable artifacts when native or build dependencies change.
- Separate security urgency from compatibility risk; never overstate an unverified vulnerability.
- Prefer one coherent update group that can be reverted on its own.
