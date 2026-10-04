# Licenses of the Linux release package

This is an engineering inventory of what the Linux release `.deb` contains and of what each license asks of a distributor. **It is not a legal review, and no legal validation has taken place.** It records the state found on 2026-10-04 for version 0.1.0 built with CPython 3.12.15 (`python3 scripts/dev.py release-deb`).

## What the package ships

| Component | Version | How it is shipped | License | Text in the package |
|---|---|---|---|---|
| control-TV | 0.1.0 | Rust binary, frozen Python bridge | GPL-3.0 (version not stated as "only" or "or later") | `/usr/share/doc/control-tv/copyright` |
| CPython (libpython, standard library) | 3.12.15 (`packaging/release-python-version`) | `libpython3.12.so.1.0`, `base_library.zip`, frozen archive | PSF-2.0 | `python-bridge/licenses/CPython-3.12.15/` |
| Libraries linked statically into that libpython by python-build-standalone | OpenSSL 3.5.9, SQLite 3.53.1, zlib 1.3.2, libedit, libffi, xz, bzip2, ncurses/terminfo, mpdecimal, expat, libuuid, HACL* | inside `libpython3.12.so.1.0` | Apache-2.0, public domain, Zlib, BSD, MIT, 0BSD, bzip2, X11 | **missing** |
| PyInstaller bootloader | 6.22.3 | the `control-tv-bridge` executable | GPL-2.0-or-later with the bootloader exception | `python-bridge/licenses/PyInstaller-6.22.3/` |
| libstdc++, libgcc_s (copied from the Ubuntu 22.04 build system; needed by protobuf's C extension) | GCC 12 | `_internal/` | GPL-3.0-or-later with the GCC Runtime Library Exception | Debian copyright files |
| PyChromecast | 14.0.10 | frozen archive | MIT | yes |
| zeroconf | 0.151.3 | 18 compiled extension modules in `_internal/zeroconf/` plus pure-Python modules inside the frozen archive | LGPL-2.1-or-later | yes (`COPYING`) |
| protobuf | 7.36.2 | frozen archive and `_upb` C extension | BSD-3-Clause | yes |
| requests | 2.34.2 | frozen archive | Apache-2.0 | yes, with `NOTICE` |
| urllib3 | 2.8.0 | frozen archive | MIT | yes |
| certifi | 2026.7.22 | frozen archive and `cacert.pem` | MPL-2.0 | yes |
| charset-normalizer | 3.5.1 | frozen archive and compiled modules | MIT | yes |
| idna | 3.20 | frozen archive | BSD-3-Clause | yes |
| ifaddr | 0.2.0 | frozen archive | MIT | yes |
| typing_extensions | 4.16.0 | frozen archive | PSF-2.0 | yes |
| Rust crates linked into the binary | 216 crates | `/usr/bin/control-tv` | mostly MIT and/or Apache-2.0; also Unicode-3.0, MPL-2.0 (4), Zlib, BSD-3-Clause, Unlicense | `/usr/share/doc/control-tv/rust-licenses/` (**9 crates publish no license file**) |
| Rust standard library | toolchain | `/usr/bin/control-tv` | MIT OR Apache-2.0 | `rust-licenses/rust-std/` |

The Python list comes from the distributions whose modules are actually frozen (not from `uv.lock`); setuptools is excluded from the bundle. The Rust list is the crates reachable through normal dependencies for `x86_64-unknown-linux-gnu`, proc-macros and build dependencies excluded. WebKitGTK, GTK and the C library are system packages the `.deb` depends on; they are not shipped.

## zeroconf (LGPL-2.1-or-later)

What the LGPL asks of someone distributing a program that contains the library, as understood here (not legal advice):
- ship the license text (done: `COPYING`);
- make the library's corresponding source available for the version shipped (zeroconf 0.151.3 is public on PyPI, but a distributor is expected to provide or offer it with the binary);
- let the user modify the library and use the modified version with the program (LGPL-2.1 section 6).

How the bundle stands against the last point: the compiled `.so` modules are separate files and can be replaced, but the pure-Python modules are inside the archive appended to the executable and cannot be replaced without rebuilding it. The build is reproducible from the public repository (`dev.py bridge-build`), which is the usual way to satisfy relinking for a frozen application, and control-TV itself is GPL-3.0, with which LGPL-2.1-or-later is compatible; whether that is sufficient, or whether zeroconf must be collected as loose replaceable files, has not been decided.

**Release blocker:** an owner decision (with legal advice if needed) on how the zeroconf obligations are met, before any public distribution.

## Blockers before a public release

1. License texts of the libraries statically linked into python-build-standalone's libpython are not shipped (uv's install does not contain them).
2. Nine Rust crates publish no license file (`alloc-stdlib`, `dlopen2`, `libappindicator-sys`, `selectors`, `unic-char-property`, `unic-char-range`, `unic-common`, `unic-ucd-ident`, `unic-ucd-version`); their texts have to be taken from upstream.
3. The zeroconf decision above.
4. control-TV's own license is not declared in `pyproject.toml`, `Cargo.toml` or `package.json`, and "GPL-3.0-only" versus "GPL-3.0-or-later" is not stated.
5. A source offer covering the GPL/LGPL/MPL components (control-TV, zeroconf, certifi, the MPL-2.0 crates, the PyInstaller bootloader, the GCC runtime) is not written.
