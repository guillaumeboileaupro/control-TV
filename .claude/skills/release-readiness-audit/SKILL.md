---
name: release-readiness-audit
description: Audit whether a software version is ready to publish using code, CI, artifacts, installation evidence, security, documentation, and release metadata. Use before tagging or publishing a release.
---

# Release Readiness Audit

Return a go, conditional go, or no-go decision backed by evidence.

- Confirm the intended commit, clean branch state, version consistency, changelog, license, and release notes.
- Require passing tests, type checks, lint, formatting, security checks, and production builds that apply to the project.
- Verify artifact names, formats, architectures, checksums, bundled assets, signing status, and provenance.
- Distinguish CI build success from installation and launch testing on each target.
- Check upgrade, persistence, import/export, first launch, uninstall, and rollback expectations.
- List every waiver, untested target, warning, and manual follow-up next to the decision.
- Do not tag, publish, sign, or upload without authorization.

Run `python scripts/audit_manifest.py release-manifest.json` to validate a structured release checklist.