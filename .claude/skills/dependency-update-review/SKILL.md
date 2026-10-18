---
name: dependency-update-review
description: Review dependency updates for compatibility, security, lockfile integrity, transitive impact, build changes, and regression risk. Use for package upgrades, automated update PRs, or dependency migrations.
---

# Dependency Update Review

Evaluate the resolved dependency graph, not only the requested version.

- Identify direct and transitive changes, version range, release notes, deprecations, runtime requirements, and license changes.
- Verify manifest and lockfile consistency and reject unexplained unrelated lockfile churn.
- Check breaking API, configuration, build, platform, database, and serialization changes.
- Run focused tests for affected code plus the repository's full required quality gates.
- Rebuild distributable artifacts when native or build dependencies change.
- Separate security urgency from compatibility risk and do not overstate unverified vulnerability impact.
- Prefer one coherent update group that can be reverted independently.

Run `python scripts/diff_requirements.py before.txt after.txt` for a normalized direct-requirement comparison.