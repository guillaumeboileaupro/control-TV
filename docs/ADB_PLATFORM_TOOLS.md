# Android Platform Tools policy

ControlTV uses the local ADB server for the optional Ubuntu TV-app inventory. Classic TCP works
with older ADB implementations; Wireless debugging requires Platform Tools 30.0.0 or newer and
is checked semantically before `adb pair`.

## Packaging decision

The Ubuntu 22.04 `adb` package remains the `.deb` dependency because it provides the supported
classic TCP path, but it is Android-10-era software and does not guarantee Wireless pairing. The
Google Platform Tools archive is **not bundled, downloaded or installed by ControlTV**:

- Google's versioned HTTPS archive is the authoritative binary provenance;
- using it requires accepting the Android SDK terms;
- those terms restrict redistribution, so a release must not embed the archive until the owner
  records an explicit legal review allowing that distribution;
- an unversioned `platform-tools-latest-linux.zip` is never acceptable for a reproducible build.

For the owner's local installation, the reliable current option is an independently installed
Android SDK whose `platform-tools` directory is ahead of `/usr/bin` in the desktop application's
`PATH`. ControlTV verifies `adb version >= 30.0.0`, then verifies that `adb help` advertises
`pair`, before it sends the one-time code. This is a runtime prerequisite, not a `.deb` payload.
Consequently a clean Ubuntu 22.04 `.deb` installation supports classic TCP but must not claim that
Wireless pairing is included.

## Pinned release evidence

[`packaging/adb-platform-tools.json`](../packaging/adb-platform-tools.json) records the current
candidate, Platform Tools 37.0.1, its versioned `dl.google.com` URL, exact byte size and SHA-256.
The checksum was calculated from that URL on 2026-10-23 without extracting, installing or running
ADB. The archive contains `platform-tools/source.properties` declaring `Pkg.Revision=37.0.1` and
`platform-tools/NOTICE.txt`; neither file substitutes for review of the current SDK terms.

The manifest is reference and audit evidence only. It deliberately sets
`redistributionApproved` to `false`, and packaging tests ensure no archive or Platform Tools
binary enters the repository or Tauri resources.

## Updating the candidate

For every stable update:

1. read the official release notes and reject preview/canary versions;
2. use a versioned official HTTPS URL, never the moving `latest` URL;
3. calculate and record size and SHA-256 before extracting anything;
4. inspect `source.properties`, `NOTICE.txt`, and the current Android SDK terms;
5. update the manifest and its tests in one focused dependency commit;
6. run the offline adapter, packaging, release-smoke and security checks;
7. keep the binary external unless a fresh legal review explicitly approves redistribution;
8. validate Wireless pairing on authorized hardware separately—archive verification is not a
   device result.

Platform Tools updates are manual and review-driven. No application startup, build or CI job may
silently download or replace ADB, and ControlTV never modifies the user's pairing keys.
