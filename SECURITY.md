# Security Policy

## Supported Versions

control-TV is currently under active development and has not yet reached its first stable release.

Security updates are provided for the latest version of the `main` branch only. Older development snapshots and branches are not supported.

| Version | Supported |
| ------- | --------- |
| `main`  | Yes       |
| Older development versions | No |

This policy will be updated when stable releases of control-TV become available.

## Reporting a Vulnerability

Please do not report security vulnerabilities through public GitHub issues, discussions, pull requests, or other public channels.

Instead, use GitHub's private vulnerability reporting feature for this repository when available.

When reporting a vulnerability, please include:

- a clear description of the issue
- the affected component or feature
- steps to reproduce the issue
- the potential security impact
- any relevant logs or technical details, with sensitive information removed

Because control-TV communicates with devices on the local network, reports involving device discovery, network communication, command execution, or exposure of local device information are particularly important.

Please do not include private network addresses, device identifiers, credentials, tokens, or other personal or sensitive information unless strictly necessary for investigating the vulnerability.

## Disclosure

Please allow reasonable time for the vulnerability to be investigated and, when appropriate, fixed before publicly disclosing it.

Confirmed vulnerabilities will be assessed and addressed according to their severity and impact.

## Dependency advisories

CI audits every dependency for known advisories (`python3 scripts/dev.py npm-audit`,
`python-audit`, `rust-audit` and `webrtc-audit`, plus OSV-Scanner on the WebRTC engine and TV
receiver locks). A blocking advisory fails the build unless a waiver in the ecosystem's waiver
file (`ui/npm-audit-waivers.json`, `python-audit-waivers.json`, `src-tauri/cargo-audit-waivers.json`,
`webrtc-engine/cargo-audit-waivers.json`, `osv-scanner.toml`) names it, with a reason and an
expiry date; waived advisories are still printed. A waiver
needs the owner's approval, covers only the advisory it names (and only the package it names,
when it names one), and an expired waiver fails the build by itself, so it is reviewed again
rather than forgotten. Ignores set in a cargo `audit.toml` are refused.

### GHSA-qwgh-2vcv-g2f7 (block-buffer), fixed locally

The current Tauri and WebRTC graph requires the RustCrypto 0.10 API through `digest` 0.10.7, while
the first upstream `block-buffer` release outside the advisory range is the incompatible 0.12.1.
Both workspaces therefore patch `digest` to the upstream 0.10.7 source under `vendor/`, with only
its block-buffer dependency redirected to the distinct `control-tv-block-buffer-compat` package.
That package keeps the required 0.10 API but resets its cursor before calling external compression
code and replaces the unsafe invalid-cursor assumption with a checked panic. Its regression test
catches a callback panic and proves that the same buffer remains usable.

Neither lockfile contains the affected `block-buffer` package. No OSV or Cargo Audit exception was
added. The vendored compatibility sources and their licenses must remain until all Tauri/WebRTC
parents support `block-buffer` 0.12.1 or later; that migration should remove the `[patch.crates-io]`
entries and the two vendored directories together.

### RUSTSEC-2024-0429 (glib), waived until 2027-01-15

- **Advisory:** unsoundness in the `Iterator` and `DoubleEndedIterator` implementations of
  `glib::VariantStrIter` (a memory-safety bug when that iterator is used); fixed in
  glib >= 0.20.
- **Why it is in the build:** the Linux desktop window only. Tauri 2 (2.11.6) runs on the
  gtk-rs 0.18 stack: `tauri` / `tauri-runtime-wry` / `wry` / `tao` / `muda` / `webkit2gtk` /
  `libappindicator` -> `gtk` 0.18.2 -> `glib` 0.18.5 (also through `gio`, `gdk`, `cairo-rs`,
  `gdk-pixbuf`). The Android build has no glib. glib 0.20 cannot be taken without the gtk-rs
  stack Tauri depends on moving with it.
- **Known use of `VariantStrIter`:** none. control-TV's Rust does not use glib directly, and no
  locked crate whose sources were searched (328 of 430; the others are other platforms'
  crates) calls it; only glib itself defines it (2026-10-15).
- **Residual risk:** a dependency update could start using `VariantStrIter`; until the waiver
  expires, the audit would not flag that use. The waiver names this advisory and the `glib`
  package only: any other advisory, any other unsound crate, or this advisory on another
  package still fails the build.
- **Removal:** delete the waiver as soon as `cargo tree -i glib` in `src-tauri/` shows
  glib >= 0.20 (Tauri on gtk-rs >= 0.20 or GTK 4), or if control-TV or a dependency is found
  to use `VariantStrIter`. On 2027-01-15 the waiver expires and the build fails until the owner
  reviews it again.

### RUSTSEC-2026-0387 (crypto-bigint), RUSTSEC-2025-0141 (bincode) and RUSTSEC-2026-0441 (pin-utils), resolved 2026-11-07

- **What they were:** timing variability in `crypto-bigint` < 0.7.6, and `bincode` 1.3.3 and
  `pin-utils` 0.1.1 unmaintained, all brought by `webrtc` 0.14 (`dtls` 0.13 -> `p256`/`p384`
  0.13, and `webrtc-util` 0.12 -> `nix` 0.26). They were waived until 2026-11-30 for
  local-network trials only.
- **Resolution:** the WebRTC engine moved to `webrtc` 0.21 and its `rtc` stack (with `ring`), and
  its tests to `rtc`'s own RTP depacketizers. None of the three crates is in `webrtc-engine/` or
  `src-tauri/` any longer, and the five waiver entries (two Cargo Audit, three OSV) are deleted:
  any of these advisories would block the audits again.
