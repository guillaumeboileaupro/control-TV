---
name: release-readiness-audit
description: Audit whether a software version is ready to publish using code, CI, artifacts, installation evidence, security, documentation and release metadata. Use before tagging or publishing a release.
---

# Release readiness audit

Return a go, conditional go or no-go decision backed by evidence.

- Confirm the intended commit, a clean branch state, version consistency, changelog, license and
  release notes.
- Require the applicable tests, type checks, lint, formatting, security checks and production
  builds to pass.
- Verify artifact names, formats, architectures, checksums, bundled assets, signing status and
  provenance.
- Keep CI build success apart from installation and launch testing on each target.
- Check first launch, upgrade, persistence, uninstall and rollback expectations.
- List every waiver, untested target, warning and manual follow-up next to the decision.
- Never tag, publish, sign or upload without the owner's authorization.
