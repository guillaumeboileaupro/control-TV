---
name: rust-tauri-disk-hygiene
description: Keep Rust and Tauri 2 projects lightweight and stop build artifacts, Android intermediates, caches and temporary files from consuming uncontrolled disk space. Use when developing, building, packaging, cleaning or diagnosing disk growth.
---

# Rust Tauri disk hygiene

The procedure behind the blocking cleanup rule of `AGENTS.md`: keep the project reproducible
without letting generated data grow unchecked.

## Ownership

- Tell repository-owned artifacts from shared toolchain caches before deleting anything; never
  delete global Cargo, Rustup, Gradle, Android SDK or NDK data during routine cleanup.
- Keep generated artifacts, package outputs, logs, temporary files and caches out of Git.
- One predictable project build area; never ad-hoc duplicate build directories to work around a
  failure.

## Build cycle

- Measure the relevant generated directories before and after substantial builds; investigate
  unexpected growth instead of rebuilding on top of it.
- Build only the target and profile under test: not the `.deb` and the APK when only one changes.
- Reuse the installed SDK and NDK components; no project-local copies.

## Cleaning levels

1. `clean`: disposable project build outputs, safe to regenerate.
2. `dist-clean`: every repository-owned generated artifact and temporary file; source,
   configuration, lockfiles and shared toolchains stay.
3. Shared-cache maintenance: inspect first, report size and ownership, and only under an
   owner-directed maintenance task.

Afterwards verify what remains and report reclaimed space when measurable.

## Temporary files

- Only in an identified application or system temporary location, never in the source tree.
- Removed on completion and, where practical, on error.
- No extracted archives, duplicate installers or obsolete package outputs without a documented
  need.

## Tauri and Android

- Tauri is the shell and packaging layer; add Node packages or frontend frameworks only when a
  requirement justifies them.
- Keep Android intermediates and generated projects identifiable and disposable.

## Failures

Diagnose the actual error before touching caches: cache deletion is not a debugging step. Remove
the narrowest verified project-owned artifact, then rebuild only the target concerned.

## Report

The target built, generated directories and their sizes, cleanup performed, reclaimed space and
what was deliberately retained.
