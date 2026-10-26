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

### RUSTSEC-2026-0387 (crypto-bigint) and RUSTSEC-2025-0141 (bincode), waived until 2026-11-30

- **Advisories:** timing variability in operations of `crypto-bigint` < 0.7.6 meant to be
  constant-time (fixed only in 0.7.6); `bincode` 1.3.3 unmaintained (no known vulnerability,
  blocking only in OSV-Scanner).
- **Why they are in the build:** the WebRTC screen stream only (`webrtc-engine/` and, through it,
  `src-tauri/`): `webrtc` 0.14 -> `dtls` 0.13 -> `p256`/`p384` 0.13 -> `elliptic-curve` 0.13 ->
  `crypto-bigint` 0.5.5, and `bincode` 1.3.3. No compatible fix exists: `dtls` 0.17.2 still
  needs `p256` ^0.13 (rechecked 2026-10-25).
- **Why the risk is accepted:** the constant-time fixes of 0.7.6 address code that 0.5.5 lacks
  (`UintRef`, `BoxedMontyForm`, the `borrowing_sub` change of 2025-12) or that this chain never
  calls (CTZ, `div_rem`, Montgomery multiplication); `p256` and `p384` do their field arithmetic
  themselves. DTLS signatures and the certificate use `ring`; `p256`/`p384` only compute
  single-use ECDHE keys, and a timing attack needs many measurements of one secret. The stream
  has no STUN/TURN server, refuses to bind every interface, needs the owner's consent and a
  48-character token over TLS, and lasts at most one session.
- **Residual risk:** not checked at machine-code level (`cargo checkct`), and the curve the TV
  negotiates was not observed (libwebrtc offers X25519 first, which never uses `crypto-bigint`).
  An attacker on the local network measuring one handshake's timing is the only exposure.
- **Use:** local-network trials only; no exposure beyond the LAN while the waivers last.
- **Removal:** the migration to `webrtc` 0.21 (its `rtc` stack uses `aws-lc-rs`/`ring`, no
  `p256` 0.13) removes both advisories; delete the three waiver entries then. On 2026-12-01 they
  expire and the audits fail until the owner reviews them again.
