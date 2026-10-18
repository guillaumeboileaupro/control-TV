---
name: dependency-update-review
description: Review dependency updates for compatibility, security, lockfile integrity, transitive impact, build changes, and regression risk.
---

# Dependency Update Review

Evaluate the resolved dependency graph, not only the requested version.

- Identify direct/transitive changes, release notes, deprecations, runtime requirements and license changes.
- Verify manifest and lockfile consistency.
- Check breaking API, configuration, build and platform changes.
- Run focused tests plus required quality gates.
- Rebuild distributable artifacts when native/build dependencies change.
- Separate security urgency from compatibility risk.
- Prefer coherent update groups that can be reverted independently.