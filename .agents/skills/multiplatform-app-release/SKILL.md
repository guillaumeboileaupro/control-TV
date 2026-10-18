---
name: multiplatform-app-release
description: Prepare and verify local-first releases for Ubuntu/Debian (.deb) and Android (.apk). Use for Tauri or Python desktop packaging, GitHub Actions release workflows, .deb packages or .apk files.
---

# Multiplatform app release

Build reproducible artifacts and keep a successful CI build apart from a validated installation.
Targets: the Ubuntu/Debian `.deb` and the Android `.apk`; no other platform without an owner
decision.

## Release matrix

- Inspect the actual stack, CPU architectures, version source, icons, bundled resources and
  signing configuration; define the expected artifact for each target.
- Keep platform code thin and business rules in the shared core.
- Never promise a platform or artifact without a credible build path in the repository.

## Builds

- Locked dependencies and clean CI environments.
- Keep generated binaries, secrets, signing keys and build caches out of Git.
- Verify downloaded bundled resources by size and checksum; generate every icon from the approved
  master asset.
- Package identifiers, names, versions, licenses, desktop entries, permissions and metadata agree
  across manifests.

## Verification

- Tests, type checks, linters, format checks and production builds before packaging.
- User data lives in a user-specific application directory, never in the source tree.
- Verify first launch, normal launch, persistence, upgrade and uninstall.
- A CI artifact stays unverified until installed and launched on its target; record a missing
  device or system test as such, never as a success.

## Publishing (owner authorization required)

- A version tag and release notes matching the packaged version; checksums for downloads.
- Describe debug-signed or sideloaded Android builds accurately, never as proof of safety.
- Never put signing credentials or private user data in artifacts or logs; no network
  transmission by default.

## Report

The artifact matrix, executed checks, artifact locations, signing status, manual validation
status and remaining blockers.
