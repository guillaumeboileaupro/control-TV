# Vendored Rust security compatibility sources

`digest-0.10.7` is the upstream RustCrypto source with one manifest-only change: its optional
`block-buffer` dependency points to `control-tv-block-buffer-compat` in this directory.

`control-tv-block-buffer-compat` preserves the `block-buffer` 0.10 API required by the current
Tauri and WebRTC dependency graph. It is derived from upstream `block-buffer` 0.10.4 under the
same MIT OR Apache-2.0 license, but has a distinct package name and two security hardenings:

- the cursor is reset before invoking the compression callback, so a caught panic leaves a valid,
  reusable buffer;
- cursor validation panics safely instead of entering `unreachable_unchecked`.

This removes the package affected by GHSA-qwgh-2vcv-g2f7 instead of assigning it a fictitious
patched version or suppressing the advisory. Remove both vendored sources once the complete
Tauri/WebRTC graph supports the RustCrypto generation containing `block-buffer` 0.12.1 or later.
