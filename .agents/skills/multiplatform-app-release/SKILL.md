---
name: multiplatform-app-release
description: Prepare and verify local-first application releases for Windows, Debian or Ubuntu, and Android. Use for Tauri or Python desktop packaging, GitHub Actions release workflows, installers, .deb packages, .exe files, or .apk files.
---

# Multiplatform App Release

Build reproducible platform artifacts and distinguish a successful CI build from a validated installation.

## Establish the release matrix
- Inspect the actual application stack, operating systems, CPU architectures, version source, icons, bundled resources, and signing configuration.
- Define the expected artifact for each target.
- Keep platform-specific code thin and business rules platform-independent where architecture permits.

## Prepare and verify builds
- Use locked dependencies and clean CI environments.
- Keep generated binaries, secrets, signing keys, and build caches out of Git.
- Ensure identifiers, names, versions, licenses, permissions and metadata agree across manifests.
- Run applicable tests, type checks, linters, format checks and production builds.
- Treat CI-produced artifacts as unverified until installed and launched on the target platform.

## Report
Return artifact matrix, executed checks, artifact locations, signing status, manual validation status, and blockers.