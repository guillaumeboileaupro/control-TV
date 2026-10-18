---
name: multiplatform-app-release
description: Prepare and verify local-first application releases for Windows, Debian or Ubuntu, and Android. Use for Tauri or Python desktop packaging, GitHub Actions release workflows, installers, .deb packages, .exe files, or .apk files.
---

# Multiplatform App Release

Build reproducible platform artifacts and distinguish a successful CI build from a validated installation.

## Establish the release matrix

- Inspect the actual application stack, supported operating systems, CPU architectures, version source, icons, bundled resources, and signing configuration.
- Define the expected artifact for each target, such as a Windows executable and installer, Debian package, or Android APK.
- Keep platform-specific code thin. Put business rules in a platform-independent core when the architecture permits it.
- Do not promise a platform or artifact until the repository contains a credible build path for it.

## Prepare builds

- Use locked dependencies and clean CI environments.
- Keep generated binaries, downloaded models, secrets, signing keys, and build caches out of Git.
- Verify bundled resources by size and checksum when they are downloaded during the build.
- Generate every icon from the approved master asset and verify platform-specific padding.
- Ensure package identifiers, application names, versions, licenses, desktop entries, permissions, and update metadata agree across manifests.

## Verify quality

- Run unit tests, type checks, linters, format checks, and production builds before packaging.
- Test data paths in a user-specific application directory, never in the source tree.
- Verify first launch, normal launch, persistence, import and export, upgrade behavior, and uninstall behavior.
- Treat a CI-produced artifact as unverified on its target platform until it has been installed and launched there.
- Record missing device or operating-system tests explicitly instead of describing them as successful.

## Release safely

- Use a version tag and release notes that match the packaged version.
- Publish checksums for downloadable artifacts when practical.
- Document unsigned Windows warnings and sideloaded Android warnings accurately without presenting them as proof of safety.
- Never upload signing credentials or private user data to artifacts or logs.
- For local-first products, do not introduce network transmission by default. Any remote feature must be optional, explicit, and documented.

## Report

Return the artifact matrix, executed checks, artifact locations or links, signing status, manual validation status, and remaining blockers.
