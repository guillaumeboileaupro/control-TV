---
name: rust-tauri-disk-hygiene
description: Keep Rust and Tauri 2 projects lightweight and prevent build artifacts, Android intermediates, caches, and temporary files from consuming uncontrolled disk space. Use when developing, building, packaging, cleaning, or diagnosing disk growth in Rust/Tauri applications.
---

# Rust Tauri Disk Hygiene

Keep the project reproducible without allowing generated data to grow without control.

## Ownership rules

- Distinguish repository-owned artifacts from shared toolchain caches before deleting anything.
- Never delete global Cargo, Rustup, Gradle, Android SDK, or Android NDK data blindly.
- Keep generated artifacts, package outputs, logs, temporary files, and caches out of Git.
- Use one predictable project build area whenever the toolchain permits it.
- Do not create ad-hoc duplicate build directories to work around failures.

## Before and after builds

- Measure relevant generated directories before and after substantial builds.
- Identify unexpected growth instead of repeatedly rebuilding on top of it.
- Build only the platform and profile required for the current task.
- Do not repeatedly build Android, Windows, and Linux artifacts when only one target is under test.
- Reuse installed shared SDK and NDK components rather than downloading project-local copies.

## Cleaning levels

Provide explicit cleaning levels rather than one destructive command:

1. `clean`: remove disposable project build outputs that are safe to regenerate.
2. `dist-clean`: remove all repository-owned generated artifacts and temporary data while preserving source, configuration, lockfiles, and shared toolchains.
3. Shared-cache maintenance: inspect first, report size and ownership, and require an explicit reason before pruning caches used by other projects.

After cleaning, verify what remains and report reclaimed disk space when measurable.

## Temporary files

- Create temporary data only in an identified application or system temporary location.
- Remove temporary data on normal completion and handle cleanup on errors where practical.
- Never use the source tree as runtime scratch storage.
- Do not retain extracted archives, duplicate installers, generated symbols, or obsolete package outputs without a documented need.

## Tauri and Android

- Treat Tauri as the application shell and packaging layer, not as justification for extra frontend tooling.
- Prefer the project's chosen lightweight frontend stack; do not add Node packages or frontend frameworks unless the requirement justifies them.
- Keep Android build intermediates and generated projects identifiable and disposable.
- Do not modify or purge the user's global Android SDK/NDK as part of routine project cleanup.

## Failure handling

When a build fails, diagnose the actual error before clearing caches. Cache deletion is not a generic debugging step. If cleanup is required, remove the narrowest affected project-owned artifact first and rebuild only the necessary target.

## Report

For build or cleanup work, report the target built, generated directories involved, their relevant sizes when available, cleanup performed, reclaimed space when measurable, and anything deliberately retained.