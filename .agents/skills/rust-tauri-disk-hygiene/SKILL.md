---
name: rust-tauri-disk-hygiene
description: Keep Rust and Tauri 2 projects lightweight and prevent build artifacts, Android intermediates, caches, and temporary files from consuming uncontrolled disk space.
---

# Rust Tauri Disk Hygiene

Keep the project reproducible without allowing generated data to grow without control.

## Ownership rules
- Distinguish repository-owned artifacts from shared toolchain caches before deletion.
- Keep generated artifacts, package outputs, logs, temporary files, and caches out of Git.
- Use one predictable project build area.
- Preserve shared Cargo, Rustup, Gradle, Android SDK and Android NDK data during routine cleanup.

## Build cycle
Measure relevant generated directories before and after substantial builds. Build only the platform/profile required. Identify unexpected growth.

## Cleaning levels
1. `clean`: disposable project build outputs.
2. `dist-clean`: all repository-owned generated artifacts and temporary data while preserving source/config/lockfiles/shared toolchains.
3. Shared-cache maintenance: inspect first and handle separately.

After cleaning, verify what remains and report reclaimed space when measurable.

## Failure handling
Diagnose build failures before cache maintenance. Remove only the narrowest verified project-owned artifact required.

## Report
Report target, generated directories, sizes when available, cleanup, reclaimed space, and intentionally retained artifacts.